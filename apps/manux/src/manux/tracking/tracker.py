"""Frame -> HandObservation for the control hand (the tracking part of hand-mouse qt_runtime._step).

Per frame: detect (inside the hand crop when enabled; a crop miss resets the crop and retries on
the whole frame), keep every hand for the capture screen, pick the control hand by physical side,
update the crop from it, and return it as a contract `HandObservation` (or None).
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, List, Optional, Tuple

from manux.core.contracts import FrameInfo, HandObservation
from manux.tracking.crop import HandCrop
from manux.tracking.landmarker import Landmarker, RawHand
from manux.tracking.selection import MIN_SCORE, physical_side, select

RECOVERY_INTERVAL = .25


def _search_rois(width: int, height: int):
    """Center first, then overlapping tiles reaching every edge, including portrait corners."""
    side = max(1, round(min(width, height) * .65))

    def starts(length):
        count = max(1, math.ceil((length - side) / (side * .85)))
        return [round(i * (length - side) / count) for i in range(count + 1)]

    cx, cy = round((width - side) / 2), round((height - side) / 2)
    tiles = {(x, y) for x in starts(width) for y in starts(height)}
    tiles.discard((cx, cy))
    ordered = [(cx, cy)] + sorted(tiles, key=lambda xy: ((xy[0] - cx)**2 + (xy[1] - cy)**2, xy))
    return [(x, y, x + side, y + side) for x, y in ordered]


@dataclass(frozen=True)
class TrackResult:
    hand: Optional[HandObservation]                 # the control hand, or None
    hands: Tuple[RawHand, ...] = ()                 # every hand found (capture screen, diagnostics)
    path: str = "full"                              # full | crop | crop_miss>full | search | skipped
    detect_ms: float = 0.0
    zoom: float = 1.0


def to_observation(raw: RawHand, frame: FrameInfo, side: str) -> HandObservation:
    return HandObservation(frame=frame, landmarks=raw.landmarks, handedness=side,  # type: ignore[arg-type]
                           score=raw.score, world=raw.world, from_crop=raw.from_crop)


class HandTracker:
    def __init__(self, landmarker: Landmarker, *, use_crop: bool = False, min_score: float = MIN_SCORE) -> None:
        self.landmarker = landmarker
        self.use_crop = use_crop
        self.min_score = min_score
        self.crop = HandCrop()
        self.auto_distance = False
        self._geometry = None
        self._search_at = 0.
        self._search_index = 0
        self._crop_retry_at = 0.
        self._recovery_roi = None

    def reset(self) -> None:
        self.crop.reset()
        self._geometry = None
        self._search_at = 0.
        self._search_index = 0
        self._crop_retry_at = 0.
        self._recovery_roi = None

    def _control_hand(self, hands, frame, control_hand):
        chosen = select(hands, control_hand, frame.mirrored, self.min_score)
        if chosen is not None:
            try:
                return to_observation(chosen, frame, physical_side(chosen, frame.mirrored, self.min_score))
            except ValueError:
                pass  # A malformed result is also a miss; recovery must use the current image.
        return None

    def step(self, frame: FrameInfo, image: Any, control_hand: str, detect: bool = True) -> TrackResult:
        if not detect:
            return TrackResult(None, (), "skipped")
        geometry = (frame.width, frame.height, frame.mirrored, frame.rotation, control_hand)
        if geometry != self._geometry:
            self.reset()
            self._geometry = geometry
        roi = self.crop.roi if (self.use_crop or self.auto_distance) and frame.t >= self._crop_retry_at else None
        hands: List[RawHand] = self.landmarker.detect(image, roi=roi)
        detect_ms = float(getattr(self.landmarker, "last_detect_ms", 0.0))
        path = "crop" if roi is not None else "full"
        hand = self._control_hand(hands, frame, control_hand)
        if roi is not None and hand is None:
            self.crop.reset()
            # Avoid paying for the same failed crop on every frame while the full view is stable.
            self._crop_retry_at = frame.t + RECOVERY_INTERVAL
            hands = self.landmarker.detect(image, roi=None)
            detect_ms += float(getattr(self.landmarker, "last_detect_ms", 0.0))
            hand = self._control_hand(hands, frame, control_hand)
            self._recovery_roi = roi if hand is None else None
            path = "crop_miss>full"
            roi = None
        matches = sum(physical_side(h, frame.mirrored, self.min_score) == control_hand
                      for h in hands) if control_hand in ("Left", "Right") else 0
        search_missing = not hands or control_hand in ("Left", "Right") and matches == 0
        if self.auto_distance and hand is None and search_missing and path == "full" and frame.t >= self._search_at:
            # At most one extra search region every 250ms; never queue camera frames.
            self._search_at = frame.t + RECOVERY_INTERVAL
            if self._recovery_roi is not None:
                roi, self._recovery_roi = self._recovery_roi, None
            else:
                regions = _search_rois(frame.width, frame.height)
                roi = regions[self._search_index % len(regions)]
                self._search_index += 1
            hands = self.landmarker.detect(image, roi=roi)
            detect_ms += float(getattr(self.landmarker, "last_detect_ms", 0.0))
            hand = self._control_hand(hands, frame, control_hand)
            path = "search"
        if (self.use_crop or self.auto_distance) and hand is not None:
            self._recovery_roi = None
            if path == "search":
                self._crop_retry_at = frame.t
            self.crop.update(hand.landmarks, frame.width, frame.height)
        else:
            self.crop.reset()
        zoom = min(frame.width, frame.height) / (roi[2] - roi[0]) if roi is not None else 1.
        return TrackResult(hand, tuple(hands), path, detect_ms, zoom)
