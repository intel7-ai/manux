"""Qt Quick desktop shell. Shares the existing engine loop with the Tk shell."""
from collections import deque
from pathlib import Path
import time
import math
import subprocess
import sys

import cv2
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Property, QSettings, Signal, Slot, QUrl
from PySide6.QtGui import QCursor, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtQuickControls2 import QQuickStyle

from src import app_paths, global_hotkeys, profiles
from src.dashboard import ACTIONS
from src.gesture_engine import DEFAULT_CONFIG_PATH
from src.hand_selection import HandSelection, selected_detection
from src.pointer_workspace import calibrated_pointer, pointer_axes
from src.tracking_test import load_recent_results

LOG_DIR = app_paths.LOG_DIR
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
    receptionChanged = Signal()       # tray / overlay state (ok, weak, none, paused)
    overlayChanged = Signal()         # overlay shown / hidden / moved
    fingerFrameChanged = Signal()     # finger tracker around the cursor
    hiddenToTray = Signal()           # the window was closed into the tray
    trackingActiveChanged = Signal()  # only when the test window opens or closes

    def __init__(self, dry_run=False, *, hide_to_tray=False, start_hidden=False,
                 overlay=False, overlay_corner="top-right"):
        super().__init__()
        self.commands = deque()
        self.closed = False
        # Tray mode (--headless / tray available): closing the window hides it instead of quitting
        self.hide_to_tray = bool(hide_to_tray)
        self.overlay_visible = bool(overlay)
        self.overlay_corner = overlay_corner if overlay_corner in (
            "top-left", "top-right", "bottom-left", "bottom-right") else "top-right"
        self.reception = ("paused", "입력 꺼짐")
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
        # QApplication (not QGuiApplication): the tray menu is a Qt Widgets QMenu
        self.application = QApplication.instance() or QApplication([])
        self.application.setQuitOnLastWindowClosed(False)
        self.preferences = QSettings("MANUX", "Hand-Mouse")
        saved = self.preferences.value("controlHand", "", type=str)
        self.control_hand = saved if saved in ("Left", "Right") else ""
        self.use_index_pointer = self.preferences.value("indexPointer", True, type=bool)
        saved_click = self.preferences.value("pointerClick", "pinch", type=str)
        # "pinch": thumb + middle/ring (existing). "trigger": option A, thumb on the index side.
        # "off": cursor tracking only, no click detection at all.
        self.click_style = saved_click if saved_click in CLICK_STYLES else "pinch"
        self.profile = profiles.get_active(DEFAULT_CONFIG_PATH.parent)   # calibration profile in use
        self.motion_request = 0                                 # +1 per press of the motion-mode button
        self.motion_state = (False, 0.0, "", 0.0, 0.0)           # (on, seconds left, last command, entering, leaving)
        self.motion_exit_until = 0.0                             # the "motion mode off" notice stays until then
        self.motion_badge = (False, "")                          # (visible, text) as last shown
        saved_anchor = self.preferences.value("pointerAnchor", "index", type=str)
        # Which point of the hand the cursor follows: index fingertip or palm centre.
        self.pointer_anchor = saved_anchor if saved_anchor in ("index", "palm") else "index"
        # Tracking test (main.py owns the TrackingTest; this only shows it)
        self.tracking_view = None           # plain view of the runtime's TrackingTest
        self.tracking_requested = False
        self.tracking_close_requested = False
        self.metrics = ("", -1, False)  # camera mode text, brightness 0..255 (-1 unknown), dark
        # Hand-area crop on/off (A/B with the tracking test) and failure recording
        self.hand_crop = self.preferences.value("handCrop", False, type=bool)
        self.finger_overlay = self.preferences.value("fingerOverlay", False, type=bool)
        self.export_text = ""
        self.finger_frame = None      # latest finger tracker frame (src/finger_overlay.py) or None
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

        # System-wide shortcuts (src/global_hotkeys.py): on/off and hide/show from any program.
        # Callbacks run on the hotkey thread, so they only queue work for poll() on this thread.
        self.ui_requests = deque()
        self.shortcuts = {action: global_hotkeys.normalize(self.preferences.value(f"shortcut/{action}", combo, type=str))
                          or combo for action, combo in global_hotkeys.DEFAULTS.items()}
        self.hotkeys = global_hotkeys.GlobalHotkeys({
            "toggle": lambda: self.ui_requests.append("toggle"),
            "hide": lambda: self.ui_requests.append("hide"),
        })
        self.hotkeys.start(self.shortcuts)

        QQuickStyle.setStyle("Basic")
        self.engine = QQmlApplicationEngine()
        self.provider = CameraImage()
        self.engine.addImageProvider("vision", self.provider)
        self.engine.rootContext().setContextProperty("dashboard", self)
        self.engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("dashboard.qml"))))
        if not self.engine.rootObjects():
            raise RuntimeError("MANUX Qt 화면을 열지 못했습니다.")
        self.window = self.engine.rootObjects()[0]
        if start_hidden and self.control_hand:
            self.window.hide()  # tray start; without a chosen hand the setup screen must show


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

    # ------------------------------------------------------------------ finger tracker around the cursor
    @Slot(bool)
    def setFingerOverlay(self, enabled):
        if bool(enabled) == self.finger_overlay:
            return
        self.finger_overlay = bool(enabled)
        self.preferences.setValue("fingerOverlay", self.finger_overlay)
        self.stateChanged.emit()

    @Property(bool, notify=stateChanged)
    def fingerOverlay(self):
        return self.finger_overlay

    def set_finger_frame(self, frame):
        if frame is None and self.finger_frame is None:
            return
        self.finger_frame = frame
        self.fingerFrameChanged.emit()

    @Property("QVariantMap", notify=fingerFrameChanged)
    def fingerFrame(self):
        """tips, contact, mode and the cursor position (logical px); empty when there is nothing to draw."""
        if self.finger_frame is None:
            return {}
        cursor = QCursor.pos()
        return dict(self.finger_frame, cursor=[cursor.x(), cursor.y()])

    # ------------------------------------------------------------------ failure recording
    @Slot()
    def markFailure(self):
        self.failure_mark_requested = True

    @Slot()
    def exportLogs(self):
        """Zip the logs into Downloads (src/log_bundle.py) and show the file in Explorer."""
        from src import log_bundle, updater
        out_dir = Path.home() / "Downloads"
        if not out_dir.is_dir():
            out_dir = Path.home()
        try:
            path = log_bundle.build_bundle(LOG_DIR, DEFAULT_CONFIG_PATH, out_dir, updater.current_version(),
                                           extra={"camera": self.metrics[0], "brightness": self.metrics[1]})
        except OSError as exc:
            self.export_text = f"로그를 묶지 못했습니다: {exc}"
        else:
            self.export_text = f"저장했습니다: {path}"
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(path)])
        self.failureChanged.emit()

    @Property(str, notify=failureChanged)
    def exportText(self):
        return self.export_text

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

    # ------------------------------------------------------------------ motion mode
    @Property(str, notify=stateChanged)
    def motionLabel(self):
        on, left, last = self.motion_state[:3]
        if on:
            return "모션 모드 · 켜짐 (눌러서 끄기)" + (f" · {last}" if last else "")
        return "모션 모드 · 꺼짐 (눌러서 켜기)"

    def _motion_badge_now(self):
        """(visible, text) of the corner badge: on while motion mode is on, 'entering' while the pose is
        held, and a short 'off' notice after it ends."""
        on, left, last, entering, leaving = self.motion_state
        if on:
            if leaving >= 0.2:
                return True, f"모션 모드 끄는 중… {int(leaving * 100)}%"
            text = "모션 모드 켜짐" + (f" · {left:.0f}초" if left > 0 else "")
            return True, text + (f" · {last}" if last else "")
        if entering >= 0.2:
            return True, f"모션 모드 진입 중… {int(entering * 100)}%"
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
        return bool(self.motion_state[0])

    @Property(bool, notify=stateChanged)
    def motionOn(self):
        return bool(self.motion_state[0])

    @Slot()
    def toggleMotion(self):
        if self.stage_name != "main":
            return
        self.motion_request += 1
        self.stateChanged.emit()

    # ------------------------------------------------------------------ calibration profile
    @Property(str, notify=stateChanged)
    def profileLabel(self):
        return f"프로필 · {self.profile or '없음'}"

    @Property(int, notify=stateChanged)
    def profileCount(self):
        return len(profiles.list_profiles(DEFAULT_CONFIG_PATH.parent))

    @Slot()
    def cycleProfile(self):
        """none -> first profile -> next ... -> none. Pauses control like the other settings."""
        if self.stage_name != "main":
            return
        names = [None] + profiles.list_profiles(DEFAULT_CONFIG_PATH.parent)
        try:
            i = names.index(self.profile)
        except ValueError:
            i = -1
        self.commands.clear()
        self.commands.append(27)
        self.profile = names[(i + 1) % len(names)]
        self.stateChanged.emit()

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
    # The runtime thread owns the TrackingTest; the dashboard only shows `tracking_view`,
    # a plain dict copied from it each frame (see src/qt_runtime.tracking_view).
    @property
    def tracking_only(self):
        """No click detection: click style "off", or while the tracking test runs."""
        return self.click_style == "off" or self.tracking_view is not None

    @Slot()
    def beginTrackingTest(self):
        if self.stage_name != "main" or self.tracking_view is not None or self.tracking_requested:
            return
        self.tracking_requested = True  # the UI loop forwards it; the test starts paused

    @Slot()
    def closeTrackingTest(self):
        if self.tracking_view is None and not self.tracking_requested:
            return
        self.tracking_requested = False
        self.tracking_close_requested = True
        self.set_tracking_view(None)

    def set_tracking_view(self, view):
        was = self.tracking_view is not None
        self.tracking_view = view
        if was != (view is not None):
            self.stateChanged.emit()
            self.trackingActiveChanged.emit()
        self.trackingChanged.emit()

    # Bound to the window's visibility / screen. They must not re-evaluate every frame:
    # on Windows re-applying FullScreen and the screen 30 times a second froze the app.
    @Property(bool, notify=trackingActiveChanged)
    def trackingActive(self):
        return self.tracking_view is not None

    @Property(str, notify=trackingActiveChanged)
    def trackingScreen(self):
        screen = QGuiApplication.primaryScreen()
        return screen.name() if screen is not None else ""

    def _view(self, key, default):
        v = self.tracking_view
        return default if v is None else v.get(key, default)

    @Property(float, notify=trackingChanged)
    def trackingTargetX(self):
        t = self._view("target", None)
        return t[0] if t else -1.0

    @Property(float, notify=trackingChanged)
    def trackingTargetY(self):
        t = self._view("target", None)
        return t[1] if t else -1.0

    @Property(float, notify=trackingChanged)
    def trackingTargetSize(self):
        """Diameter as a fraction of the test monitor's width."""
        t = self._view("target", None)
        return t[2] if t else 0.0

    @Property(float, notify=trackingChanged)
    def trackingCursorX(self):
        return float(self._view("cursor", (.5, .5))[0])

    @Property(float, notify=trackingChanged)
    def trackingCursorY(self):
        return float(self._view("cursor", (.5, .5))[1])

    @Property(str, notify=trackingChanged)
    def trackingStatus(self):
        v = self.tracking_view
        if v is None:
            return ""
        if v["done"]:
            return "완료 · " + v["summary"]
        progress = f"표적 {v['index'] + 1}/{v['total']}"
        if v["paused"]:
            return f"일시정지됨 · Space를 눌러 시작하세요 ({progress})"
        return (progress + " · " + ("멈춰 있기…" if v["holding"] else "커서를 원 안으로 옮겨 0.5초 멈추세요")
                + ("" if v["hand_present"] else " · 손을 찾는 중"))

    @Property(bool, notify=trackingChanged)
    def trackingPaused(self):
        return bool(self._view("paused", False)) and not self._view("done", False)

    @Property(bool, notify=trackingChanged)
    def trackingDone(self):
        return bool(self._view("done", False))

    # ------------------------------------------------------------------ runtime thread link
    def ui_state(self):
        from src.qt_runtime import UiState
        return UiState(
            stage=self.stage_name, control_hand=self.control_hand,
            use_index_pointer=self.use_index_pointer, click_style=self.click_style,
            anchor=self.pointer_anchor, hand_crop=self.hand_crop, axes=self.axes,
            calibrating=self.calibrating, sensitivity=self.pointer_sensitivity,
            show_fingertips=self.show_fingertips, profile=self.profile, motion_request=self.motion_request,
            finger_overlay=self.finger_overlay,
        )

    def apply_snapshot(self, snap):
        """Render one runtime snapshot (UI thread)."""
        if self.stage_name == "capture":
            self.select_hand(snap.detections, snap.mirrored)
        if self.use_index_pointer:
            self.update_pointer(snap.tip, snap.source, snap.mirrored, snap.aspect)
        if snap.metrics is not None:
            self.set_camera_metrics(*snap.metrics)
        if self.tracking_view is not None or snap.tracking is not None:
            if not self.tracking_close_requested:
                self.set_tracking_view(snap.tracking)
        if snap.tracking is None:
            self.tracking_close_requested = False
        if snap.motion is not None:
            if self.motion_state[0] and not snap.motion[0]:
                self.motion_exit_until = time.perf_counter() + 1.8       # keep a short "off" notice on screen
            self.motion_state = snap.motion
            badge = self._motion_badge_now()
            if badge != self.motion_badge:
                self.motion_badge = badge
                self.stateChanged.emit()
        if snap.failures is not None:
            self.set_failures(*snap.failures)
        if snap.recent_line:
            self.add_recent(snap.recent_line)
        self.set_finger_frame(snap.hand)
        self.show(snap.frame, snap.detected, snap.paused, snap.action, snap.fps, snap.detect_ms,
                  snap.status, contacts=snap.contacts)

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

    @Slot()
    def quitApp(self):
        self.closed = True

    @Property(bool, constant=True)
    def hidesToTray(self):
        return self.hide_to_tray

    @Slot()
    def hideDashboard(self):
        self.window.hide()
        self.hiddenToTray.emit()

    @Slot()
    def showDashboard(self):
        self.window.show()
        self.window.raise_()
        self.window.requestActivate()

    # ------------------------------------------------------------------ reception (tray + overlay)
    def _update_reception(self, detected, paused):
        if self.stage_name != "main":
            value = ("paused", "손 설정 필요")
        elif paused:
            value = ("paused", "입력 꺼짐")
        elif not detected:
            value = ("none", "손이 보이지 않음")
        elif self.metrics[2]:
            value = ("weak", "어두움 · 손 인식 약함")
        else:
            value = ("ok", "손 인식 중")
        if value != self.reception:
            self.reception = value
            self.receptionChanged.emit()

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
        """Primary screen's available area [x, y, w, h] (logical px), where the pill sits."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return [0, 0, 1280, 720]
        g = screen.availableGeometry()
        return [g.x(), g.y(), g.width(), g.height()]

    def poll(self):
        self.application.processEvents()
        while self.ui_requests:
            request = self.ui_requests.popleft()
            if request == "toggle":
                self.command(32)
            elif request == "hide":
                self.toggle_window()
        return self.commands.popleft() if self.commands else -1

    def toggle_window(self):
        if self.window.isVisible() and self.window.visibility() != self.window.Visibility.Minimized:
            if self.hide_to_tray:
                self.hideDashboard()
            else:
                self.window.showMinimized()
        else:
            self.showDashboard()

    # ------------------------------------------------------------------ system-wide shortcuts
    @Slot(str, str, result=str)
    def setShortcut(self, action, combo):
        """Change a shortcut; returns "" or why it was not taken (the old one then stays)."""
        if action not in self.shortcuts:
            return "알 수 없는 단축키입니다."
        value = global_hotkeys.normalize(combo)
        if value is None:
            return "Ctrl·Alt·Shift 중 하나 이상과 글자·숫자·F키 하나를 함께 누르세요."
        if any(v == value for a, v in self.shortcuts.items() if a != action):
            return "다른 기능이 이미 쓰는 조합입니다."
        old = dict(self.shortcuts)
        self.shortcuts[action] = value
        if action in self.hotkeys.set(self.shortcuts):
            self.shortcuts = old
            self.hotkeys.set(self.shortcuts)
            return "다른 프로그램이 이미 쓰는 조합입니다. 다른 조합을 고르세요."
        self.preferences.setValue(f"shortcut/{action}", value)
        self.stateChanged.emit()
        return ""

    @Slot(str)
    def resetShortcut(self, action):
        if action in global_hotkeys.DEFAULTS:
            self.setShortcut(action, global_hotkeys.DEFAULTS[action])

    @Property("QVariantMap", notify=stateChanged)
    def shortcutLabels(self):
        return {action: global_hotkeys.label(combo) + (" (사용 불가)" if action in self.hotkeys.failed else "")
                for action, combo in self.shortcuts.items()}

    def show(self, frame, detected, paused, action, fps, detect_ms, status="", contacts=()):
        state = (
            "입력 꺼짐" if paused else "연습 모드" if self.dry_run else "입력 활성",
            "손 감지됨" if detected else "손 대기",
            "제어 시작을 누르면 손짓으로 조작할 수 있어요." if paused
            else ACTIONS.get(action, "손짓 대기") if detected else "손을 카메라에 보여주세요.",
            "제어 시작" if paused else "일시정지",
            f"{fps:.1f} fps  ·  추론 {detect_ms:.1f} ms" + (f"  ·  {status}" if status else ""),
        )
        self._update_reception(detected, paused)
        contact_text = " + ".join(map(str, contacts))
        if (state, contact_text) != (self.state, self.contact_text):
            self.state = state
            self.contact_text = contact_text
            # Not stateChanged: that would re-evaluate every settings binding (and rebuild
            # the guide list) 30 times a second just because the fps text changed.
            self.liveChanged.emit()
        if not self.window.isVisible():
            return  # hidden in the tray: no preview conversion at all
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
        self.hotkeys.stop()
        for root in self.engine.rootObjects():
            root.close()
            root.deleteLater()
        self.application.processEvents()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
