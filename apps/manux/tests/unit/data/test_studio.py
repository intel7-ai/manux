"""Anonymous synthetic trials: curation, provenance, geometry and real engine replay."""
import gzip
import json
from pathlib import Path
import sys

import pytest

from manux.core.contracts import RecordingIdentity
from manux.data import studio
from manux.data.recording import read, replay
from manux.data.training_center import recording_sets, dataset_summary

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from smoke_worker import recording
import studio_worker
import hub


def rewrite(path, change):
    with gzip.open(path, "rt", encoding="utf-8") as file: rows = [json.loads(line) for line in file]
    change(rows)
    with gzip.open(path, "wt", encoding="utf-8") as file:
        for row in rows: file.write(json.dumps(row) + "\n")


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "profile-synthetic" / "pinch" / "train" / "recording.jsonl.gz"
    recording(path, "train")
    return tmp_path, path.relative_to(tmp_path).as_posix(), path


@pytest.mark.parametrize("key", ["../outside.jsonl.gz", ".trash/a.jsonl.gz", "C:/outside.jsonl.gz", "C:relative.jsonl.gz", "a\\b.jsonl.gz", "x.part"])
def test_recording_identifiers_cannot_leave_library(tmp_path, key):
    with pytest.raises(ValueError): studio.recording_path(tmp_path, key)


def test_quality_uses_real_end_marker_and_missing_hand(source):
    root, key, path = source
    assert not any(i["kind"] == "incomplete" for i in studio.inspect(root, key)["quality"]["issues"])
    rewrite(path, lambda rows: [r.pop("lm", None) for r in rows if r.get("type") == "frame"])
    info = studio.inspect(root, key)
    assert info["quality"]["status"] == "fail"
    assert info["quality"]["metrics"]["missing_hand_ratio"] == 1
    assert path.exists() and studio.included(root, path)  # no automatic quality-based exclusion


def test_selection_is_relative_and_content_bound(source):
    root, key, path = source
    studio.select_recording(root, key, False)
    assert recording_sets(root, RecordingIdentity("synthetic"))[0] == []
    other = root / "team" / path.name
    recording(other, "train")
    assert studio.included(root, other)  # equal basenames are independent selections
    rewrite(path, lambda rows: rows[0].update(created="synthetic changed capture"))
    assert studio.included(root, path)  # stale choice never excludes a replacement capture


def test_clean_preserves_source_and_good_measurements_and_tracks_lineage(source):
    root, key, path = source
    before = path.read_bytes()
    # A short missing frame wholly within the same neutral attempt can be interpolated.
    rewrite(path, lambda rows: [rows[11].pop(k, None) for k in ("lm", "world", "hand")])
    before = path.read_bytes()
    derived = studio.derive(root, key, "clean")
    result = read(studio.recording_path(root, derived))
    assert path.read_bytes() == before
    assert result.frames[10]["interpolated"]
    assert result.frames[5]["world"] == read(path).frames[5]["world"]
    assert result.meta["lineage"]["capture_sha256"] == studio.digest(path)
    assert not studio.included(root, studio.recording_path(root, derived))
    studio.select_recording(root, derived, True)
    train, _ = recording_sets(root, RecordingIdentity("synthetic"))
    assert train == [studio.recording_path(root, derived)]
    assert dataset_summary(root)["per_gesture"] == dataset_summary(path.parent)["per_gesture"]


def test_clean_does_not_invent_world_or_cross_label_boundary(source):
    root, key, path = source
    rewrite(path, lambda rows: [row.pop("world", None) for row in rows])
    clean = read(studio.recording_path(root, studio.derive(root, key, "clean")))
    assert all("world" not in f for f in clean.frames)
    # A missing frame at the neutral/action boundary must remain missing.
    recording(path, "train")
    rewrite(path, lambda rows: [rows[40].pop(k, None) for k in ("lm", "world", "hand")])
    clean = read(studio.recording_path(root, studio.derive(root, key, "clean")))
    assert "world" not in clean.frames[39]


def test_augmentation_is_local_train_only_not_an_independent_trial(source):
    root, key, path = source
    before = path.read_bytes()
    augmented = studio.recording_path(root, studio.derive(root, key, "augment", seed=17))
    rec = read(augmented)
    assert path.read_bytes() == before and rec.complete
    assert rec.frames[5]["world"] != read(path).frames[5]["world"]
    assert all(a["t"] < b["t"] for a, b in zip(rec.frames, rec.frames[1:]))
    assert studio.digest(path) in studio.capture_hashes(augmented)
    studio.select_recording(root, augmented.relative_to(root).as_posix(), True)
    assert len(recording_sets(root, RecordingIdentity("synthetic"))[0]) == 2
    entries, errors = hub.scan_local_recordings(root)
    assert not errors and [r.path for r in entries] == [path]
    evaluation = root / "eval.jsonl.gz"
    recording(evaluation, "eval")
    with pytest.raises(ValueError): studio.derive(root, "eval.jsonl.gz", "clean")
    with pytest.raises(ValueError): studio.select_recording(root, "eval.jsonl.gz", False)


def test_trash_removes_training_input_without_destroying_bytes(source):
    root, key, path = source
    before = path.read_bytes()
    studio.trash_recording(root, key)
    assert not path.exists() and recording_sets(root, RecordingIdentity("synthetic"))[0] == []
    assert studio.library(root) == [] and dataset_summary(root)["files"] == []
    assert next((root / ".trash").rglob("*.jsonl.gz")).read_bytes() == before
    assert hub.scan_local_recordings(root) == ([], [])


def test_simulator_replays_the_real_engine_and_never_builds_windows_sink(source):
    from manux.app import build_engine, schema
    from manux.core.contracts import OutputTarget
    from manux.settings.store import Settings
    root, key, path = source
    result = studio_worker.simulate({"store": str(root / "store"), "preset": "pinch"}, root, key, lambda: False)
    engine = build_engine(Settings(schema()), "pinch")
    assert OutputTarget.WINDOWS not in engine.sinks
    expected = []
    for item in replay(read(path)):
        snap = engine.step(item.frame, item.hand)
        expected.append([{"action": a, "phase": p, "result": why} for a, p, why in snap.results if p != "hold"])
    assert [f["rules"] for f in result["frames"]] == expected
    assert [f["model"] for f in result["frames"]] == expected  # no active model is stated explicitly
    assert len(result["frames"]) == len(read(path).frames)
    assert result["simulation"]["errors"] == 0


def test_new_worker_returns_completed_job_owned_result_file(source):
    root, key, path = source
    folder = root / "job"
    folder.mkdir()
    events = []
    request = {"action": "load", "recordings_dir": str(root), "key": key, "store": str(root / "store"),
               "job_dir": str(folder), "job_id": "f"*32}
    studio_worker.run(request, lambda event, **data: events.append({"event": event, **data}), lambda: False)
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    assert result["frames"][1]["lm"] == read(path).frames[1]["lm"]
    assert events[-1]["job_id"] == "f"*32 and "frames" not in events[-1]


@pytest.mark.parametrize("size", [(1280, 720), (640, 480), (720, 1280)])
def test_playback_preserves_recorded_geometry_and_missing_hand(source, size):
    root, key, path = source
    def change(rows):
        rows[0]["camera"] = {"mode": [*size, 30]}
        rows[1].pop("lm", None)
    rewrite(path, change)
    before = path.read_bytes()
    result = studio.playback(root, key)
    assert result["recording"]["frame_aspect"] == size[1] / size[0]
    assert result["recording"]["frame_size"] == list(size)
    assert result["recording"]["hand_frames"] == len(result["frames"]) - 1
    assert result["frames"][0]["lm"] == []
    assert result["frames"][1]["lm"] == read(path).frames[1]["lm"]
    assert path.read_bytes() == before
