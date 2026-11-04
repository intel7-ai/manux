"""
Zero-wait inference pipeline.

Camera read -> MediaPipe -> gesture engine -> mouse control run on their own thread, so the
cursor never waits for drawing or cv2.imshow. The main thread only renders the newest
published `Snapshot` (or does nothing at all while the HUD is hidden / in tray mode).

Everything that touches the camera, engine state or mouse runs on the pipeline thread;
other threads hand work over with `post()` / `set_paused()`.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple, Union

import numpy as np

from src.hand_tracker import pinch_guides

ONE_SHOT_ACTIONS = ("left_click", "right_click", "drag_toggle", "alt_tab", "show_desktop", "task_switcher")


@dataclass
class Snapshot:
    seq: int = 0
    frame: Optional[np.ndarray] = None  # None while nobody renders (HUD hidden)
    landmarks: Optional[list] = None
    guides: list = field(default_factory=list)
    palm_pt: Optional[Tuple[float, float]] = None
    recorded_path: list = field(default_factory=list)
    result: Any = None
    screen_pos: Tuple[int, int] = (0, 0)
    has_hand: bool = False
    should_detect: bool = False
    paused: bool = False
    fps: float = 0.0
    detect_ms: float = 0.0
    source: Union[int, str] = 0
    recent_action: str = ""
    fling_msg: str = ""
    grab_speed: float = 0.0
    switcher_open: bool = False


class InferencePipeline:
    def __init__(self, camera, tracker, engine, mouse, perf_log=None, max_frames: int = 0) -> None:
        self.camera = camera
        self.tracker = tracker
        self.engine = engine
        self.mouse = mouse
        self.perf_log = perf_log
        self.max_frames = int(max_frames)

        self.publish_frames = True  # False => skip frame copies (HUD hidden)
        self.frames = 0
        self.error: Optional[BaseException] = None

        self._paused = False
        self._was_paused = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cond = threading.Condition()
        self._snap: Optional[Snapshot] = None
        self._seen_seq = 0
        self._tasks: "queue.Queue[Callable[[], Optional[str]]]" = queue.Queue()
        self.messages: "queue.Queue[str]" = queue.Queue()

        self._recent_action = ""
        self._recent_until = 0.0
        self._fling_count = 0
        self._fling_msg = ""
        self._fling_until = 0.0

    # ------------------------------------------------------------ control (any thread)
    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, value: bool) -> None:
        self._paused = bool(value)

    def post(self, task: Callable[[], Optional[str]]) -> None:
        """Run `task` on the pipeline thread; a returned string becomes a status message."""
        self._tasks.put(task)

    @property
    def finished(self) -> bool:
        return self._stop.is_set() or (self.max_frames > 0 and self.frames >= self.max_frames) or (
            self._thread is not None and not self._thread.is_alive()
        )

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="inference", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def wait_snapshot(self, timeout: float = 0.05) -> Optional[Snapshot]:
        """Newest snapshot not rendered yet, or None on timeout."""
        with self._cond:
            self._cond.wait_for(lambda: self._snap is not None and self._snap.seq != self._seen_seq, timeout)
            if self._snap is None or self._snap.seq == self._seen_seq:
                return None
            self._seen_seq = self._snap.seq
            return self._snap

    def latest(self) -> Optional[Snapshot]:
        with self._cond:
            return self._snap

    # ------------------------------------------------------------ pipeline thread
    def _drain_tasks(self) -> None:
        while True:
            try:
                task = self._tasks.get_nowait()
            except queue.Empty:
                return
            try:
                msg = task()
            except Exception as exc:  # a bad command must not kill tracking
                msg = f"command failed: {exc}"
            if msg:
                self.messages.put(msg)

    def _run(self) -> None:
        try:
            self._loop()
        except BaseException as exc:  # surfaced to the main thread via .error
            self.error = exc
            raise
        finally:
            self._stop.set()
            with self._cond:
                self._cond.notify_all()

    def _loop(self) -> None:
        camera, tracker, engine, mouse = self.camera, self.tracker, self.engine, self.mouse
        while not self._stop.is_set():
            self._drain_tasks()
            ok, frame = camera.read()
            t0 = time.perf_counter()
            if not ok or frame is None:
                time.sleep(0.03)
                continue

            # Only run detection on a real camera feed (not on the phone QR waiting screen)
            should_detect = not camera.is_phone_web or (
                camera.phone_server is not None and camera.phone_server.has_client
            )
            detections = tracker.detect(frame) if should_detect else []

            fh, fw = frame.shape[:2]
            mouse.frame_aspect = fh / max(1, fw)

            paused = self._paused
            if paused and not self._was_paused:
                mouse.release_all()
            self._was_paused = paused

            landmarks = guides = palm_pt = None
            if detections:
                hand = detections[0]
                landmarks = hand.landmarks
                result = engine.process(landmarks)
                if not paused:
                    screen_pos = mouse.apply(result, engine.config)
                else:
                    screen_pos = mouse.last_screen_pos
                if result.action in ONE_SHOT_ACTIONS:
                    self._recent_action, self._recent_until = result.action, time.perf_counter() + 0.8
                guides = pinch_guides(
                    result.distances, engine.config, engine.pinched_fingers,
                    action=result.action, gesture=result.active_gesture,
                )
                palm_pt = engine.palm_anchor_point(landmarks)
                if mouse.fling_count != self._fling_count:
                    self._fling_count = mouse.fling_count
                    self._fling_msg = f"FLING! ~{mouse.last_fling_distance:.0f}px"
                    self._fling_until = time.perf_counter() + 0.8
            else:
                result = engine.process(None)
                screen_pos = mouse.hand_lost(engine.config)  # a thrown cursor keeps gliding

            now = time.perf_counter()
            self.frames += 1
            snap = Snapshot(
                seq=self.frames,
                frame=frame if self.publish_frames else None,
                landmarks=landmarks,
                guides=guides or [],
                palm_pt=palm_pt,
                recorded_path=list(mouse.recorded_path) if landmarks is not None else [],
                result=result,
                screen_pos=screen_pos,
                has_hand=landmarks is not None,
                should_detect=should_detect,
                paused=paused,
                fps=camera.fps,
                detect_ms=tracker.last_detect_ms,
                source=camera.phone_server.url if (camera.is_phone_web and camera.phone_server) else camera.config.source,
                recent_action=self._recent_action if now < self._recent_until else "",
                fling_msg=self._fling_msg if now < self._fling_until else "",
                grab_speed=mouse.grab_speed,
                switcher_open=mouse.switcher_open,
            )
            with self._cond:
                self._snap = snap
                self._cond.notify_all()

            if self.perf_log is not None:
                self.perf_log.frame(tracker.last_detect_ms if should_detect else None,
                                    (time.perf_counter() - t0) * 1000.0)
                self.perf_log.tick(camera.fps, camera.dropped_frames)
            if self.max_frames > 0 and self.frames >= self.max_frames:
                return
