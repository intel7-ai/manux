"""`local3d-v1` features: the hand in its own 3D frame (docs/contracts.md section 4).

Ported value for value from Dan's `hand-mouse-gesture-overhaul@7448e4a` `src/tcn_features.py`
(the TCN in `models/gesture_tcn.onnx` was trained on exactly these numbers).
`tests/unit/perception/test_local3d.py` pins the values of a synthetic hand, so a change in the
formula fails a test. **Any change to a value means a new version string** (`local3d-v2`):
a model trained on v1 would otherwise read wrong numbers without any error.

From the 3D joints (`world`, MediaPipe world landmarks in metres) the hand is put in its own
frame: origin at the wrist, y toward the middle-finger knuckle (9), x from the little-finger
knuckle (17) to the index knuckle (5) with its y part removed, z = x cross y, all divided by the
wrist to middle-knuckle length. Per frame (133 values):

| columns   | what |
| --------- | ---- |
| 0..62     | the 21 joints in that frame |
| 63..125   | their change since the previous frame (0 when the previous frame had no hand) |
| 126..129  | thumb tip to index / middle / ring / little tip, in hand sizes |
| 130..131  | palm centre speed in the image, hand sizes per second / 10 (image x, y with y scaled by `aspect`) |
| 132       | 1 when a hand was seen |

A frame without a hand, without `world`, or with a degenerate hand is all zeros (flag 0) and
breaks the "previous frame" chain. `aspect` is `height / width` of the frame (`FrameInfo.aspect`,
or `frame_aspect` in a recording's meta).

Pure functions: `frame_features` over a whole run of frames, `Local3dStream` for the live engine
(the same rows, one frame at a time; tested equal). numpy only.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Deque, Mapping, Optional, Sequence

import numpy as np

from manux.core.contracts import HandObservation

FEATURE_VERSION = "local3d-v1"
N_JOINTS = 21
FEATURES = 63 + 63 + 4 + 2 + 1
THUMB_TIP, TIPS = 4, (8, 12, 16, 20)
PALM = (0, 5, 9, 13, 17)
DEFAULT_ASPECT = 0.5625   # 16:9, what recordings without `frame_aspect` assume


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


class _Chain:
    """The previous-frame state that the change and speed columns need."""

    __slots__ = ("local", "palm", "t")

    def __init__(self) -> None:
        self.local: Optional[np.ndarray] = None
        self.palm: Optional[np.ndarray] = None
        self.t: Optional[float] = None

    def clear(self) -> None:
        self.local = self.palm = self.t = None

    def row(self, world: Any, lm: Any, t: float, aspect: float) -> np.ndarray:
        """One frame's 133 values (float32). Updates the chain."""
        out = np.zeros(FEATURES, dtype=np.float32)
        if not world or not lm:   # (sequences: empty / None means no hand)
            self.clear()
            return out
        local = hand_frame(np.asarray(world, dtype=np.float64))
        if local is None:
            self.clear()
            return out
        img = np.asarray(lm, dtype=np.float64)[:, :2] * np.array([1.0, aspect])   # square image units
        size_img = max(np.linalg.norm(img[9] - img[0]), 1e-6)
        palm = img[list(PALM)].mean(axis=0)
        out[0:63] = local.reshape(-1)
        if self.local is not None:
            out[63:126] = (local - self.local).reshape(-1)
        thumb = local[THUMB_TIP]
        out[126:130] = [np.linalg.norm(thumb - local[k]) for k in TIPS]
        if self.palm is not None and self.t is not None and t > self.t:
            out[130:132] = (palm - self.palm) / size_img / (t - self.t) / 10.0   # ~ -1..1
        out[132] = 1.0
        self.local, self.palm, self.t = local, palm, t
        return out


def frame_features(frames: Sequence[Mapping[str, Any]], aspect: float, fps: float = 30.0) -> np.ndarray:
    """(n, FEATURES) for a run of recorded frames (dicts with "world", "lm", "t").

    A frame without "t" gets `index / fps` (as in the original).
    """
    out = np.zeros((len(frames), FEATURES), dtype=np.float32)
    chain = _Chain()
    for i, f in enumerate(frames):
        out[i] = chain.row(f.get("world"), f.get("lm"), float(f.get("t", i / fps)), aspect)
    return out


def windows(feats: np.ndarray, length: int) -> np.ndarray:
    """(n, length, FEATURES): for each frame the `length` frames ending at it (zero-padded at the start)."""
    pad = np.zeros((length - 1, feats.shape[1]), dtype=feats.dtype)
    x = np.concatenate([pad, feats], axis=0)
    idx = np.arange(len(feats))[:, None] + np.arange(length)[None, :]
    return x[idx]


def observation_frame(hand: Optional[HandObservation], t: float) -> Mapping[str, Any]:
    """A live hand as the recorded-frame dict `frame_features` reads (no `world` -> a no-hand frame)."""
    if hand is None or hand.world is None:
        return {"t": t}
    return {"t": t, "world": hand.world, "lm": hand.landmarks}


class Local3dStream:
    """Live features: push one frame at a time, get the last `window` rows (zero-padded at the start).

    Gives exactly the rows `windows(frame_features(all frames so far), window)[-1]` would (tested),
    without recomputing the window every frame.
    """

    def __init__(self, window: int) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        self.window = window
        self._chain = _Chain()
        self._rows: Deque[np.ndarray] = deque(maxlen=window)

    def reset(self) -> None:
        self._chain.clear()
        self._rows.clear()

    def push(self, hand: Optional[HandObservation], t: float, aspect: float) -> np.ndarray:
        f = observation_frame(hand, t)
        return self.push_frame(f.get("world"), f.get("lm"), t, aspect)

    def push_frame(self, world: Any, lm: Any, t: float, aspect: float) -> np.ndarray:
        """(window, FEATURES) float32 ending at this frame."""
        self._rows.append(self._chain.row(world, lm, t, aspect))
        out = np.zeros((self.window, FEATURES), dtype=np.float32)
        n = len(self._rows)
        out[self.window - n:] = np.stack(self._rows)
        return out
