"""Where v0.4 reads bundled files and writes the user's own (docs/contracts.md section 11).

User data never goes inside the repository:
- installed app (PyInstaller, `sys.frozen`): %APPDATA%/MANUX/v04
- run from source: %APPDATA%/MANUX-dev/v04
- `MANUX_DATA_DIR` overrides both (tests, portable runs)

v0.3 (`hand-mouse/`) keeps using %APPDATA%/MANUX (installed) so both versions can be installed
side by side; `v03_config_dir()` is where the v0.3 settings are read from for migration.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


@dataclass(frozen=True)
class AppPaths:
    app_dir: Path        # bundled read-only files (models, qml, default config)
    data_dir: Path       # the user's own files
    frozen: bool

    @property
    def config_dir(self) -> Path:
        return self.data_dir / "config"

    @property
    def profiles_dir(self) -> Path:
        return self.config_dir / "profiles"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"


def _appdata(env: Mapping[str, str]) -> Path:
    return Path(env.get("APPDATA") or Path.home())


def resolve(env: Optional[Mapping[str, str]] = None, frozen: Optional[bool] = None,
            app_dir: Optional[Path] = None) -> AppPaths:
    env = os.environ if env is None else env
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if app_dir is None:
        app_dir = Path(getattr(sys, "_MEIPASS", "")) if frozen else Path(__file__).resolve().parents[3]
    override = env.get("MANUX_DATA_DIR")
    if override:
        data_dir = Path(override)
    else:
        data_dir = _appdata(env) / ("MANUX" if frozen else "MANUX-dev") / "v04"
    return AppPaths(app_dir=Path(app_dir), data_dir=data_dir, frozen=frozen)


def v03_config_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    """The installed v0.3 app's config folder (gestures.json, profiles/, active_profile.txt)."""
    env = os.environ if env is None else env
    return _appdata(env) / "MANUX" / "config"
