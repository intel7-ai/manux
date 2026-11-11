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
N_JOINTS = 21
FEATURES = 63 + 63 + 4 + 2 + 1
THUMB_TIP, TIPS = 4, (8, 12, 16, 20)
PALM = (0, 5, 9, 13, 17)


def hand_frame(world: np.ndarray) -> Optional[np.ndarray]:
    """(21, 3) world joints -> (21, 3) in the hand's own frame, scaled to the hand size. None if degenerate."""
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
    n = len(frames)
    out = np.zeros((n, FEATURES), dtype=np.float32)
    if n == 0:
        return out
    try:
        if all(
            f.get("world") is not None and f.get("lm") is not None
            and len(f["world"]) == 21 and len(f["lm"]) == 21
            for f in frames
        ):
            worlds = np.asarray([f["world"] for f in frames], dtype=np.float64)
            lms = np.asarray([f["lm"] for f in frames], dtype=np.float64)
            if worlds.shape == (n, 21, 3) and lms.shape == (n, 21, 3):
                w = worlds - worlds[:, 0:1, :]
                y = w[:, 9, :]
                size = np.linalg.norm(y, axis=1)
                if np.all(size >= 1e-6):
                    y = y / size[:, None]
                    x = w[:, 5, :] - w[:, 17, :]
                    x = x - np.sum(x * y, axis=1, keepdims=True) * y
                    nx = np.linalg.norm(x, axis=1)
                    if np.all(nx >= 1e-6):
                        x = x / nx[:, None]
                        z = np.cross(x, y)
                        basis = np.stack([x, y, z], axis=2)
                        local = (w @ basis) / size[:, None, None]
                        img = lms[:, :, :2] * np.array([1.0, aspect], dtype=np.float64)
                        size_img = np.maximum(np.linalg.norm(img[:, 9, :] - img[:, 0, :], axis=1), 1e-6)
                        palm = img[:, list(PALM), :].mean(axis=1)
                        t = np.array([float(f.get("t", i / fps)) for i, f in enumerate(frames)], dtype=np.float64)
                        out[:, 0:63] = local.reshape(n, 63)
                        if n > 1:
                            out[1:, 63:126] = (local[1:] - local[:-1]).reshape(n - 1, 63)
                        thumb = local[:, THUMB_TIP:THUMB_TIP + 1, :]
                        tips = local[:, list(TIPS), :]
                        out[:, 126:130] = np.linalg.norm(thumb - tips, axis=2)
                        if n > 1:
                            dt = t[1:] - t[:-1]
                            valid_dt = dt > 0
                            if np.any(valid_dt):
                                vel = np.zeros((n - 1, 2), dtype=np.float64)
                                vel[valid_dt] = (
                                    (palm[1:][valid_dt] - palm[:-1][valid_dt])
                                    / size_img[1:, None][valid_dt]
                                    / dt[valid_dt, None]
                                    / 10.0
                                )
                                out[1:, 130:132] = vel
                        out[:, 132] = 1.0
                        return out
    except (TypeError, ValueError, KeyError):
        out.fill(0.0)

    prev_local: Optional[np.ndarray] = None
    prev_palm = None
    prev_t = None
    for i, f in enumerate(frames):
        world = f.get("world")
        lm = f.get("lm")
        if not world or not lm:
            prev_local, prev_palm, prev_t = None, None, None
            continue
        local = hand_frame(np.asarray(world, dtype=np.float64))
        if local is None:
            prev_local, prev_palm, prev_t = None, None, None
            continue
        img = np.asarray(lm, dtype=np.float64)[:, :2] * np.array([1.0, aspect])   # square image units
        size_img = max(np.linalg.norm(img[9] - img[0]), 1e-6)
        palm = img[list(PALM)].mean(axis=0)
        t = float(f.get("t", i / fps))
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


def windows(feats: np.ndarray, length: int) -> np.ndarray:
    """(n, length, FEATURES): for each frame the `length` frames ending at it (zero-padded at the start)."""
    pad = np.zeros((length - 1, feats.shape[1]), dtype=feats.dtype)
    x = np.concatenate([pad, feats], axis=0)
    idx = np.arange(len(feats))[:, None] + np.arange(length)[None, :]
    return x[idx]
