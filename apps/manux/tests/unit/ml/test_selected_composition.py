"""Real ONNX composition; synthetic constants, never user models or data."""
from hashlib import sha256
import json
from pathlib import Path
import sys

import numpy as np
import pytest

onnx = pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")
from onnx import helper as h, numpy_helper as nh, TensorProto

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from compose_tcn import compose, _parts
from manux.ml.predictor import load_model
from manux.settings.tcn_policy import GesturePolicies


def component(path, label, logits=(4., 0.), threshold=.7):
    graph = h.make_graph([h.make_node("Identity", ["value"], ["logits"])], "synthetic",
                        [h.make_tensor_value_info("features", TensorProto.FLOAT, [1, 15, 133])],
                        [h.make_tensor_value_info("logits", TensorProto.FLOAT, [1, 2])],
                        [nh.from_array(np.array([logits], np.float32), "value")])
    onnx.save(h.make_model(graph, ir_version=8, opset_imports=[h.make_opsetid("", 17)]), path)
    card = dict(classes=["none", label], gestures={label: "pinch." + label},
                decisions={label: dict(kind="tap", modes=["pinch"], point="none")},
                window=15, features=133, feature_version="local3d-v1", threshold=threshold, frames_in_a_row=3,
                trained="synthetic", kind="personal", held_out_results=[], data=[dict(sha256="a" * 64)],
                model_sha256=sha256(path.read_bytes()).hexdigest(), class_default_modes={label: "shadow"})
    path.with_suffix(".json").write_text(json.dumps(card), encoding="utf-8")
    return card


def test_identical_tensor_bytes_for_distinct_labels_do_not_collide(tmp_path):
    a, b = tmp_path / "a.onnx", tmp_path / "b.onnx"
    component(a, "left"); component(b, "right")
    assert a.read_bytes() == b.read_bytes()
    out = tmp_path / "composed.onnx"
    result = compose(None, [a, b], out)
    assert len(set(result["composition"]["routes"].values())) == 2
    model = load_model(out, "local3d-v1", 133)
    scores = model.predictor(np.zeros((15, 133), np.float32))
    assert model.card.classes == ("none", "left", "right")
    assert scores.shape == (3,) and np.isfinite(scores).all() and scores.sum() == pytest.approx(1.)


def test_replacing_one_component_preserves_other_weights_threshold_and_choice(tmp_path):
    left, right, updated = [tmp_path / name for name in ("left.onnx", "right.onnx", "new.onnx")]
    component(left, "left", threshold=.71)
    component(right, "right", logits=(5., 0.), threshold=.83)
    first = tmp_path / "first.onnx"
    old = compose(None, [left, right], first)
    policies = GesturePolicies(tmp_path / "modes.json")
    policies.set(old, "pinch.right", "apply", old["class_model_ids"]["right"])
    policies.set(old, "pinch.left", "apply", old["class_model_ids"]["left"])
    component(updated, "left", logits=(6., 0.), threshold=.65)
    second = tmp_path / "second.onnx"
    new = compose(first, [updated], second)
    route = old["composition"]["routes"]["right"]
    assert new["composition"]["routes"]["right"] == route
    assert len(new["composition"]["components"]) == 2  # no nested ensemble or obsolete left graph
    old_part, new_part = _parts(first)[2][route], _parts(second)[2][route]
    assert [v.SerializeToString() for v in old_part[1] + old_part[2]] == [v.SerializeToString() for v in new_part[1] + new_part[2]]
    assert new["decisions"]["right"]["threshold"] == .83
    assert new["decisions"]["left"]["threshold"] == .65
    assert policies.modes(new) == {"pinch.left": "shadow", "pinch.right": "apply"}
    assert load_model(second, "local3d-v1", 133).card.classes == ("none", "left", "right")


@pytest.mark.parametrize("logits", [((4., 0.),), ((4., 0.), (0., 5.)), ((-3., 2.), (6., -1.), (1., 1.))])
def test_neutral_reduction_preserves_normalized_component_scores(tmp_path, logits):
    paths, scores = [], []
    for index, values in enumerate(logits):
        path = tmp_path / f"component-{index}.onnx"
        component(path, f"gesture_{index}", logits=values)
        paths.append(path)
        probabilities = np.exp(np.asarray(values) - max(values))
        scores.append(probabilities / probabilities.sum())
    output = tmp_path / "combined.onnx"
    compose(None, paths, output)
    expected = np.array([min(row[0] for row in scores), *(row[1] for row in scores)])
    actual = load_model(output, "local3d-v1", 133).predictor(np.zeros((15, 133), np.float32))
    np.testing.assert_allclose(actual, expected / expected.sum(), rtol=1e-6, atol=1e-7)


def test_tampered_component_is_rejected_before_composing(tmp_path):
    source = tmp_path / "input.onnx"
    component(source, "left")
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="바뀌"):
        compose(None, [source], tmp_path / "out.onnx")
    assert not (tmp_path / "out.onnx").exists()
