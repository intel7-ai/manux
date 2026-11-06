"""Selecting one gesture must never replace another gesture's learned behavior."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src import tcn_models as models, training_center as tc
from src.capture_dataset import load_sessions, training_selection
from src.gesture_catalog import BY_KEY, CLASSES
from src.gesture_gate import GestureGate
from src.recorder import SessionRecorder
from src.tcn_features import FEATURES, FEATURE_VERSION
from tests.test_expanded_training import record_tasks
from tests.test_recording_plan import hand


def write_model(folder, classes, seed=0, threshold=.7, frames=3):
    onnx = pytest.importorskip("onnx")
    from onnx import helper, numpy_helper, TensorProto
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "gesture_tcn.onnx"
    rng = np.random.default_rng(seed)
    graph = helper.make_graph([
        helper.make_node("ReduceMean", ["x", "axes"], ["mean"], keepdims=0),
        helper.make_node("MatMul", ["mean", "weights"], ["weighted"]),
        helper.make_node("Add", ["weighted", "bias"], ["logits"]),
    ], "test", [helper.make_tensor_value_info("x", TensorProto.FLOAT, ["batch", 15, FEATURES])],
       [helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch", len(classes)])], [
           numpy_helper.from_array(np.array([1], np.int64), "axes"),
           numpy_helper.from_array(rng.normal(0, .1, (FEATURES, len(classes))).astype(np.float32), "weights"),
           numpy_helper.from_array(rng.normal(0, 1, len(classes)).astype(np.float32), "bias"),
       ])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
    model.ir_version = 9
    onnx.save(model, str(path))
    metric = {"rate": seed / 10, "recognised": seed, "total": 10, "train_accuracy": .8}
    info = {"classes": classes, "window": 15, "features": FEATURES, "feature_version": FEATURE_VERSION,
            "threshold": threshold, "frames_in_a_row": frames, "trained": f"2026-11-05 12:00:{seed:02}",
            "per_gesture": {name: metric for name in classes if name != "none"},
            "per_task": {key: metric for key, task in BY_KEY.items() if task.label in classes and not task.free},
            "task_targets": {key: task.label for key, task in BY_KEY.items()}, "data": []}
    path.with_suffix(".json").write_text(json.dumps(info), encoding="utf-8")
    return path


def test_selection_expands_shared_pose_and_rejects_empty_neutral_or_unknown():
    tasks, classes = training_selection(["thumb_middle"])
    assert set(tasks) == {"thumb_middle", "thumb_middle_drag"}
    assert classes == ["none", "thumb_middle"]
    for selection in ([], ["none"], ["fist_move"], ["not-a-task"]):
        with pytest.raises(ValueError):
            training_selection(selection)


def test_selected_data_contains_only_requested_poses_and_neutral(tmp_path):
    path = record_tasks(tmp_path, ["thumb_middle", "thumb_middle_drag", "thumb_ring", "none", "open_move"])
    session = load_sessions([path], selected_tasks=["thumb_middle"])[0]
    classes = session["classes"]
    assert classes == ["none", "thumb_middle"]
    targets = [(f["label"]["task"], classes[y]) for f, y in zip(session["frames"], session["y"]) if y >= 0]
    assert {task for task, label in targets if label != "none"} == {"thumb_middle", "thumb_middle_drag"}
    assert {"none", "open_move"} <= {task for task, label in targets if label == "none"}


def test_other_gesture_is_a_hard_negative_and_preserves_real_temporal_context(tmp_path):
    rec = SessionRecorder(tmp_path, "guided", {}, now=0)
    for i, target in enumerate(["thumb_ring"] * 10 + ["none"] * 5 + ["thumb_middle"] * 5):
        rec.frame((i + 1) / 30, hand(), label={"target": target, "task": target, "phase": "do",
                                           "condition_index": 0, "set": 0})
    session = load_sessions([rec.close()], selected_tasks=["thumb_middle"])[0]
    assert (session["y"][:10] == 0).all()
    assert session["x"][10, :-1].any()
    assert session["x"][10, -1].any()


def test_successive_updates_preserve_other_models_scores_and_thresholds(tmp_path):
    from tcn_app import check_model
    base = write_model(tmp_path, ["none", "thumb_index", "thumb_ring"], 1)
    original_bytes = base.read_bytes(), base.with_suffix(".json").read_bytes()
    old, old_info = models.load_predictor(base)
    ring = write_model(tmp_path / "selected" / "ring", ["none", "thumb_ring"], 2, .81, 4)
    models.publish_selected(base, ring, ["thumb_ring"])
    first, first_info = models.load_predictor(base)
    middle = write_model(tmp_path / "selected" / "middle", ["none", "thumb_middle"], 3, .9, 2)
    models.publish_selected(base, middle, ["thumb_middle"])
    combined, info = models.load_predictor(base)
    for seed in range(5):
        x = np.random.default_rng(seed).normal(size=(1, 15, FEATURES)).astype(np.float32)
        assert combined(x)[info["classes"].index("thumb_index")] == old(x)[old_info["classes"].index("thumb_index")]
        assert combined(x)[info["classes"].index("thumb_ring")] == first(x)[first_info["classes"].index("thumb_ring")]
    assert (base.read_bytes(), base.with_suffix(".json").read_bytes()) == original_bytes
    assert info["per_gesture"]["thumb_index"]["rate"] == .1
    assert info["per_gesture"]["thumb_ring"]["rate"] == .2
    assert info["per_task"]["thumb_middle_drag"]["rate"] == .3
    assert info["class_decisions"]["thumb_ring"] == {"threshold": .81, "frames_in_a_row": 4}
    gate = GestureGate(combined, info)
    gate.valid = True
    gate._probs.extend((i / 30, np.array([0, .8, .85, .85])) for i in range(4))
    assert gate.confirmed("thumb_ring", .1)
    assert not gate.confirmed("thumb_middle", .1)
    startup = check_model(base)
    assert startup["inference"] == "passed" and len(startup["models"]) == 3


def test_partial_selection_replaces_only_that_route_of_an_earlier_group(tmp_path):
    from tcn_app import check_model
    base = tmp_path / "gesture_tcn.onnx"  # First training works without a supplied base model.
    group = write_model(tmp_path / "selected" / "group", ["none", "thumb_ring", "thumb_middle"], 1)
    models.publish_selected(base, group, ["thumb_ring", "thumb_middle"])
    first, first_info = models.load_predictor(base)
    middle = write_model(tmp_path / "selected" / "middle", ["none", "thumb_middle"], 5)
    models.publish_selected(base, middle, ["thumb_middle"])
    second, info = models.load_predictor(base)
    x = np.ones((1, 15, FEATURES), np.float32)
    assert first(x)[first_info["classes"].index("thumb_ring")] == second(x)[info["classes"].index("thumb_ring")]
    assert set(info["classes"]) == {"none", "thumb_ring", "thumb_middle"}
    assert models.available(base)
    assert check_model(base)["inference"] == "passed"


def test_bad_or_interrupted_publication_keeps_active_registry(tmp_path, monkeypatch):
    base = write_model(tmp_path, ["none", "thumb_ring"], 1)
    good = write_model(tmp_path / "selected" / "good", ["none", "thumb_ring"], 2)
    models.publish_selected(base, good, ["thumb_ring"])
    before = models.registry_path(base).read_bytes()
    bad = write_model(tmp_path / "selected" / "bad", ["none", "thumb_middle"], 3)
    bad.write_bytes(b"interrupted ONNX")
    with pytest.raises(Exception):
        models.publish_selected(base, bad, ["thumb_middle"])
    assert models.registry_path(base).read_bytes() == before
    good2 = write_model(tmp_path / "selected" / "good2", ["none", "thumb_middle"], 4)
    def interrupted(*args):
        raise OSError("interrupted before activation")
    monkeypatch.setattr(models.os, "replace", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        models.publish_selected(base, good2, ["thumb_middle"])
    assert models.registry_path(base).read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_export_includes_active_selected_models_and_standalone_loader(tmp_path, monkeypatch):
    import importlib.util
    base = write_model(tmp_path / "models", ["none", "thumb_ring"], 1)
    selected = write_model(base.parent / "selected" / "middle", ["none", "thumb_middle"], 2)
    models.publish_selected(base, selected, ["thumb_middle"])
    monkeypatch.setattr(tc, "MODEL", base)
    out = tc.export_package(tmp_path / "exports", with_dataset=False)
    assert (out / "selected" / "middle" / "gesture_tcn.onnx").exists()
    assert models.registry_path(out / "gesture_tcn.onnx").exists()
    monkeypatch.syspath_prepend(str(out))
    spec = importlib.util.spec_from_file_location("exported_example", out / "example_use.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    exported = module.GestureModel(str(out))
    original, _ = models.load_predictor(base)
    x = np.ones((1, 15, FEATURES), np.float32)
    np.testing.assert_array_equal(exported.predict(x), original(x))


def test_selection_remains_global_across_category_filters_and_running_job_is_locked():
    from src.qt_dashboard import QtDashboard
    data = {"per_gesture": {"thumb_middle": {"reps": 1, "postures": {"a": 1}, "minutes": 0},
                            "thumb_ring": {"reps": 1, "postures": {"a": 1}, "minutes": 0}}, "files": []}
    stub = SimpleNamespace(training_rows=tc.gesture_rows(data, None), training_selected=set(),
                           training_job=SimpleNamespace(running=False), training_busy="",
                           training_filter="방향 모션", trainingChanged=SimpleNamespace(emit=lambda: None))
    QtDashboard.selectTrainingTask(stub, "thumb_middle_drag", True)
    assert stub.training_selected == {"thumb_middle"}
    QtDashboard.selectTrainingTask(stub, "none", True)
    assert stub.training_selected == {"thumb_middle"}
    QtDashboard.selectAllTraining(stub, True)
    assert stub.training_selected == {"thumb_middle", "thumb_ring"}
    stub.training_job.running = True
    QtDashboard.selectAllTraining(stub, False)
    assert len(stub.training_selected) == 2


def test_new_recording_marks_only_its_gesture_stale():
    data = {"per_gesture": {}, "newest": 100,
            "files": [{"mtime": 100, "reps": {"thumb_middle": {"a": 2}}}]}
    report = {"classes": CLASSES, "mtime": 10, "class_mtime": {"thumb_ring": 10, "thumb_middle": 10}}
    rows = {row["key"]: row for row in tc.gesture_rows(data, report)}
    assert "새 녹화" in rows["thumb_middle"]["advice"]
    assert "새 녹화" not in rows["thumb_ring"]["advice"]
