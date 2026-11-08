"""Synthetic V timing/geometry and selected-model acceptance; no webcam data."""
import math

import pytest

from manux.core.contracts import FrameContext, FrameInfo, HandObservation, OutputTarget, Stage
from manux.features.control import ControlFeature, V_SIGN
import pinch_hands as H


def v_hand():
    pts = H.fist()
    opened = H.open_hand()
    for i in (5, 6, 7, 8, 9, 10, 11, 12):
        pts[i] = opened[i]
    return pts


def step(feature, t, points=None, **settings):
    frame = FrameInfo(round(t * 100), t, 1280, 720, True)
    ctx = FrameContext(frame, Stage.MAIN, OutputTarget.NONE, "pinch", settings)
    return feature.step(HandObservation(frame, tuple(points or v_hand()), "Right", .9), ctx)


def test_hold_cooldown_and_release_to_rearm():
    f = ControlFeature()
    assert not step(f, 0)
    assert not step(f, .2)
    assert not step(f, .4)
    assert not step(f, .6)
    assert [i.gesture for i in step(f, .71)] == [V_SIGN]
    for n in range(8, 50):
        assert not step(f, n / 10)
    assert not step(f, 5, H.open_hand())
    assert not f.activity().holding
    assert not step(f, 5.1)
    assert not step(f, 5.3)
    assert not step(f, 5.5)
    assert not step(f, 5.7)
    assert step(f, 5.81)


def test_cooldown_still_applies_after_release():
    f = ControlFeature()
    for n in range(8):
        step(f, n / 10, hold=.5)
    step(f, .8, H.open_hand(), hold=.5)
    assert not step(f, .9, hold=.5)
    for n in range(10, 20):
        assert not step(f, n / 10, hold=.5)
    assert step(f, 2.01, hold=.5)


@pytest.mark.parametrize("shape", [H.open_hand, H.fist, H.middle_pinch, H.desktop_hand, H.thumbs_up])
def test_other_shapes_do_not_toggle(shape):
    f = ControlFeature()
    for n in range(40):
        assert not step(f, n / 20, shape())


def test_camera_gap_and_no_hand_break_hold():
    f = ControlFeature()
    step(f, 0)
    step(f, .2)
    assert not step(f, 1)
    assert not step(f, 1.2)
    frame = FrameInfo(0, 1.3, 1280, 720, True)
    f.step(None, FrameContext(frame, Stage.MAIN, OutputTarget.NONE, "pinch"))
    assert not f.activity().holding
    assert not step(f, 1.4)


class Model:
    classes = ("none", "v_sign")
    feature_version = "spatial-mlp-v1"

    def __init__(self, confidence):
        self.confidence = confidence

    def predict(self, features):
        return {"probabilities": {"v_sign": self.confidence}}


def test_selected_mlp_rejection_cannot_fall_back_to_rules():
    f = ControlFeature(Model(.1))
    for n in range(30):
        assert not step(f, n / 20)
    f.set_model(Model(.95))
    results = [intent for n in range(30, 55) for intent in step(f, n / 20)]
    assert len(results) == 1 and results[0].gesture == V_SIGN


def test_mlp_without_v_class_is_optional_and_bad_probability_is_rejected():
    model = Model(math.nan)
    model.classes = ("none",)
    f = ControlFeature(model)
    assert len([i for n in range(25) for i in step(f, n / 20)]) == 1
    f = ControlFeature(Model(math.nan))
    assert not [i for n in range(25) for i in step(f, n / 20)]


def test_rotated_and_mirrored_v_geometry():
    # Transform in aspect-correct image space, then map back to normalized image coordinates.
    pts = v_hand()
    aspect = .5625
    for points in ([(1-x, y, z) for x, y, z in pts],
                   [(.5 + (y-.5)*aspect, .5-(x-.5)/aspect, z) for x, y, z in pts]):
        f = ControlFeature()
        assert len([i for n in range(25) for i in step(f, n / 20, points)]) == 1

