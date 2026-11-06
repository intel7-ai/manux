"""Offline hub round trips and immutable named artifacts, using real ONNX validation."""
import json
from pathlib import Path
import sys

import numpy as np
import pytest

pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml"))
from test_selected_composition import component
import model_library as library
from manux.ml.spatial_mlp import SpatialMlpModel


class MemoryHub:
    def __init__(self): self.files, self.manifest = {}, []
    def read_manifest(self): return json.loads(json.dumps(self.manifest))
    def push_files(self, uploads, manifest, message):
        for local, remote in uploads: self.files[remote] = Path(local).read_bytes()
        self.manifest = manifest
        return len(uploads)
    def download_file(self, remote, dest):
        Path(dest).write_bytes(self.files[remote])
        return dest


def test_named_models_share_and_download_without_collisions_or_active_changes(tmp_path):
    hub = MemoryHub()
    source, destination = tmp_path / "source", tmp_path / "received"
    active = tmp_path / "active.json"; active.write_bytes(b"previous-valid-active")
    models = []
    for label in ("left", "right"):
        path = tmp_path / (label + ".onnx")
        component(path, label)
        row = library.add(source, path, "같은 이름")
        models.append(row)
        library.upload(hub, source, row["id"], "같은 이름")
    assert models[0]["sha256"] == models[1]["sha256"]  # identical weights, different class semantics
    assert models[0]["id"] != models[1]["id"]
    assert len(library.remote_rows(hub)) == 2
    for row in models:
        downloaded = library.download(hub, destination, row["id"])
        assert downloaded["classes"] == row["classes"]
    assert len(library.scan(destination)) == 2
    library.rename(destination, models[0]["id"], "책상 오른손 v2")
    assert library.local(destination, models[0]["id"])["name"] == "책상 오른손 v2"
    assert active.read_bytes() == b"previous-valid-active"
    # Tampering fails and never overwrites the immutable entry.
    path = library.folder(destination, models[0]["id"]) / "model.onnx"
    path.write_bytes(b"corrupted")
    with pytest.raises(Exception): library.download(hub, destination, models[0]["id"])
    assert path.read_bytes() == b"corrupted"
    assert len(library.scan(destination)) == 1
    assert active.read_bytes() == b"previous-valid-active"


def test_remote_path_hash_and_card_tampering_are_rejected(tmp_path):
    hub = MemoryHub(); path = tmp_path / "input.onnx"; component(path, "left")
    row = library.add(tmp_path / "source", path)
    library.upload(hub, tmp_path / "source", row["id"], "synthetic")
    saved = dict(hub.manifest[0])
    for bad in ("models/../../escape.onnx", "models/../escape.onnx", "models/x\\escape.onnx", "models/C:/escape.onnx"):
        hub.manifest[0] = {**saved, "path": bad}
        with pytest.raises(ValueError): library.download(hub, tmp_path / "destination", row["id"])
    hub.manifest[0] = saved
    hub.files[saved["card_path"]] = b"{}"
    with pytest.raises(ValueError, match="카드 해시"): library.download(hub, tmp_path / "destination", row["id"])
    assert not list((tmp_path / "destination").glob("[a-f0-9]"*64))


def test_pose_model_round_trip_and_finite_dimension_validation(tmp_path):
    path = tmp_path / "pose.json"
    model = SpatialMlpModel(np.zeros((4,96)),np.zeros(4),np.zeros((2,4)),np.zeros(2),classes=("none","thumb_ring"))
    model.save(path)
    row = library.add(tmp_path / "local", path, "포즈 v1")
    hub = MemoryHub(); library.upload(hub, tmp_path / "local", row["id"], row["name"])
    restored = library.download(hub, tmp_path / "received", row["id"])
    assert restored["format"] == "spatial" and restored["name"] == "포즈 v1"
    bad = model.to_dict(); bad["weights"]["W1"][0][0] = float("nan")
    with pytest.raises(ValueError): SpatialMlpModel.from_dict(bad)
    bad = model.to_dict(); bad["weights"]["W1"][0].pop()
    with pytest.raises(ValueError): SpatialMlpModel.from_dict(bad)
