"""Migration uses synthetic local files, never the user's recordings or network."""
import gzip
from hashlib import sha256
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from manux.app import schema, training_specs
from manux.core.contracts import RecordingIdentity
from manux.data import capture_plan
from manux.data.legacy_import import pending_model, stage_import
from manux.data.recording import read
from manux.data.training_center import TrainingCenterService, recording_sets
from manux.settings.store import Settings, save
from manux.settings.tcn_policy import GesturePolicies

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from import_training import restore


def source_tree(root):
    (root / "recordings").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "models").mkdir()
    (root / "config/gestures.json").write_text(json.dumps({"pc_alias": "synthetic", "click_threshold": .2}))
    (root / "config/recording-plans.json").write_text(json.dumps({"plans": {"thumb_middle": capture_plan.preset("quick_click")}}))
    rows = [{"type": "meta", "schema": "hand-mouse-recording/1", "mode": "guided"},
            {"type": "frame", "t": 0, "world": [[0, 0, 0]] * 21, "label": {"target": "thumb_middle", "task": "thumb_middle", "phase": "do", "rep": 1, "condition_index": 0}},
            {"type": "end", "frames": 1}]
    path = root / "recordings/old.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("\n".join(map(json.dumps, rows)) + "\n")
    return path


def old_model(path, classes, recording, values):
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from onnx import helper as h, numpy_helper as nh, TensorProto
    path.parent.mkdir(parents=True, exist_ok=True)
    graph = h.make_graph([h.make_node("Identity", ["value"], ["logits"])], "synthetic",
        [h.make_tensor_value_info("x", TensorProto.FLOAT, [1, 15, 133])],
        [h.make_tensor_value_info("logits", TensorProto.FLOAT, [1, len(classes)])],
        [nh.from_array(np.array([values], np.float32), "value")])
    onnx.save(h.make_model(graph, ir_version=10, opset_imports=[h.make_opsetid("", 18)]), path)
    path.with_suffix(".json").write_text(json.dumps(dict(classes=classes, window=15, features=133,
        feature_version="local3d-v1", threshold=.7, frames_in_a_row=3, trained="synthetic", held_out_results=[],
        data=[{"file": recording.name}], per_gesture={"thumb_middle": {"rate": .99}},
        per_task={"thumb_middle": {"rate": .98}})))


def test_additive_idempotent_import_preserves_originals_and_explicit_eval(tmp_path):
    source, dest = tmp_path / "old", tmp_path / "new"
    original = source_tree(source)
    before = original.read_bytes()
    (source / ".env").write_text("DO_NOT_COPY=synthetic")
    first, report = stage_import(source, dest, "synthetic")
    imported = dest / report["recordings"][0]["path"]
    assert original.read_bytes() == before
    assert (first / "recordings" / (sha256(before).hexdigest() + ".jsonl.gz")).read_bytes() == before
    assert read(imported).frames == read(original).frames
    assert read(imported).meta["split"] == "train"
    train, evaluation = recording_sets(dest / "recordings", RecordingIdentity("synthetic"))
    assert train == [imported] and not evaluation
    assert not list(first.rglob(".env"))
    stage_import(source, dest, "synthetic")
    assert len(list((dest / "recordings").rglob("*.gz"))) == 1
    assert len(capture_plan.load_plan("thumb_middle", dest / "config/capture-plans.json")["conditions"]) == 27
    custom = capture_plan.preset("positions")
    capture_plan.save_plan(custom, "thumb_middle", dest / "config/capture-plans.json")
    stage_import(source, dest, "synthetic")
    assert capture_plan.load_plan("thumb_middle", dest / "config/capture-plans.json") == capture_plan.normalise(custom)


def test_import_never_turns_incomplete_recording_into_train(tmp_path):
    source, dest = tmp_path / "old", tmp_path / "new"
    original = source_tree(source)
    with gzip.open(original, "rt") as f: rows = f.readlines()
    with gzip.open(original, "wt") as f: f.writelines(rows[:-1])
    _, report = stage_import(source, dest, "synthetic")
    assert not report["recordings"] and report["warnings"]
    assert not list((dest / "recordings").rglob("*.gz"))
    assert not pending_model(dest)


def test_existing_eval_identity_is_preserved_and_missing_eval_cannot_install(tmp_path):
    import app_worker
    source, dest = tmp_path / "old", tmp_path / "new"
    original = source_tree(source)
    request = {"profile": "synthetic", "recordings_dir": str(dest / "recordings"), "preset": "pinch"}
    stage_import(source, dest, "synthetic")
    with pytest.raises(ValueError, match="별도 평가용"):
        app_worker.run_validate_import(request)
    with gzip.open(original, "rt") as f: rows = f.readlines()
    meta = json.loads(rows[0]); meta.update(split="eval", person="other", preset="pinch")
    with gzip.open(source / "recordings/eval.jsonl.gz", "wt") as f:
        f.write(json.dumps(meta) + "\n"); f.writelines(rows[1:])
    stage_import(source, dest, "synthetic")
    assert not recording_sets(dest / "recordings", RecordingIdentity("synthetic"))[1]
    assert len(recording_sets(dest / "recordings", RecordingIdentity("other"))[1]) == 1


def test_selected_override_survives_conversion_and_cannot_apply_before_eval(tmp_path):
    from manux.desktop import build, RuntimeRequest
    from manux.ml.predictor import load_model
    source, dest = tmp_path / "old", tmp_path / "new"
    original = source_tree(source)
    base = source / "models/gesture_tcn.onnx"
    selected = source / "models/selected/current/gesture_tcn.onnx"
    old_model(base, ["none", "thumb_middle", "thumb_ring"], original, [0, 8, 3])
    old_model(selected, ["none", "thumb_middle"], original, [8, 0])
    (source / "models/gesture_tcn.selected.json").write_text(json.dumps({"version": 1, "overrides": {"thumb_middle": "selected/current/gesture_tcn.onnx"}}))
    save(Settings(schema(), {"camera.source": "1"}), dest / "config")
    result = restore(source, dest)
    path = pending_model(dest)
    model = load_model(path, "local3d-v1", 133)
    scores = dict(zip(model.card.classes, model.predictor(np.zeros((15, 133), np.float32))))
    assert scores["thumb_ring"] > scores["thumb_middle"]  # selected rejection wins over the old base's strong positive
    assert not (dest / "models/personal/active.json").exists()
    policies = GesturePolicies(dest / "config/tcn-policies.json")
    service = TrainingCenterService(dest / "config", dest, dest / "recordings", policy_store=policies, specs=training_specs())
    assert service.profile == "synthetic" and service.current_model() == path
    rows = service.summary_and_rows()[2]
    row = next(r for r in rows if r["key"] == "thumb_middle")
    assert row["import_pending"] and row["tcn_mode"] == "shadow" and row["rate"] is None
    with pytest.raises(ValueError, match="별도 평가"):
        service.set_policy("thumb_middle", "apply", row["model_id"])
    # Even a stale/edited preference that says apply must not give an imported model ownership.
    policies.set(model.card.raw, "pinch.thumb_middle", "apply", row["model_id"])
    runtime = build(Settings(schema()), SimpleNamespace(close=lambda: None), windows=False,
                    shadow_model=path, policy_store=policies)
    assert runtime.preview_only and set(runtime._model_modes(model).values()) == {"shadow"}
    runtime.request(RuntimeRequest("tcn_policy")); runtime._drain_runtime_requests()
    assert runtime.preview_only
    saved_policies = policies.path.read_bytes()
    runtime.request(RuntimeRequest("tcn_toggle", "off")); runtime._drain_runtime_requests()
    assert not any(f.id == "tcn" for f in runtime.engine.features)
    assert not runtime.engine.gesture_sources
    runtime.request(RuntimeRequest("preview_model", str(path))); runtime._drain_runtime_requests()
    assert runtime.preview_only and not any(f.id == "tcn" for f in runtime.engine.features)
    runtime.request(RuntimeRequest("tcn_toggle", "on")); runtime._drain_runtime_requests()
    assert any(f.id == "tcn" for f in runtime.engine.features)
    assert not runtime.engine.gesture_sources
    assert policies.path.read_bytes() == saved_policies
    runtime.stop()
    assert json.loads((dest / "config/settings.json").read_text())["values"]["camera.source"] == "1"
    requests = []
    service.job.start_request = lambda r: requests.append(r) or True
    service.job.start(gestures=["thumb_ring"])
    assert requests[0]["previous_candidate"] == str(path)  # retraining ring retains the restored middle model


def test_pending_model_path_cannot_escape_imports(tmp_path):
    from manux.data.legacy_import import atomic_json
    outside = tmp_path / "outside.onnx"
    outside.write_bytes(b"test")
    root = tmp_path / "data"
    atomic_json(root / "imports/latest.json", {"candidate": str(outside)})
    assert pending_model(root) is None


def test_restore_resolves_destination_alias_before_writing_candidate_path(tmp_path, monkeypatch):
    source = tmp_path / "old"
    recording = source_tree(source)
    old_model(source / "models/gesture_tcn.onnx", ["none", "thumb_middle"], recording, [3, 0])
    monkeypatch.chdir(tmp_path)
    report = restore(Path("old"), Path("new"))
    assert report["candidate"] and pending_model(tmp_path / "new").is_file()
