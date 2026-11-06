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

from src.failure_log import FailureRecorder
from src.close_sign import CloseSign
from src.toggle_sign import ToggleSign
from src.motion_mode import ACTION_LABELS, MotionMode
from src.palm_axes import PalmAxesCalibration, valid_axes
from src.elbow_pivot import ElbowPivot
from src.pose_tracker import DEFAULT_POSE_MODEL, PoseTracker, pick_arm
from src.guided_recording import GuidedSession, tasks_for
from src.recorder import SessionRecorder
from src.gesture_gate import DEFAULT_MODEL as GATE_MODEL, GestureGate, load_predictor
from src.gesture_engine import GestureEngine, GestureResult
from src.hand_crop import HandCrop
from src.hand_selection import selected_detection
from src.hand_tracker import HandTracker, pinch_guides
from src.pointer_engine import PointerEngine, PointerSettings
from src.pointer_workspace import FINGERTIPS
from src.tracking_test import TrackingTest, result_line

ONE_SHOT = ("left_click", "right_click", "drag_toggle", "alt_tab", "show_desktop", "task_switcher")


@dataclass(frozen=True)
class UiState:
    """What the UI thread decided (replaced as a whole, read once per frame)."""
    stage: str = "splash"               # splash | welcome | capture | main
    control_hand: str = ""              # "Left" | "Right" | ""
    use_index_pointer: bool = True
    click_style: str = "pinch"
    anchor: str = "index"
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
                 log_dir: Optional[Path] = None) -> None:
        self.camera, self.tracker, self.engine, self.mouse = camera, tracker, engine, mouse
        self.perf_log = perf_log
        self.dry_run = dry_run
        self.max_frames = int(max_frames)
        self._draw_cursor_anchor = draw_cursor_anchor
        self._draw_recording = draw_recording
        self.log_dir = log_dir or Path(__file__).resolve().parents[1] / "logs"

        self.pointer = PointerEngine(GestureEngine(config_path=config_path))
        self.hand_crop = HandCrop()
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
        self._applied_profile = engine.profile_name
        self.motion = MotionMode(engine.config)
        self.close_sign = CloseSign(engine.config)
        self.toggle_sign = ToggleSign(engine.config)
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
        self.mouse.close_switcher(confirm=False)
        self.mouse.release_all()
        self.engine.reset()
        self._recent_until = 0.0

    def _drain(self, now: float) -> None:
        while True:
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                return
            kind = cmd[0]
            if kind == "pause":
                self._pause()
            elif kind == "tracking_start" and self.test is None:
                self.mouse.release_all()
                self.mouse.warp_to(*self.mouse.screens.primary.center)
                self.test = TrackingTest(self.mouse.screens.primary)
            elif kind == "tracking_close":
                self.test = None
                self._pause()
            elif kind == "failure_mark":
                if self._rec is not None:
                    self._rec.event(now, "mark")
                if self.failures.trigger(now, "manual"):
                    self._say("실패 기록: 앞뒤 3초를 저장합니다")
            elif kind == "rec_discard":
                self._discard_recording(now)
            elif kind == "key":
                self._key(cmd[1], now)

    def _key(self, key: int, now: float) -> None:
        engine, mouse, camera = self.engine, self.mouse, self.camera
        if key in (27, ord(" ")):
            was_paused = self.paused
            self._pause()
            self.paused = key == 27 or not was_paused or self.ui.stage != "main"
            self._say("Mouse Control PAUSED" if self.paused else "Mouse Control RESUMED")
        elif key in (ord("m"), ord("M")):
            new = "relative" if str(engine.config.get("movement_mode", "relative")).lower() == "absolute" else "absolute"
            engine.set_movement_mode(new)
            mouse.release_all()
            self._say(f"Switched Movement Mode -> {new.upper()}")
        elif key in (ord("f"), ord("F")):
            if self._rec is not None:
                self._rec.event(now, "mark")
            if self.failures.trigger(now, "manual"):
                self._say("실패 기록: 앞뒤 3초를 저장합니다")
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
            time.sleep(.03)
            return

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
            self._det_path = "crop" if roi is not None else "full"
            if roi is not None and not detections:
                self.hand_crop.reset()
                roi = None
                self._det_path = "crop_miss>full"
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
        else:
            chosen = selected_detection(detections, ui.control_hand, camera.config.mirror)
            detections = [chosen] if chosen is not None else []
        if ui.hand_crop and detections:
            self.hand_crop.update(detections[0].landmarks, frame.shape[1], frame.shape[0])
        else:
            self.hand_crop.reset()
        palm_calibrating = self._palm_axes(t0, ui, detections)
        guided_recording = self._recording_mode(t0, ui, detections)
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
        elif detections and not index_mode and not motion_active and not close_hold and not toggle_hold and not palm_calibrating:
            hand = detections[0]
            result = engine.process(hand.landmarks)
            rule_action = result.action
            if gate is not None:
                result = gate.filter(t0, result)
                if gate.last_note:
                    self._say(gate.last_note, 1.2)
                    gate.last_note = ""
            self._rule_action = rule_action
            if not self.paused and not guided_recording:      # guided recording: rules judge, the mouse stays still
                mouse.apply(result, engine.config)
                if result.action in ONE_SHOT:
                    self._recent_action, self._recent_until = result.action, time.perf_counter() + .8
            HandTracker.draw_hand(frame, hand.landmarks, pinch_guides(
                result.distances, engine.config, engine.pinched_fingers,
                action=result.action, gesture=result.active_gesture))
            if self._draw_cursor_anchor:
                self._draw_cursor_anchor(frame, hand.landmarks, engine.palm_anchor_point(hand.landmarks),
                                         result.is_moving)
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
        self._record_frame(t0, detections, result)
        self._last_step_t = t0
        tracking = self._tracking(t0, detections, index_mode, ui)

        now = time.perf_counter()
        status = self._status if now <= self._status_until else ""
        action = self._recent_action if now < self._recent_until else result.action
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
        )
        self._pending_failures = self._pending_recent = None
        self._pending_message = ""
        with self._cond:
            self._snap = snap
            self._cond.notify_all()
        if self.perf_log is not None:
            self.perf_log.frame(tracker.last_detect_ms if should_detect else None, (time.perf_counter() - t0) * 1000.0)
            self.perf_log.tick(camera.fps, camera.dropped_frames)

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
        blocked = self.paused and not self._intent("v_sign", t0)
        if not self.paused:
            quiet = self.toggle_sign.cfg("toggle_sign_quiet")
            for g in (self.engine, self.pointer.gestures):
                last = max(g._index_released_at, g._scroll_released_at, g._pinky_fired_at,
                           max(g._last_trigger_times.values(), default=-1e9))
                if g._index_pinched or g._secondary_since or (t0 - last) < quiet:
                    blocked = True
            blocked = blocked or bool(self.mouse.is_dragging or self.mouse.scroll_coasting)
        was_holding = self.toggle_sign.holding(t0)
        event = self.toggle_sign.update(t0, detections[0].landmarks, blocked=blocked)
        if event is not None:
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

    def _gate_for(self, ui) -> Optional[GestureGate]:
        """The intent checker when it is switched on (and the model loads), else None."""
        version = getattr(ui, "model_version", 0)
        reload = version != getattr(self, "_model_version", 0)
        if reload:
            self._model_version = version
            self._gate_failed = False
        if not ui.tcn_gate or self._gate_failed:
            return None
        if self.gate is None or reload:
            try:
                predict, info = load_predictor(GATE_MODEL)
                replacement = GestureGate(predict, info, self.engine.config)
                self.mouse.release_all()
                self.engine.reset()
                self.pointer.reset()
                self.gate = replacement
                self._say(f"손짓 확인(TCN) 켜짐: 학습된 {len(info['classes']) - 1}종 동작을 확인해요", 3.0)
            except Exception as exc:
                self._gate_failed = self.gate is None
                self._say(f"손짓 확인(TCN)을 켜지 못했어요: {exc}", 5.0)
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
        self._rec, self._guided = None, None
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
        kept = ", ".join(f"{k} {v}" for k, v in sorted(rec.counts.items()))
        base_note = f"녹화 저장: {path.name} ({rec.frames}프레임: {kept}, 표시 {rec.marks})"
        try:
            from src.training_center import run_post_recording_pipeline, set_recording_selected
            pipeline_res = run_post_recording_pipeline(
                path,
                self.engine.config,
                training_job=getattr(self, "training_job", None),
            )
            pipeline_res["frames"] = rec.frames
            pipeline_res["marks"] = rec.marks
            if pipeline_res.get("status") == "fail":
                set_recording_selected(path.name, False, path.parent)
            self._last_pipeline_result = pipeline_res
            q_msg = pipeline_res.get("message", "")
            self._rec_note = f"{base_note} · {q_msg}" if q_msg else base_note
        except Exception:
            self._last_pipeline_result = {
                "path": str(path),
                "status": "pass",
                "score": 1.0,
                "frames": rec.frames,
                "marks": rec.marks,
                "retrain_eligible": False,
                "retrain_started": False,
                "message": base_note,
            }
            self._rec_note = base_note
        self._rec_note_until = t0 + 8.0
        self._say(self._rec_note, 6.0)

    def _record_frame(self, t0: float, detections, result) -> None:
        if self._rec is None:
            return
        hand = detections[0] if detections else None
        pose = self._pose_raw[1] if self._pose_raw is not None and t0 - self._pose_raw[0] < 0.25 else None
        label = self._guided.label(t0) if self._guided is not None else {"target": "none", "phase": "use"}
        rule = None
        if result is not None:
            dd = result.distances or {}
            rule = {"action": getattr(self, "_rule_action", result.action), "gesture": result.active_gesture,
                    "gated": result.action if self.gate is not None and ui_gate_on(self) else None,
                    "dpi": round(float(getattr(result, "dpi_multiplier", 1.0) or 1.0), 2),
                    "curl": round(float(self.engine._curl_s), 3) if self.engine._curl_s is not None else None,
                    "size": round(float(dd.get("hand_size", 0.0)), 4)}
            if self._guided is not None:
                from types import SimpleNamespace
                observed = SimpleNamespace(action=rule["action"], active_gesture=result.active_gesture)
                rule["evaluated"] = True
                rule["targets"] = self._capture_rules.observe(t0, hand, observed, label)
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
            v = {"prompt": "평소처럼 쓰세요 · 이상하면 F", "sub": "", "progress": 0.0, "overall": 0.0, "waiting": False}
        v.update(mode=self._rec_mode, frames=self._rec.frames, file=self._rec.path.name, marks=self._rec.marks)
        v["sub"] = (v.get("sub") or "") + f"  ·  {self._rec.frames}프레임 녹화 중"
        return v

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
        blocked = self._motion_blocked(t0) or (not self.motion.active and not request and not self._intent("thumbs_up", t0))
        event = self.motion.update(t0, lm, request=request, blocked=blocked)
        if self._motion_last.startswith("창 닫기?") and self.motion.close_pending(t0) <= 0:
            self._motion_last = ""            # the question ran out
        if event is not None:
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
                from src.gesture_catalog import MOTION_TARGETS
                confirmed = self._intent(MOTION_TARGETS.get(event.action, ""), t0)
                sent = confirmed and self.mouse.send_motion_action(event.action)
                self._motion_last = ACTION_LABELS.get(event.action, event.action) if sent else ("TCN 동작 확인 안 됨" if not confirmed else "닫을 수 없는 창")
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
            "anchor": ui.anchor, "click_style": ui.click_style, "hand_crop": ui.hand_crop,
            "frame_px": [frame.shape[1], frame.shape[0]], "camera_mode": list(self.camera.actual_mode() or ()),
            "tracker": self.tracker.options, "sensitivity_pct": ui.sensitivity,
            "control_hand": ui.control_hand, "dry_run": self.dry_run,
            "camera_source": str(self.camera.config.source), "mirror": bool(self.camera.config.mirror),
            "crop_params": {k: getattr(self.hand_crop, k) for k in ("scale", "min_px", "recenter", "resize", "max_hand_frac")},
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
        test.update(t0, pos, hand=bool(detections), active=index_mode and not self.paused and not ui.calibrating,
                    fps=self.camera.fps, detect_ms=self.tracker.last_detect_ms)
        if test.done and not getattr(test, "saved", False):
            test.saved = True
            primary = self.mouse.screens.primary
            run = {
                "anchor": ui.anchor, "sensitivity_pct": ui.sensitivity,
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
