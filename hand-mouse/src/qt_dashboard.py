"""Qt Quick desktop shell. Shares the existing engine loop with the Tk shell."""
from collections import deque
from pathlib import Path
import time
import math

import cv2
from PySide6.QtCore import QObject, Property, QSettings, Signal, Slot, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtQuickControls2 import QQuickStyle

from src.dashboard import ACTIONS
from src.hand_selection import HandSelection, selected_detection
from src.pointer_workspace import calibrated_pointer, pointer_axes


class CameraImage(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self.image = QImage()

    def requestImage(self, image_id, size, requested_size):
        return self.image


class QtDashboard(QObject):
    stateChanged = Signal()
    previewChanged = Signal()

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

    @Slot()
    def togglePointerPreset(self):
        if self.stage_name != "main":
            return
        self.commands.clear()
        self.commands.append(27)
        self.use_index_pointer = not self.use_index_pointer
        self.preferences.setValue("indexPointer", self.use_index_pointer)
        self.stateChanged.emit()

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

    @Property(str, notify=stateChanged)
    def inputState(self):
        return self.state[0]

    @Property(str, notify=stateChanged)
    def tracking(self):
        return self.state[1]

    @Property(str, notify=stateChanged)
    def currentAction(self):
        return self.state[2]

    @Property(str, notify=stateChanged)
    def controlLabel(self):
        return self.state[3]

    @Slot(bool)
    def showFingertips(self, visible):
        self.show_fingertips = visible

    @Property(str, notify=stateChanged)
    def detectedContacts(self):
        return self.contact_text

    @Property(str, notify=stateChanged)
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
            self.stateChanged.emit()
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
        self.window.close()
        self.application.processEvents()
