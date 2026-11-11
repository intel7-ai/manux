"""
Hand-area crop: where to look next frame (2026-11-04).

With the whole frame shrunk to 640 px wide, a hand far from the webcam (or blurred in a
dim room) covers few model pixels and tracking drops while moving. Looking at a square
around the last known hand, at the camera's full resolution, gives the model a much
bigger hand. The crop stays put until the hand nears its edge (MediaPipe's video tracking
likes a stable image) and is not used when the hand is already big in the frame.
"""
from __future__ import annotations

from collections import deque
from statistics import median
from typing import Deque, Optional, Sequence, Tuple

Roi = Tuple[int, int, int, int]  # x0, y0, x1, y1 in frame pixels


class HandCrop:
    SEARCH_SCALES = (2.0, 3.0, 5.0)   # search crop side = these x the last crop side, in turn
    SEARCH_STEP = .4                  # seconds at each size
    SEARCH_MAX_PX = 480
    COLLAPSE_MAX_FRAMES = 10          # a "collapse" this long is a real change: believe it

    def __init__(self, scale: float = 2.4, min_px: int = 192, recenter: float = .18,
                 resize: float = .3, max_hand_frac: float = .35, hold_seconds: float = 0.8,
                 search_seconds: float = 0.0, collapse_ratio: float = 0.0) -> None:
        self.search_seconds = search_seconds  # after the hold, look around the last place this long (0 = off)
        self.collapse_ratio = collapse_ratio  # a box below this x the recent size is a bad reading: crop unchanged (0 = off)
        self.searching = False
        self.collapsed = False                # the last update() ignored a collapsed reading
        self._anchor: Optional[Tuple[float, float, int]] = None   # centre and side of the last real crop
        self._frame_size: Optional[Tuple[int, int]] = None
        self._sizes: Deque[float] = deque(maxlen=15)
        self._collapse_run = 0
        self.scale = scale              # crop side = scale x hand size
        self.min_px = min_px            # never smaller than this (pixels)
        self.recenter = recenter        # move when the hand centre is this far from the crop centre (x side)
        self.resize = resize            # resize when the wanted side differs this much (fraction)
        self.max_hand_frac = max_hand_frac  # hand bigger than this x frame height: no crop needed
        self.hold_seconds = hold_seconds    # after a miss, keep trying the last crop this long (far hands flicker)
        self.last_seen: Optional[float] = None
        self.roi: Optional[Roi] = None
        self.moves = 0

    def reset(self) -> None:
        self.roi = None
        self.last_seen = None
        self.searching = False
        self.collapsed = False
        self._anchor = None
        self._sizes.clear()
        self._collapse_run = 0

    def miss(self, now: float) -> None:
        """No hand this frame: keep the last crop for `hold_seconds` so the next frames look there first. Then, for
        `search_seconds`, look around the same place with bigger crops in turn (a far hand moves out of its small crop,
        and is too small for the whole-frame search: docs/far-hand-loss.md, 23)."""
        if self.roi is None or self.last_seen is None:
            self.reset()
            return
        age = now - self.last_seen
        if age <= self.hold_seconds:
            return
        if self.search_seconds <= 0 or self._anchor is None or self._frame_size is None or age > self.hold_seconds + self.search_seconds:
            self.reset()
            return
        self.searching = True
        cx, cy, side = self._anchor
        width, height = self._frame_size
        factor = self.SEARCH_SCALES[int((age - self.hold_seconds) / self.SEARCH_STEP) % len(self.SEARCH_SCALES)]
        want = int(round(min(width, height, self.SEARCH_MAX_PX, side * factor)))
        self.roi = self._square(cx, cy, want, width, height)

    @staticmethod
    def _square(cx: float, cy: float, want: int, width: int, height: int) -> Roi:
        half = want / 2
        x0 = int(round(min(max(cx - half, 0), width - want)))
        y0 = int(round(min(max(cy - half, 0), height - want)))
        return (x0, y0, x0 + want, y0 + want)

    def _is_collapse(self, size: float) -> bool:
        """A box much smaller than a moment ago: the model's joints fell together. Believed only if it goes on."""
        if self.collapse_ratio <= 0 or self.roi is None or len(self._sizes) < 5 or size >= self.collapse_ratio * median(self._sizes):
            self._collapse_run = 0
            return False
        self._collapse_run += 1
        if self._collapse_run > self.COLLAPSE_MAX_FRAMES:
            self._collapse_run = 0
            self._sizes.clear()
            return False
        return True

    def follow(self, box: Tuple[int, int, int, int], width: int, height: int, now: float) -> Optional[Roi]:
        """The detector lost the hand but a video tracker still sees it at `box` (x, y, w, h in pixels): put the crop there."""
        x, y, w, h = box
        size = max(w, h)
        if size <= 0:
            return None
        limit = min(width, height)
        want = int(round(min(limit, max(self.min_px, self.scale * size))))
        cx, cy = x + w / 2, y + h / 2
        self.roi = self._square(cx, cy, want, width, height)
        self._anchor, self._frame_size = (cx, cy, want), (width, height)
        self.searching = False
        self.last_seen = now          # the crop is alive as long as the tracker keeps following
        self.moves += 1
        return self.roi

    def update(self, landmarks: Sequence, width: int, height: int, now: Optional[float] = None) -> Optional[Roi]:
        """Feed the tracked hand's landmarks (normalized); returns the crop for the next frame."""
        if now is not None:
            self.last_seen = now
        self.searching = False
        self.collapsed = False
        self._frame_size = (width, height)
        xs = [p.x * width for p in landmarks]
        ys = [p.y * height for p in landmarks]
        if not xs:
            self.roi = None
            return None
        bw, bh = max(xs) - min(xs), max(ys) - min(ys)
        size = max(bw, bh)
        limit = min(width, height)
        if size >= self.max_hand_frac * height or size <= 0:
            self.roi = None  # big (close) hand: the whole frame already works
            self._anchor = None
            return None
        if self._is_collapse(size):
            self.collapsed = True
            return self.roi
        self._sizes.append(size)
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        want = int(round(min(limit, max(self.min_px, self.scale * size))))
        self._anchor = (cx, cy, want)
        if self.roi is not None:
            x0, y0, x1, y1 = self.roi
            side = x1 - x0
            ox, oy = (x0 + x1) / 2, (y0 + y1) / 2
            centred = abs(cx - ox) <= self.recenter * side and abs(cy - oy) <= self.recenter * side
            same_size = abs(want - side) <= self.resize * side
            if centred and same_size:
                return self.roi
        half = want / 2
        x0 = int(round(min(max(cx - half, 0), width - want)))
        y0 = int(round(min(max(cy - half, 0), height - want)))
        self.roi = (x0, y0, x0 + want, y0 + want)
        self.moves += 1
        return self.roi
