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
    path: str = "full"                              # "full" | "crop" | "crop_miss>full" | "skipped"
    detect_ms: float = 0.0


def to_observation(raw: RawHand, frame: FrameInfo, side: str) -> HandObservation:
    return HandObservation(frame=frame, landmarks=raw.landmarks, handedness=side,  # type: ignore[arg-type]
                           score=raw.score, world=raw.world, from_crop=raw.from_crop)


class HandTracker:
    def __init__(self, landmarker: Landmarker, *, use_crop: bool = False, min_score: float = MIN_SCORE) -> None:
        self.landmarker = landmarker
        self.use_crop = use_crop
        self.min_score = min_score
        self.crop = HandCrop()

    def reset(self) -> None:
        self.crop.reset()

    def step(self, frame: FrameInfo, image: Any, control_hand: str, detect: bool = True) -> TrackResult:
        if not detect:
            return TrackResult(None, (), "skipped")
        roi = self.crop.roi if self.use_crop else None
        hands: List[RawHand] = self.landmarker.detect(image, roi=roi)
        path = "crop" if roi is not None else "full"
        if roi is not None and not hands:
            self.crop.reset()
            hands = self.landmarker.detect(image, roi=None)
            path = "crop_miss>full"
        chosen = select(hands, control_hand, frame.mirrored, self.min_score)
        hand = None
        if chosen is not None:
            try:
                hand = to_observation(chosen, frame, physical_side(chosen, frame.mirrored, self.min_score))
            except ValueError:
                hand = None   # malformed landmarks: treat as no hand (contracts section 3)
        if self.use_crop and hand is not None:
            self.crop.update(hand.landmarks, frame.width, frame.height)
        else:
            self.crop.reset()
        return TrackResult(hand, tuple(hands), path, float(getattr(self.landmarker, "last_detect_ms", 0.0)))
