"""
User profiles (2026-11-04): the values the calibration wizard measured, stored per person.

config/profiles/<name>.json   {"name", "created", "updates": {key: number}, "report": {...}}
config/active_profile.txt     name of the profile in use (absent / empty = none)

A profile only OVERLAYS the base config (defaults < config/gestures.json < profile). Selecting
another profile or none never edits gestures.json. Profiles hold measured numbers only (no camera
images); the folder is git-ignored because a profile name can be a person's name.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Process-wide choice for this run (set by `main.py --profile`): None = use active_profile.txt,
# "" = no profile at all, "name" = that profile.
RUN_OVERRIDE: Optional[str] = None

NAME_RE = re.compile(r"^[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ _\-.]{1,32}$")
ACTIVE_FILE = "active_profile.txt"
PROFILES_DIR = "profiles"


def valid_name(name: str) -> bool:
    name = (name or "").strip()
    return bool(NAME_RE.match(name)) and name not in (".", "..") and not name.endswith(".")


def profiles_dir(config_dir: Path) -> Path:
    return Path(config_dir) / PROFILES_DIR


def profile_path(config_dir: Path, name: str) -> Path:
    if not valid_name(name):
        raise ValueError(f"사용할 수 없는 프로필 이름입니다: {name!r} (한글·영문·숫자·공백·-_. 32자 이하)")
    return profiles_dir(config_dir) / f"{name.strip()}.json"


def list_profiles(config_dir: Path) -> List[str]:
    try:
        return sorted(p.stem for p in profiles_dir(config_dir).glob("*.json") if valid_name(p.stem))
    except OSError:
        return []


def save_profile(config_dir: Path, name: str, updates: Dict[str, float],
                 report: Optional[Dict[str, Any]] = None) -> Path:
    """Write (or replace) a profile; a replaced one is kept as `<name>.json.bak`."""
    path = profile_path(config_dir, name)
    clean = {str(k): float(v) for k, v in updates.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_name(path.name + ".bak").write_bytes(path.read_bytes())
    payload = {"name": name.strip(), "created": time.strftime("%Y-%m-%d %H:%M:%S"),
               "updates": clean, "report": report or {}}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_profile(config_dir: Path, name: str, known_keys: Optional[set] = None) -> Optional[Dict[str, float]]:
    """The numeric overrides of a profile, or None when it is missing / unreadable.
    With `known_keys`, unknown keys are dropped (a hand-edited file cannot inject arbitrary config)."""
    try:
        data = json.loads(profile_path(config_dir, name).read_text(encoding="utf-8"))
        raw = data.get("updates", {})
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(raw, dict):
        return None
    return {k: float(v) for k, v in raw.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and (known_keys is None or k in known_keys)}


def delete_profile(config_dir: Path, name: str) -> bool:
    path = profile_path(config_dir, name)
    if not path.exists():
        return False
    path.unlink()
    if get_active(config_dir, ignore_override=True) == name.strip():
        set_active(config_dir, None)
    return True


def get_active(config_dir: Path, ignore_override: bool = False) -> Optional[str]:
    """The profile in use: this run's override, else active_profile.txt. None = no profile."""
    if RUN_OVERRIDE is not None and not ignore_override:
        return RUN_OVERRIDE or None
    try:
        name = (Path(config_dir) / ACTIVE_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name if valid_name(name) else None


def set_active(config_dir: Path, name: Optional[str]) -> None:
    """Remember the chosen profile (None / "" = no profile)."""
    path = Path(config_dir) / ACTIVE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if name:
        profile_path(config_dir, name)   # validates the name
        path.write_text(name.strip(), encoding="utf-8")
    else:
        path.write_text("", encoding="utf-8")
