import json
import shutil
import pytest

from tcn_app import check_model
from src.gesture_gate import DEFAULT_MODEL

pytest.importorskip("onnxruntime")


def test_shipped_model_runs_on_cpu(tmp_path):
    # Exercise the shipped base, independently of this PC's personal model registry.
    model = tmp_path / DEFAULT_MODEL.name
    shutil.copy2(DEFAULT_MODEL, model)
    shutil.copy2(DEFAULT_MODEL.with_suffix(".json"), model.with_suffix(".json"))
    result = check_model(model)
    assert result["inference"] == "passed"
    assert result["input"] == [1, 15, 133]
    assert result["classes"] == ["none", "thumb_index", "thumb_ring"]
    assert len(result["sha256"]) == 64


def test_missing_model_cannot_pass_startup(tmp_path):
    with pytest.raises((OSError, RuntimeError)):
        check_model(tmp_path / "missing.onnx")


def test_incompatible_features_cannot_pass_startup(tmp_path):
    path = tmp_path / "model.onnx"
    info = json.loads(DEFAULT_MODEL.with_suffix(".json").read_text(encoding="utf-8"))
    info["feature_version"] = "incompatible"
    path.with_suffix(".json").write_text(json.dumps(info), encoding="utf-8")
    with pytest.raises(ValueError, match="model features"):
        check_model(path)
