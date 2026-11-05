"""Unit tests for tools/hub.py (team dataset & model hub sync and contract checks).

Runs completely offline with a fake RemoteHub and synthetic hand observations (D-006).
"""
from __future__ import annotations

import gzip
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import pytest

from manux.core.contracts import FrameInfo, HandObservation
from manux.data.recorder import SessionRecorder
from manux.perception.local3d import FEATURE_VERSION, FEATURES

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from hub import (  # noqa: E402
    HubError,
    load_dotenv,
    main,
    pull_from_hub,
    push_to_hub,
    scan_local_recordings,
    validate_model_pair,
    validate_recording,
)


class FakeRemoteHub:
    """In-memory Hugging Face Dataset repo double."""

    def __init__(self) -> None:
        self.manifest: List[Dict[str, Any]] = []
        self.files: Dict[str, bytes] = {}
        self.commits: List[str] = []

    def read_manifest(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.manifest]

    def push_files(self, uploads: Sequence[Tuple[Path, str]], manifest_lines: Sequence[Dict[str, Any]],
                   message: str) -> int:
        for local, remote in uploads:
            self.files[remote] = Path(local).read_bytes()
        self.manifest = [dict(r) for r in manifest_lines]
        self.commits.append(message)
        return len(uploads)

    def download_file(self, remote_path: str, dest: Path) -> Path:
        if remote_path not in self.files:
            raise HubError(f"missing on fake hub: {remote_path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.files[remote_path])
        return dest


def _hand(t: float) -> HandObservation:
    frame = FrameInfo(0, t, 640, 480, True)
    lm = tuple((0.3 + i * 0.01, 0.4 + (i % 5) * 0.01, 0.0) for i in range(21))
    world = tuple((i * 0.001, (i % 4) * 0.002, (i % 2) * 0.001) for i in range(21))
    return HandObservation(frame, lm, "Right", 0.95, world)


def _make_recording(root: Path, *, person: str = "tester01", preset: str = "pinch",
                    split: str = "train", target: str = "thumb_ring", stamp: str = "20261105-150000") -> Path:
    folder = root / f"profile-{person}" / preset / split
    meta = {"person": person, "preset": preset, "split": split}
    with SessionRecorder(folder, "guided", meta, now=0.0, alias="desk01", stamp=stamp) as rec:
        for i in range(5):
            rec.frame(i * 0.05, _hand(i * 0.05),
                      label={"target": target, "task": target, "condition_key": "pose-0123456789abcdef"})
    return rec.path


def _make_model_pair(folder: Path, name: str = "gesture_tcn", *, feature_version: str = FEATURE_VERSION) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    onnx_path = folder / f"{name}.onnx"
    onnx_path.write_bytes(b"fake-onnx-binary")
    card = {
        "classes": ["none", "thumb_ring"],
        "window": 15,
        "features": FEATURES,
        "feature_version": feature_version,
        "threshold": 0.7,
        "frames_in_a_row": 3,
        "trained": "2026-11-05 15:00:00",
        "data": [{"file": "sample.jsonl.gz", "frames": 5}],
        "held_out_results": [{"group": "tester01", "f1": 0.9}],
        "kind": "base",
        "gestures": {"thumb_ring": "pinch.thumb_ring"},
    }
    (folder / f"{name}.json").write_text(json.dumps(card), encoding="utf-8")
    return onnx_path


def test_validate_recording_accepts_complete_and_ignores_part(tmp_path: Path):
    rec_path = _make_recording(tmp_path, person="tester01", target="thumb_ring")
    unfinished = SessionRecorder(tmp_path / "profile-tester01/pinch/train", "guided",
                                 {"person": "tester01"}, now=0.0, stamp="20261105-150099")
    unfinished.frame(0.0, _hand(0.0))
    part_path = unfinished.abort()

    assert part_path.name.endswith(".part")
    with pytest.raises(HubError, match="unfinished"):
        validate_recording(part_path, root=tmp_path)

    valid, errors = scan_local_recordings(tmp_path)
    assert not errors
    assert len(valid) == 1
    entry = valid[0].entry
    assert entry["person"] == "tester01"
    assert entry["counts"] == {"thumb_ring": 5}
    assert entry["conditions"] == ["pose-0123456789abcdef"]
    assert entry["path"].startswith("recordings/profile-tester01/pinch/train/")


def test_validate_recording_rejects_forbidden_meta_and_unhashed_pc(tmp_path: Path):
    bad_meta = tmp_path / "bad_user.jsonl.gz"
    with gzip.open(bad_meta, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"type": "meta", "schema": "hand-mouse-recording/1",
                            "pc": "pc-1234", "pc_named": "code", "username": "real_person"}) + "\n")
        f.write(json.dumps({"type": "frame", "t": 0.0, "label": {"target": "none"}}) + "\n")
        f.write(json.dumps({"type": "end", "frames": 1, "counts": {"none": 1}, "marks": 0}) + "\n")
    with pytest.raises(HubError, match="forbidden personal/host metadata"):
        validate_recording(bad_meta)

    legacy_pc = tmp_path / "legacy_pc.jsonl.gz"
    with gzip.open(legacy_pc, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"type": "meta", "schema": "hand-mouse-recording/1", "pc": "SEAN-LAPTOP"}) + "\n")
        f.write(json.dumps({"type": "frame", "t": 0.0, "label": {"target": "none"}}) + "\n")
        f.write(json.dumps({"type": "end", "frames": 1, "counts": {"none": 1}, "marks": 0}) + "\n")
    with pytest.raises(HubError, match="unhashed PC name"):
        validate_recording(legacy_pc)


def test_validate_model_pair_checks_card_and_feature_version(tmp_path: Path):
    onnx_ok = _make_model_pair(tmp_path / "ok")
    vm = validate_model_pair(onnx_ok)
    assert vm.remote_onnx == "models/gesture_tcn.onnx"
    assert vm.remote_json == "models/gesture_tcn.json"

    onnx_bad = _make_model_pair(tmp_path / "bad", feature_version="local3d-v0")
    with pytest.raises(HubError, match="feature_version"):
        validate_model_pair(onnx_bad)


def test_incremental_push_and_filtered_pull(tmp_path: Path):
    hub = FakeRemoteHub()
    alice_dir = tmp_path / "alice_recordings"
    _make_recording(alice_dir, person="tester01", split="train", target="thumb_ring", stamp="20261105-150100")
    _make_recording(alice_dir, person="tester02", split="eval", target="thumb_index", stamp="20261105-150200")
    model_path = _make_model_pair(tmp_path / "models")

    first = push_to_hub(hub, alice_dir, models=[model_path])
    assert first["new_recordings"] == 2
    assert first["models"] == 1
    assert len(hub.commits) == 1

    # Second push with identical files does not create a duplicate commit
    second = push_to_hub(hub, alice_dir)
    assert second["already_on_hub"] == 2
    assert second["new_recordings"] == 0
    assert len(hub.commits) == 1

    # Teammate Bob pulls only eval split
    bob_dir = tmp_path / "bob_recordings"
    pulled_eval = pull_from_hub(hub, bob_dir, splits=["eval"])
    assert pulled_eval["matched_remote"] == 1
    assert pulled_eval["downloaded"] == 1

    # Pulling eval again downloads 0 files (incremental cache)
    pulled_again = pull_from_hub(hub, bob_dir, splits=["eval"])
    assert pulled_again["already_local"] == 1
    assert pulled_again["downloaded"] == 0

    # CLI entrypoint smoke check with fake hub (including --models download)
    bob_models = tmp_path / "bob_models"
    assert main(["status", "--dir", str(bob_dir)], hub=hub) == 0
    assert main(["pull", "--dir", str(bob_dir), "--person", "tester01", "--models",
                 "--models-dir", str(bob_models)], hub=hub) == 0
    bob_recs, errors = scan_local_recordings(bob_dir)
    assert not errors and len(bob_recs) == 2
    assert (bob_models / "gesture_tcn.onnx").is_file()
    assert (bob_models / "gesture_tcn.json").is_file()


def test_load_dotenv_reads_env_and_skips_placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("MANUX_HF_REPO", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "MANUX_HF_REPO=\"se4nchoi/manux-datasets\"\n"
        "HF_TOKEN=hf_paste_your_team_token_here\n",
        encoding="utf-8",
    )
    loaded = load_dotenv([env_file])
    assert loaded == {"MANUX_HF_REPO": "se4nchoi/manux-datasets"}
    assert "HF_TOKEN" not in loaded

    env_file.write_text("HF_TOKEN='hf_real_test_token_123'\n", encoding="utf-8")
    loaded2 = load_dotenv([env_file])
    assert loaded2 == {"HF_TOKEN": "hf_real_test_token_123"}
