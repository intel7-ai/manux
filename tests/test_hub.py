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
    GitHubDatasetHub,
    HfDatasetHub,
    HubError,
    delete_hub_token,
    load_dotenv,
    load_hub_config,
    load_hub_token,
    main,
    mask_token,
    parse_repo_input,
    pull_from_hub,
    push_to_hub,
    redact_secrets,
    save_hub_config,
    save_hub_token,
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


def test_parse_repo_input_supports_urls_and_short_names() -> None:
    assert parse_repo_input("se4nchoi/manux-datasets") == ("huggingface", "se4nchoi/manux-datasets", "main")
    assert parse_repo_input("owner/repo@dev", default_provider="github") == ("github", "owner/repo", "dev")
    assert parse_repo_input("https://github.com/my-org/hand-data.git") == ("github", "my-org/hand-data", "main")
    assert parse_repo_input("https://github.com/my-org/hand-data/tree/feature-1") == (
        "github",
        "my-org/hand-data",
        "feature-1",
    )
    assert parse_repo_input("https://huggingface.co/datasets/se4nchoi/manux-datasets/tree/v2") == (
        "huggingface",
        "se4nchoi/manux-datasets",
        "v2",
    )
    with pytest.raises(HubError):
        parse_repo_input("")
    with pytest.raises(HubError):
        parse_repo_input("https://example.com/foo/bar")


def test_token_encryption_vault_and_secret_redaction(tmp_path: Path) -> None:
    cfg_path = tmp_path / "hub.json"
    sec_path = tmp_path / "hub_secrets.bin"
    raw_hf_token = "hf_SuperSecretTokenValue987654321"
    raw_gh_token = "ghp_AnotherSecretGitHubToken123456789"

    saved = save_hub_config(
        provider="huggingface",
        repo="https://huggingface.co/datasets/team/gesture-hub",
        branch="main",
        token=raw_hf_token,
        config_path=cfg_path,
        secrets_path=sec_path,
    )
    assert saved["provider"] == "huggingface"
    assert saved["repo"] == "team/gesture-hub"

    # Plaintext token must NEVER appear in hub.json or raw bytes of hub_secrets.bin
    assert raw_hf_token not in cfg_path.read_text(encoding="utf-8")
    raw_vault_bytes = sec_path.read_bytes()
    assert raw_hf_token.encode("utf-8") not in raw_vault_bytes
    assert raw_vault_bytes.startswith((b"DPAPI1:", b"FERNET1:"))

    # Decryption round-trip works per provider
    assert load_hub_token("huggingface", secrets_path=sec_path, include_env=False) == raw_hf_token
    save_hub_token(raw_gh_token, provider="github", secrets_path=sec_path)
    assert load_hub_token("github", secrets_path=sec_path, include_env=False) == raw_gh_token
    assert load_hub_token("huggingface", secrets_path=sec_path, include_env=False) == raw_hf_token

    # Masking & redaction strip tokens from error strings
    masked = mask_token(raw_hf_token)
    assert masked == "hf_S...321"
    assert raw_hf_token not in masked
    err = HubError(f"Auth failed with Bearer {raw_hf_token} and {raw_gh_token}")
    assert raw_hf_token not in str(err)
    assert raw_gh_token not in str(err)
    assert redact_secrets(f"custom={raw_hf_token}", [raw_hf_token]) == f"custom={masked}"

    # Deleting token removes it cleanly
    delete_hub_token("huggingface", secrets_path=sec_path)
    assert load_hub_token("huggingface", secrets_path=sec_path, include_env=False) == ""
    assert load_hub_token("github", secrets_path=sec_path, include_env=False) == raw_gh_token
    delete_hub_token(None, secrets_path=sec_path)
    assert not sec_path.exists()
    assert load_hub_config(cfg_path)["repo"] == "team/gesture-hub"


def test_hf_and_github_rest_clients_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hub as hub_mod

    store: Dict[str, bytes] = {}

    lfs_blobs: Dict[str, bytes] = {}
    verified_oids: set[str] = set()

    def fake_http_request(url: str, *, method: str = "GET", headers: Any = None,
                          body: bytes | None = None, timeout: float = 30.0,
                          token: str | None = None) -> Tuple[int, bytes, Dict[str, str]]:
        # Hugging Face endpoints (including LFS/Xet preupload + batch upload for binary files)
        if "huggingface.co/api/repos/create" in url:
            return 200, b'{"url":"https://huggingface.co/datasets/org/ds"}', {}
        if "huggingface.co/api/datasets/org/ds/preupload/" in url and method == "POST":
            assert body is not None
            req_files = json.loads(body.decode("utf-8")).get("files", [])
            out_files = []
            for f_item in req_files:
                p = str(f_item["path"])
                mode = "lfs" if p.endswith((".onnx", ".jsonl.gz")) else "regular"
                out_files.append({"path": p, "uploadMode": mode, "shouldIgnore": False})
            return 200, json.dumps({"files": out_files}).encode("utf-8"), {}
        if "huggingface.co/datasets/org/ds.git/info/lfs/objects/batch" in url and method == "POST":
            assert body is not None
            req_objs = json.loads(body.decode("utf-8")).get("objects", [])
            resp_objs = []
            for o in req_objs:
                oid = o["oid"]
                size = o["size"]
                resp_objs.append({
                    "oid": oid,
                    "size": size,
                    "authenticated": True,
                    "actions": {
                        "upload": {"href": f"https://lfs-storage.example.com/put/{oid}"},
                        "verify": {"href": f"https://huggingface.co/lfs-verify/{oid}", "header": {"Authorization": "Bearer lfs"}},
                    },
                })
            return 200, json.dumps({"transfer": "basic", "objects": resp_objs}).encode("utf-8"), {}
        if url.startswith("https://lfs-storage.example.com/put/") and method == "PUT":
            assert body is not None
            oid = url.rsplit("/", 1)[1]
            lfs_blobs[oid] = body
            return 200, b"", {"ETag": '"etag1"'}
        if url.startswith("https://huggingface.co/lfs-verify/") and method == "POST":
            oid = url.rsplit("/", 1)[1]
            verified_oids.add(oid)
            return 200, b"{}", {}
        if "huggingface.co/api/datasets/org/ds/commit/" in url and method == "POST":
            assert body is not None
            for line in body.decode("utf-8").splitlines():
                obj = json.loads(line)
                if obj.get("key") == "file":
                    val = obj["value"]
                    import base64
                    store["hf:" + val["path"]] = base64.b64decode(val["content"])
                elif obj.get("key") == "lfsFile":
                    val = obj["value"]
                    oid = val["oid"]
                    assert oid in lfs_blobs and oid in verified_oids
                    store["hf:" + val["path"]] = lfs_blobs[oid]
            return 200, b'{"commitOid":"abc"}', {}
        if "huggingface.co/datasets/org/ds/resolve/" in url and method == "GET":
            rel = url.split("/resolve/main/", 1)[1]
            from urllib.parse import unquote
            key = "hf:" + unquote(rel)
            if key not in store:
                return 404, b"Not Found", {}
            return 200, store[key], {}

        # GitHub endpoints (Contents fallback path)
        if "api.github.com/repos/org/gh-ds/git/ref/" in url:
            return 404, b'{"message":"empty"}', {}
        if url.endswith("api.github.com/repos/org/gh-ds") and method == "GET":
            return 200, b'{"full_name":"org/gh-ds"}', {}
        if "api.github.com/repos/org/gh-ds/contents/" in url:
            from urllib.parse import unquote
            rel = unquote(url.split("/contents/", 1)[1].split("?", 1)[0])
            key = "gh:" + rel
            if method == "GET":
                if key not in store:
                    return 404, b'{"message":"Not Found"}', {}
                import base64
                b64 = base64.b64encode(store[key]).decode("ascii")
                return 200, json.dumps({"sha": "sha1", "encoding": "base64", "content": b64}).encode("utf-8"), {}
            if method == "PUT":
                assert body is not None
                import base64
                payload = json.loads(body.decode("utf-8"))
                store[key] = base64.b64decode(payload["content"])
                return 201, b'{"content":{"sha":"sha2"}}', {}
        return 404, b"Unhandled URL", {}

    monkeypatch.setattr(hub_mod, "_http_request", fake_http_request)

    src_dir = tmp_path / "src_recs"
    rec_path = _write_valid_recording(src_dir, person="dana", preset="pinch", split="train")
    model_path = _write_valid_model_pair(tmp_path / "src_models")

    # 1. Test Hugging Face REST fallback (with LFS/Xet binary upload for .jsonl.gz and .onnx)
    hf_client = HfDatasetHub("org/ds", token="hf_testtoken123456")
    hf_client._hf = None
    hf_client.api = None
    res_hf_push = push_to_hub(hf_client, src_dir, models=[model_path])
    assert res_hf_push["new_recordings"] == 1
    assert res_hf_push["models"] == 1
    hf_dst = tmp_path / "hf_pulled"
    hf_models_dst = tmp_path / "hf_pulled_models"
    res_hf_pull = pull_from_hub(hf_client, hf_dst, include_models=True, models_dir=hf_models_dst)
    assert res_hf_pull["downloaded"] == 1
    assert res_hf_pull["models_downloaded"] == 1
    assert (hf_dst / "dana" / rec_path.name).is_file()
    assert len(list(hf_models_dst.glob("*.onnx"))) == 1

    # 2. Test GitHub REST client
    gh_client = GitHubDatasetHub("https://github.com/org/gh-ds", token="ghp_testtoken123456")
    res_gh_push = push_to_hub(gh_client, src_dir)
    assert res_gh_push["new_recordings"] == 1
    gh_dst = tmp_path / "gh_pulled"
    res_gh_pull = pull_from_hub(gh_client, gh_dst)
    assert res_gh_pull["downloaded"] == 1
    assert (gh_dst / "dana" / rec_path.name).is_file()
