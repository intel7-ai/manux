"""Regression checks for source dependencies, legacy captures and empty evaluation."""
import gzip
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import app_worker
import train_tcn
import training_device
from smoke_worker import recording
from tcn_data import load, class_mapping, class_names
from manux.core.contracts import ExecutionDevice


def rewrite(path, edit):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    edit(rows)
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


@pytest.mark.parametrize("task", ["thumb_middle", "thumb_middle_drag", "double_click", None])
def test_legacy_left_click_loads_without_rewriting_source(tmp_path, task):
    path = tmp_path / "legacy.jsonl.gz"
    recording(path, "train", "thumb_middle")
    def legacy(rows):
        for row in rows[1:-1]:
            label = row["label"]
            label.pop("gesture", None)
            if task is not None:
                label["task"] = task
    rewrite(path, legacy)
    original = path.read_bytes()
    segments = load([path], split="train")
    assert class_mapping(segments, class_names(segments)) == {"thumb_middle": "pinch.thumb_middle"}
    assert all(not f["label"].get("gesture") for s in segments for f in s.frames if f["label"]["target"] == "none")
    assert path.read_bytes() == original


@pytest.mark.parametrize("preset,task", [("pointer", "thumb_middle"), ("pinch", "unknown"), ("pinch", "thumb_ring")])
def test_legacy_inference_does_not_guess_mismatched_tasks(tmp_path, preset, task):
    path = tmp_path / "legacy.jsonl.gz"
    recording(path, "train", "thumb_middle")
    def mismatch(rows):
        rows[0]["preset"] = preset
        for row in rows[1:-1]:
            row["label"].pop("gesture", None)
            row["label"]["task"] = task
    rewrite(path, mismatch)
    segments = load([path])
    with pytest.raises(ValueError, match="declare a gesture ID"):
        class_mapping(segments, class_names(segments))


def test_explicit_gesture_is_never_replaced(tmp_path):
    path = tmp_path / "explicit.jsonl.gz"
    recording(path, "train", "thumb_middle")
    def explicit(rows):
        for row in rows[1:-1]:
            if row["label"]["target"] == "thumb_middle":
                row["label"].update(task="thumb_middle", gesture="custom.left")
    rewrite(path, explicit)
    segments = load([path])
    assert class_mapping(segments, class_names(segments)) == {"thumb_middle": "custom.left"}


@pytest.mark.parametrize("component", app_worker.TRAIN_COMPONENTS)
def test_worker_names_only_known_missing_components(component):
    message = app_worker.worker_error(ModuleNotFoundError("private detail", name=component))
    assert component in message and "--extra train" in message
    assert "private detail" not in message
    assert "private detail" not in app_worker.worker_error(ModuleNotFoundError("private detail", name="private detail"))


@pytest.fixture
def job_request(tmp_path, monkeypatch):
    monkeypatch.setattr(training_device, "select", lambda device: ExecutionDevice(device, "cpu", "synthetic", "CPU", ""))
    monkeypatch.setattr(app_worker, "find_spec", lambda name: object())
    for split in ("train", "eval"):
        recording(tmp_path / "recordings" / split / "capture.jsonl.gz", split, "thumb_middle")
    return {"recordings_dir": str(tmp_path / "recordings"), "profile": "synthetic", "preset": "pinch",
            "store": str(tmp_path / "store"), "cache_dir": str(tmp_path / "cache"),
            "job_dir": str(tmp_path), "cancel_path": str(tmp_path / "cancel"),
            "focus_labels": ["thumb_middle"], "specs": {
                "pinch.thumb_middle": {"kind": "tap", "modes": ["pinch"], "point": "none"}}}


@pytest.mark.parametrize("problem", ["idle", "no_world", "broken_windows"])
def test_unusable_eval_blocks_before_training(job_request, monkeypatch, problem):
    path = Path(job_request["recordings_dir"]) / "eval" / "capture.jsonl.gz"
    def invalid(rows):
        for i, row in enumerate(rows[1:-1]):
            if problem == "idle":
                row["label"] = {"target": "idle", "phase": "waiting"}
            elif problem == "no_world" or i % 10 == 0:
                row.pop("world", None)
    rewrite(path, invalid)
    monkeypatch.setattr(train_tcn, "main", lambda *args: pytest.fail("must not train before evaluation is ready"))
    with pytest.raises(ValueError, match="평가 녹화가 부족한 동작.*좌클릭"):
        app_worker.run_training(job_request, send=lambda *args, **kwargs: None)
    assert not (Path(job_request["store"]) / "active.json").exists()
    assert not (Path(job_request["job_dir"]) / "candidate.onnx").exists()


def test_legacy_capture_reaches_selected_classifier_training(job_request, monkeypatch):
    path = Path(job_request["recordings_dir"]) / "train" / "capture.jsonl.gz"
    rewrite(path, lambda rows: [row["label"].pop("gesture", None) for row in rows[1:-1]])
    class TrainingReached(Exception): pass
    def reached(args):
        assert args[args.index("--classes") + 1:args.index("--classes") + 3] == ["none", "thumb_middle"]
        raise TrainingReached
    monkeypatch.setattr(train_tcn, "main", reached)
    with pytest.raises(TrainingReached):
        app_worker.run_training(job_request, send=lambda *args, **kwargs: None)


def test_worker_checks_export_dependencies_before_loading_data(job_request, monkeypatch):
    monkeypatch.setattr(app_worker, "find_spec", lambda name: None if name == "onnxscript" else object())
    with pytest.raises(ModuleNotFoundError) as failure:
        app_worker.run_training(job_request, send=lambda *args, **kwargs: None)
    assert failure.value.name == "onnxscript"
    assert not Path(job_request["cache_dir"]).exists()
