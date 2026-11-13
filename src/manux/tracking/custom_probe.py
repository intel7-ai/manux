"""Original a700452 in-memory hand crop (no camera acquisition or file writes)."""
from __future__ import annotations
from typing import List, Tuple
SHOT_SIZE = 160  # Original pose_probe.SHOT_SIZE; preview remains memory-only.
HAND_CONNECTIONS: List[Tuple[int, int]] = [
    (0, 1), (1, 2), (2, 3), (3, 4),        # Thumb
    (0, 5), (5, 6), (6, 7), (7, 8),        # Index
    (5, 9), (9, 10), (10, 11), (11, 12),   # Middle
    (9, 13), (13, 14), (14, 15), (15, 16), # Ring
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20), # Pinky & Palm
]

def hand_shot(frame, landmarks, size: int = SHOT_SIZE):
    """A square crop around the hand (a quarter of its extent as margin) with the joints drawn, `size` px. None when the
    hand is off the frame. Only for the screen: see the module note."""
    import cv2
    if frame is None or landmarks is None or len(landmarks) < 21:
        return None
    h, w = frame.shape[:2]
    xs = [p.x * w for p in landmarks]
    ys = [p.y * h for p in landmarks]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    half = max(max(xs) - min(xs), max(ys) - min(ys)) * .625 + 4
    x0, y0, x1, y1 = int(cx - half), int(cy - half), int(cx + half), int(cy + half)
    cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if cx1 - cx0 < 8 or cy1 - cy0 < 8:
        return None
    crop = cv2.copyMakeBorder(frame[cy0:cy1, cx0:cx1], cy0 - y0, y1 - cy1, cx0 - x0, x1 - cx1, cv2.BORDER_CONSTANT)
    k = size / max(1, crop.shape[1])
    crop = cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)
    pts = [(int((x - x0) * k), int((y - y0) * k)) for x, y in zip(xs, ys)]
    for i, j in HAND_CONNECTIONS:
        cv2.line(crop, pts[i], pts[j], (120, 220, 90), 2, cv2.LINE_AA)
    for q in pts:
        cv2.circle(crop, q, 3, (255, 255, 255), -1, cv2.LINE_AA)
    return crop
