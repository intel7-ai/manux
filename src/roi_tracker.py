"""Follows the hand with a video tracker while the hand detector has lost it (docs/far-hand-loss.md, "영상 추적기").

When the detector loses a far hand the crop used to stay where the hand was last seen (HandCrop.miss, 0.8 s). A moving hand
leaves that spot. A video tracker (it needs only the last good frame and the hand's box) keeps following the hand in the
following frames, so the crop moves with it and the detector looks in the right place when it tries again.

kind: "off" | "mil" (OpenCV MIL, no files) | "nano" (NanoTrack, a small neural net) | "vit" (ViT tracker, neural).
The neural ones need the OpenCV Zoo weights in models/trackers/ (see docs/far-hand-loss.md); without them "mil" is used.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models" / "trackers"
NANO_FILES = ("nanotrack_backbone_sim.onnx", "nanotrack_head_sim.onnx")
VIT_FILE = "object_tracking_vittrack_2023sep.onnx"

Box = Tuple[int, int, int, int]  # x, y, w, h in frame pixels


def hand_box(landmarks, width: int, height: int, pad: float = 0.25) -> Optional[Box]:
    """Pixel box around the 21 landmarks, grown by `pad` on every side and kept inside the frame."""
    xs = [p.x * width for p in landmarks]
    ys = [p.y * height for p in landmarks]
    if not xs:
        return None
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = max(x1 - x0, 8.0), max(y1 - y0, 8.0)
    x0, y0, x1, y1 = x0 - pad * w, y0 - pad * h, x1 + pad * w, y1 + pad * h
    x0, y0 = max(0.0, x0), max(0.0, y0)
    x1, y1 = min(float(width), x1), min(float(height), y1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0)


def make_cv_tracker(kind: str, models_dir: Path = MODELS_DIR):
    """An OpenCV tracker of `kind`, or None. A neural kind without its weight files falls back to MIL."""
    import cv2
    if kind == "nano" and all((models_dir / n).is_file() for n in NANO_FILES) and hasattr(cv2, "TrackerNano_create"):
        params = cv2.TrackerNano_Params()
        params.backbone = str(models_dir / NANO_FILES[0])
        params.neckhead = str(models_dir / NANO_FILES[1])
        return cv2.TrackerNano_create(params)
    if kind == "vit" and (models_dir / VIT_FILE).is_file() and hasattr(cv2, "TrackerVit_create"):
        params = cv2.TrackerVit_Params()
        params.net = str(models_dir / VIT_FILE)
        return cv2.TrackerVit_create(params)
    if kind in ("mil", "nano", "vit") and hasattr(cv2, "TrackerMIL_create"):
        return cv2.TrackerMIL_create()
    return None


class RoiTracker:
    def __init__(self, kind: str = "off", seconds: float = 3.0,
                 factory: Optional[Callable[[str], Any]] = None) -> None:
        self.kind = str(kind or "off").lower()
        self.seconds = float(seconds)
        self._factory = factory or make_cv_tracker
        self._frame = None            # the last frame with a detected hand, and its box: where the tracker starts
        self._box: Optional[Box] = None
        self._tracker = None
        self._lost_since: Optional[float] = None

    @property
    def enabled(self) -> bool:
        return self.kind != "off" and self.seconds > 0

    def good(self, frame, box: Optional[Box]) -> None:
        """The detector found the hand in this frame."""
        if not self.enabled:
            return
        self._frame, self._box = frame, box
        self._tracker, self._lost_since = None, None

    def lost(self, frame, now: float) -> Optional[Box]:
        """The detector did not find the hand in `frame`: where the hand is now, or None when the tracker cannot say.
        The first call starts the tracker on the last good frame, the next ones follow the hand."""
        if not self.enabled or self._frame is None or self._box is None:
            return None
        if self._lost_since is None:
            self._lost_since = now
        if now - self._lost_since > self.seconds:
            self._frame = None        # lost for too long: wait for the detector
            return None
        try:
            if self._tracker is None:
                tracker = self._factory(self.kind)
                if tracker is None:
                    self._frame = None
                    return None
                tracker.init(self._frame, self._box)
                self._tracker = tracker
            ok, box = self._tracker.update(frame)
        except Exception:
            self._frame, self._tracker = None, None
            return None
        if not ok:
            self._frame, self._tracker = None, None
            return None
        x, y, w, h = (int(round(v)) for v in box)
        return (x, y, w, h) if w > 2 and h > 2 else None

    def reset(self) -> None:
        self._frame, self._box, self._tracker, self._lost_since = None, None, None, None
