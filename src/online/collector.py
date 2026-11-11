"""Collects candidate sequences during everyday use without holding up the camera loop.

The camera thread only copies the joints and runs the segmenter. Saving happens on a worker thread behind a short queue: when
the disk cannot keep up, segments are dropped and counted, the camera never waits. Everything saved here is unlabeled until a
person labels it; `suggested_label` is only what the rule engine thought.
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from src.gesture_catalog import BY_KEY
from src.online.samples import UNKNOWN, UNLABELED_UNKNOWN, USER_CONFIRMED, USER_CORRECTION, Sample, SampleStore
from src.online.segmenter import IDLE_ACTIONS, MotionSegmenter, Segment, SegmenterConfig

ACTION_TO_LABEL = {"right_click": "thumb_ring", "left_click": "thumb_middle", "drag_hold": "thumb_middle",
                   "scroll_grab": "thumb_index", "task_switcher": "thumb_pinky", "show_desktop": "palm_side"}
NONE_LABEL = "none"


def correction_choices() -> List[Dict[str, str]]:
    """Labels a person can pick: one entry per model label, "nothing" first."""
    seen, out = {NONE_LABEL}, [{"label": NONE_LABEL, "name": "아무 동작 아님"}]
    for task in BY_KEY.values():
        if not task.free and task.label not in seen and task.track == "static":
            seen.add(task.label)
            out.append({"label": task.label, "name": task.name})
    return out


def label_sample(store: SampleStore, sample_id: str, label: str) -> Sample:
    """What a person said this sequence is: a confirmation when the app guessed the same, otherwise a correction."""
    sample = store.get(sample_id)
    if sample is None:
        raise KeyError(sample_id)
    source = USER_CONFIRMED if sample.suggested_label == label else USER_CORRECTION
    return store.set_label(sample_id, label, source, approved=True)


class OnlineCollector:
    QUEUE_SIZE = 32
    RING_FRAMES = 180     # about 6 seconds: what "the last moments" can reach back to
    MANUAL_SECONDS = 3.0

    def __init__(self, store: SampleStore, *, config: Optional[SegmenterConfig] = None, pc: str = "", camera: str = "",
                 aspect: float = 720 / 1280, handedness: str = "", source_model_version: str = "") -> None:
        self.store = store
        self.segmenter = MotionSegmenter(config)
        self.guess = None      # optional (frames, aspect) -> label: the production model's guess for the "what was it?" question
        self.session = time.strftime("use-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.pc, self.camera, self.aspect, self.handedness = pc, camera, aspect, handedness
        self.source_model_version = source_model_version
        self.dropped = 0
        self.saved = 0
        self.failed = 0
        self.frame_ms_max = 0.0
        self._ring: Deque[Dict[str, Any]] = deque(maxlen=self.RING_FRAMES)
        self._manual: Deque[Sample] = deque(maxlen=4)
        self._recent: Deque[Sample] = deque(maxlen=20)
        self._recent_lock = threading.Lock()
        self._queue: "queue.Queue[Optional[Segment]]" = queue.Queue(maxsize=self.QUEUE_SIZE)
        self._worker = threading.Thread(target=self._work, name="online-collector", daemon=True)
        self._worker.start()

    # ---------------------------------------------------------------- camera thread
    def frame(self, t: float, hand: Any, action: Optional[str] = None) -> None:
        t0 = time.perf_counter()
        lm = world = None
        if hand is not None and getattr(hand, "world", None):
            lm = [(p.x, p.y, p.z) for p in hand.landmarks]
            world = hand.world
        self._ring.append({"t": float(t), "lm": lm, "world": world, "action": action})
        done = self.segmenter.feed(t, lm, world, action)
        if done is not None:
            self._send(done)
        self.frame_ms_max = max(self.frame_ms_max, (time.perf_counter() - t0) * 1000.0)

    def capture_recent(self, t: float, seconds: Optional[float] = None) -> bool:
        """The person says "that was wrong": keep the last moments whether or not the segmenter saw anything. Camera thread."""
        since = t - (self.MANUAL_SECONDS if seconds is None else seconds)
        frames = [f for f in self._ring if f["t"] >= since]
        if not any(f["lm"] is not None for f in frames):
            return False
        actions: Dict[str, int] = {}
        for f in frames:
            if f["action"] not in IDLE_ACTIONS:
                actions[str(f["action"])] = actions.get(str(f["action"]), 0) + 1
        before = self.dropped
        self._send(Segment(frames=frames, start=frames[0]["t"], end=frames[-1]["t"], reason="manual", actions=actions))
        return self.dropped == before

    def _send(self, segment: Segment) -> None:
        try:
            self._queue.put_nowait(segment)
        except queue.Full:
            self.dropped += 1

    # ---------------------------------------------------------------- worker
    def _work(self) -> None:
        while True:
            segment = self._queue.get()
            if segment is None:
                return
            guess = ACTION_TO_LABEL.get(segment.top_action, NONE_LABEL if not segment.actions else "")
            if segment.reason == "manual" and self.guess is not None:
                try:
                    guess = self.guess(segment.frames, self.aspect) or guess
                except Exception:
                    pass
            try:
                sample = self.store.add_frames(
                    segment.frames, label=UNKNOWN, label_source=UNLABELED_UNKNOWN, session=self.session, pc=self.pc,
                    camera=self.camera, aspect=self.aspect, handedness=self.handedness,
                    source_model_version=self.source_model_version,
                    suggested_label=guess,
                    extra={"start": segment.start, "end": segment.end, "reason": segment.reason,
                           "triggers": segment.triggers, "actions": segment.actions},
                )
            except OSError:
                self.failed += 1
                continue
            if sample is None:
                self.failed += 1
                continue
            self.saved += 1
            with self._recent_lock:
                self._recent.append(sample)
                if segment.reason == "manual":
                    self._manual.append(sample)

    # ---------------------------------------------------------------- any thread
    def take_manual(self) -> Optional[Sample]:
        """The oldest saved "last moments" nobody has been asked about yet."""
        with self._recent_lock:
            return self._manual.popleft() if self._manual else None

    def recent(self, count: int = 5) -> List[Sample]:
        """Newest first; only those nobody has labelled."""
        with self._recent_lock:
            items = list(self._recent)
        return [s for s in reversed(items) if s.label_source == UNLABELED_UNKNOWN][:count]

    def stats(self) -> Dict[str, Any]:
        return {"saved": self.saved, "dropped": self.dropped, "failed": self.failed, "frame_ms_max": round(self.frame_ms_max, 2)}

    def close(self, timeout: float = 5.0) -> None:
        last = self.segmenter.flush()
        if last is not None:
            self._send(last)
        self._queue.put(None)
        self._worker.join(timeout)
