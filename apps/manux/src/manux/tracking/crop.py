"""Hand-area crop: where to look next frame (port of hand-mouse/src/hand_crop.py, same behaviour).

A far or blurred hand covers few model pixels when the whole frame is shrunk; looking at a square
around the last hand at full resolution gives the model a bigger hand. The crop stays put until the
hand nears its edge and is not used when the hand is already big in the frame.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from manux.core.contracts import Landmark

Roi = Tuple[int, int, int, int]  # x0, y0, x1, y1 in frame pixels


class HandCrop:
    def __init__(self, scale: float = 2.4, min_px: int = 192, recenter: float = .18,
                 resize: float = .3, max_hand_frac: float = .35) -> None:
        self.scale = scale
        self.min_px = min_px
        self.recenter = recenter
        self.resize = resize
        self.max_hand_frac = max_hand_frac
        self.roi: Optional[Roi] = None
        self.moves = 0

    def reset(self) -> None:
        self.roi = None

    def update(self, landmarks: Sequence[Landmark], width: int, height: int) -> Optional[Roi]:
        """Feed the tracked hand (normalized points); returns the crop for the next frame."""
        xs = [p[0] * width for p in landmarks]
        ys = [p[1] * height for p in landmarks]
        if not xs:
            self.roi = None
            return None
        size = max(max(xs) - min(xs), max(ys) - min(ys))
        if size >= self.max_hand_frac * height or size <= 0:
            self.roi = None
            return None
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        want = int(round(min(min(width, height), max(self.min_px, self.scale * size))))
        if self.roi is not None:
            x0, y0, x1, y1 = self.roi
            side = x1 - x0
            ox, oy = (x0 + x1) / 2, (y0 + y1) / 2
            centred = abs(cx - ox) <= self.recenter * side and abs(cy - oy) <= self.recenter * side
            if centred and abs(want - side) <= self.resize * side:
                return self.roi
        half = want / 2
        x0 = int(round(min(max(cx - half, 0), width - want)))
        y0 = int(round(min(max(cy - half, 0), height - want)))
        self.roi = (x0, y0, x0 + want, y0 + want)
        self.moves += 1
        return self.roi
