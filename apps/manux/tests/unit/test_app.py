from manux.app import build_engine, schema
from manux.core.contracts import FrameInfo, HandObservation, OutputTarget
from manux.settings.store import Settings


def test_direct_tcn_cannot_bypass_v_hold_control_contract():
    from types import SimpleNamespace
    import pytest
    from manux.app import pipeline_parts, training_specs
    from manux.ml.card import validate_card
    card = validate_card(dict(classes=["none", "v_sign"], window=1, features=133,
        feature_version="local3d-v1", threshold=.7, frames_in_a_row=1,
        trained="synthetic", data=[], held_out_results=[], kind="base",
        gestures={"v_sign": "control.v_sign"},
        decisions={"v_sign": {"kind": "tap", "modes": ["pinch"], "point": "none"}}), "local3d-v1", 133)
    model = SimpleNamespace(predictor=SimpleNamespace(classes=card.classes), card=card)
    with pytest.raises(ValueError, match="V hold"):
        pipeline_parts(model)
    assert "control.v_sign" not in training_specs()


def test_composition_routes_real_features_to_practice():
    engine = build_engine(Settings(schema()), "pinch")
    frame = FrameInfo(0, 1.0, 1280, 720, True)
    points = [(0.5, 0.8, 0.0)] * 21
    for joint, x in zip((5, 9, 13, 17), (0.44, 0.48, 0.52, 0.56)):
        points[joint] = (x, 0.6, 0.0)
        points[joint + 2] = (x, 0.5, 0.0)
        points[joint + 3] = (x, 0.4, 0.0)
    points[4] = (0.36, 0.62, 0.0)
    hand = HandObservation(frame, tuple(points), "Right", 0.99)
    try:
        result = engine.step(frame, hand)
        assert result.output == OutputTarget.PRACTICE
        assert result.mode == "pinch"
        assert result.results == (("mouse.move", "hold", "ok"),)
        assert not result.unrouted
        assert not result.errors
        assert OutputTarget.WINDOWS not in engine.sinks
    finally:
        engine.shutdown()
