"""Source-compatible fusion stays opt-in and never reuses a lost hand's pose evidence."""
from types import SimpleNamespace
import numpy as np
import pytest

from manux.features.tcn_gate import TcnGate, SETTINGS
from manux.ml.spatial_hybrid import blend
from test_tcn_gate import card, ctx, tap, RING
import pinch_hands as H


def model(probs):
    return SimpleNamespace(feature_version="spatial-mlp-v1", classes=("thumb_ring", "none", "thumb_index"),
                           predict_proba=lambda _: np.array(probs))


def test_agreeing_spatial_pose_can_confirm_current_frame_only():
    gate = TcnGate(lambda _: np.array([.5,.1,.4]), card(), spatial_model=model([.9,.08,.02]))
    gate.observe(H.obs(1., H.open_hand()), ctx(1.))
    assert gate.filter([tap(1.)], ctx(1.)) == [tap(1.)]
    gate.observe(None, ctx(1.1))
    assert gate.last_spatial is None and not gate.confirmed(RING, 1.1)
    gate.reset("new_session")
    assert gate.last_tcn is None and gate.last_spatial is None


def test_confident_neutral_pose_vetoes_temporal_spike_without_replacing_gesture():
    gate = TcnGate(lambda _: np.array([.2,.05,.75]), card(), spatial_model=model([.02,.96,.02]))
    for i in range(6):
        t=1+i/30
        gate.observe(H.obs(t,H.open_hand()),ctx(t))
    assert not gate.confirmed(RING,t)
    assert gate.filter([tap(t)],ctx(t)) == []


def test_disabled_dan_class_retains_rule_behavior_and_hybrid_default_is_off():
    gate = TcnGate(lambda _: np.array([1.,0.,0.]), card(disabled_gestures=["thumb_ring"]))
    assert RING not in gate.checks
    assert gate.filter([tap(1.)],ctx(1.)) == [tap(1.)]
    assert SETTINGS["tcn_gate.spatial_hybrid"] is False


def test_hybrid_aligns_classes_and_rejects_invalid_probabilities():
    a=blend([.4,.1,.5],("none","thumb_ring","swipe_left"),[.95,.05],("thumb_ring","none"))
    b=blend([.4,.1,.5],("none","thumb_ring","swipe_left"),[.05,.95],("none","thumb_ring"))
    np.testing.assert_allclose(a,b)
    assert np.isclose(a.sum(),1.)
    with pytest.raises(ValueError):
        blend([.5,.5],("none","thumb_ring"),[np.nan,.5],("none","thumb_ring"))
