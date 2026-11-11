"""Sync team gesture recordings (`.jsonl.gz`) and TCN models (`.onnx` + `.json`) via Hugging Face Dataset or GitHub repos.

Enforces recording & model card contracts before anything leaves the local machine:
- `.part` files (unfinished recordings) and derived subdirectories (`cleaned/`, `augmented/`) are ignored.
- Every recording must have a valid `meta` first line, an `end` last line, zero corrupt lines,
  and no forbidden personal/host keys (`FORBIDDEN_META`) or unhashed legacy PC names.
- Every `.onnx` model must have a sibling `.json` model card passing `validate_card` for `local3d-v1`.
- Incremental sync by SHA-256 (`manifest.jsonl` at the dataset repo root): `push` uploads only new
  files in one commit; `pull` downloads only missing files matching `--person` / `--preset` / `--split`.
- Token Security: Access tokens are encrypted at rest using Windows DPAPI (`CryptProtectData` bound to the
  current Windows user) or a machine-bound Fernet key (`cryptography`), never saved in plain text in
  tracked config files, and automatically redacted from logs and error messages.

Usage:
    python tools/hub.py status --local
    python tools/hub.py status --repo <org/manux-datasets>
    python tools/hub.py push --repo <org/manux-datasets>
    python tools/hub.py push --provider github --repo <owner/repo>
    python tools/hub.py push --repo <org/manux-datasets> --model models/gesture_tcn.onnx
    python tools/hub.py pull --repo <org/manux-datasets> --split train
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import getpass
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
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
DEFAULT_PROVIDER = "huggingface"
PROVIDERS = ("huggingface", "github")
ENV_PROVIDER = "MANUX_HUB_PROVIDER"
ENV_REPO = "MANUX_HF_REPO"
ENV_GH_REPO = "MANUX_GH_REPO"
ENV_TOKEN = "HF_TOKEN"
ENV_GH_TOKEN = "GITHUB_TOKEN"
HUB_CONFIG_PATH = ROOT / "config" / "hub.json"
HUB_SECRETS_PATH = ROOT / "config" / "hub_secrets.bin"
SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_-]+")
TOKEN_REDACT_RE = re.compile(
    r"(hf_[A-Za-z0-9]{10,}|gh[pousr]_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{15,}|Bearer\s+[A-Za-z0-9._\-]+)"
)
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

    def __init__(self, message: str = "") -> None:
        super().__init__(redact_secrets(str(message)))


def redact_secrets(text: str, extra_secrets: Sequence[str] = ()) -> str:
    """Mask any token patterns or explicit secret strings from log/error messages."""
    if not text:
        return ""
    out = TOKEN_REDACT_RE.sub(lambda m: mask_token(m.group(0)), str(text))
    for secret in extra_secrets:
        s = (secret or "").strip()
        if len(s) >= 6 and s in out:
            out = out.replace(s, mask_token(s))
    return out


def mask_token(token: Optional[str]) -> str:
    """Return a safe masked preview of an access token (e.g., `hf_a...9z`) without exposing the key."""
    raw = (token or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("bearer "):
        return "Bearer " + mask_token(raw[7:])
    if len(raw) <= 8:
        return "*" * len(raw)
    return f"{raw[:4]}...{raw[-3:]}"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_encrypt(data: bytes) -> bytes:
    """Encrypt bytes using Windows DPAPI (CryptProtectData) bound to the current OS user."""
    if sys.platform != "win32" or not hasattr(ctypes, "windll"):
        raise OSError("Windows DPAPI is unavailable on this platform")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    in_blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DATA_BLOB()
    # CRYPTPROTECT_UI_FORBIDDEN = 0x01
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "MANUX_HUB_SECRET",
        None,
        None,
        None,
        0x01,
        ctypes.byref(out_blob),
    ):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_decrypt(enc: bytes) -> bytes:
    """Decrypt bytes previously encrypted with Windows DPAPI (CryptUnprotectData)."""
    if sys.platform != "win32" or not hasattr(ctypes, "windll"):
        raise OSError("Windows DPAPI is unavailable on this platform")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(enc, len(enc))
    in_blob = _DATA_BLOB(len(enc), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0x01,
        ctypes.byref(out_blob),
    ):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _fernet_key() -> bytes:
    """Derive a deterministic machine+user key for Fernet fallback encryption."""
    try:
        user = getpass.getuser()
    except Exception:
        user = "manux-user"
    seed = f"manux-hub-vault-v1|{sys.platform}|{user}|{ROOT.resolve()}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    return base64.urlsafe_b64encode(digest)


def _encrypt_vault_payload(plaintext: bytes) -> bytes:
    """Encrypt secret bytes using DPAPI (preferred on Windows) or cryptography.fernet."""
    try:
        blob = _dpapi_encrypt(plaintext)
        return b"DPAPI1:" + base64.b64encode(blob)
    except Exception:
        from cryptography.fernet import Fernet
        token = Fernet(_fernet_key()).encrypt(plaintext)
        return b"FERNET1:" + token


def _decrypt_vault_payload(payload: bytes) -> bytes:
    """Decrypt secret bytes written by `_encrypt_vault_payload`."""
    if payload.startswith(b"DPAPI1:"):
        raw = base64.b64decode(payload[len(b"DPAPI1:"):])
        return _dpapi_decrypt(raw)
    if payload.startswith(b"FERNET1:"):
        from cryptography.fernet import Fernet
        return Fernet(_fernet_key()).decrypt(payload[len(b"FERNET1:"):])
    raise HubError("Unsupported or corrupted hub secret vault format")


def _load_vault_dict(secrets_path: Path = HUB_SECRETS_PATH) -> Dict[str, str]:
    secrets_path = Path(secrets_path)
    if not secrets_path.is_file():
        return {}
    try:
        raw = secrets_path.read_bytes()
        decrypted = _decrypt_vault_payload(raw)
        obj = json.loads(decrypted.decode("utf-8"))
        if isinstance(obj, dict):
            return {str(k): str(v) for k, v in obj.items() if v}
    except Exception:
        return {}
    return {}


def _save_vault_dict(vault: Mapping[str, str], secrets_path: Path = HUB_SECRETS_PATH) -> None:
    secrets_path = Path(secrets_path)
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    clean = {str(k): str(v) for k, v in vault.items() if v}
    if not clean:
        if secrets_path.exists():
            secrets_path.unlink()
        return
    plaintext = json.dumps(clean, ensure_ascii=False).encode("utf-8")
    encrypted = _encrypt_vault_payload(plaintext)
    tmp = secrets_path.with_suffix(".bin.tmp")
    tmp.write_bytes(encrypted)
    tmp.replace(secrets_path)


def save_hub_token(token: str, provider: str = DEFAULT_PROVIDER,
                   secrets_path: Path = HUB_SECRETS_PATH) -> None:
    """Encrypt and store an access token for `provider` in the local secret vault."""
    norm_provider = normalize_provider(provider)
    clean_tok = (token or "").strip()
    vault = _load_vault_dict(secrets_path)
    if clean_tok:
        vault[norm_provider] = clean_tok
    else:
        vault.pop(norm_provider, None)
    _save_vault_dict(vault, secrets_path)


def load_hub_token(provider: str = DEFAULT_PROVIDER,
                   secrets_path: Path = HUB_SECRETS_PATH,
                   include_env: bool = True) -> str:
    """Load the access token for `provider` from the encrypted vault (or environment fallback)."""
    norm_provider = normalize_provider(provider)
    vault = _load_vault_dict(secrets_path)
    if vault.get(norm_provider):
        return vault[norm_provider]
    if include_env:
        load_dotenv()
        if norm_provider == "github":
            return (os.environ.get(ENV_GH_TOKEN) or os.environ.get("GH_TOKEN") or "").strip()
        return (os.environ.get(ENV_TOKEN) or "").strip()
    return ""


def delete_hub_token(provider: Optional[str] = None,
                     secrets_path: Path = HUB_SECRETS_PATH) -> None:
    """Delete saved access token(s) from the encrypted local secret vault."""
    secrets_path = Path(secrets_path)
    if provider is None:
        if secrets_path.exists():
            secrets_path.unlink()
        return
    norm_provider = normalize_provider(provider)
    vault = _load_vault_dict(secrets_path)
    vault.pop(norm_provider, None)
    _save_vault_dict(vault, secrets_path)


def normalize_provider(provider: Optional[str]) -> str:
    val = (provider or "").strip().lower()
    if val in ("gh", "github", "github.com"):
        return "github"
    if val in ("hf", "huggingface", "hugging_face", "huggingface.co", ""):
        return "huggingface"
    raise HubError(f"Unsupported hub provider {provider!r} (expected 'huggingface' or 'github')")


def parse_repo_input(raw_input: str, default_provider: Optional[str] = None) -> Tuple[str, str, str]:
    """Parse a repository address or URL into `(provider, repo_id, branch)`.

    Accepts:
      - `owner/repo` or `owner/repo@branch`
      - `https://github.com/owner/repo` (or `/tree/<branch>`, `.git`)
      - `https://huggingface.co/datasets/owner/repo` (or `https://huggingface.co/owner/repo`)
    """
    text = (raw_input or "").strip()
    provider = normalize_provider(default_provider) if default_provider else DEFAULT_PROVIDER
    branch = "main"
    if not text:
        raise HubError("저장소 주소(예: owner/repo 또는 https://github.com/owner/repo)를 입력해 주세요.")

    if "://" in text or text.lower().startswith(("github.com/", "huggingface.co/", "hf.co/")):
        if "://" not in text:
            text = "https://" + text
        parsed = urllib.parse.urlparse(text)
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        parts = [p for p in parsed.path.split("/") if p]
        if host == "github.com":
            provider = "github"
            if len(parts) < 2:
                raise HubError(f"올바른 GitHub 저장소 주소가 아닙니다: {raw_input!r} (예: owner/repo)")
            owner, repo = parts[0], parts[1]
            if repo.endswith(".git"):
                repo = repo[:-4]
            if len(parts) >= 4 and parts[2] in ("tree", "blob"):
                branch = "/".join(parts[3:])
            text = f"{owner}/{repo}"
        elif host in ("huggingface.co", "hf.co"):
            provider = "huggingface"
            if parts and parts[0] in ("datasets", "spaces", "models"):
                parts = parts[1:]
            if len(parts) < 2:
                raise HubError(f"올바른 Hugging Face 저장소 주소가 아닙니다: {raw_input!r} (예: org/dataset-name)")
            owner, repo = parts[0], parts[1]
            if repo.endswith(".git"):
                repo = repo[:-4]
            if len(parts) >= 4 and parts[2] in ("tree", "blob", "resolve"):
                branch = "/".join(parts[3:])
            text = f"{owner}/{repo}"
        else:
            raise HubError(
                f"지원하지 않는 허브 호스트입니다: {host!r} (github.com 또는 huggingface.co 주소를 입력해 주세요)"
            )
    else:
        if "@" in text:
            text, branch_part = text.rsplit("@", 1)
            if branch_part.strip():
                branch = branch_part.strip()
        parts = [p for p in text.strip("/").split("/") if p]
        if len(parts) == 3 and parts[0] == "datasets":
            provider = "huggingface"
            parts = parts[1:]
        if len(parts) != 2:
            raise HubError(
                f"저장소 주소는 '<소유자>/<저장소이름>' 또는 전체 URL 형식이어야 합니다: {raw_input!r}"
            )
        owner, repo = parts[0], parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        text = f"{owner}/{repo}"

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text):
        raise HubError(f"유효하지 않은 저장소 이름 형식입니다: {text!r}")
    return provider, text, branch or "main"


def load_hub_config(config_path: Path = HUB_CONFIG_PATH) -> Dict[str, Any]:
    """Load non-sensitive hub settings (`provider`, `repo`, `branch`, `include_models`)."""
    load_dotenv()
    config_path = Path(config_path)
    raw: Dict[str, Any] = {}
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except Exception:
            raw = {}
    env_prov = os.environ.get(ENV_PROVIDER, "")
    provider = normalize_provider(raw.get("provider") or env_prov or DEFAULT_PROVIDER)
    default_repo = (
        os.environ.get(ENV_GH_REPO, "") if provider == "github" else os.environ.get(ENV_REPO, DEFAULT_REPO)
    )
    repo = str(raw.get("repo") or default_repo or DEFAULT_REPO).strip()
    branch = str(raw.get("branch") or "main").strip() or "main"
    include_models = bool(raw.get("include_models", True))
    return {
        "provider": provider,
        "repo": repo,
        "branch": branch,
        "include_models": include_models,
    }


def save_hub_config(
    *,
    provider: Optional[str] = None,
    repo: Optional[str] = None,
    branch: Optional[str] = None,
    include_models: Optional[bool] = None,
    token: Optional[str] = None,
    config_path: Path = HUB_CONFIG_PATH,
    secrets_path: Path = HUB_SECRETS_PATH,
) -> Dict[str, Any]:
    """Validate and save hub configuration; if `token` is provided (non-None), encrypt & store it separately."""
    current = load_hub_config(config_path)
    target_provider = normalize_provider(provider if provider is not None else current["provider"])
    target_branch = (branch if branch is not None else current.get("branch") or "main").strip() or "main"
    raw_repo = (repo if repo is not None else current.get("repo") or "").strip()
    if raw_repo:
        parsed_provider, parsed_repo, parsed_branch = parse_repo_input(raw_repo, default_provider=target_provider)
        # If the user pasted a full URL, honor its detected host/branch
        if "://" in raw_repo or raw_repo.lower().startswith(("github.com/", "huggingface.co/", "hf.co/", "datasets/")):
            target_provider = parsed_provider
        if ("@" in raw_repo or "/tree/" in raw_repo) and branch is None:
            target_branch = parsed_branch
        target_repo = parsed_repo
    else:
        target_repo = ""

    data = {
        "provider": target_provider,
        "repo": target_repo,
        "branch": target_branch,
        "include_models": bool(current["include_models"] if include_models is None else include_models),
    }
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(config_path)

    if token is not None and token.strip():
        save_hub_token(token.strip(), provider=target_provider, secrets_path=secrets_path)
    return data


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


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Mapping[str, str]] = None,
    body: Optional[bytes] = None,
    timeout: float = 30.0,
    token: Optional[str] = None,
) -> Tuple[int, bytes, Mapping[str, str]]:
    """Perform an HTTP request via urllib, redacting secrets from any raised HubError."""
    req_headers: Dict[str, str] = {"User-Agent": "MANUX-GestureHub/1.0"}
    if headers:
        req_headers.update(headers)
    if token and "Authorization" not in req_headers:
        req_headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            return int(status), resp.read(), resp.headers
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        return int(exc.code), err_body.encode("utf-8"), exc.headers or {}
    except urllib.error.URLError as exc:
        msg = redact_secrets(f"네트워크 연결 실패 ({url}): {exc.reason}", [token or ""])
        raise HubError(msg) from exc
    except OSError as exc:
        msg = redact_secrets(f"네트워크 요청 오류 ({url}): {exc}", [token or ""])
        raise HubError(msg) from exc


class HfDatasetHub:
    """Hugging Face Dataset repository client (`huggingface_hub.HfApi` when installed, or built-in HTTPS REST fallback)."""

    def __init__(self, repo_id: str, token: Optional[str] = None, branch: str = "main",
                 base_url: str = "https://huggingface.co") -> None:
        _, parsed_repo, parsed_branch = parse_repo_input(repo_id, default_provider="huggingface")
        self.repo_id = parsed_repo
        self.branch = (branch or parsed_branch or "main").strip() or "main"
        self.base_url = base_url.rstrip("/")
        resolved_tok = (token if token is not None else load_hub_token("huggingface")).strip()
        self.token: Optional[str] = resolved_tok or None
        try:
            import huggingface_hub as hf  # type: ignore[import-not-found]
            self._hf: Any = hf
            self.api: Any = hf.HfApi(token=self.token, endpoint=self.base_url)
        except ImportError:
            self._hf = None
            self.api = None

    def _quote_path(self, remote_path: str) -> str:
        return "/".join(urllib.parse.quote(part, safe="") for part in remote_path.strip("/").split("/") if part)

    def ensure_repo(self) -> None:
        if not self.token:
            raise HubError(
                "Hugging Face 업로드(push)에는 쓰기 권한이 있는 액세스 토큰(hf_...)이 필요합니다. 허브 설정에서 토큰을 입력해 주세요."
            )
        if self._hf is not None and self.api is not None:
            try:
                self.api.create_repo(repo_id=self.repo_id, repo_type="dataset", private=True, exist_ok=True)
                return
            except Exception as exc:
                raise HubError(redact_secrets(f"Hugging Face 저장소 준비 실패 ({self.repo_id}): {exc}", [self.token])) from exc

        owner, name = self.repo_id.split("/", 1)
        payload: Dict[str, Any] = {"type": "dataset", "name": name, "private": True}
        status, raw, _ = _http_request(
            f"{self.base_url}/api/repos/create",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload).encode("utf-8"),
            token=self.token,
        )
        if status in (200, 201, 409):
            return
        # Try with organization field if owner is an org
        payload["organization"] = owner
        status2, raw2, _ = _http_request(
            f"{self.base_url}/api/repos/create",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload).encode("utf-8"),
            token=self.token,
        )
        if status2 in (200, 201, 409):
            return
        # Check if repo already exists and is accessible
        check_status, _, _ = _http_request(
            f"{self.base_url}/api/datasets/{self.repo_id}",
            method="GET",
            token=self.token,
        )
        if check_status == 200:
            return
        detail = raw.decode("utf-8", errors="replace")[:240]
        if status in (401, 403):
            raise HubError(f"Hugging Face 인증 실패 ({status}): 토큰 권한(Write)과 저장소 이름({self.repo_id})을 확인해 주세요.")
        raise HubError(f"Hugging Face 저장소 생성/확인 실패 ({status}): {detail}")

    def read_manifest(self) -> List[Dict[str, Any]]:
        if self._hf is not None:
            try:
                local = self._hf.hf_hub_download(
                    repo_id=self.repo_id,
                    filename=MANIFEST_FILE,
                    repo_type="dataset",
                    revision=self.branch,
                    token=self.token,
                )
                text = Path(local).read_text(encoding="utf-8")
            except Exception as exc:
                name = type(exc).__name__
                if name in ("EntryNotFoundError", "RepositoryNotFoundError", "RevisionNotFoundError") or "404" in str(exc):
                    return []
                raise HubError(redact_secrets(f"failed to read {MANIFEST_FILE} from {self.repo_id}: {exc}", [self.token or ""])) from exc
        else:
            rev = urllib.parse.quote(self.branch, safe="")
            url = f"{self.base_url}/datasets/{self.repo_id}/resolve/{rev}/{MANIFEST_FILE}"
            status, body, _ = _http_request(url, method="GET", token=self.token)
            if status == 404:
                return []
            if status in (401, 403):
                raise HubError(
                    f"Hugging Face 저장소({self.repo_id}) 접근 거부 ({status}): 비공개 저장소라면 유효한 토큰(hf_...)을 설정해 주세요."
                )
            if status >= 400:
                detail = body.decode("utf-8", errors="replace")[:200]
                raise HubError(f"failed to read {MANIFEST_FILE} from {self.repo_id} (HTTP {status}): {detail}")
            text = body.decode("utf-8")

        rows: List[Dict[str, Any]] = []
        for line in text.splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def _upload_lfs_objects(self, lfs_items: Sequence[Dict[str, Any]]) -> None:
        """Upload binary files via the Hugging Face Git LFS batch API (basic & multipart transfers)."""
        if not lfs_items:
            return
        by_oid: Dict[str, Dict[str, Any]] = {str(it["sha256"]): it for it in lfs_items}
        batch_req = {
            "operation": "upload",
            "transfers": ["basic", "multipart"],
            "ref": {"name": f"refs/heads/{self.branch}"},
            "hash_algo": "sha256",
            "objects": [{"oid": oid, "size": int(it["size"])} for oid, it in by_oid.items()],
        }
        batch_url = f"{self.base_url}/datasets/{self.repo_id}.git/info/lfs/objects/batch"
        status, body, _ = _http_request(
            batch_url,
            method="POST",
            headers={
                "Accept": "application/vnd.git-lfs+json",
                "Content-Type": "application/vnd.git-lfs+json",
            },
            body=json.dumps(batch_req).encode("utf-8"),
            timeout=60.0,
            token=self.token,
        )
        if status >= 400:
            detail = body.decode("utf-8", errors="replace")[:240]
            raise HubError(f"Hugging Face LFS 배치 요청 실패 (HTTP {status}): {detail}")

        try:
            batch_resp = json.loads(body.decode("utf-8"))
        except ValueError as exc:
            raise HubError(f"Hugging Face LFS 배치 응답 파싱 실패: {exc}") from exc

        for obj in batch_resp.get("objects") or []:
            oid = str(obj.get("oid") or "")
            if obj.get("error"):
                err = obj["error"]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                raise HubError(f"Hugging Face LFS 객체 오류 ({oid[:8]}): {msg}")
            item = by_oid.get(oid)
            if not item:
                continue
            raw_bytes: bytes = item["bytes"]
            size = int(item["size"])
            actions = obj.get("actions") or {}

            up_action = actions.get("upload")
            if isinstance(up_action, dict) and up_action.get("href"):
                up_href = str(up_action["href"])
                up_headers = dict(up_action.get("header") or {})
                if "chunk_size" in up_headers:
                    chunk_size = int(up_headers.pop("chunk_size"))
                    part_keys = sorted(
                        (int(k), k, str(v)) for k, v in up_headers.items() if str(k).isdigit()
                    )
                    completed_parts: List[Dict[str, Any]] = []
                    for part_num, _, part_url in part_keys:
                        start = (part_num - 1) * chunk_size
                        chunk = raw_bytes[start : start + chunk_size]
                        p_status, p_body, p_hdrs = _http_request(
                            part_url,
                            method="PUT",
                            headers={"Content-Length": str(len(chunk))},
                            body=chunk,
                            timeout=300.0,
                            token=None,
                        )
                        if p_status >= 400:
                            detail = p_body.decode("utf-8", errors="replace")[:200]
                            raise HubError(f"Hugging Face LFS 분할 업로드 실패 (파트 {part_num}, HTTP {p_status}): {detail}")
                        etag = p_hdrs.get("ETag") or p_hdrs.get("etag") or ""
                        completed_parts.append({"partNumber": part_num, "etag": etag})

                    comp_status, comp_body, _ = _http_request(
                        up_href,
                        method="POST",
                        headers={
                            "Accept": "application/vnd.git-lfs+json",
                            "Content-Type": "application/vnd.git-lfs+json",
                        },
                        body=json.dumps({"oid": oid, "parts": completed_parts}).encode("utf-8"),
                        timeout=120.0,
                        token=None,
                    )
                    if comp_status >= 400:
                        detail = comp_body.decode("utf-8", errors="replace")[:200]
                        raise HubError(f"Hugging Face LFS 분할 업로드 완료 처리 실패 (HTTP {comp_status}): {detail}")
                else:
                    req_hdrs = {"Content-Type": "application/octet-stream", "Content-Length": str(size)}
                    req_hdrs.update({str(k): str(v) for k, v in up_headers.items()})
                    u_status, u_body, _ = _http_request(
                        up_href,
                        method="PUT",
                        headers=req_hdrs,
                        body=raw_bytes,
                        timeout=300.0,
                        token=None,
                    )
                    if u_status >= 400:
                        detail = u_body.decode("utf-8", errors="replace")[:200]
                        raise HubError(f"Hugging Face LFS 파일 전송 실패 ({item['remote']}, HTTP {u_status}): {detail}")

            ver_action = actions.get("verify")
            if isinstance(ver_action, dict) and ver_action.get("href"):
                ver_href = str(ver_action["href"])
                ver_headers = {
                    "Accept": "application/vnd.git-lfs+json",
                    "Content-Type": "application/vnd.git-lfs+json",
                }
                for k, v in (ver_action.get("header") or {}).items():
                    ver_headers[str(k)] = str(v)
                v_status, v_body, _ = _http_request(
                    ver_href,
                    method="POST",
                    headers=ver_headers,
                    body=json.dumps({"oid": oid, "size": size}).encode("utf-8"),
                    timeout=60.0,
                    token=self.token if "Authorization" not in ver_headers else None,
                )
                if v_status >= 400:
                    detail = v_body.decode("utf-8", errors="replace")[:200]
                    raise HubError(f"Hugging Face LFS 검증 실패 ({item['remote']}, HTTP {v_status}): {detail}")

    def push_files(self, uploads: Sequence[Tuple[Path, str]], manifest_lines: Sequence[Dict[str, Any]],
                   message: str) -> int:
        self.ensure_repo()
        manifest_bytes = (
            "\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in manifest_lines) + "\n"
        ).encode("utf-8")

        if self._hf is not None and self.api is not None:
            ops = [
                self._hf.CommitOperationAdd(path_in_repo=remote, path_or_fileobj=str(local))
                for local, remote in uploads
            ]
            ops.append(self._hf.CommitOperationAdd(path_in_repo=MANIFEST_FILE, path_or_fileobj=manifest_bytes))
            try:
                self.api.create_commit(
                    repo_id=self.repo_id,
                    repo_type="dataset",
                    revision=self.branch,
                    operations=ops,
                    commit_message=message,
                )
            except Exception as exc:
                raise HubError(redact_secrets(f"Hugging Face 커밋 실패 ({self.repo_id}): {exc}", [self.token or ""])) from exc
            return len(uploads)

        rev = urllib.parse.quote(self.branch, safe="")
        prepared: List[Dict[str, Any]] = []
        for local, remote in uploads:
            raw_bytes = Path(local).read_bytes()
            prepared.append({
                "local": Path(local),
                "remote": remote,
                "bytes": raw_bytes,
                "size": len(raw_bytes),
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "sample": base64.b64encode(raw_bytes[:512]).decode("ascii"),
            })

        # Query Hugging Face preupload endpoint to determine which files must use LFS/Xet storage
        upload_modes: Dict[str, str] = {}
        if prepared:
            pre_url = f"{self.base_url}/api/datasets/{self.repo_id}/preupload/{rev}"
            pre_payload = {
                "files": [
                    {"path": it["remote"], "size": it["size"], "sample": it["sample"]}
                    for it in prepared
                ]
            }
            pre_status, pre_body, _ = _http_request(
                pre_url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=json.dumps(pre_payload).encode("utf-8"),
                timeout=60.0,
                token=self.token,
            )
            if pre_status == 200:
                try:
                    pre_json = json.loads(pre_body.decode("utf-8"))
                    for f_info in pre_json.get("files") or []:
                        if isinstance(f_info, dict) and f_info.get("path"):
                            upload_modes[str(f_info["path"])] = str(f_info.get("uploadMode") or "regular")
                except ValueError:
                    pass
            elif pre_status != 404:
                detail = pre_body.decode("utf-8", errors="replace")[:240]
                raise HubError(f"Hugging Face 업로드 사전 검사(preupload) 실패 (HTTP {pre_status}): {detail}")

        lfs_items = [it for it in prepared if upload_modes.get(it["remote"]) == "lfs"]
        if lfs_items:
            self._upload_lfs_objects(lfs_items)

        ndjson_lines: List[str] = [
            json.dumps({"key": "header", "value": {"summary": message, "description": ""}}, ensure_ascii=False)
        ]
        for it in prepared:
            remote = it["remote"]
            if upload_modes.get(remote) == "lfs":
                ndjson_lines.append(
                    json.dumps(
                        {
                            "key": "lfsFile",
                            "value": {
                                "path": remote,
                                "algo": "sha256",
                                "oid": it["sha256"],
                                "size": it["size"],
                            },
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                b64 = base64.b64encode(it["bytes"]).decode("ascii")
                ndjson_lines.append(
                    json.dumps(
                        {"key": "file", "value": {"content": b64, "path": remote, "encoding": "base64"}},
                        ensure_ascii=False,
                    )
                )
        manifest_b64 = base64.b64encode(manifest_bytes).decode("ascii")
        ndjson_lines.append(
            json.dumps(
                {"key": "file", "value": {"content": manifest_b64, "path": MANIFEST_FILE, "encoding": "base64"}},
                ensure_ascii=False,
            )
        )
        body = ("\n".join(ndjson_lines) + "\n").encode("utf-8")
        url = f"{self.base_url}/api/datasets/{self.repo_id}/commit/{rev}"
        status, resp_body, _ = _http_request(
            url,
            method="POST",
            headers={"Content-Type": "application/x-ndjson"},
            body=body,
            timeout=120.0,
            token=self.token,
        )
        if status >= 400:
            detail = resp_body.decode("utf-8", errors="replace")[:280]
            raise HubError(f"Hugging Face 파일 업로드 실패 (HTTP {status}): {detail}")
        return len(uploads)

    def download_file(self, remote_path: str, dest: Path) -> Path:
        if self._hf is not None:
            try:
                cached = Path(
                    self._hf.hf_hub_download(
                        repo_id=self.repo_id,
                        filename=remote_path,
                        repo_type="dataset",
                        revision=self.branch,
                        token=self.token,
                    )
                )
            except Exception as exc:
                raise HubError(redact_secrets(f"파일 다운로드 실패 ({remote_path}): {exc}", [self.token or ""])) from exc
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cached, dest)
            return dest

        rev = urllib.parse.quote(self.branch, safe="")
        quoted_path = self._quote_path(remote_path)
        url = f"{self.base_url}/datasets/{self.repo_id}/resolve/{rev}/{quoted_path}"
        status, body, _ = _http_request(url, method="GET", timeout=120.0, token=self.token)
        if status >= 400:
            detail = body.decode("utf-8", errors="replace")[:200]
            raise HubError(f"Hugging Face 파일 다운로드 실패 ({remote_path}, HTTP {status}): {detail}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return dest


class GitHubDatasetHub:
    """GitHub repository client using the GitHub REST API v3 (Contents & Git Data APIs)."""

    def __init__(self, repo_id: str, token: Optional[str] = None, branch: str = "main",
                 api_base: str = "https://api.github.com") -> None:
        _, parsed_repo, parsed_branch = parse_repo_input(repo_id, default_provider="github")
        self.repo_id = parsed_repo
        self.branch = (branch or parsed_branch or "main").strip() or "main"
        self.api_base = api_base.rstrip("/")
        resolved_tok = (token if token is not None else load_hub_token("github")).strip()
        self.token: Optional[str] = resolved_tok or None

    def _headers(self, accept: str = "application/vnd.github+json") -> Dict[str, str]:
        h: Dict[str, str] = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _quote_path(self, remote_path: str) -> str:
        return "/".join(urllib.parse.quote(part, safe="") for part in remote_path.strip("/").split("/") if part)

    def ensure_repo(self) -> None:
        if not self.token:
            raise HubError(
                "GitHub 업로드(push)에는 쓰기 권한(Contents: Read & Write 또는 repo)이 있는 토큰(ghp_... / github_pat_...)이 필요합니다."
            )
        status, body, _ = _http_request(
            f"{self.api_base}/repos/{self.repo_id}",
            method="GET",
            headers=self._headers(),
            token=self.token,
        )
        if status == 200:
            return
        if status == 404:
            owner, name = self.repo_id.split("/", 1)
            payload = json.dumps({"name": name, "private": True, "auto_init": True}).encode("utf-8")
            c_status, c_body, _ = _http_request(
                f"{self.api_base}/user/repos",
                method="POST",
                headers={**self._headers(), "Content-Type": "application/json"},
                body=payload,
                token=self.token,
            )
            if c_status in (200, 201):
                return
            # Try organization endpoint
            o_status, o_body, _ = _http_request(
                f"{self.api_base}/orgs/{owner}/repos",
                method="POST",
                headers={**self._headers(), "Content-Type": "application/json"},
                body=payload,
                token=self.token,
            )
            if o_status in (200, 201):
                return
            detail = c_body.decode("utf-8", errors="replace")[:240]
            raise HubError(f"GitHub 저장소({self.repo_id})를 찾을 수 없거나 생성할 수 없습니다 (HTTP {c_status}): {detail}")
        if status in (401, 403):
            raise HubError(f"GitHub 인증 실패 (HTTP {status}): 토큰 권한과 저장소 주소({self.repo_id})를 확인해 주세요.")
        detail = body.decode("utf-8", errors="replace")[:240]
        raise HubError(f"GitHub 저장소 확인 실패 (HTTP {status}): {detail}")

    def _fetch_file_bytes(self, remote_path: str, allow_missing: bool = False) -> Optional[bytes]:
        quoted = self._quote_path(remote_path)
        ref = urllib.parse.quote(self.branch, safe="")
        url = f"{self.api_base}/repos/{self.repo_id}/contents/{quoted}?ref={ref}"
        status, body, _ = _http_request(
            url,
            method="GET",
            headers=self._headers(),
            timeout=60.0,
            token=self.token,
        )
        if status == 404:
            if allow_missing:
                return None
            raise HubError(f"GitHub에서 파일을 찾을 수 없습니다: {remote_path}")
        if status in (401, 403):
            raise HubError(
                f"GitHub 저장소({self.repo_id}) 접근 거부 (HTTP {status}): 비공개 저장소라면 유효한 토큰을 입력해 주세요."
            )
        if status >= 400:
            detail = body.decode("utf-8", errors="replace")[:200]
            raise HubError(f"GitHub 파일 조회 실패 ({remote_path}, HTTP {status}): {detail}")

        try:
            meta = json.loads(body.decode("utf-8"))
        except ValueError:
            return body
        if isinstance(meta, dict):
            if meta.get("content") and meta.get("encoding") == "base64":
                return base64.b64decode(meta["content"])
            dl_url = meta.get("download_url")
            if dl_url:
                d_status, d_body, _ = _http_request(str(dl_url), method="GET", timeout=120.0, token=self.token)
                if d_status < 400:
                    return d_body
            sha = meta.get("sha")
            if sha:
                b_status, b_body, _ = _http_request(
                    f"{self.api_base}/repos/{self.repo_id}/git/blobs/{sha}",
                    method="GET",
                    headers=self._headers(),
                    timeout=120.0,
                    token=self.token,
                )
                if b_status < 400:
                    blob_obj = json.loads(b_body.decode("utf-8"))
                    return base64.b64decode(blob_obj.get("content", ""))
        raise HubError(f"GitHub 응답에서 파일 본문을 읽을 수 없습니다: {remote_path}")

    def read_manifest(self) -> List[Dict[str, Any]]:
        raw = self._fetch_file_bytes(MANIFEST_FILE, allow_missing=True)
        if not raw:
            return []
        rows: List[Dict[str, Any]] = []
        for line in raw.decode("utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def _put_single_content(self, remote_path: str, content: bytes, message: str) -> None:
        quoted = self._quote_path(remote_path)
        ref = urllib.parse.quote(self.branch, safe="")
        get_url = f"{self.api_base}/repos/{self.repo_id}/contents/{quoted}?ref={ref}"
        g_status, g_body, _ = _http_request(get_url, method="GET", headers=self._headers(), token=self.token)
        existing_sha: Optional[str] = None
        if g_status == 200:
            try:
                existing_sha = json.loads(g_body.decode("utf-8")).get("sha")
            except Exception:
                existing_sha = None
        payload: Dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content).decode("ascii"),
            "branch": self.branch,
        }
        if existing_sha:
            payload["sha"] = existing_sha
        put_url = f"{self.api_base}/repos/{self.repo_id}/contents/{quoted}"
        p_status, p_body, _ = _http_request(
            put_url,
            method="PUT",
            headers={**self._headers(), "Content-Type": "application/json"},
            body=json.dumps(payload).encode("utf-8"),
            timeout=120.0,
            token=self.token,
        )
        if p_status >= 400:
            detail = p_body.decode("utf-8", errors="replace")[:240]
            raise HubError(f"GitHub 파일 업로드 실패 ({remote_path}, HTTP {p_status}): {detail}")

    def push_files(self, uploads: Sequence[Tuple[Path, str]], manifest_lines: Sequence[Dict[str, Any]],
                   message: str) -> int:
        self.ensure_repo()
        manifest_bytes = (
            "\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in manifest_lines) + "\n"
        ).encode("utf-8")

        all_items: List[Tuple[str, bytes]] = [(remote, Path(local).read_bytes()) for local, remote in uploads]
        all_items.append((MANIFEST_FILE, manifest_bytes))

        # Attempt atomic multi-file commit via Git Data API (ref -> commit -> blobs -> tree -> commit -> update ref)
        ref_url = f"{self.api_base}/repos/{self.repo_id}/git/ref/heads/{urllib.parse.quote(self.branch, safe='')}"
        r_status, r_body, _ = _http_request(ref_url, method="GET", headers=self._headers(), token=self.token)
        if r_status == 200:
            try:
                ref_obj = json.loads(r_body.decode("utf-8"))
                head_sha = ref_obj["object"]["sha"]
                c_status, c_body, _ = _http_request(
                    f"{self.api_base}/repos/{self.repo_id}/git/commits/{head_sha}",
                    method="GET",
                    headers=self._headers(),
                    token=self.token,
                )
                if c_status == 200:
                    base_tree_sha = json.loads(c_body.decode("utf-8"))["tree"]["sha"]
                    tree_entries: List[Dict[str, Any]] = []
                    for remote_path, data in all_items:
                        b_status, b_body, _ = _http_request(
                            f"{self.api_base}/repos/{self.repo_id}/git/blobs",
                            method="POST",
                            headers={**self._headers(), "Content-Type": "application/json"},
                            body=json.dumps({
                                "content": base64.b64encode(data).decode("ascii"),
                                "encoding": "base64",
                            }).encode("utf-8"),
                            timeout=120.0,
                            token=self.token,
                        )
                        if b_status >= 400:
                            raise HubError(f"GitHub blob 생성 실패 ({remote_path}, HTTP {b_status})")
                        blob_sha = json.loads(b_body.decode("utf-8"))["sha"]
                        tree_entries.append({
                            "path": remote_path.strip("/"),
                            "mode": "100644",
                            "type": "blob",
                            "sha": blob_sha,
                        })
                    t_status, t_body, _ = _http_request(
                        f"{self.api_base}/repos/{self.repo_id}/git/trees",
                        method="POST",
                        headers={**self._headers(), "Content-Type": "application/json"},
                        body=json.dumps({"base_tree": base_tree_sha, "tree": tree_entries}).encode("utf-8"),
                        timeout=60.0,
                        token=self.token,
                    )
                    if t_status < 400:
                        new_tree_sha = json.loads(t_body.decode("utf-8"))["sha"]
                        nc_status, nc_body, _ = _http_request(
                            f"{self.api_base}/repos/{self.repo_id}/git/commits",
                            method="POST",
                            headers={**self._headers(), "Content-Type": "application/json"},
                            body=json.dumps({
                                "message": message,
                                "tree": new_tree_sha,
                                "parents": [head_sha],
                            }).encode("utf-8"),
                            timeout=60.0,
                            token=self.token,
                        )
                        if nc_status < 400:
                            new_commit_sha = json.loads(nc_body.decode("utf-8"))["sha"]
                            u_status, u_body, _ = _http_request(
                                ref_url,
                                method="PATCH",
                                headers={**self._headers(), "Content-Type": "application/json"},
                                body=json.dumps({"sha": new_commit_sha, "force": False}).encode("utf-8"),
                                timeout=60.0,
                                token=self.token,
                            )
                            if u_status < 400:
                                return len(uploads)
            except HubError:
                raise
            except Exception:
                pass

        # Fallback: upload sequentially via Contents API (works even on an uninitialized/empty repo)
        for remote_path, data in all_items:
            self._put_single_content(remote_path, data, message)
        return len(uploads)

    def download_file(self, remote_path: str, dest: Path) -> Path:
        raw = self._fetch_file_bytes(remote_path, allow_missing=False)
        assert raw is not None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        return dest


def create_hub_client(
    *,
    provider: Optional[str] = None,
    repo: Optional[str] = None,
    token: Optional[str] = None,
    branch: Optional[str] = None,
    config_path: Path = HUB_CONFIG_PATH,
    secrets_path: Path = HUB_SECRETS_PATH,
) -> Tuple[RemoteHub, Dict[str, Any]]:
    """Resolve provider, repo, branch, and encrypted token, returning `(client, resolved_info)`."""
    cfg = load_hub_config(config_path)
    raw_provider = provider or cfg.get("provider") or DEFAULT_PROVIDER
    raw_repo = (repo if repo is not None else cfg.get("repo") or "").strip()
    raw_branch = (branch if branch is not None else cfg.get("branch") or "main").strip() or "main"

    resolved_provider, resolved_repo, parsed_branch = parse_repo_input(raw_repo, default_provider=raw_provider)
    if branch is None and ("@" in raw_repo or "/tree/" in raw_repo):
        raw_branch = parsed_branch

    resolved_token = (
        token.strip()
        if (token is not None and token.strip())
        else load_hub_token(resolved_provider, secrets_path=secrets_path, include_env=True)
    )
    client: RemoteHub
    if resolved_provider == "github":
        client = GitHubDatasetHub(resolved_repo, token=resolved_token, branch=raw_branch)
    else:
        client = HfDatasetHub(resolved_repo, token=resolved_token, branch=raw_branch)

    info = {
        "provider": resolved_provider,
        "repo": resolved_repo,
        "branch": raw_branch,
        "has_token": bool(resolved_token),
        "token_masked": mask_token(resolved_token),
    }
    return client, info


def test_hub_connection(
    *,
    provider: Optional[str] = None,
    repo: Optional[str] = None,
    token: Optional[str] = None,
    branch: Optional[str] = None,
    config_path: Path = HUB_CONFIG_PATH,
    secrets_path: Path = HUB_SECRETS_PATH,
) -> Dict[str, Any]:
    """Verify repository accessibility and return remote summary counts."""
    client, info = create_hub_client(
        provider=provider,
        repo=repo,
        token=token,
        branch=branch,
        config_path=config_path,
        secrets_path=secrets_path,
    )
    entries = client.read_manifest()
    summary = summarize_entries(entries)
    return {
        **info,
        "ok": True,
        "remote": summary,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    load_dotenv()
    cfg = load_hub_config()
    parser = argparse.ArgumentParser(
        description="MANUX team dataset & model hub (Hugging Face Datasets / GitHub)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--provider",
        default=cfg.get("provider", DEFAULT_PROVIDER),
        choices=PROVIDERS,
        help=f"Hub provider: 'huggingface' or 'github' (default: {cfg.get('provider', DEFAULT_PROVIDER)})",
    )
    common.add_argument(
        "--repo",
        default=cfg.get("repo", DEFAULT_REPO),
        help="Repository '<owner>/<name>' or full GitHub/Hugging Face URL",
    )
    common.add_argument(
        "--branch",
        default=cfg.get("branch", "main"),
        help="Target branch or revision (default: main)",
    )
    common.add_argument(
        "--token",
        default="",
        help="Access token (optional; defaults to encrypted local vault or HF_TOKEN / GITHUB_TOKEN)",
    )
    common.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Local recordings directory (default: ./recordings)",
    )

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

    cf = sub.add_parser("config", parents=[common], help="Save hub provider, repository address, and encrypted token")
    cf.add_argument("--clear-token", action="store_true", help="Remove stored access token from encrypted vault")

    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None, hub: Optional[RemoteHub] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    recordings_dir = args.dir or (ROOT / "recordings")
    try:
        if args.command == "config":
            if getattr(args, "clear_token", False):
                delete_hub_token(args.provider)
            saved = save_hub_config(
                provider=args.provider,
                repo=args.repo,
                branch=args.branch,
                token=args.token if args.token else None,
            )
            tok = load_hub_token(saved["provider"])
            print(
                json.dumps(
                    {
                        **saved,
                        "has_token": bool(tok),
                        "token_masked": mask_token(tok),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "status":
            valid_recs, errors = scan_local_recordings(recordings_dir)
            local_summary = summarize_entries([r.entry for r in valid_recs])
            out: Dict[str, Any] = {"recordings_dir": str(recordings_dir), "local": local_summary}
            if errors:
                out["local_errors"] = errors
            if not args.local and (hub is not None or args.repo):
                client = hub or create_hub_client(
                    provider=args.provider, repo=args.repo, token=args.token, branch=args.branch
                )[0]
                remote_entries = client.read_manifest()
                out["remote"] = summarize_entries(remote_entries)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0

        client = hub or create_hub_client(
            provider=args.provider, repo=args.repo, token=args.token, branch=args.branch
        )[0]
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
        print(f"error: {redact_secrets(str(exc), [getattr(args, 'token', '')])}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
