"""Sync team gesture recordings (`.jsonl.gz`) and TCN models (`.onnx` + `.json`) via a Hugging Face Dataset repo.

Enforces docs/contracts.md sections 9 and 10 and D-006 before anything leaves the local machine:
- `.part` files (unfinished recordings) are ignored.
- Every recording must have a valid `meta` first line, an `end` last line, zero corrupt lines,
  and no forbidden personal/host keys (`FORBIDDEN_META`) or unhashed legacy PC names.
- Every `.onnx` model must have a sibling `.json` model card passing `validate_card` for `local3d-v1`.
- Incremental sync by SHA-256 (`manifest.jsonl` at the dataset repo root): `push` uploads only new
  files in one commit; `pull` downloads only missing files matching `--person` / `--preset` / `--split`.

Usage:
    uv run --python 3.12 python tools/hub.py status --local
    uv run --python 3.12 --with huggingface-hub python tools/hub.py status --repo <org/manux-datasets>
    uv run --python 3.12 --with huggingface-hub python tools/hub.py push --repo <org/manux-datasets>
    uv run --python 3.12 --with huggingface-hub python tools/hub.py pull --repo <org/manux-datasets> --split train
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

from manux.data.recorder import FORBIDDEN_META, public_source
from manux.data.recording import Recording, RecordingError, read
from manux.ml.card import ModelCard, ModelError, card_path, load_card
from manux.perception.local3d import FEATURE_VERSION, FEATURES
from manux.settings.paths import resolve

MANIFEST_FILE = "manifest.jsonl"
DEFAULT_REPO = "se4nchoi/manux-datasets"
ENV_REPO = "MANUX_HF_REPO"
ENV_TOKEN = "HF_TOKEN"
PLACEHOLDER_TOKEN = "hf_paste_your_team_token_here"
_APP_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _APP_DIR.parent.parent


def load_dotenv(paths: Optional[Sequence[Path]] = None, *, override: bool = False) -> Dict[str, str]:
    """Read `KEY=VALUE` pairs from `.env` file(s) (`apps/manux/.env` and repo root `.env`) using stdlib only."""
    candidates = list(paths) if paths is not None else [_APP_DIR / ".env", _REPO_ROOT / ".env"]
    loaded: Dict[str, str] = {}
    for env_path in candidates:
        p = Path(env_path)
        if not p.is_file():
            continue
        for raw_line in p.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            if not key or val == PLACEHOLDER_TOKEN:
                continue
            if override or (key not in os.environ and key not in loaded):
                os.environ[key] = val
                loaded[key] = val
    return loaded


class HubError(ValueError):
    """Raised when a local file violates the recording/model contract or a hub operation fails."""


@dataclass(frozen=True)
class ValidatedRecording:
    path: Path
    remote_path: str
    sha256: str
    size_bytes: int
    entry: Dict[str, Any]


@dataclass(frozen=True)
class ValidatedModel:
    onnx_path: Path
    json_path: Path
    remote_onnx: str
    remote_json: str
    onnx_sha256: str
    card: ModelCard
    entry: Dict[str, Any]


class RemoteHub(Protocol):
    """Minimal interface over `huggingface_hub.HfApi` so unit tests run offline without the library."""

    def read_manifest(self) -> List[Dict[str, Any]]: ...

    def push_files(self, uploads: Sequence[Tuple[Path, str]], manifest_lines: Sequence[Dict[str, Any]],
                   message: str) -> int: ...

    def download_file(self, remote_path: str, dest: Path) -> Path: ...


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_remote_path(rec: Recording, path: Path, root: Optional[Path] = None) -> str:
    """`recordings/profile-<person>/<preset>/<split>/<filename>` in POSIX form."""
    if root is not None:
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
            if rel.startswith("profile-"):
                return f"recordings/{rel}"
        except ValueError:
            pass
    person = str(rec.meta.get("person") or rec.meta.get("profile") or "unknown").lower()
    preset = str(rec.meta.get("preset") or "pinch").lower()
    split = str(rec.meta.get("split") or "train").lower()
    return f"recordings/profile-{person}/{preset}/{split}/{path.name}"


def validate_recording(path: Path, root: Optional[Path] = None) -> ValidatedRecording:
    """Verify docs/contracts.md section 9 and build a `manifest.jsonl` index entry."""
    path = Path(path)
    if path.name.endswith(".part"):
        raise HubError(f"{path.name}: unfinished (.part) recording cannot be shared")
    try:
        rec = read(path)
    except (OSError, RecordingError) as exc:
        raise HubError(f"{path.name}: {exc}") from exc
    if not rec.complete or rec.end is None:
        raise HubError(f"{path.name}: missing 'end' line (incomplete recording)")
    if rec.bad_lines > 0:
        raise HubError(f"{path.name}: contains {rec.bad_lines} corrupt JSON line(s)")
    if not rec.frames:
        raise HubError(f"{path.name}: recording has 0 frames")

    bad_keys = FORBIDDEN_META & {str(k).lower() for k in rec.meta}
    if bad_keys:
        raise HubError(f"{path.name}: forbidden personal/host metadata key(s): {sorted(bad_keys)}")
    if rec.meta.get("pc") and not rec.meta.get("pc_named"):
        raise HubError(f"{path.name}: legacy recording contains unhashed PC name (missing pc_named)")

    pc, _ = public_source(rec.meta)
    person = str(rec.meta.get("person") or rec.meta.get("profile") or "unknown").lower()
    preset = str(rec.meta.get("preset") or "pinch").lower()
    split = str(rec.meta.get("split") or "train").lower()
    counts = dict((rec.end or {}).get("counts") or {})
    tasks = sorted({
        str((f.get("label") or {}).get("task"))
        for f in rec.frames
        if (f.get("label") or {}).get("task")
    })
    conditions = sorted({
        str((f.get("label") or {}).get("condition_key"))
        for f in rec.frames
        if (f.get("label") or {}).get("condition_key")
    })
    sha = file_sha256(path)
    size = path.stat().st_size
    remote = canonical_remote_path(rec, path, root)
    entry = {
        "type": "recording",
        "path": remote,
        "file": path.name,
        "sha256": sha,
        "size_bytes": size,
        "schema": rec.meta.get("schema"),
        "mode": rec.meta.get("mode"),
        "created": rec.meta.get("created"),
        "person": person,
        "pc": pc,
        "preset": preset,
        "split": split,
        "frames": len(rec.frames),
        "marks": int((rec.end or {}).get("marks", 0)),
        "counts": counts,
        "tasks": tasks,
        "conditions": conditions,
    }
    return ValidatedRecording(path=path, remote_path=remote, sha256=sha, size_bytes=size, entry=entry)


def validate_model_pair(onnx_path: Path) -> ValidatedModel:
    """Verify docs/contracts.md section 10 for a `.onnx` + `.json` model pair."""
    onnx_path = Path(onnx_path)
    if onnx_path.suffix.lower() != ".onnx":
        raise HubError(f"{onnx_path.name}: expected a .onnx file")
    if not onnx_path.is_file() or onnx_path.stat().st_size == 0:
        raise HubError(f"{onnx_path.name}: missing or empty .onnx file")
    cpath = card_path(onnx_path)
    try:
        card = load_card(cpath, FEATURE_VERSION, FEATURES)
    except ModelError as exc:
        raise HubError(f"{cpath.name}: {exc}") from exc
    sha = file_sha256(onnx_path)
    remote_onnx = f"models/{onnx_path.name}"
    remote_json = f"models/{cpath.name}"
    entry = {
        "type": "model",
        "path": remote_onnx,
        "card_path": remote_json,
        "file": onnx_path.name,
        "sha256": sha,
        "size_bytes": onnx_path.stat().st_size,
        "feature_version": card.feature_version,
        "classes": list(card.classes),
        "kind": card.kind,
        "trained": card.trained,
    }
    return ValidatedModel(
        onnx_path=onnx_path,
        json_path=cpath,
        remote_onnx=remote_onnx,
        remote_json=remote_json,
        onnx_sha256=sha,
        card=card,
        entry=entry,
    )


def scan_local_recordings(folder: Path) -> Tuple[List[ValidatedRecording], List[str]]:
    """Scan `folder` recursively for `.jsonl.gz` / `.jsonl` files, skipping `.part` files."""
    folder = Path(folder)
    if not folder.is_dir():
        return [], []
    valid: List[ValidatedRecording] = []
    errors: List[str] = []
    candidates = sorted(
        p for p in folder.rglob("*")
        if p.is_file() and (p.name.endswith(".jsonl.gz") or p.name.endswith(".jsonl"))
    )
    for path in candidates:
        try:
            valid.append(validate_recording(path, root=folder))
        except HubError as exc:
            errors.append(str(exc))
    return valid, errors


def merge_manifest(existing: Iterable[Mapping[str, Any]],
                   added: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate manifest entries by `sha256` (or `path`), sorted by `path`."""
    by_sha: Dict[str, Dict[str, Any]] = {}
    for row in existing:
        sha = str(row.get("sha256") or "")
        if sha:
            by_sha[sha] = dict(row)
    for row in added:
        sha = str(row.get("sha256") or "")
        if sha:
            by_sha[sha] = dict(row)
    return sorted(by_sha.values(), key=lambda r: (str(r.get("path", "")), str(r.get("sha256", ""))))


def filter_manifest(entries: Iterable[Mapping[str, Any]], *,
                    persons: Sequence[str] = (),
                    presets: Sequence[str] = (),
                    splits: Sequence[str] = (),
                    classes: Sequence[str] = ()) -> List[Dict[str, Any]]:
    person_set = {p.lower() for p in persons if p}
    preset_set = {p.lower() for p in presets if p}
    split_set = {s.lower() for s in splits if s}
    class_set = {c.lower() for c in classes if c}
    matched: List[Dict[str, Any]] = []
    for row in entries:
        if row.get("type", "recording") != "recording":
            continue
        if person_set and str(row.get("person", "")).lower() not in person_set:
            continue
        if preset_set and str(row.get("preset", "")).lower() not in preset_set:
            continue
        if split_set and str(row.get("split", "")).lower() not in split_set:
            continue
        if class_set:
            counts = row.get("counts") or {}
            if not any(c in counts and int(counts[c]) > 0 for c in class_set):
                continue
        matched.append(dict(row))
    return matched


def summarize_entries(entries: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    by_person: Counter = Counter()
    by_split: Counter = Counter()
    by_preset: Counter = Counter()
    by_class: Counter = Counter()
    total_frames = 0
    total_bytes = 0
    rec_files = 0
    models = 0
    for row in entries:
        if row.get("type", "recording") == "model":
            models += 1
            continue
        rec_files += 1
        frames = int(row.get("frames") or 0)
        total_frames += frames
        total_bytes += int(row.get("size_bytes") or 0)
        by_person[str(row.get("person") or "unknown")] += 1
        by_split[str(row.get("split") or "train")] += 1
        by_preset[str(row.get("preset") or "pinch")] += 1
        for cls, n in (row.get("counts") or {}).items():
            by_class[str(cls)] += int(n)
    return {
        "files": rec_files,
        "models": models,
        "frames": total_frames,
        "size_mb": round(total_bytes / (1024 * 1024), 2),
        "by_person": dict(sorted(by_person.items())),
        "by_preset": dict(sorted(by_preset.items())),
        "by_split": dict(sorted(by_split.items())),
        "by_class": dict(sorted(by_class.items())),
    }


def push_to_hub(hub: RemoteHub, recordings_dir: Path, *,
                models: Sequence[Path] = (), dry_run: bool = False) -> Dict[str, Any]:
    """Validate local recordings (and optional models) and upload only new files to the Hub."""
    valid_recs, errors = scan_local_recordings(recordings_dir)
    if errors:
        raise HubError("refusing to push due to invalid recording(s):\n  " + "\n  ".join(errors))
    valid_models = [validate_model_pair(m) for m in models]

    remote_entries = hub.read_manifest()
    remote_shas = {str(r.get("sha256")) for r in remote_entries if r.get("sha256")}

    new_recs = [r for r in valid_recs if r.sha256 not in remote_shas]
    new_models = [m for m in valid_models if m.onnx_sha256 not in remote_shas]
    uploads: List[Tuple[Path, str]] = [(r.path, r.remote_path) for r in new_recs]
    for m in new_models:
        uploads.append((m.onnx_path, m.remote_onnx))
        uploads.append((m.json_path, m.remote_json))

    merged = merge_manifest(
        remote_entries,
        [*(r.entry for r in new_recs), *(m.entry for m in new_models)],
    )
    committed = 0
    if uploads and not dry_run:
        msg = f"Add {len(new_recs)} recording(s) and {len(new_models)} model(s)"
        committed = hub.push_files(uploads, merged, msg)
    return {
        "scanned": len(valid_recs),
        "already_on_hub": len(valid_recs) - len(new_recs),
        "new_recordings": len(new_recs),
        "models": len(new_models),
        "committed_files": committed,
        "dry_run": dry_run,
    }


def pull_from_hub(hub: RemoteHub, recordings_dir: Path, *,
                  persons: Sequence[str] = (),
                  presets: Sequence[str] = (),
                  splits: Sequence[str] = (),
                  classes: Sequence[str] = (),
                  include_models: bool = False,
                  models_dir: Optional[Path] = None,
                  dry_run: bool = False) -> Dict[str, Any]:
    """Download missing recordings matching the filters into `recordings_dir` (and optional models)."""
    recordings_dir = Path(recordings_dir)
    all_remote = hub.read_manifest()
    remote_entries = filter_manifest(
        all_remote, persons=persons, presets=presets, splits=splits, classes=classes,
    )
    local_recs, _ = scan_local_recordings(recordings_dir)
    local_shas = {r.sha256 for r in local_recs}

    to_download = [r for r in remote_entries if str(r.get("sha256")) not in local_shas]
    downloaded: List[Path] = []
    if not dry_run:
        for row in to_download:
            remote_path = str(row["path"])
            rel = remote_path[len("recordings/"):] if remote_path.startswith("recordings/") else remote_path
            dest = recordings_dir / Path(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            hub.download_file(remote_path, dest)
            downloaded.append(dest)

    models_downloaded = 0
    if include_models:
        target_models_dir = Path(models_dir) if models_dir is not None else recordings_dir.parent / "models"
        remote_models = [r for r in all_remote if r.get("type") == "model"]
        for m in remote_models:
            onnx_rel = str(m["path"])
            json_rel = str(m["card_path"])
            onnx_dest = target_models_dir / Path(onnx_rel).name
            json_dest = target_models_dir / Path(json_rel).name
            if onnx_dest.is_file() and json_dest.is_file() and file_sha256(onnx_dest) == m.get("sha256"):
                continue
            models_downloaded += 1
            if not dry_run:
                target_models_dir.mkdir(parents=True, exist_ok=True)
                hub.download_file(onnx_rel, onnx_dest)
                hub.download_file(json_rel, json_dest)

    return {
        "matched_remote": len(remote_entries),
        "already_local": len(remote_entries) - len(to_download),
        "downloaded": len(downloaded) if not dry_run else len(to_download),
        "models_downloaded": models_downloaded,
        "dry_run": dry_run,
    }


class HfDatasetHub:
    """Production implementation backed by `huggingface_hub.HfApi` (dataset repo)."""

    def __init__(self, repo_id: str, token: Optional[str] = None) -> None:
        if not repo_id or "/" not in repo_id:
            raise HubError(
                f"Hugging Face dataset repo id must look like '<org-or-user>/<repo-name>' "
                f"(pass --repo or set {ENV_REPO})"
            )
        try:
            import huggingface_hub as hf
        except ImportError as exc:
            raise HubError("Hugging Face sync requires: uv run --python 3.12 --with huggingface-hub ...") from exc
        self._hf = hf
        self.repo_id = repo_id
        self.token = token or os.environ.get(ENV_TOKEN) or None
        self.api = hf.HfApi(token=self.token)

    def ensure_repo(self) -> None:
        self.api.create_repo(repo_id=self.repo_id, repo_type="dataset", private=True, exist_ok=True)

    def read_manifest(self) -> List[Dict[str, Any]]:
        try:
            local = self._hf.hf_hub_download(
                repo_id=self.repo_id, filename=MANIFEST_FILE, repo_type="dataset", token=self.token,
            )
        except Exception as exc:
            name = type(exc).__name__
            if name in ("EntryNotFoundError", "RepositoryNotFoundError", "RevisionNotFoundError"):
                return []
            if "404" in str(exc):
                return []
            raise HubError(f"failed to read {MANIFEST_FILE} from {self.repo_id}: {exc}") from exc
        rows: List[Dict[str, Any]] = []
        for line in Path(local).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def push_files(self, uploads: Sequence[Tuple[Path, str]], manifest_lines: Sequence[Dict[str, Any]],
                   message: str) -> int:
        self.ensure_repo()
        ops = [
            self._hf.CommitOperationAdd(path_in_repo=remote, path_or_fileobj=str(local))
            for local, remote in uploads
        ]
        manifest_bytes = (
            "\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in manifest_lines) + "\n"
        ).encode("utf-8")
        ops.append(self._hf.CommitOperationAdd(path_in_repo=MANIFEST_FILE, path_or_fileobj=manifest_bytes))
        self.api.create_commit(
            repo_id=self.repo_id, repo_type="dataset", operations=ops, commit_message=message,
        )
        return len(uploads)

    def download_file(self, remote_path: str, dest: Path) -> Path:
        cached = Path(
            self._hf.hf_hub_download(
                repo_id=self.repo_id, filename=remote_path, repo_type="dataset", token=self.token,
            )
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached, dest)
        return dest


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="MANUX team dataset & model hub (Hugging Face Datasets)")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=os.environ.get(ENV_REPO, DEFAULT_REPO),
                        help=f"HF Dataset repo '<org>/<name>' (default: {DEFAULT_REPO} or {ENV_REPO})")
    common.add_argument("--token", default=os.environ.get(ENV_TOKEN, ""),
                        help=f"HF access token (or set {ENV_TOKEN} / `hf auth login`)")
    common.add_argument("--dir", type=Path, default=None,
                        help="Local recordings directory (default: MANUX user recordings_dir)")

    st = sub.add_parser("status", parents=[common], help="Show local and remote dataset summary")
    st.add_argument("--local", action="store_true", help="Only inspect local recordings (offline)")

    ps = sub.add_parser("push", parents=[common], help="Validate and upload new local recordings/models")
    ps.add_argument("--model", type=Path, action="append", default=[],
                    help="Optional .onnx model to validate (.json card required) and upload")
    ps.add_argument("--dry-run", action="store_true", help="Validate and diff against Hub without uploading")

    pl = sub.add_parser("pull", parents=[common], help="Incrementally download missing recordings from Hub")
    pl.add_argument("--person", action="append", default=[], help="Filter by tester alias")
    pl.add_argument("--preset", action="append", default=[], help="Filter by preset (pinch, pointer, trigger)")
    pl.add_argument("--split", action="append", default=[], help="Filter by split (train, eval)")
    pl.add_argument("--class", dest="classes", action="append", default=[], help="Filter by recorded class name")
    pl.add_argument("--models", action="store_true", help="Also download shared .onnx + .json models")
    pl.add_argument("--models-dir", type=Path, default=None, help="Directory for downloaded models")
    pl.add_argument("--dry-run", action="store_true", help="Show how many files would be downloaded")

    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None, hub: Optional[RemoteHub] = None) -> int:
    args = parse_args(argv)
    recordings_dir = args.dir or resolve().recordings_dir
    try:
        if args.command == "status":
            valid_recs, errors = scan_local_recordings(recordings_dir)
            local_summary = summarize_entries([r.entry for r in valid_recs])
            out: Dict[str, Any] = {"recordings_dir": str(recordings_dir), "local": local_summary}
            if errors:
                out["local_errors"] = errors
            if not args.local and (hub is not None or args.repo):
                client = hub or HfDatasetHub(args.repo, args.token)
                remote_entries = client.read_manifest()
                out["remote"] = summarize_entries(remote_entries)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0

        client = hub or HfDatasetHub(args.repo, args.token)
        if args.command == "push":
            res = push_to_hub(client, recordings_dir, models=args.model, dry_run=args.dry_run)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0
        if args.command == "pull":
            res = pull_from_hub(
                client, recordings_dir,
                persons=args.person, presets=args.preset, splits=args.split, classes=args.classes,
                include_models=args.models, models_dir=args.models_dir, dry_run=args.dry_run,
            )
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0
    except HubError as exc:
        print(f"error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
