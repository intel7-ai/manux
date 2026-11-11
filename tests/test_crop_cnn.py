"""Hand pictures and the small CNN (synthetic pictures, no camera)."""
import gzip
import json
from types import SimpleNamespace

import numpy as np
import pytest

from src.crop_cnn import (CropNet, auc, augment, build_samples, evaluate_condition, predict_cnn, run_fires,
                          stack_indexes, train_cnn)
from src.crop_recorder import CropRecorder, crop_hand, crops_path, hand_square, load_crops


def hand(cx, cy, half):
    """21 joints spread over a square of +-half (fractions of the frame) round (cx, cy)."""
    pts = []
    for i in range(21):
        a = i / 20
        pts.append(SimpleNamespace(x=cx - half + 2 * half * a, y=cy - half + 2 * half * (1 - a), z=0.0))
    return pts


def test_the_square_is_inside_the_frame_and_at_least_the_minimum():
    x0, y0, side = hand_square(hand(0.5, 0.5, 0.01), 1280, 720)
    assert side >= 24 and 0 <= x0 <= 1280 - side and 0 <= y0 <= 720 - side
    x0, y0, side = hand_square(hand(0.99, 0.99, 0.01), 1280, 720)       # in the corner: moved back inside
    assert x0 + side <= 1280 and y0 + side <= 720
    assert hand_square([], 1280, 720) is None


def test_the_crop_is_a_48_by_48_grey_picture_of_the_hand():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[340:380, 620:660] = 255                                        # a bright patch where the hand is
    pic = crop_hand(frame, hand(0.5, 0.5, 0.016))
    assert pic.shape == (48, 48) and pic.dtype == np.uint8 and pic.mean() > 100


def test_saved_pictures_keep_their_frame_index(tmp_path):
    rec = CropRecorder()
    frame = np.full((720, 1280, 3), 80, dtype=np.uint8)
    assert rec.add(7, frame, hand(0.5, 0.5, 0.02)) and rec.add(9, frame, hand(0.4, 0.5, 0.02))
    assert not rec.add(10, frame, [])
    target = crops_path(tmp_path / "a.jsonl.gz")
    assert target.parent.name == "crops" and rec.save(target) == target
    index, crops = load_crops(target)
    assert list(index) == [7, 9] and crops.shape == (2, 48, 48)
    assert CropRecorder().save(tmp_path / "none.npz") is None


def test_the_stack_looks_back_and_repeats_the_first_picture():
    got = stack_indexes(5, frames=3, step=2)
    assert got[4].tolist() == [0, 2, 4] and got[0].tolist() == [0, 0, 0] and got[1].tolist() == [0, 0, 1]


def test_the_net_gives_one_score_per_class_and_ignores_brightness():
    net = CropNet(3).eval()
    import torch
    x = torch.rand(4, 6, 48, 48)
    assert net(x).shape == (4, 3)
    assert torch.allclose(net(x), net(x * 0.5 + 0.2), atol=1e-3)
    assert augment(x).shape == x.shape


def synthetic(n_per=60, seed=0):
    """Class 0 = a flat grey picture with noise, class 1 = the same with a bright bar: easy to tell apart."""
    rng = np.random.default_rng(seed)
    pics = rng.integers(60, 110, size=(2 * n_per, 48, 48)).astype(np.uint8)
    pics[n_per:, 20:28, 8:40] = 230
    y = np.array([0] * n_per + [1] * n_per)
    idx = stack_indexes(2 * n_per)
    return pics, idx, y


def test_the_cnn_learns_a_simple_picture_difference():
    pics, idx, y = synthetic()
    model = train_cnn(pics, idx, y, 2, epochs=6)
    prob = predict_cnn(model, pics, idx, np.arange(len(y)))
    assert (prob.argmax(1) == y).mean() > 0.9


def test_auc_and_the_fire_rule():
    s = np.array([0.9, 0.8, 0.2, 0.1])
    assert auc(s, np.array([True, True, False, False])) == 1.0
    assert auc(s, np.array([False, False, True, True])) == 0.0
    prob = np.array([0.9, 0.9, 0.1, 0.9, 0.9, 0.9, 0.9])
    assert run_fires(prob, 0.7, 4, ["a"] * 7) == [6]                    # the first run is too short
    assert run_fires(prob, 0.7, 4, ["a"] * 3 + ["b"] * 4) == [6]


def test_evaluation_counts_reps_and_fires_away_from_the_asked_pinch():
    n = 120
    t = np.arange(n) / 30.0
    is_do = np.zeros(n, dtype=bool)
    is_do[30:50] = True
    tokens = [("f", 0, 1 if d else None, bool(d)) for d in is_do]
    rep_of = [tok[:3] for tok in tokens]
    prob = np.zeros(n)
    prob[35:45] = 0.95                                                   # right on the asked pinch
    prob[90:100] = 0.95                                                  # a false fire, far from it
    r = evaluate_condition(prob, is_do, rep_of, t, tokens)
    assert (r["hit"], r["reps"], r["false_fires"]) == (1, 1, 1) and r["quiet_minutes"] > 0


def test_build_samples_pairs_pictures_with_labels(tmp_path):
    path = tmp_path / "20261109-000000-guided.jsonl.gz"
    lines = [{"type": "meta"}]
    for i in range(10):
        phase = "do" if 4 <= i < 8 else "prep"
        lines.append({"type": "frame", "t": i / 30, "label": {"task": "thumb_index", "phase": phase, "set": 0,
                                                              "rep": 1, "condition_key": "far"}})
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(x) for x in lines) + "\n")
    rec = CropRecorder()
    frame = np.full((720, 1280, 3), 90, dtype=np.uint8)
    for i in range(10):
        rec.add(i, frame, hand(0.5, 0.5, 0.02))
    rec.save(crops_path(path))
    data = build_samples([path])
    assert len(data["y"]) == 10 and data["pics"].shape == (10, 48, 48) and data["idx"].shape == (10, 6)
    assert data["classes"][0] == "none" and len(data["classes"]) == 2
    assert int((data["y"] != 0).sum()) == 4
    with pytest.raises(ValueError):
        build_samples([tmp_path / "missing.jsonl.gz"])


def test_centre_crop_keeps_the_middle_and_zero_keeps_all():
    from src.crop_cnn import centre_crop
    pics = np.zeros((2, 48, 48), dtype=np.uint8)
    pics[:, 24, 24] = 9
    got = centre_crop(pics, 36)
    assert got.shape == (2, 36, 36) and got[:, 18, 18].tolist() == [9, 9]
    assert centre_crop(pics, 0) is pics and centre_crop(pics, 48) is pics
    net = CropNet(2, size=36).eval()
    import torch
    assert net(torch.rand(1, 6, 36, 36)).shape == (1, 2)


def test_adaptive_crop_cuts_small_hands_and_keeps_big_ones_whole():
    from src.crop_cnn import adaptive_crop
    pics = np.zeros((2, 48, 48), dtype=np.uint8)
    pics[0, 0:6, :] = 200                                      # background on the border
    pics[1, 0:6, :] = 200
    got = adaptive_crop(pics, np.array([0.02, 0.04], dtype=np.float32), big=0.03)
    assert got.shape == (2, 40, 40)
    assert got[0].max() == 0 and got[1].max() > 100            # small hand: border gone; big hand: border kept
