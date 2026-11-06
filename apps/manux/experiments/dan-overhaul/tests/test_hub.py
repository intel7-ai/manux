"""Unit tests for tools/hub.py (team dataset & model hub sync and contract checks).

Runs completely offline with a fake RemoteHub and synthetic hand observations.
"""
from __future__ import annotations

import gzip
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import pytest

from src.recorder import SessionRecorder
from src.tcn_features import FEATURE_VERSION, FEATURES

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
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


@dataclass(frozen=True)
class _Pt:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class _FakeHand:
    landmarks: Tuple[_Pt, ...]
    world: Tuple[Tuple[float, float, float], ...]
    handedness: str = "Right"
    score: float = 0.98


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
        for local_path, remote_path in uploads:
            self.files[remote_path] = Path(local_path).read_bytes()
        self.manifest = [dict(r) for r in manifest_lines]
        self.commits.append(message)
        return len(uploads)

    def download_file(self, remote_path: str, dest: Path) -> Path:
        if remote_path not in self.files:
            raise HubError(f"missing remote file: {remote_path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.files[remote_path])
        return dest


def _sample_hand() -> _FakeHand:
    pts = tuple(_Pt(0.1 + 0.01 * i, 0.2 + 0.01 * i, -0.02) for i in range(21))
    world = tuple((0.01 * i, -0.01 * i, 0.005 * i) for i in range(21))
    return _FakeHand(landmarks=pts, world=world)


def _write_valid_recording(folder: Path, *, person: str = "alice", preset: str = "pinch",
                           split: str = "train", label_name: str = "thumb_index",
                           frames: int = 4) -> Path:
    rec = SessionRecorder(
        folder,
        "guided",
        {
            "pc_alias": "desk-01",
            "person": person,
            "preset": preset,
            "split": split,
            "control_hand": "Right",
            "camera": {"source": "0", "mode": [1280, 720, 30.0], "mirror": True, "note": "webcam"},
        },
        now=100.0,
    )
    hand = _sample_hand()
    for i in range(frames):
        rec.frame(100.0 + i * 0.033, hand=hand, label={"phase": "hold", "label": label_name})
    out = rec.close()
    assert out is not None
    return out


def _write_valid_model_pair(folder: Path, stem: str = "gesture_tcn") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    onnx_path = folder / f"{stem}.onnx"
    json_path = folder / f"{stem}.json"
    onnx_path.write_bytes(b"FAKE-ONNX-BINARY-CONTENT")
    card = {
        "classes": ["none", "thumb_index", "thumb_ring"],
        "window": 15,
        "features": FEATURES,
        "feature_version": FEATURE_VERSION,
        "threshold": 0.7,
        "frames_in_a_row": 3,
        "trained": "2026-11-05 18:00:00",
        "data": [{"file": "sample.jsonl.gz", "frames": 100}],
        "held_out_results": [{"posture": 0}],
    }
    json_path.write_text(json.dumps(card), encoding="utf-8")
    return onnx_path


def test_validate_recording_accepts_valid_and_rejects_part_or_incomplete(tmp_path: Path) -> None:
    valid_path = _write_valid_recording(tmp_path, person="jun", preset="pinch", split="train")
    info = validate_recording(valid_path)
    assert info.frames == 4
    assert info.remote_path == f"recordings/jun/{valid_path.name}"
    assert info.entry["person"] == "jun"
    assert info.entry["preset"] == "pinch"
    assert info.entry["split"] == "train"
    assert info.entry["counts"] == {"thumb_index": 4}

    # .part files must be rejected by validate_recording and ignored by scan_local_recordings
    part_path = tmp_path / "unfinished.jsonl.gz.part"
    shutil.copy2(valid_path, part_path)
    with pytest.raises(HubError, match=r"only completed '\.jsonl\.gz'"):
        validate_recording(part_path)

    scanned, errors = scan_local_recordings(tmp_path)
    assert errors == []
    assert [r.path for r in scanned] == [valid_path]

    # Derived folders (cleaned/ and augmented/) must be ignored by scan_local_recordings
    cleaned_dir = tmp_path / "cleaned"
    cleaned_dir.mkdir()
    shutil.copy2(valid_path, cleaned_dir / valid_path.name.replace(".jsonl.gz", ".cleaned.jsonl.gz"))
    aug_dir = tmp_path / "augmented"
    aug_dir.mkdir()
    shutil.copy2(valid_path, aug_dir / valid_path.name.replace(".jsonl.gz", ".aug0.jsonl.gz"))
    scanned2, errors2 = scan_local_recordings(tmp_path)
    assert errors2 == []
    assert [r.path for r in scanned2] == [valid_path]

    # Truncated recording without "end" line must be rejected
    no_end = tmp_path / "20261105-000000-00000000-desk-01-guided.jsonl.gz"
    with gzip.open(no_end, "wt", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "meta", "schema": "hand-mouse-recording/1", "mode": "guided",
            "pc": "desk-01", "pc_named": "alias",
        }) + "\n")
        f.write(json.dumps({"type": "frame", "t": 0.0, "label": {"label": "none"}}) + "\n")
    with pytest.raises(HubError, match="missing closing 'end' record"):
        validate_recording(no_end)


def test_validate_recording_blocks_personal_host_leak(tmp_path: Path) -> None:
    leaked = tmp_path / "leaked.jsonl.gz"
    with gzip.open(leaked, "wt", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "meta", "schema": "hand-mouse-recording/1", "mode": "guided",
            "pc": "DESKTOP-REALHOST",
        }) + "\n")
        f.write(json.dumps({"type": "frame", "t": 0.0, "label": {"label": "none"}}) + "\n")
        f.write(json.dumps({"type": "end", "frames": 1, "counts": {"none": 1}, "marks": 0}) + "\n")
    with pytest.raises(HubError, match="legacy unhashed PC name"):
        validate_recording(leaked)


def test_push_and_pull_incremental_sync_with_filters_and_models(tmp_path: Path) -> None:
    hub = FakeRemoteHub()
    alice_dir = tmp_path / "alice_local"
    bob_dir = tmp_path / "bob_local"

    r1 = _write_valid_recording(alice_dir, person="alice", preset="pinch", split="train", label_name="thumb_index")
    r2 = _write_valid_recording(alice_dir, person="bob", preset="pointer", split="eval", label_name="thumb_ring")
    onnx_model = _write_valid_model_pair(tmp_path / "alice_models")

    # 1st push uploads 2 recordings + 1 model pair (4 files + manifest)
    res1 = push_to_hub(hub, alice_dir, models=[onnx_model])
    assert res1 == {
        "scanned": 2,
        "already_on_hub": 0,
        "new_recordings": 2,
        "models": 1,
        "committed_files": 4,
        "dry_run": False,
    }
    assert len(hub.commits) == 1

    # 2nd push is a no-op because SHA-256 hashes are already on the Hub
    res2 = push_to_hub(hub, alice_dir, models=[onnx_model])
    assert res2["new_recordings"] == 0
    assert res2["already_on_hub"] == 2
    assert res2["committed_files"] == 0
    assert len(hub.commits) == 1

    # Filtered pull (--split train) downloads only Alice's train recording
    bob_models = tmp_path / "bob_models"
    pull1 = pull_from_hub(hub, bob_dir, splits=["train"], include_models=True, models_dir=bob_models)
    assert pull1 == {
        "matched_remote": 1,
        "already_local": 0,
        "downloaded": 1,
        "models_downloaded": 1,
        "dry_run": False,
    }
    assert (bob_dir / "alice" / r1.name).is_file()
    assert not (bob_dir / "bob" / r2.name).exists()
    assert len(list(bob_models.glob("*.onnx"))) == 1
    assert len(list(bob_models.glob("*.json"))) == 1

    # Re-running the same pull downloads 0 files (incremental)
    pull2 = pull_from_hub(hub, bob_dir, splits=["train"], include_models=True, models_dir=bob_models)
    assert pull2["downloaded"] == 0
    assert pull2["already_local"] == 1
    assert pull2["models_downloaded"] == 0

    # CLI status works with the injected fake hub
    rc = main(["status", "--dir", str(bob_dir), "--repo", "org/manux-datasets"], hub=hub)
    assert rc == 0


def test_validate_model_pair_rejects_missing_or_bad_card(tmp_path: Path) -> None:
    onnx_only = tmp_path / "broken.onnx"
    onnx_only.write_bytes(b"ONNX")
    with pytest.raises(HubError, match="invalid model card"):
        validate_model_pair(onnx_only)

    onnx_only.with_suffix(".json").write_text(
        json.dumps({"classes": ["none", "thumb_index"], "feature_version": "wrong-v0"}),
        encoding="utf-8",
    )
    with pytest.raises(HubError, match="invalid model card"):
        validate_model_pair(onnx_only)


def test_load_dotenv_reads_env_and_skips_placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANUX_HF_REPO", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nMANUX_HF_REPO=se4nchoi/manux-datasets\nHF_TOKEN=hf_paste_your_team_token_here\n",
        encoding="utf-8",
    )
    loaded = load_dotenv([env_file])
    assert loaded == {"MANUX_HF_REPO": "se4nchoi/manux-datasets"}
    assert "HF_TOKEN" not in loaded

    env_file.write_text("HF_TOKEN='hf_real_test_token_123'\n", encoding="utf-8")
    loaded2 = load_dotenv([env_file])
    assert loaded2 == {"HF_TOKEN": "hf_real_test_token_123"}
