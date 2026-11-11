"""
MediaPipe Hand Landmarker Wrapper (Tasks Vision API)
Detects 21 3D hand landmarks in real time and draws visual skeletons & pinch indicators.
"""

from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import mediapipe as mp
import numpy as np

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)
DEFAULT_MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "hand_landmarker.task"

# Standard MediaPipe 21 hand landmark connections
HAND_CONNECTIONS: List[Tuple[int, int]] = [
    (0, 1), (1, 2), (2, 3), (3, 4),        # Thumb
    (0, 5), (5, 6), (6, 7), (7, 8),        # Index
    (5, 9), (9, 10), (10, 11), (11, 12),   # Middle
    (9, 13), (13, 14), (14, 15), (15, 16), # Ring
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20), # Pinky & Palm
]


FINGER_TIPS = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}
GUIDE_COLORS = {  # BGR
    "near": (0, 220, 255),     # yellow: a finger is approaching the thumb
    "pinched": (0, 255, 0),    # green : pinch recognized
    "action": (0, 60, 255),    # red   : click / drag / switcher actually firing
}
ACTION_FINGER = {
    "left_click": "middle", "right_click": "ring", "drag_hold": "middle",
    "task_switcher": "pinky", "alt_tab": "pinky", "drag_toggle": "pinky", "grab": "index",
    "scroll_grab": "index",
}


def pinch_guides(
    distances: dict,
    config: dict,
    pinched: set,
    action: str = "none",
    gesture: str = "NONE",
    near_factor: float = 1.8,
) -> List[Tuple[str, str]]:
    """
    Which thumb -> finger lines to draw: every recognized pinch, plus the single nearest
    finger once it comes within `near_factor` x its own pinch distance. Nothing in a fist
    or the show-desktop posture (the thumb rests near the fingers there).
    """
    if gesture in ("fist", "thumb_tucked_open"):
        return []
    thresholds = {
        "index": float(config.get("pinch_threshold", 0.055)),
        "middle": float(config.get("click_threshold", 0.052)),
        "ring": float(config.get("ring_click_threshold", 0.044)),
        "pinky": float(config.get("pinky_click_threshold", 0.05)),
    }
    fired = ACTION_FINGER.get(action)
    guides: List[Tuple[str, str]] = []
    for finger in ("index", "middle", "ring", "pinky"):
        if finger in pinched or finger == fired:
            state = "action" if (finger == fired and action not in ("grab", "scroll_grab")) else "pinched"
            guides.append((finger, state))
    if not guides:
        best, best_ratio = None, near_factor
        for finger, thr in thresholds.items():
            d = distances.get(finger)
            if d is None or thr <= 0:
                continue
            ratio = d / thr
            if ratio <= best_ratio:
                best, best_ratio = finger, ratio
        if best is not None:
            guides.append((best, "near"))
    return guides


@dataclass
class HandLandmark:
    x: float
    y: float
    z: float = 0.0


@dataclass
class HandDetection:
    landmarks: List[HandLandmark]
    handedness: str  # "Left" or "Right"
    score: float
    from_crop: bool = False  # found in the hand-area crop (landmarks already mapped back)
    # 3D joints in metres around the hand's centre (MediaPipe hand_world_landmarks): the same hand shape gives about the
    # same values from any camera angle. Kept for the dataset recorder (src/recorder.py); None if the model gave none.
    world: Optional[List[Tuple[float, float, float]]] = None


def _world(result, idx: int) -> Optional[List[Tuple[float, float, float]]]:
    """3D hand joints of detection `idx` (metres, hand-centred), or None."""
    world = getattr(result, "hand_world_landmarks", None)
    if not world or idx >= len(world) or not world[idx]:
        return None
    return [(float(p.x), float(p.y), float(p.z)) for p in world[idx]]


def merge_looks(looks: List[List[HandDetection]], outlier: float = 0.35) -> List[HandDetection]:
    """The hand seen in several crops of the same frame -> one hand: the landmark-wise average. A look that is far from the
    others (further than `outlier` x the hand's size from the median) is dropped; looks that found nothing are skipped.
    The first look keeps its hand label and score. One look alone is returned as it is."""
    found = [d[0] for d in looks if d]
    if not found:
        return []
    if len(found) == 1:
        return [found[0]]
    pts = np.array([[(p.x, p.y, p.z) for p in d.landmarks] for d in found], dtype=float)      # looks x 21 x 3
    median = np.median(pts, axis=0)
    size = float(np.max(np.linalg.norm(median[[5, 9, 13, 17], :2] - median[0, :2], axis=1)))
    dist = np.linalg.norm(pts[:, :, :2] - median[None, :, :2], axis=2).mean(axis=1)           # per look
    keep = dist <= max(outlier * size, 1e-6) if size > 1e-6 else np.ones(len(found), dtype=bool)
    if not keep.any():
        keep = dist <= dist.min() + 1e-9
    kept = [d for d, k in zip(found, keep) if k]
    mean = pts[keep].mean(axis=0)
    world = None
    worlds = [d.world for d in kept if d.world is not None]
    if worlds:
        world = [tuple(float(v) for v in row) for row in np.mean(np.array(worlds, dtype=float), axis=0)]
    first = kept[0]
    return [HandDetection(landmarks=[HandLandmark(x=float(a), y=float(b), z=float(c)) for a, b, c in mean],
                          handedness=first.handedness, score=max(d.score for d in kept), from_crop=True, world=world)]


def ensure_model_exists(model_path: Path = DEFAULT_MODEL_PATH) -> Path:
    """Ensure the MediaPipe hand_landmarker.task model exists locally; download if missing."""
    model_path.parent.mkdir(parents=True, exist_ok=True)
    if not model_path.exists() or model_path.stat().st_size < 100_000:
        print(f"[HandTracker] Downloading MediaPipe model to {model_path} ...")
        urllib.request.urlretrieve(MODEL_URL, str(model_path))
    return model_path


class HandTracker:
    """Real-time hand tracker built on Google MediaPipe Tasks Vision API.

    Presence / tracking confidence default to 0.5 (MediaPipe's own default; was 0.6). At 0.6
    a dim room or a hand far from a 30 fps webcam dropped tracking while the cursor was being
    moved, and every drop forces a fresh palm detection (2026-11-03). Detection stays 0.6 so
    a new hand still needs a confident find. All four are config keys: see tracker_options().
    """

    def __init__(
        self,
        model_path: Optional[Path] = None,
        num_hands: int = 1,
        min_detection_confidence: float = 0.6,
        min_presence_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
        detect_max_width: int = 640,
        crop_upscale_side: int = 0,
        crop_max_zoom: float = 4.0,
        crop_tta: int = 0,
        crop_tta_max_side: int = 200,
    ) -> None:
        # MediaPipe shrinks the image to a small fixed input internally anyway, so feeding it
        # a 720p frame mostly costs conversion time (14.5 ms -> 10.6 ms per frame at 640 px).
        # Landmarks are normalized, so they map back to the full frame unchanged.
        self.detect_max_width = int(detect_max_width)
        self.options = {
            "min_detection_confidence": min_detection_confidence,
            "min_presence_confidence": min_presence_confidence,
            "min_tracking_confidence": min_tracking_confidence,
            "detect_max_width": self.detect_max_width,
            "crop_upscale_side": int(crop_upscale_side),
            "crop_max_zoom": float(crop_max_zoom),
            "crop_tta": int(crop_tta),
            "crop_tta_max_side": int(crop_tta_max_side),
        }
        self.crop_upscale_side = int(crop_upscale_side)
        self.crop_max_zoom = float(crop_max_zoom)
        # Several looks at a small hand (see _detect_crop): `crop_tta` extra crops, only while the crop is at most
        # `crop_tta_max_side` pixels (a far hand). Each extra crop has its own landmarker so its video tracking stays steady.
        self.crop_tta = int(crop_tta)
        self.crop_tta_max_side = int(crop_tta_max_side)
        self._tta_landmarkers: list = []
        self._tta_timestamps: list = []
        self.last_detect_ms: float = 0.0
        resolved_model = ensure_model_exists(model_path or DEFAULT_MODEL_PATH)
        # MediaPipe's native Windows file loader cannot reliably open Korean paths.
        model_bytes = resolved_model.read_bytes()

        BaseOptions = mp.tasks.BaseOptions
        HandLandmarker = mp.tasks.vision.HandLandmarker
        HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
        VisionRunningMode = mp.tasks.vision.RunningMode

        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_buffer=model_bytes),
            running_mode=VisionRunningMode.VIDEO,
            num_hands=num_hands,
            min_hand_detection_confidence=min_detection_confidence,
            min_hand_presence_confidence=min_presence_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._landmarker = HandLandmarker.create_from_options(options)
        self._last_timestamp_ms: int = 0
        # Hand-area crop: a second landmarker (one hand) fed a full-resolution crop around
        # the hand, created on first use. Separate instance = separate VIDEO-mode state.
        self._crop_options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_buffer=model_bytes),
            running_mode=VisionRunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=min_detection_confidence,
            min_hand_presence_confidence=min_presence_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._crop_landmarker = None
        self._crop_timestamp_ms: int = 0
        self.crop_max_side = 640  # a crop bigger than this is shrunk before MediaPipe

    def detect(self, frame_bgr: np.ndarray, roi: Optional[Tuple[int, int, int, int]] = None
               ) -> List[HandDetection]:
        """Detect hands in a BGR frame and return 21 normalized landmarks per hand.

        With `roi` = (x0, y0, x1, y1) in frame pixels, look only inside that crop, at the
        camera's full resolution (no 640 px downscale), and map the landmarks back to
        whole-frame coordinates. A far-away hand then covers many more model pixels.
        """
        if roi is not None:
            return self._detect_crop(frame_bgr, roi)
        t0 = time.perf_counter()
        h, w = frame_bgr.shape[:2]
        if self.detect_max_width > 0 and w > self.detect_max_width:
            scale = self.detect_max_width / float(w)
            frame_bgr = cv2.resize(frame_bgr, (self.detect_max_width, max(1, int(round(h * scale)))),
                                   interpolation=cv2.INTER_AREA)
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)

        timestamp_ms = int(time.perf_counter() * 1000)
        if timestamp_ms <= self._last_timestamp_ms:
            timestamp_ms = self._last_timestamp_ms + 1
        self._last_timestamp_ms = timestamp_ms

        result = self._landmarker.detect_for_video(mp_image, timestamp_ms)
        self.last_detect_ms = (time.perf_counter() - t0) * 1000.0
        detections: List[HandDetection] = []

        if not result.hand_landmarks:
            return detections

        for idx, hand_lms in enumerate(result.hand_landmarks):
            landmarks = [HandLandmark(x=lm.x, y=lm.y, z=lm.z) for lm in hand_lms]
            handedness = "Right"
            score = 1.0
            if result.handedness and idx < len(result.handedness) and result.handedness[idx]:
                handedness = result.handedness[idx][0].category_name or "Right"
                score = float(result.handedness[idx][0].score or 1.0)
            detections.append(HandDetection(landmarks=landmarks, handedness=handedness, score=score,
                                            world=_world(result, idx)))

        return detections

    @staticmethod
    def draw_hand(
        frame: np.ndarray,
        landmarks: Sequence[HandLandmark],
        guides: Sequence[Tuple[str, str]] = (),
    ) -> None:
        """
        Draw the hand skeleton plus thumb -> finger pinch guide lines.
        `guides` comes from `pinch_guides()`: (finger, state) with state
        "near" (yellow), "pinched" (green) or "action" (red).
        """
        h, w = frame.shape[:2]
        pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]

        # Marks follow the hand's size in the frame: at fixed sizes they cover a far hand of 30-50 pixels completely.
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        k = min(1.0, max(max(xs) - min(xs), max(ys) - min(ys)) / 160.0) if pts else 1.0

        def px(value: float, least: int = 1) -> int:
            return max(least, int(round(value * k)))

        pinched_any = any(state != "near" for _, state in guides)
        line_color = (0, 230, 118) if pinched_any else (220, 220, 220)
        for i, j in HAND_CONNECTIONS:
            cv2.line(frame, pts[i], pts[j], line_color, px(2), cv2.LINE_AA)
        for point in pts:
            cv2.circle(frame, point, px(4), (255, 180, 0), -1, cv2.LINE_AA)

        thumb_pt = pts[4]
        for finger, state in guides:
            tip_pt = pts[FINGER_TIPS[finger]]
            color = GUIDE_COLORS[state]
            cv2.line(frame, thumb_pt, tip_pt, color, px(3 if state != "near" else 2), cv2.LINE_AA)
            cv2.circle(frame, thumb_pt, px(8, 2), color, -1, cv2.LINE_AA)
            cv2.circle(frame, tip_pt, px(8, 2), color, -1, cv2.LINE_AA)
            if state != "near":
                mid = ((thumb_pt[0] + tip_pt[0]) // 2, (thumb_pt[1] + tip_pt[1]) // 2)
                cv2.circle(frame, mid, px(11, 3), color, 1 if k < .5 else 2, cv2.LINE_AA)

    # a crop is looked at again from slightly shifted places (fractions of its side): the errors of the looks differ a little
    TTA_SHIFTS = ((0.12, 0.06), (-0.12, -0.06), (0.06, -0.12), (-0.06, 0.12))

    def _crop_landmarker_for(self, slot: int):
        """Slot 0 is the usual crop landmarker; slots 1.. are the extra ones (made when first needed)."""
        if slot == 0:
            if self._crop_landmarker is None:
                self._crop_landmarker = mp.tasks.vision.HandLandmarker.create_from_options(self._crop_options)
            return self._crop_landmarker
        while len(self._tta_landmarkers) < slot:
            self._tta_landmarkers.append(mp.tasks.vision.HandLandmarker.create_from_options(self._crop_options))
            self._tta_timestamps.append(0)
        return self._tta_landmarkers[slot - 1]

    def _detect_crop(self, frame_bgr: np.ndarray, roi: Tuple[int, int, int, int]) -> List[HandDetection]:
        """One look at the crop, plus (for a small crop) `crop_tta` more from shifted crops, and the hand is their average."""
        t0 = time.perf_counter()
        base = self._crop_pass(frame_bgr, roi, 0)
        extra = int(getattr(self, "crop_tta", 0))
        x0, y0, x1, y1 = (int(v) for v in roi)
        side = max(x1 - x0, y1 - y0)
        if extra <= 0 or side > int(getattr(self, "crop_tta_max_side", 200)):
            return base
        h, w = frame_bgr.shape[:2]
        looks = [base]
        for slot in range(1, extra + 1):
            sx, sy = self.TTA_SHIFTS[(slot - 1) % len(self.TTA_SHIFTS)]
            dx, dy = int(round(sx * side)), int(round(sy * side))
            nx0 = max(0, min(w - (x1 - x0), x0 + dx))
            ny0 = max(0, min(h - (y1 - y0), y0 + dy))
            looks.append(self._crop_pass(frame_bgr, (nx0, ny0, nx0 + (x1 - x0), ny0 + (y1 - y0)), slot))
        merged = merge_looks(looks)
        self.last_detect_ms = (time.perf_counter() - t0) * 1000.0
        return merged

    def _crop_pass(self, frame_bgr: np.ndarray, roi: Tuple[int, int, int, int], slot: int = 0) -> List[HandDetection]:
        t0 = time.perf_counter()
        h, w = frame_bgr.shape[:2]
        x0, y0, x1, y1 = (int(v) for v in roi)
        x0, y0 = max(0, min(w - 1, x0)), max(0, min(h - 1, y0))
        x1, y1 = max(x0 + 1, min(w, x1)), max(y0 + 1, min(h, y1))
        crop = frame_bgr[y0:y1, x0:x1]
        cw, ch = x1 - x0, y1 - y0
        side = max(cw, ch)
        upscale_side = getattr(self, "crop_upscale_side", 0)
        if 0 < upscale_side and side < upscale_side:
            # Digital zoom for a far hand: enlarge the small crop with a good filter before MediaPipe, which would
            # otherwise stretch it itself. No new detail, but the hand reaches the size the models are made for.
            # Landmarks are normalized to the crop, so they still map back with (cw, ch).
            k = min(float(getattr(self, "crop_max_zoom", 4.0)), upscale_side / float(side))
            crop = cv2.resize(crop, (max(1, int(round(cw * k))), max(1, int(round(ch * k)))),
                              interpolation=cv2.INTER_CUBIC)
            side = max(crop.shape[1], crop.shape[0])
        if side > self.crop_max_side:
            scale = self.crop_max_side / float(side)
            crop = cv2.resize(crop, (max(1, int(round(cw * scale))), max(1, int(round(ch * scale)))),
                              interpolation=cv2.INTER_AREA)
        landmarker = self._crop_landmarker_for(slot)
        rgb = np.ascontiguousarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        timestamp_ms = int(time.perf_counter() * 1000)
        last = self._crop_timestamp_ms if slot == 0 else self._tta_timestamps[slot - 1]
        if timestamp_ms <= last:
            timestamp_ms = last + 1
        if slot == 0:
            self._crop_timestamp_ms = timestamp_ms
        else:
            self._tta_timestamps[slot - 1] = timestamp_ms
        result = landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), timestamp_ms)
        self.last_detect_ms = (time.perf_counter() - t0) * 1000.0
        detections: List[HandDetection] = []
        for idx, hand_lms in enumerate(result.hand_landmarks or []):
            # crop-normalized -> frame-normalized (z is in units of image width, like x)
            landmarks = [HandLandmark(x=(x0 + lm.x * cw) / w, y=(y0 + lm.y * ch) / h, z=lm.z * cw / w)
                         for lm in hand_lms]
            handedness, score = "Right", 1.0
            if result.handedness and idx < len(result.handedness) and result.handedness[idx]:
                handedness = result.handedness[idx][0].category_name or "Right"
                score = float(result.handedness[idx][0].score or 1.0)
            detections.append(HandDetection(landmarks=landmarks, handedness=handedness, score=score,
                                            from_crop=True, world=_world(result, idx)))
        return detections

    def close(self) -> None:
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None
        if self._crop_landmarker is not None:
            self._crop_landmarker.close()
            self._crop_landmarker = None
        for extra in self._tta_landmarkers:
            extra.close()
        self._tta_landmarkers, self._tta_timestamps = [], []


TRACKER_DEFAULTS = {
    "tracker_min_detection_confidence": 0.6,
    "tracker_min_presence_confidence": 0.5,
    "tracker_min_tracking_confidence": 0.5,
    "tracker_detect_max_width": 960,
    "tracker_crop_upscale_side": 512,   # a far hand's small crop is enlarged to this side before MediaPipe (0 = off)
    "tracker_crop_max_zoom": 16.0,      # ... by at most this factor (it was 4 until the "zoom as far as it goes" test)
    "tracker_crop_tta": 0,              # a small hand is looked at again in this many shifted crops and the joints averaged (0 = off)
    "tracker_crop_tta_max_side": 200,   # ... only while the crop is at most this many pixels wide
}


def tracker_options(config) -> dict:
    """HandTracker keyword arguments from config keys (bad values fall back to defaults)."""
    def number(key, low, high):
        try:
            value = float(config.get(key, TRACKER_DEFAULTS[key]))
        except (TypeError, ValueError):
            return TRACKER_DEFAULTS[key]
        return value if low <= value <= high else TRACKER_DEFAULTS[key]

    return {
        "min_detection_confidence": number("tracker_min_detection_confidence", 0.05, 0.95),
        "min_presence_confidence": number("tracker_min_presence_confidence", 0.05, 0.95),
        "min_tracking_confidence": number("tracker_min_tracking_confidence", 0.05, 0.95),
        # 0 = no downscale (full camera width)
        "detect_max_width": int(number("tracker_detect_max_width", 0, 4096)),
        "crop_upscale_side": int(number("tracker_crop_upscale_side", 0, 1024)),
        "crop_max_zoom": float(number("tracker_crop_max_zoom", 1, 32)),
        "crop_tta": int(number("tracker_crop_tta", 0, 4)),
        "crop_tta_max_side": int(number("tracker_crop_tta_max_side", 32, 640)),
    }
