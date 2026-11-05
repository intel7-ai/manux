"""local3d-v1 values are pinned: a formula change must come with a new FEATURE_VERSION.

The expected numbers were produced by Dan's original `src/tcn_features.py`
(hand-mouse-gesture-overhaul@7448e4a) on this synthetic hand. The port was also checked equal
(bit for bit) to the original on a full guided recording, outside the repository (D-006).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from manux.core.contracts import FrameInfo, HandObservation
from manux.perception import local3d
from manux.perception.local3d import FEATURE_VERSION, FEATURES, Local3dStream, frame_features, hand_frame, windows

ASPECT = 0.5625


def synthetic_hand(k: int):
    """A plausible right hand that moves a little with k (no real person's data)."""
    world, lm = [], []
    for j in range(21):
        f, s = ((j - 1) // 4, (j - 1) % 4) if j else (-1, 0)
        x = 0.0 if j == 0 else (f - 1.5) * 0.02 + 0.003 * s * math.sin(k * 0.3 + f)
        y = 0.0 if j == 0 else -(0.03 + 0.02 * s) - 0.004 * f
        z = 0.0 if j == 0 else 0.004 * s * math.cos(k * 0.2 + f) - 0.002 * f
        world.append([round(x, 5), round(y, 5), round(z, 5)])
        lm.append([round(0.5 + x * 2 + 0.01 * k, 4), round(0.6 + y * 2, 4), round(z, 4)])
    return world, lm


def synthetic_frames():
    frames = []
    for k in range(4):
        w, l = synthetic_hand(k)
        frames.append({"t": k / 30, "world": w, "lm": l})
    frames.insert(2, {"t": 2 / 30 - 0.001})       # one frame without a hand breaks the chain
    return frames


# per frame: block sums (joints, change, thumb distances, palm speed, flag), weighted sum, picked values
PINNED = [
    ([39.194993, 0.0, 5.852486, 0.0, 1.0], 15.846155, [0.929465, 0.538462, 1.969231, 0.0, 0.730584, 2.023554, 0.0]),
    ([39.129477, -0.065515, 5.521276, 0.635633, 1.0], 16.205863, [0.929465, 0.538462, 1.986897, 0.00591, 0.707591, 1.909295, 0.635633]),
    ([0.0, 0.0, 0.0, 0.0, 0.0], 0.0, [0.0] * 7),
    ([39.103538, 0.0, 5.185308, 0.0, 1.0], 15.296609, [0.929465, 0.538462, 2.004231, 0.0, 0.67434, 1.812787, 0.0]),
    ([39.11571, 0.012172, 4.870724, 0.635633, 1.0], 15.746971, [0.929465, 0.538462, 2.019808, 0.005205, 0.633986, 1.742503, 0.635633]),
]
BLOCKS = ((0, 63), (63, 126), (126, 130), (130, 132), (132, 133))
PICKED = (3, 4, 13, 70, 126, 129, 130)


def test_version_and_size():
    assert FEATURE_VERSION == "local3d-v1"
    assert FEATURES == 133


def test_values_are_pinned():
    feats = frame_features(synthetic_frames(), ASPECT).astype(np.float64)
    assert feats.shape == (5, FEATURES) and feats.dtype == np.float64
    weights = np.arange(1, FEATURES + 1) / FEATURES
    for row, (sums, weighted, picked) in zip(feats, PINNED):
        assert [row[a:b].sum() for a, b in BLOCKS] == pytest.approx(sums, abs=2e-6)
        assert (row * weights).sum() == pytest.approx(weighted, abs=2e-6)
        assert [row[j] for j in PICKED] == pytest.approx(picked, abs=2e-6)


def test_hand_frame_axes():
    w, _ = synthetic_hand(0)
    local = hand_frame(np.asarray(w, dtype=np.float64))
    assert local[0] == pytest.approx([0, 0, 0])
    assert local[9] == pytest.approx([0, 1, 0], abs=1e-9)       # y = wrist -> middle knuckle, unit length
    assert local[5][0] > local[17][0]                           # x points from little to index knuckle
    assert hand_frame(np.zeros((21, 3))) is None                # degenerate


def test_missing_world_or_hand_gives_zeros():
    w, l = synthetic_hand(0)
    feats = frame_features([{"t": 0.0, "lm": l}, {"t": 0.03, "world": w}, {"t": 0.06, "world": w, "lm": l},
                            {"t": 0.09, "world": [[0.0, 0.0, 0.0]] * 21, "lm": l}], ASPECT)
    assert not feats[0].any() and not feats[1].any() and not feats[3].any()
    assert feats[2, 132] == 1.0 and not feats[2, 63:126].any()   # first hand after a gap: no change values


def test_windows_pad_at_the_start():
    feats = frame_features(synthetic_frames(), ASPECT)
    w = windows(feats, 3)
    assert w.shape == (5, 3, FEATURES)
    assert not w[0, :2].any() and np.array_equal(w[0, 2], feats[0])
    assert np.array_equal(w[4], feats[2:5])


def test_live_stream_gives_the_same_windows():
    frames = synthetic_frames() + [{"t": 5 / 30 + i / 30, "world": synthetic_hand(4 + i)[0], "lm": synthetic_hand(4 + i)[1]}
                                   for i in range(20)]
    expected = windows(frame_features(frames, ASPECT), 15)
    stream = Local3dStream(15)
    for i, f in enumerate(frames):
        info = FrameInfo(seq=i, t=f["t"], width=1280, height=720, mirrored=True)
        hand = HandObservation(info, tuple(map(tuple, f["lm"])), "Right", 0.9, world=tuple(map(tuple, f["world"]))) \
            if "world" in f else None
        got = stream.push(hand, f["t"], info.aspect)
        assert np.array_equal(got, expected[i]), i
    stream.reset()
    assert not stream.push(None, 1.0, ASPECT).any()


def test_live_hand_without_world_is_a_no_hand_frame():
    w, l = synthetic_hand(0)
    info = FrameInfo(seq=0, t=0.0, width=1280, height=720, mirrored=True)
    hand = HandObservation(info, tuple(map(tuple, l)), "Right", 0.9)
    assert local3d.observation_frame(hand, 0.0) == {"t": 0.0}
