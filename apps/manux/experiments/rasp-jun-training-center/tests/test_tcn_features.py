import math

import numpy as np

from src.tcn_features import FEATURES, frame_features, hand_frame, windows


def _hand():
    rng = np.random.default_rng(0)
    w = rng.normal(0, 0.03, (21, 3))
    w[0] = 0.0
    w[9] = (0.0, 0.08, 0.0)
    w[5], w[17] = (0.02, 0.07, 0.0), (-0.03, 0.06, 0.0)
    return w


def test_hand_frame_is_the_same_from_any_camera_angle_and_distance():
    w = _hand()
    a = math.radians(40)
    rot = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    turned = (w @ rot.T) * 1.7 + 0.2
    assert np.allclose(hand_frame(w), hand_frame(turned), atol=1e-9)


def test_frame_features_and_windows_shapes_and_missing_hand():
    w = _hand()
    lm = [[0.5 + p[0], 0.5 + p[1], 0.0] for p in w]
    frames = [{"t": 0.0, "world": w.tolist(), "lm": lm}, {"t": 0.033}, {"t": 0.066, "world": w.tolist(), "lm": lm}]
    f = frame_features(frames, 0.5625)
    assert f.shape == (3, FEATURES) and f[0, -1] == 1.0 and not f[1].any() and f[2, -1] == 1.0
    x = windows(f, 15)
    assert x.shape == (3, 15, FEATURES) and np.array_equal(x[2, -1], f[2]) and not x[0, :-1].any()
