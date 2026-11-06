"""Qt Quick desktop shell. Shares the existing engine loop with the Tk shell."""
from collections import deque
from pathlib import Path
import copy
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
        self.model_version = 0                                  # +1 per newly trained model (runtime reloads the gate)
        from src.training_center import TrainingJob, get_augmentation_settings
        self.training_job = TrainingJob()
        self.training_rows = []                                 # one row per gesture (src/training_center.gesture_rows)
        self.training_files = []                                # per-file quality & selection rows
        self.model_backups = []                                 # restorable model snapshots & exports
        self.sim_result = {}                                    # latest simulator run result (lightweight header)
        self._sim_playback_frames = []                          # per-frame skeleton & event data kept in Python
        self.sim_eval = {}                                      # latest simulator data quality & calibration evaluation
        self.aug_settings = get_augmentation_settings()         # augmentation enabled / strength / factor settings
        self.diag_result = {}                                   # latest app self-diagnostics result
        self.training_info = ("", "")                           # (data line, model line)
        self.training_storage = ("", False)                     # (recordings size in git, past the split limit)
        self.training_busy = ""                                 # "읽는 중…" / "내보내는 중…"
        self.training_note = ""
        self._training_was_running = False
        self.training_timer = QTimer()
        self.training_timer.setInterval(400)
        self.training_timer.timeout.connect(self._training_tick)
        from src.gesture_gate import DEFAULT_MODEL
        self.tcn_gate = DEFAULT_MODEL.exists()                  # intent check by the trained model: on when one exists
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
        if mode != "off":
            self.last_rec_review = None
        self.rec_mode = mode
        self.rec_focus = ""
        self.rec_plan = None
        self.rec_paused = False
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
    @Slot()
    def openTraining(self):
        self.refreshTraining()
        self.training_timer.start()

    @Slot()
    def closeTraining(self):
        if not self.training_job.running:
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
                rows = tc.gesture_rows(summary, report)
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
                model = (f"모델: {report.get('trained')} 학습 · 녹화 {len(report.get('data', []))}개로" if report
                         else "모델: 아직 없음 (학습하세요)")

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
                self.training_files = file_rows
                self.model_backups = tc.list_model_backups()
                self.aug_settings = tc.get_augmentation_settings()
                if file_rows and not self.sim_eval.get("file"):
                    try:
                        self.sim_eval = tc.evaluate_recording_in_simulator(file_rows[0]["file"])
                    except Exception:
                        pass
                self._training_summary = summary
                self.training_storage = tc.storage_text(summary)
            except Exception as exc:
                self.training_note = f"읽지 못했어요: {exc}"
            self.training_busy = ""

        import threading
        threading.Thread(target=work, daemon=True).start()
        self.training_timer.start()

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
        self.training_busy = "전체 녹화 품질 검사 중…"
        self.trainingChanged.emit()

        def work():
            from src import training_center as tc
            try:
                res = tc.evaluate_all_recordings_quality(force=True, auto_exclude_failed=bool(auto_exclude_failed))
                fails = sum(1 for r in res if r.get("status") == "fail")
                warns = sum(1 for r in res if r.get("status") == "warning")
                oks = sum(1 for r in res if r.get("status") == "pass")
                excl_msg = " (불량 자동 제외됨)" if auto_exclude_failed and fails else ""
                self.training_note = f"품질 전수검사 완료: 양호 {oks}개 · 주의 {warns}개 · 불량 {fails}개{excl_msg}"
            except Exception as exc:
                self.training_note = f"품질 검사 실패: {exc}"
            self.training_busy = ""
            self.refreshTraining()

        import threading
        threading.Thread(target=work, daemon=True).start()
        self.training_timer.start()

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
        target = filename
        if not target and self.training_files:
            target = self.training_files[0].get("file", "")
        if not target:
            return
        from src import training_center as tc
        try:
            self.sim_eval = tc.evaluate_recording_in_simulator(target, force_recheck=True)
            self.aug_settings = tc.get_augmentation_settings()
            self.training_note = self.sim_eval.get("headline", "데이터 평가 완료")
        except Exception as exc:
            self.training_note = f"데이터 평가 실패: {exc}"
        self.trainingChanged.emit()

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
        self.training_busy = f"'{target}' 데이터 보정(결측 보간·관절 이상치 스무딩) 중…"
        self.trainingChanged.emit()

        def work():
            from src import training_center as tc
            try:
                res = tc.calibrate_recording_data(
                    target,
                    max_interp_gap=int(max_interp_gap),
                    max_joint_jump=float(max_joint_jump),
                    min_do_frames=int(min_do_frames),
                )
                self.sim_eval = tc.evaluate_recording_in_simulator(target, force_recheck=False)
                self._store_sim_result(tc.simulate_model_on_recording(target, use_cleaned=True))
                self.training_note = res.get("message", "데이터 보정 완료")
            except Exception as exc:
                self.training_note = f"데이터 보정 실패: {exc}"
            self.training_busy = ""
            self.refreshTraining()

        import threading
        threading.Thread(target=work, daemon=True).start()
        self.training_timer.start()

    @Slot(str)
    def resetCalibratedSimRecording(self, filename=""):
        if self.training_busy:
            return
        target = filename
        if not target and self.training_files:
            target = self.training_files[0].get("file", "")
        if not target:
            return
        from src import training_center as tc
        try:
            res = tc.reset_calibrated_recording(target)
            self.sim_eval = tc.evaluate_recording_in_simulator(target, force_recheck=False)
            self._store_sim_result(tc.simulate_model_on_recording(target, use_cleaned=False))
            self.training_note = res.get("message", "보정 초기화 완료")
            self.refreshTraining()
        except Exception as exc:
            self.training_note = f"보정 초기화 실패: {exc}"
            self.trainingChanged.emit()

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
        self.training_busy = f"{label_str} 데이터 증강 적용 중…"
        self.trainingChanged.emit()

        def work():
            from src import training_center as tc
            try:
                res = tc.apply_recording_augmentation(
                    target,
                    enabled=bool(enabled),
                    strength=str(strength),
                    factor=int(factor),
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
            self.training_busy = ""
            self.trainingChanged.emit()

        import threading
        threading.Thread(target=work, daemon=True).start()
        self.training_timer.start()

    @Slot(str)
    @Slot(str, bool)
    def runSimulation(self, filename="", use_cleaned=True):
        if self.training_busy:
            return
        target = filename
        if not target and self.training_files:
            target = self.training_files[0].get("file", "")
        if not target:
            self.training_note = "시뮬레이션할 녹화 파일이 없습니다."
            self.trainingChanged.emit()
            return
        self.training_busy = f"'{target}' 시뮬레이션 중…"
        self.trainingChanged.emit()

        def work():
            from src import training_center as tc
            try:
                res = tc.simulate_model_on_recording(target, use_cleaned=bool(use_cleaned))
                self._store_sim_result(res)
                self.sim_eval = res.get("quality") or tc.evaluate_recording_in_simulator(target)
                self.aug_settings = tc.get_augmentation_settings()
                self.training_note = res.get("summary", "시뮬레이션 완료")
            except Exception as exc:
                self.training_note = f"시뮬레이션 실패: {exc}"
            self.training_busy = ""

        import threading
        threading.Thread(target=work, daemon=True).start()
        self.training_timer.start()

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
        self.training_busy = "앱 및 데이터셋 자가 진단 중…"
        self.trainingChanged.emit()

        def work():
            from src import training_center as tc
            try:
                self.diag_result = tc.run_app_diagnostics()
                self.training_note = self.diag_result.get("summary", "자가 진단 완료")
            except Exception as exc:
                self.training_note = f"자가 진단 오류: {exc}"
            self.training_busy = ""

        import threading
        threading.Thread(target=work, daemon=True).start()
        self.training_timer.start()

    @Slot()
    def startTraining(self):
        if self.training_job.start():
            self.training_note = ""
            self._training_was_running = True
            self.training_timer.start()
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
            self.trainingChanged.emit()

    @Slot()
    def stopTraining(self):
        self.training_job.stop()

    @Slot()
    def exportModel(self):
        if self.training_busy:
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

    @Slot()
    def pushToHub(self):
        if self.training_busy:
            return
        self.training_busy = "Hugging Face 허브로 녹화·모델 업로드 중…"
        self.trainingChanged.emit()

        def work():
            from tools import hub as hf_hub
            from src.training_center import MODEL, REC_DIR
            try:
                hf_hub.load_dotenv()
                repo = os.environ.get(hf_hub.ENV_REPO, hf_hub.DEFAULT_REPO)
                token = os.environ.get(hf_hub.ENV_TOKEN, "")
                client = hf_hub.HfDatasetHub(repo, token)
                models = [MODEL] if MODEL.is_file() and MODEL.with_suffix(".json").is_file() else []
                res = hf_hub.push_to_hub(client, REC_DIR, models=models)
                self.training_note = (
                    f"허브 업로드 완료 ({repo}): 새 녹화 {res['new_recordings']}개, "
                    f"모델 {res['models']}개 (기존 {res['already_on_hub']}개)"
                )
            except Exception as exc:
                self.training_note = f"허브 업로드 실패: {exc}"
            self.training_busy = ""

        import threading
        threading.Thread(target=work, daemon=True).start()
        self.training_timer.start()

    @Slot()
    def pullFromHub(self):
        if self.training_busy:
            return
        self.training_busy = "Hugging Face 허브에서 팀 데이터 내려받는 중…"
        self.trainingChanged.emit()

        def work():
            from tools import hub as hf_hub
            from src.training_center import REC_DIR
            try:
                hf_hub.load_dotenv()
                repo = os.environ.get(hf_hub.ENV_REPO, hf_hub.DEFAULT_REPO)
                token = os.environ.get(hf_hub.ENV_TOKEN, "")
                client = hf_hub.HfDatasetHub(repo, token)
                res = hf_hub.pull_from_hub(client, REC_DIR, include_models=True)
                from src.training_center import run_post_recording_pipeline
                for rec_file in sorted(REC_DIR.rglob("*.jsonl.gz")):
                    if "cleaned" in rec_file.parts or "augmented" in rec_file.parts:
                        continue
                    if rec_file.parent != REC_DIR and not (REC_DIR / rec_file.name).exists():
                        import shutil
                        shutil.copy2(rec_file, REC_DIR / rec_file.name)
                        rec_file = REC_DIR / rec_file.name
                    if not (REC_DIR / "cleaned" / rec_file.name).exists():
                        run_post_recording_pipeline(
                            rec_file,
                            {"auto_quality_check": True, "auto_clean": True, "auto_offline_augment": True},
                        )
                self.training_note = (
                    f"허브 동기화·정제·증강 완료 ({repo}): 새 녹화 {res['downloaded']}개, "
                    f"모델 {res['models_downloaded']}개 내려받음"
                )
                self.refreshTraining()
            except Exception as exc:
                self.training_note = f"허브 동기화 실패: {exc}"
            self.training_busy = ""

        import threading
        threading.Thread(target=work, daemon=True).start()
        self.training_timer.start()

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

    @Property(bool, notify=stateChanged)
    def tcnGate(self):
        return bool(self.tcn_gate)

    @Property(bool, notify=stateChanged)
    def tcnAvailable(self):
        from src.gesture_gate import DEFAULT_MODEL
        return DEFAULT_MODEL.exists()

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
            rec_plan=copy.deepcopy(self.rec_plan), rec_paused=self.rec_paused,
            rec_discard=self.rec_discard,
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
            if isinstance(rec_view, dict) and rec_view.get("mode") == "off" and rec_view.get("file"):
                rec_file = rec_view.get("file")
                if rec_file != getattr(self, "_last_handled_rec_file", None):
                    self._last_handled_rec_file = rec_file
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
