"""User profiles: measured values per person, overlaid on the settings (port of hand-mouse/src/profiles.py).

<config dir>/profiles/<name>.json   {"name", "created", "values": {key: value}, "report": {...}}
<config dir>/active_profile.txt     the profile in use (absent / empty = none)

Differences from v0.3: values use v0.4 keys (`<owner>.<name>`), the file key is "values"
(v0.3 used "updates"; both are read), and there is no process-wide override: callers pass the name.
A profile name can be a person's name, so profiles live only in the user's data folder.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from manux.settings.store import write_json_atomic

NAME_RE = re.compile(r"^[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ _\-.]{1,32}$")
ACTIVE_FILE = "active_profile.txt"
PROFILES_DIR = "profiles"


def valid_name(name: str) -> bool:
    name = (name or "").strip()
    return bool(NAME_RE.match(name)) and name not in (".", "..") and not name.endswith(".")


def profile_path(config_dir: Path, name: str) -> Path:
    if not valid_name(name):
        raise ValueError(f"사용할 수 없는 프로필 이름입니다: {name!r} (한글·영문·숫자·공백·-_. 32자 이하)")
    return Path(config_dir) / PROFILES_DIR / f"{name.strip()}.json"


def list_profiles(config_dir: Path) -> List[str]:
    try:
        return sorted(p.stem for p in (Path(config_dir) / PROFILES_DIR).glob("*.json") if valid_name(p.stem))
    except OSError:
        return []


def save_profile(config_dir: Path, name: str, values: Dict[str, Any],
                 report: Optional[Dict[str, Any]] = None) -> Path:
    """Write (or replace) a profile; a replaced one is kept as `<name>.json.bak`."""
    path = profile_path(config_dir, name)
    if path.exists():
        path.with_name(path.name + ".bak").write_bytes(path.read_bytes())
    write_json_atomic(path, {"name": name.strip(), "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                             "values": dict(values), "report": report or {}})
    return path


def load_profile(config_dir: Path, name: str) -> Optional[Dict[str, Any]]:
    """The profile's values, or None when it is missing or unreadable. Validation happens in Settings."""
    try:
        data = json.loads(profile_path(config_dir, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw = data.get("values", data.get("updates")) if isinstance(data, dict) else None
    return dict(raw) if isinstance(raw, dict) else None


def delete_profile(config_dir: Path, name: str) -> bool:
    path = profile_path(config_dir, name)
    if not path.exists():
        return False
    path.unlink()
    if get_active(config_dir) == name.strip():
        set_active(config_dir, None)
    return True


def get_active(config_dir: Path) -> Optional[str]:
    try:
        name = (Path(config_dir) / ACTIVE_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name if valid_name(name) else None


def set_active(config_dir: Path, name: Optional[str]) -> None:
    path = Path(config_dir) / ACTIVE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if name:
        profile_path(config_dir, name)   # validates the name
        path.write_text(name.strip(), encoding="utf-8")
    else:
        path.write_text("", encoding="utf-8")
