"""
Hand-area crop: where to look next frame (2026-11-04).

With the whole frame shrunk to 640 px wide, a hand far from the webcam (or blurred in a
dim room) covers few model pixels and tracking drops while moving. Looking at a square
around the last known hand, at the camera's full resolution, gives the model a much
bigger hand. The crop stays put until the hand nears its edge (MediaPipe's video tracking
likes a stable image) and is not used when the hand is already big in the frame.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

Roi = Tuple[int, int, int, int]  # x0, y0, x1, y1 in frame pixels


class HandCrop:
    def __init__(self, scale: float = 2.4, min_px: int = 192, recenter: float = .18,
                 resize: float = .3, max_hand_frac: float = .35) -> None:
        self.scale = scale              # crop side = scale x hand size
        self.min_px = min_px            # never smaller than this (pixels)
        self.recenter = recenter        # move when the hand centre is this far from the crop centre (x side)
        self.resize = resize            # resize when the wanted side differs this much (fraction)
        self.max_hand_frac = max_hand_frac  # hand bigger than this x frame height: no crop needed
        self.roi: Optional[Roi] = None
        self.moves = 0

    def reset(self) -> None:
        self.roi = None

    def update(self, landmarks: Sequence, width: int, height: int) -> Optional[Roi]:
        """Feed the tracked hand's landmarks (normalized); returns the crop for the next frame."""
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
            return None
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        want = int(round(min(limit, max(self.min_px, self.scale * size))))
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
