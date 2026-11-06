"""Qt Quick desktop shell. Shares the existing engine loop with the Tk shell."""
from collections import deque
from pathlib import Path
import copy
import json
import time
import math

import cv2
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Property, QSettings, QTimer, Signal, Slot, QUrl
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
    hiddenToTray = Signal()           # the window was closed into the tray
    trackingActiveChanged = Signal()  # only when the test window opens or closes
    trainingChanged = Signal()        # training center: data, scores, training job, export
    trainingStarted = Signal()        # navigate to progress only after the process really started

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
        self._training_summary = {}
        from src.recording_plan_editor import RecordingPlanEditor
        self.recording_editor = RecordingPlanEditor()
        self.recording_editor.startRequested.connect(self._start_focused_plan)
        self.model_version = 0                                  # +1 per newly trained model (runtime reloads the gate)
        from src.training_center import TrainingJob
        self.training_job = TrainingJob()
        self.training_rows = []                                 # one row per gesture (src/training_center.gesture_rows)
        self.training_selected = set()                          # model labels; shared-pose tasks are linked
        self.training_info = ("", "")                           # (data line, model line)
        self.training_storage = ("", False)                     # (recordings size in git, past the split limit)
        self.training_busy = ""                                 # "읽는 중…" / "내보내는 중…"
        self.training_note = ""
        self._training_was_running = False
        self._training_center_open = False
        self._training_progress_open = False
        self.training_timer = QTimer()
        self.training_timer.setInterval(400)
        self.training_timer.timeout.connect(self._training_tick)
        from src.gesture_gate import DEFAULT_MODEL
        from src.tcn_models import available
        self.tcn_gate = available(DEFAULT_MODEL)
        try:
            self.tcn_policies = json.loads(self.preferences.value("tcnPolicies", "{}", type=str))
            if not isinstance(self.tcn_policies, dict):
                self.tcn_policies = {}
        except (ValueError, TypeError):
            self.tcn_policies = {}
        self._training_report = {}
        self.palm_axes_view = (False, "", 0.0, "", False)       # running, prompt, progress, note, saved
        self.motion_state = (False, 0.0, "", 0.0, 0.0)           # (on, seconds left, last command, entering, leaving)
        self.motion_exit_until = 0.0                             # the "motion mode off" notice stays until then
        self.toggle_progress = 0.0                               # progress of the on/off sign hold
        self.close_state = (0.0, 0.0)                            # (hold progress 0..1, seconds left to confirm) of the close-window sign
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
        self.rec_mode = mode
        self.rec_focus = ""
        self.rec_plan = None
        self.rec_paused = False
        self.stateChanged.emit()

    @Property(str, notify=stateChanged)
    def recFocus(self):
        return self.rec_focus

    # ------------------------------------------------------------------ training center (src/training_center.py)
    @Slot()
    def openTraining(self):
        self._training_center_open = True
        self.refreshTraining()
        self.training_timer.start()

    @Slot()
    def closeTraining(self):
        self._training_center_open = False
        self._sync_training_timer()

    @Slot()
    def openTrainingProgress(self):
        self._training_progress_open = True
        self.training_timer.start()
        self.trainingChanged.emit()

    @Slot()
    def closeTrainingProgress(self):
        self._training_progress_open = False
        self._sync_training_timer()

    def _sync_training_timer(self):
        if self._training_center_open or self._training_progress_open or self.training_job.running or self.training_busy:
            self.training_timer.start()
        else:
            self.training_timer.stop()

    @Slot()
    def refreshTraining(self):
        """Re-read recordings and the model's scores in the background (a new recording takes a few seconds)."""
        if self.training_busy:
            return
        self.training_busy = "녹화 읽는 중…"
        self.trainingChanged.emit()

        def work():
            from src import training_center as tc
            try:
                summary = tc.dataset_summary()
                report = tc.model_report()
                self._training_report = report or {}
                rows = tc.gesture_rows(summary, report)
                files = [f for f in summary["files"] if "error" not in f]
                frames = sum(f.get("frames", 0) for f in files)
                minutes = sum(f.get("seconds", 0) for f in files) / 60
                valid = sum(f.get("valid_frames", 0) for f in files)
                data = f"녹화 {len(files)}개 · {minutes:.0f}분 · 3D 관절 {100 * valid / max(1, frames):.0f}%"
                waiting = len(summary["files"]) - len(files)
                if waiting:
                    data += f" · 다음 학습에 포함 {waiting}개 (끝나지 않았거나 받는 중)"
                model = (f"모델: {len(report['classes']) - 1}종 학습됨 · 선택 학습 {len(report.get('selected_classes', []))}종" if report
                         else "모델: 아직 없음 (학습하세요)")
                self.training_rows, self.training_info = rows, (data, model)
                self._training_summary = summary
                self.training_storage = tc.storage_text(summary)
            except Exception as exc:
                self.training_note = f"읽지 못했어요: {exc}"
            self.training_busy = ""

        import threading
        threading.Thread(target=work, daemon=True).start()
        self.training_timer.start()

    @Slot()
    def startTraining(self):
        if self.training_busy or self.rec_mode != "off" or self.training_job.running:
            return
        from src.gesture_catalog import BY_KEY
        tasks = [key for key, task in BY_KEY.items() if task.label in self.training_selected]
        try:
            if not tasks:
                raise ValueError("학습할 동작을 체크하세요. 중립·휴식 구간은 자동으로 포함됩니다.")
            if not self.training_job.start(tasks=tasks):
                return
            self.training_note = "선택한 동작만 학습 중입니다. 다른 동작의 기존 학습 결과는 유지됩니다."
            self._training_was_running = True
            self.training_timer.start()
            self.trainingStarted.emit()
        except (ValueError, OSError) as exc:
            self.training_note = str(exc)
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
        if self.training_job.start(gestures=[key]):
            self.training_note = task.name + " 선택 학습을 시작합니다."
            self._training_was_running = True
            self.training_timer.start()
            self.trainingStarted.emit()
            self.trainingChanged.emit()

    @Slot()
    def stopTraining(self):
        self.training_job.stop()
        self.trainingChanged.emit()

    @Slot()
    def exportModel(self):
        if self.training_busy or self.training_job.running:
            return
        self.training_busy = "내보내는 중… (녹화 전체를 데이터셋으로 묶어요)"
        self.trainingChanged.emit()

        def work():
            from src import training_center as tc
            try:
                out = tc.export_package()
                self.training_note = f"내보냈어요: exports/{out.name}.zip"
                self._export_dir = str(out.parent)
            except Exception as exc:
                self.training_note = f"내보내지 못했어요: {exc}"
            self.training_busy = ""

        import threading
        threading.Thread(target=work, daemon=True).start()
        self.training_timer.start()

    @Slot()
    def openExports(self):
        import os
        from src.training_center import EXPORT_DIR
        EXPORT_DIR.mkdir(exist_ok=True)
        os.startfile(str(EXPORT_DIR))  # noqa: S606 - Windows explorer on our own folder

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
        self.stateChanged.emit()

    def _training_tick(self):
        job = self.training_job
        if self._training_was_running and not job.running:
            self._training_was_running = False
            s = job.snapshot()
            if s.get("error"):
                self.training_note = s["error"]
            elif s.get("success"):
                if s.get("published", True):
                    self.training_note = "선택 동작 학습 완료 · 새 모델은 시험 모드입니다. 동작별 비교 결과를 보고 TCN 적용을 선택하세요."
                    self.model_version += 1
                    self.stateChanged.emit()
                else:
                    self.training_note = "구분 성능 기준 미달로 기존 모델을 유지합니다. " + " / ".join(s.get("publication_issues", []))
            elif s.get("cancelled"):
                self.training_note = "학습을 멈췄어요. 기존 모델을 유지합니다."
            self.refreshTraining()
        self.trainingChanged.emit()
        self._sync_training_timer()

    @Property("QVariantList", notify=trainingChanged)
    def trainingRows(self):
        category = getattr(self, "training_filter", "전체")
        from src.tcn_policy import mode_for, MODES
        return [dict(r, selected=r["label"] in self.training_selected,
                     tcn_mode=MODES.index(mode_for(r["key"], getattr(self, "_training_report", {}),
                                                  getattr(self, "tcn_policies", {}))))
                for r in self.training_rows if category == "전체" or r["category"] == category]

    @Slot(str, int)
    def setTaskTcnMode(self, task, index):
        from src.tcn_policy import MODES, policy_entry
        if self.training_job.running or self.training_busy or not 0 <= index < len(MODES):
            return
        try:
            self.tcn_policies[task] = policy_entry(task, MODES[index], self._training_report)
            self.preferences.setValue("tcnPolicies", json.dumps(self.tcn_policies))
            self.preferences.sync()
            self.model_version += 1  # discard any pending action when policy changes
            self.stateChanged.emit()
            self.trainingChanged.emit()
        except ValueError as exc:
            self.training_note = str(exc)
            self.trainingChanged.emit()

    @Slot(str, bool)
    def selectTrainingTask(self, key, selected):
        if self.training_job.running or self.training_busy:
            return
        row = next((r for r in self.training_rows if r["key"] == key), None)
        if row is None or row["free"] or (selected and not row["can_train"]):
            return
        if selected:
            self.training_selected.add(row["label"])
        else:
            self.training_selected.discard(row["label"])
        self.trainingChanged.emit()

    @Slot(bool)
    def selectAllTraining(self, selected):
        if self.training_job.running or self.training_busy:
            return
        # Category filters only change visibility; selection and the count are global.
        self.training_selected = {r["label"] for r in self.training_rows if r["can_train"]} if selected else set()
        self.trainingChanged.emit()

    @Property(int, notify=trainingChanged)
    def trainingSelectedCount(self):
        return len(self.training_selected)

    @Property(str, notify=trainingChanged)
    def trainingSelectionText(self):
        from src.gesture_catalog import NAMES
        if not self.training_selected:
            return "학습할 동작을 체크하세요. 선택하지 않은 동작의 기존 학습 결과는 유지됩니다."
        names = [name for key, name in NAMES.items() if key in self.training_selected]
        return f"선택 {len(self.training_selected)}종: " + " · ".join(names)

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

    @Property("QVariantList", notify=trainingChanged)
    def trainingLosses(self):
        return list(self.training_job.snapshot().get("losses", []))[-60:]

    @Property("QVariantMap", notify=trainingChanged)
    def trainingDetails(self):
        from src.training_progress import progress_details
        return progress_details(self.training_job.snapshot())

    @Property(str, notify=trainingChanged)
    def trainingProgressSummary(self):
        details = self.trainingDetails
        if details["status"] == "idle":
            return "진행 페이지에서 학습 단계·남은 시간·완료 예상 시각을 확인할 수 있습니다."
        return f"{details['percent']}% · 남은 시간 {details['remaining']} · 완료 예상 {details['finish']}"

    @Property(bool, notify=stateChanged)
    def tcnGate(self):
        return bool(self.tcn_gate)

    @Property(bool, notify=stateChanged)
    def tcnAvailable(self):
        from src.gesture_gate import DEFAULT_MODEL
        from src.tcn_models import available
        return available(DEFAULT_MODEL)

    @Slot(bool)
    def setTcnGate(self, on):
        self.tcn_gate = bool(on)
        self.stateChanged.emit()

    @Slot()
    def recStart(self):
        self.rec_advance += 1
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
            palm_axes_request=self.palm_axes_requests[0], palm_axes_cancel=self.palm_axes_requests[1],
            palm_axes_reset=self.palm_axes_requests[2],
            rec_mode=self.rec_mode, rec_advance=self.rec_advance, tcn_gate=self.tcn_gate,
            rec_focus=self.rec_focus, model_version=self.model_version,
            tcn_policies=copy.deepcopy(self.tcn_policies),
            rec_plan=copy.deepcopy(self.rec_plan), rec_paused=self.rec_paused,
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
        self.toggle_progress = getattr(snap, "toggle_progress", 0.0)
        rec_view = getattr(snap, "rec", None)
        if rec_view != self.rec_view:
            self.rec_view = rec_view
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
        for root in self.engine.rootObjects():
            root.close()
            root.deleteLater()
        self.application.processEvents()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
