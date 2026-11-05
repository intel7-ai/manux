"""The QML bridge: v0.3's `QtDashboard` interface on the v0.4 runtime (port of hand-mouse/src/qt_dashboard.py).

`ui/qml/dashboard.qml` is v0.3's screen with its latest fixes, unchanged. This object exposes the
same properties and slots under the same names (context property `dashboard`). Differences:
- it renders `ViewFrame`s from the desktop runtime and sends `UiCommand`s; it never decides gestures,
- settings go through `settings.store.Settings` (v0.4 keys) and are saved to the user data folder,
- not ported yet, shown as such: v0.3's frame drawings.

Only `run()` creates Qt objects for the whole app; the runtime thread never touches them.
"""
from __future__ import annotations

import copy
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Property, QSettings, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QCursor, QGuiApplication, QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from manux.core.contracts import Stage
from manux.engine.commands import Mark, RequestMode, SetBaseMode, SetControl, SetStage
from manux.settings import profiles, store
from manux.ui import cards as guide
from manux.ui.finger import overlay_frame
from manux.ui.tutorial import Tutorial
from manux.ui.view import ViewFrame

QML = Path(__file__).with_name("qml") / "dashboard.qml"
CLICK_STYLES = ("pinch", "trigger", "off")
CLICK_LABELS = {"pinch": "클릭 · 엄지+중지/약지", "trigger": "클릭 · 엄지 방아쇠 (A안)", "off": "클릭 · 끔 (트래킹만)"}
ACTION_TEXT = {"mouse.move": "커서 이동", "mouse.left_click": "클릭", "mouse.right_click": "우클릭",
               "mouse.scroll": "스크롤", "mouse.drag": "드래그", "window.task_switcher": "창 고르기",
               "window.show_desktop": "바탕화면", "nav.back": "뒤로", "nav.forward": "앞으로",
               "tab.prev": "이전 탭", "tab.next": "다음 탭"}
MOTION_TEXT = {k: ACTION_TEXT[k] for k in ("nav.back", "nav.forward", "tab.prev", "tab.next")}
STAGES = {"splash": Stage.SPLASH, "welcome": Stage.WELCOME, "capture": Stage.CAPTURE,
          "tutorial": Stage.TUTORIAL, "main": Stage.MAIN}
KEY_SPACE, KEY_ESC, KEY_Q = 32, 27, 81


class CameraImage(QQuickImageProvider):
    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.Image)
        self.image = QImage()

    def requestImage(self, image_id, size, requested_size):
        return self.image


class _NullJob:
    running = False

    def start(self, epochs: int = 20, gestures=None) -> bool:
        return False

    def stop(self) -> None:
        return None

    def snapshot(self) -> Dict[str, Any]:
        return {"running": False}


class _NullTrainingService:
    """Fallback when `Bridge` is constructed in unit tests without `services['training_center']`."""

    categories = ("전체", "클릭·드래그", "창·모드", "방향 모션", "이동·중립")
    total_tasks = 18
    options = {
        "orientation": ["손바닥 정면"],
        "position": ["중앙", "자유 이동"],
        "distance": ["보통 (약 60cm)"],
        "posture": ["바로 앉기"],
        "camera": ["눈높이"],
        "lighting": ["보통"],
    }
    names: Dict[str, str] = {}
    all_names: Dict[str, str] = {}
    profile = "local"
    repo = "se4nchoi/manux-datasets"
    split = "train"
    preset_name = "pinch"

    def __init__(self, config_dir: Path) -> None:
        self.exports_dir = Path(config_dir) / "exports"
        self.job = _NullJob()
        self.hub_job = _NullJob()

    def close(self): pass

    def has_task(self, key: str) -> bool:
        return True

    def task_info(self, key: str) -> Optional[Dict[str, Any]]:
        return {"key": key, "name": key, "do": "", "free": False, "direction": "", "label": key, "gesture": None} if key else None

    def is_free(self, key: str) -> bool:
        return key in ("none", "open_move", "fist_move", "pointer_move")

    def condition(self, **values: Any) -> Dict[str, Any]:
        base: Dict[str, Any] = {
            "name": "새 자세", "enabled": True, "notes": "",
            **{k: v[0] for k, v in self.options.items()},
            "reps": 10, "prep": 1.5, "hold": 2.0, "rest": 1.5, "free_seconds": 60,
        }
        base.update(values)
        return base

    def preset(self, name: str = "basic") -> Dict[str, Any]:
        return {"schema": "manux-capture-plan/1", "conditions": [self.condition(name="정면 · 중앙")]}

    def condition_key(self, row: Any) -> str:
        return "pose-default"

    def normalise(self, plan: Any, *, require_enabled: bool = True) -> Dict[str, Any]:
        return copy.deepcopy(plan)

    def duration(self, plan: Any, focus: str = "") -> float:
        return 60.0

    def load_plan(self, focus: str) -> Dict[str, Any]:
        return self.preset()

    def save_plan(self, plan: Any, focus: str) -> Dict[str, Any]:
        return copy.deepcopy(plan)

    def summary_and_rows(self):
        return {"files": [], "per_gesture": {}, "coverage": {}}, None, [], ("녹화 총 0 MB / 권장 한도 500 MB", False)

    def export(self) -> Path:
        raise FileNotFoundError("학습한 모델이 없어요. 먼저 학습하세요.")


class RecordingPlanEditor(QObject):
    """Qt state for `RecordingPlanDialog.qml`; edits are drafts until saved or started."""

    changed = Signal()
    startRequested = Signal(object, object)

    def __init__(self, tc, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.tc = tc
        self.plan = self.tc.preset()
        self.focus = ""
        self.index = 0
        self.note = ""
        self.coverage: Dict[str, Any] = {}

    def open(self, focus: str, coverage: Optional[Dict[str, Any]] = None) -> None:
        self.focus, self.index, self.note = focus, 0, ""
        self.coverage = coverage or {}
        try:
            self.plan = self.tc.load_plan(focus)
        except (OSError, ValueError, TypeError) as exc:
            self.plan = self.tc.preset()
            self.note = f"저장한 설정을 읽지 못해 기본안을 표시합니다: {exc}"
        self.changed.emit()

    @Property(str, notify=changed)
    def title(self):
        if not isinstance(self.focus, str):
            return f"선택한 동작 {len(self.focus)}개 · 녹화 계획"
        name = self.tc.all_names.get(self.focus) or self.tc.names.get(self.focus, "전체 동작")
        return (name if self.focus else "전체 동작") + " · 집중 녹화 설정"

    @Property("QVariantList", notify=changed)
    def rows(self):
        out = []
        for row in self.plan["conditions"]:
            data = self.coverage.get(self.tc.condition_key(row), {})
            if self.neutral:
                count = f"수집 {data.get('seconds', 0) / 60:.1f}분"
            else:
                count = f"수집 {data.get('reps', 0)}회"
            out.append({**row, "coverage": count})
        return out

    @Property("QVariantMap", notify=changed)
    def selected(self):
        return dict(self.plan["conditions"][self.index])

    @Property(int, notify=changed)
    def selectedIndex(self):
        return self.index

    @Property("QVariantMap", constant=True)
    def choices(self):
        return self.tc.options

    @Property(bool, notify=changed)
    def neutral(self):
        keys = (self.focus,) if isinstance(self.focus, str) else self.focus
        return bool(keys) and all(self.tc.is_free(key) for key in keys)

    @Property(str, notify=changed)
    def instruction(self):
        if not isinstance(self.focus, str):
            return " → ".join(self.tc.all_names.get(key, key) for key in self.focus)
        task = self.tc.task_info(self.focus) if self.focus else None
        if task is None:
            return "전체 동작을 차례로 녹화합니다."
        extra = " 준비 때 시작 위치에 손을 놓고, 유지 시간에 한 번 쓸고, 쉬기 때 돌아오세요." if task.get("direction") else ""
        return str(task.get("do", "")) + extra

    @Property(bool, notify=changed)
    def allGestures(self):
        return not self.focus or (not isinstance(self.focus, str) and any(self.tc.is_free(key) for key in self.focus))

    @Property(str, notify=changed)
    def summary(self):
        count = sum(1 for r in self.plan["conditions"] if r["enabled"])
        seconds = self.tc.duration(self.plan, self.focus)
        return f"선택 {count}/{len(self.plan['conditions'])}자세 · 순수 녹화 약 {seconds / 60:.1f}분 + 자세 준비 시간"

    @Property(str, notify=changed)
    def message(self):
        return self.note

    @Slot(int)
    def select(self, index):
        if 0 <= index < len(self.plan["conditions"]):
            self.index = index
            self.changed.emit()

    @Slot(str, "QVariant")
    def setField(self, key, value):
        if key not in self.selected:
            return
        candidate = copy.deepcopy(self.plan)
        candidate["conditions"][self.index][key] = value
        try:
            self.plan = self.tc.normalise(candidate, require_enabled=False)
            self.note = "아직 저장하지 않은 설정입니다."
        except (ValueError, TypeError) as exc:
            self.note = str(exc)
        self.changed.emit()

    @Slot(str)
    def usePreset(self, name):
        self.plan, self.index = self.tc.preset(name), 0
        self.note = "추천안을 불러왔습니다. 필요한 자세만 선택해도 됩니다."
        self.changed.emit()

    @Slot(bool)
    def add(self, duplicate=False):
        if len(self.plan["conditions"]) >= 30:
            self.note = "자세는 최대 30개입니다."
        else:
            row = self.selected if duplicate else self.tc.condition()
            if duplicate:
                row["name"] = row["name"][:75] + " 복사"
            self.plan["conditions"].append(row)
            self.index = len(self.plan["conditions"]) - 1
            self.note = "추가한 자세의 조건을 설정하세요."
        self.changed.emit()

    @Slot()
    def remove(self):
        if len(self.plan["conditions"]) > 1:
            self.plan["conditions"].pop(self.index)
            self.index = min(self.index, len(self.plan["conditions"]) - 1)
            self.note = "저장하면 선택한 자세가 설정 목록에서 빠집니다. 기존 녹화는 유지됩니다."
        else:
            self.note = "자세를 하나 이상 남겨주세요."
        self.changed.emit()

    @Slot(result=bool)
    def save(self):
        try:
            self.plan = self.tc.save_plan(self.plan, self.focus)
            self.note = "저장했습니다. 이 동작의 다음 집중 녹화에도 적용됩니다."
        except (OSError, ValueError, TypeError) as exc:
            self.note = f"저장하지 못했습니다: {exc}"
            self.changed.emit()
            return False
        self.changed.emit()
        return True

    @Slot(result=bool)
    def start(self):
        if not self.save():
            return False
        self.startRequested.emit(self.focus, copy.deepcopy(self.plan))
        return True


class Bridge(QObject):
    stateChanged = Signal()
    previewChanged = Signal()
    trackingChanged = Signal()
    liveChanged = Signal()
    metricsChanged = Signal()
    recentChanged = Signal()
    failureChanged = Signal()
    receptionChanged = Signal()
    overlayChanged = Signal()
    fingerFrameChanged = Signal()
    tutorialChanged = Signal()
    hiddenToTray = Signal()
    trackingActiveChanged = Signal()
    trainingChanged = Signal()

    def __init__(self, runtime, settings: store.Settings, config_dir: Path, *,
                 services: Optional[Dict[str, Callable]] = None, hotkeys=None, hotkey_defaults=None,
                 normalize_combo: Callable[[str], Optional[str]] = lambda c: c, combo_label=lambda c: c,
                 hide_to_tray: bool = False) -> None:
        super().__init__()
        self.runtime = runtime
        self.settings = settings
        self.config_dir = Path(config_dir)
        self.services = services or {}
        self.application = QApplication.instance() or QApplication([])
        self.application.setQuitOnLastWindowClosed(False)
        # UI preferences (hand, shortcuts, calibration axes ...) live next to settings.json, not in the
        # registry: all v0.4 user data stays in one folder (settings/paths.py) and v0.3's are untouched.
        self.preferences = QSettings(str(self.config_dir / "ui.ini"), QSettings.Format.IniFormat)
        self.closed = False
        self.hide_to_tray = bool(hide_to_tray)
        # screen state (v0.3 names)
        self.stage_name = "splash"
        self.page = "home"
        self.paused = True
        saved = self.preferences.value("controlHand", "", type=str) or settings.get("tracking.control_hand")
        self.control_hand = saved if saved in ("Left", "Right") else ""
        self.use_index_pointer = self.preferences.value("indexPointer", False, type=bool)
        self.click_style = settings.get("pointer.click_style") if settings.get("pointer.click_style") in CLICK_STYLES else "pinch"
        self.pointer_anchor = settings.get("pointer.anchor") if settings.get("pointer.anchor") in ("index", "palm") else "index"
        self.hand_crop = bool(settings.get("tracking.hand_crop"))
        self.finger_overlay = self.preferences.value("fingerOverlay", False, type=bool)
        self.overlay_visible = False
        self.overlay_corner = "top-right"
        try:
            sensitivity = self.preferences.value("pointerSensitivity", 100, type=int)
        except (TypeError, ValueError):
            sensitivity = 100
        self.pointer_sensitivity = max(50, min(150, sensitivity))
        self.profile = profiles.get_active(self.config_dir)
        self._apply_profile()
        self.card_on = {key: guide.card_enabled(settings.get, key) for key in guide.SWITCHES}
        # live view
        self.state = ("입력 꺼짐", "카메라 연결 대기", "손을 카메라에 보여주세요.", "제어 시작", "")
        self.live_gesture = ""
        self.contact_text = ""
        self.metrics = ("", -1, False)
        self.reception = ("paused", "입력 꺼짐")
        self.motion_on = False
        self.motion_last = ""
        self.motion_exit_until = 0.0
        self.motion_badge = (False, "")
        self.finger_frame = None
        self.failure_marks = 0
        self.failure_summary = "아직 저장된 기록이 없습니다."
        self.export_text = ""
        self.tracking_view = None            # plain dict from ViewFrame.tracking while the test window is open
        self.recent = ()                     # tracking test result lines, newest first
        # dataset recording & training center
        self.tc = self.services.get("training_center") or _NullTrainingService(self.config_dir)
        self.rec_mode = "off"
        self.rec_view: Optional[Dict[str, Any]] = None
        self.rec_focus = ""
        self.rec_plan: Optional[Dict[str, Any]] = None
        self.rec_paused = False
        self._training_summary: Dict[str, Any] = {}
        self.recording_editor = RecordingPlanEditor(self.tc, self)
        self.recording_editor.startRequested.connect(self._start_focused_plan)
        self.training_job = self.tc.job
        self.training_rows = []
        self.training_selected = []
        self.training_filter = "전체"
        self.training_info = ("", "")
        self.training_storage = ("", False)
        self.training_busy = ""
        self.training_note = ""
        self._training_was_running = False
        self.training_timer = QTimer(self)
        self.training_timer.setInterval(400)
        self.training_timer.timeout.connect(self._training_tick)
        # hand choice / tutorial / calibration
        self.choice = _HandChoice()
        self.candidate = ""
        self.remaining = 5
        self.tutorial: Optional[Tutorial] = None
        self._tutorial_progress = -1.0
        self.axes = self._load_axes()
        self.samples = []
        self.calibrating = False
        self.calibration_error = ""
        self.tip = None
        self.tip_at = 0.0
        self.aspect = 1.0
        # shortcuts
        self.hotkeys = hotkeys
        self.hotkey_defaults = dict(hotkey_defaults or {})
        self.normalize_combo = normalize_combo
        self.combo_label = combo_label
        self.shortcuts = {a: normalize_combo(self.preferences.value(f"shortcut/{a}", c, type=str)) or c
                          for a, c in self.hotkey_defaults.items()}
        self.ui_requests = []
        if hotkeys is not None:
            hotkeys.start(self.shortcuts)
        # push the starting state to the runtime
        self._seq = -1
        self.last_preview = 0.0
        self.sequence = 0
        self.runtime.control_hand = self.control_hand
        self.runtime.use_crop = self.hand_crop
        self.runtime.set_pointer_gain(self.pointer_sensitivity / 100)
        self.runtime.set_override("pointer", "axes", self.axes)
        self._send(SetBaseMode("pointer" if self.use_index_pointer else "pinch"))
        self._send(SetStage(Stage.SPLASH))
        # Qt
        QQuickStyle.setStyle("Basic")
        self.engine = QQmlApplicationEngine()
        self.provider = CameraImage()
        self.engine.addImageProvider("vision", self.provider)
        self.engine.rootContext().setContextProperty("dashboard", self)
        self.engine.load(QUrl.fromLocalFile(str(QML)))
        if not self.engine.rootObjects():
            raise RuntimeError("MANUX 화면을 열지 못했습니다.")
        self.window = self.engine.rootObjects()[0]
        self.window.setProperty("title", "MANUX")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(15)

    # ------------------------------------------------------------------ helpers
    def _send(self, command) -> None:
        self.runtime.engine.commands.put(command)

    def _save(self) -> None:
        try:
            store.save(self.settings, self.config_dir)
        except OSError as exc:
            self.export_text = f"설정을 저장하지 못했습니다: {exc}"
            self.failureChanged.emit()

    def _set(self, key: str, value: Any) -> None:
        if self.settings.set(key, value):
            self._save()

    def _pause(self) -> None:
        """v0.3 behaviour: a settings change pauses control; the user turns it on again."""
        if not self.paused:
            self.paused = True
            self._send(SetControl(False))

    def _set_stage(self, name: str) -> None:
        self.stage_name = name
        self._send(SetStage(STAGES[name]))
        if name != "main":
            self.paused = True
        self.stateChanged.emit()

    def _apply_profile(self) -> None:
        values = profiles.load_profile(self.config_dir, self.profile) if self.profile else None
        self.settings.apply_profile(values)

    # ------------------------------------------------------------------ basic state
    @Property(str, notify=stateChanged)
    def stage(self):
        return self.stage_name

    @Property(bool, notify=stateChanged)
    def pointerOnly(self):
        return self.use_index_pointer

    @Property(int, notify=stateChanged)
    def pointerSensitivity(self):
        return self.pointer_sensitivity

    @Slot(int)
    def setPointerSensitivity(self, value):
        value = max(50, min(150, int(value)))
        if value != self.pointer_sensitivity:
            self.pointer_sensitivity = value
            self.preferences.setValue("pointerSensitivity", value)
            self.runtime.set_pointer_gain(value / 100)
            self.stateChanged.emit()

    @Slot()
    def persistPointerSettings(self):
        self.preferences.sync()

    @Property(str, notify=stateChanged)
    def pointerPreset(self):
        return "검지 · 기본 마우스" if self.use_index_pointer else "손바닥 · Dan 기본"

    @Slot(bool)
    def setPointerPreset(self, index_pointer):
        if self.stage_name != "main" or bool(index_pointer) == self.use_index_pointer:
            return
        self._pause()
        self.use_index_pointer = bool(index_pointer)
        self.preferences.setValue("indexPointer", self.use_index_pointer)
        self._send(SetBaseMode("pointer" if self.use_index_pointer else "pinch"))
        self.stateChanged.emit()

    @Slot()
    def togglePointerPreset(self):
        self.setPointerPreset(not self.use_index_pointer)

    @Slot(str)
    def setClickStyle(self, style):
        if self.stage_name != "main" or style not in CLICK_STYLES or style == self.click_style:
            return
        self._pause()
        self.click_style = style
        self._set("pointer.click_style", style)
        self.stateChanged.emit()

    @Slot()
    def toggleClickStyle(self):
        if self.stage_name == "main":
            self.setClickStyle(CLICK_STYLES[(CLICK_STYLES.index(self.click_style) + 1) % len(CLICK_STYLES)])

    @Property(str, notify=stateChanged)
    def clickStyle(self):
        return self.click_style

    @Property(bool, notify=stateChanged)
    def triggerClick(self):
        return self.click_style == "trigger"

    @Property(bool, notify=stateChanged)
    def clickOff(self):
        return self.click_style == "off"

    @Property(str, notify=stateChanged)
    def clickStyleLabel(self):
        return CLICK_LABELS[self.click_style]

    @Slot(str)
    def setAnchor(self, anchor):
        if self.stage_name != "main" or anchor not in ("index", "palm") or anchor == self.pointer_anchor:
            return
        self._pause()
        self.pointer_anchor = anchor
        self._set("pointer.anchor", anchor)
        self.stateChanged.emit()

    @Slot()
    def toggleAnchor(self):
        self.setAnchor("palm" if self.pointer_anchor == "index" else "index")

    @Property(str, notify=stateChanged)
    def pointerAnchor(self):
        return self.pointer_anchor

    @Property(str, notify=stateChanged)
    def anchorLabel(self):
        return "기준점 · 검지 끝" if self.pointer_anchor == "index" else "기준점 · 손바닥 중심"

    @Slot(bool)
    def setHandCrop(self, enabled):
        if self.stage_name != "main" or bool(enabled) == self.hand_crop:
            return
        self._pause()
        self.hand_crop = bool(enabled)
        self.runtime.use_crop = self.hand_crop
        self._set("tracking.hand_crop", self.hand_crop)
        self.stateChanged.emit()

    @Property(bool, notify=stateChanged)
    def handCrop(self):
        return self.hand_crop

    # ------------------------------------------------------------------ finger tracker HUD
    @Slot(bool)
    def setFingerOverlay(self, enabled):
        if bool(enabled) != self.finger_overlay:
            self.finger_overlay = bool(enabled)
            self.preferences.setValue("fingerOverlay", self.finger_overlay)
            self.stateChanged.emit()

    @Property(bool, notify=stateChanged)
    def fingerOverlay(self):
        return self.finger_overlay

    @Property("QVariantMap", notify=fingerFrameChanged)
    def fingerFrame(self):
        if self.finger_frame is None:
            return {}
        cursor = QCursor.pos()
        return dict(self.finger_frame, cursor=[cursor.x(), cursor.y()])

    # ------------------------------------------------------------------ "that was wrong" / logs
    @Slot()
    def markFailure(self):
        self._send(Mark("ui"))
        if hasattr(self.runtime, "mark_recording"):
            self.runtime.mark_recording()
        self.failure_marks += 1
        self.failure_summary = f"이번 실행에서 표시 {self.failure_marks}회 (로그 묶음에 포함)"
        self.failureChanged.emit()

    @Slot()
    def exportLogs(self):
        export = self.services.get("export_logs")
        if export is None:
            self.export_text = "로그 내보내기는 아직 준비 중입니다."
        else:
            try:
                self.export_text = f"저장했습니다: {export(self.metrics)}"
            except OSError as exc:
                self.export_text = f"로그를 묶지 못했습니다: {exc}"
        self.failureChanged.emit()

    @Property(str, notify=failureChanged)
    def exportText(self):
        return self.export_text

    @Property(str, notify=failureChanged)
    def failureSummary(self):
        return self.failure_summary

    @Property(str, notify=failureChanged)
    def failureLast(self):
        return ""

    # ------------------------------------------------------------------ camera metrics
    @Property(str, notify=metricsChanged)
    def cameraMode(self):
        return self.metrics[0]

    @Property(int, notify=metricsChanged)
    def brightness(self):
        return self.metrics[1]

    @Property(bool, notify=metricsChanged)
    def lowLight(self):
        return self.metrics[2]

    @Property("QVariantList", notify=recentChanged)
    def recentResults(self):
        return list(self.recent)

    # ------------------------------------------------------------------ motion mode
    @Property(str, notify=stateChanged)
    def motionLabel(self):
        if self.motion_on:
            return "모션 모드 · 켜짐 (눌러서 끄기)" + (f" · {self.motion_last}" if self.motion_last else "")
        return "모션 모드 · 꺼짐 (눌러서 켜기)"

    def _badge_now(self):
        if self.motion_on:
            return True, "모션 모드 켜짐" + (f" · {self.motion_last}" if self.motion_last else "")
        if time.perf_counter() < self.motion_exit_until:
            return True, "모션 모드 꺼짐"
        return False, ""

    @Property(bool, notify=stateChanged)
    def motionBadgeVisible(self):
        return self.motion_badge[0]

    @Property(str, notify=stateChanged)
    def motionBadgeText(self):
        return self.motion_badge[1]

    @Property(bool, notify=stateChanged)
    def motionBadgeOn(self):
        return self.motion_on

    @Property(bool, notify=stateChanged)
    def motionOn(self):
        return self.motion_on

    @Slot()
    def toggleMotion(self):
        if self.stage_name in ("main", "tutorial"):
            self._send(RequestMode(None if self.motion_on else "motion"))

    # ------------------------------------------------------------------ profiles and pages
    @Property(str, notify=stateChanged)
    def profileLabel(self):
        return f"프로필 · {self.profile or '없음'}"

    @Property(int, notify=stateChanged)
    def profileCount(self):
        return len(profiles.list_profiles(self.config_dir))

    @Property("QVariantList", notify=stateChanged)
    def profileNames(self):
        return [["", "기본 (프로필 없음)"]] + [[n, n] for n in profiles.list_profiles(self.config_dir)]

    @Property(str, notify=stateChanged)
    def profileName(self):
        return self.profile or ""

    @Slot(str)
    def selectProfile(self, name):
        value = name or None
        if self.stage_name != "main" or value == self.profile:
            return
        if value is not None and value not in profiles.list_profiles(self.config_dir):
            return
        self._pause()
        self.profile = value
        profiles.set_active(self.config_dir, value)
        self._apply_profile()
        self.stateChanged.emit()

    @Slot()
    def cycleProfile(self):
        names = [None] + profiles.list_profiles(self.config_dir)
        i = names.index(self.profile) if self.profile in names else -1
        self.selectProfile(names[(i + 1) % len(names)] or "")

    @Slot(str)
    def setPage(self, page):
        if page in ("home", "profiles", "customize") and page != self.page:
            self.page = page
            self.stateChanged.emit()

    @Property(str, notify=stateChanged)
    def currentPage(self):
        return self.page

    @Property("QVariantList", notify=stateChanged)
    def switchableCards(self):
        return [[str(c["key"]), str(c["title"]), str(c["how"]), self.card_on.get(str(c["key"]), True)]
                for c in guide.PALM_CARDS if c["key"] in guide.SWITCHES]

    @Slot(str, bool)
    def setCardEnabled(self, key, enabled):
        if key not in guide.SWITCHES or self.card_on.get(key, True) == bool(enabled):
            return
        self._pause()
        for name, value in guide.switch_values(key, enabled).items():
            self.settings.set(name, value)
        self._save()
        self.card_on[key] = bool(enabled)
        self.stateChanged.emit()

    def _cards(self):
        cards = guide.cards(self.use_index_pointer, self.click_style, self.pointer_anchor)
        return cards if self.use_index_pointer else [c for c in cards if self.card_on.get(str(c["key"]), True)]

    @Property("QVariantList", notify=stateChanged)
    def gestureCards(self):
        return guide.for_qml(self._cards())

    # ------------------------------------------------------------------ dataset recording & training center
    def _rec(self, key: str, default: Any) -> Any:
        return (self.rec_view or {}).get(key, default)

    @Property(str, notify=stateChanged)
    def recMode(self):
        return self.rec_mode

    @Property(bool, notify=stateChanged)
    def recActive(self):
        return self.rec_view is not None

    @Property(bool, notify=stateChanged)
    def recWaiting(self):
        return bool(self._rec("waiting", False))

    @Property(str, notify=stateChanged)
    def recPrompt(self):
        return str(self._rec("prompt", ""))

    @Property(str, notify=stateChanged)
    def recSub(self):
        return str(self._rec("sub", ""))

    @Property(str, notify=stateChanged)
    def recCondition(self):
        return str(self._rec("condition_detail", ""))

    @Property(str, notify=stateChanged)
    def recPosition(self):
        return str(self._rec("position", "자유 이동"))

    @Property(bool, notify=stateChanged)
    def recPaused(self):
        return self.rec_paused

    @Slot()
    def toggleRecordingPause(self):
        if self.rec_mode == "guided":
            self.rec_paused = not self.rec_paused
            if hasattr(self.runtime, "set_recording_paused"):
                self.runtime.set_recording_paused(self.rec_paused)
            self.stateChanged.emit()

    @Property(QObject, constant=True)
    def recordingPlan(self):
        return self.recording_editor

    @Property(float, notify=stateChanged)
    def recProgress(self):
        return float(self._rec("progress", 0.0))

    @Property(float, notify=stateChanged)
    def recOverall(self):
        return float(self._rec("overall", 0.0))

    @Slot(str)
    def setRecMode(self, mode):
        if mode not in ("off", "guided", "free") or (mode != "off" and (self.stage_name != "main" or self.use_index_pointer)):
            return
        if mode != "off":
            if self.training_job.running or self.tc.hub_job.running:
                return
            if not getattr(self.tc, "profile", ""):
                self.training_note = "학습 센터에서 내 별명을 먼저 저장하세요."
                self.trainingChanged.emit()
                return
            self._pause()
        self.rec_mode = mode
        self.rec_focus = ""
        self.rec_plan = None
        self.rec_paused = False
        if hasattr(self.runtime, "set_recording"):
            self.runtime.set_recording(mode, "", None, profile=self.tc.profile or "local", split=self.tc.split)
        self.stateChanged.emit()

    @Property(str, notify=stateChanged)
    def recFocus(self):
        return self.rec_focus if isinstance(self.rec_focus, str) else ", ".join(self.rec_focus)

    @Slot()
    def recStart(self):
        if hasattr(self.runtime, "advance_recording"):
            self.runtime.advance_recording()
        self.stateChanged.emit()

    @Slot()
    def openTraining(self):
        self.refreshTraining()
        self.training_timer.start()

    @Slot()
    def closeTraining(self):
        if not self.training_job.running and not self.tc.hub_job.running:
            self.training_timer.stop()

    @Slot()
    def refreshTraining(self):
        if self.training_busy:
            return
        self.training_busy = "녹화 읽는 중…"
        self.trainingChanged.emit()

        def work():
            try:
                summary, report, rows, storage = self.tc.summary_and_rows()
                files = [f for f in summary["files"] if "error" not in f]
                frames = sum(f.get("frames", 0) for f in files)
                minutes = sum(f.get("seconds", 0) for f in files) / 60.0
                valid = sum(f.get("valid_frames", 0) for f in files)
                data = f"녹화 {len(files)}개 · {minutes:.0f}분 · 3D 관절 {100 * valid / max(1, frames):.0f}%"
                data += f" · train {sum(f.get('split') == 'train' for f in files)} / eval {sum(f.get('split') == 'eval' for f in files)}"
                waiting = len(summary["files"]) - len(files)
                if waiting:
                    data += f" · 다음 학습에 포함 {waiting}개 (끝나지 않았거나 받는 중)"
                model = (f"모델: {report.get('trained')} 학습 · 녹화 {len(report.get('data', []))}개로" if report
                         else "모델: 아직 없음 (학습하세요)")
                self.training_rows, self.training_info = rows, (data, model)
                self._training_summary = summary
                self.training_storage = storage
            except Exception as exc:
                self.training_note = f"읽지 못했어요: {exc}"
            self.training_busy = ""

        threading.Thread(target=work, daemon=True).start()
        self.training_timer.start()

    @Slot()
    def startTraining(self):
        if self.rec_mode != "off" or self.tc.hub_job.running:
            return
        self._pause()
        if self.training_job.start():
            self.training_note = ""
            self._training_was_running = True
            self.training_timer.start()
            self.trainingChanged.emit()
        else:
            err = self.training_job.snapshot().get("error")
            if err:
                self.training_note = str(err)
                self.trainingChanged.emit()

    @Slot(str)
    def trainSelectedGesture(self, key):
        if self.rec_mode != "off" or self.training_job.running or self.tc.hub_job.running:
            return
        task = self.tc.task_info(key)
        if task is None or task.get("label") == "none":
            return
        self._pause()
        if self.training_job.start(gestures=[key]):
            self.training_note = str(task.get("name", key)) + "을 포함해 통합 재학습합니다. 기존 동작도 유지합니다."
            self._training_was_running = True
            self.training_timer.start()
            self.trainingChanged.emit()
        else:
            err = self.training_job.snapshot().get("error")
            if err:
                self.training_note = str(err)
                self.trainingChanged.emit()

    @Slot()
    def stopTraining(self):
        self.training_job.stop()

    @Slot()
    def exportModel(self):
        if self.training_busy:
            return
        self.training_busy = "내보내는 중…"
        self.trainingChanged.emit()

        def work():
            try:
                out = self.tc.export()
                self.training_note = f"내보냈어요: exports/{out.name}.zip"
            except Exception as exc:
                self.training_note = f"내보내지 못했어요: {exc}"
            self.training_busy = ""

        threading.Thread(target=work, daemon=True).start()
        self.training_timer.start()

    @Slot()
    def openExports(self):
        self.tc.exports_dir.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(str(self.tc.exports_dir))  # noqa: S606 - Windows explorer on user exports folder

    @Slot(str, result=bool)
    def focusRecord(self, key):
        if self.stage_name != "main" or self.rec_mode != "off" or self.training_job.running or self.tc.hub_job.running:
            return False
        if not self.tc.profile:
            self.training_note = "내 별명을 먼저 저장한 뒤 녹화를 시작하세요."
            self.trainingChanged.emit()
            return False
        if key and not self.tc.has_task(key):
            return False
        coverage = self._training_summary.get("coverage", {}).get(key, {})
        self.recording_editor.open(key, coverage)
        self.command(KEY_ESC)
        return True

    def _start_focused_plan(self, key: str, plan: Dict[str, Any]) -> None:
        if self.stage_name != "main" or self.rec_mode != "off" or self.training_job.running or self.tc.hub_job.running:
            return
        if not self.tc.profile:
            self.training_note = "학습 센터에서 내 별명을 먼저 저장하세요."
            self.trainingChanged.emit()
            return
        self._pause()
        if self.use_index_pointer:
            self.setPointerPreset(False)
        self.rec_mode, self.rec_focus, self.rec_plan = "guided", key, copy.deepcopy(plan)
        self.rec_paused = False
        if hasattr(self.runtime, "set_recording"):
            self.runtime.set_recording("guided", key, self.rec_plan, profile=self.tc.profile, split=self.tc.split)
        self.stateChanged.emit()

    def _training_tick(self) -> None:
        hub_state = self.tc.hub_job.snapshot()
        if hub_state.get("finished") and hub_state.get("finished") != getattr(self, "_hub_finished", None):
            self._hub_finished = hub_state["finished"]
            self.refreshTraining()
        job = self.training_job
        if self._training_was_running and not job.running:
            self._training_was_running = False
            s = job.snapshot()
            if s.get("error"):
                self.training_note = str(s["error"])
            else:
                self.training_note = "별도 평가를 통과해 모델을 설치했습니다. 앱 적용 상태는 메인 화면에서 확인하세요."
                self.stateChanged.emit()
            self.refreshTraining()
        self.trainingChanged.emit()

    @Property("QVariantList", notify=trainingChanged)
    def trainingRows(self):
        category = getattr(self, "training_filter", "전체")
        return [{**r, "selected": r["key"] in self.training_selected} for r in self.training_rows
                if category == "전체" or r["category"] == category]

    @Property("QStringList", notify=trainingChanged)
    def selectedTrainingKeys(self):
        return list(self.training_selected)

    @Property("QVariantList", notify=trainingChanged)
    def selectedTrainingRows(self):
        rows = {r["key"]: r for r in self.training_rows}
        return [rows[key] for key in self.training_selected if key in rows]

    @Property(bool, notify=trainingChanged)
    def selectionCanTrain(self):
        rows = self.selectedTrainingRows
        return bool(rows) and any(r["can_train"] for r in rows) and all(r["can_train"] or r["label"] == "none" for r in rows)

    @Slot(str, bool)
    def setTrainingSelected(self, key, selected):
        if not self.tc.has_task(key):
            return
        if selected and key not in self.training_selected:
            self.training_selected = [*self.training_selected, key]
        elif not selected:
            self.training_selected = [k for k in self.training_selected if k != key]
        self.trainingChanged.emit()

    @Slot(bool)
    def selectVisibleTraining(self, selected):
        visible = [r["key"] for r in self.trainingRows]
        self.training_selected = (list(dict.fromkeys([*self.training_selected, *visible])) if selected
                                  else [k for k in self.training_selected if k not in visible])
        self.trainingChanged.emit()

    @Slot(result=bool)
    def recordTrainingSelection(self):
        if (self.stage_name != "main" or not self.training_selected or self.training_busy
                or self.rec_mode != "off" or self.training_job.running or self.tc.hub_job.running):
            if self.stage_name != "main":
                self.training_note = "메인 화면에서 녹화를 시작하세요."
            elif not self.training_selected:
                self.training_note = "녹화할 동작을 하나 이상 선택하세요."
            else:
                self.training_note = "진행 중인 작업이 끝난 뒤 녹화를 시작하세요."
            self.trainingChanged.emit()
            return False
        if not self.tc.profile:
            self.training_note = "녹화 별명을 먼저 정해 주세요."
            self.trainingChanged.emit()
            return False
        # The exact checked subset, not a focus string that could fall back to the full catalog.
        keys = tuple(self.training_selected)
        self.recording_editor.open(keys)
        self._pause()
        return True

    @Slot()
    def trainTrainingSelection(self):
        if (not self.selectionCanTrain or self.training_busy or self.rec_mode != "off"
                or self.training_job.running or self.tc.hub_job.running):
            return
        keys = [r["key"] for r in self.selectedTrainingRows if r["can_train"]]
        self._pause()
        if self.training_job.start(gestures=keys):
            self._training_was_running = True
            self.training_note = f"선택한 {len(keys)}개 동작을 포함해 기존 모델과 통합 재학습합니다."
            self.training_timer.start()
        else:
            self.training_note = str(self.training_job.snapshot().get("error") or "학습을 시작하지 못했습니다.")
        self.trainingChanged.emit()

    @Property(str, notify=trainingChanged)
    def trainingFilter(self):
        return getattr(self, "training_filter", "전체")

    @Property("QStringList", constant=True)
    def trainingCategories(self):
        return list(self.tc.categories)

    @Property(str, notify=trainingChanged)
    def trainingCatalog(self):
        return f"전체 {self.tc.total_tasks}개 녹화 항목 · {len(self.trainingRows)}개 표시 · 미학습 동작도 녹화 가능"

    @Slot(str)
    def setTrainingFilter(self, category):
        if category in self.trainingCategories:
            self.training_filter = category
            self.trainingChanged.emit()

    @Property(str, notify=trainingChanged)
    def trainingData(self):
        return self.training_info[0]

    @Property(str, notify=trainingChanged)
    def trainingModel(self):
        return self.training_info[1]

    @Property(str, notify=trainingChanged)
    def trainingStorage(self):
        return self.training_storage[0]

    @Property(bool, notify=trainingChanged)
    def trainingStorageWarn(self):
        return bool(self.training_storage[1])

    @Property(str, notify=trainingChanged)
    def trainingBusy(self):
        return self.training_busy

    @Property(str, notify=trainingChanged)
    def trainingNote(self):
        return self.training_note

    @Property(bool, notify=trainingChanged)
    def trainingRunning(self):
        return bool(self.training_job.running)

    @Property(str, notify=trainingChanged)
    def trainingProfile(self):
        return self.tc.profile

    @Property(str, notify=trainingChanged)
    def hubRepo(self):
        return str(self.tc.hub_job.snapshot().get("result", {}).get("repo") or self.tc.repo)

    @Property(bool, notify=trainingChanged)
    def hubConnected(self):
        state = self.tc.hub_job.snapshot()
        return bool(state.get("result", {}).get("repo")) and not state.get("error")

    @Property(bool, notify=trainingChanged)
    def hubCanUpload(self):
        state = self.tc.hub_job.snapshot()
        return self.hubConnected and state.get("result", {}).get("upload_access") is True

    @Property(str, notify=trainingChanged)
    def recordingSplit(self):
        return self.tc.split

    @Slot(str)
    def setRecordingSplit(self, split):
        if split in ("train", "eval") and self.rec_mode == "off":
            self.tc.split = split
            self.refreshTraining()
            self.trainingChanged.emit()

    @Slot(str)
    def saveTrainingProfile(self, profile):
        if self.rec_mode != "off" or self.training_job.running or self.tc.hub_job.running:
            return
        try:
            self.tc.configure(profile)
            self.training_note = "녹화 별명을 저장했습니다. 동작을 체크해 시작하세요."
            self.refreshTraining()
        except (ValueError, OSError):
            self.training_note = "별명은 영문·숫자·_·- 1~32자로 입력하세요."
        self.trainingChanged.emit()

    @Slot(str)
    def runHub(self, action):
        if self.rec_mode != "off" or self.training_job.running or self.tc.hub_job.running:
            return
        if action not in ("status", "upload", "download"):
            return
        self.tc.hub_action(action)
        self.training_timer.start()
        self.trainingChanged.emit()

    @Property(bool, notify=trainingChanged)
    def hubRunning(self):
        return self.tc.hub_job.running

    @Property(str, notify=trainingChanged)
    def hubStatus(self):
        state = self.tc.hub_job.snapshot()
        return str(state.get("error") or state.get("stage") or ".env 설정으로 연결합니다.")

    @Property(str, notify=trainingChanged)
    def trainingStage(self):
        s = self.training_job.snapshot()
        if not s.get("stage"):
            return ""
        text = str(s["stage"])
        if s.get("target") and not text.startswith("선택"):
            name = self.tc.names.get(s["target"], s["target"])
            text = f"{name} · {text}"
        if s.get("running") and s.get("epoch"):
            text += f" · 반복 {s['epoch']}/{s['epochs']} · 손실 {s.get('loss', 0):.3f}"
        if s.get("running") and s.get("started"):
            text += f" · {int(time.time() - s['started'])}초"
        if s.get("skipped"):
            text += f" · 다음 학습으로 미룬 녹화 {len(s['skipped'])}개"
        return text

    @Property(float, notify=trainingChanged)
    def trainingProgress(self):
        return float(self.training_job.snapshot().get("progress", 0.0))

    @Property("QVariantList", notify=trainingChanged)
    def trainingLosses(self):
        return list(self.training_job.snapshot().get("losses", []))[-60:]

    # ------------------------------------------------------------------ tracking test
    # The runtime thread owns the test (data/tracking_test.py); this only shows ViewFrame.tracking.
    @Slot()
    def beginTrackingTest(self):
        if self.stage_name != "main" or self.tracking_view is not None:
            return
        self._pause()
        if not self.use_index_pointer:          # the test measures the index pointer (v0.3 rule)
            self.setPointerPreset(True)
        reason = self.runtime.start_tracking_test()
        if reason:
            self.export_text = reason
            self.failureChanged.emit()
            return
        self._set_tracking_view({"done": False, "paused": True, "index": 0, "total": 0, "target": None,
                                 "holding": False, "hand_present": True, "cursor": (.5, .5), "summary": ""})

    @Slot()
    def closeTrackingTest(self):
        if self.tracking_view is None:
            return
        self.runtime.close_tracking_test()
        self._pause()
        self._set_tracking_view(None)

    def _set_tracking_view(self, view) -> None:
        was = self.tracking_view is not None
        self.tracking_view = view
        if was != (view is not None):
            self.stateChanged.emit()
            self.trackingActiveChanged.emit()
        self.trackingChanged.emit()

    # Bound to the window's visibility / screen: they must not re-evaluate every frame
    # (v0.3 on Windows: re-applying FullScreen 30 times a second froze the app).
    @Property(bool, notify=trackingActiveChanged)
    def trackingActive(self):
        return self.tracking_view is not None

    @Property(str, notify=trackingActiveChanged)
    def trackingScreen(self):
        screen = QGuiApplication.primaryScreen()
        return screen.name() if screen is not None else ""

    def _tview(self, key, default):
        v = self.tracking_view
        return default if v is None else v.get(key, default)

    @Property(float, notify=trackingChanged)
    def trackingTargetX(self):
        t = self._tview("target", None)
        return float(t[0]) if t else -1.0

    @Property(float, notify=trackingChanged)
    def trackingTargetY(self):
        t = self._tview("target", None)
        return float(t[1]) if t else -1.0

    @Property(float, notify=trackingChanged)
    def trackingTargetSize(self):
        """Diameter as a fraction of the test monitor's width."""
        t = self._tview("target", None)
        return float(t[2]) if t else 0.0

    @Property(float, notify=trackingChanged)
    def trackingCursorX(self):
        return float(self._tview("cursor", (.5, .5))[0])

    @Property(float, notify=trackingChanged)
    def trackingCursorY(self):
        return float(self._tview("cursor", (.5, .5))[1])

    @Property(str, notify=trackingChanged)
    def trackingStatus(self):
        v = self.tracking_view
        if v is None:
            return ""
        if v["done"]:
            return "완료 · " + v["summary"]
        progress = f"표적 {v['index'] + 1}/{v['total']}" if v["total"] else "준비 중"
        if v["paused"]:
            return f"일시정지됨 · Space를 눌러 시작하세요 ({progress})"
        return (progress + " · " + ("멈춰 있기…" if v["holding"] else "커서를 원 안으로 옮겨 0.5초 멈추세요")
                + ("" if v["hand_present"] else " · 손을 찾는 중"))

    @Property(bool, notify=trackingChanged)
    def trackingPaused(self):
        return bool(self._tview("paused", False)) and not self._tview("done", False)

    @Property(bool, notify=trackingChanged)
    def trackingDone(self):
        return bool(self._tview("done", False))

    # ------------------------------------------------------------------ pointer direction calibration
    def _axes_key(self) -> str:
        return f"pointerAxes/{self.control_hand}/{int(bool(self.runtime.settings.get('camera.mirror')))}"

    def _load_axes(self):
        saved = self.preferences.value(self._axes_key(), [])
        try:
            axes = tuple(map(float, saved))
        except (TypeError, ValueError):
            return None
        ok = len(axes) == 4 and all(map(math.isfinite, axes)) and math.hypot(*axes[:2]) and math.hypot(*axes[2:])
        return axes if ok else None

    @Property(bool, notify=stateChanged)
    def calibratingPointer(self):
        return self.calibrating

    @Property(bool, notify=stateChanged)
    def pointerCalibrated(self):
        return self.axes is not None

    @Property(str, notify=stateChanged)
    def calibrationHint(self):
        if self.calibration_error:
            return self.calibration_error
        return ("1/3 편하게 손을 든 위치에서 Enter", "2/3 검지를 오른쪽으로 조금 옮긴 뒤 Enter",
                "3/3 처음 위치에서 검지를 위로 조금 옮긴 뒤 Enter")[len(self.samples)]

    @Slot()
    def beginPointerCalibration(self):
        if "pointer_axes" not in self.services:
            return
        self._pause()
        self.samples.clear()
        self.calibration_error = ""
        self.calibrating = True
        self.stateChanged.emit()

    @Slot()
    def cancelPointerCalibration(self):
        self.calibrating = False
        self.samples.clear()
        self.stateChanged.emit()

    @Slot()
    def resetPointerCalibration(self):
        self._pause()
        self.preferences.remove(self._axes_key())
        self.axes = None
        self.runtime.set_override("pointer", "axes", None)
        self.stateChanged.emit()

    @Slot()
    def capturePointerSample(self):
        if not self.calibrating:
            return
        if self.tip is None or time.perf_counter() - self.tip_at > .25:
            self.calibration_error = "검지 끝을 카메라에 보여주고 다시 Enter를 눌러주세요."
        else:
            self.calibration_error = ""
            self.samples.append(self.tip)
            if len(self.samples) == 3:
                axes = self.services["pointer_axes"](*self.samples, self.aspect)
                self.samples.clear()
                if axes is None:
                    self.calibration_error = "방향 차이가 작아요. 처음 위치에서 다시 Enter를 눌러주세요."
                else:
                    self.axes = tuple(axes)
                    self.preferences.setValue(self._axes_key(), list(self.axes))
                    self.preferences.sync()
                    self.runtime.set_override("pointer", "axes", self.axes)
                    self.calibrating = False
        self.stateChanged.emit()

    # ------------------------------------------------------------------ hand choice
    @Property(str, notify=stateChanged)
    def chosenHand(self):
        return {"Left": "왼손", "Right": "오른손"}.get(self.control_hand, "")

    @Property(str, notify=stateChanged)
    def handCandidate(self):
        return {"Left": "왼손", "Right": "오른손"}.get(self.candidate, "")

    @Property(int, notify=stateChanged)
    def secondsRemaining(self):
        return self.remaining

    @Slot()
    def finishSplash(self):
        self._set_stage("main" if self.control_hand else "welcome")

    @Slot()
    def beginHandSetup(self):
        self.cancelPointerCalibration()
        self.choice = _HandChoice()
        self.candidate, self.remaining = "", 5
        self._set_stage("capture")

    @Slot()
    def cancelHandSetup(self):
        if self.control_hand:
            self._set_stage("main")

    def _save_hand(self, side: str) -> None:
        self.control_hand = side
        self.runtime.control_hand = side
        self.preferences.setValue("controlHand", side)
        self._set("tracking.control_hand", side)
        self.axes = self._load_axes()
        self.runtime.set_override("pointer", "axes", self.axes)
        self._set_stage("main")
        if not self.preferences.value("tutorialDone", False, type=bool):
            self.beginTutorial()

    def _choose(self, view: ViewFrame) -> None:
        now = time.perf_counter()
        sides = [h.side for h in view.hands]
        chosen = self.choice.observe(sides, now)
        if chosen:
            self._save_hand(chosen)
            return
        remaining = math.ceil(self.choice.seconds_remaining(now))
        if (self.choice.side, remaining) != (self.candidate, self.remaining):
            self.candidate, self.remaining = self.choice.side, remaining
            self.stateChanged.emit()

    # ------------------------------------------------------------------ tutorial
    @Property(bool, constant=True)
    def tutorialAvailable(self):
        return True

    @Slot()
    def beginTutorial(self):
        if not self.control_hand:
            return
        self.cancelPointerCalibration()
        self.tutorial = Tutorial(self._cards())
        self._set_stage("tutorial")
        self.tutorialChanged.emit()

    @Slot()
    def tutorialNext(self):
        if self.tutorial is not None:
            self.tutorial.next()
            self.tutorialChanged.emit()

    @Slot()
    def tutorialBack(self):
        if self.tutorial is not None:
            self.tutorial.back()
            self.tutorialChanged.emit()

    @Slot()
    def finishTutorial(self):
        self.tutorial = None
        self.preferences.setValue("tutorialDone", True)
        self._set_stage("main")
        self.tutorialChanged.emit()

    def _tutorial_frame(self, view: ViewFrame) -> None:
        t = self.tutorial
        if t is None or t.finished:
            return
        now = time.perf_counter()
        changed = t.observe(view.actions if view.landmarks is not None else (), self.motion_on, now)
        changed = t.tick(now) or changed
        progress = round(t.progress, 2)
        if changed or progress != self._tutorial_progress:
            self._tutorial_progress = progress
            self.tutorialChanged.emit()

    @Property(int, notify=tutorialChanged)
    def tutorialIndex(self):
        return self.tutorial.index if self.tutorial else 0

    @Property(int, notify=tutorialChanged)
    def tutorialCount(self):
        return len(self.tutorial.steps) if self.tutorial else 0

    @Property(bool, notify=tutorialChanged)
    def tutorialFinished(self):
        return bool(self.tutorial and self.tutorial.finished)

    @Property(str, notify=tutorialChanged)
    def tutorialTitle(self):
        card = self.tutorial.current if self.tutorial else None
        return str(card["title"]) if card else ""

    @Property(str, notify=tutorialChanged)
    def tutorialHow(self):
        card = self.tutorial.current if self.tutorial else None
        return str(card["how"]) if card else ""

    @Property(bool, notify=tutorialChanged)
    def tutorialSucceeded(self):
        return bool(self.tutorial and self.tutorial.succeeded_at is not None)

    @Property(float, notify=tutorialChanged)
    def tutorialProgress(self):
        return self.tutorial.progress if self.tutorial else 0.0

    @Property("QVariantList", notify=tutorialChanged)
    def tutorialDoneSteps(self):
        return list(self.tutorial.done) if self.tutorial else []

    # ------------------------------------------------------------------ live status
    @Property(str, notify=liveChanged)
    def liveGesture(self):
        return self.live_gesture

    @Property(str, notify=liveChanged)
    def inputState(self):
        return self.state[0]

    @Property(str, notify=liveChanged)
    def tracking(self):
        return self.state[1]

    @Property(str, notify=liveChanged)
    def currentAction(self):
        return self.state[2]

    @Property(str, notify=liveChanged)
    def controlLabel(self):
        return self.state[3]

    @Property(str, notify=liveChanged)
    def details(self):
        return self.state[4]

    @Property(str, notify=liveChanged)
    def detectedContacts(self):
        return self.contact_text

    @Slot(bool)
    def showFingertips(self, visible):
        return None      # v0.3 drew numbers into the frame; v0.4 does not draw into frames (yet)

    @Property(str, notify=previewChanged)
    def previewUrl(self):
        return f"image://vision/{self.sequence}" if self.sequence else ""

    # ------------------------------------------------------------------ keys, window, tray
    @Slot(int)
    def command(self, key):
        if self.calibrating and key != KEY_Q:
            if key == KEY_ESC:
                self.cancelPointerCalibration()
            return
        if key == KEY_Q:
            self.closed = True
        elif self.stage_name == "main" and key == KEY_SPACE:
            self.paused = not self.paused
            self._send(SetControl(not self.paused))
        elif self.stage_name == "main" and key == KEY_ESC:
            self._pause()

    @Slot()
    def requestClose(self):
        self.closed = True

    @Slot()
    def quitApp(self):
        self.closed = True

    @Property(bool, constant=True)
    def hidesToTray(self):
        return self.hide_to_tray

    @Slot()
    def hideDashboard(self):
        self.window.hide()
        self.runtime.want_image = False
        self.hiddenToTray.emit()

    @Slot()
    def showDashboard(self):
        self.runtime.want_image = True
        self.window.show()
        self.window.raise_()
        self.window.requestActivate()

    def toggle_window(self):
        if self.window.isVisible():
            self.hideDashboard() if self.hide_to_tray else self.window.showMinimized()
        else:
            self.showDashboard()

    @Property(str, notify=receptionChanged)
    def receptionState(self):
        return self.reception[0]

    @Property(str, notify=receptionChanged)
    def receptionText(self):
        return self.reception[1]

    @Slot(bool)
    def setOverlayVisible(self, visible):
        if bool(visible) != self.overlay_visible:
            self.overlay_visible = bool(visible)
            self.overlayChanged.emit()

    @Property(bool, notify=overlayChanged)
    def overlayVisible(self):
        return self.overlay_visible

    @Property(str, notify=overlayChanged)
    def overlayCorner(self):
        return self.overlay_corner

    @Property("QVariantList", notify=overlayChanged)
    def overlayArea(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return [0, 0, 1280, 720]
        g = screen.availableGeometry()
        return [g.x(), g.y(), g.width(), g.height()]

    @Slot(str, str, result=str)
    def setShortcut(self, action, combo):
        if action not in self.shortcuts:
            return "알 수 없는 단축키입니다."
        value = self.normalize_combo(combo)
        if value is None:
            return "Ctrl·Alt·Shift 중 하나 이상과 글자·숫자·F키 하나를 함께 누르세요."
        if any(v == value for a, v in self.shortcuts.items() if a != action):
            return "다른 기능이 이미 쓰는 조합입니다."
        old = dict(self.shortcuts)
        self.shortcuts[action] = value
        if self.hotkeys is not None and action in self.hotkeys.set(self.shortcuts):
            self.shortcuts = old
            self.hotkeys.set(self.shortcuts)
            return "다른 프로그램이 이미 쓰는 조합입니다. 다른 조합을 고르세요."
        self.preferences.setValue(f"shortcut/{action}", value)
        self.stateChanged.emit()
        return ""

    @Slot(str)
    def resetShortcut(self, action):
        if action in self.hotkey_defaults:
            self.setShortcut(action, self.hotkey_defaults[action])

    @Property("QVariantMap", notify=stateChanged)
    def shortcutLabels(self):
        failed = getattr(self.hotkeys, "failed", set())
        return {a: self.combo_label(c) + (" (사용 불가)" if a in failed else "") for a, c in self.shortcuts.items()}

    # ------------------------------------------------------------------ frame rendering (UI thread)
    def poll(self) -> None:
        while self.ui_requests:
            request = self.ui_requests.pop(0)
            if request == "toggle":
                self.command(KEY_SPACE)
            elif request == "hide":
                self.toggle_window()
        if self.closed:
            self.application.quit()
            return
        seq, view = self.runtime.latest()
        if seq != self._seq:
            self._seq = seq
            self.apply_view(view)

    def apply_view(self, view: ViewFrame) -> None:
        snap = view.snapshot
        rec_view = dict(view.rec) if view.rec is not None else None
        if rec_view != self.rec_view:
            self.rec_view = rec_view
            if rec_view is not None and rec_view.get("mode") == "off":
                self.rec_mode = "off"
            self.stateChanged.emit()
        if self.tracking_view is not None and view.tracking is not None and view.tracking != self.tracking_view:
            self._set_tracking_view(dict(view.tracking))
        if view.recent != self.recent:
            self.recent = view.recent
            self.recentChanged.emit()
        if self.stage_name == "capture":
            self._choose(view)
        if (view.camera_mode, view.brightness, view.low_light) != self.metrics:
            self.metrics = (view.camera_mode, view.brightness, view.low_light)
            self.metricsChanged.emit()
        motion_on = bool(snap is not None and snap.mode == "motion")
        for action in view.actions:
            if action in MOTION_TEXT:
                self.motion_last = MOTION_TEXT[action]
        if self.motion_on and not motion_on:
            self.motion_exit_until = time.perf_counter() + 1.8
        if motion_on != self.motion_on:
            self.motion_on = motion_on
            if not motion_on:
                self.motion_last = ""
            self.stateChanged.emit()
        badge = self._badge_now()
        if badge != self.motion_badge:
            self.motion_badge = badge
            self.stateChanged.emit()
        if self.stage_name == "tutorial":
            self._tutorial_frame(view)
        if view.landmarks is not None:
            tip = view.landmarks[8]
            self.tip, self.tip_at = (tip[0], tip[1]), time.perf_counter()
            self.aspect = view.aspect
        frame = (overlay_frame(view.landmarks, view.mirrored, view.actions)
                 if self.finger_overlay and view.landmarks is not None and not self.paused else None)
        if frame is not None or self.finger_frame is not None:
            self.finger_frame = frame
            self.fingerFrameChanged.emit()
        self._show(view)

    def _show(self, view: ViewFrame) -> None:
        detected = view.landmarks is not None
        paused = self.paused or self.stage_name != "main"
        action = ACTION_TEXT.get(view.actions[0], "손짓 대기") if view.actions else "손짓 대기"
        state = (
            "연습 모드" if self.stage_name == "tutorial" else "입력 꺼짐" if paused else "입력 활성",
            ("손 감지됨" if detected else "손 대기") if view.camera_ok else "카메라 없음",
            view.message if not view.camera_ok else
            "제어 시작을 누르면 손짓으로 조작할 수 있어요." if paused and self.stage_name == "main"
            else action if detected else "손을 카메라에 보여주세요.",
            "제어 시작" if paused else "일시정지",
            f"{view.fps:.1f} fps  ·  추론 {view.detect_ms:.1f} ms"
            + (f"  ·  앱 내 프레임 지연 {view.frame_age_ms:.1f} ms" if view.frame_age_ms is not None else "")
            + (f"  ·  {view.model_status}" if view.model_status else ""),
        )
        if self.stage_name != "main":
            reception = ("paused", "손 설정 필요" if self.stage_name in ("welcome", "capture") else "연습 중")
        elif paused:
            reception = ("paused", "입력 꺼짐")
        elif not detected:
            reception = ("none", "손이 보이지 않음")
        elif view.low_light:
            reception = ("weak", "어두움 · 손 인식 약함")
        else:
            reception = ("ok", "손 인식 중")
        if reception != self.reception:
            self.reception = reception
            self.receptionChanged.emit()
        live = guide.active_key(self._cards(), view.actions if detected and not paused else (),
                                motion_on=self.motion_on and not paused)
        if (state, live) != (self.state, self.live_gesture):
            self.state, self.live_gesture = state, live
            self.liveChanged.emit()
        if view.image is None or not self.window.isVisible():
            return
        now = time.perf_counter()
        if now - self.last_preview < 1 / 24:
            return
        self.last_preview = now
        image = view.image
        h, w = image.shape[:2]
        step = max(1, int(math.ceil(w / 900)))
        if step > 1:
            image = image[::step, ::step].copy()
            h, w = image.shape[:2]
        self.provider.image = QImage(image.data, w, h, image.strides[0], QImage.Format_BGR888).copy()
        self.sequence += 1
        self.previewChanged.emit()

    def close(self) -> None:
        if getattr(self, "_closed_qml", False):
            return
        self._closed_qml = True
        self.timer.stop()
        self.training_timer.stop()
        self.tc.close()
        if self.hotkeys is not None:
            self.hotkeys.stop()
        for root in self.engine.rootObjects():
            root.close()
            root.deleteLater()
        self.application.processEvents()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()


class _HandChoice:
    """v0.3 HandSelection on physical sides: one hand held up alone for 5 s (at least 5 frames)."""

    hold_seconds = 5.0

    def __init__(self) -> None:
        self.side, self.since, self.last_at, self.frames = "", None, None, 0

    def seconds_remaining(self, now: float) -> float:
        return self.hold_seconds if self.since is None else max(0.0, self.hold_seconds - (now - self.since))

    def observe(self, sides, now: float) -> str:
        side = sides[0] if len(sides) == 1 else ""
        if not side:
            self.side, self.since, self.last_at, self.frames = "", None, None, 0
            return ""
        if side != self.side or self.last_at is None or now - self.last_at > .25:
            self.side, self.since, self.last_at, self.frames = side, now, None, 0
        if self.last_at is None or now > self.last_at:
            self.last_at = now
            self.frames += 1
        return side if self.frames >= 5 and self.seconds_remaining(now) == 0 else ""


def run(runtime, settings, paths, *, tray: bool = True, screenshot: Optional[Path] = None,
        services: Optional[Dict[str, Callable]] = None, hotkeys=None, hotkey_defaults=None,
        normalize_combo=None, combo_label=None, make_tray=None) -> int:
    """Run the desktop window until the user quits. The composition root passes the platform pieces."""
    app = QApplication.instance() or QApplication([])
    bridge = Bridge(runtime, settings, paths.config_dir, services=services, hotkeys=hotkeys,
                    hotkey_defaults=hotkey_defaults, normalize_combo=normalize_combo or (lambda c: c),
                    combo_label=combo_label or (lambda c: c), hide_to_tray=tray and make_tray is not None)
    if hotkeys is not None:
        hotkeys.callbacks.update({"toggle": lambda: bridge.ui_requests.append("toggle"),
                                  "hide": lambda: bridge.ui_requests.append("hide")})
    tray_icon = make_tray(bridge) if (tray and make_tray is not None) else None
    runtime.start()
    if screenshot is not None:
        def grab():
            bridge.finishSplash()
            if bridge.stage_name == "main":
                bridge.command(KEY_SPACE)      # show the live view with control on (a UI check, see --no-windows-input)
            QTimer.singleShot(2500, lambda: (bridge.window.grabWindow().save(str(screenshot)), app.quit()))
        QTimer.singleShot(2500, grab)
    try:
        app.exec()
    finally:
        if tray_icon is not None:
            tray_icon.hide()
        runtime.stop()
        bridge.close()
    return 0
