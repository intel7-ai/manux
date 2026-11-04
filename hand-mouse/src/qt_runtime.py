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

import math
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2

from src.failure_log import FailureRecorder
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
                if self.failures.trigger(now, "manual"):
                    self._say("실패 기록: 앞뒤 3초를 저장합니다")
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
            pointer.frame_missing()
            time.sleep(.03)
            return

        should_detect = not camera.is_phone_web or (camera.phone_server is not None and camera.phone_server.has_client)
        roi = self.hand_crop.roi if ui.hand_crop else None
        if should_detect:
            detections = tracker.detect(frame, roi=roi)
            if roi is not None and not detections:
                self.hand_crop.reset()
                roi = None
                detections = tracker.detect(frame)
        else:
            detections = []
        all_detections = list(detections)

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
        if self._had_hand and not detections:
            mouse.close_switcher(confirm=False)
            mouse.release_all()
            engine.reset()
        self._had_hand = bool(detections)

        fh, fw = frame.shape[:2]
        mouse.frame_aspect = fh / max(1, fw)
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
        if index_mode:
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
        elif detections and not index_mode:
            hand = detections[0]
            result = engine.process(hand.landmarks)
            if not self.paused:
                mouse.apply(result, engine.config)
                if result.action in ONE_SHOT:
                    self._recent_action, self._recent_until = result.action, time.perf_counter() + .8
            HandTracker.draw_hand(frame, hand.landmarks, pinch_guides(
                result.distances, engine.config, engine.pinched_fingers,
                action=result.action, gesture=result.active_gesture))
            if self._draw_cursor_anchor:
                self._draw_cursor_anchor(frame, hand.landmarks, engine.palm_anchor_point(hand.landmarks),
                                         result.is_moving)
            if self._draw_recording:
                self._draw_recording(frame, mouse.recorded_path)
            if mouse.fling_count != self._last_fling:
                self._last_fling = mouse.fling_count
                self._say(f"FLING! ~{mouse.last_fling_distance:.0f}px", .8)
        elif not index_mode:
            result = engine.process(None)

        if roi is not None:
            cv2.rectangle(frame, roi[:2], roi[2:], (189, 229, 70), 2, cv2.LINE_AA)
        self._failures(t0, detections, roi, index_mode, ui, frame)
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
        )
        self._pending_failures = self._pending_recent = None
        self._pending_message = ""
        with self._cond:
            self._snap = snap
            self._cond.notify_all()
        if self.perf_log is not None:
            self.perf_log.frame(tracker.last_detect_ms if should_detect else None, (time.perf_counter() - t0) * 1000.0)
            self.perf_log.tick(camera.fps, camera.dropped_frames)

    def _failures(self, t0, detections, roi, index_mode, ui, frame) -> None:
        if index_mode and not ui.calibrating:
            hand = detections[0] if detections else None
            kind = self.failures.record(
                t0, landmarks=hand.landmarks if hand else None, score=hand.score if hand else 0.0,
                moving=self.pointer.latch.latched and not self.paused,
                crop=list(roi) if roi else None, cursor=list(self.mouse.last_screen_pos),
                speed=round(self.pointer.latch.speed, 3),
                brightness=self._metrics[1] if self._metrics else None,
                fps=round(self.camera.fps, 1), detect_ms=round(self.tracker.last_detect_ms, 1), paused=self.paused,
            )
            if kind:
                self._say(f"실패 기록: {kind}")
        saved = self.failures.poll(t0, {
            "anchor": ui.anchor, "click_style": ui.click_style, "hand_crop": ui.hand_crop,
            "frame_px": [frame.shape[1], frame.shape[0]], "camera_mode": list(self.camera.actual_mode() or ()),
            "tracker": self.tracker.options, "sensitivity_pct": ui.sensitivity,
            "control_hand": ui.control_hand, "dry_run": self.dry_run,
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
