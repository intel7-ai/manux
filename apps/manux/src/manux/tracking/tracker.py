"""Frame -> HandObservation for the control hand (the tracking part of hand-mouse qt_runtime._step).

Per frame: detect (inside the hand crop when enabled; a crop miss resets the crop and retries on
the whole frame), keep every hand for the capture screen, pick the control hand by physical side,
update the crop from it, and return it as a contract `HandObservation` (or None).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from manux.core.contracts import FrameInfo, HandObservation
from manux.tracking.crop import HandCrop
from manux.tracking.landmarker import Landmarker, RawHand
from manux.tracking.selection import MIN_SCORE, physical_side, select


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

    def reset(self) -> None:
        self.crop.reset()
        self._geometry = None
        self._search_at = 0.

    def step(self, frame: FrameInfo, image: Any, control_hand: str, detect: bool = True) -> TrackResult:
        if not detect:
            return TrackResult(None, (), "skipped")
        geometry = (frame.width, frame.height, frame.mirrored, frame.rotation)
        if geometry != self._geometry:
            self.crop.reset()
            self._geometry = geometry
        roi = self.crop.roi if self.use_crop or self.auto_distance else None
        hands: List[RawHand] = self.landmarker.detect(image, roi=roi)
        detect_ms = float(getattr(self.landmarker, "last_detect_ms", 0.0))
        path = "crop" if roi is not None else "full"
        if roi is not None and not hands:
            self.crop.reset()
            hands = self.landmarker.detect(image, roi=None)
            detect_ms += float(getattr(self.landmarker, "last_detect_ms", 0.0))
            path = "crop_miss>full"
            roi = None
        if self.auto_distance and not hands and path == "full" and frame.t >= self._search_at:
            # At most one extra search region every 250ms; never queue camera frames.
            self._search_at = frame.t + .25
            positions = ((.5, .5), (.25, .5), (.75, .5), (.5, .25), (.5, .75))
            cx, cy = positions[self._search_index % len(positions)]
            self._search_index += 1
            side = max(1, round(min(frame.width, frame.height) * .65))
            x = max(0, min(frame.width - side, round(cx * frame.width - side / 2)))
            y = max(0, min(frame.height - side, round(cy * frame.height - side / 2)))
            roi = (x, y, x + side, y + side)
            hands = self.landmarker.detect(image, roi=roi)
            detect_ms += float(getattr(self.landmarker, "last_detect_ms", 0.0))
            path = "search"
        chosen = select(hands, control_hand, frame.mirrored, self.min_score)
        hand = None
        if chosen is not None:
            try:
                hand = to_observation(chosen, frame, physical_side(chosen, frame.mirrored, self.min_score))
            except ValueError:
                hand = None   # malformed landmarks: treat as no hand (contracts section 3)
        if (self.use_crop or self.auto_distance) and hand is not None:
            self.crop.update(hand.landmarks, frame.width, frame.height)
        else:
            self.crop.reset()
        zoom = min(frame.width, frame.height) / (roi[2] - roi[0]) if roi is not None else 1.
        return TrackResult(hand, tuple(hands), path, detect_ms, zoom)
