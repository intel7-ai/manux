"""The QML bridge: v0.3's `QtDashboard` interface on the v0.4 runtime (port of hand-mouse/src/qt_dashboard.py).

`ui/qml/dashboard.qml` is v0.3's screen with its latest fixes, unchanged. This object exposes the
same properties and slots under the same names (context property `dashboard`). Differences:
- it renders `ViewFrame`s from the desktop runtime and sends `UiCommand`s; it never decides gestures,
- settings go through `settings.store.Settings` (v0.4 keys) and are saved to the user data folder,
- the original studio camera retains its hand skeleton, palm ring and pinch guide drawings.

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

from PySide6.QtCore import Qt, QCoreApplication, QEvent, QObject, Property, QSettings, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QCursor, QGuiApplication, QIcon, QImage, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from manux.core.contracts import RuntimeRequest, Stage
from manux.core.boundary import calc_edge_proximity
from manux.engine.commands import Mark, RequestMode, SetBaseMode, SetBusy, SetControl, SetStage
from manux.settings import profiles, store
from manux.settings.sensitivity import rows as sensitivity_rows
from manux.ui import cards as guide
from manux.ui.finger import overlay_frame
from manux.ui.tutorial import Tutorial, onboarding_steps
from manux.ui.view import ViewFrame
from manux.ui.studio import StudioBridge
from manux.ui.ai_customization import AiCustomizationBridge

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


_DEFAULT_STUDIO_CATEGORIES = (
    {"id": "click", "name": "마우스·클릭", "icon": "🖱️", "desc": "좌·우클릭, 스크롤, 동시클릭"},
    {"id": "window", "name": "창·시스템 제어", "icon": "🪟", "desc": "창 전환, 바탕화면, 토글, 닫기"},
    {"id": "guard", "name": "이동·방어 가드", "icon": "🛡️", "desc": "커서 이동, 휴식 자세, 헛클릭 방어"},
)

_DEFAULT_STUDIO_ACTIONS = (
    # click
    {"id": "thumb_middle", "category": "click", "key": "1", "icon": "👆", "label": "1. 좌클릭 (엄지+중지)", "short": "좌클릭 (엄지+중지)",
     "desc": "엄지 끝과 중지 끝을 맞대어 좌클릭하는 자세"},
    {"id": "thumb_ring", "category": "click", "key": "2", "icon": "💍", "label": "2. 우클릭 (엄지+약지)", "short": "우클릭 (엄지+약지)",
     "desc": "엄지 끝과 약지 끝을 맞대어 우클릭하는 자세"},
    {"id": "thumb_index", "category": "click", "key": "3", "icon": "📜", "label": "3. 스크롤 진입 (엄지+검지)", "short": "스크롤 (엄지+검지)",
     "desc": "엄지 끝과 검지 끝을 맞대어 상하 스크롤 모드로 진입하는 자세"},
    {"id": "thumb_middle_ring", "category": "click", "key": "4", "icon": "🤞", "label": "4. 양쪽 클릭 (엄지+중지+약지)", "short": "양쪽 클릭 (엄지+중지+약지)",
     "desc": "엄지 끝에 중지와 약지를 동시에 맞대어 좌클릭·우클릭을 독립적으로 함께 판정하는 자세"},
    # window
    {"id": "thumb_pinky", "category": "window", "key": "1", "icon": "🤙", "label": "1. 창 전환 (엄지+새끼)", "short": "창 전환 (엄지+새끼)",
     "desc": "엄지 끝과 새끼손가락 끝을 맞대어 창 전환(Alt+Tab) 창을 띄우는 자세"},
    {"id": "palm_side", "category": "window", "key": "2", "icon": "🖐️", "label": "2. 바탕화면 (엄지 접고 손 가로)", "short": "바탕화면 (손가로)",
     "desc": "엄지만 손바닥 안으로 접고 네 손가락을 펴 손을 가로로 세워 모든 창을 최소화하는 자세"},
    {"id": "v_sign", "category": "window", "key": "3", "icon": "✌️", "label": "3. 제어 켜기·끄기 (V 손 모양)", "short": "제어 토글 (V자)",
     "desc": "검지와 중지만 V자로 펴고 나머지는 접어 마우스 제어를 토글하는 자세"},
    {"id": "thumbs_up", "category": "window", "key": "4", "icon": "👍", "label": "4. 모션 모드 (엄지 위로)", "short": "모션 모드 (엄지척)",
     "desc": "네 손가락을 접고 엄지만 위로 세워 모션 제어 모드를 전환하는 자세"},
    # guard
    {"id": "none", "category": "guard", "key": "1", "icon": "✊", "label": "1. 이동·휴식 가드 (none)", "short": "이동·휴식",
     "desc": "아무 동작 아님 · 반주먹이나 편 손으로 커서를 이동하거나 쉬는 자세 (헛클릭 방어)"},
)


class _NullPoseStudio:
    """Fallback when `Bridge` is constructed in unit tests without `services['pose_studio']`."""

    def __init__(self, config_dir: Path) -> None:
        self.snapshots_dir = Path(config_dir) / "pose_snapshots"
        self.models_dir = Path(config_dir) / "models" / "spatial_mlp"
        self.max_samples = 1000
        self.samples = []
        self.selected_ground_truth = "none"
        self.capture_mode = "stream"
        self.is_streaming = False
        self.discrepancy_only = False
        self.require_full_joints = True
        self.full_joints_tracked = False
        self.active_model_id = "spatial_pose_mlp.json"
        self.status_note = "숫자키(1~8)로 동작을 고르고 Space 또는 수집 버튼으로 프레임을 모으세요."
        self.hand_tracked = False
        self.live_system_action = "none"
        self.live_ai_action = "none"
        self.live_ai_confidence = 0.0
        self.live_hybrid_action = "none"
        self.live_hybrid_confidence = 0.0
        self.model_summary: Dict[str, Any] = {"loaded": False}

    def set_capture_mode(self, mode: str) -> str:
        self.capture_mode = mode
        if mode == "snapshot":
            self.is_streaming = False
        return self.capture_mode

    def set_ground_truth(self, action_id: str) -> bool:
        self.selected_ground_truth = action_id
        return True

    def set_discrepancy_only(self, enabled: bool) -> bool:
        self.discrepancy_only = bool(enabled)
        return self.discrepancy_only

    def start_stream(self) -> bool:
        self.capture_mode = "stream"
        self.is_streaming = True
        return True

    def stop_stream(self) -> bool:
        self.is_streaming = False
        return False

    def toggle_stream_or_snapshot(self) -> Dict[str, Any]:
        if self.capture_mode == "stream":
            self.is_streaming = not self.is_streaming
            return {"mode": "stream", "streaming": self.is_streaming}
        return self.capture_snapshot()

    def observe_frame(self, landmarks, world, system_action, now_ms=None):
        self.hand_tracked = landmarks is not None and len(landmarks) == 21
        self.live_system_action = system_action
        return None

    def effective_predicted_action(self) -> str:
        return self.live_ai_action if self.model_summary.get("loaded") else self.live_system_action

    def is_live_match(self) -> bool:
        return self.hand_tracked and self.effective_predicted_action() == self.selected_ground_truth

    def capture_snapshot(self, **kwargs) -> Dict[str, Any]:
        if not self.hand_tracked:
            return {"success": False, "reason": "no_hand_tracked"}
        self.samples.append({"groundTruthAction": self.selected_ground_truth, "isMatch": True})
        return {"success": True, "totalSamples": len(self.samples)}

    def undo_last(self) -> bool:
        if self.samples:
            self.samples.pop()
            return True
        return False

    def clear_samples(self) -> None:
        self.is_streaming = False
        self.samples.clear()

    def summary(self) -> Dict[str, Any]:
        by_act = {a["id"]: 0 for a in _DEFAULT_STUDIO_ACTIONS}
        for s in self.samples:
            gt = s.get("groundTruthAction", "none")
            if gt in by_act:
                by_act[gt] += 1
        return {
            "total": len(self.samples),
            "maxSamples": self.max_samples,
            "matches": sum(1 for s in self.samples if s.get("isMatch")),
            "discrepancies": sum(1 for s in self.samples if not s.get("isMatch")),
            "byAction": by_act,
            "savedTotal": 0,
            "savedByAction": dict(by_act),
            "savedFiles": 0,
        }

    def save_dataset(self) -> Optional[Path]:
        return None

    def set_require_full_joints(self, enabled: bool) -> bool:
        self.require_full_joints = bool(enabled)
        return self.require_full_joints

    def list_models(self) -> List[Dict[str, Any]]:
        return [{
            "id": "spatial_pose_mlp.json",
            "name": "기본 모델 (Default)",
            "active": True,
            "exists": False,
            "test_accuracy": 0.0,
            "train_accuracy": 0.0,
            "total_samples": 0,
            "trained_at": "미학습",
            "file_size_kb": 0.0,
        }]

    def create_model(self, name: str) -> Dict[str, Any]:
        return {"success": True, "id": f"{name}.json", "name": name}

    def activate_model(self, model_id: str) -> bool:
        self.active_model_id = model_id
        return True

    def delete_model(self, model_id: str) -> bool:
        return True

    def train_now(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        return {"success": False, "reason": "not_enough_samples"}


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

    @Property(bool, notify=changed)
    def clickGesture(self):
        keys = (self.focus,) if isinstance(self.focus, str) else self.focus
        return bool(keys) and all(key in ("thumb_middle", "thumb_ring") for key in keys)

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
    @Property(QObject, constant=True)
    def aiCustomization(self):
        return self.ai_customization

    stateChanged = Signal()
    sensitivityChanged = Signal()
    sensitivityApplied = Signal()
    advancedChanged = Signal()
    advancedApplied = Signal()
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
    pointerSetupChanged = Signal()
    hiddenToTray = Signal()
    trackingActiveChanged = Signal()
    trainingChanged = Signal()
    trainingReadComplete = Signal()
    trainingStarted = Signal()
    settingsRequested = Signal()
    studioChanged = Signal()
    phoneCameraChanged = Signal()
    phoneCameraRequested = Signal()

    def __init__(self, runtime, settings: store.Settings, config_dir: Path, *,
                 services: Optional[Dict[str, Callable]] = None, hotkeys=None, hotkey_defaults=None,
                 normalize_combo: Callable[[str], Optional[str]] = lambda c: c, combo_label=lambda c: c,
                 hide_to_tray: bool = False, layout: str = "standard",
                 edge_overlay: Optional[Any] = None) -> None:
        super().__init__()
        self.runtime = runtime
        self.settings = settings
        self.config_dir = Path(config_dir)
        self.services = services or {}
        self.layout = layout
        self._phone_view = {"available": False, "enabled": False, "status": "휴대폰 연결 준비 중"}
        self._phone_qr_url = ""
        self.edge_overlay = edge_overlay
        self._palm_cal = None
        self._palm_axes = None
        self._palm_hint = ""
        self._tcn_enabled = True
        self._pose_studio_visible = False
        self._studio_center_open = False
        self.application = QApplication.instance() or QApplication([])
        self.application.setApplicationName("MANUX")
        self.application.setApplicationDisplayName("MANUX")
        self.application.setWindowIcon(QIcon(str(QML.parent / "assets" / "manux-icon.svg")))
        if not getattr(self.application, "_manux_fonts_loaded", False):
            QFontDatabase.addApplicationFont(str(QML.parent / "assets" / "fonts" / "NotoSansKR.ttf"))
            self.application._manux_fonts_loaded = True
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
        self.mlp_badge_text = ""
        self.metrics = ("", -1, False)
        self.reception = ("paused", "입력 꺼짐")
        self.motion_on = False
        self.motion_last = ""
        self.motion_exit_until = 0.0
        self._control_on = False
        self._control_notice_until = 0.0
        self.motion_badge = (False, "")
        self.finger_frame = None
        self.failure_marks = 0
        self.failure_summary = "아직 저장된 기록이 없습니다."
        self.export_text = ""
        self.tracking_view = None            # plain dict from ViewFrame.tracking while the test window is open
        self.recent = ()                     # tracking test result lines, newest first
        # dataset recording & training center
        self.tc = self.services.get("training_center") or _NullTrainingService(self.config_dir)
        self.studio = StudioBridge(self)
        self.ai_customization = AiCustomizationBridge(self, self.services.get("ai_customization"))
        self.studio.changed.connect(self.trainingChanged.emit)
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
        self.training_selected = [key for key in self.preferences.value("trainingSelected", [], type=list)
                                  if self.tc.has_task(key)]
        self.training_filter = "전체"
        self.training_info = ("", "")
        self.training_storage = ("", False)
        self.training_busy = ""
        self.training_note = ""
        self._training_was_running = False
        self._progress_visible = False
        self.trainingReadComplete.connect(self.trainingChanged.emit, Qt.ConnectionType.QueuedConnection)
        self.training_timer = QTimer(self)
        self.training_timer.setInterval(400)
        self.training_timer.timeout.connect(self._training_tick)
        # pose snapshot studio & spatial MLP
        self.pose_studio = self.services.get("pose_studio") or _NullPoseStudio(self.config_dir)
        self._spatial_guard_enabled = False
        self._studio_categories = self.services["studio_categories"]() if "studio_categories" in self.services else list(_DEFAULT_STUDIO_CATEGORIES)
        self._studio_category = "guard"
        self._studio_actions = (self.services["studio_actions"]() if "studio_actions" in self.services
                                else list(_DEFAULT_STUDIO_ACTIONS))
        self._studio_category = "click"
        self._studio_sys_fn = self.services.get("studio_system_action")
        self._studio_summary_cache = self.pose_studio.summary()
        # hand choice / tutorial / calibration
        self.choice = _HandChoice()
        self.candidate = ""
        self.remaining = 5
        self.tutorial: Optional[Tutorial] = None
        self._pointer_setup_view = {}
        self._setup_before_tutorial = False
        self._tutorial_progress = -1.0
        self._preview_view = ViewFrame(None)
        self._last_practice_seq = 0
        self._practice_min_epoch = 0
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
        self._load_palm_axes()
        if self.layout == "studio":
            self._tcn_enabled = self.preferences.value("tcnEnabled", True, type=bool)
            self.runtime.request(RuntimeRequest("tcn_toggle", "on" if self._tcn_enabled else "off"))
        self._send(SetBaseMode("pointer" if self.use_index_pointer else "pinch"))
        self._send(SetStage(Stage.SPLASH))
        # Qt
        QQuickStyle.setStyle("Basic")
        self.engine = QQmlApplicationEngine()
        self.provider = CameraImage()
        self.engine.addImageProvider("vision", self.provider)
        self.phone_qr_provider = CameraImage()
        self.engine.addImageProvider("phoneqr", self.phone_qr_provider)
        self.engine.addImageProvider("probeshot", self.ai_customization.probe_shot)
        self.engine.rootContext().setContextProperty("dashboard", self)
        qml = QML.parent / "studio" / "dashboard.qml" if self.layout == "studio" else QML
        self.engine.load(QUrl.fromLocalFile(str(qml)))
        if not self.engine.rootObjects():
            raise RuntimeError("MANUX 화면을 열지 못했습니다.")
        self.window = self.engine.rootObjects()[0]
        self.window.setIcon(self.application.windowIcon())
        if self.layout != "studio":
            self.window.setProperty("title", "MANUX")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(15)
        make_screen_preview = self.services.get("screen_preview")
        self.screen_preview = make_screen_preview(self) if make_screen_preview else None
        if self.services.get("phone_dialog"):
            QTimer.singleShot(200, self.phoneCameraRequested.emit)

    @Property("QVariantMap", notify=phoneCameraChanged)
    def phoneCamera(self):
        return self._phone_view

    @Property(str, notify=phoneCameraChanged)
    def phoneQrUrl(self):
        return self._phone_qr_url

    @Slot(bool)
    def setPhoneDialogOpen(self, opened):
        self._pause()
        self._send(SetBusy("connecting", opened))

    @Slot(str, str)
    def phoneCameraAction(self, action, host):
        self._pause()
        self.runtime.request(RuntimeRequest("phone_camera", action, {"host": host}))

    @Property(bool, notify=phoneCameraChanged)
    def pcCameraAvailable(self):
        source = getattr(self.runtime, "source", None)
        return callable(getattr(source, "reconnect_camera", None)) and not self._phone_view.get("enabled", False)

    @Slot()
    def reconnectCamera(self):
        if (self.pcCameraAvailable and self.rec_mode == "off" and not self.recActive
                and not self.trackingActive and not self.calibratingPointer and not self.palmCalibrating
                and not self.pointerSetupActive):
            self._pause()
            self.runtime.request(RuntimeRequest("camera_reconnect"))

    @Slot()
    def copyPhoneUrl(self):
        self.application.clipboard().setText(self._phone_view.get("url", ""))

    def _update_phone(self, value):
        value = dict(value or {"available": False, "enabled": False, "status": "녹화 재생에서는 휴대폰 연결을 사용할 수 없습니다."})
        if value == self._phone_view:
            return
        url = value.get("url", "")
        if url != self._phone_view.get("url", ""):
            self._phone_qr_url = ""
            self.phone_qr_provider.image = QImage()
            if url:
                try:
                    import qrcode
                    qr = qrcode.QRCode(border=4)
                    qr.add_data(url)  # URL only: pairing code/token never enter the QR.
                    qr.make(fit=True)
                    matrix = qr.get_matrix()
                    size = len(matrix)
                    image = QImage(size, size, QImage.Format.Format_RGB32)
                    for y, row in enumerate(matrix):
                        for x, black in enumerate(row):
                            image.setPixel(x, y, 0xff000000 if black else 0xffffffff)
                    self.phone_qr_provider.image = image.scaled(size*6, size*6)
                    self._phone_qr_url = "image://phoneqr/" + str(time.monotonic_ns())
                except ImportError:
                    pass  # The selectable URL remains usable without the optional QR extra.
        self._phone_view = value
        self.phoneCameraChanged.emit()

    # Original Rasp-Jun studio presentation and calibration adapters.
    @Property(str, constant=True)
    def appName(self):
        return "MANUX"

    @Property(str, constant=True)
    def appTitle(self):
        return "MANUX"

    @Property("QVariantList", notify=trainingChanged)
    def studioTrainingRows(self):
        category = getattr(self, "training_filter", "전체")
        files = self._training_summary.get("files", [])
        rows = []
        for r in self.training_rows:
            if category != "전체" and r["category"] != category:
                continue
            row = {**r, "selected": r["key"] in self.training_selected}
            info = self.tc.task_info(row["key"]) or {}
            count = sum(bool(f.get("coverage", {}).get(row["key"])) for f in files if "error" not in f)
            rows.append({**row, "free": info.get("free", False), "recordings": count,
                         "can_train": row.get("can_train", False) and (count > 0 or row.get("reps", 0) > 0),
                         "tcn_available": bool(row.get("model_id")),
                         "tcn_mode": {"rules": 0, "shadow": 1, "apply": 2}.get(row.get("tcn_mode"), 0),
                         "interaction_text": ("가져온 모델 · 별도 평가 전 시험만 가능" if row.get("import_pending")
                                              else "별도 평가 결과를 기준으로 동작별 시험·적용을 선택하세요.")})
        return rows

    @Property(int, notify=trainingChanged)
    def trainingSelectedCount(self):
        return len(self.training_selected)

    @Property(str, notify=trainingChanged)
    def trainingSelectionText(self):
        if not self.training_selected:
            return "학습할 동작을 체크하세요. 선택하지 않은 동작의 기존 학습 결과는 유지됩니다."
        return f"선택 {len(self.training_selected)}종: " + " · ".join(
            (self.tc.task_info(key) or {}).get("name", key) for key in self.training_selected)

    @Slot(str, bool)
    def selectTrainingTask(self, key, selected):
        if not self.training_job.running and not self.training_busy:
            self.setTrainingSelected(key, selected)

    @Slot(bool)
    def selectAllTraining(self, selected):
        if self.training_job.running or self.training_busy:
            return
        if selected:
            self.selectRecordedTraining()
        else:
            self.training_selected = []
            self.preferences.setValue("trainingSelected", [])
            self.trainingChanged.emit()

    @Slot(str, int)
    def setTaskTcnMode(self, key, index):
        row = next((r for r in self.training_rows if r["key"] == key), None)
        if row and index in (0, 1, 2):
            self.setGestureTcnMode(key, ("rules", "shadow", "apply")[index], row.get("model_id", ""))

    @Slot()
    def openTrainingProgress(self):
        self.showTrainingProgress(True)

    @Slot()
    def closeTrainingProgress(self):
        self.showTrainingProgress(False)

    @Property("QVariantMap", notify=trainingChanged)
    def trainingDetails(self):
        return self.trainingProgressState

    @Property(str, notify=trainingChanged)
    def trainingProgressSummary(self):
        details = self.trainingDetails
        if details.get("status", "idle") == "idle":
            return "진행 페이지에서 학습 단계·남은 시간·완료 예상 시각을 확인할 수 있습니다."
        return f"{details['percent']}% · 남은 시간 {details['remaining']} · 완료 예상 {details['finish']}"

    @Property(bool, notify=trainingChanged)
    def tcnAvailable(self):
        return hasattr(self.tc, "current_model") and self.tc.current_model().is_file()

    @Property(bool, notify=stateChanged)
    def tcnGate(self):
        return self._tcn_enabled

    @Slot(bool)
    def setTcnGate(self, enabled):
        self._pause()
        self._tcn_enabled = bool(enabled)
        self.preferences.setValue("tcnEnabled", self._tcn_enabled)
        self.runtime.request(RuntimeRequest("tcn_toggle", "on" if enabled else "off"))
        self.stateChanged.emit()

    def _palm_key(self):
        return f"palmAxes/{self.control_hand}/{int(bool(self.settings.get('camera.mirror')))}"

    def _load_palm_axes(self):
        saved = self.preferences.value(self._palm_key(), [])
        valid = self.services.get("valid_palm_axes", lambda v: False)
        self._palm_axes = tuple(map(float, saved)) if valid(saved) else None
        self.runtime.set_override("pinch", "palm_axes", self._palm_axes)

    @Property(bool, notify=stateChanged)
    def palmCalibrating(self):
        return self._palm_cal is not None

    @Property(bool, notify=stateChanged)
    def palmCalibrated(self):
        return self._palm_axes is not None

    @Property(str, notify=stateChanged)
    def palmAxesHint(self):
        return self._palm_hint

    @Slot()
    def beginPalmCalibration(self):
        if (self.stage_name != "main" or self.use_index_pointer or self.rec_mode != "off"
                or self.training_job.running or "palm_calibration" not in self.services):
            return
        self.cancelPointerCalibration()
        self._pause()
        self._send(SetBusy("calibrating", True))
        self._palm_cal = self.services["palm_calibration"](start=time.perf_counter())
        self._palm_hint = "주먹을 쥐고 위아래로 움직일 준비를 하세요."
        self.stateChanged.emit()

    @Slot()
    def cancelPalmCalibration(self):
        if self._palm_cal is not None:
            self._palm_cal = None
            self._palm_hint = "방향 보정을 취소했습니다. 기존 보정은 유지됩니다."
            self._send(SetBusy("calibrating", False))
            self.stateChanged.emit()

    @Slot()
    def resetPalmCalibration(self):
        self.cancelPalmCalibration()
        self._pause()
        self._palm_axes = None
        self.preferences.remove(self._palm_key())
        self.runtime.set_override("pinch", "palm_axes", None)
        self._palm_hint = "방향 보정을 초기화했습니다."
        self.stateChanged.emit()

    def _update_palm_view(self, view):
        cal = self._palm_cal
        if cal is not None:
            now = time.perf_counter()
            if cal.update(now, self.services["palm_point"](view.landmarks), view.aspect):
                if cal.axes is not None:
                    self._palm_axes = tuple(cal.axes)
                    self.preferences.setValue(self._palm_key(), list(self._palm_axes))
                    self.preferences.sync()
                    self.runtime.set_override("pinch", "palm_axes", self._palm_axes)
                self._palm_hint = cal.view(now)[0] + " · " + cal.error
                self._palm_cal = None
                self._send(SetBusy("calibrating", False))
            else:
                prompt, progress, note = cal.view(now)
                self._palm_hint = f"{prompt} · {note} {int(progress * 100)}%"
            self.stateChanged.emit()


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
        # V may have enabled the engine before the next UI poll updates the display.
        self.paused = True
        self._send(SetControl(False))

    @Slot(bool)
    def setInteractionBusy(self, busy):
        self._pause()
        self._send(SetBusy("enrolling", bool(busy)))

    def _set_stage(self, name: str) -> None:
        if name != "tutorial" and self._pointer_setup_view:
            self.runtime.request(RuntimeRequest("pointer_setup", "skip"))
            self._pointer_setup_view = {}
            self.pointerSetupChanged.emit()
        self.stage_name = name
        self._send(SetStage(STAGES[name]))
        if name != "main":
            self.paused = True
        self.stateChanged.emit()

    def _apply_profile(self) -> None:
        values = profiles.load_profile(self.config_dir, self.profile) if self.profile else None
        self.settings.apply_profile(values)

    # ------------------------------------------------------------------ basic state
    @Property("QVariantList", notify=sensitivityChanged)
    def sensitivityRows(self):
        return sensitivity_rows(self.settings)

    @Property(str, notify=sensitivityChanged)
    def sensitivityStatus(self):
        return getattr(self, "_sensitivity_status", "")

    @Property(bool, notify=sensitivityChanged)
    def sensitivityPending(self):
        return getattr(self, "_sensitivity_pending", False)

    @Slot("QVariantMap", result=bool)
    def applySensitivity(self, values):
        if self.sensitivityPending:
            return False
        self._pause()
        self._sensitivity_revision = self.runtime.sensitivity_result[0]
        accepted = self.runtime.request(RuntimeRequest("sensitivity", str(self.config_dir), values))
        self._sensitivity_pending = accepted
        self._sensitivity_status = "설정 저장 중…" if accepted else "앱이 종료 중이라 저장할 수 없습니다."
        self.sensitivityChanged.emit()
        self.stateChanged.emit()
        return accepted

    @Property("QVariantList", notify=advancedChanged)
    def advancedRows(self):
        from manux.settings.advanced import rows
        return rows(self.settings)

    @Property(bool, notify=advancedChanged)
    def advancedPending(self):
        return getattr(self, "_advanced_pending", False)

    @Property(str, notify=advancedChanged)
    def advancedStatus(self):
        return getattr(self, "_advanced_status", "")

    @Slot("QVariantMap", result=bool)
    def applyAdvancedSettings(self, values):
        if self.advancedPending:
            return False
        self._pause()
        self._advanced_revision = self.runtime.advanced_result[0]
        accepted = self.runtime.request(RuntimeRequest("advanced", str(self.config_dir), values))
        self._advanced_pending = accepted
        self._advanced_status = "설정 저장 중…" if accepted else "앱이 종료 중이라 저장할 수 없습니다."
        self.advancedChanged.emit()
        self.stateChanged.emit()
        return accepted

    @Property(bool, notify=advancedChanged)
    def showDesktopEnabled(self):
        return bool(self.settings.get("pinch.enable_thumb_tucked_open"))

    @Slot(bool)
    def setShowDesktopEnabled(self, enabled):
        self.applyAdvancedSettings({"pinch.enable_thumb_tucked_open": bool(enabled)})

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
        self.cancelPalmCalibration()
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
        if self.stage_name == "main" and time.perf_counter() < self._control_notice_until:
            return True, "제어 켜짐" if self._control_on else "제어 꺼짐"
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
        if page in ("home", "studio", "profiles", "customize") and page != self.page:
            if page == "studio":
                self._pause()
                self._studio_summary_cache = self.pose_studio.summary()
                self.studioChanged.emit()
            elif self.page == "studio" and self.pose_studio.is_streaming:
                self.pose_studio.stop_stream()
                self.studioChanged.emit()
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
            if self.training_job.running or self.tc.hub_job.running or self.studio.busy:
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
        self._pause()
        self._studio_center_open = True
        self.refreshTraining()
        self.training_timer.start()

    @Slot()
    def closeTraining(self):
        self.setPoseStudioVisible(False)
        self.studio.active = False
        self._studio_center_open = False
        if not self.training_job.running and not self.tc.hub_job.running and not self._progress_visible:
            self.training_timer.stop()

    @Slot(bool)
    def showTrainingProgress(self, visible):
        self._progress_visible = bool(visible)
        if visible:
            self._pause()
            self.training_timer.start()
            self.trainingChanged.emit()
        else:
            self.closeTraining()

    @Property("QVariantMap", notify=trainingChanged)
    def trainingProgressState(self):
        return self.tc.progress() if hasattr(self.tc, "progress") else {}

    @Property(QObject, constant=True)
    def learningStudio(self):
        return self.studio

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
                if report and report.get("import_pending") and not report.get("evaluation_source"):
                    model = "이전 모델 복구 · TCN 시험 중 · 별도 평가 전"
                self.training_rows, self.training_info = rows, (data, model)
                self._training_summary = summary
                self.training_storage = storage
            except Exception as exc:
                self.training_note = f"읽지 못했어요: {exc}"
            self.training_busy = ""
            self.trainingReadComplete.emit()

        threading.Thread(target=work, daemon=True).start()
        self.training_timer.start()

    @Slot()
    def startTraining(self):
        if self.layout == "studio":
            self.trainTrainingSelection()
            return
        if self.rec_mode != "off" or self.tc.hub_job.running or self.studio.busy:
            return
        self._pause()
        if self.training_job.start():
            self.training_note = ""
            self._training_was_running = True
            self.training_timer.start()
            self.trainingChanged.emit()
            self.trainingStarted.emit()
        else:
            err = self.training_job.snapshot().get("error")
            if err:
                self.training_note = str(err)
                self.trainingChanged.emit()

    @Slot(str)
    def trainSelectedGesture(self, key):
        if self.rec_mode != "off" or self.training_job.running or self.tc.hub_job.running or self.studio.busy:
            return
        task = self.tc.task_info(key)
        if task is None or task.get("label") == "none":
            return
        self._pause()
        if self.training_job.start(gestures=[key]):
            self.training_note = str(task.get("name", key)) + "만 학습합니다. 선택하지 않은 분류기는 유지합니다."
            self._training_was_running = True
            self.training_timer.start()
            self.trainingChanged.emit()
            self.trainingStarted.emit()
        else:
            err = self.training_job.snapshot().get("error")
            if err:
                self.training_note = str(err)
                self.trainingChanged.emit()

    @Slot()
    def stopTraining(self):
        self.training_job.stop()

    @Slot(str, str, str)
    def setGestureTcnMode(self, key, mode, model_id):
        if self.rec_mode != "off" or self.training_job.running or self.tc.hub_job.running:
            return
        self._pause()
        try:
            self.tc.set_policy(key, mode, model_id)
            self.training_note = "동작별 TCN 설정을 저장했습니다. 제어를 다시 켜서 확인하세요."
        except (ValueError, OSError) as error:
            self.training_note = str(error)
        self.refreshTraining()
        self.trainingChanged.emit()

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
        if self.stage_name != "main" or self.rec_mode != "off" or self.training_job.running or self.tc.hub_job.running or self.studio.busy:
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
        if self.stage_name != "main" or self.rec_mode != "off" or self.training_job.running or self.tc.hub_job.running or self.studio.busy:
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
            if self.studio.active: self.studio.refresh()
        job = self.training_job
        if self._training_was_running and not job.running:
            self._training_was_running = False
            s = job.snapshot()
            if s.get("error"):
                self.training_note = str(s["error"])
            else:
                if s.get("kind") == "pose_train":
                    self.pose_studio.model_summary = self.pose_studio._load_saved_model_summary()
                    self.pose_studio._load_active_model_instance()
                    self.pose_studio.status_note = "포즈 모델 준비 완료 · 실시간 제어에 적용되었습니다."
                    self.training_note = self.pose_studio.status_note
                    self.studioChanged.emit()
                elif s.get("kind") == "import":
                    self.tc.profile = s.get("result", {}).get("profile", self.tc.profile)
                    self.training_note = "이전 작업을 가져왔습니다. 모델은 시험 상태입니다. 가져온 개인 설정은 앱 재시작 후 반영됩니다."
                else:
                    self.training_note = "별도 평가 통과 · 새 동작은 TCN 시험 모드입니다. 확인 후 동작별로 TCN 적용을 선택하세요."
                self.stateChanged.emit()
            self.refreshTraining()
            if self.studio.active: self.studio.refresh()
        self.trainingChanged.emit()

    @Property("QVariantList", notify=trainingChanged)
    def trainingRows(self):
        category = getattr(self, "training_filter", "전체")
        return [
            {**r, "selected": r["key"] in self.training_selected}
            for r in self.training_rows
            if r["category"] not in ("클릭·드래그", "창·모드")
            and (category == "전체" or r["category"] == category)
        ]

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
        self.preferences.setValue("trainingSelected", self.training_selected)
        self.trainingChanged.emit()

    @Slot(bool)
    def selectVisibleTraining(self, selected):
        visible = [r["key"] for r in self.trainingRows]
        self.training_selected = (list(dict.fromkeys([*self.training_selected, *visible])) if selected
                                  else [k for k in self.training_selected if k not in visible])
        self.preferences.setValue("trainingSelected", self.training_selected)
        self.trainingChanged.emit()

    @Slot()
    def selectRecordedTraining(self):
        self.training_selected = [row["key"] for row in self.training_rows if row.get("can_train") and
                                  (row.get("reps", 0) > 0 or row.get("minutes", 0) > 0)]
        self.preferences.setValue("trainingSelected", self.training_selected)
        self.trainingChanged.emit()

    @Property(str, notify=trainingChanged)
    def trainingImportStatus(self):
        return self.tc.import_status() if hasattr(self.tc, "import_status") else ""

    @Slot(str)
    def importTrainingWork(self, url):
        if self.training_busy or self.rec_mode != "off" or self.training_job.running or self.tc.hub_job.running:
            return
        path = QUrl(url).toLocalFile()
        if not path:
            return
        self._pause()
        if self.training_job.import_legacy(path, self.tc.data_dir, self.tc.profile):
            self._training_was_running = True
            self.training_timer.start()
            self.trainingStarted.emit()
        self.trainingChanged.emit()

    @Slot()
    def validateImportedModel(self):
        if self.training_busy or self.rec_mode != "off" or self.training_job.running or self.tc.hub_job.running:
            return
        self._pause()
        candidate = self.tc.imported_candidate()
        if candidate is None:
            self.training_note = "가져온 모델 후보가 없습니다. 녹화한 동작을 선택해 학습하세요."
            self.trainingChanged.emit()
            return
        if self.training_job.validate_import(candidate):
            self._training_was_running = True
            self.training_timer.start()
            self.trainingStarted.emit()
        self.trainingChanged.emit()

    @Slot(result=bool)
    def recordTrainingSelection(self):
        if (self.stage_name != "main" or not self.training_selected or self.training_busy
                or self.rec_mode != "off" or self.training_job.running or self.tc.hub_job.running or self.studio.busy):
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
                or self.training_job.running or self.tc.hub_job.running or self.studio.busy):
            return
        keys = [r["key"] for r in self.selectedTrainingRows if r["can_train"]]
        self._pause()
        if self.training_job.start(gestures=keys):
            self._training_was_running = True
            self.training_note = f"선택한 {len(keys)}개 동작만 학습합니다. 다른 분류기는 그대로 유지합니다."
            self.training_timer.start()
            self.trainingStarted.emit()
        else:
            self.training_note = str(self.training_job.snapshot().get("error") or "학습을 시작하지 못했습니다.")
        self.trainingChanged.emit()

    @Property(str, notify=trainingChanged)
    def trainingFilter(self):
        return getattr(self, "training_filter", "전체")

    @Property("QStringList", constant=True)
    def trainingCategories(self):
        return [c for c in self.tc.categories if c not in ("클릭·드래그", "창·모드")]

    @Property(str, notify=trainingChanged)
    def trainingCatalog(self):
        return f"TCN 시계열 모션 {len(self.trainingRows)}개 표시 (클릭·창 제어는 포즈 스튜디오 전담)"

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
        return self.training_busy or ("스튜디오 작업 중" if self.studio.busy else "")

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
        if split != self.tc.split and self.pose_studio.samples:
            self.training_note = "포즈 표본을 저장하고 ‘새 수집’을 누른 뒤 용도를 바꾸세요. 기존 시도를 평가로 바꾸지 않습니다."
            self.trainingChanged.emit()
            return
        if split in ("train", "eval") and self.rec_mode == "off" and not self.studio.busy:
            self.tc.split = split
            self.refreshTraining()
            self.trainingChanged.emit()

    @Slot(str)
    def saveTrainingProfile(self, profile):
        if profile != self.tc.profile and self.pose_studio.samples:
            self.training_note = "포즈 표본을 저장하고 ‘새 수집’을 누른 뒤 별명을 바꾸세요."
            self.trainingChanged.emit()
            return
        if self.rec_mode != "off" or self.training_job.running or self.tc.hub_job.running or self.studio.busy:
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
        if self.rec_mode != "off" or self.training_job.running or self.tc.hub_job.running or self.studio.busy:
            return
        if action not in ("status", "upload", "download"):
            return
        self.tc.hub_action(action, classes=[row["label"] for row in self.training_rows if row["key"] in self.training_selected])
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
        execution = s.get("execution")
        if execution:
            text += " · " + str(execution["actual"]) + " · " + str(execution.get("name", ""))
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

    # ------------------------------------------------------------------ pose snapshot studio & spatial MLP
    @Slot(bool)
    def setPoseStudioVisible(self, visible):
        self._pose_studio_visible = bool(visible)
        if visible: self._pause()
        else:
            self.pose_studio.stop_stream()
            self.ai_customization.setPoseStudioVisible(False)
        self.studioChanged.emit()

    @Slot(bool)
    def setAiPoseStudioVisible(self, visible):
        if visible:
            self.pose_studio.stop_stream()
            self.studioChanged.emit()
        self.ai_customization.setPoseStudioVisible(visible)

    @Property(bool, notify=studioChanged)
    def spatialGuardEnabled(self): return self._spatial_guard_enabled

    @Slot()
    def toggleSpatialGuard(self):
        if self.training_job.running or self.studio.busy or self.tc.hub_job.running or self.rec_mode != "off": return
        self._pause()
        self.runtime.request(RuntimeRequest("spatial_guard", "off" if self._spatial_guard_enabled else "on"))

    def _studio_label(self, action_id: str) -> str:
        if action_id == 'rock_sign':
            return 'Rock sign (이전 모델 분류 · 기본 동작 없음)'
        for item in self._studio_actions:
            if item["id"] == action_id:
                return item["short"]
        return action_id

    @Property("QVariantList", notify=studioChanged)
    def studioCategories(self):
        counts = self._studio_summary_cache.get("byAction", {})
        saved = self._studio_summary_cache.get("savedByAction", {})
        out = []
        for cat in self._studio_categories:
            cid = cat["id"]
            cat_actions = [a for a in self._studio_actions if a.get("category") == cid]
            total_samples = sum(counts.get(a["id"], 0) + saved.get(a["id"], 0) for a in cat_actions)
            out.append({
                **cat,
                "selected": cid == self._studio_category,
                "actionCount": len(cat_actions),
                "totalCount": total_samples,
            })
        return out

    @Property(str, notify=studioChanged)
    def studioCategory(self):
        return self._studio_category

    @Property("QVariantList", notify=studioChanged)
    def studioCategoryActions(self):
        cat = self._studio_category
        return [a for a in self.studioActions if a.get("category") == cat]

    @Slot(str)
    def setStudioCategory(self, cat_id):
        if cat_id != self._studio_category and any(c["id"] == cat_id for c in self._studio_categories):
            self._studio_category = cat_id
            curr = self.pose_studio.selected_ground_truth
            cat_action_ids = [a["id"] for a in self._studio_actions if a.get("category") == cat_id]
            if curr not in cat_action_ids and cat_action_ids:
                self.setStudioGroundTruth(cat_action_ids[0])
            self.studioChanged.emit()

    @Slot()
    def nextStudioCategory(self):
        cat_ids = [c["id"] for c in self._studio_categories]
        if not cat_ids:
            return
        idx = cat_ids.index(self._studio_category) if self._studio_category in cat_ids else 0
        next_cat = cat_ids[(idx + 1) % len(cat_ids)]
        self.setStudioCategory(next_cat)

    @Property("QVariantList", notify=studioChanged)
    def studioActions(self):
        counts = self._studio_summary_cache.get("byAction", {})
        saved = self._studio_summary_cache.get("savedByAction", {})
        per_cls = (self.pose_studio.model_summary.get("per_class") or {}) if self.pose_studio.model_summary.get("loaded") else {}
        out = []
        for item in self._studio_actions:
            aid = item["id"]
            c_session = counts.get(aid, 0)
            c_saved = saved.get(aid, 0)
            acc = (per_cls.get(aid) or {}).get("accuracy")
            out.append({
                **item,
                "selected": self.pose_studio.selected_ground_truth == aid,
                "sessionCount": c_session,
                "savedCount": c_saved,
                "totalCount": c_session + c_saved,
                "accuracyText": f"{acc:.0f}%" if acc is not None else "",
            })
        return out

    @Property(str, notify=studioChanged)
    def studioGroundTruth(self):
        return self.pose_studio.selected_ground_truth

    @Property(str, notify=studioChanged)
    def studioGroundTruthLabel(self):
        return self._studio_label(self.pose_studio.selected_ground_truth)

    @Property(str, notify=studioChanged)
    def studioGroundTruthDesc(self):
        for item in self._studio_actions:
            if item["id"] == self.pose_studio.selected_ground_truth:
                return item["desc"]
        return ""

    @Property(str, notify=studioChanged)
    def studioCaptureMode(self):
        return self.pose_studio.capture_mode

    @Property(bool, notify=studioChanged)
    def studioStreaming(self):
        return bool(self.pose_studio.is_streaming)

    @Property(bool, notify=studioChanged)
    def studioDiscrepancyOnly(self):
        return bool(self.pose_studio.discrepancy_only)

    @Property(bool, notify=studioChanged)
    def studioHandTracked(self):
        return bool(self.pose_studio.hand_tracked)

    @Property(str, notify=studioChanged)
    def studioSystemAction(self):
        if not self.pose_studio.hand_tracked:
            return "손 대기 중"
        return self._studio_label(self.pose_studio.live_system_action)

    @Property(str, notify=studioChanged)
    def studioAiAction(self):
        if not self.pose_studio.model_summary.get("loaded"):
            return "미학습 (수집 후 학습 클릭)"
        if not self.pose_studio.hand_tracked:
            return "손 대기 중"
        hybrid_act = getattr(self.pose_studio, "live_hybrid_action", self.pose_studio.live_ai_action)
        hybrid_conf = getattr(self.pose_studio, "live_hybrid_confidence", self.pose_studio.live_ai_confidence)
        lbl = self._studio_label(hybrid_act)
        return f"{lbl} ({hybrid_conf * 100:.0f}%)"

    @Property(bool, notify=studioChanged)
    def studioLiveMatch(self):
        return bool(self.pose_studio.is_live_match())

    @Property(bool, notify=studioChanged)
    def studioRequireFullJoints(self):
        return bool(getattr(self.pose_studio, "require_full_joints", True))

    @Property(bool, notify=studioChanged)
    def studioFullJointsTracked(self):
        return bool(getattr(self.pose_studio, "full_joints_tracked", False))

    @Property(str, notify=studioChanged)
    def studioActiveModelId(self):
        return str(getattr(self.pose_studio, "active_model_id", "spatial_pose_mlp.json"))

    @Property("QVariantList", notify=studioChanged)
    def studioModelList(self):
        if hasattr(self.pose_studio, "list_models"):
            return self.pose_studio.list_models()
        return []

    @Property(str, notify=studioChanged)
    def studioCompareBadge(self):
        if not self.pose_studio.hand_tracked:
            return "카메라에 손을 보여주세요"
        if getattr(self.pose_studio, "require_full_joints", False) and not getattr(self.pose_studio, "full_joints_tracked", True):
            return "⚠️ 손가락 일부가 화면 밖으로 잘렸습니다 (전체 손을 보여주세요)"
        if self.pose_studio.is_live_match():
            return "✅ 판정 일치 (정상 인식 영역)"
        return "🚨 판정 불일치! (사각지대/오인식 구간 — 지금 수집하면 즉시 교정됩니다)"

    @Property(int, notify=studioChanged)
    def studioTotalSamples(self):
        return int(self._studio_summary_cache.get("total", 0))

    @Property(int, notify=studioChanged)
    def studioMaxSamples(self):
        return int(self._studio_summary_cache.get("maxSamples", 1000))

    @Property(str, notify=studioChanged)
    def studioSummaryText(self):
        s = self._studio_summary_cache
        return (f"현재 세션: {s.get('total', 0)}/{s.get('maxSamples', 1000)}개 "
                f"(일치 {s.get('matches', 0)} · 불일치 {s.get('discrepancies', 0)})  |  "
                f"누적 저장 파일: {s.get('savedFiles', 0)}개 (총 {s.get('savedTotal', 0) + s.get('total', 0)}표본)")

    @Property(str, notify=studioChanged)
    def studioModelText(self):
        ms = self.pose_studio.model_summary
        name = ms.get("name") or getattr(self.pose_studio, "active_model_id", "spatial_pose_mlp.json")
        if not ms.get("loaded"):
            return f"Spatial MLP [{name}]: 아직 학습 전 (표본을 모으고 [⚡ 4초 즉시 학습]을 누르세요)"
        return (f"Spatial MLP [{name}] 활성 · 학습 데이터 내부 확인(실제 정확도 아님) {ms.get('test_accuracy', 0):.1f}% "
                f"(학습 {ms.get('train_accuracy', 0):.1f}%) · 표본 {ms.get('total_samples', 0)}개 · "
                f"{ms.get('elapsed_ms', 0):.0f}ms 소요")

    @Property(bool, notify=studioChanged)
    def studioModelLoaded(self):
        return bool(self.pose_studio.model_summary.get("loaded"))

    @Property(str, notify=studioChanged)
    def studioNote(self):
        return str(self.pose_studio.status_note)

    @Slot(str)
    def setStudioCaptureMode(self, mode):
        if mode in ("snapshot", "stream"):
            self.pose_studio.set_capture_mode(mode)
            self._studio_summary_cache = self.pose_studio.summary()
            self.studioChanged.emit()

    @Slot(str)
    def setStudioGroundTruth(self, action_id):
        if self.pose_studio.set_ground_truth(action_id):
            self.studioChanged.emit()

    @Slot(int)
    def selectStudioActionByNumber(self, num):
        cat_actions = self.studioCategoryActions
        if 1 <= num <= len(cat_actions):
            self.setStudioGroundTruth(cat_actions[num - 1]["id"])
        elif 1 <= num <= len(self._studio_actions):
            self.setStudioGroundTruth(self._studio_actions[num - 1]["id"])

    @Slot(bool)
    def setStudioDiscrepancyOnly(self, enabled):
        self.pose_studio.set_discrepancy_only(bool(enabled))
        self.studioChanged.emit()

    @Slot(bool)
    def setStudioRequireFullJoints(self, enabled):
        if hasattr(self.pose_studio, "set_require_full_joints"):
            self.pose_studio.set_require_full_joints(bool(enabled))
            self.studioChanged.emit()

    @Slot(str)
    def createStudioModel(self, name):
        if not self.saveStudioDataset():
            return
        if hasattr(self.pose_studio, "create_model"):
            self.pose_studio.create_model(str(name))
            self._studio_summary_cache = self.pose_studio.summary()
            self.studioChanged.emit()

    @Slot(str)
    def activateStudioModel(self, model_id):
        if not self.saveStudioDataset():
            return
        if hasattr(self.pose_studio, "activate_model"):
            self.pose_studio.activate_model(str(model_id))
            self._studio_summary_cache = self.pose_studio.summary()
            self.studioChanged.emit()

    @Slot(str)
    def deleteStudioModel(self, model_id):
        if hasattr(self.pose_studio, "delete_model"):
            self.pose_studio.delete_model(str(model_id))
            self._studio_summary_cache = self.pose_studio.summary()
            self.studioChanged.emit()

    @Slot()
    def triggerStudioCapture(self):
        if self.training_job.running or self.studio.busy or self.tc.hub_job.running or self.rec_mode != "off": return
        if not self.pose_studio.samples:
            self.pose_studio.profile, self.pose_studio.split = self.tc.profile, self.tc.split
        self._pause()
        self.pose_studio.toggle_stream_or_snapshot()
        self._studio_summary_cache = self.pose_studio.summary()
        self.studioChanged.emit()

    @Slot()
    def undoStudioSample(self):
        self.pose_studio.undo_last()
        self._studio_summary_cache = self.pose_studio.summary()
        self.studioChanged.emit()

    @Slot()
    def clearStudioSamples(self):
        self.pose_studio.clear_samples()
        self._studio_summary_cache = self.pose_studio.summary()
        self.studioChanged.emit()

    @Slot(result=bool)
    def saveStudioDataset(self):
        if self.training_job.running or self.studio.busy or self.tc.hub_job.running or self.rec_mode != "off":
            return False
        try:
            self.pose_studio.save_dataset()
        except (OSError, ValueError) as error:
            self.pose_studio.status_note = f"표본 저장 실패 · 수집 표본은 유지됩니다. {error}"
            self.studioChanged.emit()
            return False
        self._studio_summary_cache = self.pose_studio.summary()
        self.studioChanged.emit()
        return True

    @Slot()
    def trainStudioModel(self):
        if self.training_job.running or self.studio.busy or self.tc.hub_job.running or self.rec_mode != "off": return
        self._pause()
        self.pose_studio.stop_stream()
        if not self.saveStudioDataset():
            return
        if hasattr(self.tc, "pose_train") and self.tc.pose_train(self.pose_studio.active_model_id):
            self._training_was_running = True
            self.training_timer.start()
            self.trainingChanged.emit()
        self._studio_summary_cache = self.pose_studio.summary()
        self.studioChanged.emit()

    @Slot()
    def openStudioSnapshotsFolder(self):
        self.pose_studio.snapshots_dir.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(str(self.pose_studio.snapshots_dir))  # noqa: S606

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
        self.cancelPalmCalibration()
        self.cancelPointerCalibration()
        self.choice = _HandChoice()
        self.candidate, self.remaining = "", 5
        self._set_stage("capture")

    @Slot()
    def cancelHandSetup(self):
        if self.control_hand:
            self._set_stage("main")

    def _save_hand(self, side: str) -> None:
        self.cancelPalmCalibration()
        self.control_hand = side
        self.runtime.control_hand = side
        self.preferences.setValue("controlHand", side)
        self._set("tracking.control_hand", side)
        self._load_palm_axes()
        self.axes = self._load_axes()
        self.runtime.set_override("pointer", "axes", self.axes)
        self._set_stage("main")
        if not self.preferences.value("tutorialDone", False, type=bool):
            if self.pointerSetupAvailable and not self.pointerOnly:
                self._open_pointer_setup(before_tutorial=True)
            else:
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

    # ------------------------------------------------------------------ pointer setup
    @Property(bool, constant=True)
    def pointerSetupAvailable(self):
        return getattr(self.runtime, "pointer_setup_dir", None) is not None

    @Property("QVariantMap", notify=pointerSetupChanged)
    def pointerSetup(self):
        return self._pointer_setup_view

    @Property(bool, notify=pointerSetupChanged)
    def pointerSetupActive(self):
        return bool(self._pointer_setup_view)

    @Slot()
    def beginPointerSetup(self):
        if (self.pointerSetupAvailable and not self.pointerOnly and self.control_hand and self.stage_name == "main"
                and self.rec_mode == "off" and not self.trackingActive):
            self._open_pointer_setup(before_tutorial=False)

    def _open_pointer_setup(self, *, before_tutorial):
        self.cancelPalmCalibration()
        self.cancelPointerCalibration()
        self.tutorial = None
        self._setup_before_tutorial = before_tutorial
        self._pointer_setup_view = {"phase": "intro"}
        self._set_stage("tutorial")
        self.pointerSetupChanged.emit()

    @Slot(str)
    def pointerSetupAction(self, action):
        if self.stage_name != "tutorial" or not self._pointer_setup_view:
            return
        if action == "skip":
            self._close_pointer_setup()
        elif action in ("capture", "retest"):
            self.runtime.request(RuntimeRequest("pointer_setup", action))
        elif action in ("start", "apply"):
            if self.runtime.request(RuntimeRequest("pointer_setup", action)):
                self._pointer_setup_view = {"phase": "waiting"}
                self.pointerSetupChanged.emit()

    @Slot(str, float)
    def pointerSetupAdjust(self, key, value):
        if self.stage_name == "tutorial" and self._pointer_setup_view.get("phase") in ("aim", "preview", "review"):
            self.runtime.request(RuntimeRequest("pointer_setup", "adjust", {key: value}))

    def _close_pointer_setup(self):
        self.runtime.request(RuntimeRequest("pointer_setup", "skip"))
        self._send(RequestMode(None))
        self._pointer_setup_view = {}
        self.pointerSetupChanged.emit()
        if self._setup_before_tutorial:
            self.beginTutorial()
        else:
            self._set_stage("main")

    # ------------------------------------------------------------------ tutorial
    @Property(bool, constant=True)
    def tutorialAvailable(self):
        return True

    @Slot()
    def beginTutorial(self):
        if not self.control_hand:
            return
        self.cancelPointerCalibration()
        self.tutorial = Tutorial(onboarding_steps(self._cards()))
        self._set_stage("tutorial")
        self._reset_practice()
        self.tutorialChanged.emit()

    @Slot()
    def tutorialNext(self):
        if self.tutorial is not None:
            self.tutorial.next()
            self._reset_practice()
            self.tutorialChanged.emit()

    @Slot()
    def tutorialBack(self):
        if self.tutorial is not None:
            self.tutorial.back()
            self._reset_practice()
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
        old_index = t.index
        changed = False
        for event in view.practice_events:
            if event.seq <= self._last_practice_seq:
                continue
            self._last_practice_seq = event.seq
            if view.practice_epoch < self._practice_min_epoch:
                continue
            # Each retained tap/begin is a distinct engine event, even when the UI missed
            # every intervening frame. Keep its own cursor position for target hit testing.
            t.observe((), event.motion_on, event.t, hand_tracked=event.has_hand,
                      point=event.cursor, precision=event.precision)
            changed = t.observe(event.actions, event.motion_on, event.t,
                                hand_tracked=event.has_hand, point=event.cursor,
                                precision=event.precision) or changed
        current_actions = view.actions if view.snapshot is None else tuple(a for a in view.actions if a == "mouse.move")
        changed = t.observe(current_actions if view.landmarks is not None else (), self.motion_on, now,
                            hand_tracked=view.landmarks is not None, point=view.practice_cursor,
                            precision=view.practice_precision) or changed
        changed = t.tick(now) or changed
        if t.index != old_index:
            self._reset_practice()
        progress = round(t.progress, 2)
        if changed or progress != self._tutorial_progress or t.step_key == "sensitivity":
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

    def _reset_practice(self):
        from manux.engine.commands import ResetPractice
        self._practice_min_epoch = self._preview_view.practice_epoch + 1
        if self._preview_view.practice_events:
            self._last_practice_seq = max(self._last_practice_seq, self._preview_view.practice_events[-1].seq)
        self._send(ResetPractice())

    @Slot()
    def tutorialRetry(self):
        if self.tutorial:
            self.tutorial.retry()
            self._reset_practice()
            self.tutorialChanged.emit()

    @Property(str, notify=tutorialChanged)
    def tutorialKey(self):
        return self.tutorial.step_key if self.tutorial else ""

    @Property("QVariantList", notify=tutorialChanged)
    def tutorialTargets(self):
        return self.tutorial.targets if self.tutorial else []

    @Property("QVariantList", notify=tutorialChanged)
    def tutorialCursor(self):
        p = self.tutorial.cursor if self.tutorial else None
        return list(p) if p else []

    @Property(int, notify=tutorialChanged)
    def tutorialFailures(self):
        return self.tutorial.failures if self.tutorial else 0

    @Property(bool, notify=tutorialChanged)
    def tutorialCanSkip(self):
        return bool(self.tutorial and self.tutorial.can_skip)

    @Property(str, notify=tutorialChanged)
    def tutorialHelp(self):
        return self.tutorial.help_text if self.tutorial else ""

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
    def mlpBadge(self):
        return self.mlp_badge_text

    @Property(str, notify=liveChanged)
    def detectedContacts(self):
        return self.contact_text

    @Slot(bool)
    def showFingertips(self, visible):
        return None      # v0.3 drew numbers into the frame; v0.4 does not draw into frames (yet)

    @Property(str, notify=previewChanged)
    def previewUrl(self):
        return f"image://vision/{self.sequence}" if self.sequence else ""

    @Property("QVariantList", notify=previewChanged)
    def previewLandmarks(self):
        v = self._preview_view
        return [list(p) for p in v.landmarks] if v.image is not None and v.landmarks is not None else []

    @Property(str, notify=previewChanged)
    def previewHandLabel(self):
        return {"Right": "조작 손: 오른손", "Left": "조작 손: 왼손"}.get(self._preview_view.control_hand, "조작 손 대기")

    @Property(str, notify=previewChanged)
    def previewMode(self):
        snap = self._preview_view.snapshot
        return snap.mode if snap else "대기"

    @Property(str, notify=previewChanged)
    def previewGesture(self):
        return self._preview_view.preview_gesture or "동작 대기"

    @Property("QVariantList", notify=previewChanged)
    def previewGuides(self):
        return [list(g) for g in self._preview_view.preview_guides]

    @Property("QVariantList", notify=previewChanged)
    def previewAnchor(self):
        return list(self._preview_view.preview_anchor or ())

    @Property(bool, notify=previewChanged)
    def previewMoving(self):
        return self._preview_view.preview_moving

    # ------------------------------------------------------------------ keys, window, tray
    @Slot(int)
    def command(self, key):
        if self.pointerSetupActive and key != KEY_Q:
            if key == KEY_ESC:
                self._close_pointer_setup()
            elif key == KEY_SPACE and self._pointer_setup_view.get("hand") and not self._pointer_setup_view.get("capturing"):
                if self._pointer_setup_view.get("phase") in ("rest", "horizontal", "up", "down"):
                    self.pointerSetupAction("capture")
            return
        if self.palmCalibrating and key != KEY_Q:
            if key == KEY_ESC:
                self.cancelPalmCalibration()
            return
        if key == KEY_SPACE and self.layout == "studio" and (
                self._studio_center_open or self._progress_visible or self.rec_mode != "off"
                or bool(self.window.property("learningDialogOpen"))):
            if not self._pose_studio_visible:
                return
        if key == 111 and self.layout == "studio":
            self._pause()
            self.settingsRequested.emit()
            return
        if key == 110 and self.layout == "studio":
            if self.stage_name == "main" and self.rec_mode == "off" and not self.calibrating:
                self._pause()
                self.runtime.request(RuntimeRequest("next_monitor"))
            return
        if self.calibrating and key != KEY_Q:
            if key == KEY_ESC:
                self.cancelPointerCalibration()
            return
        if key == KEY_Q:
            self.closed = True
        elif self.stage_name == "main" and (self.page == "studio" or self._pose_studio_visible):
            if self.ai_customization.poseStudioVisible:
                if key == KEY_SPACE:
                    self.ai_customization.capturePoseStudioExample()
                elif key == KEY_ESC:
                    self.ai_customization.cancelPoseStudioCapture()
            elif key == KEY_SPACE:
                self.triggerStudioCapture()
            elif key == KEY_ESC and self.pose_studio.is_streaming:
                self.pose_studio.stop_stream()
                self.studioChanged.emit()
            elif key in (9, 16777217):
                self.nextStudioCategory()
            elif 49 <= key <= 57:  # keys '1'..'9' (supports 9th class: both click)
                self.selectStudioActionByNumber(key - 48)
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
        advanced = getattr(self.runtime, "advanced_result", (0, True, ""))
        if advanced[0] != getattr(self, "_advanced_revision", 0):
            self._advanced_revision = advanced[0]
            self._advanced_pending = False
            self._advanced_status = advanced[2]
            self.advancedChanged.emit()
            self.sensitivityChanged.emit()
            self.stateChanged.emit()
            if advanced[1]:
                self.advancedApplied.emit()
        result = getattr(self.runtime, "sensitivity_result", (0, True, ""))
        if result[0] != getattr(self, "_sensitivity_revision", 0):
            self._sensitivity_revision = result[0]
            self._sensitivity_pending = False
            self._sensitivity_status = result[2]
            self.sensitivityChanged.emit()
            if result[1]:
                self.sensitivityApplied.emit()
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
        elif self._badge_now() != self.motion_badge:
            self.motion_badge = self._badge_now()
            self.stateChanged.emit()

    def apply_view(self, view: ViewFrame) -> None:
        self._preview_view = view
        self._update_phone(view.phone)
        # Notify overlays even with no image so a lost hand cannot leave old joints behind.
        self.previewChanged.emit()
        if self._pointer_setup_view and view.pointer_setup is not None:
            current = dict(view.pointer_setup)
            for key in ("target", "cursor"):
                if current.get(key) is not None:
                    current[key] = list(current[key])
            if current.get("phase") == "saved":
                self._close_pointer_setup()
            elif current != self._pointer_setup_view:
                self._pointer_setup_view = current
                self.pointerSetupChanged.emit()
        if view.spatial_guard_enabled != self._spatial_guard_enabled:
            self._spatial_guard_enabled = view.spatial_guard_enabled
            self.studioChanged.emit()
        if view.pose_status and view.pose_status != self.studio.pose_note:
            self.studio.pose_note = view.pose_status
            self.studio.changed.emit()
        self._update_palm_view(view)
        snap = view.snapshot
        if snap is not None and self.stage_name == "main" and self._control_on != snap.control_on:
            self._control_on = snap.control_on
            self._control_notice_until = time.perf_counter() + 1.8
        if snap is not None and self.stage_name == "main" and self.paused == snap.control_on:
            self.paused = not snap.control_on
            self.stateChanged.emit()
        if self.edge_overlay is not None:
            enabled = bool(self.settings.get("boundary_alert.enabled"))
            margin = float(self.settings.get("boundary_alert.margin"))
            control_active = bool(snap is not None and snap.control_on and snap.has_hand and view.landmarks is not None)
            if enabled and control_active:
                prox = calc_edge_proximity(view.landmarks, mirrored=view.mirrored, margin=margin)
                self.edge_overlay.set_proximities(prox)
            else:
                self.edge_overlay.clear()
        rec_view = dict(view.rec) if view.rec is not None else None
        if rec_view != self.rec_view:
            self.rec_view = rec_view
            if rec_view is not None and rec_view.get("mode") == "off":
                self.rec_mode = "off"
                self.studio.refresh()
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
        if self.page == "studio" or self._pose_studio_visible:
            self.ai_customization.observePoseStudioFrame(view.landmarks, view.aspect)
            sys_act = (self._studio_sys_fn(view.rule_preview or snap, view.actions) if self._studio_sys_fn is not None
                       else ("none" if not view.actions else view.actions[0]))
            if sys_act in ("none", "thumb_middle", "thumb_ring") and view.preview_guides:
                pinched_tips = {tip for tip, state in view.preview_guides if state in ("pinched", "action")}
                if (
                    view.landmarks is not None
                    and len(view.landmarks) >= 17
                    and (12 in pinched_tips or 16 in pinched_tips or sys_act in ("thumb_middle", "thumb_ring"))
                ):
                    t_pt, m_pt, r_pt = view.landmarks[4], view.landmarks[12], view.landmarks[16]
                    if (
                        math.hypot(t_pt[0] - m_pt[0], t_pt[1] - m_pt[1]) <= 0.052
                        and math.hypot(t_pt[0] - r_pt[0], t_pt[1] - r_pt[1]) <= 0.048
                    ):
                        pinched_tips.update((12, 16))
                if 12 in pinched_tips and 16 in pinched_tips:
                    sys_act = "thumb_middle_ring"
                elif sys_act == "none":
                    if 16 in pinched_tips:
                        sys_act = "thumb_ring"
                    elif 12 in pinched_tips:
                        sys_act = "thumb_middle"
                    elif 8 in pinched_tips:
                        sys_act = "thumb_index"
                    elif 20 in pinched_tips:
                        sys_act = "thumb_pinky"
            prev_samples_len = len(self.pose_studio.samples)
            prev_hand = self.pose_studio.hand_tracked
            res = self.pose_studio.observe_frame(view.landmarks, view.world, sys_act)
            now_samples_len = len(self.pose_studio.samples)
            now_hand = self.pose_studio.hand_tracked

            now_t = time.perf_counter()
            captured = bool(res and res.get("success"))
            hand_toggled = (now_hand != prev_hand)
            if captured or now_samples_len != prev_samples_len or hand_toggled:
                self._studio_summary_cache = self.pose_studio.summary()
                self._last_studio_emit = now_t
                self.studioChanged.emit()
            elif now_t - getattr(self, "_last_studio_emit", 0.0) >= 0.033:
                self._last_studio_emit = now_t
                self.studioChanged.emit()
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
        if view.mlp_badge != self.mlp_badge_text:
            self.mlp_badge_text = view.mlp_badge
            self.liveChanged.emit()
        if (state, live) != (self.state, self.live_gesture):
            self.state, self.live_gesture = state, live
            self.liveChanged.emit()
        if not view.camera_ok and not self.provider.image.isNull():
            self.provider.image = QImage()
            self.sequence += 1
            self.previewChanged.emit()
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
        if getattr(self, "ai_customization", None) is not None:
            self.ai_customization.close()
        if getattr(self, "_closed_qml", False):
            return
        self._closed_qml = True
        if getattr(self, "edge_overlay", None) is not None:
            try:
                self.edge_overlay.clear()
                self.edge_overlay.close()
                self.edge_overlay.deleteLater()
            except Exception:
                pass
            self.edge_overlay = None
        self.timer.stop()
        self.training_timer.stop()
        if self.screen_preview is not None:
            self.screen_preview.close()
        self.studio.close()
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
        normalize_combo=None, combo_label=None, make_tray=None, make_edge_overlay=None, layout=None) -> int:
    """Run the desktop window until the user quits. The composition root passes the platform pieces."""
    app = QApplication.instance() or QApplication([])
    preferences = QSettings(str(paths.config_dir / "ui.ini"), QSettings.Format.IniFormat)
    layout = layout or preferences.value("layout", "standard", type=str)
    edge_overlay = make_edge_overlay() if (make_edge_overlay is not None and screenshot is None) else None
    bridge = Bridge(runtime, settings, paths.config_dir, services=services, hotkeys=hotkeys, layout=layout,
                    hotkey_defaults=hotkey_defaults, normalize_combo=normalize_combo or (lambda c: c),
                    combo_label=combo_label or (lambda c: c), hide_to_tray=tray and make_tray is not None,
                    edge_overlay=edge_overlay)
    preferences.setValue("layout", layout)
    preferences.sync()
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
        if edge_overlay is not None:
            try:
                edge_overlay.clear()
                edge_overlay.close()
                edge_overlay.deleteLater()
            except Exception:
                pass
        runtime.stop()
        bridge.close()
    return 0
