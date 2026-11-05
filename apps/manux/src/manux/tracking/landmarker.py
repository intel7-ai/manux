"""MediaPipe hand landmarker (port of hand-mouse/src/hand_tracker.py, Dan's branch version with world joints).

Returns every hand MediaPipe found as a `RawHand`: image landmarks (0..1 of the whole frame),
world landmarks (metres), MediaPipe's handedness label and score. Picking the control hand and
turning MediaPipe's label into the physical hand happens in `selection` / `tracker`.

MediaPipe and OpenCV are imported only when a `MediaPipeLandmarker` is created.
"""
from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Protocol, Sequence, Tuple

from manux.core.contracts import Landmark

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")
Roi = Tuple[int, int, int, int]  # x0, y0, x1, y1 in frame pixels


@dataclass(frozen=True)
class RawHand:
    landmarks: Tuple[Landmark, ...]          # 21 points, whole-frame image coordinates
    label: str                               # MediaPipe's "Left" / "Right" (NOT yet the physical hand)
    score: float
    world: Optional[Tuple[Landmark, ...]] = None
    from_crop: bool = False


class Landmarker(Protocol):
    last_detect_ms: float

    def detect(self, frame: Any, roi: Optional[Roi] = None) -> List[RawHand]: ...

    def close(self) -> None: ...


def ensure_model(path: Path) -> Path:
    """Download the MediaPipe hand model if it is missing (v0.3 behaviour)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size < 100_000:
        urllib.request.urlretrieve(MODEL_URL, str(path))
    return path


def _hands(result: Any, map_point) -> List[RawHand]:
    hands: List[RawHand] = []
    world_all = getattr(result, "hand_world_landmarks", None) or []
    for idx, lms in enumerate(result.hand_landmarks or []):
        label, score = "Right", 1.0
        if result.handedness and idx < len(result.handedness) and result.handedness[idx]:
            label = result.handedness[idx][0].category_name or "Right"
            score = float(result.handedness[idx][0].score or 1.0)
        world = None
        if idx < len(world_all) and world_all[idx]:
            world = tuple((float(p.x), float(p.y), float(p.z)) for p in world_all[idx])
        hands.append(RawHand(tuple(map_point(p) for p in lms), label, score, world))
    return hands


class MediaPipeLandmarker:
    def __init__(self, model_path: Path, *, min_detection_confidence: float = 0.6,
                 min_presence_confidence: float = 0.5, min_tracking_confidence: float = 0.5,
                 detect_max_width: int = 640, num_hands: int = 2, crop_max_side: int = 480) -> None:
        import mediapipe as mp  # lazy: see module docstring

        self._mp = mp
        self.detect_max_width = int(detect_max_width)
        self.crop_max_side = int(crop_max_side)
        self.last_detect_ms = 0.0
        vision = mp.tasks.vision

        def options(hands: int):
            return vision.HandLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(ensure_model(Path(model_path)))),
                running_mode=vision.RunningMode.VIDEO, num_hands=hands,
                min_hand_detection_confidence=min_detection_confidence,
                min_hand_presence_confidence=min_presence_confidence,
                min_tracking_confidence=min_tracking_confidence)

        self._landmarker = vision.HandLandmarker.create_from_options(options(num_hands))
        self._crop_options = options(1)       # separate VIDEO-mode state for the crop
        self._crop_landmarker = None
        self._ts = 0
        self._crop_ts = 0

    @staticmethod
    def _next_ts(last: int) -> int:
        ts = int(time.perf_counter() * 1000)
        return ts if ts > last else last + 1

    def detect(self, frame: Any, roi: Optional[Roi] = None) -> List[RawHand]:
        import cv2

        if roi is not None:
            return self._detect_crop(frame, roi)
        t0 = time.perf_counter()
        h, w = frame.shape[:2]
        if self.detect_max_width > 0 and w > self.detect_max_width:
            scale = self.detect_max_width / float(w)
            frame = cv2.resize(frame, (self.detect_max_width, max(1, int(round(h * scale)))),
                               interpolation=cv2.INTER_AREA)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        self._ts = self._next_ts(self._ts)
        result = self._landmarker.detect_for_video(image, self._ts)
        self.last_detect_ms = (time.perf_counter() - t0) * 1000.0
        return _hands(result, lambda p: (float(p.x), float(p.y), float(p.z)))

    def _detect_crop(self, frame: Any, roi: Roi) -> List[RawHand]:
        import cv2
        import numpy as np

        t0 = time.perf_counter()
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = clamp_roi(roi, w, h)
        crop = frame[y0:y1, x0:x1]
        cw, ch = x1 - x0, y1 - y0
        if max(cw, ch) > self.crop_max_side:
            scale = self.crop_max_side / float(max(cw, ch))
            crop = cv2.resize(crop, (max(1, int(round(cw * scale))), max(1, int(round(ch * scale)))),
                              interpolation=cv2.INTER_AREA)
        if self._crop_landmarker is None:
            self._crop_landmarker = self._mp.tasks.vision.HandLandmarker.create_from_options(self._crop_options)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB,
                               data=np.ascontiguousarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)))
        self._crop_ts = self._next_ts(self._crop_ts)
        result = self._crop_landmarker.detect_for_video(image, self._crop_ts)
        self.last_detect_ms = (time.perf_counter() - t0) * 1000.0
        hands = _hands(result, crop_to_frame((x0, y0, x1, y1), w, h))
        return [RawHand(hand.landmarks, hand.label, hand.score, hand.world, True) for hand in hands]

    def close(self) -> None:
        for name in ("_landmarker", "_crop_landmarker"):
            lm = getattr(self, name)
            if lm is not None:
                lm.close()
                setattr(self, name, None)


def clamp_roi(roi: Roi, width: int, height: int) -> Roi:
    x0, y0, x1, y1 = (int(v) for v in roi)
    x0, y0 = max(0, min(width - 1, x0)), max(0, min(height - 1, y0))
    return x0, y0, max(x0 + 1, min(width, x1)), max(y0 + 1, min(height, y1))


def crop_to_frame(roi: Roi, width: int, height: int):
    """Map a crop-normalized point to whole-frame-normalized (z is in image-width units, like x)."""
    x0, y0, x1, y1 = roi
    cw, ch = x1 - x0, y1 - y0

    def to_frame(p: Any) -> Landmark:
        return ((x0 + p.x * cw) / width, (y0 + p.y * ch) / height, p.z * cw / width)
    return to_frame


def options_from(section: dict) -> dict:
    """MediaPipeLandmarker keyword arguments from the `tracking.` settings section."""
    keys = ("min_detection_confidence", "min_presence_confidence", "min_tracking_confidence", "detect_max_width")
    return {k: section[k] for k in keys if k in section}
