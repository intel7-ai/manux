"""
Qt mode runtime: camera -> hand tracking -> pointer engine -> OS mouse on its own thread.

Before (2026-11-04) the Qt dashboard ran this inline on the UI thread, so a slow UI frame
delayed the cursor and vice versa, and a tray-only app had nothing to run on. Now:

- this thread owns the camera, MediaPipe, the pointer engine, the mouse, the hand-area crop,
  failure recording and the tracking test, and never touches Qt;
- the Qt thread hands over a `UiState` (what the user chose) and commands, and renders the
  newest `Snapshot` (preview frame, status, metrics, tracking-test view).

The same split lets a tray / overlay front end drive it without the dashboard window.
"""
from __future__ import annotations

import json
import math
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2

from src.action_bindings import CompositeGestureDetector, DEFAULT_GESTURE_BINDINGS, get_action_registry
from src.failure_log import FailureRecorder
from src.finger_kinematics import CausalFingerTracker
from src.close_sign import CloseSign
from src.toggle_sign import ToggleSign
from src.motion_mode import ACTION_LABELS, MotionMode
from src.palm_axes import PalmAxesCalibration, valid_axes
from src.elbow_pivot import ElbowPivot
from src.pose_tracker import DEFAULT_POSE_MODEL, PoseTracker, pick_arm
from src.guided_recording import GuidedSession, tasks_for
from src.recorder import SessionRecorder
from src.gesture_gate import DEFAULT_MODEL as GATE_MODEL, GestureGate, load_predictor
from src.click_check import ClickCheck
from src.click_log import ClickLog
from src.gesture_engine import GestureEngine, GestureResult
from src.hand_crop import HandCrop
from src.crop_recorder import CropRecorder, crops_path
from src.roi_tracker import RoiTracker, hand_box
from src.hand_selection import HandFollower, selected_detection
from src.virtual_hand import hand_px
from src.virtual_hand import (ACTION_NAMES as HAND_ACTION_NAMES, BoxMapper, DEFAULT_BOX, Grip, HandSize, PINCH_HOLD, Ratchet, ShapePinch, ShapeSwitch, ShapeTriple, ViewSmoother, triple_point,
                              click_offset, holds_still, pinch_point, VIRTUAL_OFF_ACTIONS, virtual_guides,
                              palm_centre, valid_box, view as virtual_hand_view)
from src.online.collector import OnlineCollector
from src.finger_overlay import overlay_frame, preview_hand
from src.pose_probe import PoseProbe, hand_posture
from src.macro import cancel_all as cancel_macros
from src.sequence import SequenceDetector, SequenceStore
from src.path_gesture import PathDetector, PathStore
from src.draw_mode import DrawMode, ShapeStore, ensure_bindings as ensure_draw_bindings

VH_ANCHOR_FOLLOW = .15   # share of the way the drawn hand's anchor follows its thumb-index point each frame (manux)
from src.perf_log import foreground_window_title
from src.online.commands import CommandGate, binding_key, command_of, command_text, forget_command
from src.online.pose_memory import POSE_FILE, PoseMemory, PoseRecognizer, pose_features
from src.online.registration import USER_SOURCE, RegistrationConfig, RegistrationError, RegistrationSession
from src.online.samples import DEFAULT_ROOT as ONLINE_DEFAULT_ROOT, SampleStore
from src.recorder import machine_info
from src.hand_tracker import HandLandmark, HandTracker, pinch_guides
from src.pointer_engine import PointerEngine, PointerSettings
from src.pointer_workspace import FINGERTIPS
from src.tracking_test import TrackingTest, result_line

ONE_SHOT = ("left_click", "right_click", "drag_toggle", "alt_tab", "show_desktop", "task_switcher")


def _online_gate_class():
    try:
        from src.online.safety import OnlineGate
        return OnlineGate
    except Exception:          # no torch: there is no learned-model checker to tell apart
        return type(None)

FREEZE_SECONDS = .2     # a flicker of the recognition must not let the cursor jump

DOUBLE_TAP = .7     # seconds after a pinch click in which the next thumb + index press is the second click
CLICK_CHECK_THRESHOLD = .3   # TCN thumb_index peak over a press needed for a click. Held-out replay (16 recordings the TCN
# never saw, 2026-11-10): .3 keeps 93 % of thumb + index clicks and cuts rest-time closes only 8.5 -> 6.8 a minute, because most of
# those are real pinches right after a thumb + index task (labelled rest). Too little to pay 7 % of the clicks for: off by default
# ("virtual_click_check": true in config/gestures.json turns it on).
CLICK_NAMES = {"left": "좌클릭", "double": "더블클릭", "drag": "끌기", "right": "우클릭"}
PASSIVE_ACTIONS = frozenset({"none", "move_cursor", "pointer_wait"})   # the engine is doing nothing a hand shape would undo
VH_LOST_KEEP = .4     # seconds the drawn hand stays (faded) after the camera loses the hand


@dataclass(frozen=True)
class UiState:
    """What the UI thread decided (replaced as a whole, read once per frame)."""
    stage: str = "splash"               # splash | welcome | capture | main
    control_hand: str = ""              # "Left" | "Right" | ""
    use_index_pointer: bool = True
    click_style: str = "pinch"
    anchor: str = "index"
    anchor_joints: Tuple[int, ...] = ()  # landmarks the cursor follows (palm mode / "직접 지정"); () = config default
    hand_crop: bool = False
    axes: Optional[Tuple[float, float, float, float]] = None
    calibrating: bool = False
    sensitivity: int = 100              # percent
    show_fingertips: bool = False
    profile: Optional[str] = None       # calibration profile the user picked (None = no profile)
    motion_request: int = 0             # +1 each time the user presses the motion-mode button
    palm_axes_request: int = 0          # +1 per press of "cursor direction" (palm / fist cursor): start the wizard
    palm_axes_cancel: int = 0           # +1 per press of its cancel button
    palm_axes_reset: int = 0            # +1 per press of its reset button
    rec_mode: str = "off"               # dataset recording (src/recorder.py): "off" | "guided" | "free"
    rec_advance: int = 0                # +1 per press of "start" in guided recording
    tcn_gate: bool = False              # right click / scroll grab confirmed by the trained model (src/gesture_gate.py)
    gate_model: str = "tcn"             # "tcn" = models/gesture_tcn.onnx, "online" = the model learned from this person
    virtual_hand: bool = False          # the hand on the screen: a box on the camera image maps to the monitor (src/virtual_hand.py)
    virtual_box: Tuple[float, float, float, float] = DEFAULT_BOX
    screen_ratio: float = 1.0           # physical / logical pixels of the main screen (the drawn hand is in logical ones)
    rec_focus: str = ""                 # guided recording of one gesture only (training center "집중 녹화"); "" = all
    model_version: int = 0              # +1 when the training center saved a new model: reload the gate
    rec_plan: Optional[Dict[str, Any]] = None
    rec_paused: bool = False
    rec_discard: int = 0                # +1 per press of "녹화 취소 (버리기)"


@dataclass
class Snapshot:
    seq: int
    frame: Any                          # annotated BGR frame for the preview
    detected: bool
    paused: bool
    action: str
    fps: float
    detect_ms: float
    status: str
    contacts: Tuple[int, ...] = ()
    tip: Optional[Tuple[float, float]] = None   # index fingertip (direction calibration)
    aspect: float = 1.0
    source: Any = 0
    mirrored: bool = True
    detections: List[Any] = field(default_factory=list)  # all hands (hand setup screen)
    metrics: Optional[Tuple[str, int, bool]] = None      # camera mode, brightness, dark
    tracking: Optional[Dict[str, Any]] = None             # tracking test view (None = closed)
    failures: Optional[Tuple[str, str]] = None            # summary, last file (when changed)
    recent_line: Optional[str] = None                     # new tracking result line
    message: str = ""                                      # one-off console message
    toggle_progress: float = 0.0                          # progress 0..1 of the on/off sign hold
    close_sign: Optional[Tuple[float, float, str]] = None  # (progress 0..1 of the close-window hold, seconds left to confirm, notice)
    motion: Optional[Tuple[bool, float, str, float, float]] = None  # on, seconds left (0 = no timeout), last command, entering 0..1, leaving 0..1
    palm_axes: Tuple[bool, str, float, str, bool] = (False, "", 0.0, "", False)  # wizard running, prompt, progress, note, correction saved
    rec: Optional[Dict[str, Any]] = None  # dataset recording view: mode, prompt, sub, progress, overall, waiting, frames, file
    gate_status: Optional[Dict[str, Any]] = None  # Stage 7: calibrated confidence & rejection reason from GestureGate
    registration: Optional[Dict[str, Any]] = None  # adding a new gesture: state, demo, seconds, report (src/online/registration.py)
    online_gesture: str = ""              # a registered gesture recognised right now (shown on the preview, sends nothing)
    virtual_hand: Optional[Dict[str, Any]] = None  # the hand drawn at the cursor (src/virtual_hand.py), None = no hand
    preview_hand: Optional[Dict[str, Any]] = None  # skeleton drawn over the preview by src/HandSkeleton.qml (src/finger_overlay.py)
    finger: Optional[Dict[str, Any]] = None        # fingertips drawn next to the cursor by src/FingerTracker.qml
    pose_probe: Optional[Dict[str, Any]] = None    # [손 모양 보여주기] countdown / holding / result (src/pose_probe.py)
    draw: Optional[Dict[str, Any]] = None          # drawing mode: pen, stroke, last recognised shape (src/draw_mode.py)
    hand_lost: bool = False               # no hand for `hand_lost_alert` seconds: the screen shows it big
    arm_lost: bool = False                # Pose has not seen the arm for `arm_lost_alert` seconds (only when Pose runs)
    admin_blocked: bool = False           # an admin window (Task Manager) is in front and this app is not admin: input is dropped


def tracking_view(test: TrackingTest, cursor) -> Dict[str, Any]:
    """Everything the test window shows, as plain data (no shared mutable object)."""
    t = test.current
    return {
        "done": test.done,
        "paused": test.paused,
        "index": test.index,
        "total": len(test.targets),
        "target": None if t is None else (t.fx, t.fy, t.size_px / test.monitor.width),
        "holding": t is not None and t.inside_since is not None,
        "hand_present": test.hand_present,
        "cursor": test.cursor_fraction(cursor),
        "summary": test.summary_text() if test.done else "",
    }


def ui_style(ui) -> str:
    return getattr(ui, "click_style", "")


def ui_gate_on(rt) -> bool:
    return bool(getattr(rt.ui, "tcn_gate", False))


class QtRuntime:
    def __init__(self, *, camera, tracker: HandTracker, engine: GestureEngine, mouse, perf_log,
                 config_path=None, dry_run: bool = False, max_frames: int = 0,
                 draw_cursor_anchor: Callable = None, draw_recording: Callable = None,
                 log_dir: Optional[Path] = None, online_root: Optional[Path] = None) -> None:
        self.camera, self.tracker, self.engine, self.mouse = camera, tracker, engine, mouse
        self.online: Optional[OnlineCollector] = None     # config "online_learning_enabled" (docs/online-learning-design.md)
        self._online_root = online_root
        self._online_failed = False
        self.online_memory: Optional[PoseMemory] = None
        self.recognizer: Optional[PoseRecognizer] = None
        self.registration: Optional[RegistrationSession] = None
        self.registration_config: Optional[RegistrationConfig] = None
        self._registration_view: Optional[Dict[str, Any]] = None
        self._online_gesture = ""
        self._online_gesture_until = 0.0
        self._virtual_hand: Optional[Dict[str, Any]] = None
        self.box_mapper = BoxMapper()
        self.view_smoother = ViewSmoother()
        self.hand_size = HandSize()      # the drawn hand's scale, steady when the hand turns
        self._vh_click: Optional[List[float]] = None   # pinch point on the drawn hand, frozen while the fingers touch
        self._vh_engaged = False
        # thumb-index point of the drawn hand (logical px from the palm): the drawing is shifted so it sits on the cursor.
        # Follows slowly, frozen while clicking or gripping (manux-merged-20261110 VirtualHandFeature._anchor).
        self._vh_anchor: Optional[List[float]] = None
        self.shape_pinch = ShapePinch("index")         # thumb + index = left click / drag, from the hand shape only
        self.shape_right = ShapeTriple()               # thumb + index + middle together = right click, from the hand shape only
        self._pinch_triple = False                     # this thumb + index press became the three-finger right click
        self.click_check: Optional[ClickCheck] = None  # the TCN's second look at thumb + index clicks (loaded on first use)
        self.click_log: Optional[ClickLog] = None      # everyday virtual hand clicks + F "not a click" marks (logs/virtual_clicks)
        self._triple_open_t = -1e9                     # when the three fingers last came apart
        self.shape_switch = ShapeSwitch()              # thumb folded, four fingers up = task switcher, from the hand shape only
        self.grip = Grip()                             # what the virtual hand holds: a file (drag) or the page (scroll)
        self.ratchet = Ratchet()                       # a fist scrolls one way: bringing the hand back is not a scroll
        self._pinch_since: Optional[float] = None      # thumb + index closed since (shape pinch)
        self._pinch_drag = False
        self._pinch_double = False                     # this press is a double click's second click
        self._pinch_rejected = False                   # the click check dropped this press while it was held
        self._last_tap: Optional[Tuple[float, Tuple[int, int]]] = None   # (time, screen point) of the last pinch click
        self._vh_last: Optional[Dict[str, Any]] = None  # the last drawn hand, kept a moment when the camera loses the hand
        self._vh_lost: Optional[float] = None
        self.command_gate = CommandGate()
        self._online_model_version = 0
        self._freeze_until = 0.0
        self.perf_log = perf_log
        self.dry_run = dry_run
        self.max_frames = int(max_frames)
        self._draw_cursor_anchor = draw_cursor_anchor
        self._draw_recording = draw_recording
        # True: the hand skeleton and cursor anchor are drawn by QML over the preview instead of into the camera frame
        self.qml_skeleton = True
        self.pose_probe = PoseProbe()      # "this hand shape" for the AI binding helper (src/llm_binding.py)
        self.draw_store = ShapeStore()
        self.seq_store = SequenceStore()       # gesture sequences: shapes in order run a command (src/sequence.py)
        self.path_store = PathStore()          # shape-drawing gestures: e.g. a fist drawing a circle (src/path_gesture.py)
        self.paths = PathDetector()
        self.sequences = SequenceDetector()
        self.draw = DrawMode(self.draw_store)   # drawing mode: shapes drawn on the monitor run commands
        try:
            ensure_draw_bindings(get_action_registry(), self.draw_store)
        except Exception:
            pass
        self._preview_hand: Optional[Dict[str, Any]] = None
        self._finger: Optional[Dict[str, Any]] = None
        self.log_dir = log_dir or Path(__file__).resolve().parents[1] / "logs"

        self.pointer = PointerEngine(GestureEngine(config_path=config_path))
        # crop_min_px: how small the crop around a far hand may get (the hand then fills more of what MediaPipe sees)
        self.hand_crop = HandCrop(min_px=int(engine.config.get("crop_min_px", 96)),
                                  scale=float(engine.config.get("crop_scale", 2.4)),
                                  search_seconds=float(engine.config.get("crop_search_seconds", 0.0)),
                                  collapse_ratio=float(engine.config.get("crop_collapse_ratio", 0.0)))
        self._search_frames = 0
        # while the detector has lost the hand, a video tracker keeps the crop on it (src/roi_tracker.py)
        self.roi_tracker = RoiTracker(str(engine.config.get("crop_tracker", "off")),
                                      float(engine.config.get("crop_track_seconds", 3.0)))
        self.failures = FailureRecorder(self.log_dir / "failures")
        self.test: Optional[TrackingTest] = None

        self.ui = UiState()
        self.paused = True
        self.frames = 0
        self.error: Optional[BaseException] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._commands: "queue.Queue[tuple]" = queue.Queue()
        self._cond = threading.Condition()
        self._snap: Optional[Snapshot] = None
        self._seen = 0

        self._had_hand = False
        self._det_path = ""
        self._hand_follower = HandFollower()
        self._applied_profile = engine.profile_name
        self.motion = MotionMode(engine.config)
        self.close_sign = CloseSign(engine.config)
        self.toggle_sign = ToggleSign(engine.config)
        self.finger_tracker = CausalFingerTracker()
        self.composite_detector = CompositeGestureDetector(get_action_registry())
        self._guided_rep_token = None
        self._close_notice, self._close_notice_until = "", 0.0
        self._motion_seen = 0
        self._motion_last = ""
        self._palm_seen = (0, 0, 0)               # palm_axes_request / cancel / reset already handled
        self._palm_cal: Optional[PalmAxesCalibration] = None
        self._palm_note, self._palm_note_until = "", 0.0
        # Elbow pivot (src/elbow_pivot.py): estimated from the palm's arcs, used by the palm / fist cursor when
        # `elbow_pivot` is on. The mouse controller reads it; this thread feeds it.
        self.elbow = ElbowPivot(engine.config)
        mouse.elbow_pivot = self.elbow
        # Arm tracking (src/pose_tracker.py): shoulder / elbow / wrist of the cursor arm, every few frames (palm mode)
        self.pose: Optional[PoseTracker] = None
        if engine.config.get("pose_enabled", False) and DEFAULT_POSE_MODEL.exists():
            try:
                self.pose = PoseTracker()
            except Exception as exc:                # a broken model must not stop the app
                print(f"  [Warn] Pose model not loaded: {exc}")
        self._arm = None
        self._arm_t = -1e9
        self._hand_seen_t = time.perf_counter()
        self._crop_rec: Optional[CropRecorder] = None      # hand pictures of a guided recording (rec_save_crops)
        self._admin_blocked, self._admin_checked_t = False, 0.0
        self._pose_raw: Optional[Tuple[float, list]] = None     # (time, Pose joints 11-16 as x, y, visibility) unsmoothed
        # Dataset recording (src/recorder.py, src/guided_recording.py)
        self.rec_dir = Path(__file__).resolve().parents[1] / "recordings"
        self._rec: Optional[SessionRecorder] = None
        self._guided: Optional[GuidedSession] = None
        self._rec_mode = "off"
        self._rec_adv_seen = 0
        self._rec_note, self._rec_note_until = "", 0.0
        self._last_pipeline_result: Optional[Dict[str, Any]] = None
        self.training_job = None
        # Intent check by the trained gesture model (loaded the first time it is switched on)
        self.gate: Optional[GestureGate] = None
        self._gate_failed = False
        self._elbow_announced = False
        self._was_dragging = False
        self._seen_inertia = None
        self._last_scroll_total = [0, 0]
        self._raw_hands: list = []
        self._last_step_t = 0.0
        self._status, self._status_until = "", 0.0
        self._recent_action, self._recent_until = "", 0.0
        self._last_fling = 0
        self._metrics: Optional[Tuple[str, int, bool]] = None
        self._pending_failures: Optional[Tuple[str, str]] = None
        self._pending_recent: Optional[str] = None
        self._pending_message = ""

    # ------------------------------------------------------------------ any thread
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="qt-runtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    @property
    def finished(self) -> bool:
        return self._stop.is_set() or (self._thread is not None and not self._thread.is_alive())

    def post(self, *command) -> None:
        """("key", code) | ("pause",) | ("tracking_start",) | ("tracking_close",) | ("failure_mark",)"""
        self._commands.put(command)

    def wait_snapshot(self, timeout: float = .03) -> Optional[Snapshot]:
        with self._cond:
            self._cond.wait_for(lambda: self._snap is not None and self._snap.seq != self._seen, timeout)
            if self._snap is None or self._snap.seq == self._seen:
                return None
            self._seen = self._snap.seq
            return self._snap

    # ------------------------------------------------------------------ runtime thread
    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._step()
                if self.max_frames > 0 and self.frames >= self.max_frames:
                    break
        except BaseException as exc:  # surfaced to the UI thread through .error / .finished
            self.error = exc
            raise
        finally:
            self._stop.set()
            try:
                self.mouse.release_all()
            except Exception:
                pass
            if self.online is not None:
                try:
                    self.online.close()
                except Exception:
                    pass
            try:
                self._stop_recording(time.perf_counter())   # quitting mid-recording still leaves a complete file
            except Exception:
                pass
            with self._cond:
                self._cond.notify_all()

    def _say(self, text: str, seconds: float = 2.0) -> None:
        self._status, self._status_until = text, time.perf_counter() + seconds

    def _pause(self) -> None:
        self.paused = True
        cancel_macros()          # a running macro stops between steps (Pause = emergency stop)
        self.mouse.close_switcher(confirm=False)
        self.mouse.release_all()
        self.engine.reset()
        self._recent_until = 0.0

    def _record_negative_candidate(self, now: float, source: str, note: str = "") -> None:
        g_stat = self.gate.status_summary() if self.gate is not None else {}
        recent_act = getattr(self, "_recent_action", "") if now < getattr(self, "_recent_until", 0.0) + 2.0 else None
        self.failures.record_negative_candidate(
            now,
            source=source,
            suspected_action=recent_act,
            suspected_gesture=g_stat.get("top_gesture", ""),
            calibrated_confidence=g_stat.get("calibrated_confidence"),
            rejection_reason=g_stat.get("rejection_reason", ""),
            note=note,
        )

    def _drain(self, now: float) -> None:
        while True:
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                return
            kind = cmd[0]
            if kind == "pause":
                if not self.paused and now < getattr(self, "_recent_until", 0.0) + 1.0:
                    self._record_negative_candidate(now, "user_cancelled", "Paused immediately after action")
                self._pause()
            elif kind == "tracking_start" and self.test is None:
                self.mouse.release_all()
                self.mouse.warp_to(*self.mouse.screens.primary.center)
                self.test = TrackingTest(self.mouse.screens.primary)
            elif kind == "tracking_close":
                self.test = None
                self._pause()
            elif kind == "failure_mark" and self._virtual_click_mark(now):
                pass
            elif kind == "failure_mark":
                if self._rec is not None:
                    self._rec.event(now, "mark")
                self._record_negative_candidate(now, "f_key", "Manual failure mark button")
                if self.online is not None:
                    self.online.capture_recent(now)      # the window then asks what it really was (main.py run_qt)
                if self.failures.trigger(now, "manual"):
                    self._say("실패·오작동 부정 후보 기록 (자동 재학습 미투입)")
            elif kind == "return_block_mark":
                if self._rec is not None:
                    self._rec.event(now, "return_blocked_fp")
                if self.failures.mark_return_block_fp(now, note="Manual return block false-positive mark"):
                    self._say("이동 차단 오탐 기록 (복귀 억제 오작동)")
            elif kind == "rec_discard":
                self._discard_recording(now)
            elif kind == "register_start":
                self._register_start(cmd[1], now)
            elif kind == "register_approve":
                self._register_approve()
            elif kind == "register_cancel":
                self._register_cancel()
            elif kind == "draw_mode":
                self._set_draw(not self.draw.active if cmd[1] is None else bool(cmd[1]))
            elif kind == "seq_reload":
                self.seq_store.load()
                self.sequences.reset()
                self.path_store.load()
                self.paths.reset()
            elif kind == "draw_reload":
                self.draw_store.load()
                try:
                    ensure_draw_bindings(get_action_registry(), self.draw_store)
                except Exception:
                    pass
            elif kind == "pose_probe":
                self.pose_probe.start(now)
            elif kind == "pose_probe_cancel":
                self.pose_probe.cancel()
            elif kind == "gesture_delete":
                self._gesture_delete(cmd[1])
            elif kind == "online_reload":
                self._online_reload()
            elif kind == "key":
                self._key(cmd[1], now)

    def _key(self, key: int, now: float) -> None:
        engine, mouse, camera = self.engine, self.mouse, self.camera
        if self.registration is not None and self.registration.active and key in (27, ord(" ")):
            if key == 27:
                self._register_cancel()      # Space must not start the mouse in the middle of demonstrations
            return
        if key in (27, ord(" ")):
            was_paused = self.paused
            if not was_paused and now < getattr(self, "_recent_until", 0.0) + 1.0:
                self._record_negative_candidate(now, "user_cancelled", "Key pause immediately after action")
            self._pause()
            self.paused = key == 27 or not was_paused or self.ui.stage != "main"
            self._say("Mouse Control PAUSED" if self.paused else "Mouse Control RESUMED")
        elif key in (ord("m"), ord("M")):
            new = "relative" if str(engine.config.get("movement_mode", "relative")).lower() == "absolute" else "absolute"
            engine.set_movement_mode(new)
            mouse.release_all()
            self._say(f"Switched Movement Mode -> {new.upper()}")
        elif key in (ord("f"), ord("F")) and self._virtual_click_mark(now):
            pass
        elif key in (ord("f"), ord("F")):
            if self._rec is not None:
                self._rec.event(now, "mark")
            self._record_negative_candidate(now, "f_key", "User pressed F key")
            if self.failures.trigger(now, "manual"):
                self._say("실패·오작동 부정 후보 기록 (자동 재학습 미투입)")
        elif key in (ord("g"), ord("G")):
            if self._rec is not None:
                self._rec.event(now, "return_blocked_fp")
            if self.failures.mark_return_block_fp(now, note="User pressed G key (return block false positive)"):
                self._say("이동 차단 오탐 기록 (G 키)")
        elif key in (ord("n"), ord("N")):
            mouse.release_all()
            target = mouse.cycle_monitor()
            self._say(f"Monitor {mouse.screens.monitors.index(target) + 1}/{len(mouse.screens)}")
        elif key in (ord("+"), ord("="), ord("-"), ord("_")):
            step = .005 if key in (ord("+"), ord("=")) else -.005
            thr = max(.02, min(.12, float(engine.config.get("pinch_threshold", .055)) + step))
            engine.config["pinch_threshold"] = round(thr, 4)
            engine.config["release_threshold"] = round(thr + .02, 4)
            self._say(f"Pinch Threshold -> {thr:.3f}")
        elif key in (ord("["), ord("]")):
            cur = float(engine.config.get("fling_strength", 1.0))
            new = round(max(.1, min(5.0, cur * 1.2 if key == ord("]") else cur / 1.2)), 3)
            engine.config["fling_strength"] = new
            self._say(f"Fling Distance -> {new:.2f} ([S] to save)")
        elif key in (ord("r"), ord("R")):
            engine.load_config()
            self._say("Reloaded config/gestures.json")
        elif key in (ord("s"), ord("S")):
            self._say(f"Saved config to {engine.save_config().name}")
        elif key in (ord("p"), ord("P")):
            camera.switch_source("phone")
            self._say("Switched to Smartphone Browser Camera Mode", 2.5)
        elif key in (ord("c"), ord("C")):
            if camera.is_phone_web:
                camera.switch_source(0)
                self._say("Switched to USB Webcam 0")
            elif isinstance(camera.config.source, int):
                nxt = (camera.config.source + 1) % 3
                if camera.switch_source(nxt):
                    self._say(f"Switched to Camera Index {nxt}")
                else:
                    camera.switch_source(0)
                    self._say("Reverted to Camera Index 0")

    def _step(self) -> None:
        camera, tracker, engine, mouse, pointer = self.camera, self.tracker, self.engine, self.mouse, self.pointer
        self._drain(time.perf_counter())
        ui = self.ui
        ok, frame = camera.read()
        t0 = time.perf_counter()
        if not ok or frame is None:
            if self._guided is not None:
                self._guided.update(t0, available=False)
            pointer.frame_missing()
            if self._had_hand or mouse.is_dragging:
                # the camera stopped (unplugged, the phone app closed) in the middle of a drag: without this the mouse
                # button stayed down until Pause, because the "hand lost" release below is never reached
                mouse.close_switcher(confirm=False)
                mouse.release_all()
                engine.reset()
                self._had_hand = False
            time.sleep(.03)
            return
        # The preview is drawn on `frame` itself, later in this step. What keeps pixels for later needs the camera's picture.
        clean = frame.copy() if self._crop_rec is not None or (ui.hand_crop and self.roi_tracker.enabled) else None

        if ui.anchor_joints != self.engine.cursor_anchor_override:
            # the user picked other skeleton points as the cursor reference (both engines use it)
            self.engine.cursor_anchor_override = tuple(ui.anchor_joints)
            self.pointer.gestures.cursor_anchor_override = tuple(ui.anchor_joints)
        if ui.profile != self._applied_profile:
            # the user picked another calibration profile: both engines reload their config
            self._applied_profile = ui.profile
            self.engine.set_profile(ui.profile)
            self.pointer.gestures.set_profile(ui.profile)
            self._say(f"프로필: {ui.profile or '없음'}", 2.0)
        should_detect = not camera.is_phone_web or (camera.phone_server is not None and camera.phone_server.has_client)
        roi = self.hand_crop.roi if ui.hand_crop else None
        if should_detect:
            detections = tracker.detect(frame, roi=roi)
            searching = roi is not None and self.hand_crop.searching
            self._det_path = "search" if searching else "crop" if roi is not None else "full"
            self._search_frames = self._search_frames + 1 if searching else 0
            if roi is not None and not detections and searching and self._search_frames % 6:
                pass   # a far hand is too small for the whole frame: it is only tried now and then during the search
            elif roi is not None and not detections:
                # the crop stays (HandCrop.miss) so the next frames try it again; this frame falls back to the full frame
                roi = None
                self._det_path = "search_miss>full" if searching else "crop_miss>full"
                detections = tracker.detect(frame)
        else:
            detections = []
            self._det_path = "skipped"
        all_detections = list(detections)
        # every hand MediaPipe returned this frame, before the control-hand filter below
        self._raw_hands = [[d.handedness, round(float(d.score), 2)] for d in all_detections]

        if ui.stage != "main":
            if not self.paused:
                self._pause()
            detections = []
            self._hand_follower.reset()
        else:
            if engine.config.get("hand_follow_enabled", True):
                chosen = self._hand_follower.select(detections, ui.control_hand, camera.config.mirror, t0)
                if self._hand_follower.followed:
                    self._det_path += "+follow"
            else:
                chosen = selected_detection(detections, ui.control_hand, camera.config.mirror)
            detections = [chosen] if chosen is not None else []
            if detections:
                self._hand_seen_t = t0
        self.mouse.hand_size = self._hand_width_fraction(detections[0].landmarks, frame.shape[1], frame.shape[0]) if detections else None
        live = ui.stage == "main" and not self.paused and not self.draw.active and not ui.calibrating
        sequences = self.seq_store.active() if live else {}
        path_gestures = self.path_store.active() if live else {}
        posture = None
        if detections and (self.pose_probe.active or sequences or path_gestures):
            # the hand shape from the landmarks alone, in every mode (index pointer, motion mode, a held sign, paused)
            posture = hand_posture(detections[0].landmarks, frame.shape[0] / max(1, frame.shape[1]))
        if self.pose_probe.active and detections:
            # [손 모양 보여주기]: it used to be fed only inside the palm controls, which a thumbs-up or motion mode skip
            lm0 = detections[0].landmarks
            k = frame.shape[0] / max(1, frame.shape[1])           # x and y in the same units
            palm = palm_centre(lm0)
            size = hand_px(lm0, 1.0, k)                          # steady when the hand turns (src/virtual_hand.hand_px)
            angles = (math.degrees(math.atan2(-(lm0[9].y - lm0[0].y) * k, lm0[9].x - lm0[0].x)),
                      math.degrees(math.atan2(-(lm0[4].y - lm0[0].y) * k, lm0[4].x - lm0[0].x)))
            self.pose_probe.log_dir = self.log_dir / "pose_probe"
            self.pose_probe.step(t0, posture, (palm[0], palm[1] * k), size, angles,
                                 [[round(p.x, 4), round(p.y, 4), round(getattr(p, "z", 0.0), 4)] for p in lm0])
        if sequences:
            if detections:
                fired = self.sequences.step(t0, posture, sequences)
                if fired is not None:
                    self._run_sequence(fired)
            else:
                self.sequences.lost(t0)
        if path_gestures:
            # shape-drawing gestures: a hand shape held while the palm draws a shape (src/path_gesture.py)
            if detections:
                lm0 = detections[0].landmarks
                k = frame.shape[0] / max(1, frame.shape[1])
                pc = palm_centre(lm0)
                size = hand_px(lm0, 1.0, k)
                tip = 4 if posture == "thumbs_up" else 9          # a thumbs-up turns with its thumb
                angle = math.degrees(math.atan2(-(lm0[tip].y - lm0[0].y) * k, lm0[tip].x - lm0[0].x))
                fired = self.paths.step(t0, posture, (pc[0], pc[1] * k), size, path_gestures, self.draw_store, angle)
            else:
                fired = self.paths.step(t0, None, None, 0.0, path_gestures, self.draw_store)
            if fired is not None:
                self._run_gesture_key(fired, self.path_store.items.get(fired, {}).get("name", fired), "손 모양 그리기")
        if ui.hand_crop and detections:
            self.hand_crop.update(detections[0].landmarks, frame.shape[1], frame.shape[0], now=t0)
            if self.roi_tracker.enabled and not self.hand_crop.collapsed:
                self.roi_tracker.good(clean, hand_box(detections[0].landmarks, frame.shape[1], frame.shape[0]))
        elif ui.hand_crop:
            followed = self.roi_tracker.lost(frame, t0) if self.roi_tracker.enabled else None
            if followed is not None:
                self.hand_crop.follow(followed, frame.shape[1], frame.shape[0], t0)
            else:
                self.hand_crop.miss(t0)
        else:
            self.hand_crop.reset()
            self.roi_tracker.reset()
        palm_calibrating = self._palm_axes(t0, ui, detections)
        guided_recording = self._recording_mode(t0, ui, detections)
        self._virtual_hand = None
        self._preview_hand = self._finger = None
        if not detections:
            # A fast hand blurs and is lost for a few frames: keep the drawn hand where it was (faded) and its filters, so it glides
            # on from there instead of vanishing and popping up elsewhere. Only a longer loss lets go of everything.
            if self._vh_lost is None:
                self._vh_lost = t0
                if self._vh_last is not None:
                    self._vh_last = {**self._vh_last, "lost": True, "action": "", "guides": []}
            if t0 - self._vh_lost <= VH_LOST_KEEP and self._vh_last is not None:
                self._virtual_hand = self._vh_last
            else:
                self._vh_last = None
                self.box_mapper.reset()
                self.view_smoother.reset()
                self.hand_size.reset()
                self.ratchet.reset()
                self._vh_anchor = None
            self._vh_click, self._vh_engaged = None, False
            self.grip.reset()
            self.shape_pinch.reset()
            self.shape_right.reset()
            self.shape_switch.reset()
            self._pinch_since, self._pinch_drag, self._pinch_double = None, False, False
        else:
            self._vh_lost = None
        command_hold = self._online_recognize(t0, ui, detections, frame, guided_recording)
        self._track_arm(t0, frame, ui, detections)
        if engine.config.get("elbow_pivot", False) and detections and not ui.use_index_pointer:
            self.elbow.observe(t0, engine.palm_anchor_point(detections[0].landmarks), mouse.frame_aspect)
            if self.elbow.source == "arcs" and not self._elbow_announced:
                self._elbow_announced = True
                self._say("팔꿈치 위치를 찾았어요: 위아래·좌우를 팔꿈치 기준으로 펴서 움직여요", 3.0)
        else:
            self.elbow.observe(t0, None, mouse.frame_aspect)
        gate = self._gate_for(ui) if not ui.use_index_pointer and ui.stage == "main" else None
        if gate is not None:
            gate.step(t0, detections[0] if detections else None, frame.shape[0] / max(1, frame.shape[1]))
        self._rule_action = "none"
        self._engine_raw = None     # what the rule engine itself said this frame (guided capture overwrites result.action)
        toggle_hold = (not palm_calibrating) and self._toggle_sign(t0, ui, detections)
        motion_active = (not toggle_hold) and self._motion(t0, ui, detections)
        close_hold = (not motion_active) and (not toggle_hold) and self._close_sign(t0, ui, detections)
        if self._had_hand and not detections:
            mouse.close_switcher(confirm=False)
            mouse.release_all()
            engine.reset()
        self._had_hand = bool(detections)

        fh, fw = frame.shape[:2]
        mouse.frame_aspect = fh / max(1, fw)
        engine.frame_aspect = mouse.frame_aspect
        if self.frames % 10 == 0:
            small = cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA)
            level = int(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).mean())
            try:
                dark_below = float(engine.config.get("low_light_threshold", 70))
            except (TypeError, ValueError):
                dark_below = 70.0
            mode = camera.actual_mode()
            self._metrics = (f"{mode[0]}×{mode[1]} · {mode[2]:g}fps" if mode else "", level, level < dark_below)

        index_mode = ui.use_index_pointer
        draw_active = (self.draw.active and not index_mode and not motion_active and not toggle_hold
                       and not palm_calibrating and ui.stage == "main" and not guided_recording)
        if draw_active:
            self._draw_step(t0, ui, detections, fw, fh)
        tracking_only = self.test is not None
        contacts: Tuple[int, ...] = ()
        tip_xy = None
        result = GestureResult()
        frame_out = None
        if index_mode and not motion_active:
            if detections:
                tip = detections[0].landmarks[GestureEngine.INDEX_TIP]
                tip_xy = (tip.x, tip.y)
            frame_out = pointer.step(
                t0, detections[0].landmarks if detections else None, mouse.frame_aspect,
                PointerSettings(click_style=ui.click_style, anchor=ui.anchor, tracking_only=tracking_only,
                                axes=ui.axes, config=engine.config),
                active=not self.paused and not ui.calibrating, detecting=should_detect,
            )
        else:
            pointer.reset()

        if frame_out is not None:
            points = detections[0].landmarks
            contacts = frame_out.contacts
            if frame_out.action is not None:
                action = frame_out.action
                if frame_out.warp is not None:
                    mouse.warp_to(*frame_out.warp)
                result = GestureResult(action=action, cursor_point=frame_out.cursor_point,
                                       is_moving=frame_out.cursor_point is not None)
                mouse.apply(result, engine.config, index_tip=frame_out.pointer_point,
                            index_sensitivity=ui.sensitivity / 100, pointer_source=frame_out.pointer_source)
                pointer.record_cursor(t0, mouse.last_screen_pos)
                parts = [f"이동 {frame_out.speed:.2f}" + (" 유지" if frame_out.latched else "")]
                if self._metrics and self._metrics[0]:
                    parts.insert(0, "카메라 " + self._metrics[0])
                if tracking_only or ui.click_style == "off":
                    parts.append("클릭 끔")
                elif ui.click_style == "trigger":
                    r = frame_out.trigger_ratio
                    parts.append(f"방아쇠 {r:.2f}" if math.isfinite(r) else "방아쇠 —")
                if not frame_out.click_armed:
                    parts.append("클릭 대기: 엄지를 떼세요")
                self._say("  ·  ".join(parts), .5)
                if action in ("left_click", "right_click"):
                    self._recent_action, self._recent_until = action, time.perf_counter() + .5
            x, y = frame_out.preview_point
            if not ui.show_fingertips and 0 <= x <= 1 and 0 <= y <= 1:
                cv2.circle(frame, (int(x * fw), int(y * fh)), 6, (189, 229, 70), -1, cv2.LINE_AA)
            for number, landmark_id in enumerate(FINGERTIPS if ui.show_fingertips else ()):
                p = points[landmark_id]
                if not (0 <= p.x <= 1 and 0 <= p.y <= 1):
                    continue
                px, py = int(p.x * fw), int(p.y * fh)
                color = (189, 229, 70) if number == 1 or number in contacts else (245, 245, 245)
                cv2.circle(frame, (px, py), 10, (49, 35, 17), -1, cv2.LINE_AA)
                cv2.putText(frame, str(number), (px - 5, py + 5), cv2.FONT_HERSHEY_SIMPLEX, .46, color, 1, cv2.LINE_AA)
        elif detections and not index_mode and not motion_active and not draw_active and not close_hold and not toggle_hold \
                and not palm_calibrating:
            glab = None
            gtask = None
            if self._guided is not None:
                from src.gesture_catalog import BY_KEY
                glab = self._guided.label(t0)
                gtask = BY_KEY.get(glab.get("task"))
                gtok = (glab.get("task"), glab.get("set"), glab.get("rep"), glab.get("phase") == "do")
                if gtok != self._guided_rep_token:
                    self._guided_rep_token = gtok
                    engine._desktop_armed = True
                    engine._desktop_since = None
                    engine._last_trigger_times.clear()
                    engine._last_curled_at = -1e9
                    engine._last_halffist_at = -1e9
                    engine._index_released_at = -1e9
                    engine._scroll_released_at = -1e9
                    engine._pinky_fired_at = -1e9
                    engine._desktop_fired_at = -1e9
                    engine._side_released_at = -1e9
                    engine._side_armed = {"index": True, "ring": True, "pinky": True}
                    engine._click_fresh = {"middle": True, "ring": True, "pinky": True}
                    engine._secondary_since.clear()
                    engine._secondary_off.clear()
                    engine._index_pinched = False
                    engine._middle_hold_pinched = False
            hand = detections[0]
            result = engine.process(hand.landmarks)
            self._engine_raw = (result.action, result.active_gesture)   # before the guided capture below sets the target
            self._guided_obs_targets = None
            draw_pinched = engine.pinched_fingers
            if self._guided is not None and glab is not None:
                obs_targets = self._capture_rules.observe(t0, hand, result, glab)
                self._guided_obs_targets = obs_targets
                if gtask is not None and not gtask.free and gtask.label != "none":
                    if glab.get("phase") == "do" and gtask.key in obs_targets:
                        result.action = gtask.action or gtask.label
                        if gtask.label in ("thumb_index", "thumb_middle", "thumb_ring", "thumb_pinky", "thumb_index_middle"):
                            result.active_gesture = f"{gtask.label}_pinch"
                            draw_pinched = (
                                {"index", "middle"}
                                if gtask.label == "thumb_index_middle"
                                else {gtask.label.replace("thumb_", "")}
                            )
                        else:
                            result.active_gesture = gtask.label
                            draw_pinched = set()
                    else:
                        result.action = "none"
                        result.active_gesture = "none"
                        draw_pinched = set()
                elif glab.get("phase") in ("prep", "rest", "waiting", "paused"):
                    result.action = "none"
                    result.active_gesture = "none"
                    draw_pinched = set()
            rule_action = result.action
            if gate is not None and not guided_recording:
                result = gate.filter(t0, result)
                if gate.last_note:
                    self._say(gate.last_note, 1.2)
                    gate.last_note = ""
            self._rule_action = rule_action
            if command_hold:
                result.action, result.cursor_point, result.is_moving = "none", None, False
            screen_point = None       # box mode: where the palm (the drawn hand) is on the monitor
            cursor_point = None       # where the OS cursor goes: the palm, or the drawn pinch point while a click finger is in
            vh = None
            apply_cfg = engine.config
            if getattr(ui, "virtual_hand", False) and valid_box(ui.virtual_box) and not guided_recording:
                if result.action in VIRTUAL_OFF_ACTIONS:
                    result.action = "none"                # index / middle pinches are off here (the fist grabs)
                early = virtual_guides(pinch_guides(result.distances, engine.config, draw_pinched, action=result.action,
                                                    gesture=result.active_gesture))
                switching = bool(mouse.switcher_open)    # the task switcher: no page scroll, no drag; the box still places the cursor
                grip = self.grip.step("fist" if result.active_gesture == "fist" and not command_hold and not switching else None, t0)
                check = self._click_check(engine.config)
                if check is not None:
                    check.step(t0, hand, frame.shape[0] / max(1, frame.shape[1]))
                pinch_evt = None
                if grip is None and not command_hold:
                    quick = self._last_tap is not None and t0 - self._last_tap[0] <= DOUBLE_TAP   # a second tap: no waiting
                    right_evt = self.shape_right.step(hand.landmarks, frame.shape[1], frame.shape[0])
                    if right_evt == "down" or (self.shape_right.closed and self.shape_pinch.closed):
                        self._pinch_triple = True          # the index got there first: that press is the right click now
                    if right_evt == "up":
                        self._triple_open_t = t0
                    # while the three are together, and a moment after (the middle leaving looks like a thumb + index pinch)
                    settling = self.shape_right.closed or t0 - self._triple_open_t < .3
                    pinch_evt = None if settling and not self.shape_pinch.closed else self.shape_pinch.step(
                        hand.landmarks, frame.shape[1], frame.shape[0], frames=1 if quick else None)
                else:
                    self.shape_pinch.reset()
                    self.shape_right.reset()
                    right_evt = None
                    self._pinch_triple = False
                    self._pinch_since, self._pinch_drag, self._pinch_double = None, False, False
                second = None          # a second tap soon after a click: the double click's second click, at the same point
                if pinch_evt == "down":
                    self._pinch_since, self._pinch_drag, self._pinch_rejected = t0, False, False
                    last = self._last_tap
                    if last is not None and t0 - last[0] <= DOUBLE_TAP:
                        second, self._pinch_since = last[1], None      # no drag and no click on opening from this press
                        self._pinch_double = True
                    self._last_tap = None
                was_drag = self._pinch_drag
                if (self.shape_pinch.closed and self._pinch_since is not None and t0 - self._pinch_since >= PINCH_HOLD
                        and not switching and not self._pinch_triple and not self.shape_right.closed
                        and self._checked(check, t0)):
                    self._pinch_drag = True                # held closed: it is a drag, not a click
                dragging = self.shape_pinch.closed and self._pinch_drag
                tap = pinch_evt == "up" and not self._pinch_drag and not self._pinch_double and not self._pinch_triple \
                    and not self._pinch_rejected and self._checked(check, t0)
                drop = pinch_evt == "up" and self._pinch_drag     # the button comes up between the fingers, not at the palm
                press_t = self._pinch_since                    # when the fingers met (for the click log)
                if pinch_evt == "up" or (not self.shape_pinch.closed and not self.shape_right.closed):
                    self._pinch_since, self._pinch_drag, self._pinch_double = None, False, False
                    self._pinch_triple = False
                    self._pinch_rejected = False
                aiming = (self.shape_pinch.closed and not dragging) or tap or self.shape_right.closed
                nx, ny = self.box_mapper.target(hand.landmarks, ui.virtual_box, now=t0, aim=grip is None,
                                                hold=command_hold or grip == "scroll" or aiming
                                                or (grip is None and holds_still(early, result.action, result.active_gesture)))
                if (tap or second is not None) and result.action in PASSIVE_ACTIONS:
                    result.action = "left_click"          # thumb + index closed and opened again: click between them
                elif right_evt == "down" and result.action in PASSIVE_ACTIONS:
                    result.action = "right_click"         # thumb, index and middle together: right click there
                switch_now = grip is None and not command_hold and not self.shape_pinch.closed and not self.shape_right.closed                     and self.shape_switch.step(hand.landmarks, frame.shape[1], frame.shape[0], t0)
                if switch_now and result.action in PASSIVE_ACTIONS:
                    result.action = "task_switcher"       # opens it; the same shape again while it is open picks the highlighted one
                elif dragging:
                    result.action = "drag_hold"           # held closed: the button stays down until the fingers open
                log = self._click_log(engine.config)
                if log is not None and not self.paused:
                    log.frame(t0, hand)
                    kind = ("double" if second is not None and result.action == "left_click"
                            else "left" if tap and result.action == "left_click"
                            else "right" if right_evt == "down" and result.action == "right_click"
                            else "drag" if dragging and not was_drag else None)
                    if kind is not None:
                        pressed = press_t if kind in ("left", "drag") else t0
                        peak = check.peak(pressed, t0) if check is not None and check.ready and pressed is not None else None
                        log.click(kind, t0, pressed, peak)
                m = mouse.absolute_monitor
                screen_point = (int(round(m.left + nx * (m.width - 1))), int(round(m.top + ny * (m.height - 1))))
                if grip == "scroll":
                    # a fist holds the page: hand down = browse down (the pinch scroll goes the other way, like a touch screen)
                    if self.grip.started:
                        self.ratchet.reset()
                    # sideways is easier than up and down: fist right = browse down, left = up (fed to the scroll as up/down)
                    cx, cy = palm_centre(hand.landmarks)
                    result.action, result.cursor_point = "scroll_grab", (0.5, self.ratchet.step(cx, t0))
                    flip = not engine.config.get("virtual_fist_scroll_follows_page", False)
                    apply_cfg = {**engine.config, "scroll_invert": bool(engine.config.get("scroll_invert", False)) != flip,
                                 "scroll_gain": float(engine.config.get("scroll_gain", 36000.0))
                                 * float(engine.config.get("virtual_fist_scroll_speed", .5))}
                else:
                    self.ratchet.reset()
                vh = virtual_hand_view(hand.landmarks, frame.shape[1], frame.shape[0], early, result.action,
                                      size=self.hand_size(hand.landmarks, frame.shape[1], frame.shape[0], t0))
                if vh is not None:
                    vh["pts"] = self.view_smoother(vh["pts"], t0)
                    vh["grip"] = grip is not None          # a fist holds the page: the drawn hand turns amber (manux)
                    if grip is not None:
                        vh["click"] = None
                    elif self.shape_pinch.closed or tap or drop:
                        vh["click"] = pinch_point(vh["pts"], "index")
                    elif self.shape_right.closed:
                        vh["click"] = triple_point(vh["pts"])
                    else:
                        vh["click"] = click_offset(vh["pts"], early, result.action)
                engaged = (self.box_mapper.held or dragging or drop or self.shape_pinch.closed or self.shape_right.closed
                           or tap or result.action in ("left_click", "right_click", "drag_hold"))
                # The cursor IS the clicking point: no jump to the pinch point when the fingers meet (manux measured 18.5
                # jumps >= 60 px a minute from that offset). The drawing is shifted instead, so that its thumb-index point
                # (`anchor`, followed slowly, frozen while clicking or gripping) sits on the cursor.
                if vh is not None:
                    aim_px = pinch_point(vh["pts"], "index")
                    if self._vh_anchor is None:
                        self._vh_anchor = list(aim_px)
                    elif not engaged and grip is None:
                        self._vh_anchor = [a + VH_ANCHOR_FOLLOW * (b - a) for a, b in zip(self._vh_anchor, aim_px)]
                self._vh_click = None
                cursor_point, jump = screen_point, False
                if second is not None:
                    cursor_point, jump = second, True      # exactly the first click's pixel: Windows pairs them as a double click
                if tap and result.action == "left_click":
                    self._last_tap = (t0, cursor_point)
                self._vh_engaged = engaged
                result.is_moving = False      # the box places the cursor below, not the relative movement
                if switching:
                    apply_cfg = {**apply_cfg, "switcher_external_cursor": True}
            if not self.paused and not guided_recording:      # guided recording: rules judge, the mouse stays still
                if cursor_point is not None:
                    # the hand does the clicking: the cursor goes to the pinch point first (at once when the fingers meet)
                    if cursor_point != tuple(mouse.last_screen_pos):
                        mouse._move_os_cursor(*cursor_point, immediate=jump)
                    if result.action == "drag_hold" and not mouse._hold_dragging:
                        mouse._hold_dragging = True
                        mouse._mouse_down()
                comp_hit = self.composite_detector.step(
                    t0, hand.landmarks, result.active_gesture, draw_pinched, mouse.frame_aspect
                )
                if comp_hit is not None:
                    reg = get_action_registry()
                    fg = mouse.foreground_info() if not mouse.dry_run else {}
                    resolved = reg.resolve_effective_action(comp_hit["key"], fg)
                    ok, badge = self._run_resolved(reg, resolved)
                    if ok:
                        self._say(f"복합 동작 [{comp_hit['name']}] → {badge}", 1.5)
                        self._recent_action, self._recent_until = comp_hit["key"], time.perf_counter() + 0.8
                        result.action = "none"
                elif result.action in ("left_click", "right_click", "show_desktop", "task_switcher"):
                    action_to_key = {
                        "left_click": "thumb_middle",
                        "right_click": "thumb_ring",
                        "show_desktop": "palm_side",
                        "task_switcher": "thumb_pinky",
                    }
                    gkey = action_to_key[result.action]
                    reg = get_action_registry()
                    fg = mouse.foreground_info() if not mouse.dry_run else {}
                    resolved = reg.resolve_effective_action(gkey, fg)
                    default_b = DEFAULT_GESTURE_BINDINGS.get(gkey, {})
                    if (
                        bool(resolved.get("matched_app"))
                        or resolved.get("action_type") != default_b.get("action_type")
                        or resolved.get("action_value") != default_b.get("action_value")
                        or not reg.get_binding(gkey).get("enabled", True)
                    ):
                        ok, badge = self._run_resolved(reg, resolved)
                        if ok:
                            self._say(f"매핑 실행: {badge}", 1.2)
                        self._recent_action, self._recent_until = result.action, time.perf_counter() + 0.8
                        result.action = "none"
                mouse.apply(result, apply_cfg)
                if result.action in ONE_SHOT:
                    self._recent_action, self._recent_until = result.action, time.perf_counter() + .8
            guides = pinch_guides(result.distances, engine.config, draw_pinched,
                                  action=result.action, gesture=result.active_gesture)
            anchor = engine.palm_anchor_point(hand.landmarks)
            if self.qml_skeleton:
                # the skeleton is drawn by QML over the preview (src/HandSkeleton.qml, from manux-merged-20261110)
                self._preview_hand = preview_hand(hand.landmarks, guides, anchor, result.is_moving)
            else:
                HandTracker.draw_hand(frame, hand.landmarks, guides)
            self._finger = overlay_frame(hand.landmarks, camera.config.mirror, result.action, result.is_moving)
            label = self._recent_action if time.perf_counter() < self._recent_until else result.action
            if vh is None:
                vh = virtual_hand_view(hand.landmarks, frame.shape[1], frame.shape[0], guides, label,
                                      size=self.hand_size(hand.landmarks, frame.shape[1], frame.shape[0], t0))
                if vh is not None:
                    vh["pts"] = self.view_smoother(vh["pts"], t0)
            if vh is not None:
                if screen_point is not None:
                    guides = virtual_guides(guides)         # only the pinches that work here are drawn on the screen hand
                vh["guides"] = [[f, s] for f, s in guides]
                vh["action"] = HAND_ACTION_NAMES.get(label, "")
                if screen_point is not None and self._vh_anchor is not None:
                    vh["anchor"] = [round(v, 1) for v in self._vh_anchor]   # drawn on the cursor (the window centre)
                    vh["click"] = list(vh["anchor"])
                    vh["aim"] = not self._vh_engaged
                elif self._vh_click:
                    vh["click"] = list(self._vh_click)
                else:
                    vh["click"] = click_offset(vh["pts"], guides, label)
                if screen_point is not None:
                    vh["screen"] = list(screen_point)       # where it is drawn (the palm), also while paused
                    vh["switcher"] = bool(mouse.switcher_open)   # the window goes back above the task switcher
            self._virtual_hand = vh
            self._vh_last = vh
            if self._draw_cursor_anchor and not self.qml_skeleton:
                self._draw_cursor_anchor(frame, hand.landmarks, anchor, result.is_moving)
            self._draw_arm(frame, t0)
            if engine.config.get("elbow_pivot", False):
                self._draw_elbow(frame, engine.palm_anchor_point(hand.landmarks), mouse.frame_aspect)
            if self._draw_recording:
                self._draw_recording(frame, mouse.recorded_path)
            if mouse.fling_count != self._last_fling:
                self._last_fling = mouse.fling_count
                self._say(f"FLING! ~{mouse.last_fling_distance:.0f}px", .8)
        elif not index_mode:
            result = engine.process(None)

        if roi is not None:
            cv2.rectangle(frame, roi[:2], roi[2:], (189, 229, 70), 2, cv2.LINE_AA)
        self._failures(t0, detections, roi, index_mode, ui, frame, result, frame_out)
        self._record_frame(t0, detections, result, clean)
        if ui.stage == "main" and not guided_recording and self.test is None:
            self._online_frame(t0, detections, frame)
        self._last_step_t = t0
        tracking = self._tracking(t0, detections, index_mode, ui)

        now = time.perf_counter()
        status = self._status if now <= self._status_until else ""
        if now < self._recent_until:
            action = self._recent_action
        elif result.action and result.action != "none":
            action = result.action
        elif self.mouse.switcher_open:
            action = "task_switcher"
        elif result.active_gesture in (
            "thumb_pinky_pinch",
            "thumb_ring_pinch",
            "thumb_middle_pinch",
            "thumb_index_pinch",
            "thumb_index_middle_pinch",
        ):
            action = str(engine._gesture_cfg(result.active_gesture).get("action", "move_cursor"))
        elif detections and not index_mode:
            action = "move_cursor"
        else:
            action = result.action
        self.frames += 1
        snap = Snapshot(
            seq=self.frames, frame=frame, detected=bool(detections), paused=self.paused, action=action,
            fps=camera.fps, detect_ms=tracker.last_detect_ms, status=status, contacts=contacts,
            tip=tip_xy, aspect=mouse.frame_aspect, source=camera.config.source,
            mirrored=camera.config.mirror, detections=all_detections if ui.stage == "capture" else [],
            metrics=self._metrics, tracking=tracking, failures=self._pending_failures,
            recent_line=self._pending_recent, message=self._pending_message,
            motion=(self.motion.active, self.motion.remaining(t0), self._motion_last, self.motion.entering(t0),
                    self.motion.leaving(t0)),
            toggle_progress=self.toggle_sign.progress(t0),
            close_sign=(self.close_sign.progress(t0), self.close_sign.pending(t0),
                        self._close_notice if time.perf_counter() < self._close_notice_until else ""),
            palm_axes=self._palm_view(t0),
            rec=self._rec_view(t0),
            gate_status=self.gate.status_summary() if (self.gate is not None and ui.tcn_gate) else None,
            registration=self._registration_view,
            online_gesture=self._online_gesture if t0 < self._online_gesture_until else "",
            virtual_hand=self._virtual_hand,
            preview_hand=self._preview_hand,
            pose_probe=self._pose_probe_view(t0),
            draw=self.draw.view(t0) if self.draw.active or self.draw.last is not None else None,
            finger=self._finger,
            admin_blocked=self._admin_window_in_front(t0),
            hand_lost=(t0 - self._hand_seen_t) > float(self.engine.config.get("hand_lost_alert", 0.35)),
            arm_lost=bool(self.pose is not None and not ui.use_index_pointer
                          and (self._arm is None or t0 - self._arm_t > float(self.engine.config.get("arm_lost_alert", 0.8)))),
        )
        self._pending_failures = self._pending_recent = None
        self._pending_message = ""
        with self._cond:
            self._snap = snap
            self._cond.notify_all()
        if self.perf_log is not None:
            if ui.stage == "main":
                lms = detections[0].landmarks if detections else None
                self.perf_log.hand(
                    found=bool(detections), raw=bool(all_detections), followed=self._hand_follower.followed,
                    size=max(math.hypot(lms[k].x - lms[0].x, (lms[k].y - lms[0].y) * frame.shape[0] / frame.shape[1])
                             for k in (5, 9, 13, 17)) if lms else None)
            self.perf_log.frame(tracker.last_detect_ms if should_detect else None, (time.perf_counter() - t0) * 1000.0)
            self.perf_log.tick(camera.fps, camera.dropped_frames)

    def _online_frame(self, t0: float, detections, frame) -> None:
        """Everyday-use collection. A failure here switches collection off, it never stops the mouse."""
        if self._online_failed or not self.engine.config.get("online_learning_enabled", False):
            return
        try:
            if self.online is None:
                config = self.engine.config
                self.online = OnlineCollector(
                    SampleStore(self._online_root or ONLINE_DEFAULT_ROOT), pc=machine_info(str(config.get("pc_alias", "")))["pc"],
                    camera=str(config.get("camera_note", "")), aspect=frame.shape[0] / max(1, frame.shape[1]),
                    handedness=str(self.ui.control_hand))
                self._online_reload()
            hand = detections[0] if detections else None
            lm = world = None
            if hand is not None and getattr(hand, "world", None):
                lm, world = [(p.x, p.y, p.z) for p in hand.landmarks], hand.world
            if self.registration is not None and self.registration.active:
                self.registration.update(t0, lm, world)     # demonstrations are saved on approval, not as everyday use
                self._registration_view = self.registration.view()
                return
            self.online.frame(t0, hand, getattr(self, "_rule_action", None) if hand is not None else None)
        except Exception as exc:
            self._online_failed = True
            self.registration = None
            self._say(f"학습 데이터 수집을 껐어요: {exc}", 5.0)

    def _online_recognize(self, t0: float, ui, detections, frame, guided_recording: bool) -> bool:
        """Registered gestures, before the rules act on this frame. True = one is being made: the hand is giving a command, so the
        cursor stays where it is and no click is judged (otherwise the command lands somewhere else)."""
        if self._online_failed or not self.engine.config.get("online_learning_enabled", False):
            return False
        if ui.stage != "main" or guided_recording or self.test is not None or (self.registration is not None and self.registration.active):
            return False
        try:
            if self.recognizer is None:
                if not self._online_load():
                    return False
                self.recognizer = PoseRecognizer(self.online_memory, frame.shape[0] / max(1, frame.shape[1]))
            hand = detections[0] if detections else None
            lm = world = None
            if hand is not None and getattr(hand, "world", None):
                lm, world = [(p.x, p.y, p.z) for p in hand.landmarks], hand.world
            fired = self.recognizer.step(t0, lm, world, USER_SOURCE)
            if fired:
                self._online_gesture, self._online_gesture_until = fired, t0 + 1.0
            elif self.recognizer.current:
                self._online_gesture_until = max(self._online_gesture_until, t0 + .3)
            due = self.command_gate.step(t0, self.recognizer.current, allowed=not self.paused)
            if due:
                self._run_command(due, t0)
            if self.recognizer.candidate:
                self._freeze_until = t0 + FREEZE_SECONDS
            return t0 < self._freeze_until
        except Exception as exc:
            self._online_failed = True
            self._say(f"새 동작 인식을 껐어요: {exc}", 5.0)
            return False

    def _run_command(self, label: str, t0: float) -> None:
        """The command the person chose for a registered gesture. Nothing happens for a gesture without one."""
        value = command_of(label)
        if not value:
            return
        if foreground_window_title().startswith("Hand-Mouse"):
            # the keys would land in this app itself, where a letter can be a shortcut (Q quits)
            self._online_gesture, self._online_gesture_until = f"{label}: 앱 창에는 명령을 보내지 않아요", t0 + 1.5
            return
        registry = get_action_registry()
        resolved = registry.resolve_effective_action(binding_key(label), self.mouse.foreground_info() if not self.mouse.dry_run else {})
        ok, badge = registry.execute_resolved_action(resolved, dry_run=self.mouse.dry_run, mouse_controller=self.mouse)
        self._online_gesture = f"{label} → {command_text(value)}" if ok else f"{label}: {badge}"
        self._online_gesture_until = t0 + 1.5
        self._say(f"새 동작 실행: {label} → {badge}", 1.5)

    def _gesture_delete(self, label: str) -> None:
        if not self._online_load() or self.online_memory.classes.get(label) is None or self.online_memory.classes[label].source != USER_SOURCE:
            return
        self.online_memory.remove_class(label)
        root = self._online_root or ONLINE_DEFAULT_ROOT
        self.online_memory.save(root / POSE_FILE)
        store = SampleStore(root)
        store.delete([s.sample_id for s in store.samples(label=label)])
        forget_command(label)
        if self.recognizer is not None:
            self.recognizer.reset()
        self.command_gate.reset()

    def _online_load(self) -> bool:
        """The poses of the known classes and the registered gestures (scripts/online_build_pose.py)."""
        if self.online_memory is not None:
            return True
        path = (self._online_root or ONLINE_DEFAULT_ROOT) / POSE_FILE
        if getattr(self, "_online_load_failed", False) or not path.is_file():
            return False
        try:
            self.online_memory = PoseMemory.load(path)
        except Exception as exc:
            self._online_load_failed = True
            self._say(f"새 동작 기능을 켜지 못했어요: {exc}", 5.0)
            return False
        return True

    def _register_start(self, name: str, now: float) -> None:
        if not self.engine.config.get("online_learning_enabled", False) or not self._online_load():
            self._registration_view = {"state": "failed", "message": "새 동작 기능이 꺼져 있거나 준비되지 않았어요"}
            return
        self._pause()
        try:
            config = self.engine.config
            aspect = self.mouse.frame_aspect
            self.registration = RegistrationSession(
                name, memory=self.online_memory, embed=lambda frames: pose_features(frames, aspect),
                store=SampleStore(self._online_root or ONLINE_DEFAULT_ROOT), config=self.registration_config,
                meta={"pc": machine_info(str(config.get("pc_alias", "")))["pc"], "camera": str(config.get("camera_note", "")),
                      "aspect": aspect, "handedness": str(self.ui.control_hand)})
            self._registration_view = self.registration.view()
        except RegistrationError as exc:
            self.registration = None
            self._registration_view = {"state": "failed", "message": str(exc)}

    def _register_approve(self) -> None:
        if self.registration is None:
            return
        try:
            self.registration.approve((self._online_root or ONLINE_DEFAULT_ROOT) / POSE_FILE)
            self._registration_view = self.registration.view()
            if self.recognizer is not None:
                self.recognizer.reset()
        except (RegistrationError, OSError, ValueError) as exc:
            self._registration_view = {"state": "failed", "message": str(exc)}
        self.registration = None

    def _register_cancel(self) -> None:
        if self.registration is not None:
            self.registration.cancel()
        self.registration, self._registration_view = None, None

    def _online_reload(self) -> None:
        """A new production model (or a rollback): the guesses come from it from now on. Without torch or a model: the rule engine's."""
        self._online_model_version += 1          # the gate reloads too, and holds actions for a moment (OnlineGate)
        if self.online is None:
            return
        try:
            from src.online.app_job import ProductionPredictor
            from src.online.versions import ModelManager
            root = self._online_root or ONLINE_DEFAULT_ROOT
            self.online.guess = ProductionPredictor(root).guess if ModelManager(root).has("production") else None
        except Exception:
            self.online.guess = None

    def take_correction(self):
        """A saved "last moments" sample waiting for the person's answer, or None. UI thread."""
        return self.online.take_manual() if self.online is not None else None

    def _motion_blocked(self, t0: float) -> bool:
        """A pinch, scroll or drag is going on or has just ended: the entry pose does not count then."""
        quiet = float(self.motion.cfg("motion_enter_quiet"))
        for g in (self.engine, self.pointer.gestures):
            last = max(g._index_released_at, g._scroll_released_at, g._pinky_fired_at,
                       max(g._last_trigger_times.values(), default=-1e9))
            if g._index_pinched or g._secondary_since or (t0 - last) < quiet:
                return True
        return bool(self.mouse.is_dragging or self.mouse.scroll_coasting)

    def _toggle_sign(self, t0: float, ui, detections) -> bool:
        """The V sign held still switches the mouse control on / off (like the Space key). It keeps watching while the control is
        off. True while the sign is held with the control ON (the cursor and pinches are suspended, so the fingers do not move it)."""
        ready = ui.stage == "main" and not ui.calibrating and not ui.use_index_pointer and self.test is None and self._guided is None
        if not ready or not detections:
            if self.toggle_sign.progress(t0) > 0 or self.toggle_sign.last_pose:
                self.toggle_sign.update(t0, None)
            return False
        blocked = False
        if not self.paused:
            quiet = self.toggle_sign.cfg("toggle_sign_quiet")
            for g in (self.engine, self.pointer.gestures):
                last = max(g._index_released_at, g._scroll_released_at, g._pinky_fired_at,
                           max(g._last_trigger_times.values(), default=-1e9))
                if g._index_pinched or g._secondary_since or (t0 - last) < quiet:
                    blocked = True
            blocked = blocked or bool(self.mouse.is_dragging or self.mouse.scroll_coasting)
        was_holding = self.toggle_sign.holding(t0)
        v_model_ok = bool(
            self.gate is not None
            and self.ui.tcn_gate
            and "v_sign" in getattr(self.gate, "classes", ())
            and self.gate.allows("v_sign", t0)
        )
        event = self.toggle_sign.update(
            t0,
            detections[0].landmarks,
            blocked=blocked,
            model_confirmed=v_model_ok,
        )
        custom = self._sign_override("v_sign") if event is not None and not self.paused else None
        if custom is not None:
            # 2026-11-10: the V sign's binding was changed (e.g. by the AI helper) but this detector always toggled the
            # control, so the new command never ran. Now it runs the binding; while the control is OFF the V sign
            # still turns it on (else no hand gesture could switch the control back on).
            self.mouse.release_all()
            self.engine.reset()
            self.pointer.reset()
            if custom != "off":
                ok, badge = self._run_resolved(get_action_registry(), custom)
                self._close_notice, self._close_notice_until = f"V 손 모양 → {badge}", time.perf_counter() + 2.0
        elif event is not None:
            self.mouse.release_all()
            self.engine.reset()
            self.pointer.reset()
            self._key(ord(" "), t0)                         # same as the Space key: toggles the control
            text = "마우스 제어 꺼짐 (V 손 모양)" if self.paused else "마우스 제어 켜짐 (V 손 모양)"
            self._close_notice, self._close_notice_until = text, time.perf_counter() + 2.5
            self.failures.trigger(t0, "toggle_sign", note=text)
        holding = self.toggle_sign.holding(t0) and not self.paused
        if holding and not was_holding:
            self.mouse.release_all()
            self.engine.reset()
            self.pointer.reset()
        return holding

    def _close_sign(self, t0: float, ui, detections) -> bool:
        """The rock sign held still asks to close the window; once more within a few seconds sends Alt+F4.
        True while the sign is held (the pinch controls are suspended then, so the folded fingers do not run the cursor)."""
        ready = ui.stage == "main" and not self.paused and not ui.calibrating and not ui.use_index_pointer
        if not ready or not detections:
            if self.close_sign.progress(t0) > 0 or self.close_sign.last_pose:
                self.close_sign.update(t0, None)
            return False
        quiet = self.close_sign.cfg("close_sign_quiet")
        blocked = not self._intent("rock_sign", t0)
        for g in (self.engine, self.pointer.gestures):
            last = max(g._index_released_at, g._scroll_released_at, g._pinky_fired_at,
                       max(g._last_trigger_times.values(), default=-1e9))
            if g._index_pinched or g._secondary_since or (t0 - last) < quiet:
                blocked = True
        blocked = blocked or bool(self.mouse.is_dragging or self.mouse.scroll_coasting)
        was_holding = self.close_sign.holding(t0)
        event = self.close_sign.update(t0, detections[0].landmarks, blocked=blocked)
        if event is not None:
            if event.kind == "ask":
                self._say("창을 닫을까요? 4초 안에 같은 손(검지·새끼만 펴기)을 한 번 더 하면 닫습니다", 4.0)
            elif event.kind == "close":
                sent = self.mouse.send_motion_action("close_window")
                info = dict(getattr(self.mouse, "last_close_info", {}) or {})
                event.info.update({"sent": bool(sent), "window": info})
                if sent:
                    self._close_notice, self._close_notice_until = "창 닫기 보냄 (Alt+F4)", time.perf_counter() + 2.0
                elif info.get("own"):
                    self._close_notice, self._close_notice_until = "이 앱이 앞에 있어 닫지 않음: 닫을 창을 앞에 두세요", time.perf_counter() + 3.0
                else:
                    self._close_notice, self._close_notice_until = "닫을 수 없는 창 (바탕화면·작업표시줄)", time.perf_counter() + 3.0
                self._say(self._close_notice, 3.0)
            if event.kind in ("ask", "close"):
                self.failures.trigger(t0, f"close_{event.kind}", note=json.dumps(event.info, ensure_ascii=False))
        holding = self.close_sign.holding(t0)
        if holding and not was_holding:
            self.mouse.release_all()
            self.engine.reset()
            self.pointer.reset()
        return holding

    def _track_arm(self, t0: float, frame, ui, detections) -> None:
        """Pose every `pose_every` frames in palm mode; a visible elbow goes to the elbow pivot."""
        if self.pose is None or ui.use_index_pointer or not detections:
            return
        every = max(1, int(self.engine.config.get("pose_every", 3)))
        if self.frames % every:
            return
        lms = self.pose.detect(frame)
        if lms:
            self._pose_raw = (t0, [list(lms[i]) for i in range(11, 17)])
        hand_wrist = detections[0].landmarks[0]
        arm = pick_arm(lms, (hand_wrist.x, hand_wrist.y)) if lms else None
        if arm is None:
            return
        # Pose joints jitter a lot from frame to frame (the shoulder and elbow sat still but jumped on the preview):
        # average them, and keep the last good position of a joint the model is unsure of.
        k = max(0.05, min(1.0, float(self.engine.config.get("pose_smooth", 0.25))))
        min_vis = float(self.engine.config.get("pose_min_visibility", 0.5))
        prev = self._arm if (self._arm is not None and t0 - self._arm_t < 1.0) else None
        smooth = {"side": arm["side"]}
        for name in ("shoulder", "elbow", "wrist"):
            x, y, v = arm[name]
            if prev is None:
                smooth[name] = (x, y, v)
            elif v < min_vis:
                px, py, _ = prev[name]
                smooth[name] = (px, py, v)
            else:
                px, py, pv = prev[name]
                kk = k if pv >= min_vis else 1.0
                smooth[name] = (px + kk * (x - px), py + kk * (y - py), v)
        arm = smooth
        self._arm, self._arm_t = arm, t0
        ex, ey, ev = arm["elbow"]
        if ev >= float(self.engine.config.get("pose_min_visibility", 0.5)) and self.engine.config.get("elbow_pivot", False):
            self.elbow.set_pose((ex, ey), self.mouse.frame_aspect, t0)

    def _draw_arm(self, frame, t0: float) -> None:
        """Shoulder - elbow - wrist of the cursor arm (Pose). Joints the model is unsure of are drawn hollow."""
        if self._arm is None or t0 - self._arm_t > 0.5:
            return
        fh, fw = frame.shape[:2]
        min_vis = float(self.engine.config.get("pose_min_visibility", 0.5))
        pts = [self._arm["shoulder"], self._arm["elbow"], self._arm["wrist"]]
        xy = [(int(x * fw), int(y * fh)) for x, y, _ in pts]
        teal = (200, 200, 60)
        for (a, b), (_, _, va), (_, _, vb) in zip(zip(xy, xy[1:]), pts, pts[1:]):
            if min(va, vb) >= min_vis:
                cv2.line(frame, a, b, teal, 3, cv2.LINE_AA)
        for (x, y), (_, _, v), name in zip(xy, pts, ("shoulder", "elbow", "wrist")):
            if not (0 <= x < fw and 0 <= y < fh):
                continue
            cv2.circle(frame, (x, y), 9, teal, -1 if v >= min_vis else 2, cv2.LINE_AA)
            if name != "wrist":
                cv2.putText(frame, name, (x + 12, y - 8), cv2.FONT_HERSHEY_SIMPLEX, .55, teal, 2, cv2.LINE_AA)

    def _draw_elbow(self, frame, palm, aspect: float) -> None:
        """Estimated elbow (src/elbow_pivot.py) on the preview: a ring at it and the forearm line to the palm point.
        Off-screen (the usual case, below the frame) it is drawn at the frame edge with the line pointing to it."""
        fh, fw = frame.shape[:2]
        orange = (40, 160, 255)
        if self.elbow.source == "pose":
            return                                  # the Pose arm (_draw_arm) already shows the real elbow
        if self.elbow.elbow is None:
            cv2.putText(frame, "elbow: move left/right to find it", (12, fh - 14), cv2.FONT_HERSHEY_SIMPLEX, .55,
                        orange, 2, cv2.LINE_AA)
            return
        ex, ey = self.elbow.elbow[0] * fw, self.elbow.elbow[1] / max(1e-6, aspect) * fh
        if palm is not None:
            px, py = palm[0] * fw, palm[1] * fh
            cv2.line(frame, (int(px), int(py)), (int(ex), int(ey)), orange, 2, cv2.LINE_AA)
        inside = 0 <= ex < fw and 0 <= ey < fh
        mx, my = int(min(max(ex, 8), fw - 8)), int(min(max(ey, 8), fh - 8))
        cv2.circle(frame, (mx, my), 11, orange, 3 if inside else 2, cv2.LINE_AA)
        label = "elbow (estimated)"
        cv2.putText(frame, label if inside else label + " (off screen)", (mx + 14, max(18, my - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, .55, orange, 2, cv2.LINE_AA)

    def _click_log(self, config) -> Optional[ClickLog]:
        """Everyday clicks of the virtual hand and the F marks (src/click_log.py); None when switched off."""
        if not config.get("virtual_click_log", True):
            return None
        if self.click_log is None:
            self.click_log = ClickLog(self.log_dir / "virtual_clicks", pc=machine_info(str(config.get("pc_alias", "")))["pc"],
                                      camera=str(config.get("camera_note", "")))
        return self.click_log

    def _virtual_click_mark(self, now: float) -> bool:
        """F right after a virtual hand click: that click was not meant. True = handled (no "what was it?" question)."""
        kind = self.click_log.mark_last(now) if self.click_log is not None else None
        if kind is None:
            return False
        self._say(f"방금 {CLICK_NAMES.get(kind, kind)}은 '클릭 아님'으로 기록했어요", 1.5)
        return True

    def _click_check(self, config) -> Optional[ClickCheck]:
        """The TCN's look at the virtual hand's thumb + index clicks (src/click_check.py); None when switched off."""
        if not config.get("virtual_click_check", False):     # off by default: see CLICK_CHECK_THRESHOLD
            return None
        if self.click_check is None and not self.dry_run:
            self.click_check = ClickCheck(float(config.get("virtual_click_threshold", CLICK_CHECK_THRESHOLD)))
        return self.click_check

    def _checked(self, check: Optional[ClickCheck], t0: float) -> bool:
        """Does the model see this thumb + index press as one? (No model: yes.) Says so when it drops one."""
        if check is None or check.allows(self._pinch_since, t0):
            return True
        self._pinch_since = None           # this press is done: no drag later, no click on opening
        self._pinch_rejected = True        # (allows() says yes to a press without a start time, so remember the no)
        self._say("집기로 보이지 않아 클릭하지 않았어요", .8)
        return False

    def _gate_for(self, ui) -> Optional[GestureGate]:
        """The intent checker when it is switched on (and the model loads), else None. `gate_model` "online" uses the model learned from
        this person (src/online/safety.py); a promoted model or a rollback reloads it."""
        key = (getattr(ui, "model_version", 0), getattr(ui, "gate_model", "tcn"), self._online_model_version)
        reload = key != getattr(self, "_gate_key", None)
        if reload:
            self._gate_key = key
            self._gate_failed = False
        if not ui.tcn_gate or self._gate_failed:
            return None
        if self.gate is None or reload:
            try:
                if key[1] == "online":
                    from src.online.safety import OnlineGate
                    replacement = OnlineGate(self._online_root or ONLINE_DEFAULT_ROOT, self.engine.config)
                    note = f"손짓 확인: 내 모델 켜짐 ({len(replacement.classes) - 1}종 동작)"
                else:
                    predict, info = load_predictor(GATE_MODEL)
                    replacement = GestureGate(predict, info, self.engine.config)
                    note = f"손짓 확인(TCN) 켜짐: 학습된 {len(info['classes']) - 1}종 동작을 확인해요"
                self.mouse.release_all()
                self.engine.reset()
                self.pointer.reset()
                self.gate = replacement
                self._say(note, 3.0)
            except Exception as exc:
                # a failed reload keeps the checker that was working; only a first load that fails leaves none
                switched = self.gate is not None and isinstance(self.gate, _online_gate_class()) != (key[1] == "online")
                if switched:
                    self.gate = None          # the other kind was asked for: never keep confirming with the wrong model
                self._gate_failed = self.gate is None
                self._say(f"손짓 확인을 켜지 못했어요: {exc}", 5.0)
                return self.gate
        return self.gate
        return self.gate

    def _intent(self, target, now):
        gate = self.gate if self.ui.tcn_gate and not self.ui.use_index_pointer else None
        return gate is None or gate.allows(target, now)

    # ------------------------------------------------------------------ dataset recording
    def _rec_meta(self, mode: str) -> Dict[str, Any]:
        cam = self.camera
        cfg = self.engine.config
        try:
            real = cam.actual_mode()
        except Exception:
            real = None
        keys = ("pinch_threshold", "release_threshold", "click_threshold", "ring_click_threshold", "pinky_click_threshold",
                "pinch_size_ratio", "click_size_ratio", "ring_click_size_ratio", "pinky_click_size_ratio",
                "fist_dpi_multiplier", "shape_dpi_curve", "side_rearm_ratio", "curled_click_size_ratio")
        return {
            "camera": {"source": str(cam.config.source), "mode": list(real) if real else None,
                       "mirror": bool(cam.config.mirror), "phone_web": bool(cam.is_phone_web),
                       "note": str(cfg.get("camera_note", ""))},
            "frame_aspect": round(float(self.mouse.frame_aspect), 5),
            "control_hand": self.ui.control_hand, "profile": self.engine.profile_name or "",
            "pose": self.pose is not None,
            "rules": {k: cfg.get(k) for k in keys if k in cfg},
            "pose_joints": "MediaPipe Pose 11-16: left shoulder, right shoulder, left elbow, right elbow, left wrist, "
                           "right wrist (x, y, visibility)",
        }

    def _recording_mode(self, t0: float, ui, detections=()) -> bool:
        """Start / stop / steer the recording. True while a guided recording runs (the mouse must not move)."""
        discard_req = getattr(ui, "rec_discard", 0)
        if discard_req != getattr(self, "_rec_discard_seen", 0):
            self._rec_discard_seen = discard_req
            self._discard_recording(t0)
            self._rec_mode = "off"
            self._rec_focus = ""
            return False
        mode = ui.rec_mode if ui.stage == "main" and not ui.use_index_pointer else "off"
        focus = getattr(ui, "rec_focus", "") if mode == "guided" else ""
        if mode != self._rec_mode or focus != getattr(self, "_rec_focus", ""):
            self._stop_recording(t0)
            self._rec_mode = mode
            self._rec_focus = focus
            if mode in ("guided", "free"):
                self.mouse.release_all()
                meta = self._rec_meta(mode)
                meta["focus"] = focus
                meta["pc_alias"] = str(self.engine.config.get("pc_alias", ""))
                plan = getattr(ui, "rec_plan", None) if mode == "guided" else None
                self._guided = GuidedSession(self.engine.config, tasks=tasks_for(focus), plan=plan) if mode == "guided" else None
                if self._guided is not None:
                    from src.capture_rules import CaptureRules
                    self._capture_rules = CaptureRules(self.engine.config)
                    self._pause()
                    if self._guided.plan:
                        meta["capture_plan"] = self._guided.plan
                self._rec = SessionRecorder(self.rec_dir, mode, meta, now=t0)
                self._crop_rec = CropRecorder() if (mode == "guided" and self.engine.config.get("rec_save_crops")) else None
                self._rec_adv_seen = ui.rec_advance
        if self._guided is not None:
            if not self.paused:
                self._pause()
            self._guided.paused = getattr(ui, "rec_paused", False)
            if ui.rec_advance != self._rec_adv_seen:
                self._rec_adv_seen = ui.rec_advance
                self._guided.advance(t0)
            available = True
            if self._guided.plan:
                available = bool(detections and getattr(detections[0], "world", None))
            if self._guided.update(t0, available=available) and self._rec is not None:
                self._rec.event(t0, "set_done", {"set": self._guided.index - 1})
        return self._guided is not None

    def _discard_recording(self, t0: float) -> None:
        if self._rec is None:
            return
        rec = self._rec
        rec.discard()
        self._rec, self._guided, self._crop_rec = None, None, None
        self._rec_mode = "off"
        self._last_pipeline_result = {
            "path": "",
            "status": "discarded",
            "score": 0.0,
            "retrain_eligible": False,
            "retrain_started": False,
            "message": f"녹화를 취소하고 버렸습니다 ({rec.frames}프레임 삭제됨)",
        }
        self._rec_note = self._last_pipeline_result["message"]
        self._rec_note_until = t0 + 6.0
        self._say(self._rec_note, 4.0)

    def _stop_recording(self, t0: float) -> None:
        if self._rec is None:
            return
        rec = self._rec
        path = rec.close()
        self._rec, self._guided = None, None
        crops, self._crop_rec = self._crop_rec, None
        kept = ", ".join(f"{k} {v}" for k, v in sorted(rec.counts.items()))
        base_note = f"녹화 저장: {path.name} ({rec.frames}프레임: {kept}, 표시 {rec.marks})"
        # The quality check, cleaning and augmentation of the finished file took ~4 s on this thread and froze the camera loop
        # (loop_max 4215 ms, 111 camera frames lost, 2026-11-09): they run on their own thread now. The screen shows
        # "품질 검사 중" until they are done.
        self._pipeline_token = getattr(self, "_pipeline_token", 0) + 1
        token = self._pipeline_token
        self._last_pipeline_result = {
            "path": str(path), "status": "pending", "score": None, "frames": rec.frames, "marks": rec.marks,
            "retrain_eligible": False, "retrain_started": False, "message": "품질 검사 중…",
        }
        self._rec_note = f"{base_note} · 품질 검사 중…"
        self._rec_note_until = t0 + 600.0
        self._say(self._rec_note, 6.0)
        threading.Thread(target=self._finish_recording_pipeline, args=(token, path, rec.frames, rec.marks, base_note, crops),
                         daemon=True).start()

    def _finish_recording_pipeline(self, token: int, path, frames: int, marks, base_note: str, crops=None) -> None:
        if crops is not None and len(crops):
            try:
                crops.save(crops_path(path))          # saved here, not on the camera thread: it compresses a few MB
            except Exception:
                pass
        try:
            from src.training_center import run_post_recording_pipeline, set_recording_selected
            pipeline_res = run_post_recording_pipeline(
                path,
                self.engine.config,
                training_job=getattr(self, "training_job", None),
            )
            pipeline_res["frames"] = frames
            pipeline_res["marks"] = marks
            if pipeline_res.get("status") == "fail":
                set_recording_selected(path.name, False, path.parent)
            q_msg = pipeline_res.get("message", "")
            note = f"{base_note} · {q_msg}" if q_msg else base_note
        except Exception:
            # the check itself failed: that is not a pass (it used to be shown as "pass", score 1.0)
            base_note = f"{base_note} · 품질 검사를 하지 못했어요"
            pipeline_res = {
                "path": str(path),
                "status": "warning",
                "score": None,
                "frames": frames,
                "marks": marks,
                "retrain_eligible": False,
                "retrain_started": False,
                "message": base_note,
            }
            note = base_note
        if token != getattr(self, "_pipeline_token", 0):
            return                      # another recording was saved meanwhile: this result is stale
        self._last_pipeline_result = pipeline_res
        self._rec_note = note
        self._rec_note_until = time.perf_counter() + 8.0
        self._say(note, 6.0)

    def _record_frame(self, t0: float, detections, result, frame=None) -> None:
        if self._rec is None:
            return
        hand = detections[0] if detections else None
        if self._crop_rec is not None and hand is not None and frame is not None:
            self._crop_rec.add(self._rec.frames, frame, hand.landmarks)      # the picture of the frame about to be written
        pose = self._pose_raw[1] if self._pose_raw is not None and t0 - self._pose_raw[0] < 0.25 else None
        if self._guided is not None:
            label = self._guided.label(t0)
        elif getattr(self.ui, "rec_paused", False):
            label = {"target": "idle", "phase": "paused"}
        else:
            label = {"target": "none", "phase": "use"}
        rule = None
        if result is not None:
            dd = result.distances or {}
            rule = {"action": getattr(self, "_rule_action", result.action), "gesture": result.active_gesture,
                    "gated": result.action if self.gate is not None and ui_gate_on(self) else None,
                    "dpi": round(float(getattr(result, "dpi_multiplier", 1.0) or 1.0), 2),
                    "curl": round(float(self.engine._curl_s), 3) if self.engine._curl_s is not None else None,
                    "size": round(float(dd.get("hand_size", 0.0)), 4),
                    "mode": getattr(result, "hand_shape_mode", "normal"),
                    "clutch": getattr(self.mouse, "clutch_state", "inactive"),
                    "return": getattr(self.mouse, "return_state", "none")}
            if self._hand_follower.followed:
                rule["followed"] = True      # kept only by HandFollower: lets a recording measure what the rule adds
            if self._guided is not None:
                from types import SimpleNamespace
                from src.gesture_catalog import BY_KEY
                from src.capture_rules import detect_interfering_signals
                gtask = BY_KEY.get(label.get("task"))
                raw_action = rule["action"]
                raw_gesture = result.active_gesture
                observed = SimpleNamespace(action=raw_action, active_gesture=raw_gesture)
                rule["evaluated"] = True
                engine_raw = getattr(self, "_engine_raw", None)
                if engine_raw is not None:
                    # the real engine reading: rule.action / raw_action are replaced by the target in `do` (see docs/far-hand-loss.md)
                    rule["engine_action"], rule["engine_gesture"] = engine_raw
                rule["raw_action"] = raw_action
                rule["raw_gesture"] = raw_gesture
                obs_targets = getattr(self, "_guided_obs_targets", None)
                if obs_targets is None:
                    obs_targets = self._capture_rules.observe(t0, hand, observed, label)
                if gtask is not None and not gtask.free and gtask.label != "none":
                    lm_hand = hand.landmarks if hand else None
                    # Interference = what the engine itself read besides the target. `observed` holds the target action that
                    # guided capture put in (see above), which can never match another gesture, so use the real reading.
                    engine_seen = (SimpleNamespace(action=engine_raw[0], active_gesture=engine_raw[1])
                                   if engine_raw is not None else observed)
                    rule["interfering_targets"] = (
                        detect_interfering_signals(lm_hand, engine_seen, gtask.key)
                        if label.get("phase") == "do"
                        else []
                    )
                    if label.get("phase") == "do":
                        rule["targets"] = [t for t in obs_targets if t == gtask.key]
                        if gtask.key in rule["targets"]:
                            rule["action"] = gtask.action or gtask.label
                            rule["gesture"] = (
                                f"{gtask.label}_pinch"
                                if gtask.label in ("thumb_index", "thumb_middle", "thumb_ring", "thumb_pinky", "thumb_index_middle")
                                else gtask.label
                            )
                        else:
                            rule["action"] = "none"
                            rule["gesture"] = "none"
                    else:
                        rule["targets"] = []
                        rule["action"] = "none"
                        rule["gesture"] = "none"
                elif label.get("phase") in ("prep", "rest", "waiting", "paused"):
                    rule["interfering_targets"] = []
                    rule["targets"] = []
                    rule["action"] = "none"
                    rule["gesture"] = "none"
                else:
                    rule["interfering_targets"] = []
                    rule["targets"] = list(obs_targets)
        self._rec.frame(t0, hand, pose, label, rule, self.mouse.last_screen_pos)

    def _rec_view(self, t0: float) -> Optional[Dict[str, Any]]:
        if self._rec is None:
            if t0 < self._rec_note_until:
                view: Dict[str, Any] = {
                    "mode": "off", "prompt": "", "sub": self._rec_note, "progress": 0.0, "overall": 0.0,
                    "waiting": False, "frames": 0,
                }
                if self._last_pipeline_result is not None:
                    view["quality_status"] = self._last_pipeline_result.get("status")
                    view["quality_score"] = self._last_pipeline_result.get("score")
                    view["quality_message"] = self._last_pipeline_result.get("message")
                    view["retrain_eligible"] = bool(self._last_pipeline_result.get("retrain_eligible"))
                    view["retrain_started"] = bool(self._last_pipeline_result.get("retrain_started"))
                    view["file"] = Path(self._last_pipeline_result.get("path", "")).name
                return view
            return None
        if self._guided is not None:
            v = dict(self._guided.view(t0))
        else:
            free_paused = bool(getattr(self.ui, "rec_paused", False))
            v = {
                "prompt": (
                    "일시정지 · 팔을 올리고 자세를 잡은 뒤 스페이스바(또는 [계속])를 눌러 녹화를 시작하세요"
                    if free_paused
                    else "평소처럼 쓰세요 (스페이스바: 일시정지 · 이상하면 F)"
                ),
                "sub": "",
                "progress": 0.0,
                "overall": 0.0,
                "waiting": False,
                "paused": free_paused,
            }
        v.update(mode=self._rec_mode, frames=self._rec.frames, file=self._rec.path.name, marks=self._rec.marks)
        v["sub"] = (v.get("sub") or "") + f"  ·  {self._rec.frames}프레임 녹화 중"
        return v

    def _admin_window_in_front(self, t0: float) -> bool:
        """Task Manager and other admin windows drop this app's input unless the app is admin too (src/elevation.py).
        Asked twice a second: it is a few Windows calls."""
        if t0 - self._admin_checked_t >= 0.5:
            self._admin_checked_t = t0
            try:
                from src.elevation import blocked_by_elevated_window
                self._admin_blocked = bool(blocked_by_elevated_window())
            except Exception:
                self._admin_blocked = False
        return self._admin_blocked

    @staticmethod
    def _hand_width_fraction(landmarks, width: int, height: int) -> float:
        """Hand size as in docs/far-hand-loss.md: wrist to the farthest finger base (5, 9, 13, 17), in frame widths."""
        wrist = landmarks[0]
        aspect = height / max(1, width)
        return max(math.hypot(landmarks[k].x - wrist.x, (landmarks[k].y - wrist.y) * aspect) for k in (5, 9, 13, 17))

    def _palm_axes(self, t0: float, ui, detections) -> bool:
        """Cursor direction wizard for the palm / fist cursor (src/palm_axes.py). True while it records (no cursor moves)."""
        start, cancel, reset = ui.palm_axes_request, ui.palm_axes_cancel, ui.palm_axes_reset
        seen_start, seen_cancel, seen_reset = self._palm_seen
        self._palm_seen = (start, cancel, reset)
        ready = ui.stage == "main" and not ui.use_index_pointer and self.test is None
        if reset != seen_reset:
            self._palm_cal = None
            self.engine.config.pop("palm_axes", None)
            self.engine.save_config()
            self._palm_note, self._palm_note_until = "커서 방향 보정을 지웠어요", t0 + 3.0
        if cancel != seen_cancel or not ready:
            self._palm_cal = None
        if start != seen_start and ready:
            self.mouse.release_all()
            self.engine.reset()
            self._palm_cal = PalmAxesCalibration(self.engine.config, start=t0)
        cal = self._palm_cal
        if cal is None:
            return False
        palm = self.engine.palm_anchor_point(detections[0].landmarks) if detections else None
        if cal.update(t0, palm, self.mouse.frame_aspect):
            self._palm_cal = None
            if cal.axes is not None:
                self.engine.config["palm_axes"] = [round(a, 4) for a in cal.axes]
                self.engine.save_config()
                self._palm_note = "커서 방향 보정 완료. " + cal.error
            else:
                self._palm_note = "커서 방향 보정 실패: " + cal.error
            self._palm_note_until = t0 + 5.0
            self.mouse.release_all()
        return self._palm_cal is not None

    def _palm_view(self, t0: float) -> Tuple[bool, str, float, str, bool]:
        saved = valid_axes(self.engine.config.get("palm_axes"))
        if self._palm_cal is not None:
            prompt, progress, note = self._palm_cal.view(t0)
            return True, prompt, progress, note, saved
        return False, self._palm_note if t0 < self._palm_note_until else "", 0.0, "", saved

    # ------------------------------------------------------------------ drawing mode (src/draw_mode.py)
    def _set_draw(self, on: bool) -> None:
        if on == self.draw.active:
            return
        self.draw.set_active(on)
        self.mouse.release_all()
        self.engine.reset()
        self._say("그리기 모드 켜짐 · 검지를 펴서 그리고, 손을 펴거나 쥐면 끝" if on else "그리기 모드 꺼짐", 2.0)

    def _run_sequence(self, key: str) -> None:
        self._run_gesture_key(key, self.seq_store.items.get(key, {}).get("name", key), "이어 하기")

    def _run_gesture_key(self, key: str, name: str, kind: str) -> None:
        reg = get_action_registry()
        fg = self.mouse.foreground_info() if not self.mouse.dry_run else {}
        resolved = reg.resolve_effective_action(key, fg)
        ok, badge = self._run_resolved(reg, resolved)
        self._recent_action, self._recent_until = "none", time.perf_counter() + .8
        self._say(f"{kind} [{name}] → {badge}", 1.5)

    def _run_resolved(self, reg, resolved):
        """Run a binding; the mode switches the registry cannot reach are done here."""
        if resolved.get("action_type") == "mouse_action" and resolved.get("action_value") == "toggle_draw":
            self._set_draw(not self.draw.active)
            return True, "그리기 모드 " + ("켜짐" if self.draw.active else "꺼짐")
        return reg.execute_resolved_action(resolved, dry_run=self.mouse.dry_run, mouse_controller=self.mouse)

    def _draw_step(self, t0: float, ui, detections, fw: int, fh: int) -> None:
        box = ui.virtual_box if valid_box(ui.virtual_box) else DEFAULT_BOX
        m = self.mouse.absolute_monitor
        if not detections:
            judged = self.draw.lost(t0)
        else:
            judged = self.draw.step(t0, detections[0].landmarks, box, fw, fh, aspect=m.width / max(1, m.height))
        if judged is None:
            return
        if not judged["ok"]:
            self._say(f"그림을 알아보지 못했어요 ({judged['why']})", 1.2)
            return
        if self.paused:
            self._say(f"그림 [{judged['name']}] · 일시정지 중이라 실행 안 함", 1.2)
            return
        reg = get_action_registry()
        fg = self.mouse.foreground_info() if not self.mouse.dry_run else {}
        resolved = reg.resolve_effective_action(judged["key"], fg)
        ok, badge = self._run_resolved(reg, resolved)
        if self.draw.last is not None:
            self.draw.last["action"] = badge
        self._say(f"그림 [{judged['name']}] → {badge}", 1.5)

    def _pose_probe_view(self, t0: float):
        self.pose_probe.finish_if_due(t0)       # also when the hand is gone (too few frames -> "failed")
        return self.pose_probe.view(t0)

    def _sign_override(self, key: str):
        """A sign with its own detector (V = control on/off, thumbs-up = motion mode): its binding if the user changed
        it ("off" when switched off), else None (the sign does its built-in job)."""
        try:
            reg = get_action_registry()
            b = reg.get_binding(key)
            if not b.get("enabled", True):
                return "off"
            fg = self.mouse.foreground_info() if not self.mouse.dry_run else {}
            resolved = reg.resolve_effective_action(key, fg)
        except Exception:
            return None
        default = DEFAULT_GESTURE_BINDINGS.get(key, {})
        if not resolved.get("matched_app") and (resolved.get("action_type"), resolved.get("action_value")) == \
                (default.get("action_type"), default.get("action_value")):
            return None
        return resolved

    def _binding_on(self, key: str, t0: float) -> bool:
        """Whether the action screen's binding for `key` is switched on (read again at most twice a second)."""
        cache = getattr(self, "_binding_cache", None)
        if cache is None:
            cache = self._binding_cache = {}
        hit = cache.get(key)
        if hit is None or t0 - hit[0] > .5:
            try:
                from src.action_bindings import get_action_registry
                on = bool(get_action_registry().get_binding(key).get("enabled", True))
            except Exception:
                on = True
            hit = cache[key] = (t0, on)
        return hit[1]

    def _motion(self, t0: float, ui, detections) -> bool:
        """Run motion mode for this frame. True while it is on (the pinch controls are then suspended)."""
        ready = ui.stage == "main" and not self.paused and not ui.calibrating
        request = ""
        if ui.motion_request != self._motion_seen:
            self._motion_seen = ui.motion_request
            request = "toggle" if ready else ""
        if not ready:
            if self.motion.active:
                self.motion.update(t0, None, request="off")
            return False
        lm = detections[0].landmarks if detections else None
        # the thumbs-up binding switched off on the action screen: the sign no longer turns motion mode on
        sign_off = not self.motion.active and not request and not self._binding_on("thumbs_up", t0)
        blocked = self._motion_blocked(t0) or sign_off or (not self.motion.active and not request and not self._intent("thumbs_up", t0))
        event = self.motion.update(t0, lm, request=request, blocked=blocked)
        if self._motion_last.startswith("창 닫기?") and self.motion.close_pending(t0) <= 0:
            self._motion_last = ""            # the question ran out
        if event is not None:
            custom = self._sign_override("thumbs_up") if event.kind == "enter" and not request else None
            if custom is not None:
                # the thumbs-up's binding was changed: run it instead of motion mode (the button still opens motion mode)
                self.motion.update(t0, None, request="off")
                self.mouse.release_all()
                if custom != "off":
                    ok, badge = self._run_resolved(get_action_registry(), custom)
                    self._say(f"엄지 위로 → {badge}", 1.5)
                return False
            if event.kind == "enter":
                self.mouse.release_all()
                self.engine.reset()
                self.pointer.reset()
                self._motion_last = ""
                self._say("모션 모드 켜짐: 손을 펴고 좌우로 쓸어 보세요", 3.0)
            elif event.kind == "exit":
                self._say(f"모션 모드 꺼짐 ({event.info.get('why')})", 2.0)
            elif event.kind == "close_ask":
                self._motion_last = "창 닫기? 한 번 더 아래로"
                self._say("창을 닫을까요? 3초 안에 손을 올렸다 한 번 더 아래로 쓸면 닫습니다", 3.0)
            elif event.kind == "swipe":
                from src.gesture_catalog import BY_KEY, MOTION_TARGETS
                target_key = MOTION_TARGETS.get(event.action, event.action)
                confirmed = self._intent(target_key, t0)
                sent = confirmed and self.mouse.send_motion_action(event.action)
                if sent:
                    if event.action in ACTION_LABELS:
                        self._motion_last = ACTION_LABELS[event.action]
                    elif target_key in BY_KEY:
                        self._motion_last = BY_KEY[target_key].name
                    else:
                        self._motion_last = event.action
                else:
                    self._motion_last = "TCN 동작 확인 안 됨" if not confirmed else "닫을 수 없는 창"
                self._say(f"모션: {self._motion_last}", 1.5)
            if self.failures.trigger(t0, f"motion_{event.kind}", note=json.dumps(event.info, ensure_ascii=False)):
                pass
        return self.motion.active

    def _gesture_fields(self, t0, index_mode, result, frame_out) -> Dict[str, Any]:
        """Click / drag state of this frame, so a misfired click or drag can be read back afterwards."""
        action = (frame_out.action if (index_mode and frame_out is not None) else getattr(result, "action", None))
        g = self.engine
        since = getattr(g, "_secondary_since", {}) or {}
        held = {f: round((t0 - v) * 1000) for f, v in since.items()}
        d = getattr(result, "distances", None) or {}
        fields: Dict[str, Any] = {
            "act": action if action not in (None, "none") else None,
            "gest": getattr(result, "active_gesture", None) if not index_mode else None,
            "held_ms": held or None,
            "long": bool(getattr(g, "_long_fired", {}).get("middle")) or None,
            "browse": (list(self.mouse.browse_target) if getattr(self.mouse, "_browse_centered", False)
                       and self.mouse.browse_target else None),   # browsing: [x, y, "region:..."|"window:..."] it is centred on
            "dk": getattr(g, "last_desktop", None),            # show-desktop posture: tuck, hold, activity
            "motion": True if self.motion.active else None,
            "mo_miss": self.motion.last_miss if self.motion.active else None,   # biggest refused stroke and why
            "sign": [self.close_sign.last_pose, round(self.close_sign.progress(t0), 2), round(self.toggle_sign.progress(t0), 2)],   # rock sign pose?, its hold progress, V sign progress
            "pk": getattr(g, "last_pinky", None),               # thumb+pinky acceptance conditions
            "hold": getattr(g, "last_hold", None),   # thumb+index: (hold dist, release thr, tip dist)
            "d": {k: round(float(v), 3) for k, v in d.items()} if isinstance(d, dict) and d else None,
            "click_style": ui_style(self.ui) if index_mode else None,
            "clutch_state": getattr(self.mouse, "clutch_state", "inactive"),
            "return_state": getattr(self.mouse, "return_state", "none"),
            "hand_shape_mode": getattr(result, "hand_shape_mode", "normal") if not index_mode else None,
        }
        # Win+D fired: keep the seconds before it (a firing used to leave no record unless another event was near)
        if action == "show_desktop":
            if self.failures.trigger(t0, "desktop", note=json.dumps(getattr(g, "last_desktop", None))):
                self._say("기록: 바탕화면 보기")
        # a right / left click fired: keep the seconds before it too (a false click used to leave no record)
        if action in ("right_click", "left_click") and not self.paused:
            if self.failures.trigger(t0, "click", note=f"{action} {json.dumps(d, ensure_ascii=False) if isinstance(d, dict) else ''}"):
                self._say("기록: 클릭")
        # a drag starting (button down) is an event worth keeping with the seconds before it
        dragging = action == "drag_hold"
        if dragging and not self._was_dragging and not self.paused:
            if self.failures.trigger(t0, "drag_started", note=f"held_ms={held}"):
                self._say("기록: 드래그 시작")
        # a drag ending: keep the last seconds too (a stroke longer than the 3 s before drag_started was lost)
        if dragging and not self._was_dragging:
            self._drag_began = t0
        if self._was_dragging and not dragging and not self.paused and t0 - getattr(self, "_drag_began", t0) >= 0.3:
            if self.failures.trigger(t0, "drag_ended", note=f"seconds={t0 - self._drag_began:.2f}"):
                self._say("기록: 드래그 끝", 1.0)
        self._was_dragging = dragging
        # scroll inertia: keep the numbers of every launch / refusal (raw vs used speed, axis, directions)
        li = getattr(self.mouse, "last_inertia", None)
        if li is not None and li is not self._seen_inertia:
            self._seen_inertia = li
            fields["inertia"] = li
            if self.failures.trigger(t0, "inertia", note=json.dumps(li)):
                self._say("기록: 관성 스크롤")
        fields["coast"] = bool(getattr(self.mouse, "scroll_coasting", False)) or None
        total = list(getattr(self.mouse, "scroll_total", [0, 0]))
        sent = [total[0] - self._last_scroll_total[0], total[1] - self._last_scroll_total[1]]
        self._last_scroll_total = total
        fields["wheel"] = sent if any(sent) else None          # units handed to the pump this frame
        pv, ph = getattr(self.mouse, "_pump", None).pending() if getattr(self.mouse, "_pump", None) else (0, 0)
        fields["wheel_pending"] = round(pv) if abs(pv) >= 1 else None
        return fields

    def _failures(self, t0, detections, roi, index_mode, ui, frame, result=None, frame_out=None) -> None:
        if not ui.calibrating:
            hand = detections[0] if detections else None
            kind = self.failures.record(
                t0, landmarks=hand.landmarks if hand else None, score=hand.score if hand else 0.0,
                moving=(self.pointer.latch.latched if index_mode else bool(getattr(result, "is_moving", False)))
                and not self.paused,
                crop=list(roi) if roi else None, cursor=list(self.mouse.last_screen_pos),
                speed=round(self.pointer.latch.speed, 3), mode="index" if index_mode else "palm",
                **self._gesture_fields(t0, index_mode, result, frame_out),
                brightness=self._metrics[1] if self._metrics else None,
                fps=round(self.camera.fps, 1), detect_ms=round(self.tracker.last_detect_ms, 1), paused=self.paused,
                path=self._det_path, raw=self._raw_hands, latched=bool(self.pointer.latch.latched),
                armed=bool(self.pointer.click_armed), trig=self.pointer.trigger.state,
                dropped=self.camera.dropped_frames, dt_ms=round((t0 - self._last_step_t) * 1000, 1)
                if self._last_step_t else None,
            )
            if kind:
                self._say(f"실패 기록: {kind}")
        saved = self.failures.poll(t0, {
            "anchor": ui.anchor, "anchor_joints": list(ui.anchor_joints), "click_style": ui.click_style, "hand_crop": ui.hand_crop,
            "frame_px": [frame.shape[1], frame.shape[0]], "camera_mode": list(self.camera.actual_mode() or ()),
            "tracker": self.tracker.options, "sensitivity_pct": ui.sensitivity,
            "control_hand": ui.control_hand, "dry_run": self.dry_run,
            "camera_source": str(self.camera.config.source), "mirror": bool(self.camera.config.mirror),
            "crop_params": {**{k: getattr(self.hand_crop, k) for k in ("scale", "min_px", "recenter", "resize", "max_hand_frac",
                                                                       "search_seconds", "collapse_ratio")},
                            "tracker": self.roi_tracker.kind, "track_seconds": self.roi_tracker.seconds},
            "config": {k: v for k, v in self.engine.config.items() if isinstance(v, (int, float, str, bool))},
        })
        if saved is not None:
            self._pending_failures = (self.failures.summary_text(), saved.name)
            self._pending_message = f"[Failure] saved {saved}"

    def _tracking(self, t0, detections, index_mode, ui) -> Optional[Dict[str, Any]]:
        test = self.test
        if test is None:
            return None
        pos = self.mouse.last_screen_pos
        test.update(
            t0, pos,
            hand=bool(detections),
            active=(index_mode or True) and not self.paused and not ui.calibrating,
            fps=self.camera.fps,
            detect_ms=self.tracker.last_detect_ms,
            clutch_state=getattr(self.mouse, "clutch_state", "inactive"),
            return_state=getattr(self.mouse, "return_state", "none"),
            hand_shape_mode=getattr(self.engine, "_shape_mode", "normal"),
        )
        if test.done and not getattr(test, "saved", False):
            test.saved = True
            primary = self.mouse.screens.primary
            scheme = str(self.engine.config.get("movement_scheme", "legacy_shape"))
            ret_supp = str(self.engine.config.get("return_suppression", "off"))
            run = {
                "anchor": ui.anchor, "anchor_joints": list(ui.anchor_joints), "sensitivity_pct": ui.sensitivity,
                "movement_scheme": scheme, "return_suppression": ret_supp,
                "condition": f"{scheme}+{ret_supp}",
                "direction_calibrated": ui.axes is not None, "control_hand": ui.control_hand,
                "camera_source": self.camera.config.source, "monitor_px": f"{primary.width}x{primary.height}",
                "monitors": len(self.mouse.screens), "dry_run": self.dry_run,
                "camera_mode": "x".join(map(str, self.camera.actual_mode() or ())) or "unknown",
                "hand_crop": ui.hand_crop,
                **{f"tracker_{k}": v for k, v in self.tracker.options.items()},
            }
            text = test.summary_text()
            try:
                saved = test.save_csv(self.log_dir, run)
                text += f" · {saved.name}"
            except OSError as exc:
                text += f" · 저장 실패: {exc}"
            test.result_text = text
            self._pending_recent = result_line({**run, **test.summary()}, time.strftime("%m/%d %H:%M"))
            self._pending_message = f"[Tracking] {text}"
        view = tracking_view(test, pos)
        if test.done:
            view["summary"] = getattr(test, "result_text", view["summary"])
        return view
