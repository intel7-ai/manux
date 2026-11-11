"""Cuts everyday use into candidate gesture sequences, frame by frame and without the future.

A frame is active when a finger touches the thumb, the fingers move, the palm moves fast, or the rule engine fired something.
A segment carries some frames from before it starts. It is a candidate: this says "something happened here", not what it means.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Sequence

import numpy as np

from src.tcn_features import PALM, THUMB_TIP, TIPS, hand_frame

IDLE_ACTIONS = (None, "", "none", "move_cursor", "pointer_wait")


@dataclass
class SegmenterConfig:
    contact: float = .35            # thumb-fingertip distance / hand size (wrist to middle knuckle)
    finger_speed: float = 2.0       # mean fingertip speed in the hand's own frame, hand sizes per second
    palm_speed: float = 6.0         # hand sizes per second
    pose_change: float = .45        # mean fingertip distance from the recent pose, hand sizes: a held sign touches nothing
    pose_seconds: float = 2.0       # how fast "the recent pose" follows the hand
    start_frames: int = 2           # active frames in a row to start
    end_seconds: float = .4         # quiet time that ends a segment
    max_gap: float = .5             # a longer hand loss ends a segment
    min_seconds: float = .25
    max_seconds: float = 6.0
    pre_frames: int = 15


@dataclass
class Segment:
    frames: List[Dict[str, Any]]
    start: float                    # time of the first active frame (frames also holds the time before it)
    end: float
    reason: str                     # quiet | hand_lost | too_long | flush
    triggers: Dict[str, int] = field(default_factory=dict)
    actions: Dict[str, int] = field(default_factory=dict)

    @property
    def top_action(self) -> str:
        return max(self.actions, key=self.actions.get) if self.actions else ""


class MotionSegmenter:
    def __init__(self, config: Optional[SegmenterConfig] = None) -> None:
        self.config = config or SegmenterConfig()
        self.reset()

    def reset(self) -> None:
        self._pre: Deque[Dict[str, Any]] = deque(maxlen=self.config.pre_frames)
        self._cur: Optional[List[Dict[str, Any]]] = None
        self._start = 0.0
        self._last_active = 0.0
        self._last_hand: Optional[float] = None
        self._run = 0
        self._triggers: Dict[str, int] = {}
        self._actions: Dict[str, int] = {}
        self._prev_local = None
        self._prev_palm = None
        self._prev_t: Optional[float] = None
        self._pose = None

    def _why_active(self, t: float, lm: Any, world: Any, action: Optional[str]) -> List[str]:
        why: List[str] = []
        if action not in IDLE_ACTIONS:
            why.append("rule")
        world_arr = np.asarray(world, dtype=np.float64)
        local = hand_frame(world_arr)
        if local is None:
            self._prev_local = None
            return why
        if float(np.min(np.linalg.norm(local[list(TIPS)] - local[THUMB_TIP], axis=1))) < self.config.contact:
            why.append("contact")
        lm_arr = np.asarray(lm, dtype=np.float64)[:, :2]
        size = max(float(np.linalg.norm(lm_arr[9] - lm_arr[0])), 1e-6)
        palm = lm_arr[list(PALM)].mean(axis=0)
        dt = t - self._prev_t if self._prev_t is not None else 0.0
        if self._prev_local is not None and dt > 0:
            speed = float(np.linalg.norm(local[list(TIPS)] - self._prev_local[list(TIPS)], axis=1).mean()) / dt
            if speed > self.config.finger_speed:
                why.append("fingers")
            if float(np.linalg.norm(palm - self._prev_palm)) / size / dt > self.config.palm_speed:
                why.append("palm")
        tips = local[[THUMB_TIP, *TIPS]]
        if self._pose is None:
            self._pose = tips.copy()
        elif float(np.linalg.norm(tips - self._pose, axis=1).mean()) > self.config.pose_change:
            why.append("pose")
        if dt > 0:
            self._pose += (tips - self._pose) * min(1.0, dt / self.config.pose_seconds)
        self._prev_local, self._prev_palm, self._prev_t = local, palm, t
        return why

    def _close(self, end: float, reason: str) -> Optional[Segment]:
        frames, self._cur = self._cur, None
        triggers, actions = self._triggers, self._actions
        self._triggers, self._actions, self._run = {}, {}, 0
        if not frames or end - self._start < self.config.min_seconds:
            return None
        return Segment(frames=frames, start=self._start, end=end, reason=reason, triggers=triggers, actions=actions)

    def feed(self, t: float, lm: Any = None, world: Any = None, action: Optional[str] = None, **extra: Any) -> Optional[Segment]:
        """One frame (lm and world None = no hand). Returns a segment when one has just ended."""
        frame: Dict[str, Any] = {"t": float(t), "lm": lm, "world": world, "action": action, **extra}
        has_hand = lm is not None and world is not None
        if has_hand:
            self._last_hand = t
            why = self._why_active(t, lm, world, action)
        else:
            why = []
            self._prev_local, self._prev_palm, self._prev_t = None, None, None
            if self._last_hand is None or t - self._last_hand > self.config.max_gap:
                self._pose = None      # a new start: the first pose seen is not a change
        if self._cur is None:
            self._run = self._run + 1 if why else 0
            self._pre.append(frame)
            if self._run >= self.config.start_frames:
                self._cur = list(self._pre)
                self._pre.clear()
                self._start = self._last_active = float(t)
            return None
        self._cur.append(frame)
        for reason in why:
            self._triggers[reason] = self._triggers.get(reason, 0) + 1
        if action not in IDLE_ACTIONS:
            self._actions[str(action)] = self._actions.get(str(action), 0) + 1
        if why:
            self._last_active = float(t)
        if self._last_hand is not None and t - self._last_hand > self.config.max_gap:
            return self._close(self._last_active, "hand_lost")
        if t - self._start > self.config.max_seconds:
            return self._close(float(t), "too_long")
        if has_hand and t - self._last_active > self.config.end_seconds:   # a flickering far hand is not a quiet hand
            return self._close(self._last_active, "quiet")
        return None

    def flush(self) -> Optional[Segment]:
        return self._close(self._last_active, "flush") if self._cur is not None else None


def evaluate_segments(frames: Sequence[Dict[str, Any]], config: Optional[SegmenterConfig] = None) -> Dict[str, float]:
    """Against the schedule of a guided recording: repetitions covered, share of segment time inside `do`, segments per minute
    outside `do`. The schedule says what was asked, not exactly what the hand did, so this is a rough check."""
    seg = MotionSegmenter(config)
    segments: List[Segment] = []
    for f in frames:
        rule = f.get("rule") or {}
        done = seg.feed(float(f["t"]), f.get("lm"), f.get("world"), rule.get("engine_action", rule.get("action")))
        if done:
            segments.append(done)
    done = seg.flush()
    if done:
        segments.append(done)
    reps: Dict[tuple, List[float]] = {}
    do_time = rest_time = 0.0
    do_spans: List[tuple] = []
    prev = None
    for f in frames:
        lab = f.get("label") or {}
        t = float(f["t"])
        if prev is not None and 0 < t - prev[0] < .5:
            if prev[1] == "do":
                do_time += t - prev[0]
            elif prev[1] in ("prep", "rest"):
                rest_time += t - prev[0]
        prev = (t, lab.get("phase"))
        if lab.get("phase") == "do":
            reps.setdefault((lab.get("set"), lab.get("rep")), []).append(t)
    do_spans = [(min(v), max(v)) for v in reps.values()]
    covered = sum(1 for a, b in do_spans if any(min(b, s.end) - max(a, s.start) >= .5 * min(b - a, 1.0) for s in segments))
    inside = outside_count = 0.0
    total = 0.0
    for s in segments:
        length = s.end - s.start
        overlap = sum(max(0.0, min(b, s.end) - max(a, s.start)) for a, b in do_spans)
        inside += overlap
        total += length
        if overlap < .25 * length:
            outside_count += 1
    return {"reps": len(do_spans), "covered": covered, "segments": len(segments), "share_in_do": inside / total if total else 0.0,
            "outside_segments": outside_count, "outside_per_min": outside_count / (rest_time / 60) if rest_time else 0.0,
            "median_seconds": float(np.median([s.end - s.start for s in segments])) if segments else 0.0}
