"""Sync team gesture recordings (`.jsonl.gz`) and TCN models (`.onnx` + `.json`) via a Hugging Face Dataset repo.

Enforces recording & model card contracts before anything leaves the local machine:
- `.part` files (unfinished recordings) and derived subdirectories (`cleaned/`, `augmented/`) are ignored.
- Every recording must have a valid `meta` first line, an `end` last line, zero corrupt lines,
  and no forbidden personal/host keys (`FORBIDDEN_META`) or unhashed legacy PC names.
- Every `.onnx` model must have a sibling `.json` model card passing `validate_card` for `local3d-v1`.
- Incremental sync by SHA-256 (`manifest.jsonl` at the dataset repo root): `push` uploads only new
  files in one commit; `pull` downloads only missing files matching `--person` / `--preset` / `--split`.

Usage:
    python tools/hub.py status --local
    python tools/hub.py status --repo <org/manux-datasets>
    python tools/hub.py push --repo <org/manux-datasets>
    python tools/hub.py push --repo <org/manux-datasets> --model models/gesture_tcn.onnx
    python tools/hub.py pull --repo <org/manux-datasets> --split train
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.recorder import FORBIDDEN_META, LEGACY_RAW_PC_RE, SCHEMA, public_source, read_session  # noqa: E402
from src.tcn_features import FEATURE_VERSION, FEATURES  # noqa: E402

MANIFEST_FILE = "manifest.jsonl"
DEFAULT_REPO = "se4nchoi/manux-datasets"
ENV_REPO = "MANUX_HF_REPO"
ENV_TOKEN = "HF_TOKEN"
SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_-]+")
DERIVED_RECORDING_DIRS = frozenset({"cleaned", "augmented", ".cache"})

REQUIRED_CARD_KEYS = (
    "classes",
    "window",
    "features",
    "feature_version",
    "threshold",
    "frames_in_a_row",
    "trained",
    "data",
    "held_out_results",
)
CARD_KINDS = ("base", "personal", "gesture_binary")
CLASS_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def load_dotenv(search_paths: Optional[Sequence[Path]] = None) -> Dict[str, str]:
    """Load `KEY=VALUE` pairs from `.env` files into `os.environ` without overwriting existing vars."""
    if search_paths is None:
        search_paths = (
            Path.cwd() / ".env",
            ROOT / ".env",
            ROOT.parent / ".env",
        )
    loaded: Dict[str, str] = {}
    seen: set[Path] = set()
    for candidate in search_paths:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        for raw in resolved.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            if not key or not val or val == "hf_paste_your_team_token_here":
                continue
            if key not in os.environ:
                os.environ[key] = val
                loaded[key] = val
    return loaded


class HubError(RuntimeError):
    """Validation or remote-sync failure with a clear user-facing message."""


class ModelError(HubError):
    """Invalid or missing `.json` model card."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def card_path(model_path: Path) -> Path:
    """`gesture_tcn.onnx` -> `gesture_tcn.json` (same folder, same name)."""
    return Path(model_path).with_suffix(".json")


def _int_field(card: Mapping[str, Any], key: str, minimum: int = 1) -> int:
    v = card[key]
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        raise ModelError("invalid", f"{key} must be an integer >= {minimum}, got {v!r}")
    return v


def validate_card(card: Any, feature_version: str = FEATURE_VERSION, features: Optional[int] = FEATURES) -> Dict[str, Any]:
    """Validate a model card dictionary against the `local3d-v1` (133-feature) contract."""
    if not isinstance(card, Mapping):
        raise ModelError("invalid", "the card is not a JSON object")
    missing = [k for k in REQUIRED_CARD_KEYS if k not in card]
    if missing:
        raise ModelError("invalid", f"missing keys {missing}")
    classes = card["classes"]
    if (
        not isinstance(classes, list)
        or len(classes) < 2
        or not all(isinstance(c, str) and CLASS_RE.fullmatch(c) for c in classes)
        or len(set(classes)) != len(classes)
    ):
        raise ModelError("invalid", f"classes must be 2+ distinct lowercase names, got {classes!r}")
    _int_field(card, "window")
    n_features = _int_field(card, "features")
    _int_field(card, "frames_in_a_row")
    thr = card["threshold"]
    if isinstance(thr, bool) or not isinstance(thr, (int, float)) or not math.isfinite(thr) or not 0.0 < thr <= 1.0:
        raise ModelError("invalid", f"threshold must be within (0, 1], got {thr!r}")
    if card["feature_version"] != feature_version:
        raise ModelError("feature_version", f"model {card['feature_version']!r} != app {feature_version!r}")
    if features is not None and n_features != features:
        raise ModelError("feature_version", f"model has {n_features} features per frame, the app computes {features}")
    if not isinstance(card["data"], list) or not isinstance(card["held_out_results"], list):
        raise ModelError("invalid", "data and held_out_results must be lists")
    kind = card.get("kind") or card.get("model_kind") or "base"
    if kind not in CARD_KINDS:
        raise ModelError("invalid", f"kind must be one of {CARD_KINDS}, got {kind!r}")
    return dict(card)


def load_card(path: Path, feature_version: str = FEATURE_VERSION, features: Optional[int] = FEATURES) -> Dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise ModelError("missing", str(path.name))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModelError("unreadable", f"{path.name}: {type(exc).__name__}") from exc
    return validate_card(data, feature_version, features)


@dataclass(frozen=True)
class LocalRecording:
    path: Path
    sha256: str
    size_bytes: int
    meta: Mapping[str, Any]
    frames: int
    counts: Mapping[str, int]
    remote_path: str

    @property
    def entry(self) -> Dict[str, Any]:
        pc, cam_note = public_source(self.meta)
        person = _clean_token(self.meta.get("person") or self.meta.get("profile") or pc or "unknown", "unknown")
        preset = _clean_token(self.meta.get("preset") or self.meta.get("focus") or "pinch", "pinch")
        split = _clean_token(self.meta.get("split") or "train", "train")
        return {
            "type": "recording",
            "path": self.remote_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "schema": str(self.meta.get("schema", SCHEMA)),
            "mode": str(self.meta.get("mode", "guided")),
            "person": person,
            "preset": preset,
            "split": split,
            "pc": pc,
            "camera_note": cam_note,
            "control_hand": str(self.meta.get("control_hand") or "Right"),
            "created": str(self.meta.get("created") or ""),
            "frames": self.frames,
            "counts": dict(self.counts),
        }


@dataclass(frozen=True)
class LocalModelPair:
    onnx_path: Path
    json_path: Path
    onnx_sha256: str
    json_sha256: str
    card: Mapping[str, Any]
    remote_onnx: str
    remote_json: str

    @property
    def entry(self) -> Dict[str, Any]:
        return {
            "type": "model",
            "path": self.remote_onnx,
            "card_path": self.remote_json,
            "sha256": self.onnx_sha256,
            "card_sha256": self.json_sha256,
            "classes": list(self.card["classes"]),
            "window": int(self.card["window"]),
            "features": int(self.card["features"]),
            "feature_version": str(self.card["feature_version"]),
            "trained": str(self.card["trained"]),
        }


class RemoteHub(Protocol):
    """Minimal remote interface so tests run offline without touching Hugging Face."""

    def read_manifest(self) -> List[Dict[str, Any]]: ...

    def push_files(self, uploads: Sequence[Tuple[Path, str]], manifest_lines: Sequence[Dict[str, Any]],
                   message: str) -> int: ...

    def download_file(self, remote_path: str, dest: Path) -> Path: ...


def _clean_token(value: Any, fallback: str) -> str:
    cleaned = SAFE_TOKEN_RE.sub("", str(value or "")).strip("_-")
    return (cleaned[:32] if cleaned else fallback).lower()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_remote_path(path: Path, meta: Mapping[str, Any]) -> str:
    """`recordings/<person>/<filename>.jsonl.gz` inside the Hugging Face dataset repository."""
    pc, _ = public_source(meta)
    person = _clean_token(meta.get("person") or meta.get("profile") or pc or "unknown", "unknown")
    return f"recordings/{person}/{path.name}"


def validate_recording(path: Path) -> LocalRecording:
    """Strictly verify a `.jsonl.gz` recording before allowing it to be uploaded."""
    path = Path(path)
    if path.name.endswith(".part") or not path.name.endswith(".jsonl.gz"):
        raise HubError(f"{path.name}: only completed '.jsonl.gz' files are allowed")
    if not path.is_file():
        raise HubError(f"{path}: file does not exist")

    try:
        first_obj: Optional[Dict[str, Any]] = None
        end_obj: Optional[Dict[str, Any]] = None
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line_no, raw in enumerate(f, start=1):
                obj = json.loads(raw)
                if line_no == 1:
                    first_obj = obj
                if obj.get("type") == "end":
                    end_obj = obj
    except (OSError, EOFError, ValueError) as exc:
        raise HubError(f"{path.name}: corrupt gzip/json ({type(exc).__name__}: {exc})") from exc

    if not first_obj or first_obj.get("type") != "meta":
        raise HubError(f"{path.name}: first line must be a 'meta' record")
    if end_obj is None:
        raise HubError(f"{path.name}: missing closing 'end' record (incomplete session)")

    forbidden = sorted(k for k in first_obj if k.lower() in FORBIDDEN_META)
    if forbidden:
        raise HubError(f"{path.name}: forbidden personal/host metadata keys {forbidden}")

    raw_pc = str(first_obj.get("pc") or "")
    pc_named = first_obj.get("pc_named")
    if raw_pc and not pc_named and not LEGACY_RAW_PC_RE.match(raw_pc):
        raise HubError(
            f"{path.name}: legacy unhashed PC name {raw_pc!r} without 'pc_named' flag is not allowed on the Hub"
        )

    meta, frames, _ = read_session(path)
    if not meta.get("complete"):
        raise HubError(f"{path.name}: read_session marked recording incomplete")
    if meta.get("schema") != SCHEMA:
        raise HubError(f"{path.name}: unsupported schema {meta.get('schema')!r} (expected {SCHEMA!r})")
    if meta.get("mode") not in ("guided", "free"):
        raise HubError(f"{path.name}: mode must be 'guided' or 'free', got {meta.get('mode')!r}")
    if len(frames) == 0:
        raise HubError(f"{path.name}: recording has 0 frames")

    counts: Counter = Counter()
    for fr in frames:
        lab = fr.get("label") or {}
        if not fr.get("lm"):
            counts["no_hand"] += 1
        elif lab.get("label") or lab.get("target"):
            counts[str(lab.get("label") or lab.get("target"))] += 1
        elif lab.get("phase") not in (None, "hold", "do"):
            counts[str(lab.get("phase"))] += 1
        else:
            counts["none"] += 1

    sha = file_sha256(path)
    size_bytes = path.stat().st_size
    remote_path = canonical_remote_path(path, meta)
    return LocalRecording(
        path=path,
        sha256=sha,
        size_bytes=size_bytes,
        meta=meta,
        frames=len(frames),
        counts=dict(counts),
        remote_path=remote_path,
    )


def _is_derived_recording(recordings_dir: Path, candidate: Path) -> bool:
    """Skip derived pipeline outputs (`recordings/cleaned/`, `recordings/augmented/`, `*.cleaned.jsonl.gz`)."""
    try:
        rel_parts = candidate.relative_to(recordings_dir).parts[:-1]
    except ValueError:
        rel_parts = ()
    if any(part.lower() in DERIVED_RECORDING_DIRS for part in rel_parts):
        return True
    if candidate.name.endswith(".cleaned.jsonl.gz"):
        return True
    return False


def scan_local_recordings(recordings_dir: Path) -> Tuple[List[LocalRecording], List[str]]:
    """Scan `recordings_dir` recursively for raw `.jsonl.gz` files (ignoring `.part`, `cleaned/`, `augmented/`)."""
    recordings_dir = Path(recordings_dir)
    if not recordings_dir.exists():
        return [], []
    valid: List[LocalRecording] = []
    errors: List[str] = []
    seen_sha: Dict[str, Path] = {}
    for candidate in sorted(recordings_dir.rglob("*.jsonl.gz")):
        if candidate.name.endswith(".part") or _is_derived_recording(recordings_dir, candidate):
            continue
        try:
            rec = validate_recording(candidate)
        except HubError as exc:
            errors.append(str(exc))
            continue
        if rec.sha256 in seen_sha:
            continue
        seen_sha[rec.sha256] = candidate
        valid.append(rec)
    return valid, errors


def validate_model_pair(onnx_path: Path) -> LocalModelPair:
    """Verify `<model>.onnx` + `<model>.json` against `validate_card(..., 'local3d-v1', 133)`."""
    onnx_path = Path(onnx_path)
    if onnx_path.suffix.lower() != ".onnx" or not onnx_path.is_file():
        raise HubError(f"{onnx_path}: model file must be an existing '.onnx' file")
    jpath = card_path(onnx_path)
    try:
        card = load_card(jpath, FEATURE_VERSION, FEATURES)
    except ModelError as exc:
        raise HubError(f"{jpath.name}: invalid model card ({exc.code}: {exc.detail})") from exc
    onnx_sha = file_sha256(onnx_path)
    json_sha = file_sha256(jpath)
    short = onnx_sha[:8]
    stem = _clean_token(onnx_path.stem, "gesture_tcn")
    remote_onnx = f"models/{stem}-{short}.onnx"
    remote_json = f"models/{stem}-{short}.json"
    return LocalModelPair(
        onnx_path=onnx_path,
        json_path=jpath,
        onnx_sha256=onnx_sha,
        json_sha256=json_sha,
        card=card,
        remote_onnx=remote_onnx,
        remote_json=remote_json,
    )


def merge_manifest(existing: Sequence[Mapping[str, Any]],
                   added: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate manifest entries by `path` and `sha256`, sorted by `path`."""
    added_paths = {str(row.get("path") or "") for row in added if row.get("path")}
    by_sha: Dict[str, Dict[str, Any]] = {}
    for row in existing:
        if str(row.get("path") or "") in added_paths:
            continue
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
            raise HubError(
                "Hugging Face sync requires `huggingface-hub` (e.g. `uv pip install huggingface-hub`)."
            ) from exc
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
                        help="Local recordings directory (default: ./recordings)")

    st = sub.add_parser("status", parents=[common], help="Show local and remote dataset summary")
    st.add_argument("--local", action="store_true", help="Only inspect local recordings (offline)")

    ps = sub.add_parser("push", parents=[common], help="Validate and upload new local recordings/models")
    ps.add_argument("--model", type=Path, action="append", default=[],
                    help="Optional .onnx model to validate (.json card required) and upload")
    ps.add_argument("--dry-run", action="store_true", help="Validate and diff against Hub without uploading")

    pl = sub.add_parser("pull", parents=[common], help="Incrementally download missing recordings from Hub")
    pl.add_argument("--person", action="append", default=[], help="Filter by tester alias")
    pl.add_argument("--preset", action="append", default=[], help="Filter by preset or gesture focus")
    pl.add_argument("--split", action="append", default=[], help="Filter by split (train, eval)")
    pl.add_argument("--class", dest="classes", action="append", default=[], help="Filter by recorded class name")
    pl.add_argument("--models", action="store_true", help="Also download shared .onnx + .json models")
    pl.add_argument("--models-dir", type=Path, default=None, help="Directory for downloaded models")
    pl.add_argument("--dry-run", action="store_true", help="Show how many files would be downloaded")

    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None, hub: Optional[RemoteHub] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    recordings_dir = args.dir or (ROOT / "recordings")
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
