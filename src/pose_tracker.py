"""
Arm tracking with MediaPipe Pose (2026-11-04): shoulder, elbow and wrist of the arm that moves the cursor.

The hand model only sees the hand. To show the shoulder and the real elbow on the preview, and to give the palm /
fist cursor correction (src/elbow_pivot.py) a measured elbow instead of one guessed from arcs, the Pose Landmarker
(models/pose_landmarker_lite.task, Google's official model) runs every few frames. The arm is the one whose Pose
wrist is closest to the hand the hand model found, so it does not depend on mirroring or left / right labels.

`PoseTracker.detect(frame)` -> all 33 landmarks of the first person (normalised x, y, visibility) or None.
`pick_arm(landmarks, hand_wrist)` -> {"shoulder", "elbow", "wrist": (x, y, visibility)} for the closer arm.
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

DEFAULT_POSE_MODEL = Path(__file__).resolve().parent.parent / "models" / "pose_landmarker_lite.task"

# MediaPipe Pose indices (the person's own left / right)
ARMS = {"left": (11, 13, 15), "right": (12, 14, 16)}

Joint = Tuple[float, float, float]   # x, y (normalised image), visibility 0..1


def pick_arm(landmarks: Sequence[Joint], hand_wrist: Optional[Tuple[float, float]]) -> Optional[Dict[str, Joint]]:
    """The arm whose wrist is nearest the tracked hand (or the more visible arm without a hand)."""
    if not landmarks or len(landmarks) < 17:
        return None
    best, best_key = None, None
    for side, (s, e, w) in ARMS.items():
        wx, wy, wv = landmarks[w]
        if hand_wrist is not None:
            key = math.hypot(wx - hand_wrist[0], wy - hand_wrist[1])
        else:
            key = -(landmarks[s][2] + landmarks[e][2] + wv)
        if best_key is None or key < best_key:
            best, best_key = side, key
    s, e, w = ARMS[best]
    return {"side": best, "shoulder": landmarks[s], "elbow": landmarks[e], "wrist": landmarks[w]}


class PoseTracker:
    def __init__(self, model_path: Optional[Path] = None, detect_max_width: int = 640) -> None:
        import mediapipe as mp

        self._mp = mp
        path = Path(model_path or DEFAULT_POSE_MODEL)
        if not path.exists():
            raise FileNotFoundError(f"Pose model not found: {path}")
        options = mp.tasks.vision.PoseLandmarkerOptions(
            # Read with Python so Windows paths containing Korean also work.
            base_options=mp.tasks.BaseOptions(model_asset_buffer=path.read_bytes()),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._landmarker = mp.tasks.vision.PoseLandmarker.create_from_options(options)
        self._last_ts = 0
        self.detect_max_width = detect_max_width
        self.last_detect_ms = 0.0

    def detect(self, frame_bgr: np.ndarray) -> Optional[List[Joint]]:
        t0 = time.perf_counter()
        h, w = frame_bgr.shape[:2]
        if self.detect_max_width > 0 and w > self.detect_max_width:
            k = self.detect_max_width / float(w)
            frame_bgr = cv2.resize(frame_bgr, (self.detect_max_width, max(1, int(round(h * k)))), interpolation=cv2.INTER_AREA)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        ts = max(self._last_ts + 1, int(time.perf_counter() * 1000))
        self._last_ts = ts
        result = self._landmarker.detect_for_video(image, ts)
        self.last_detect_ms = (time.perf_counter() - t0) * 1000.0
        if not result.pose_landmarks:
            return None
        return [(lm.x, lm.y, float(getattr(lm, "visibility", 1.0) or 0.0)) for lm in result.pose_landmarks[0]]

    def close(self) -> None:
        try:
            self._landmarker.close()
        except Exception:
            pass
