"""Qt Quick desktop shell. Shares the existing engine loop with the Tk shell."""
from collections import deque
from pathlib import Path
import time
import math

import cv2
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Property, QSettings, Signal, Slot, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtQuickControls2 import QQuickStyle

from src.dashboard import ACTIONS
from src.hand_selection import HandSelection, selected_detection
from src.pointer_workspace import calibrated_pointer, pointer_axes
from src.tracking_test import load_recent_results

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
RECENT_LIMIT = 5


class CameraImage(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self.image = QImage()

    def requestImage(self, image_id, size, requested_size):
        return self.image


CLICK_STYLES = ("pinch", "trigger", "off")
CLICK_LABELS = {
    "pinch": "클릭 · 엄지+중지/약지",
    "trigger": "클릭 · 엄지 방아쇠 (A안)",
    "off": "클릭 · 끔 (트래킹만)",
}


class QtDashboard(QObject):
    stateChanged = Signal()
    previewChanged = Signal()
    trackingChanged = Signal()        # every frame while the test runs (target, cursor, text)
    liveChanged = Signal()            # per-frame status (fps, action...); settings stay on stateChanged
    metricsChanged = Signal()         # camera mode / brightness, a few times a second
    recentChanged = Signal()          # tracking test result list
    failureChanged = Signal()         # failure recording summary
    trackingActiveChanged = Signal()  # only when the test window opens or closes

    def __init__(self, dry_run=False):
        super().__init__()
        self.commands = deque()
        self.closed = False
        self.dry_run = dry_run
        self.last_preview = 0.0
        self.sequence = 0
        self.contact_text = ""
        self.show_fingertips = False
        self.calibration_key = ""
        self.axes = None
        self.samples = []
        self.calibrating = False
        self.calibration_error = ""
        self.tip = None
        self.tip_at = 0.0
        self.aspect = 1.0
        self.state = ("입력 꺼짐", "카메라 연결 대기", "손을 카메라에 보여주세요.", "제어 시작", "")
        self.application = QGuiApplication([])
        self.application.setQuitOnLastWindowClosed(False)
        self.preferences = QSettings("MANUX", "Hand-Mouse")
        saved = self.preferences.value("controlHand", "", type=str)
        self.control_hand = saved if saved in ("Left", "Right") else ""
        self.use_index_pointer = self.preferences.value("indexPointer", True, type=bool)
        saved_click = self.preferences.value("pointerClick", "pinch", type=str)
        # "pinch": thumb + middle/ring (existing). "trigger": option A, thumb on the index side.
        # "off": cursor tracking only, no click detection at all.
        self.click_style = saved_click if saved_click in CLICK_STYLES else "pinch"
        saved_anchor = self.preferences.value("pointerAnchor", "index", type=str)
        # Which point of the hand the cursor follows: index fingertip or palm centre.
        self.pointer_anchor = saved_anchor if saved_anchor in ("index", "palm") else "index"
        # Tracking test (main.py owns the TrackingTest; this only shows it)
        self.tracking_test = None
        self.tracking_requested = False
        self.tracking_cursor = (.5, .5)
        self.tracking_result = ""
        self.metrics = ("", -1, False)  # camera mode text, brightness 0..255 (-1 unknown), dark
        # Hand-area crop on/off (A/B with the tracking test) and failure recording
        self.hand_crop = self.preferences.value("handCrop", False, type=bool)
        self.failure_mark_requested = False
        self.failure_summary = "아직 저장된 기록이 없습니다."
        self.failure_last = ""
        self.recent = load_recent_results(LOG_DIR, RECENT_LIMIT)
        try:
            saved_sensitivity = self.preferences.value("pointerSensitivity", 100, type=int)
        except (TypeError, ValueError):
            saved_sensitivity = 100
        self.pointer_sensitivity = max(50, min(150, saved_sensitivity))
        self.stage_name = "splash"
        self.selection = HandSelection()
        self.candidate = ""
        self.remaining = 5

        QQuickStyle.setStyle("Basic")
        self.engine = QQmlApplicationEngine()
        self.provider = CameraImage()
        self.engine.addImageProvider("vision", self.provider)
        self.engine.rootContext().setContextProperty("dashboard", self)
        self.engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("dashboard.qml"))))
        if not self.engine.rootObjects():
            raise RuntimeError("MANUX Qt 화면을 열지 못했습니다.")
        self.window = self.engine.rootObjects()[0]

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
        value = max(50, min(150, value))
        if value != self.pointer_sensitivity:
            self.pointer_sensitivity = value
            self.preferences.setValue("pointerSensitivity", value)
            self.stateChanged.emit()

    @Slot()
    def persistPointerSettings(self):
        self.preferences.sync()

    @Property(str, notify=stateChanged)
    def pointerPreset(self):
        return "검지 · 기본 마우스" if self.use_index_pointer else "손바닥 · Dan 기본"

    def _pause_for_change(self):
        self.commands.clear()
        self.commands.append(27)  # settings change while paused, like the old toggles

    @Slot(bool)
    def setPointerPreset(self, index_pointer):
        if self.stage_name != "main" or bool(index_pointer) == self.use_index_pointer:
            return
        self._pause_for_change()
        self.use_index_pointer = bool(index_pointer)
        self.preferences.setValue("indexPointer", self.use_index_pointer)
        self.stateChanged.emit()

    @Slot(str)
    def setClickStyle(self, style):
        if self.stage_name != "main" or style not in CLICK_STYLES or style == self.click_style:
            return
        self._pause_for_change()
        self.click_style = style
        self.preferences.setValue("pointerClick", style)
        self.stateChanged.emit()

    @Slot(str)
    def setAnchor(self, anchor):
        if self.stage_name != "main" or anchor not in ("index", "palm") or anchor == self.pointer_anchor:
            return
        self._pause_for_change()
        self.pointer_anchor = anchor
        self.preferences.setValue("pointerAnchor", anchor)
        self.stateChanged.emit()

    @Slot(bool)
    def setHandCrop(self, enabled):
        if self.stage_name != "main" or bool(enabled) == self.hand_crop:
            return
        self._pause_for_change()
        self.hand_crop = bool(enabled)
        self.preferences.setValue("handCrop", self.hand_crop)
        self.stateChanged.emit()

    @Property(bool, notify=stateChanged)
    def handCrop(self):
        return self.hand_crop

    # ------------------------------------------------------------------ failure recording
    @Slot()
    def markFailure(self):
        self.failure_mark_requested = True

    def set_failures(self, summary, last_file):
        self.failure_summary, self.failure_last = summary, last_file
        self.failureChanged.emit()

    @Property(str, notify=failureChanged)
    def failureSummary(self):
        return self.failure_summary

    @Property(str, notify=failureChanged)
    def failureLast(self):
        return self.failure_last

    @Property(str, notify=stateChanged)
    def clickStyle(self):
        return self.click_style

    @Property(str, notify=stateChanged)
    def pointerAnchor(self):
        return self.pointer_anchor

    # ------------------------------------------------------------------ camera metrics
    def set_camera_metrics(self, mode_text, brightness, low_light):
        value = (mode_text or "", int(brightness), bool(low_light))
        if value != self.metrics:
            self.metrics = value
            self.metricsChanged.emit()

    @Property(str, notify=metricsChanged)
    def cameraMode(self):
        return self.metrics[0]

    @Property(int, notify=metricsChanged)
    def brightness(self):
        return self.metrics[1]

    @Property(bool, notify=metricsChanged)
    def lowLight(self):
        return self.metrics[2]

    # ------------------------------------------------------------------ recent tracking results
    def add_recent(self, line):
        self.recent = ([line] + self.recent)[:RECENT_LIMIT]
        self.recentChanged.emit()

    @Property("QVariantList", notify=recentChanged)
    def recentResults(self):
        return list(self.recent)

    @Slot()
    def togglePointerPreset(self):
        if self.stage_name != "main":
            return
        self.commands.clear()
        self.commands.append(27)
        self.use_index_pointer = not self.use_index_pointer
        self.preferences.setValue("indexPointer", self.use_index_pointer)
        self.stateChanged.emit()

    @Property(bool, notify=stateChanged)
    def triggerClick(self):
        return self.click_style == "trigger"

    @Property(bool, notify=stateChanged)
    def clickOff(self):
        return self.click_style == "off"

    @Property(str, notify=stateChanged)
    def clickStyleLabel(self):
        return CLICK_LABELS[self.click_style]

    @Slot()
    def toggleClickStyle(self):
        if self.stage_name != "main":
            return
        self.commands.clear()
        self.commands.append(27)  # pause first, like the preset switch
        i = CLICK_STYLES.index(self.click_style)
        self.click_style = CLICK_STYLES[(i + 1) % len(CLICK_STYLES)]
        self.preferences.setValue("pointerClick", self.click_style)
        self.stateChanged.emit()

    @property
    def tracking_only(self):
        """No click detection: click style "off", or while the tracking test runs."""
        return self.click_style == "off" or self.tracking_test is not None

    @Property(str, notify=stateChanged)
    def anchorLabel(self):
        return "기준점 · 검지 끝" if self.pointer_anchor == "index" else "기준점 · 손바닥 중심"

    @Slot()
    def toggleAnchor(self):
        if self.stage_name != "main":
            return
        self.commands.clear()
        self.commands.append(27)
        self.pointer_anchor = "palm" if self.pointer_anchor == "index" else "index"
        self.preferences.setValue("pointerAnchor", self.pointer_anchor)
        self.stateChanged.emit()

    # ------------------------------------------------------------------ tracking test
    @Slot()
    def beginTrackingTest(self):
        if self.stage_name != "main" or self.tracking_test is not None:
            return
        self.commands.clear()
        self.commands.append(27)  # paused; Space in the test window starts control
        self.tracking_result = ""
        self.tracking_requested = True

    def set_tracking(self, test):
        self.tracking_test = test
        self.stateChanged.emit()
        self.trackingActiveChanged.emit()
        self.trackingChanged.emit()

    def tracking_frame(self, cursor_fraction):
        self.tracking_cursor = cursor_fraction
        self.trackingChanged.emit()

    def tracking_finished(self, text):
        self.tracking_result = text
        self.trackingChanged.emit()

    @Slot()
    def closeTrackingTest(self):
        if self.tracking_test is None:
            return
        self.commands.clear()
        self.commands.append(27)
        self.tracking_test = None
        self.stateChanged.emit()
        self.trackingActiveChanged.emit()
        self.trackingChanged.emit()

    # Bound to the window's visibility / screen. They must not re-evaluate every frame:
    # on Windows re-applying FullScreen and the screen 30 times a second froze the app.
    @Property(bool, notify=trackingActiveChanged)
    def trackingActive(self):
        return self.tracking_test is not None

    @Property(str, notify=trackingActiveChanged)
    def trackingScreen(self):
        screen = QGuiApplication.primaryScreen()
        return screen.name() if screen is not None else ""

    def _target(self):
        return self.tracking_test.current if self.tracking_test is not None else None

    @Property(float, notify=trackingChanged)
    def trackingTargetX(self):
        t = self._target()
        return t.fx if t else -1.0

    @Property(float, notify=trackingChanged)
    def trackingTargetY(self):
        t = self._target()
        return t.fy if t else -1.0

    @Property(float, notify=trackingChanged)
    def trackingTargetSize(self):
        """Diameter as a fraction of the test monitor's width."""
        t = self._target()
        return t.size_px / self.tracking_test.monitor.width if t else 0.0

    @Property(float, notify=trackingChanged)
    def trackingCursorX(self):
        return float(self.tracking_cursor[0])

    @Property(float, notify=trackingChanged)
    def trackingCursorY(self):
        return float(self.tracking_cursor[1])

    @Property(str, notify=trackingChanged)
    def trackingStatus(self):
        test = self.tracking_test
        if test is None:
            return ""
        if test.done:
            return "완료 · " + (self.tracking_result or test.summary_text())
        if test.paused:
            return f"일시정지됨 · Space를 눌러 시작하세요 (표적 {test.index + 1}/{len(test.targets)})"
        t = test.current
        holding = t.inside_since is not None
        return (f"표적 {test.index + 1}/{len(test.targets)} · "
                + ("멈춰 있기…" if holding else "커서를 원 안으로 옮겨 0.5초 멈추세요")
                + ("" if test.hand_present else " · 손을 찾는 중"))

    @Property(bool, notify=trackingChanged)
    def trackingPaused(self):
        return self.tracking_test is not None and self.tracking_test.paused and not self.tracking_test.done

    @Property(bool, notify=trackingChanged)
    def trackingDone(self):
        return self.tracking_test is not None and self.tracking_test.done

    def update_pointer(self, point, source, mirrored, aspect):
        key = f"pointerAxes/{self.control_hand}/{source}/{int(mirrored)}/{aspect:.4f}"
        if key != self.calibration_key:
            self.calibration_key = key
            saved = self.preferences.value(key, [])
            try:
                axes = tuple(map(float, saved))
                self.axes = axes if len(axes) == 4 and all(map(math.isfinite, axes)) and math.hypot(*axes[:2]) and math.hypot(*axes[2:]) else None
            except (TypeError, ValueError):
                self.axes = None
            self.calibrating = False
            self.stateChanged.emit()
        self.aspect = aspect
        self.tip = point if point is not None and all(0 <= value <= 1 for value in point) else None
        self.tip_at = time.perf_counter()

    def pointer_position(self, point):
        return calibrated_pointer(point, self.axes, self.aspect)

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
        return (
            "1/3 편하게 손을 든 위치에서 Enter",
            "2/3 검지를 오른쪽으로 조금 옮긴 뒤 Enter",
            "3/3 처음 위치에서 검지를 위로 조금 옮긴 뒤 Enter",
        )[len(self.samples)]

    @Slot()
    def beginPointerCalibration(self):
        if not self.calibration_key:
            return
        self.commands.clear()
        self.commands.append(27)
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
        self.commands.clear()
        self.commands.append(27)
        self.preferences.remove(self.calibration_key)
        self.axes = None
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
                axes = pointer_axes(*self.samples, self.aspect)
                self.samples.clear()
                if axes is None:
                    self.calibration_error = "방향 차이가 작아요. 처음 위치에서 다시 Enter를 눌러주세요."
                else:
                    self.axes = axes
                    self.preferences.setValue(self.calibration_key, list(axes))
                    self.preferences.sync()
                    self.calibrating = False
        self.stateChanged.emit()

    @Property(str, notify=stateChanged)
    def chosenHand(self):
        return {"Left":"왼손", "Right":"오른손"}.get(self.control_hand, "")

    @Property(str, notify=stateChanged)
    def handCandidate(self):
        return {"Left":"왼손", "Right":"오른손"}.get(self.candidate, "")

    @Property(int, notify=stateChanged)
    def secondsRemaining(self):
        return self.remaining

    @Slot()
    def finishSplash(self):
        self.stage_name = "main" if self.control_hand else "welcome"
        self.stateChanged.emit()

    @Slot()
    def beginHandSetup(self):
        self.cancelPointerCalibration()
        self.commands.clear()
        self.commands.append(27)
        self.stage_name = "capture"
        self.selection.reset()
        self.candidate = ""
        self.remaining = 5
        self.stateChanged.emit()

    def save_hand(self, side):
        self.control_hand = side
        self.preferences.setValue("controlHand", self.control_hand)
        self.preferences.sync()
        self.stage_name = "main"
        self.stateChanged.emit()

    @Slot()
    def cancelHandSetup(self):
        if self.control_hand:
            self.stage_name = "main"
            self.stateChanged.emit()

    def select_hand(self, detections, mirrored):
        if self.stage_name == "capture":
            now = time.perf_counter()
            selected = self.selection.observe(detections, mirrored, now)
            if selected:
                self.save_hand(selected)
            else:
                candidate = self.selection.side
                remaining = math.ceil(self.selection.seconds_remaining(now))
                if (candidate, remaining) != (self.candidate, self.remaining):
                    self.candidate = candidate
                    self.remaining = remaining
                    self.stateChanged.emit()
        if self.stage_name != "main":
            return None
        return selected_detection(detections, self.control_hand, mirrored)

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

    @Slot(bool)
    def showFingertips(self, visible):
        self.show_fingertips = visible

    @Property(str, notify=liveChanged)
    def detectedContacts(self):
        return self.contact_text

    @Property(str, notify=liveChanged)
    def details(self):
        return self.state[4]

    @Property(str, notify=previewChanged)
    def previewUrl(self):
        return f"image://vision/{self.sequence}" if self.sequence else ""

    @Slot(int)
    def command(self, key):
        if self.calibrating and key != 81:
            if key == 27:
                self.cancelPointerCalibration()
            return
        if self.stage_name == "main" or key == 81:
            self.commands.append(key)

    @Slot()
    def requestClose(self):
        self.closed = True

    def poll(self):
        self.application.processEvents()
        return self.commands.popleft() if self.commands else -1

    def show(self, frame, detected, paused, action, fps, detect_ms, status="", contacts=()):
        state = (
            "입력 꺼짐" if paused else "연습 모드" if self.dry_run else "입력 활성",
            "손 감지됨" if detected else "손 대기",
            "제어 시작을 누르면 손짓으로 조작할 수 있어요." if paused
            else ACTIONS.get(action, "손짓 대기") if detected else "손을 카메라에 보여주세요.",
            "제어 시작" if paused else "일시정지",
            f"{fps:.1f} fps  ·  추론 {detect_ms:.1f} ms" + (f"  ·  {status}" if status else ""),
        )
        contact_text = " + ".join(map(str, contacts))
        if (state, contact_text) != (self.state, self.contact_text):
            self.state = state
            self.contact_text = contact_text
            # Not stateChanged: that would re-evaluate every settings binding (and rebuild
            # the guide list) 30 times a second just because the fps text changed.
            self.liveChanged.emit()
        now = time.perf_counter()
        if now - self.last_preview < 1 / 24:
            return
        self.last_preview = now
        height, width = frame.shape[:2]
        if width > 900:
            frame = cv2.resize(frame, (900, round(height * 900 / width)))
            height, width = frame.shape[:2]
        self.provider.image = QImage(frame.data, width, height, frame.strides[0], QImage.Format_BGR888).copy()
        self.sequence += 1
        self.previewChanged.emit()

    def close(self):
        """Tear the QML side down before this object goes away.

        Otherwise the windows outlive `dashboard` at interpreter exit and every binding
        logs "Cannot read property ... of null" (dozens of lines after Ctrl+C or Q).
        """
        if getattr(self, "_closed_qml", False):
            return
        self._closed_qml = True
        for root in self.engine.rootObjects():
            root.close()
            root.deleteLater()
        self.application.processEvents()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
