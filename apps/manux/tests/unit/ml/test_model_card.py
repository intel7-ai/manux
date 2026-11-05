"""Model card checks and the predictor interface (docs/contracts.md section 10), without onnxruntime."""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from manux.ml import ModelError, OnnxPredictor, load_card, load_model, validate_card

V = "local3d-v1"


def dan_style_card(**changes):
    """The shape of Dan's models/gesture_tcn.json (no kind, no gestures), with synthetic values."""
    card = {
        "classes": ["none", "thumb_index", "thumb_ring"], "window": 15, "features": 133, "feature_version": V,
        "threshold": 0.7, "frames_in_a_row": 3, "epochs": 25, "trained": "2026-11-05 04:18:48",
        "data": [{"file": "x-guided.jsonl.gz", "pc": "pc-test", "camera": {"mode": [1280, 720, 30.0]}, "frames": 10}],
        "held_out_results": [{"posture": 0, "rules": {}, "tcn": {}}],
    }
    card.update(changes)
    return {k: v for k, v in card.items() if v is not None}


class FakeSession:
    def __init__(self, in_shape=("batch", 15, 133), out_shape=("batch", 3), logits=(0.0, 0.0, 2.0)):
        self.in_shape, self.out_shape, self.logits = list(in_shape), list(out_shape), np.array([logits])
        self.calls = []

    def get_inputs(self):
        return [SimpleNamespace(name="x", shape=self.in_shape)]

    def get_outputs(self):
        return [SimpleNamespace(name="logits", shape=self.out_shape)]

    def run(self, names, feeds):
        self.calls.append(feeds["x"].shape)
        return [self.logits]


def test_dan_card_loads_as_a_base_model_without_gestures():
    card = validate_card(dan_style_card(), V, 133)
    assert card.kind == "base" and dict(card.gestures) == {}
    assert card.classes == ("none", "thumb_index", "thumb_ring") and card.window == 15 and card.threshold == 0.7
    assert card.raw["epochs"] == 25


def test_kind_and_gestures_are_read_and_checked():
    card = validate_card(dan_style_card(kind="personal", gestures={"thumb_ring": "pinch.thumb_ring"}), V)
    assert card.kind == "personal" and card.gestures["thumb_ring"] == "pinch.thumb_ring"
    bad = [
        {"kind": "shared"},
        {"gestures": {"thumb_pinky": "pinch.thumb_pinky"}},             # not a class
        {"gestures": {"thumb_ring": "right_click"}},                    # not a gesture id
        {"gestures": {"thumb_ring": "pinch.x", "thumb_index": "pinch.x"}},
        {"gestures": ["thumb_ring"]},
        {"gestures": {"none": "pinch.none"}},
    ]
    for change in bad:
        with pytest.raises(ModelError) as e:
            validate_card(dan_style_card(**change), V)
        assert e.value.code == "invalid", change


@pytest.mark.parametrize("key", ["classes", "window", "features", "feature_version", "threshold",
                                 "frames_in_a_row", "trained", "data", "held_out_results"])
def test_every_required_key(key):
    card = dan_style_card()
    del card[key]
    with pytest.raises(ModelError) as e:
        validate_card(card, V)
    assert e.value.code == "invalid"


@pytest.mark.parametrize("change", [{"classes": ["none"]}, {"classes": ["none", "none"]}, {"classes": ["None", "a"]},
                                    {"window": 0}, {"window": 1.5}, {"threshold": 0}, {"threshold": 1.2},
                                    {"frames_in_a_row": True}, {"data": {}}])
def test_bad_values(change):
    with pytest.raises(ModelError) as e:
        validate_card(dan_style_card(**change), V)
    assert e.value.code == "invalid"


def test_feature_version_and_size_must_match_the_app():
    with pytest.raises(ModelError) as e:
        validate_card(dan_style_card(feature_version="local3d-v2"), V)
    assert e.value.code == "feature_version" and e.value.text == "특징 버전 다름"
    with pytest.raises(ModelError) as e:
        validate_card(dan_style_card(features=132), V, 133)
    assert e.value.code == "feature_version"


def test_files(tmp_path):
    with pytest.raises(ModelError) as e:
        load_card(tmp_path / "none.json", V)
    assert e.value.code == "missing" and e.value.text == "모델 없음"
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(ModelError) as e:
        load_card(tmp_path / "broken.json", V)
    assert e.value.code == "unreadable"


def test_load_model_checks_card_file_and_shapes(tmp_path):
    model = tmp_path / "m.onnx"
    with pytest.raises(ModelError) as e:                       # no card
        load_model(model, V, 133, session_factory=lambda p: FakeSession())
    assert e.value.code == "missing"
    (tmp_path / "m.json").write_text(json.dumps(dan_style_card()), encoding="utf-8")
    with pytest.raises(ModelError) as e:                       # card but no model file
        load_model(model, V, 133, session_factory=lambda p: FakeSession())
    assert e.value.code == "missing"
    model.write_bytes(b"fake")
    loaded = load_model(model, V, 133, session_factory=lambda p: FakeSession())
    probs = loaded.predictor(np.zeros((15, 133), dtype=np.float32))
    assert probs.shape == (3,) and probs.sum() == pytest.approx(1.0) and probs.argmax() == 2
    for session in (FakeSession(in_shape=(1, 10, 133)), FakeSession(in_shape=(1, 15, 132)),
                    FakeSession(in_shape=(15, 133)), FakeSession(out_shape=("batch", 4)),
                    FakeSession(out_shape=("batch", "n"), logits=(0.0, 1.0))):
        with pytest.raises(ModelError) as e:
            load_model(model, V, 133, session_factory=lambda p, s=session: s)
        assert e.value.code == "shape"


def test_predictor_feeds_a_batch_of_one():
    card = validate_card(dan_style_card(), V)
    session = FakeSession()
    predictor = OnnxPredictor(session, card)
    predictor(np.ones((15, 133)))
    assert session.calls[-1] == (1, 15, 133)


@pytest.mark.parametrize("decision", [
    {}, {"kind": "tap", "modes": [], "point": "none"},
    {"kind": "span", "modes": ["pinch"], "point": "none"},
    {"kind": "tap", "modes": ["pinch"], "point": "none", "threshold": float("nan")},
    {"kind": "tap", "modes": ["pinch"], "point": "none", "frames_in_a_row": True},
    {"kind": "tap", "modes": ["pinch"], "point": "none", "action": "mouse.right_click"},
])
def test_bad_direct_declarations_are_rejected(decision):
    with pytest.raises(ModelError):
        validate_card(dan_style_card(gestures={"thumb_ring": "pinch.thumb_ring"},
                                    decisions={"thumb_ring": decision}), V)


def test_model_hash_is_checked_before_opening_a_session(tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(b"changed model")
    path.with_suffix(".json").write_text(json.dumps(dan_style_card(model_sha256="0" * 64)), encoding="utf-8")
    def forbidden(path):
        pytest.fail("must verify the hash before opening the runtime session")
    with pytest.raises(ModelError, match="checksum"):
        load_model(path, V, 133, session_factory=forbidden)


def test_real_onnxruntime_if_installed(tmp_path):
    ort = pytest.importorskip("onnxruntime")
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper

    # logits = mean over time of x[..., :3] (a tiny real ONNX graph)
    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, ["batch", 15, 133])
    y = helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch", 3])
    starts = helper.make_tensor("s", TensorProto.INT64, [1], [0])
    ends = helper.make_tensor("e", TensorProto.INT64, [1], [3])
    axes = helper.make_tensor("a", TensorProto.INT64, [1], [2])
    raxes = helper.make_tensor("ra", TensorProto.INT64, [1], [1])
    graph = helper.make_graph([helper.make_node("Slice", ["x", "s", "e", "a"], ["sl"]),
                               helper.make_node("ReduceMean", ["sl", "ra"], ["logits"], keepdims=0)],
                              "g", [x], [y], initializer=[starts, ends, axes, raxes])
    m = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
    m.ir_version = min(m.ir_version, 10)
    onnx.save(m, str(tmp_path / "m.onnx"))
    (tmp_path / "m.json").write_text(json.dumps(dan_style_card()), encoding="utf-8")
    loaded = load_model(tmp_path / "m.onnx", V, 133)
    w = np.zeros((15, 133), dtype=np.float32)
    w[:, 1] = 3.0
    assert loaded.predictor(w).argmax() == 1
    assert ort is not None
