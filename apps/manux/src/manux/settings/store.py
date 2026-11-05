"""Settings file: load, validate, overlay a profile, save atomically (docs/contracts.md section 11).

File: `<config dir>/settings.json` = {"version": 1, "values": {key: value}}.
- Unknown keys are kept as they are (a newer feature or a v0.3 key a feature has not claimed yet).
- A known key with a bad value falls back to its default and adds a warning. Loading never raises.
- Saving writes a temporary file and renames it; a failed save leaves the old file untouched.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from manux.settings.schema import Schema

VERSION = 1
FILE_NAME = "settings.json"


class Settings:
    def __init__(self, schema: Schema, values: Optional[Mapping[str, Any]] = None) -> None:
        self.schema = schema
        self._stored: Dict[str, Any] = {}       # what the file holds (validated where known)
        self._overlay: Dict[str, Any] = {}      # the active profile's values (not saved here)
        self.warnings: List[str] = []
        for key, value in (values or {}).items():
            self._put(key, value, self._stored)

    def _put(self, key: str, value: Any, into: Dict[str, Any]) -> None:
        setting = self.schema.get(key)
        if setting is None:
            into[key] = value
            return
        checked, warning = setting.coerce(value)
        if warning:
            self.warnings.append(warning)
            into.pop(key, None)   # the default applies
        else:
            into[key] = checked

    def get(self, key: str) -> Any:
        if key in self._overlay:
            return self._overlay[key]
        if key in self._stored:
            return self._stored[key]
        setting = self.schema.get(key)
        if setting is None:
            raise KeyError(f"unknown setting {key!r}")
        return setting.default

    def set(self, key: str, value: Any) -> bool:
        """Store a user value. False (and a warning) when the value is invalid; unknown keys are refused."""
        setting = self.schema.get(key)
        if setting is None:
            self.warnings.append(f"{key}: unknown setting; not saved")
            return False
        checked, warning = setting.coerce(value)
        if warning:
            self.warnings.append(warning)
            return False
        self._stored[key] = checked
        return True

    def section(self, owner: str) -> Dict[str, Any]:
        """Resolved values of `<owner>.*` with the prefix removed (what a feature gets in FrameContext)."""
        prefix = owner + "."
        keys = {k for k in self.schema.keys() if k.startswith(prefix)}
        keys |= {k for k in list(self._stored) + list(self._overlay) if k.startswith(prefix) and k in self.schema}
        return {k[len(prefix):]: self.get(k) for k in sorted(keys)}

    def apply_profile(self, values: Optional[Mapping[str, Any]]) -> None:
        """Overlay a profile's values (None = no profile). Unknown keys in a profile are ignored."""
        self._overlay = {}
        for key, value in (values or {}).items():
            if key in self.schema:
                self._put(key, value, self._overlay)

    def stored(self) -> Dict[str, Any]:
        return dict(self._stored)


def load(schema: Schema, config_dir: Path) -> Settings:
    path = Path(config_dir) / FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Settings(schema)
    except (OSError, ValueError) as exc:
        settings = Settings(schema)
        settings.warnings.append(f"{path.name}: unreadable ({exc}); using defaults")
        return settings
    values = data.get("values") if isinstance(data, dict) else None
    if not isinstance(values, dict):
        settings = Settings(schema)
        settings.warnings.append(f"{path.name}: no 'values' object; using defaults")
        return settings
    settings = Settings(schema, values)
    if data.get("version") != VERSION:
        settings.warnings.append(f"{path.name}: version {data.get('version')!r}, expected {VERSION}")
    return settings


def save(settings: Settings, config_dir: Path) -> Path:
    path = Path(config_dir) / FILE_NAME
    write_json_atomic(path, {"version": VERSION, "values": settings.stored()})
    return path


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
