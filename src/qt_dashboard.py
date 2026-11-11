"""Qt Quick desktop shell. Shares the existing engine loop with the Tk shell."""
import json
from collections import deque
from pathlib import Path
import copy
import time
import math

import cv2
from PySide6.QtCore import (QCoreApplication, QEvent, QMetaObject, QObject, Property, QSettings, Qt, QThread, QTimer,
                            Signal, Slot, QUrl)
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtQuickControls2 import QQuickStyle

from src import profiles
from src.dashboard import ACTIONS
from src.gesture_engine import DEFAULT_CONFIG_PATH
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


def text_input_has_focus() -> bool:
    """True while the keyboard goes into a text field, in any of the app's windows (TextField, SpinBox text, ...)."""
    item = QGuiApplication.focusObject()
    return item is not None and (item.inherits("QQuickTextInput") or item.inherits("QQuickTextEdit"))



def _timer(owner, action: str) -> None:
    """Start or stop owner.training_timer on the screen thread, whichever thread asks (a QTimer only works from the
    thread it lives in). An owner without a Qt thread (the plain objects some tests use) is driven directly."""
    thread = getattr(owner, "thread", None)
    if callable(thread) and QThread.currentThread() is not thread():
        QMetaObject.invokeMethod(owner, "_timer_" + action, Qt.ConnectionType.QueuedConnection)
        return
    getattr(owner.training_timer, action)()



def _saved_box(text: str):
    from src.virtual_hand import DEFAULT_BOX, valid_box
    try:
        box = tuple(float(v) for v in text.split(","))
    except ValueError:
        return DEFAULT_BOX
    return box if valid_box(box) else DEFAULT_BOX


def _screen_ratio() -> float:
    try:
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        return float(screen.devicePixelRatio()) if screen is not None else 1.0
    except Exception:
        return 1.0


def _virtual_hand_default() -> bool:
    """config "virtual_hand_enabled" (on unless set to false)."""
    try:
        cfg = json.loads((Path(__file__).resolve().parents[1] / "config" / "gestures.json").read_text(encoding="utf-8"))
        return bool(cfg.get("virtual_hand_enabled", True))
    except (OSError, ValueError):
        return True

class QtDashboard(QObject):
    stateChanged = Signal()
    previewChanged = Signal()
    trackingChanged = Signal()        # every frame while the test runs (target, cursor, text)
    liveChanged = Signal()            # per-frame status (fps, action...); settings stay on stateChanged
    metricsChanged = Signal()         # camera mode / brightness, a few times a second
    recentChanged = Signal()          # tracking test result list
    failureChanged = Signal()         # failure recording summary
    correctionChanged = Signal()      # online learning: the "what was that really?" question
    learnChanged = Signal()           # online learning: background learning rounds
    virtualHandChanged = Signal()     # the hand drawn at the cursor
    fingerFrameChanged = Signal()     # the fingertips drawn next to the cursor (src/FingerTracker.qml)
    llmChanged = Signal()             # gestures connected in words (src/llm_binding.py)
    drawChanged = Signal()            # drawing mode overlay (src/draw_mode.py)
    drawShapesChanged = Signal()      # drawing mode shapes and their commands (settings)
    registrationChanged = Signal()    # online learning: adding a new gesture
    receptionChanged = Signal()       # tray / overlay state (ok, weak, none, paused)
    overlayChanged = Signal()         # overlay shown / hidden / moved
    hiddenToTray = Signal()           # the window was closed into the tray
    trackingActiveChanged = Signal()  # only when the test window opens or closes
    trainingChanged = Signal()        # training center: data, scores, training job, export
    motionTemplatesDone = Signal(str, object, str)   # (key, result, error) from the template worker thread

    def __init__(self, dry_run=False, *, hide_to_tray=False, start_hidden=False,
                 overlay=False, overlay_corner="top-right", tcn_app=False):
        super().__init__()
        self.tcn_app = bool(tcn_app)
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
        self.preferences = (QSettings(str(DEFAULT_CONFIG_PATH.parent / "tcn-ui.ini"), QSettings.IniFormat)
                            if self.tcn_app else QSettings("MANUX", "Hand-Mouse"))
        saved = self.preferences.value("controlHand", "", type=str)
        self.control_hand = saved if saved in ("Left", "Right") else ""
        self.use_index_pointer = (False if self.tcn_app
                                  else self.preferences.value("indexPointer", True, type=bool))
        saved_click = self.preferences.value("pointerClick", "pinch", type=str)
        # "pinch": thumb + middle/ring (existing). "trigger": option A, thumb on the index side.
        # "off": cursor tracking only, no click detection at all.
        self.click_style = saved_click if saved_click in CLICK_STYLES else "pinch"
        self.profile = profiles.get_active(DEFAULT_CONFIG_PATH.parent)   # calibration profile in use
        self.motion_request = 0                                 # +1 per press of the motion-mode button
        self.palm_axes_requests = [0, 0, 0]                     # start / cancel / reset presses (palm cursor direction)
        self.rec_mode = "off"                                   # dataset recording: off | guided | free
        self.rec_advance = 0                                    # +1 per press of "start" in guided recording
        self.rec_view = None                                    # latest recording view from the runtime
        self.rec_focus = ""                                     # guided recording of one gesture only
        self.rec_plan = None
        self.rec_paused = False
        self.rec_discard = 0                                    # +1 per press of "녹화 취소(버리기)"
        self.last_rec_review = None                             # post-recording confirmation banner state
        self._training_summary = {}
        from src.recording_plan_editor import RecordingPlanEditor
        self.recording_editor = RecordingPlanEditor()
        self.recording_editor.startRequested.connect(self._start_focused_plan)
        self.motionTemplatesDone.connect(self._finish_motion_templates)
        self.model_version = 0                                  # +1 per newly trained model (runtime reloads the gate)
        from src.training_center import (
            TrainingJob,
            detect_gpu_training_capability,
            get_augmentation_settings,
            get_training_device,
            get_training_mode,
        )
        self.training_job = TrainingJob()
        self.training_rows = []                                 # one row per gesture (src/training_center.gesture_rows)
        self.training_files = []                                # per-file quality & selection rows
        self.model_backups = []                                 # restorable model snapshots & exports
        self.onnx_models = []                                   # selectable ONNX models with per-gesture rates
        self.selected_onnx_model_id = "composite:active"        # currently selected ONNX model ID in simulator
        self.spatial_spray = {}                                 # 2D spatial heatmap/spray distribution for selected ONNX model
        self.spray_gesture_filter = "all"                       # "all" or specific gesture label for spatial spray
        self.training_improvement = {}                          # latest before-vs-after training improvement evaluation
        self.sim_result = {}                                    # latest simulator run result (lightweight header)
        self._sim_playback_frames = []                          # per-frame skeleton & event data kept in Python
        self.sim_eval = {}                                      # latest simulator data quality & calibration evaluation
        self.selected_sim_file = ""                             # currently selected simulator target recording file
        self._pending_sim_request = None                        # queued (filename, use_cleaned) if requested while busy
        self.aug_settings = get_augmentation_settings()         # augmentation enabled / strength / factor settings
        saved_train_mode = self.preferences.value("trainingMode", "", type=str)
        self.training_mode = saved_train_mode if saved_train_mode in ("fast", "thorough") else get_training_mode()
        self._gpu_cap = detect_gpu_training_capability()
        saved_train_dev = self.preferences.value("trainingDevice", "", type=str)
        self.training_device_pref = saved_train_dev if saved_train_dev in ("auto", "gpu", "cpu") else get_training_device()
        self.diag_result = {}                                   # latest app self-diagnostics result
        from tools import hub as hf_hub
        self.hub_config = hf_hub.load_hub_config()
        self.hub_status_note = ""
        self.training_info = ("", "")                           # (data line, model line)
        self.training_storage = ("", False)                     # (recordings size in git, past the split limit)
        self.training_busy = ""                                 # "읽는 중…" / "내보내는 중…"
        self._task_title = ""                                   # active background task title
        self._task_step = ""                                    # active background task detailed step
        self._task_progress = -1.0                              # 0.0..1.0 when determinate, -1.0 when indeterminate
        self._task_started = 0.0                                # unix timestamp when current busy task started
        self.training_note = ""
        self._training_was_running = False
        self.training_timer = QTimer()
        self.training_timer.setInterval(400)
        self.training_timer.timeout.connect(self._training_tick)
        from src.gesture_gate import DEFAULT_MODEL
        self.tcn_gate = DEFAULT_MODEL.exists()                  # intent check by the trained model: on when one exists
        self.gate_model = "tcn"                                 # or "online": the model learned from this person
        self.palm_axes_view = (False, "", 0.0, "", False)       # running, prompt, progress, note, saved
        self.motion_state = (False, 0.0, "", 0.0, 0.0)           # (on, seconds left, last command, entering, leaving)
        self.motion_exit_until = 0.0                             # the "motion mode off" notice stays until then
        self.toggle_progress = 0.0                               # progress of the on/off sign hold
        self.close_state = (0.0, 0.0)                            # (hold progress 0..1, seconds left to confirm) of the close-window sign
        self.motion_badge = (False, "")                          # (visible, text) as last shown
        saved_anchor = self.preferences.value("pointerAnchor", "index", type=str)
        # Which point of the hand the cursor follows: index fingertip or palm centre.
        self.pointer_anchor = saved_anchor if saved_anchor in ("index", "palm") else "index"
        # Skeleton points the cursor follows (palm mode, and "직접 지정" in index mode); () = palm centre default
        from src.gesture_engine import normalize_anchor_joints
        self.anchor_joints = normalize_anchor_joints(self.preferences.value("cursorAnchorJoints", "", type=str))
        # Tracking test (main.py owns the TrackingTest; this only shows it)
        self.tracking_view = None           # plain view of the runtime's TrackingTest
        self.tracking_requested = False
        self.tracking_close_requested = False
        self.metrics = ("", -1, False)  # camera mode text, brightness 0..255 (-1 unknown), dark
        # Hand-area crop on/off (A/B with the tracking test) and failure recording
        self.hand_crop = self.preferences.value("handCrop", False, type=bool)
        self.failure_mark_requested = False
        self.online_ready = False
        self.online_commands = []
        self.registration_view = None
        self.live_gesture = ""
        self.virtual_hand = None
        self.virtual_hand_on = self.preferences.value("virtualHand", _virtual_hand_default(), type=bool)
        self.finger_overlay = self.preferences.value("fingerOverlay", False, type=bool)
        self.finger_frame = {}
        self.preview_hand = {}            # shown with the preview image it belongs to (src/HandSkeleton.qml)
        self._pending_preview_hand = {}
        self.llm_provider = str(self.preferences.value("llmProvider", "claude") or "claude")
        self.llm_busy = False
        self.llm_message = ""
        self.llm_result = {}
        self.llm_models = []
        self._llm_session_keys = {}       # keys typed this run where they cannot be saved encrypted
        self.pose_probe_view = None       # [손 모양 보여주기]: countdown / holding / done / failed
        self.captured_key = ""            # [키 기억하기]: the combination pressed
        self.key_capturing = False
        self.draw_view = {}               # drawing mode: pen, stroke, last shape (from the runtime)
        self.draw_message = ""
        self.draw_test = {}               # what a stroke drawn in the settings is recognised as
        self._draw_store = None
        self.virtual_box = _saved_box(self.preferences.value("virtualBox", "", type=str))
        self.box_editing = False
        self.my_gestures = []
        self.deleted_gestures = set()
        self.command_message = ""
        self.online_root = None
        self.online_job = None
        self.learn_seen = -1
        self.learn_message = ""
        self.learn_summary = {}
        self.correction_sample = None
        self.correction_store = None
        self.correction_choices = []
        self.correction_guess = ""
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
        # the Studio theme's font (src/theme/Studio.qml), bundled so Korean shows whatever fonts the PC has
        from PySide6.QtGui import QFontDatabase
        QFontDatabase.addApplicationFont(str(Path(__file__).with_name("assets") / "fonts" / "NotoSansKR.ttf"))
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

    @Property(str, constant=True)
    def appTitle(self):
        return "Hand-Mouse · MANUX TCN" if self.tcn_app else "Hand-Mouse · MANUX Qt"

    @Property(str, constant=True)
    def appName(self):
        return "MANUX TCN" if self.tcn_app else "MANUX"

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
        if not self.correction_sample:       # F again while the question is open: nothing new to mark
            self.failure_mark_requested = True

    # ------------------------------------------------------------------ adding a new gesture (online learning)
    def set_online_ready(self, ready):
        self.online_ready = bool(ready)
        self.registrationChanged.emit()

    def set_registration(self, view):
        if view != self.registration_view:
            self.registration_view = view
            if (view or {}).get("state") == "done":
                self.refreshMyGestures()
            self.registrationChanged.emit()

    # ------------------------------------------------------------------ background learning (online learning, phase 4)
    def _online_job(self):
        if self.online_job is None:
            from src.online.app_job import OnlineJob
            from src.online.samples import DEFAULT_ROOT
            self.online_job = OnlineJob(self.online_root or DEFAULT_ROOT)
        return self.online_job

    @Slot()
    def startOnlineLearning(self):
        if self._online_job().start():
            self.learn_message = "학습을 시작했어요. 카메라와 마우스는 그대로 쓸 수 있어요."
            self.learnChanged.emit()

    @Slot()
    def rollbackOnlineModel(self):
        if self._online_job().start(rollback=True):
            self.learn_message = "이전 모델로 되돌리는 중…"
            self.learnChanged.emit()

    @Slot()
    def refreshOnlineSummary(self):
        from src.online.app_job import summary
        from src.online.samples import DEFAULT_ROOT
        try:
            self.learn_summary = summary(self.online_root or DEFAULT_ROOT)
        except Exception:
            self.learn_summary = {}
        self.learnChanged.emit()

    def poll_online_job(self):
        """Called every loop of the UI thread: turns the job's progress into words once it changes."""
        job = self.online_job
        if job is None or job.version == self.learn_seen:
            return
        self.learn_seen = job.version
        state = dict(job.state)
        phase = state.get("phase")
        if phase == "update":
            self.learn_message = f"학습 중… {state.get('done', 0)}/{state.get('total', 0)}"
        elif phase in ("starting", "loading"):
            self.learn_message = "데이터를 읽는 중…"
        elif phase == "checking":
            self.learn_message = "학습에 안 쓴 데이터로 검사 중…"
        elif phase == "error":
            self.learn_message = f"학습하지 못했어요: {state.get('message', '')}"
        elif phase == "done":
            self.learn_message = self._learn_result(state)
            self.online_commands.append(("online_reload",))
            self.refreshOnlineSummary()
        self.learnChanged.emit()

    def _learn_result(self, state):
        from src.online.update_job import reason_ko
        from src.online.collector import correction_choices
        names = {c["label"]: c["name"] for c in correction_choices()}
        status = state.get("status")
        if status == "promoted":
            text = "새 모델을 반영했어요."
            if state.get("untested_new_classes"):
                text += " (" + ", ".join(state["untested_new_classes"]) + "은 데이터가 적어 따로 검사하지 못했어요)"
            return text
        if status == "rejected":
            reasons = list(dict.fromkeys(reason_ko(p, names) for p in state.get("problems", [])))
            return "검사에서 떨어져 지금 모델을 그대로 써요: " + ", ".join(reasons[:3])
        if status == "rolled_back":
            return state.get("message") or "이전 모델로 되돌렸어요."
        if status == "nothing_to_roll_back":
            return "되돌릴 이전 모델이 없어요."
        return state.get("message") or "학습할 정정·확인 데이터가 없어요. F를 눌러 정정하거나 새 동작을 등록해 주세요."

    @Property(bool, notify=learnChanged)
    def learnRunning(self):
        return self.online_job is not None and self.online_job.running

    @Property(bool, notify=learnChanged)
    def learnCanUse(self):
        """A model learned from this person exists (production), so it can confirm gestures."""
        return bool(self.learn_summary.get("has_production"))

    @Property(bool, notify=learnChanged)
    def learnCanRollback(self):
        return bool(self.learn_summary.get("can_rollback"))

    @Property(str, notify=learnChanged)
    def learnText(self):
        if self.learn_message:
            return self.learn_message
        s = self.learn_summary
        if not s:
            return ""
        last = s.get("last")
        when = f" · 마지막 학습 {last['time'][5:16]}" if last else " · 아직 학습 안 함"
        return f"정정·확인한 데이터 {s.get('samples', 0)}개" + when

    @Slot()
    def refreshMyGestures(self):
        """The gestures a person registered, with the command of each."""
        from src.online.commands import command_of, command_text
        from src.online.pose_memory import POSE_FILE, PoseMemory
        from src.online.registration import USER_SOURCE
        from src.online.samples import DEFAULT_ROOT
        rows = []
        path = (self.online_root or DEFAULT_ROOT) / POSE_FILE
        if path.is_file():
            try:
                memory = PoseMemory.load(path)
                for entry in memory.classes.values():
                    if entry.source == USER_SOURCE and entry.label not in self.deleted_gestures:
                        value = command_of(entry.label)
                        rows.append({"label": entry.label, "value": value, "text": command_text(value)})
            except (OSError, ValueError) as exc:
                print(f"[Online] 내 동작 목록을 읽지 못했어요: {exc}")
        self.my_gestures = rows
        self.registrationChanged.emit()

    @Property("QVariantList", notify=registrationChanged)
    def myGestures(self):
        return self.my_gestures

    @Property("QVariantList", constant=True)
    def commandChoices(self):
        from src.online.commands import choices
        return choices()

    @Property(str, notify=registrationChanged)
    def commandMessage(self):
        return self.command_message

    @Slot(str, str, result=bool)
    def setGestureCommand(self, label, value):
        """Empty value = show on the screen only."""
        from src.online.commands import CommandError, set_command
        try:
            text = set_command(label, value)
        except CommandError as exc:
            self.command_message = str(exc)
            self.registrationChanged.emit()
            return False
        self.command_message = f"'{label}' → {text} 연결했어요"
        self.refreshMyGestures()
        return True

    @Slot()
    def clearCommandMessage(self):
        self.command_message = ""
        self.registrationChanged.emit()

    @Slot(str)
    def deleteMyGesture(self, label):
        self.deleted_gestures.add(label)
        self.online_commands.append(("gesture_delete", label))
        self.command_message = f"'{label}'을(를) 지웠어요"
        self.refreshMyGestures()

    # ------------------------------------------------------------------ the hand drawn on the screen
    def set_virtual_hand(self, view, paused=False):
        if view is None and self.virtual_hand is None:
            return
        if view is not None and view.get("screen"):
            # the box mode's point is in physical pixels (like the OS cursor); windows are placed in logical ones
            from PySide6.QtGui import QGuiApplication
            ratio = QGuiApplication.primaryScreen().devicePixelRatio() or 1.0
            x, y = view["screen"][0] / ratio, view["screen"][1] / ratio
        else:
            from PySide6.QtGui import QCursor
            pos = QCursor.pos()         # logical pixels, the same space as window positions (DPI handled by Qt)
            x, y = pos.x(), pos.y()
        self.virtual_hand = None if view is None else {**view, "x": x, "y": y, "paused": bool(paused)}
        self.virtualHandChanged.emit()
        if view is not None and view.get("switcher"):
            self._raise_virtual_hand()

    def _raise_virtual_hand(self):
        """The Windows task switcher opens above every always-on-top window: put the hand back above it (a few times a second)."""
        import sys
        import time
        now = time.monotonic()
        if now - getattr(self, "_vh_raised", 0.0) < .25 or sys.platform != "win32":
            return
        self._vh_raised = now
        try:
            import ctypes
            win = self.engine.rootObjects()[0].findChild(QObject, "virtualHandWindow")
            if win is None or not win.isVisible():
                return
            HWND_TOPMOST, SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = -1, 0x1, 0x2, 0x10, 0x40
            ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(int(win.winId())), ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
                                              SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        except Exception:
            pass

    # ------------------------------------------------------------------ skeleton over the preview, fingertips at the cursor
    @Property("QVariant", notify=previewChanged)
    def previewHand(self):
        """{"lm": 21 [x, y] 0..1 of the frame, "guides": [[tip, state]], "anchor": [x, y] or [], "moving"}; {} = no hand."""
        return self.preview_hand

    def set_finger_frame(self, frame):
        if not self.finger_overlay and not self.finger_frame:
            return
        if frame is None or not self.finger_overlay:
            if self.finger_frame:
                self.finger_frame = {}
                self.fingerFrameChanged.emit()
            return
        from PySide6.QtGui import QCursor
        pos = QCursor.pos()             # logical pixels, the space windows are placed in
        self.finger_frame = {**frame, "cursor": [pos.x(), pos.y()]}
        self.fingerFrameChanged.emit()

    @Property("QVariant", notify=fingerFrameChanged)
    def fingerFrame(self):
        return self.finger_frame

    @Property(bool, notify=stateChanged)
    def fingerOverlay(self):
        return self.finger_overlay

    @Slot(bool)
    def setFingerOverlay(self, on):
        self.finger_overlay = bool(on)
        self.preferences.setValue("fingerOverlay", self.finger_overlay)
        if not on:
            self.set_finger_frame(None)
        self.stateChanged.emit()

    @Property("QVariant", notify=virtualHandChanged)
    def virtualHand(self):
        return self.virtual_hand or {}

    @Property(bool, notify=stateChanged)
    def virtualHandOn(self):
        return self.virtual_hand_on

    @Slot(bool)
    def setVirtualHand(self, on):
        self.virtual_hand_on = bool(on)
        self.preferences.setValue("virtualHand", self.virtual_hand_on)
        self.stateChanged.emit()

    @Property("QVariantList", notify=stateChanged)
    def virtualBox(self):
        return list(self.virtual_box)

    @Property(bool, notify=stateChanged)
    def boxEditing(self):
        return self.box_editing

    @Slot(bool)
    def setBoxEditing(self, on):
        self.box_editing = bool(on)
        self.stateChanged.emit()

    @Slot(float, float, float, float, result=bool)
    def setVirtualBox(self, x0, y0, x1, y1):
        """From a drag on the preview, 0..1 of the camera image in either direction. Too small a box is refused."""
        from src.virtual_hand import valid_box
        box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        box = tuple(round(min(1.0, max(0.0, v)), 4) for v in box)
        self.box_editing = False
        if not valid_box(box):
            self.stateChanged.emit()
            return False
        self.virtual_box = box
        self.preferences.setValue("virtualBox", ",".join(str(v) for v in box))
        self.stateChanged.emit()
        return True

    def set_live_gesture(self, label):
        if label != self.live_gesture:
            self.live_gesture = label
            self.registrationChanged.emit()

    @Property(str, notify=registrationChanged)
    def liveGesture(self):
        return self.live_gesture

    @Property(bool, notify=registrationChanged)
    def onlineReady(self):
        return self.online_ready

    @Property("QVariant", notify=registrationChanged)
    def registration(self):
        return self.registration_view or {"state": ""}

    @Property(str, notify=registrationChanged)
    def registrationAdvice(self):
        """What the check found, in words (internal class names stay out of sight)."""
        report = (self.registration_view or {}).get("report") or {}
        if not report:
            return ""
        from src.online.collector import correction_choices
        names = {c["label"]: c["name"] for c in correction_choices()}
        lines = []
        conflicts = report.get("conflicts") or []
        other = conflicts[0]["label"] if conflicts else report.get("confused_with")
        if other:
            lines.append(f"'{names.get(other, other)}'와(과) 헷갈릴 수 있어요.")
        if report.get("rest_fires"):
            lines.append("쉬는 손 모양과도 비슷해요. 더 뚜렷한 동작을 권해요.")
        if not report.get("reliable"):
            lines.append("안정적이지 않아요. 다시 시연하는 것을 권해요.")
        return " ".join(lines) or "기존 동작과 잘 구분돼요."

    @Slot(str)
    def startRegistration(self, name):
        self.online_commands.append(("register_start", name))

    @Slot()
    def approveRegistration(self):
        self.online_commands.append(("register_approve",))

    @Slot()
    def cancelRegistration(self):
        self.online_commands.append(("register_cancel",))

    # ------------------------------------------------------------------ "what was that really?" (online learning)
    def show_correction(self, sample, store):
        """Asks a person to label the last moments. Their answer is the only thing that makes this sample a training target."""
        from src.online.collector import correction_choices
        self.correction_sample, self.correction_store = sample, store
        self.correction_choices = correction_choices()
        names = {c["label"]: c["name"] for c in self.correction_choices}
        self.correction_guess = names.get(sample.suggested_label, "")
        self.correctionChanged.emit()

    @Property(bool, notify=correctionChanged)
    def correctionVisible(self):
        return self.correction_sample is not None

    @Property(str, notify=correctionChanged)
    def correctionGuess(self):
        return self.correction_guess

    @Property("QVariantList", notify=correctionChanged)
    def correctionChoices(self):
        return self.correction_choices

    @Slot(str)
    def answerCorrection(self, label):
        from src.online.collector import label_sample
        sample, store = self.correction_sample, self.correction_store
        if sample is None:
            return
        self.correction_sample = None
        self.correctionChanged.emit()
        try:
            label_sample(store, sample.sample_id, label)
        except (KeyError, OSError, ValueError) as exc:
            print(f"[Online] 정정 저장 실패: {exc}")
        self.learn_message = ""
        self.refreshOnlineSummary()

    @Slot()
    def dismissCorrection(self):
        """No answer: the sample has no use and holds a person's movement, so it goes."""
        sample, store = self.correction_sample, self.correction_store
        if sample is None:
            return
        self.correction_sample = None
        self.correctionChanged.emit()
        try:
            store.delete([sample.sample_id])
        except OSError as exc:
            print(f"[Online] 표본 삭제 실패: {exc}")

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
        progress, pending, notice = (tuple(self.close_state) + ("",))[:3]
        if notice:
            return True, notice
        if self.toggle_progress >= 0.15:
            return True, f"마우스 제어 켜기/끄기 자세 유지 중… {int(self.toggle_progress * 100)}%"
        if progress >= 0.15:
            return True, (f"창 닫는 중… {int(progress * 100)}%" if pending > 0 else f"창 닫기 자세 유지 중… {int(progress * 100)}%")
        if pending > 0:
            return True, f"창 닫기? 같은 손 모양 한 번 더 ({pending:.0f}초)"
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

    # ------------------------------------------------------------------ palm / fist cursor direction (src/palm_axes.py)
    @Property(bool, notify=stateChanged)
    def palmCalibrating(self):
        return bool(self.palm_axes_view[0])

    @Property(bool, notify=stateChanged)
    def palmCalibrated(self):
        return bool(self.palm_axes_view[4])

    @Property(str, notify=stateChanged)
    def palmAxesHint(self):
        running, prompt, progress, note, _ = self.palm_axes_view
        if running:
            return f"{prompt}  ·  {note} {int(progress * 100)}%"
        return prompt

    # ------------------------------------------------------------------ dataset recording (src/recorder.py)
    def _rec(self, key, default):
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
    def recBig(self):
        """One big word (or the countdown number) for reading the recording from 3 m away."""
        return str(self._rec("big", ""))

    @Property(str, notify=stateChanged)
    def recBigKind(self):
        return str(self._rec("big_kind", ""))

    @Property(bool, notify=stateChanged)
    def handLost(self):
        return bool(getattr(self, "hand_lost", False))

    @Property(bool, notify=stateChanged)
    def armLost(self):
        return bool(getattr(self, "arm_lost", False))

    @Property(bool, notify=stateChanged)
    def adminBlocked(self):
        return bool(getattr(self, "admin_blocked", False))

    @Slot()
    def restartAsAdmin(self):
        """Start this app again as administrator (Windows asks for permission) and close this one."""
        from src.elevation import relaunch_as_admin
        if relaunch_as_admin():
            self.closed = True

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
        if self.rec_mode in ("guided", "free"):
            self.rec_paused = not self.rec_paused
            self.stateChanged.emit()

    @Slot()
    def handleSpaceKey(self):
        """Control recording start/pause-resume with Spacebar during active recording, or toggle live mouse control."""
        self.command(32)

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
            self.last_rec_review = None
        self.rec_mode = mode
        self.rec_focus = ""
        self.rec_plan = None
        # Free mode starts paused so clicking with the mouse and raising the arm is not captured as training data
        self.rec_paused = (mode == "free")
        if mode != "off":
            self._async_update_live_spray("all")
        self.stateChanged.emit()

    @Slot()
    def discardRecording(self):
        """Cancel and discard the active recording without saving it to disk."""
        if self.rec_mode == "off":
            return
        self.rec_discard += 1
        self.rec_mode = "off"
        self.rec_focus = ""
        self.rec_plan = None
        self.rec_paused = False
        self.last_rec_review = None
        self.stateChanged.emit()

    @Property(bool, notify=stateChanged)
    def hasRecReview(self):
        return bool(self.last_rec_review and self.last_rec_review.get("file"))

    @Property(str, notify=stateChanged)
    def recReviewFile(self):
        return str((self.last_rec_review or {}).get("file", ""))

    @Property(str, notify=stateChanged)
    def recReviewMessage(self):
        return str((self.last_rec_review or {}).get("message", ""))

    @Property(str, notify=stateChanged)
    def recReviewStatus(self):
        return str((self.last_rec_review or {}).get("status", "pass"))

    @Slot(bool)
    def keepLastRecording(self, train_now=False):
        if not self.last_rec_review:
            return
        fname = self.last_rec_review.get("file", "")
        self.last_rec_review = None
        if fname:
            from src import training_center as tc
            tc.set_recording_selected(fname, True)
            self.training_note = f"'{fname}' 녹화를 학습 데이터에 포함했습니다."
        self.stateChanged.emit()
        self.refreshTraining()
        if train_now and not self.training_job.running:
            self.startTraining()

    @Slot()
    def excludeLastRecording(self):
        if not self.last_rec_review:
            return
        fname = self.last_rec_review.get("file", "")
        self.last_rec_review = None
        if fname:
            from src import training_center as tc
            tc.set_recording_selected(fname, False)
            self.training_note = f"'{fname}' 녹화를 학습에서 제외했습니다 (녹화 선별 탭에서 언제든 변경 가능)."
        self.stateChanged.emit()
        self.refreshTraining()

    @Slot()
    def deleteLastRecording(self):
        if not self.last_rec_review:
            return
        fname = self.last_rec_review.get("file", "")
        self.last_rec_review = None
        if fname:
            from src import training_center as tc
            tc.delete_recording_file(fname)
            self.training_note = f"'{fname}' 녹화 파일을 삭제했습니다."
        self.stateChanged.emit()
        self.refreshTraining()

    @Property(str, notify=stateChanged)
    def recFocus(self):
        return self.rec_focus

    # ------------------------------------------------------------------ training center (src/training_center.py)
    def _set_busy_task(self, title: str, step: str = "", progress: float = -1.0):
        if title:
            if not getattr(self, "training_busy", "") or getattr(self, "_task_started", 0.0) <= 0.0:
                self._task_started = time.time()
            self._task_title = str(title)
            self._task_step = str(step or title)
            self._task_progress = float(progress)
            self.training_busy = f"{title} · {step}" if (step and step != title) else str(title)
        else:
            self._task_title = ""
            self._task_step = ""
            self._task_progress = -1.0
            self._task_started = 0.0
            self.training_busy = ""
        self.trainingChanged.emit()
        if not title and getattr(self, "_pending_sim_request", None):
            req_file, req_cleaned = self._pending_sim_request
            self._pending_sim_request = None
            if req_file:
                self.runSimulation(req_file, req_cleaned)

    # QTimer may only be started and stopped from the thread it lives in. The dashboard's worker threads (record check,
    # export, delete ...) end by asking for a refresh, which starts the timer: Qt printed "Timers cannot be started /
    # stopped from another thread" and the call did nothing. These hand the call over to the screen thread.
    @Slot()
    def _timer_start(self):
        _timer(self, "start")

    @Slot()
    def _timer_stop(self):
        _timer(self, "stop")

    @Slot()
    def openTraining(self):
        self.refreshTraining()
        _timer(self, "start")

    @Slot()
    def closeTraining(self):
        if not self.training_job.running and not self.training_busy:
            _timer(self, "stop")

    @Slot()
    def refreshTraining(self):
        """Re-read recordings and the model's scores in the background (a new recording takes a few seconds)."""
        thread = getattr(self, "thread", None)
        if callable(thread) and QThread.currentThread() is not thread():   # a worker thread asked: do it on the screen thread
            QMetaObject.invokeMethod(self, "refreshTraining", Qt.ConnectionType.QueuedConnection)
            return
        if self.training_busy:
            return
        self._set_busy_task("녹화 데이터 및 모델 성적 분석 중", "[1/4단계] 녹화 데이터셋 및 품질 요약 스캔 중…", 0.15)

        def work():
            from src import training_center as tc
            try:
                summary = tc.dataset_summary()
                self._set_busy_task("녹화 데이터 및 모델 성적 분석 중", "[2/4단계] 학습된 AI 모델 성적표 및 개선도 분석 중…", 0.42)
                report = tc.model_report()
                improvement = tc.load_last_training_improvement()
                rows = tc.gesture_rows(summary, report, improvement)
                files = [f for f in summary["files"] if "error" not in f]
                selected_files = [f for f in files if f.get("selected", True)]
                frames = sum(f.get("frames", 0) for f in selected_files)
                minutes = sum(f.get("seconds", 0) for f in selected_files) / 60
                valid = sum(f.get("valid_frames", 0) for f in selected_files)
                excluded_cnt = len(files) - len(selected_files)
                excl_txt = f" (제외 {excluded_cnt}개)" if excluded_cnt else ""
                data = f"학습 녹화 {len(selected_files)}/{len(files)}개{excl_txt} · {minutes:.0f}분 · 3D 관절 {100 * valid / max(1, frames):.0f}%"
                waiting = len(summary["files"]) - len(files)
                if waiting:
                    data += f" · 오류/미완료 {waiting}개"
                if report:
                    rep_mode = report.get("training_mode")
                    rep_folds = report.get("folds") or []
                    mode_info = ""
                    if rep_mode == "fast" or (isinstance(rep_folds, list) and len(rep_folds) == 1):
                        mode_info = " · 빠른 학습(일부 자세 평가)"
                    elif rep_mode == "thorough" or (isinstance(rep_folds, list) and len(rep_folds) > 1):
                        mode_info = f" · 정밀 학습(자세 {len(rep_folds)}개 평가)"
                    rep_dev = str(report.get("training_device") or "")
                    dev_info = " · GPU 학습" if rep_dev == "cuda" else (" · CPU 학습" if rep_dev == "cpu" else "")
                    model = f"모델: {report.get('trained')} 학습 · 녹화 {len(report.get('data', []))}개로{mode_info}{dev_info}"
                else:
                    model = "모델: 아직 없음 (학습하세요)"

                file_rows = []
                for f in reversed(summary["files"]):
                    fname = f.get("file", "")
                    if "error" in f:
                        file_rows.append({
                            "file": fname, "selected": False, "frames": 0, "seconds": 0.0,
                            "valid_pct": 0, "pose_pct": 0,
                            "quality_status": "fail", "quality_score": 0.0,
                            "quality_text": "손상/미완료: " + str(f["error"]),
                            "detail_text": "로드 불가",
                            "recorded_mode_label": "손상된 파일",
                            "target_gestures_text": "손상/미완료 녹화",
                            "target_short_text": "손상됨",
                            "target_conditions_text": "",
                            "combo_label": f"{fname}  [손상/미완료]",
                        })
                    else:
                        fr = int(f.get("frames", 0))
                        sec = float(f.get("seconds", 0.0))
                        vf = int(f.get("valid_frames", 0))
                        pf = int(f.get("pose_frames", 0))
                        vp = int(round(100.0 * vf / max(1, fr)))
                        pp = int(round(100.0 * pf / max(1, fr)))
                        q_score = f.get("quality_score")
                        q_stat = f.get("quality_status", "unknown")
                        q_sum = f.get("quality_summary", "")
                        score_str = f"{q_score * 100:.0f}점" if isinstance(q_score, (int, float)) else "미검사"
                        reps_map = f.get("reps") or {}
                        total_reps = sum(sum(p.values()) for p in reps_map.values())
                        mode_lbl = str(f.get("recorded_mode_label") or ("자유 이동 녹화" if f.get("mode") == "free" else "가이드 녹화"))
                        tgt_txt = str(f.get("target_gestures_text") or "목표 동작 미기재")
                        tgt_short = str(f.get("target_short_text") or tgt_txt)
                        cond_txt = str(f.get("target_conditions_text") or "")
                        zones_txt = str(f.get("screen_zones_text") or "")
                        pose_tag = f" · 팔·어깨 {pp}%" if pp > 0 else " · 팔·어깨 추정"
                        zone_tag = f" · {zones_txt}" if zones_txt else ""
                        file_rows.append({
                            "file": fname,
                            "selected": bool(f.get("selected", True)),
                            "frames": fr,
                            "seconds": round(sec, 1),
                            "valid_pct": vp,
                            "pose_pct": pp,
                            "quality_status": q_stat,
                            "quality_score": float(q_score) if isinstance(q_score, (int, float)) else 0.0,
                            "quality_text": f"품질 {score_str} · {q_sum}" if q_sum else f"품질 {score_str}",
                            "detail_text": f"{sec:.0f}초 ({fr}프레임) · 3D 손검출 {vp}%{pose_tag} · 반복 {total_reps}회{zone_tag}",
                            "recorded_mode_label": mode_lbl,
                            "target_gestures_text": tgt_txt,
                            "target_short_text": tgt_short,
                            "target_conditions_text": cond_txt,
                            "screen_zones_text": zones_txt,
                            "combo_label": f"[{tgt_short}] {fname} ({sec:.0f}초 · 품질 {score_str})",
                        })

                self.training_rows, self.training_info = rows, (data, model)
                self.training_improvement = improvement
                self.training_files = file_rows
                self._set_busy_task("녹화 데이터 및 모델 성적 분석 중", "[3/4단계] ONNX 모델 목록 및 화면 영역별 스프레이 분포 계산 중…", 0.72)
                self.model_backups = tc.list_model_backups()
                self.onnx_models = tc.list_available_onnx_models()
                valid_ids = {m["id"] for m in self.onnx_models}
                if not getattr(self, "selected_onnx_model_id", "") or self.selected_onnx_model_id not in valid_ids:
                    active_m = next((m for m in self.onnx_models if m.get("active")), None)
                    self.selected_onnx_model_id = (
                        active_m["id"]
                        if active_m
                        else (self.onnx_models[0]["id"] if self.onnx_models else "composite:active")
                    )
                try:
                    self.spatial_spray = tc.compute_model_spatial_spray(
                        model_id=self.selected_onnx_model_id,
                        gesture_filter=getattr(self, "spray_gesture_filter", "all"),
                    )
                except Exception:
                    pass
                self._set_busy_task("녹화 데이터 및 모델 성적 분석 중", "[4/4단계] 연산 장치(GPU/CPU) 및 시뮬레이터 상태 갱신 중…", 0.92)
                self.aug_settings = tc.get_augmentation_settings()
                self._gpu_cap = tc.detect_gpu_training_capability()
                valid_files = {r["file"] for r in file_rows if r.get("file")}
                if not getattr(self, "selected_sim_file", "") or self.selected_sim_file not in valid_files:
                    prev_file = self.sim_result.get("file") or self.sim_eval.get("file") or ""
                    self.selected_sim_file = (
                        prev_file if prev_file in valid_files else (file_rows[0]["file"] if file_rows else "")
                    )
                if self.selected_sim_file:
                    if self.sim_eval.get("file") != self.selected_sim_file:
                        try:
                            self.sim_eval = tc.evaluate_recording_in_simulator(self.selected_sim_file)
                        except Exception:
                            pass
                    if self.sim_result.get("file") != self.selected_sim_file:
                        try:
                            sim_res = tc.simulate_model_on_recording(
                                self.selected_sim_file,
                                use_cleaned=True,
                                model_id=self.selected_onnx_model_id,
                            )
                            self._store_sim_result(sim_res)
                            if sim_res.get("quality"):
                                self.sim_eval = sim_res["quality"]
                        except Exception:
                            pass
                self._training_summary = summary
                self.training_storage = tc.storage_text(summary)
            except Exception as exc:
                self.training_note = f"읽지 못했어요: {exc}"
            self._set_busy_task("")

        import threading
        threading.Thread(target=work, daemon=True).start()
        _timer(self, "start")

    @Property("QVariantList", notify=trainingChanged)
    def trainingFiles(self):
        return self.training_files

    @Slot(str, bool)
    def setRecordingSelected(self, filename, selected):
        from src import training_center as tc
        tc.set_recording_selected(filename, bool(selected))
        self.training_note = f"'{filename}' 녹화를 학습 {'포함' if selected else '제외'}로 설정했습니다."
        self.refreshTraining()

    @Slot(str)
    def deleteRecording(self, filename):
        if self.training_busy or self.training_job.running:
            return
        from src import training_center as tc
        if tc.delete_recording_file(filename):
            self.training_note = f"'{filename}' 녹화 파일을 삭제했습니다."
            if self.last_rec_review and self.last_rec_review.get("file") == filename:
                self.last_rec_review = None
                self.stateChanged.emit()
            self.refreshTraining()

    @Slot(bool)
    def evaluateAllRecordings(self, auto_exclude_failed=False):
        if self.training_busy:
            return
        self._set_busy_task("전체 녹화 품질 전수검사", "녹화 폴더의 전체 파일 품질 검사를 준비하는 중…", 0.05)

        def work():
            from src import training_center as tc
            try:
                res = tc.evaluate_all_recordings_quality(
                    force=True,
                    auto_exclude_failed=bool(auto_exclude_failed),
                    progress_cb=lambda p, msg: self._set_busy_task("전체 녹화 품질 전수검사", msg, p),
                )
                fails = sum(1 for r in res if r.get("status") == "fail")
                warns = sum(1 for r in res if r.get("status") == "warning")
                oks = sum(1 for r in res if r.get("status") == "pass")
                excl_msg = " (불량 자동 제외됨)" if auto_exclude_failed and fails else ""
                self.training_note = f"품질 전수검사 완료: 양호 {oks}개 · 주의 {warns}개 · 불량 {fails}개{excl_msg}"
            except Exception as exc:
                self.training_note = f"품질 검사 실패: {exc}"
            self._set_busy_task("")
            self.refreshTraining()

        import threading
        threading.Thread(target=work, daemon=True).start()
        _timer(self, "start")

    @Slot(str)
    def resetGestureTraining(self, key):
        if self.rec_mode != "off" or self.training_job.running or self.training_busy:
            return
        from src import training_center as tc
        try:
            info = tc.reset_gesture_training(key)
            self.model_version += 1
            self.training_note = f"'{info['name']}' 동작의 학습을 초기화했습니다 (이전 모델은 자동 백업되었으며, 해당 동작은 기본 규칙으로 작동합니다)."
            self.stateChanged.emit()
            self.refreshTraining()
        except Exception as exc:
            self.training_note = f"초기화 실패: {exc}"
            self.trainingChanged.emit()

    @Property("QVariantList", notify=trainingChanged)
    def modelBackups(self):
        return self.model_backups

    @Property("QVariantList", notify=trainingChanged)
    def onnxModels(self):
        return list(getattr(self, "onnx_models", None) or [])

    @Property(str, notify=trainingChanged)
    def selectedOnnxModelId(self):
        return str(getattr(self, "selected_onnx_model_id", "composite:active") or "composite:active")

    @Property("QVariantMap", notify=trainingChanged)
    def selectedOnnxModel(self):
        models = getattr(self, "onnx_models", None) or []
        sel_id = self.selectedOnnxModelId
        for m in models:
            if m.get("id") == sel_id:
                return dict(m)
        return dict(models[0]) if models else {}

    @Property("QVariantList", notify=trainingChanged)
    def selectedOnnxGestureRates(self):
        m = self.selectedOnnxModel
        return list(m.get("gesture_rates") or [])

    @Property("QVariantMap", notify=trainingChanged)
    def spatialSprayData(self):
        return dict(getattr(self, "spatial_spray", None) or {})

    @Property("QVariantList", notify=trainingChanged)
    def spatialSprayCells(self):
        sp = getattr(self, "spatial_spray", None) or {}
        return list(sp.get("cells") or [])

    @Property("QVariantList", notify=trainingChanged)
    def spatialSprayFilterOptions(self):
        sp = getattr(self, "spatial_spray", None) or {}
        return list(sp.get("filter_options") or [{"key": "all", "name": "전체 동작"}])

    @Property(str, notify=trainingChanged)
    def spatialSprayHeadline(self):
        sp = getattr(self, "spatial_spray", None) or {}
        return str(sp.get("headline") or "화면 영역별 동작 학습 분포 분석 중…")

    @Property(str, notify=trainingChanged)
    def sprayGestureFilter(self):
        return str(getattr(self, "spray_gesture_filter", "all") or "all")

    def _async_update_live_spray(self, gesture_filter="all"):
        def work():
            from src import training_center as tc
            try:
                if not getattr(self, "onnx_models", None):
                    self.onnx_models = tc.list_available_onnx_models()
                self.spatial_spray = tc.compute_model_spatial_spray(
                    model_id=self.selectedOnnxModelId,
                    gesture_filter=gesture_filter or "all",
                )
                self.trainingChanged.emit()
            except Exception:
                pass

        import threading
        threading.Thread(target=work, daemon=True).start()

    @Slot(str)
    def selectOnnxModel(self, model_id):
        target_id = str(model_id or "").strip()
        if not target_id:
            return
        self.selected_onnx_model_id = target_id
        from src import training_center as tc
        try:
            self.spatial_spray = tc.compute_model_spatial_spray(
                model_id=self.selected_onnx_model_id,
                gesture_filter=self.sprayGestureFilter,
            )
            sel = self.selectedOnnxModel
            if sel:
                self.training_note = f"시뮬레이터 분석 대상 ONNX 선택: {sel.get('title', target_id)}"
        except Exception as exc:
            self.training_note = f"ONNX 모델 분포 계산 실패: {exc}"
        self.trainingChanged.emit()

    @Slot(str)
    def setSprayGestureFilter(self, gesture_key):
        norm = str(gesture_key or "all").strip() or "all"
        self.spray_gesture_filter = norm
        from src import training_center as tc
        try:
            self.spatial_spray = tc.compute_model_spatial_spray(
                model_id=self.selectedOnnxModelId,
                gesture_filter=norm,
            )
        except Exception:
            pass
        self.trainingChanged.emit()

    @Slot(str)
    def activateOnnxModel(self, model_id=""):
        if self.rec_mode != "off" or self.training_job.running or self.training_busy:
            return
        target_id = str(model_id or self.selectedOnnxModelId).strip()
        if not target_id:
            return
        from src import training_center as tc
        try:
            res = tc.activate_onnx_model(target_id)
            self.selected_onnx_model_id = str(res.get("id") or target_id)
            self.model_version += 1
            self.tcn_gate = True
            self.training_note = str(res.get("message") or "선택한 ONNX 모델을 실사용 모델로 불러왔습니다.")
            self.stateChanged.emit()
            self.refreshTraining()
        except Exception as exc:
            self.training_note = f"ONNX 모델 불러오기 실패: {exc}"
            self.trainingChanged.emit()

    @Slot(str)
    def trainSelectedOnnxModel(self, model_id=""):
        if self.rec_mode != "off" or self.training_job.running or self.training_busy:
            return
        target_id = str(model_id or self.selectedOnnxModelId).strip()
        sel = None
        for m in getattr(self, "onnx_models", None) or []:
            if m.get("id") == target_id:
                sel = m
                break
        trainable_key = str((sel or {}).get("trainable_key") or "")
        if trainable_key and trainable_key != "all":
            self.trainSelectedGesture(trainable_key)
        else:
            self.startTraining()

    @Property("QVariantMap", notify=trainingChanged)
    def trainingImprovement(self):
        return getattr(self, "training_improvement", None) or {}

    @Property("QVariantList", notify=trainingChanged)
    def trainingImprovementItems(self):
        imp = getattr(self, "training_improvement", None) or {}
        return list(imp.get("items") or [])

    @Property(str, notify=trainingChanged)
    def trainingImprovementHeadline(self):
        imp = getattr(self, "training_improvement", None) or {}
        return str(imp.get("headline") or "")

    @Slot()
    def dismissTrainingImprovement(self):
        import json
        from src.training_center import MODEL
        imp_path = MODEL.parent / "last_training_improvement.json"
        cur = dict(getattr(self, "training_improvement", None) or {})
        cur["dismissed"] = True
        try:
            if imp_path.is_file():
                doc = json.loads(imp_path.read_text(encoding="utf-8"))
                if isinstance(doc, dict):
                    doc["dismissed"] = True
                    imp_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            elif cur.get("available"):
                imp_path.parent.mkdir(parents=True, exist_ok=True)
                imp_path.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        self.training_improvement = cur
        self.trainingChanged.emit()

    @Property("QVariantMap", notify=trainingChanged)
    def gestureTestResults(self):
        return getattr(self, "_gesture_test_results", None) or {}

    @Slot(str)
    def testGestureJudgment(self, key):
        if self.training_busy:
            return
        from src.gesture_catalog import NAMES
        gname = NAMES.get(key, key)
        self._set_busy_task(
            f"'{gname}' 동작 판정 검증 중",
            f"최근 '{gname}' 녹화 데이터에서 규칙 및 AI 모델 판정률을 검증하고 있습니다…",
            0.35,
        )

        def work():
            from src import training_center as tc
            try:
                res = tc.evaluate_gesture_judgment(key)
                store = dict(getattr(self, "_gesture_test_results", None) or {})
                store[key] = {
                    "summary": res.get("summary", ""),
                    "detail": res.get("detail", ""),
                    "latest_file": res.get("latest_file", ""),
                    "ok": bool(res.get("ok")),
                }
                self._gesture_test_results = store
                if isinstance(res.get("latest_sim"), dict):
                    self._store_sim_result(res["latest_sim"])
                self.training_note = str(res.get("summary") or "")
            except Exception as exc:
                self.training_note = f"판정 검증 실패: {exc}"
            self._set_busy_task("")

        import threading
        threading.Thread(target=work, daemon=True).start()
        _timer(self, "start")

    @Slot(str)
    def restoreModelBackup(self, backup_id):
        if self.rec_mode != "off" or self.training_job.running or self.training_busy:
            return
        from src import training_center as tc
        try:
            res = tc.restore_model_backup(backup_id)
            self.model_version += 1
            self.training_note = f"모델 복원 완료: {res['restored']} 버전으로 되돌렸습니다."
            self.stateChanged.emit()
            self.refreshTraining()
        except Exception as exc:
            self.training_note = f"모델 복원 실패: {exc}"
            self.trainingChanged.emit()

    def _store_sim_result(self, res: dict) -> None:
        pb = list(res.get("playback_frames") or [])
        self._sim_playback_frames = pb
        slim = {k: v for k, v in res.items() if k != "playback_frames"}
        self.sim_result = slim
        if slim.get("file"):
            self.selected_sim_file = str(slim["file"])

    @Property(str, notify=trainingChanged)
    def selectedSimFile(self):
        return str(
            getattr(self, "selected_sim_file", "")
            or self.sim_result.get("file")
            or self.sim_eval.get("file")
            or (self.training_files[0].get("file", "") if self.training_files else "")
        )

    @Slot(str)
    def setSelectedSimFile(self, filename):
        target = str(filename or "").strip()
        if target and target != getattr(self, "selected_sim_file", ""):
            self.selected_sim_file = target
            self.trainingChanged.emit()

    @Property("QVariantMap", notify=trainingChanged)
    def simulationResult(self):
        return self.sim_result

    @Property("QVariantList", notify=trainingChanged)
    def simulationGestures(self):
        return list(self.sim_result.get("gestures", []))

    @Property("QVariantList", notify=trainingChanged)
    def simulationTimeline(self):
        return list(self.sim_result.get("timeline", []))

    @Property("QVariantList", notify=trainingChanged)
    def simulationReps(self):
        return list(self.sim_result.get("reps", []))

    @Property(int, notify=trainingChanged)
    def simPlaybackCount(self):
        return len(self._sim_playback_frames)

    @Slot(int, result="QVariantMap")
    def getSimPlaybackFrame(self, idx):
        n = len(self._sim_playback_frames)
        if n == 0:
            return {}
        clamped = max(0, min(n - 1, int(idx)))
        frame = dict(self._sim_playback_frames[clamped])
        start_t = max(0, clamped - 14)
        trail = [
            self._sim_playback_frames[k]["palm"]
            for k in range(start_t, clamped + 1)
            if self._sim_playback_frames[k].get("palm")
        ]
        frame["trail"] = trail
        return frame

    @Slot(int, result=int)
    def findNextSimEventFrame(self, after_idx):
        n = len(self._sim_playback_frames)
        if n == 0:
            return 0
        start = (max(0, int(after_idx)) + 1) % n
        prev_active = False
        for offset in range(n):
            fi = (start + offset) % n
            f = self._sim_playback_frames[fi]
            is_active = bool(f.get("event_kind", "none") != "none" or f.get("model_hit") or f.get("phase") == "do")
            if is_active and not prev_active:
                return fi
            prev_active = is_active
        return start

    @Property(str, notify=trainingChanged)
    def simulationSummary(self):
        return str(self.sim_result.get("summary", "녹화 파일을 선택하고 [모델 시뮬레이션 실행] 또는 [데이터 평가]를 누르세요."))

    @Property(str, notify=trainingChanged)
    def simQualityHeadline(self):
        return str(self.sim_eval.get("headline", "녹화 파일을 선택하면 데이터 품질·손 유실·관절 이상치 평가 결과가 표시됩니다."))

    @Property(str, notify=trainingChanged)
    def simQualityStatus(self):
        return str(self.sim_eval.get("active_status", "unknown"))

    @Property(bool, notify=trainingChanged)
    def simCleanedExists(self):
        return bool(self.sim_eval.get("cleaned_exists", False))

    @Property("QVariantList", notify=trainingChanged)
    def simQualityIssues(self):
        return list(self.sim_eval.get("issues", []))

    @Property(str, notify=trainingChanged)
    def simRecordedModeLabel(self):
        return str(self.sim_eval.get("recorded_mode_label") or self.sim_result.get("recorded_mode_label") or "가이드 녹화")

    @Property(str, notify=trainingChanged)
    def simTargetGesturesText(self):
        return str(
            self.sim_eval.get("target_gestures_text")
            or self.sim_result.get("target_gestures_text")
            or "녹화 파일을 선택하면 녹화하려던 목표 동작이 여기에 표시됩니다."
        )

    @Property(str, notify=trainingChanged)
    def simTargetConditionsText(self):
        return str(
            self.sim_eval.get("target_conditions_text")
            or self.sim_result.get("target_conditions_text")
            or "기본 촬영 자세"
        )

    @Property(str, notify=trainingChanged)
    def simTargetInstructionText(self):
        return str(
            self.sim_eval.get("target_instruction_text")
            or self.sim_result.get("target_instruction_text")
            or ""
        )

    @Property(str, notify=trainingChanged)
    def simScreenZonesText(self):
        return str(
            self.sim_eval.get("screen_zones_text")
            or self.sim_result.get("screen_zones_text")
            or "화면 영역 분석 중"
        )

    @Property(int, notify=trainingChanged)
    def simPoseCoveragePct(self):
        val = self.sim_eval.get("pose_coverage_pct")
        if val is None:
            val = self.sim_result.get("pose_coverage_pct", 0)
        return int(val or 0)

    @Property(bool, notify=trainingChanged)
    def augEnabled(self):
        return bool(self.aug_settings.get("enabled", True))

    @Property(str, notify=trainingChanged)
    def augStrength(self):
        return str(self.aug_settings.get("strength", "normal"))

    @Property(str, notify=trainingChanged)
    def augStrengthLabel(self):
        return str(self.aug_settings.get("strength_label", "보통 (회전 ±35° · 크기 ±15% · 시간 ±15%)"))

    @Property(int, notify=trainingChanged)
    def augFactor(self):
        return int(self.aug_settings.get("factor", 2))

    @Property(str, notify=trainingChanged)
    def augSummary(self):
        aug_info = self.sim_eval.get("augmentation") or {}
        if aug_info.get("summary"):
            return str(aug_info["summary"])
        en = "사용 중" if self.augEnabled else "꺼짐"
        return f"증강 {en} · 강도: {self.augStrengthLabel} · 수량: {self.augFactor}배"

    @Slot(str)
    def evaluateSimRecording(self, filename=""):
        target = str(filename or "").strip()
        if not target and getattr(self, "selected_sim_file", ""):
            target = self.selected_sim_file
        if not target and self.training_files:
            target = self.training_files[0].get("file", "")
        if not target:
            return
        self.selected_sim_file = target
        if self.training_busy:
            self._pending_sim_request = (target, True)
            self.trainingChanged.emit()
            return
        self._set_busy_task("녹화 데이터 정밀 평가 중", f"'{target}' 손 랜드마크 검출률·관절 이상치·화면 영역 평가 중…", 0.45)

        def work():
            from src import training_center as tc
            try:
                self.sim_eval = tc.evaluate_recording_in_simulator(target, force_recheck=True)
                if self.sim_result.get("file") != target:
                    self._set_busy_task("녹화 데이터 정밀 평가 중", f"'{target}' 시뮬레이터 프레임 및 모델 예측 동기화 중…", 0.70)
                    sim_res = tc.simulate_model_on_recording(
                        target,
                        use_cleaned=True,
                        model_id=self.selectedOnnxModelId,
                    )
                    self._store_sim_result(sim_res)
                self._set_busy_task("녹화 데이터 정밀 평가 중", f"'{target}' 데이터 증강 및 품질 요약 반영 중…", 0.88)
                self.aug_settings = tc.get_augmentation_settings()
                self.training_note = self.sim_eval.get("headline", "데이터 평가 완료")
            except Exception as exc:
                self.training_note = f"데이터 평가 실패: {exc}"
            self._set_busy_task("")

        import threading
        threading.Thread(target=work, daemon=True).start()
        _timer(self, "start")

    @Slot(str, int, float, int)
    def calibrateSimRecording(self, filename="", max_interp_gap=3, max_joint_jump=0.38, min_do_frames=4):
        if self.training_busy:
            return
        target = filename
        if not target and self.training_files:
            target = self.training_files[0].get("file", "")
        if not target:
            self.training_note = "보정할 녹화 파일이 없습니다."
            self.trainingChanged.emit()
            return
        self._set_busy_task(
            "녹화 데이터 자동 보정 중",
            f"[1/3단계] '{target}' 결측 구간 보간 및 관절 이상치 스무딩 중…",
            0.25,
        )

        def work():
            from src import training_center as tc
            try:
                res = tc.calibrate_recording_data(
                    target,
                    max_interp_gap=int(max_interp_gap),
                    max_joint_jump=float(max_joint_jump),
                    min_do_frames=int(min_do_frames),
                )
                self._set_busy_task(
                    "녹화 데이터 자동 보정 중",
                    f"[2/3단계] '{target}' 보정 후 데이터 품질 재평가 중…",
                    0.60,
                )
                self.sim_eval = tc.evaluate_recording_in_simulator(target, force_recheck=False)
                self._set_busy_task(
                    "녹화 데이터 자동 보정 중",
                    f"[3/3단계] '{target}' 보정본 모델 시뮬레이션 재실행 중…",
                    0.85,
                )
                self._store_sim_result(
                    tc.simulate_model_on_recording(target, use_cleaned=True, model_id=self.selectedOnnxModelId)
                )
                self.training_note = res.get("message", "데이터 보정 완료")
            except Exception as exc:
                self.training_note = f"데이터 보정 실패: {exc}"
            self._set_busy_task("")
            self.refreshTraining()

        import threading
        threading.Thread(target=work, daemon=True).start()
        _timer(self, "start")

    @Slot(str)
    def resetCalibratedSimRecording(self, filename=""):
        if self.training_busy:
            return
        target = filename
        if not target and self.training_files:
            target = self.training_files[0].get("file", "")
        if not target:
            return
        self._set_busy_task("데이터 보정본 초기화 중", f"[1/2단계] '{target}' 보정본 제거 및 원본 품질 복구 중…", 0.35)

        def work():
            from src import training_center as tc
            try:
                res = tc.reset_calibrated_recording(target)
                self.sim_eval = tc.evaluate_recording_in_simulator(target, force_recheck=False)
                self._set_busy_task("데이터 보정본 초기화 중", f"[2/2단계] '{target}' 원본 데이터로 시뮬레이션 재실행 중…", 0.75)
                self._store_sim_result(
                    tc.simulate_model_on_recording(target, use_cleaned=False, model_id=self.selectedOnnxModelId)
                )
                self.training_note = res.get("message", "보정 초기화 완료")
            except Exception as exc:
                self.training_note = f"보정 초기화 실패: {exc}"
            self._set_busy_task("")
            self.refreshTraining()

        import threading
        threading.Thread(target=work, daemon=True).start()
        _timer(self, "start")

    @Slot(bool, str, int)
    def setAugmentationConfig(self, enabled, strength, factor):
        from src import training_center as tc
        self.aug_settings = tc.set_augmentation_settings(
            enabled=bool(enabled),
            strength=str(strength),
            factor=int(factor),
        )
        cur_file = self.sim_eval.get("file", "")
        if cur_file:
            try:
                self.sim_eval = tc.evaluate_recording_in_simulator(cur_file, force_recheck=False)
            except Exception:
                pass
        self.training_note = (
            f"증강 설정 변경됨: {'사용' if self.augEnabled else '사용 안 함'} · "
            f"{self.augStrengthLabel} · {self.augFactor}배"
        )
        self.trainingChanged.emit()

    @Slot(str, bool, str, int)
    def applySimAugmentation(self, filename="", enabled=True, strength="normal", factor=2):
        if self.training_busy:
            return
        target = filename
        if not target and self.training_files:
            target = self.training_files[0].get("file", "")
        label_str = "선택된 전체 녹화" if target == "__all__" else f"'{target}'"
        self._set_busy_task(
            "오프라인 데이터 증강 생성 중",
            f"[1/2단계] {label_str} 3D 회전·크기·시간축 증강 데이터 생성 중 ({factor}배)…",
            0.35,
        )

        def work():
            from src import training_center as tc
            try:
                res = tc.apply_recording_augmentation(
                    target,
                    enabled=bool(enabled),
                    strength=str(strength),
                    factor=int(factor),
                )
                self._set_busy_task(
                    "오프라인 데이터 증강 생성 중",
                    f"[2/2단계] {label_str} 증강 결과 검증 및 시뮬레이터 상태 갱신 중…",
                    0.82,
                )
                self.aug_settings = res.get("settings", tc.get_augmentation_settings())
                eval_target = self.sim_eval.get("file") or (
                    self.training_files[0].get("file", "") if self.training_files else ""
                )
                if target and target != "__all__":
                    eval_target = target
                if eval_target:
                    self.sim_eval = tc.evaluate_recording_in_simulator(eval_target, force_recheck=False)
                self.training_note = res.get("message", "데이터 증강 적용 완료")
            except Exception as exc:
                self.training_note = f"데이터 증강 실패: {exc}"
            self._set_busy_task("")

        import threading
        threading.Thread(target=work, daemon=True).start()
        _timer(self, "start")

    @Slot(str)
    @Slot(str, bool)
    def runSimulation(self, filename="", use_cleaned=True):
        target = str(filename or "").strip()
        if not target and getattr(self, "selected_sim_file", ""):
            target = self.selected_sim_file
        if not target and self.training_files:
            target = self.training_files[0].get("file", "")
        if not target:
            self.training_note = "시뮬레이션할 녹화 파일이 없습니다."
            self.trainingChanged.emit()
            return
        self.selected_sim_file = target
        if self.training_busy:
            self._pending_sim_request = (target, bool(use_cleaned))
            self.trainingChanged.emit()
            return
        self._set_busy_task(
            "모델 시뮬레이션 실행 중",
            f"[1/3단계] '{target}' 녹화 프레임 로드 및 선택된 ONNX 모델 추론 중…",
            0.30,
        )

        def work():
            from src import training_center as tc
            try:
                res = tc.simulate_model_on_recording(
                    target,
                    use_cleaned=bool(use_cleaned),
                    model_id=self.selectedOnnxModelId,
                )
                self._set_busy_task(
                    "모델 시뮬레이션 실행 중",
                    f"[2/3단계] '{target}' 타임라인·관절 스켈레톤 리플레이 데이터 구성 중…",
                    0.75,
                )
                self._store_sim_result(res)
                self._set_busy_task(
                    "모델 시뮬레이션 실행 중",
                    f"[3/3단계] '{target}' 품질 리포트 및 증강 정보 반영 중…",
                    0.92,
                )
                self.sim_eval = res.get("quality") or tc.evaluate_recording_in_simulator(target)
                self.aug_settings = tc.get_augmentation_settings()
                self.training_note = res.get("summary", "시뮬레이션 완료")
            except Exception as exc:
                self.training_note = f"시뮬레이션 실패: {exc}"
            self._set_busy_task("")

        import threading
        threading.Thread(target=work, daemon=True).start()
        _timer(self, "start")

    @Property("QVariantList", notify=trainingChanged)
    def diagnosticsChecks(self):
        return list(self.diag_result.get("checks", []))

    @Property(str, notify=trainingChanged)
    def diagnosticsSummary(self):
        return str(self.diag_result.get("summary", "[자가 진단 실행]을 눌러 모델·설정·데이터 무결성을 점검하세요."))

    @Property(str, notify=trainingChanged)
    def diagnosticsOverall(self):
        return str(self.diag_result.get("overall", "unknown"))

    @Slot()
    def runDiagnostics(self):
        if self.training_busy:
            return
        self._set_busy_task(
            "앱 및 데이터셋 자가 진단 중",
            "ONNX 모델 무결성·설정 스키마·MediaPipe 에셋·녹화 데이터셋 점검 중…",
            0.45,
        )

        def work():
            from src import training_center as tc
            try:
                self.diag_result = tc.run_app_diagnostics()
                self.training_note = self.diag_result.get("summary", "자가 진단 완료")
            except Exception as exc:
                self.training_note = f"자가 진단 오류: {exc}"
            self._set_busy_task("")

        import threading
        threading.Thread(target=work, daemon=True).start()
        _timer(self, "start")

    @Property(str, notify=trainingChanged)
    def trainingMode(self):
        mode = getattr(self, "training_mode", "fast")
        return mode if mode in ("fast", "thorough") else "fast"

    @Property(int, notify=trainingChanged)
    def trainingConditionCount(self):
        summary = getattr(self, "_training_summary", None) or {}
        if "condition_count" in summary:
            return int(summary["condition_count"] or 0)
        conds = {ck for g in (summary.get("per_gesture") or {}).values() for ck in (g.get("postures") or {}).keys() if ck}
        return len(conds)

    @Property(int, notify=trainingChanged)
    def thoroughRunCount(self):
        c = self.trainingConditionCount
        return (c + 1) if c >= 2 else 1

    @Property(str, notify=trainingChanged)
    def trainingModeRunsText(self):
        c = self.trainingConditionCount
        thorough_runs = (c + 1) if c >= 2 else 1
        fast_runs = 2 if c >= 2 else 1
        if self.trainingMode == "thorough":
            return f"정밀 학습: 전체 {c}개 자세 교차평가 + 최종 모델 (총 {thorough_runs}회 학습 실행 · 오래 걸림)"
        return f"빠른 학습(권장): 대표 자세 1개 평가(일부 자세 평가) + 최종 모델 (총 {fast_runs}회 학습 실행 · 간격 2 · 증강 1배)"

    @Slot(str)
    def setTrainingMode(self, mode):
        norm = str(mode) if str(mode) in ("fast", "thorough") else "fast"
        self.training_mode = norm
        prefs = getattr(self, "preferences", None)
        if prefs is not None:
            prefs.setValue("trainingMode", norm)
        try:
            from src import training_center as tc
            tc.set_training_mode(norm)
        except Exception:
            pass
        self.trainingChanged.emit()

    @Property(bool, notify=trainingChanged)
    def gpuAvailable(self):
        cap = getattr(self, "_gpu_cap", None) or {}
        return bool(cap.get("cuda_available", False))

    @Property(bool, notify=trainingChanged)
    def canInstallCudaTorch(self):
        cap = getattr(self, "_gpu_cap", None) or {}
        return bool(cap.get("can_install_cuda_torch", False))

    @Property(str, notify=trainingChanged)
    def gpuDeviceName(self):
        cap = getattr(self, "_gpu_cap", None) or {}
        return str(cap.get("device_name") or "")

    @Property(str, notify=trainingChanged)
    def trainingDevice(self):
        pref = str(getattr(self, "training_device_pref", "auto") or "auto").strip().lower()
        if pref in ("auto", "gpu", "cuda") and self.gpuAvailable:
            return "gpu"
        return "cpu"

    @Property(str, notify=trainingChanged)
    def gpuButtonLabel(self):
        name = self.gpuDeviceName
        if self.gpuAvailable:
            short = name.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "").strip()
            return f"GPU 가속 ({short})" if short else "GPU 가속"
        return "GPU 가속 (미지원)"

    @Property(str, notify=trainingChanged)
    def gpuStatusText(self):
        cap = getattr(self, "_gpu_cap", None) or {}
        status = str(cap.get("status_label") or "연산 장치 확인 중")
        if self.gpuAvailable:
            cur = "GPU 고속 학습 선택됨" if self.trainingDevice == "gpu" else "CPU 학습 선택됨"
            return f"{status} · {cur}"
        if self.canInstallCudaTorch:
            return f"{status} · 우측 [GPU 가속 활성화]를 누르면 GPU 학습이 켜집니다"
        return f"{status} · 자동으로 CPU 학습이 사용됩니다"

    @Slot(str)
    def setTrainingDevice(self, device):
        raw = str(device or "auto").strip().lower()
        if raw == "cuda":
            raw = "gpu"
        norm = raw if raw in ("auto", "gpu", "cpu") else "auto"
        if norm == "gpu" and not self.gpuAvailable:
            norm = "cpu"
        self.training_device_pref = norm
        prefs = getattr(self, "preferences", None)
        if prefs is not None:
            prefs.setValue("trainingDevice", norm)
        try:
            from src import training_center as tc
            tc.set_training_device(norm)
        except Exception:
            pass
        self.trainingChanged.emit()

    @Slot()
    def enableGpuAcceleration(self):
        if self.training_busy or self.training_job.running:
            return
        self._set_busy_task(
            "GPU 가속 환경 설치 및 활성화 중",
            "[1/2단계] CUDA GPU용 PyTorch 패키지 다운로드 및 설치 중… (약 1분 소요)",
            -1.0,
        )
        self.training_note = self.training_busy

        def work():
            from src import training_center as tc
            try:
                cap = tc.install_cuda_torch()
                self._set_busy_task(
                    "GPU 가속 환경 설치 및 활성화 중",
                    "[2/2단계] GPU 장치 호환성 검증 및 학습 가속 설정 적용 중…",
                    0.85,
                )
                self._gpu_cap = cap
                if cap.get("cuda_available"):
                    self.training_device_pref = "gpu"
                    prefs = getattr(self, "preferences", None)
                    if prefs is not None:
                        prefs.setValue("trainingDevice", "gpu")
                    tc.set_training_device("gpu")
                    self.training_note = f"GPU 가속 활성화 완료: {cap.get('status_label')}"
                else:
                    self.training_note = f"설치 후에도 CUDA를 사용할 수 없습니다: {cap.get('status_label')}"
            except Exception as exc:
                self.training_note = f"GPU 가속 설치 실패: {exc}"
            self._set_busy_task("")

        import threading
        threading.Thread(target=work, daemon=True).start()
        _timer(self, "start")

    @Slot()
    def startTraining(self):
        mode = getattr(self, "training_mode", None)
        kwargs = {"mode": mode} if mode in ("fast", "thorough") else {}
        dev = getattr(self, "trainingDevice", None)
        if dev in ("gpu", "cpu", "auto"):
            kwargs["device"] = dev
        if self.training_job.start(**kwargs):
            self.training_note = ""
            self._training_was_running = True
            _timer(self, "start")
            self.trainingChanged.emit()

    @Slot(str)
    def trainSelectedGesture(self, key):
        if self.rec_mode != "off" or self.training_job.running:
            return
        from src.gesture_catalog import BY_KEY
        task = BY_KEY.get(key)
        if task is None or task.label == "none":
            return
        self._pause_for_change()
        mode = getattr(self, "training_mode", None)
        kwargs = {"gestures": [key]}
        if mode in ("fast", "thorough"):
            kwargs["mode"] = mode
        dev = getattr(self, "trainingDevice", None)
        if dev in ("gpu", "cpu", "auto"):
            kwargs["device"] = dev
        if self.training_job.start(**kwargs):
            self.training_note = task.name + " 선택 학습을 시작합니다."
            self._training_was_running = True
            _timer(self, "start")
            self.trainingChanged.emit()

    @Slot()
    def stopTraining(self):
        self.training_job.stop()

    @Slot()
    def pauseTraining(self):
        """Stops after the evaluation fold in progress; [이어서 학습] goes on from the next one."""
        self.training_job.pause()
        self.trainingChanged.emit()

    @Slot()
    def resumeTraining(self):
        if self.rec_mode != "off" or self.training_job.running or self.training_busy:
            return
        if self.training_job.resume():
            self.training_note = ""
            self._training_was_running = True
            _timer(self, "start")
            self.trainingChanged.emit()

    @Slot()
    def discardTraining(self):
        self.training_job.discard_interrupted()
        self.training_note = "이어하기 기록을 지웠어요. 다음 학습은 처음부터 시작해요."
        self.trainingChanged.emit()

    @Property(bool, notify=trainingChanged)
    def trainingPausing(self):
        return bool(self.training_job.running and self.training_job.state.get("pausing"))

    @Property(bool, notify=trainingChanged)
    def trainingResumable(self):
        return bool(not self.training_job.running and not getattr(self, "training_busy", "")
                    and self.training_job.interrupted() is not None)

    @Property(str, notify=trainingChanged)
    def trainingResumeText(self):
        info = self.training_job.interrupted()
        if info is None:
            return ""
        total = f"/{info['total']}" if info["total"] else ""
        head = "일시중지된 학습" if info["paused"] else "끝나지 않은 학습(앱이나 PC가 꺼졌을 수 있어요)"
        return f"{head} · 평가 {info['done']}{total}판 저장됨"

    @Slot()
    def exportModel(self):
        if self.training_busy:
            return
        self._set_busy_task("모델 및 학습 데이터셋 패키지 내보내기", "[1/4단계] 현재 모델 및 메타데이터 확인 중…", 0.15)

        def work():
            from src import training_center as tc
            try:
                out = tc.export_package(
                    progress_cb=lambda p, msg: self._set_busy_task("모델 및 학습 데이터셋 패키지 내보내기", msg, p)
                )
                self.training_note = f"내보냈어요: exports/{out.name}.zip"
                self._export_dir = str(out.parent)
            except Exception as exc:
                self.training_note = f"내보내지 못했어요: {exc}"
            self._set_busy_task("")

        import threading
        threading.Thread(target=work, daemon=True).start()
        _timer(self, "start")

    @Slot()
    def openExports(self):
        import os
        from src.training_center import EXPORT_DIR
        EXPORT_DIR.mkdir(exist_ok=True)
        os.startfile(str(EXPORT_DIR))  # noqa: S606 - Windows explorer on our own folder

    @Property(str, notify=trainingChanged)
    def hubProvider(self):
        return str(self.hub_config.get("provider", "huggingface"))

    @Property(str, notify=trainingChanged)
    def hubRepo(self):
        return str(self.hub_config.get("repo", ""))

    @Property(str, notify=trainingChanged)
    def hubBranch(self):
        return str(self.hub_config.get("branch", "main"))

    @Property(bool, notify=trainingChanged)
    def hubIncludeModels(self):
        return bool(self.hub_config.get("include_models", True))

    @Property(bool, notify=trainingChanged)
    def hubHasToken(self):
        from tools import hub as hf_hub
        return bool(hf_hub.load_hub_token(self.hubProvider))

    @Property(str, notify=trainingChanged)
    def hubTokenMasked(self):
        from tools import hub as hf_hub
        tok = hf_hub.load_hub_token(self.hubProvider)
        return hf_hub.mask_token(tok) if tok else "저장된 토큰 없음"

    @Property(str, notify=trainingChanged)
    def hubStatusNote(self):
        return str(getattr(self, "hub_status_note", ""))

    @Property(str, notify=trainingChanged)
    def hubSummaryLabel(self):
        prov = "GitHub" if self.hubProvider == "github" else "Hugging Face"
        repo = self.hubRepo or "미설정"
        tok_label = f"토큰 {self.hubTokenMasked}" if self.hubHasToken else "토큰 미설정"
        return f"{prov} · {repo} ({self.hubBranch}) · {tok_label}"

    @Slot(str, str, str, str, bool, result=bool)
    def saveHubSettings(self, provider, repo, branch, token, include_models=True):
        from tools import hub as hf_hub
        try:
            saved = hf_hub.save_hub_config(
                provider=str(provider or "huggingface"),
                repo=str(repo or ""),
                branch=str(branch or "main"),
                include_models=bool(include_models),
                token=str(token) if token and str(token).strip() else None,
            )
            self.hub_config = saved
            prov_ko = "GitHub" if saved["provider"] == "github" else "Hugging Face"
            tok_saved = " + 토큰 암호화 저장됨(DPAPI)" if (token and str(token).strip()) else ""
            self.hub_status_note = f"허브 설정 저장 완료: {prov_ko} · {saved['repo']} ({saved['branch']}){tok_saved}"
            self.training_note = self.hub_status_note
            self.trainingChanged.emit()
            return True
        except Exception as exc:
            self.hub_status_note = f"허브 설정 오류: {hf_hub.redact_secrets(str(exc), [str(token or '')])}"
            self.training_note = self.hub_status_note
            self.trainingChanged.emit()
            return False

    @Slot(str)
    def clearHubToken(self, provider=""):
        from tools import hub as hf_hub
        target = str(provider or self.hubProvider)
        hf_hub.delete_hub_token(target)
        self.hub_status_note = "저장된 액세스 토큰을 암호화 금고에서 삭제했습니다."
        self.training_note = self.hub_status_note
        self.trainingChanged.emit()

    @Slot(str, str, str, str)
    def testHubConnectionSlot(self, provider="", repo="", branch="", token=""):
        if self.training_busy:
            return
        from tools import hub as hf_hub
        if repo.strip():
            if not self.saveHubSettings(
                provider or self.hubProvider,
                repo,
                branch or self.hubBranch,
                token,
                self.hubIncludeModels,
            ):
                return
        self._set_busy_task("원격 허브 연결 확인 중", "원격 허브 저장소 연결 및 액세스 권한 확인 중…", -1.0)
        self.hub_status_note = self.training_busy

        def work():
            try:
                res = hf_hub.test_hub_connection()
                rem = res.get("remote", {})
                prov_ko = "GitHub" if res.get("provider") == "github" else "Hugging Face"
                msg = (
                    f"연결 성공 ({prov_ko} · {res.get('repo')}@{res.get('branch')}): "
                    f"원격 녹화 {rem.get('files', 0)}개 ({rem.get('frames', 0)}프레임, {rem.get('size_mb', 0)}MB), "
                    f"공유 모델 {rem.get('models', 0)}개"
                )
                self.hub_status_note = msg
                self.training_note = msg
            except Exception as exc:
                err = f"허브 연결 확인 실패: {hf_hub.redact_secrets(str(exc), [str(token or '')])}"
                self.hub_status_note = err
                self.training_note = err
            self._set_busy_task("")

        import threading
        threading.Thread(target=work, daemon=True).start()
        _timer(self, "start")

    @Slot()
    @Slot(str, str, str, str, bool)
    def pushToHub(self, provider="", repo="", branch="", token="", include_models=True):
        if self.training_busy:
            return
        from tools import hub as hf_hub
        if provider or repo or branch or token:
            if not self.saveHubSettings(
                provider or self.hubProvider,
                repo or self.hubRepo,
                branch or self.hubBranch,
                token,
                include_models,
            ):
                return
        self.hub_config = hf_hub.load_hub_config()
        prov_ko = "GitHub" if self.hubProvider == "github" else "Hugging Face"
        self._set_busy_task(
            f"{prov_ko} 원격 허브 업로드 중",
            f"[1/2단계] {self.hubRepo} 저장소 인증 및 로컬 녹화·모델 탐색 중…",
            0.25,
        )
        self.hub_status_note = self.training_busy

        def work():
            from src.training_center import MODEL, REC_DIR
            try:
                client, info = hf_hub.create_hub_client()
                use_models = bool(self.hub_config.get("include_models", True))
                models = [MODEL] if (use_models and MODEL.is_file() and MODEL.with_suffix(".json").is_file()) else []
                self._set_busy_task(
                    f"{prov_ko} 원격 허브 업로드 중",
                    f"[2/2단계] {self.hubRepo} 저장소로 새 녹화 및 ONNX 모델 전송 중…",
                    0.65,
                )
                res = hf_hub.push_to_hub(client, REC_DIR, models=models)
                msg = (
                    f"허브 업로드 완료 ({info['provider']} · {info['repo']}): "
                    f"새 녹화 {res['new_recordings']}개, 모델 {res['models']}개 (기존 {res['already_on_hub']}개)"
                )
                self.hub_status_note = msg
                self.training_note = msg
            except Exception as exc:
                err = f"허브 업로드 실패: {hf_hub.redact_secrets(str(exc), [str(token or '')])}"
                self.hub_status_note = err
                self.training_note = err
            self._set_busy_task("")

        import threading
        threading.Thread(target=work, daemon=True).start()
        _timer(self, "start")

    @Slot()
    @Slot(str, str, str, str, bool)
    def pullFromHub(self, provider="", repo="", branch="", token="", include_models=True):
        if self.training_busy:
            return
        from tools import hub as hf_hub
        if provider or repo or branch or token:
            if not self.saveHubSettings(
                provider or self.hubProvider,
                repo or self.hubRepo,
                branch or self.hubBranch,
                token,
                include_models,
            ):
                return
        self.hub_config = hf_hub.load_hub_config()
        prov_ko = "GitHub" if self.hubProvider == "github" else "Hugging Face"
        self._set_busy_task(
            f"{prov_ko} 원격 허브 동기화 중",
            f"[1/3단계] {self.hubRepo} 저장소에서 팀 녹화·모델 내려받는 중…",
            0.25,
        )
        self.hub_status_note = self.training_busy

        def work():
            from src.training_center import REC_DIR
            try:
                client, info = hf_hub.create_hub_client()
                use_models = bool(self.hub_config.get("include_models", True))
                res = hf_hub.pull_from_hub(client, REC_DIR, include_models=use_models)
                from src.training_center import run_post_recording_pipeline
                rec_files = [
                    p
                    for p in sorted(REC_DIR.rglob("*.jsonl.gz"))
                    if "cleaned" not in p.parts and "augmented" not in p.parts
                ]
                total_f = len(rec_files)
                for idx, rec_file in enumerate(rec_files, 1):
                    if rec_file.parent != REC_DIR and not (REC_DIR / rec_file.name).exists():
                        import shutil
                        shutil.copy2(rec_file, REC_DIR / rec_file.name)
                        rec_file = REC_DIR / rec_file.name
                    if not (REC_DIR / "cleaned" / rec_file.name).exists():
                        self._set_busy_task(
                            f"{prov_ko} 원격 허브 동기화 중",
                            f"[2/3단계] 내려받은 녹화 품질검사·정제·증강 처리 중 ({idx}/{total_f}: {rec_file.name})…",
                            0.30 + 0.60 * (idx / max(1, total_f)),
                        )
                        run_post_recording_pipeline(
                            rec_file,
                            {"auto_quality_check": True, "auto_clean": True, "auto_offline_augment": True},
                        )
                msg = (
                    f"허브 동기화·정제·증강 완료 ({info['provider']} · {info['repo']}): "
                    f"새 녹화 {res['downloaded']}개, 모델 {res['models_downloaded']}개 내려받음"
                )
                self.hub_status_note = msg
                self.training_note = msg
                self._set_busy_task("")
                self.refreshTraining()
                return
            except Exception as exc:
                err = f"허브 동기화 실패: {hf_hub.redact_secrets(str(exc), [str(token or '')])}"
                self.hub_status_note = err
                self.training_note = err
            self._set_busy_task("")

        import threading
        threading.Thread(target=work, daemon=True).start()
        _timer(self, "start")

    @Slot(str, result=bool)
    def focusRecord(self, key):
        """Open a draft plan; starting is a separate action in the editor."""
        if self.stage_name != "main" or self.rec_mode != "off" or self.training_job.running:
            return False
        from src.gesture_catalog import BY_KEY
        if key and key not in BY_KEY:
            return False
        coverage = self._training_summary.get("coverage", {}).get(key, {})
        self.recording_editor.open(key, coverage)
        self.command(27)
        return True

    def _start_focused_plan(self, key, plan):
        if self.stage_name != "main" or self.rec_mode != "off" or self.training_job.running:
            return
        self._pause_for_change()
        self.use_index_pointer = False
        self.rec_mode, self.rec_focus, self.rec_plan = "guided", key, copy.deepcopy(plan)
        self.rec_paused = False
        self._async_update_live_spray(key if key else "all")
        self.stateChanged.emit()

    def _training_tick(self):
        job = self.training_job
        if self._training_was_running and not job.running:
            self._training_was_running = False
            s = job.snapshot()
            if s.get("error"):
                self.training_note = s["error"]
            else:
                imp = s.get("improvement")
                if isinstance(imp, dict) and imp.get("available"):
                    self.training_improvement = imp
                    self.training_note = f"학습 완료! {imp.get('headline', '새 모델의 개선 효과를 확인하세요.')}"
                else:
                    self.training_note = "학습 끝! 새 모델의 성적을 보여 드려요."
                self.model_version += 1
                self.stateChanged.emit()
            self.refreshTraining()
        self.trainingChanged.emit()

    @Property("QVariantList", notify=trainingChanged)
    def trainingRows(self):
        category = getattr(self, "training_filter", "전체")
        return [r for r in self.training_rows if category == "전체" or r["category"] == category]

    @Property(str, notify=trainingChanged)
    def trainingFilter(self):
        return getattr(self, "training_filter", "전체")

    @Property("QStringList", constant=True)
    def trainingCategories(self):
        from src.gesture_catalog import CATEGORIES
        return CATEGORIES

    @Property(str, notify=trainingChanged)
    def trainingCatalog(self):
        from src.gesture_catalog import TASKS
        return f"전체 {len(TASKS)}개 녹화 항목 · {len(self.trainingRows)}개 표시 · 미학습 동작도 녹화 가능"

    @Slot(str)
    def setTrainingFilter(self, category):
        if category in self.trainingCategories:
            self.training_filter = category
            self.trainingChanged.emit()

    # ------------------------------------------------------------------ Shortcut Bindings, Composite Gestures & Temporal Motions
    @Property("QVariantList", notify=trainingChanged)
    def sidebarGestureMappings(self):
        from src.action_bindings import format_action_badge, get_action_registry
        from src.gesture_catalog import BY_KEY, DISPLAY_KEYS

        reg = get_action_registry()
        items = [
            {"key": "move_cursor", "gesture": "반주먹 / 손 이동", "badge": "포인터 이동", "track": "static"},
        ]
        for k in DISPLAY_KEYS:
            t = BY_KEY.get(k)
            if t is None or t.free or k == "thumb_middle_drag":
                continue
            b = reg.get_binding(k)
            if not b.get("enabled", True):
                continue
            short_name = t.name.split("(")[0].strip() if "(" in t.name else t.name
            if short_name.startswith("모션 · "):
                short_name = short_name.replace("모션 · ", "", 1).strip()
            raw_badge = format_action_badge(str(b.get("action_type", "")), str(b.get("action_value", "")), None)
            if raw_badge.startswith("단축키: "):
                raw_badge = raw_badge.replace("단축키: ", "", 1)
            if raw_badge.startswith("마우스 "):
                raw_badge = raw_badge.replace("마우스 ", "", 1)
            ov_list = b.get("app_overrides") or []
            if ov_list:
                raw_badge = f"{raw_badge} (+앱{len(ov_list)})"
            items.append(
                {
                    "key": k,
                    "gesture": short_name,
                    "badge": raw_badge,
                    "track": t.track,
                }
            )
        for c_key, c_def in reg.composite_gestures.items():
            if not c_def.get("enabled", True):
                continue
            b = reg.get_binding(c_key)
            raw_badge = format_action_badge(str(b.get("action_type", "")), str(b.get("action_value", "")), None)
            if raw_badge.startswith("단축키: "):
                raw_badge = raw_badge.replace("단축키: ", "", 1)
            items.append(
                {
                    "key": c_key,
                    "gesture": str(c_def.get("name", c_key)),
                    "badge": raw_badge,
                    "track": "composite",
                }
            )
        return items

    @Property("QVariantList", notify=trainingChanged)
    def actionBindingRows(self):
        return [r for r in self.training_rows if r.get("can_train", False)]

    @Property("QVariantList", notify=trainingChanged)
    def compositeGestureRows(self):
        from src.action_bindings import POSTURE_NAMES_KO, TRIGGER_NAMES_KO, get_action_registry

        reg = get_action_registry()
        out = []
        for k, c in reg.composite_gestures.items():
            b = reg.get_binding(k)
            bp = str(c.get("base_posture", "thumb_index"))
            tr = str(c.get("trigger_type", "swipe_left"))
            out.append(
                {
                    "key": k,
                    "name": str(c.get("name", k)),
                    "base_posture": bp,
                    "base_posture_ko": POSTURE_NAMES_KO.get(bp, bp),
                    "trigger_type": tr,
                    "trigger_type_ko": TRIGGER_NAMES_KO.get(tr, tr),
                    "min_distance": float(c.get("min_distance", 0.08)),
                    "min_speed": float(c.get("min_speed", 0.22)),
                    "hold_seconds": float(c.get("hold_seconds", 0.55)),
                    "action_type": str(b.get("action_type", "hotkey")),
                    "action_value": str(b.get("action_value", "")),
                    "badge": str(b.get("badge", "")),
                    "detail": str(b.get("detail", "")),
                    "enabled": bool(b.get("enabled", True)),
                    "app_overrides": list(b.get("app_overrides") or []),
                }
            )
        return out

    @Property("QVariantList", notify=trainingChanged)
    def motionTrackRows(self):
        return [r for r in self.training_rows if r.get("track") == "motion"]

    @Property("QVariantList", notify=trainingChanged)
    def staticTrackRows(self):
        return [r for r in self.training_rows if r.get("track") == "static"]

    @Property("QVariantList", constant=True)
    def hotkeyPresets(self):
        from src.action_bindings import WINDOWS_HOTKEY_PRESETS
        return list(WINDOWS_HOTKEY_PRESETS)

    @Property("QStringList", constant=True)
    def hotkeyGroups(self):
        from src.gesture_presets import HOTKEY_GROUPS
        return list(HOTKEY_GROUPS)

    @Property("QVariantList", constant=True)
    def youtubeKeyPresets(self):
        from src.action_bindings import YOUTUBE_KEY_PRESETS
        out, seen = [], set()
        for k, v in YOUTUBE_KEY_PRESETS.items():
            if v["label"] in seen:       # "k" and "yt_play_pause" are the same key: one button
                continue
            seen.add(v["label"])
            out.append({"value": k, "label": v["label"], "badge": v["badge"]})
        return out

    @Property("QStringList", constant=True)
    def recipeGroups(self):
        from src.gesture_presets import RECIPE_GROUPS
        return list(RECIPE_GROUPS)

    @Property("QVariantList", constant=True)
    def motionRecipes(self):
        from src.gesture_presets import recipes_of
        return recipes_of("motion")

    @Property("QVariantList", constant=True)
    def compositeRecipes(self):
        from src.gesture_presets import recipes_of
        return recipes_of("composite")

    @Property("QVariantList", constant=True)
    def mouseActionPresets(self):
        from src.action_bindings import MOUSE_ACTION_LABELS
        return [{"value": k, "label": v["label"], "badge": v["badge"]} for k, v in MOUSE_ACTION_LABELS.items()]

    @Property("QVariantList", constant=True)
    def posturePresets(self):
        from src.action_bindings import POSTURE_NAMES_KO
        return [{"value": k, "label": v} for k, v in POSTURE_NAMES_KO.items()]

    @Property("QVariantList", constant=True)
    def compositePosturePresets(self):
        """Base shapes a composite gesture can start from: a fixed shape is the point of it, so no "any"."""
        from src.action_bindings import POSTURE_NAMES_KO
        return [{"value": k, "label": v} for k, v in POSTURE_NAMES_KO.items() if k != "any"]

    @Property("QVariantList", constant=True)
    def directionPresets(self):
        from src.action_bindings import DIRECTION_NAMES_KO
        return [{"value": k, "label": v} for k, v in DIRECTION_NAMES_KO.items()]

    @Property("QVariantList", constant=True)
    def compositeTriggerPresets(self):
        from src.action_bindings import TRIGGER_NAMES_KO
        return [{"value": k, "label": v} for k, v in TRIGGER_NAMES_KO.items()]

    @Slot(str, str, str, bool, result=bool)
    def saveGestureBinding(self, gesture_key, action_type, action_value, enabled=True):
        from src.action_bindings import get_action_registry
        try:
            reg = get_action_registry()
            saved = reg.set_binding(
                str(gesture_key or ""),
                str(action_type or "mouse_action"),
                str(action_value or "none"),
                enabled=bool(enabled),
            )
            self.training_note = f"단축키/동작 매핑 저장됨: [{gesture_key}] → {saved.get('badge')}"
            self.refreshTraining()
            self.stateChanged.emit()
            return True
        except Exception as exc:
            self.training_note = f"매핑 저장 실패: {exc}"
            self.trainingChanged.emit()
            return False

    # ------------------------------------------------------------------ gestures connected in words (LLM)
    def _llm_config(self):
        from src.llm_providers import LlmConfig, PROVIDERS
        from src import secret_store
        pid = self.llm_provider if self.llm_provider in PROVIDERS else "claude"
        saved = str(self.preferences.value(f"llmKey/{pid}", "") or "")
        key = secret_store.unprotect(saved) if saved else self._llm_session_keys.get(pid, "")
        return LlmConfig(provider=pid, model=str(self.preferences.value(f"llmModel/{pid}", "") or ""),
                         base_url=str(self.preferences.value(f"llmBase/{pid}", "") or ""), api_key=key)

    def _llm_set(self, busy=None, message=None):
        if busy is not None:
            self.llm_busy = busy
        if message is not None:
            self.llm_message = message
        self.llmChanged.emit()

    @Property("QVariantList", constant=True)
    def llmProviders(self):
        from src.llm_providers import PROVIDERS
        return [{"id": p.id, "label": p.label, "note": p.note, "needsKey": bool(p.key_env), "keyEnv": p.key_env,
                 "defaultModel": p.default_model, "defaultBase": p.base_url} for p in PROVIDERS.values()]

    @Property("QVariantMap", notify=llmChanged)
    def llmSettings(self):
        import os
        cfg = self._llm_config()
        spec = cfg.spec
        saved = bool(self.preferences.value(f"llmKey/{cfg.provider}", "")) or cfg.provider in self._llm_session_keys
        env = bool(spec.key_env and os.environ.get(spec.key_env))
        source = ("필요 없음" if not spec.key_env else "저장된 키 사용" if saved
                  else f"환경 변수 {spec.key_env} 사용" if env else "키 없음")
        from src.llm_binding import describe_request
        return {"provider": cfg.provider, "model": cfg.model, "modelShown": cfg.model_name(), "baseUrl": cfg.base_url,
                "defaultBase": spec.base_url, "needsKey": bool(spec.key_env), "hasKey": bool(cfg.key()) or not spec.key_env,
                "keySource": source, "keySaved": saved, "privacy": describe_request(cfg, "")}

    @Property(bool, notify=llmChanged)
    def llmBusy(self):
        return self.llm_busy

    @Property(str, notify=llmChanged)
    def llmMessage(self):
        return self.llm_message

    @Property("QVariantMap", notify=llmChanged)
    def llmResult(self):
        return self.llm_result

    @Property("QVariantList", notify=llmChanged)
    def llmModels(self):
        return self.llm_models

    @Slot(str)
    def setLlmProvider(self, pid):
        from src.llm_providers import PROVIDERS
        if pid in PROVIDERS and pid != self.llm_provider:
            self.llm_provider = pid
            self.preferences.setValue("llmProvider", pid)
            self.llm_models = []
            self._llm_set(message="")

    @Slot(str)
    def setLlmModel(self, model):
        self.preferences.setValue(f"llmModel/{self.llm_provider}", str(model or "").strip())
        self.llmChanged.emit()

    @Slot(str)
    def setLlmBaseUrl(self, url):
        url = str(url or "").strip()
        if url and not url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            self._llm_set(message="주소는 https:// 또는 이 PC(http://localhost)만 쓸 수 있어요.")
            return
        self.preferences.setValue(f"llmBase/{self.llm_provider}", url)
        self.llmChanged.emit()

    @Slot(str)
    def saveLlmKey(self, key):
        from src import secret_store
        key = str(key or "").strip()
        if not key:
            return
        token = secret_store.protect(key)
        if token:
            self.preferences.setValue(f"llmKey/{self.llm_provider}", token)
            self._llm_set(message="키를 이 Windows 사용자 전용으로 암호화해 저장했어요.")
        else:
            self._llm_session_keys[self.llm_provider] = key
            self._llm_set(message="이 시스템에서는 키를 저장할 수 없어 이번 실행 동안만 써요.")

    @Slot()
    def clearLlmKey(self):
        self.preferences.remove(f"llmKey/{self.llm_provider}")
        self._llm_session_keys.pop(self.llm_provider, None)
        self._llm_set(message="저장한 키를 지웠어요.")

    def _llm_job(self, work, busy_text):
        if self.llm_busy:
            return
        self._llm_set(busy=True, message=busy_text)

        def run():
            from src.llm_providers import LlmError
            try:
                message = work()
            except LlmError as exc:
                message = str(exc)
            except Exception as exc:                      # never let a worker error reach the screen as a crash
                message = f"실패했어요: {type(exc).__name__}"
            self._llm_set(busy=False, message=message)

        import threading
        threading.Thread(target=run, daemon=True).start()

    @Slot()
    def loadLlmModels(self):
        cfg = self._llm_config()

        def work():
            from src.llm_providers import list_models
            self.llm_models = list_models(cfg)
            return f"모델 {len(self.llm_models)}개를 불러왔어요." if self.llm_models else "쓸 수 있는 모델이 없어요."
        self._llm_job(work, "모델 목록을 불러오는 중…")

    @Slot(str)
    def askLlm(self, text):
        from src.action_bindings import get_action_registry
        from src.llm_binding import ask, gesture_list
        cfg = self._llm_config()
        gestures = gesture_list(get_action_registry())
        shown = dict(self.pose_probe_view) if (self.pose_probe_view or {}).get("state") == "done" else None
        key = self.captured_key

        def work():
            result = ask(cfg, text, gestures, shown=shown, key=key)
            self.llm_result = {**result, "request": str(text).strip()}
            n = sum(1 for r in result["proposals"] if r["ok"])
            if not result["proposals"]:
                return "바꿀 연결을 찾지 못했어요." + (" 아래 질문을 확인해 주세요." if result["questions"] else "")
            return f"제안 {len(result['proposals'])}개 ({n}개 적용 가능). 확인하고 [선택한 연결 적용]을 눌러 주세요."
        self._llm_job(work, f"{cfg.spec.label}에 묻는 중…")

    # ------------------------------------------------------------------ drawing mode (src/draw_mode.py)
    def _shapes(self):
        if self._draw_store is None:
            from src.draw_mode import ShapeStore
            self._draw_store = ShapeStore()
        return self._draw_store

    def _draw_edited(self, message=""):
        self.online_commands.append(("draw_reload",))       # the runtime has its own copy of the shapes
        if message:
            self.draw_message = message
        self.drawShapesChanged.emit()

    @Property("QVariantMap", notify=drawChanged)
    def drawView(self):
        return self.draw_view

    @Property(bool, notify=drawChanged)
    def drawOn(self):
        return bool(self.draw_view.get("on"))

    @Property("QVariantList", notify=drawChanged)
    def drawArea(self):
        """The primary monitor [x, y, w, h] (logical px): where the drawing overlay lies (the box maps onto it)."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return [0, 0, 1920, 1080]
        g = screen.geometry()
        return [g.x(), g.y(), g.width(), g.height()]

    @Slot()
    def toggleDrawMode(self):
        self.online_commands.append(("draw_mode", None))

    @Property("QVariantList", notify=drawShapesChanged)
    def drawShapes(self):
        from src.action_bindings import get_action_registry
        reg = get_action_registry()
        out = []
        for shape in self._shapes().shapes():
            b = reg.get_binding(shape["key"])
            out.append({**shape, "action_type": str(b.get("action_type", "mouse_action")),
                        "action_value": str(b.get("action_value", "none")), "badge": str(b.get("badge", ""))})
        return out

    # ------------------------------------------------------------------ gesture sequences (src/sequence.py)
    @Property("QVariantList", notify=drawShapesChanged)
    def sequenceRows(self):
        from src.action_bindings import get_action_registry
        from src.sequence import SequenceStore
        from src.path_gesture import PathStore
        reg = get_action_registry()
        rows = [{**row, "kind": "sequence"} for row in SequenceStore().rows()]
        rows += [{**row, "kind": "path"} for row in PathStore().rows()]     # hand shape + drawing (src/path_gesture.py)
        return [{**row, "badge": str(reg.get_binding(row["key"]).get("badge", ""))} for row in rows]

    @Slot(str)
    def removeSequence(self, key):
        from src.path_gesture import PathStore
        from src.sequence import SequenceStore
        if SequenceStore().remove(key) or PathStore().remove(key):
            self.online_commands.append(("seq_reload",))
            self.draw_message = "이어 하기를 지웠어요."
        self.drawShapesChanged.emit()
        self.llmChanged.emit()

    @Slot(str, bool)
    def setSequenceEnabled(self, key, on):
        from src.path_gesture import PathStore
        from src.sequence import SequenceStore
        SequenceStore().set_enabled(key, on)
        PathStore().set_enabled(key, on)
        self.online_commands.append(("seq_reload",))
        self.drawShapesChanged.emit()

    @Property(float, notify=drawShapesChanged)
    def drawThreshold(self):
        return float(self._shapes().threshold)

    @Property(str, notify=drawShapesChanged)
    def drawMessage(self):
        return self.draw_message

    @Property("QVariantMap", notify=drawShapesChanged)
    def drawTest(self):
        return self.draw_test

    @Slot(str, "QVariantList", result=bool)
    def addDrawShape(self, name, strokes):
        try:
            key = self._shapes().add(name, [list(st) for st in strokes])
        except ValueError as exc:
            self.draw_message = str(exc)
            self.drawShapesChanged.emit()
            return False
        self._draw_edited(f"'{name}' 그림을 등록했어요. 아래에서 명령을 연결해 주세요.")
        return bool(key)

    @Slot(str, "QVariantList")
    def addDrawExample(self, key, stroke):
        try:
            self._shapes().add_example(key, list(stroke))
        except ValueError as exc:
            self.draw_message = str(exc)
            self.drawShapesChanged.emit()
            return
        self._draw_edited("예시를 더했어요.")

    @Slot(str)
    def removeDrawShape(self, key):
        if self._shapes().remove(key):
            self._draw_edited("그림을 지웠어요.")

    @Slot(str, bool)
    def setDrawShapeEnabled(self, key, on):
        self._shapes().set_enabled(key, on)
        self._draw_edited()

    @Slot(float)
    def setDrawThreshold(self, value):
        self._shapes().set_threshold(value)
        self._draw_edited()

    @Slot(str, str, str, result=bool)
    def setDrawBinding(self, key, action_type, action_value):
        from src.action_bindings import get_action_registry
        from src.llm_binding import _check_value
        problem = _check_value(str(action_type), str(action_value).strip())
        if problem:
            self.draw_message = problem
            self.drawShapesChanged.emit()
            return False
        reg = get_action_registry()
        current = reg.get_binding(key)
        reg.set_binding(key, action_type, str(action_value).strip(), enabled=bool(current.get("enabled", True)),
                        track="drawing")
        self.draw_message = f"{self._shapes().name(key)} → {reg.get_binding(key).get('badge')}"
        self.drawShapesChanged.emit()
        return True

    @Slot("QVariantList")
    def testDrawStroke(self, stroke):
        self.draw_test = self._shapes().test(list(stroke))
        self.drawShapesChanged.emit()

    @Property("QVariantMap", notify=llmChanged)
    def llmContext(self):
        """What 이/저 point to: the shown hand shape and the remembered key."""
        return {"pose": self.pose_probe_view or {}, "key": self.captured_key, "capturing": self.key_capturing}

    @Slot()
    def startPoseProbe(self):
        self.online_commands.append(("pose_probe",))
        self.pose_probe_view = {"state": "countdown", "seconds": 3}
        self._llm_set(message="손을 카메라에 보여 주세요. 3초 뒤 2초 동안 손 모양을 봅니다.")

    @Slot()
    def clearPoseProbe(self):
        self.online_commands.append(("pose_probe_cancel",))
        self.pose_probe_view = None
        self.llmChanged.emit()

    @Slot()
    def captureKey(self):
        if self.key_capturing:
            return
        import sys
        if sys.platform != "win32":
            self._llm_set(message="키 기억하기는 Windows에서만 돼요. 요청에 키를 직접 적어 주세요 (예: ctrl+shift+t).")
            return
        self.key_capturing = True
        self._llm_set(message="8초 안에 기억할 키 조합을 누르세요 (예: Ctrl+Shift+T). 이 조합 하나만 기억해요.")

        def work():
            from src.key_capture import capture
            combo = None
            try:
                combo = capture(timeout=8.0)
            finally:
                self.key_capturing = False
            if combo:
                self.captured_key = combo
                self._llm_set(message=f"기억한 키: {combo}")
            else:
                self._llm_set(message="키 조합이 눌리지 않았어요.")
        import threading
        threading.Thread(target=work, daemon=True).start()

    @Slot()
    def clearCapturedKey(self):
        self.captured_key = ""
        self.llmChanged.emit()

    @Slot(int, bool)
    def setLlmProposalChecked(self, index, on):
        rows = list(self.llm_result.get("proposals") or [])
        if 0 <= index < len(rows) and rows[index].get("ok"):
            rows[index] = {**rows[index], "checked": bool(on)}
            self.llm_result = {**self.llm_result, "proposals": rows}
            self.llmChanged.emit()

    @Slot()
    def applyLlmProposals(self):
        from src.action_bindings import get_action_registry
        from src.llm_binding import History, apply
        rows = list(self.llm_result.get("proposals") or [])
        try:
            done = apply(rows, get_action_registry(), history=History(), request=str(self.llm_result.get("request", "")))
        except Exception as exc:
            self._llm_set(message=f"저장하지 못했어요: {exc}")
            return
        if not done:
            self._llm_set(message="선택한 연결이 없어요.")
            return
        if all(line.startswith("저장하지 못함") for line in done):   # nothing was saved: keep the proposals on screen
            self._llm_set(message=" / ".join(done))
            return
        self.llm_result = {}
        self.online_commands.append(("seq_reload",))    # a new sequence: the runtime reads the file again
        self.drawShapesChanged.emit()
        self.training_note = "말·글로 연결: " + " / ".join(done)
        self.refreshTraining()
        self.stateChanged.emit()
        self._llm_set(message="적용했어요: " + " / ".join(done))

    @Property("QVariantList", notify=llmChanged)
    def llmHistory(self):
        """What the AI connected, newest first, with whether it is still in force (src/llm_binding.History)."""
        from src.action_bindings import get_action_registry
        from src.llm_binding import History
        try:
            return History().rows(get_action_registry())
        except Exception:
            return []

    @Slot(int)
    def deleteLlmConnection(self, index):
        from src.action_bindings import get_action_registry
        from src.llm_binding import History
        try:
            message = History().delete(int(index), get_action_registry())
        except ValueError as exc:
            message = str(exc)
        self.online_commands.append(("seq_reload",))
        self.drawShapesChanged.emit()
        self.refreshTraining()
        self.stateChanged.emit()
        self._llm_set(message=message)

    @Slot(int)
    def forgetLlmConnection(self, index):
        from src.llm_binding import History
        History().forget(int(index))
        self._llm_set(message="목록에서 지웠어요.")

    @Slot(int)
    def undoLlmConnection(self, index):
        from src.action_bindings import get_action_registry
        from src.llm_binding import History
        try:
            message = History().undo(int(index), get_action_registry())
        except ValueError as exc:
            message = str(exc)
        self.online_commands.append(("seq_reload",))
        self.drawShapesChanged.emit()
        self.refreshTraining()
        self.stateChanged.emit()
        self._llm_set(message=message)

    @Slot()
    def clearLlmResult(self):
        self.llm_result = {}
        self._llm_set(message="")

    @Slot()
    def startVoiceTyping(self):
        """Windows voice typing (Win+H) into the focused text box: speech and typing go through the same request box."""
        import sys
        if sys.platform != "win32" or self.dry_run:
            self._llm_set(message="음성 입력은 Windows의 음성 입력(Win+H)을 씁니다. 글상자를 누르고 Win+H를 눌러 주세요.")
            return

        def press():
            try:
                import pyautogui
                pyautogui.hotkey("win", "h", _pause=False)
            except Exception:
                self._llm_set(message="음성 입력을 열지 못했어요. 글상자를 누르고 Win+H를 직접 눌러 주세요.")
        QTimer.singleShot(200, press)      # after the text box has the focus
        self._llm_set(message="말씀하세요. 끝나면 [제안 받기]를 누르세요. (Windows 음성 입력, 인터넷 필요)")

    @Slot(str, result=bool)
    def resetGestureBinding(self, gesture_key):
        from src.action_bindings import get_action_registry
        try:
            reg = get_action_registry()
            saved = reg.reset_binding(str(gesture_key or ""))
            self.training_note = f"기본 매핑으로 복원됨: [{gesture_key}] → {saved.get('badge')}"
            self.refreshTraining()
            self.stateChanged.emit()
            return True
        except Exception as exc:
            self.training_note = f"매핑 초기화 실패: {exc}"
            self.trainingChanged.emit()
            return False

    @Slot(str, str, str, str, result=bool)
    def addAppOverrideSlot(self, gesture_key, app_match, action_type, action_value):
        from src.action_bindings import get_action_registry
        try:
            reg = get_action_registry()
            saved = reg.add_app_override(
                str(gesture_key or ""),
                str(app_match or ""),
                str(action_type or "hotkey"),
                str(action_value or ""),
            )
            self.training_note = f"프로그램 전용 단축키 등록됨 ({app_match}): {saved.get('badge')}"
            self.refreshTraining()
            return True
        except Exception as exc:
            self.training_note = f"앱 전용 단축키 등록 실패: {exc}"
            self.trainingChanged.emit()
            return False

    @Slot(str, str, result=bool)
    def removeAppOverrideSlot(self, gesture_key, app_match):
        from src.action_bindings import get_action_registry
        try:
            reg = get_action_registry()
            reg.remove_app_override(str(gesture_key or ""), str(app_match or ""))
            self.training_note = f"프로그램 전용 단축키 삭제됨 ({app_match})"
            self.refreshTraining()
            return True
        except Exception as exc:
            self.training_note = f"앱 전용 단축키 삭제 실패: {exc}"
            self.trainingChanged.emit()
            return False

    @Slot(str, str, str, str, str, float, float, result=bool)
    def registerCompositeGestureSlot(
        self,
        name,
        base_posture,
        trigger_type,
        action_type,
        action_value,
        min_distance=0.08,
        hold_seconds=0.55,
    ):
        from src.action_bindings import get_action_registry
        try:
            reg = get_action_registry()
            comp = reg.register_composite_gesture(
                key="",
                name=str(name or ""),
                base_posture=str(base_posture or "thumb_index"),
                trigger_type=str(trigger_type or "swipe_left"),
                action_type=str(action_type or "hotkey"),
                action_value=str(action_value or "ctrl+tab"),
                min_distance=float(min_distance),
                hold_seconds=float(hold_seconds),
            )
            self.training_note = f"새 복합 손동작 등록 완료: {comp['name']} ({comp['key']})"
            self.refreshTraining()
            self.stateChanged.emit()
            return True
        except Exception as exc:
            self.training_note = f"복합 손동작 등록 실패: {exc}"
            self.trainingChanged.emit()
            return False

    @Slot(str, result=bool)
    def deleteCompositeGestureSlot(self, key):
        from src.action_bindings import get_action_registry
        try:
            reg = get_action_registry()
            ok = reg.delete_composite_gesture(str(key or ""))
            self.training_note = f"복합 손동작 삭제됨 ({key})" if ok else f"삭제할 복합 동작을 찾지 못했습니다 ({key})"
            self.refreshTraining()
            self.stateChanged.emit()
            return bool(ok)
        except Exception as exc:
            self.training_note = f"복합 손동작 삭제 실패: {exc}"
            self.trainingChanged.emit()
            return False

    @Slot(str, str, str, str, str, str, float, float, result=bool)
    def registerMotionGestureSlot(
        self,
        key,
        name,
        posture,
        direction,
        action_type,
        action_value,
        min_distance=0.14,
        min_speed=0.25,
    ):
        from src.action_bindings import get_action_registry
        try:
            reg = get_action_registry()
            m_def = reg.register_motion_gesture(
                key=str(key or ""),
                name=str(name or ""),
                posture=str(posture or "open_hand"),
                direction=str(direction or "right"),
                action_type=str(action_type or "hotkey"),
                action_value=str(action_value or "alt+right"),
                min_distance=float(min_distance),
                min_speed=float(min_speed),
            )
            self.training_note = (
                f"새 시계열 모션 동작 등록 완료: [{m_def['name']}] · "
                f"[이 모션 녹화] 후 [시계열 모션 궤적 학습]을 실행하세요."
            )
            self.refreshTraining()
            self.stateChanged.emit()
            return True
        except Exception as exc:
            self.training_note = f"시계열 모션 등록 실패: {exc}"
            self.trainingChanged.emit()
            return False

    @Slot(str, result=bool)
    def deleteMotionGestureSlot(self, key):
        from src.action_bindings import get_action_registry
        try:
            reg = get_action_registry()
            ok = reg.delete_motion_gesture(str(key or ""))
            self.training_note = f"사용자 시계열 모션 삭제됨 ({key})" if ok else "기본 내장 모션은 삭제할 수 없습니다 (비활성화 또는 매핑 변경 가능)"
            self.refreshTraining()
            self.stateChanged.emit()
            return bool(ok)
        except Exception as exc:
            self.training_note = f"시계열 모션 삭제 실패: {exc}"
            self.trainingChanged.emit()
            return False

    @Slot(str, result=bool)
    def trainMotionTemplatesSlot(self, target_key=""):
        """Dedicated Time-Series Motion Learning: trains 16-step trajectory & velocity templates (+ TCN if requested).

        Reading every recording takes 10+ s, so it runs on a worker thread (it used to freeze the window);
        the result comes back on the UI thread through `motionTemplatesDone` -> `_finish_motion_templates`."""
        if self.training_busy or self.training_job.running:
            return False
        from src.motion_mode import train_motion_templates_from_recordings
        from src.training_center import REC_DIR

        clean_key = str(target_key or "").strip()
        targets = [clean_key] if clean_key else None
        self._set_busy_task("시계열 모션 궤적 학습", "녹화에서 모션 스트로크(쓸기 구간)를 읽는 중…")

        def work():
            try:
                res = train_motion_templates_from_recordings(rec_dir=REC_DIR, target_keys=targets)
                self.motionTemplatesDone.emit(clean_key, res, "")
            except Exception as exc:
                self.motionTemplatesDone.emit(clean_key, None, str(exc))

        import threading
        threading.Thread(target=work, daemon=True).start()
        _timer(self, "start")
        return True

    def _finish_motion_templates(self, clean_key, res, error=""):
        """UI thread: report the template result and, for one motion, start its TCN specialist training."""
        self._set_busy_task("")
        if error or not isinstance(res, dict):
            self.training_note = f"시계열 모션 학습 오류: {error or '결과 없음'}"
            self.trainingChanged.emit()
            return False
        cnt = int(res.get("trained_count", 0))
        if clean_key:
            from src.gesture_catalog import BY_KEY
            task_obj = BY_KEY.get(clean_key)
            t_name = task_obj.name if task_obj else clean_key
            if cnt > 0:
                # Also trigger binary TCN specialist training for this motion if recordings exist
                self.trainSelectedGesture(clean_key)
                t_info = (res.get("templates") or {}).get(clean_key, {})
                tr_pct = float(t_info.get("trajectory_recognition_rate", 0.0)) * 100.0
                ret_pct = float(t_info.get("return_suppression_rate", 1.0)) * 100.0
                self.training_note = (
                    f"시계열 모션 궤적 템플릿 학습 완료 ({t_info.get('name', t_name)}): "
                    f"궤적 일치 {tr_pct:.0f}% · 역방향 복귀 오발화 차단 {ret_pct:.0f}% (+ 시계열 TCN 병행 학습 중)"
                )
                if not self.training_job.running:
                    self.refreshTraining()
                else:
                    self.trainingChanged.emit()
            else:
                self.training_note = (
                    f"'{t_name}' 동작의 시계열 모션 녹화 데이터가 아직 없습니다 (현재 0회). "
                    f"먼저 [집중 녹화] 버튼으로 모션 스트로크를 녹화한 뒤 학습하세요."
                )
                self.trainingChanged.emit()
        else:
            if cnt > 0:
                self.training_note = f"전체 시계열 모션 {cnt}개 동작의 16-step 궤적·속도 프로파일 템플릿 학습 완료!"
            else:
                self.training_note = "학습할 시계열 모션 녹화 데이터가 아직 없습니다. 먼저 모션 동작을 녹화하세요."
            self.refreshTraining()
        return cnt > 0

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
        return self.training_job.running

    @Property(bool, notify=trainingChanged)
    def trainingCanStart(self):
        return bool(
            not self.training_job.running
            and not getattr(self, "training_busy", "")
            and self.rec_mode == "off"
            and len(getattr(self, "training_files", None) or ()) > 0
        )

    @Property(str, notify=trainingChanged)
    def trainingStage(self):
        s = self.training_job.snapshot()
        if not s.get("stage"):
            return ""
        text = s["stage"]
        if s.get("target"):
            from src.gesture_catalog import NAMES
            text = NAMES.get(s["target"], s["target"]) + " · " + text
        if s.get("running") and s.get("epoch"):
            text += f" · 반복 {s['epoch']}/{s['epochs']} · 손실 {s.get('loss', 0):.3f}"
        if s.get("running"):
            text += f" · {int(time.time() - s['started'])}초"
        if s.get("skipped"):
            text += f" · 다음 학습으로 미룬 녹화 {len(s['skipped'])}개"
        return text

    @Property(float, notify=trainingChanged)
    def trainingProgress(self):
        return float(self.training_job.snapshot().get("progress", 0.0))

    @Property(bool, notify=trainingChanged)
    def appWorking(self):
        return bool(self.training_job.running or getattr(self, "training_busy", ""))

    @Property(str, notify=trainingChanged)
    def workTitle(self):
        if self.training_job.running:
            s = self.training_job.snapshot()
            dev_tag = "GPU 가속" if self.trainingDevice == "gpu" else "CPU"
            if s.get("target"):
                from src.gesture_catalog import NAMES
                tname = NAMES.get(s["target"], s["target"])
                return f"AI 모델 선택 학습 진행 중 ({tname} · {dev_tag})"
            return f"전체 동작 통합 AI 모델 학습 진행 중 ({dev_tag})"
        if getattr(self, "training_busy", ""):
            return str(getattr(self, "_task_title", "") or self.training_busy)
        return ""

    @Property(str, notify=trainingChanged)
    def workDetail(self):
        if self.training_job.running:
            return self.trainingStage or "학습 프로세스 초기화 및 녹화 데이터 준비 중…"
        if getattr(self, "training_busy", ""):
            return str(getattr(self, "_task_step", "") or self.training_busy)
        return ""

    @Property(float, notify=trainingChanged)
    def workProgress(self):
        if self.training_job.running:
            return float(self.training_job.snapshot().get("progress", 0.0))
        if getattr(self, "training_busy", ""):
            return float(getattr(self, "_task_progress", -1.0))
        return 0.0

    @Property(bool, notify=trainingChanged)
    def workIndeterminate(self):
        if self.training_job.running:
            p = float(self.training_job.snapshot().get("progress", 0.0))
            return p <= 0.015
        if getattr(self, "training_busy", ""):
            return float(getattr(self, "_task_progress", -1.0)) < 0.0
        return False

    @Property(str, notify=trainingChanged)
    def workPercentText(self):
        if not self.appWorking:
            return ""
        if self.workIndeterminate:
            return "처리 중"
        pct = int(round(max(0.0, min(1.0, self.workProgress)) * 100))
        return f"{pct}%"

    @Property(str, notify=trainingChanged)
    def workElapsedText(self):
        if self.training_job.running:
            started = self.training_job.snapshot().get("started") or time.time()
            sec = max(0, int(time.time() - float(started)))
            return f"{sec}초 경과"
        if getattr(self, "training_busy", ""):
            started = getattr(self, "_task_started", 0.0) or time.time()
            sec = max(0, int(time.time() - float(started)))
            return f"{sec}초 경과"
        return ""

    @Property("QVariantList", notify=trainingChanged)
    def trainingLosses(self):
        return list(self.training_job.snapshot().get("losses", []))[-60:]

    @Property(bool, notify=stateChanged)
    def tcnGate(self):
        return bool(self.tcn_gate)

    @Property(bool, notify=stateChanged)
    def tcnAvailable(self):
        from src.gesture_gate import DEFAULT_MODEL
        return DEFAULT_MODEL.exists()

    @Property(str, notify=stateChanged)
    def gateCalibratedConfidenceText(self):
        gs = getattr(self, "gate_status", None)
        if not isinstance(gs, dict):
            return "보정 확신도: 대기 중 (T=0.81)"
        conf_pct = int(round(float(gs.get("calibrated_confidence", 0.0)) * 100.0))
        top_ko = gs.get("top_gesture_ko") or gs.get("top_gesture") or "없음"
        temp = float(gs.get("temperature", 0.81))
        q_score = float(gs.get("quality_score", 1.0))
        return f"보정 확신도 {conf_pct}% (T={temp:.2f}) · 동작: {top_ko} · 품질 {q_score:.2f}"

    @Property(str, notify=stateChanged)
    def gateRejectionReasonText(self):
        gs = getattr(self, "gate_status", None)
        if not isinstance(gs, dict):
            return "거절/보류 사유: 없음"
        reason_ko = gs.get("rejection_reason_ko") or "정상 (승인)"
        q_w = int(gs.get("quality_withheld", 0))
        ood_v = int(gs.get("ood_vetoed", 0))
        sp_v = int(gs.get("spatial_vetoed", 0))
        return f"판정 사유: {reason_ko}  (누적 보류 · 품질 {q_w} / OOD {ood_v} / 중립억제 {sp_v})"

    @Slot(bool)
    def setTcnGate(self, on):
        self.tcn_gate = bool(on)
        self.stateChanged.emit()

    @Property(str, notify=stateChanged)
    def gateModel(self):
        return self.gate_model

    @Slot(str)
    def setGateModel(self, value):
        self.gate_model = "online" if value == "online" else "tcn"
        self.stateChanged.emit()

    def _review_saved_recording(self, rec_view) -> None:
        """A recording was saved: show its quality banner. The check runs after the save ("pending"), so the same file comes
        again with its result; a failed one is excluded from training by the check itself, and the lists are read again so
        they show it."""
        if not (isinstance(rec_view, dict) and rec_view.get("mode") == "off" and rec_view.get("file")):
            return
        rec_file = rec_view.get("file")
        handled_key = (rec_file, rec_view.get("quality_status"))
        if handled_key == getattr(self, "_last_handled_rec_file", None):
            return
        self._last_handled_rec_file = handled_key
        q_msg = rec_view.get("quality_message") or rec_view.get("sub") or f"저장됨: {rec_file}"
        q_status = rec_view.get("quality_status") or "pass"
        self.last_rec_review = {
            "file": rec_file,
            "status": q_status,
            "score": rec_view.get("quality_score"),
            "message": str(q_msg),
        }
        self.training_note = str(q_msg)
        self.trainingChanged.emit()
        if q_status != "pending":
            self.refreshTraining()

    def _auto_save_finished_recording(self, rec_view) -> None:
        """Recording alone, far from the screen: a finished guided session is saved by itself after `rec_auto_save`
        seconds, so nobody has to walk back to press "녹화 끝 (저장)"."""
        finished = isinstance(rec_view, dict) and rec_view.get("finished") and self.rec_mode == "guided"
        if not finished or float(rec_view.get("auto_save") or 0.0) <= 0.0:
            self._rec_finished_at = None
            return
        now = time.perf_counter()
        if getattr(self, "_rec_finished_at", None) is None:
            self._rec_finished_at = now
        elif now - self._rec_finished_at >= float(rec_view["auto_save"]):
            self._rec_finished_at = None
            self.setRecMode("off")

    @Slot()
    def recStart(self):
        if self.rec_mode == "free" and self.rec_paused:
            self.rec_paused = False
        else:
            self.rec_advance += 1
            self.rec_paused = False
        self.stateChanged.emit()

    @Slot()
    def beginPalmCalibration(self):
        if self.stage_name != "main" or self.use_index_pointer:
            return
        self.palm_axes_requests[0] += 1
        self.stateChanged.emit()

    @Slot()
    def cancelPalmCalibration(self):
        self.palm_axes_requests[1] += 1
        self.stateChanged.emit()

    @Slot()
    def resetPalmCalibration(self):
        self.palm_axes_requests[2] += 1
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
        return "기준점 · 검지 끝" if self.pointer_anchor == "index" else "기준점 · " + self.anchorJointsLabel

    # ------------------------------------------------------------------ cursor reference joints
    @Property("QVariantList", notify=stateChanged)
    def anchorJoints(self):
        return list(self.anchor_joints)

    @Property(str, notify=stateChanged)
    def anchorJointsLabel(self):
        from src.gesture_engine import describe_anchor_joints
        return describe_anchor_joints(self.anchor_joints)

    @Property("QVariantList", constant=True)
    def handJointNames(self):
        from src.gesture_engine import HAND_JOINT_NAMES
        return list(HAND_JOINT_NAMES)

    @Property("QVariantList", constant=True)
    def stableAnchorJoints(self):
        from src.gesture_engine import STABLE_ANCHOR_JOINTS
        return sorted(STABLE_ANCHOR_JOINTS)

    @Slot("QVariantList")
    def setAnchorJoints(self, joints):
        """Use these landmarks (their centre) as the cursor reference. [] = back to the palm-centre default.

        In index-pointer mode this also switches the cursor reference to "직접 지정" (anchor "palm"),
        since picking points is how the user says which point to follow."""
        from src.gesture_engine import normalize_anchor_joints
        picked = normalize_anchor_joints(joints)
        if picked == (0, 9):
            picked = ()      # wrist + middle MCP centre is exactly the default palm centre
        switch = self.use_index_pointer and self.pointer_anchor != "palm"
        if picked == self.anchor_joints and not switch:
            return
        self._pause_for_change()
        self.anchor_joints = picked
        self.preferences.setValue("cursorAnchorJoints", ",".join(str(j) for j in picked))
        if switch:
            self.pointer_anchor = "palm"
            self.preferences.setValue("pointerAnchor", "palm")
        self.stateChanged.emit()

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
            anchor=self.pointer_anchor, anchor_joints=self.anchor_joints, hand_crop=self.hand_crop, axes=self.axes,
            calibrating=self.calibrating, sensitivity=self.pointer_sensitivity,
            show_fingertips=self.show_fingertips, profile=self.profile, motion_request=self.motion_request,
            palm_axes_request=self.palm_axes_requests[0], palm_axes_cancel=self.palm_axes_requests[1],
            palm_axes_reset=self.palm_axes_requests[2],
            rec_mode=self.rec_mode, rec_advance=self.rec_advance, tcn_gate=self.tcn_gate, gate_model=self.gate_model,
            virtual_hand=self.virtual_hand_on and not self.use_index_pointer, virtual_box=self.virtual_box,
            screen_ratio=_screen_ratio(),
            rec_focus=self.rec_focus, model_version=self.model_version,
            rec_plan=copy.deepcopy(self.rec_plan), rec_paused=self.rec_paused,
            rec_discard=self.rec_discard,
        )

    LAG_FPS, RECOVER_FPS = 20.0, 26.0     # camera loop speed at which background learning is slowed / let go again

    def throttle_learning(self, fps):
        """Spec 13: background learning must not slow the camera. Below LAG_FPS the learning process drops to idle priority, back
        to below normal from RECOVER_FPS (the gap keeps it from flipping every frame)."""
        job = self.online_job
        if job is None or not job.running or not fps:
            self.learn_slowed = False
            return
        slowed = getattr(self, "learn_slowed", False)
        if not slowed and fps < self.LAG_FPS:
            self.learn_slowed = job.set_low_priority(True) or slowed
        elif slowed and fps >= self.RECOVER_FPS:
            job.set_low_priority(False)
            self.learn_slowed = False

    def emergency_stop(self):
        """Spec 10 kill switch (Pause key, anywhere): control off, buttons and keys let go, background learning stopped."""
        if self.online_job is not None and self.online_job.running:
            self.online_job.stop()
            self.learn_message = "긴급 정지로 학습을 멈췄어요"
            self.learnChanged.emit()

    def apply_snapshot(self, snap):
        """Render one runtime snapshot (UI thread)."""
        self.throttle_learning(getattr(snap, "fps", 0.0))
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
        self.toggle_progress = getattr(snap, "toggle_progress", 0.0)
        self.set_registration(getattr(snap, "registration", None))
        self.set_live_gesture(getattr(snap, "online_gesture", ""))
        self.set_virtual_hand(getattr(snap, "virtual_hand", None), snap.paused)
        self._pending_preview_hand = getattr(snap, "preview_hand", None) or {}
        draw = getattr(snap, "draw", None) or {}
        if draw != self.draw_view:
            self.draw_view = draw
            self.drawChanged.emit()
        probe = getattr(snap, "pose_probe", None)
        if probe != self.pose_probe_view:
            self.pose_probe_view = probe
            self.llmChanged.emit()
        self.set_finger_frame(getattr(snap, "finger", None))
        lost = (bool(getattr(snap, "hand_lost", False)), bool(getattr(snap, "arm_lost", False)),
                bool(getattr(snap, "admin_blocked", False)))
        if lost != (getattr(self, "hand_lost", False), getattr(self, "arm_lost", False), getattr(self, "admin_blocked", False)):
            self.hand_lost, self.arm_lost, self.admin_blocked = lost
            self.stateChanged.emit()
        rec_view = getattr(snap, "rec", None)
        self._auto_save_finished_recording(rec_view)
        if rec_view != self.rec_view:
            self.rec_view = rec_view
            self._review_saved_recording(rec_view)
            self.stateChanged.emit()
        palm_view = getattr(snap, "palm_axes", None)
        if palm_view is not None and palm_view != self.palm_axes_view:
            self.palm_axes_view = palm_view
            self.stateChanged.emit()
        if snap.close_sign is not None:
            self.close_state = snap.close_sign
        if snap.motion is not None:
            if self.motion_state[0] and not snap.motion[0]:
                self.motion_exit_until = time.perf_counter() + 1.8       # keep a short "off" notice on screen
            self.motion_state = snap.motion
            badge = self._motion_badge_now()
            if badge != self.motion_badge:
                self.motion_badge = badge
                self.stateChanged.emit()
        gate_status = getattr(snap, "gate_status", None)
        if gate_status != getattr(self, "gate_status", None):
            self.gate_status = gate_status
            self.stateChanged.emit()
        if snap.failures is not None:
            self.set_failures(*snap.failures)
        if snap.recent_line:
            self.add_recent(snap.recent_line)
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
        if key == 32 and self.stage_name == "main" and self.rec_mode in ("guided", "free") and self.tracking_view is None:
            if self.rec_mode == "guided" and self.recWaiting:
                self.recStart()
            else:
                self.toggleRecordingPause()
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
        return self.commands.popleft() if self.commands else -1

    def show(self, frame, detected, paused, action, fps, detect_ms, status="", contacts=()):
        state = (
            "입력 꺼짐" if paused else "연습 모드" if self.dry_run else "입력 활성",
            "손 감지됨" if detected else "손 대기",
            "제어 시작을 누르면 손짓으로 조작할 수 있어요." if paused
            else ACTIONS.get(action, "포인터 이동") if detected else "손을 카메라에 보여주세요.",
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
        self.preview_hand = self._pending_preview_hand
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
