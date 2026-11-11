"""Hand pictures for a pixel classifier (docs/far-hand-loss.md, "손 조각 CNN").

During a guided recording the app also keeps a small grey picture of the hand in every frame, cut out with the box the app itself
puts round the hand (the box of the 21 joints, a little wider, made square). A far hand is only 30-50 pixels, so 48 x 48 holds all
there is. The pictures go to recordings/crops/<recording>.crops.npz; `frame_index` ties each one to the n-th "frame" line of the
recording. They are never uploaded: recordings/crops/ is in .gitignore (they are pictures of a hand).
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

SIZE = 48          # the picture is SIZE x SIZE grey pixels
PAD = 1.35         # the square is this much wider than the box of the joints
MIN_SIDE = 24      # never cut a smaller square than this (pixels of the camera frame)


def hand_square(landmarks: Sequence, width: int, height: int, pad: float = PAD) -> Optional[Tuple[int, int, int]]:
    """(x0, y0, side) of the square round the hand, kept inside the frame; None when it cannot be made."""
    if not landmarks:
        return None
    xs = [p.x * width for p in landmarks]
    ys = [p.y * height for p in landmarks]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    side = int(round(max(max(xs) - min(xs), max(ys) - min(ys), 1.0) * pad))
    side = max(MIN_SIDE, min(side, width, height))
    x0 = int(round(min(max(cx - side / 2, 0), width - side)))
    y0 = int(round(min(max(cy - side / 2, 0), height - side)))
    return x0, y0, side


def crop_hand(frame_bgr: np.ndarray, landmarks: Sequence, size: int = SIZE) -> Optional[np.ndarray]:
    """The hand as a size x size grey picture (uint8), or None."""
    h, w = frame_bgr.shape[:2]
    box = hand_square(landmarks, w, h)
    if box is None:
        return None
    x0, y0, side = box
    patch = frame_bgr[y0:y0 + side, x0:x0 + side]
    if patch.size == 0:
        return None
    grey = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch
    interpolation = cv2.INTER_AREA if side >= size else cv2.INTER_CUBIC
    return cv2.resize(grey, (size, size), interpolation=interpolation)


def crops_path(recording: Path | str) -> Path:
    recording = Path(recording)
    return recording.parent / "crops" / (recording.name + ".crops.npz")


class CropRecorder:
    def __init__(self, size: int = SIZE) -> None:
        self.size = size
        self.frame_index: List[int] = []
        self.crops: List[np.ndarray] = []

    def __len__(self) -> int:
        return len(self.crops)

    def add(self, frame_index: int, frame_bgr: np.ndarray, landmarks: Sequence) -> bool:
        crop = crop_hand(frame_bgr, landmarks, self.size)
        if crop is None:
            return False
        self.frame_index.append(int(frame_index))
        self.crops.append(crop)
        return True

    def save(self, path: Path | str) -> Optional[Path]:
        if not self.crops:
            return None
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, frame_index=np.asarray(self.frame_index, dtype=np.int32),
                            crops=np.stack(self.crops).astype(np.uint8), size=np.int32(self.size))
        return path


def load_crops(path: Path | str) -> Tuple[np.ndarray, np.ndarray]:
    """(frame_index, crops) of a saved file."""
    data = np.load(path)
    return data["frame_index"], data["crops"]
