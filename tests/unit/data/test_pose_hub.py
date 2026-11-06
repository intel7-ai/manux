import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from manux.data.pose_studio import PoseSnapshotStudio
from pose_hub import push, pull, validate
from test_model_library import MemoryHub


def test_action_specific_pose_round_trip_preserves_split_and_excludes_extra_metadata(tmp_path):
    studio = PoseSnapshotStudio(tmp_path / "source")
    studio.profile = "synthetic"
    hand = tuple((i * .01, (i % 4) * .02, i * .001) for i in range(21))
    for label in ("thumb_ring", "thumb_middle"):
        studio.capture_snapshot(ground_truth=label,landmarks=hand)
    original = studio.save_dataset()
    raw = json.loads(original.read_text(encoding="utf-8"))
    raw["private_note"] = "synthetic-extra-field"
    raw["samples"][0]["private_note"] = "synthetic-extra-field"
    original.write_text(json.dumps(raw),encoding="utf-8")
    hub = MemoryHub()
    assert push(hub,studio.snapshots_dir,"synthetic",["thumb_ring"]) == 1
    assert push(hub,studio.snapshots_dir,"synthetic",["thumb_ring"]) == 0
    assert all("private_note" not in data.decode() for data in hub.files.values())
    assert pull(hub,tmp_path/"received",["thumb_middle"]) == 0
    assert pull(hub,tmp_path/"received",["thumb_ring"]) == 1
    assert pull(hub,tmp_path/"received",["thumb_ring"]) == 0
    received = next((tmp_path/"received").rglob("*.json"))
    doc = json.loads(received.read_text(encoding="utf-8"))
    assert doc["split"] == "train" and len(doc["samples"]) == 1
    assert doc["samples"][0]["groundTruthAction"] == "thumb_ring"
    received.write_text("corrupted",encoding="utf-8")
    with pytest.raises(ValueError,match="기존 포즈"): pull(hub,tmp_path/"received",["thumb_ring"])
    assert received.read_text() == "corrupted"


def test_eval_pose_samples_are_never_added_to_training(tmp_path):
    studio = PoseSnapshotStudio(tmp_path)
    studio.profile, studio.split = "synthetic", "eval"
    studio.capture_snapshot(landmarks=tuple((i*.01,i*.02,0.) for i in range(21)))
    path = studio.save_dataset()
    studio.clear_samples()
    assert studio.load_all_samples() == []
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["samples"][0]["rawLandmarks"][0]["z"] = float("inf")
    with pytest.raises(ValueError,match="좌표"): validate(doc)
