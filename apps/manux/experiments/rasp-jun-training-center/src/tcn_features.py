"""
Per-frame features for the gesture model (TCN), the same in training (scripts/train_tcn.py) and in the app.

From the 3D hand joints (MediaPipe world landmarks, metres around the hand) the hand is put in its OWN frame:
origin at the wrist, y toward the middle-finger knuckle, x across the knuckles (index -> little finger), z out of
the palm, scaled by the wrist -> middle-knuckle length. The same hand shape then gives the same numbers from any
camera angle or seat (the 2026-11-05 recording had three postures; the rules recognised the gestures 10/15 in the
usual one and 5/15 with the phone moved). Added per frame:
- the 21 joints in that frame (63) and their change since the last frame (63),
- the thumb tip's distance to each fingertip, relative to the hand size (4),
- how fast the palm moves in the image, in hand sizes per second (2): moving the cursor with a half fist vs a
  deliberate pinch,
- "a hand was seen" (1).
A frame without a hand is all zeros with that flag 0.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np

FEATURE_VERSION = "local3d-v1"
PIPELINE_VERSION = "continuous-v2"
MAX_FRAME_GAP = 0.25
N_JOINTS = 21
FEATURES = 63 + 63 + 4 + 2 + 1
THUMB_TIP, TIPS = 4, (8, 12, 16, 20)
PALM = (0, 5, 9, 13, 17)


def hand_frame(world: np.ndarray) -> Optional[np.ndarray]:
    """(21, 3) world joints -> (21, 3) in the hand's own frame, scaled to the hand size. None if degenerate."""
    try:
        world = np.asarray(world, dtype=np.float64)
    except (ValueError, TypeError):
        return None
    if world.shape != (21, 3) or not np.isfinite(world).all():
        return None
    w = world - world[0]
    y = w[9]
    size = np.linalg.norm(y)
    if size < 1e-6:
        return None
    y = y / size
    x = w[5] - w[17]
    x = x - np.dot(x, y) * y
    nx = np.linalg.norm(x)
    if nx < 1e-6:
        return None
    x = x / nx
    z = np.cross(x, y)
    return (w @ np.stack([x, y, z], axis=1)) / size


def frame_features(frames: Sequence[dict], aspect: float, fps: float = 30.0) -> np.ndarray:
    """(n, FEATURES) for a run of recorded frames (dicts with "world", "lm", "t")."""
    out = np.zeros((len(frames), FEATURES), dtype=np.float32)
    prev_local: Optional[np.ndarray] = None
    prev_palm = None
    prev_t = None
    for i, f in enumerate(frames):
        world = f.get("world")
        lm = f.get("lm")
        if world is None or lm is None:
            prev_local, prev_palm, prev_t = None, None, None
            continue
        local = hand_frame(np.asarray(world, dtype=np.float64))
        try:
            image = np.asarray(lm, dtype=np.float64)
        except (ValueError, TypeError):
            prev_local, prev_palm, prev_t = None, None, None
            continue
        if local is None or image.shape != (21, 3) or not np.isfinite(image).all():
            prev_local, prev_palm, prev_t = None, None, None
            continue
        img = image[:, :2] * np.array([1.0, aspect])   # square image units
        size_img = max(np.linalg.norm(img[9] - img[0]), 1e-6)
        palm = img[list(PALM)].mean(axis=0)
        t = float(f.get("t", i / fps))
        if not np.isfinite(t):
            prev_local, prev_palm, prev_t = None, None, None
            continue
        if prev_t is not None and not 0 < t - prev_t <= MAX_FRAME_GAP:
            prev_local, prev_palm = None, None
        out[i, 0:63] = local.reshape(-1)
        if prev_local is not None:
            out[i, 63:126] = (local - prev_local).reshape(-1)
        thumb = local[THUMB_TIP]
        out[i, 126:130] = [np.linalg.norm(thumb - local[k]) for k in TIPS]
        if prev_palm is not None and prev_t is not None and t > prev_t:
            out[i, 130:132] = (palm - prev_palm) / size_img / (t - prev_t) / 10.0   # ~ -1..1
        out[i, 132] = 1.0
        prev_local, prev_palm, prev_t = local, palm, t
    return out


def temporal_inputs(frames: Sequence[dict], aspect: float, length: int = 15, *, include_windows=True):
    """One physical timeline for recording and inference. Labels never alter model inputs.

    Missing-hand frames remain zero steps in the timeline; a real timestamp gap starts
    a new window. This also prevents a disconnected camera from confirming an old pose.
    """
    feats = frame_features(frames, aspect)
    segments = np.zeros(len(frames), dtype=np.int64)
    x = np.zeros((len(frames), length, FEATURES), np.float32) if include_windows else None
    start = 0
    for i in range(1, len(frames) + 1):
        gap = i == len(frames)
        if not gap:
            dt = float(frames[i].get("t", i / 30)) - float(frames[i - 1].get("t", (i - 1) / 30))
            gap = not 0 < dt <= MAX_FRAME_GAP
        if gap:
            segments[start:i] = start
            if include_windows and i > start:
                x[start:i] = windows(feats[start:i], length)
            start = i
    return feats, x, segments


def windows(feats: np.ndarray, length: int) -> np.ndarray:
    """(n, length, FEATURES): for each frame the `length` frames ending at it (zero-padded at the start)."""
    pad = np.zeros((length - 1, feats.shape[1]), dtype=feats.dtype)
    x = np.concatenate([pad, feats], axis=0)
    idx = np.arange(len(feats))[:, None] + np.arange(length)[None, :]
    return x[idx]
