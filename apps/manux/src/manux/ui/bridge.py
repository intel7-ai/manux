"""The QML bridge: v0.3's `QtDashboard` interface on the v0.4 runtime (port of hand-mouse/src/qt_dashboard.py).

`ui/qml/dashboard.qml` is v0.3's screen with its latest fixes, unchanged. This object exposes the
same properties and slots under the same names (context property `dashboard`). Differences:
- it renders `ViewFrame`s from the desktop runtime and sends `UiCommand`s; it never decides gestures,
- settings go through `settings.store.Settings` (v0.4 keys) and are saved to the user data folder,
- not ported yet, shown as such: v0.3's frame drawings.

Only `run()` creates Qt objects for the whole app; the runtime thread never touches them.
"""
from __future__ import annotations

import math
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
            f"{view.fps:.1f} fps  ·  추론 {view.detect_ms:.1f} ms",
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
