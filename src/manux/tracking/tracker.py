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

HAND_SIDE_GRACE = 0.22
MAX_TRACK_GAP = 0.12
PALM_POINTS = (0, 5, 9, 13, 17)


@dataclass(frozen=True)
class TrackResult:
    hand: Optional[HandObservation]                 # the control hand, or None
    hands: Tuple[RawHand, ...] = ()                 # every hand found (capture screen, diagnostics)
    path: str = "full"                              # "full" | "crop" | "crop_miss>full" | "skipped"
    detect_ms: float = 0.0


def to_observation(raw: RawHand, frame: FrameInfo, side: str) -> HandObservation:
    return HandObservation(frame=frame, landmarks=raw.landmarks, handedness=side,  # type: ignore[arg-type]
                           score=raw.score, world=raw.world, from_crop=raw.from_crop)


def _same_palm(previous: HandObservation, current: HandObservation) -> bool:
    """Compare palm joints in pixels so the tolerance scales with the hand."""
    width, height = current.frame.width, current.frame.height
    before = [(previous.landmarks[i][0] * width, previous.landmarks[i][1] * height) for i in PALM_POINTS]
    after = [(current.landmarks[i][0] * width, current.landmarks[i][1] * height) for i in PALM_POINTS]
    scale = math.dist(before[0], before[2])
    if scale < 4.0:
        return False
    ratio = math.dist(after[0], after[2]) / scale
    displacement = sum(math.dist(a, b) for a, b in zip(before, after)) / len(PALM_POINTS)
    return 0.65 <= ratio <= 1.5 and displacement <= 0.65 * scale


class HandTracker:
    def __init__(self, landmarker: Landmarker, *, use_crop: bool = False, min_score: float = MIN_SCORE) -> None:
        self.landmarker = landmarker
        self.use_crop = use_crop
        self.min_score = min_score
        self.crop = HandCrop()
        self._geometry = None
        self._last_hand: Optional[HandObservation] = None
        self._confirmed_at: Optional[float] = None

    def reset(self) -> None:
        self.crop.reset()
        self._geometry = None
        self._last_hand = None
        self._confirmed_at = None

    def _select(self, hands: List[RawHand], frame: FrameInfo, side: str) -> Optional[HandObservation]:
        chosen = select(hands, side, frame.mirrored, self.min_score)
        if chosen is not None:
            try:
                hand = to_observation(chosen, frame, side)
            except ValueError:
                return None
            self._confirmed_at = frame.t
            return hand

        previous = self._last_hand
        confirmed_at = self._confirmed_at
        if (len(hands) != 1 or previous is None or confirmed_at is None
                or not 0 < frame.t - previous.frame.t <= MAX_TRACK_GAP
                or frame.t - confirmed_at > HAND_SIDE_GRACE):
            return None
        candidate = hands[0]
        if candidate.label not in ("Left", "Right") or not 0.5 <= candidate.score < self.min_score:
            return None
        try:
            hand = to_observation(candidate, frame, side)
        except ValueError:
            return None
        return hand if _same_palm(previous, hand) else None

    def step(self, frame: FrameInfo, image: Any, control_hand: str, detect: bool = True) -> TrackResult:
        if not detect:
            self.reset()
            return TrackResult(None, (), "skipped")
        geometry = (frame.width, frame.height, frame.mirrored, frame.rotation, control_hand)
        if geometry != self._geometry:
            self.reset()
            self._geometry = geometry
        roi = self.crop.roi if self.use_crop else None
        hands: List[RawHand] = self.landmarker.detect(image, roi=roi)
        path = "crop" if roi is not None else "full"
        if roi is not None and not hands:
            self.crop.reset()
            hands = self.landmarker.detect(image, roi=None)
            path = "crop_miss>full"
        hand = self._select(hands, frame, control_hand)
        self._last_hand = hand
        if hand is None:
            self._confirmed_at = None
        if self.use_crop and hand is not None:
            self.crop.update(hand.landmarks, frame.width, frame.height)
        else:
            self.crop.reset()
        return TrackResult(hand, tuple(hands), path, float(getattr(self.landmarker, "last_detect_ms", 0.0)))
