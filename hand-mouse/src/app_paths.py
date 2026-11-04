"""
Where the app reads its bundled files and where it writes the user's own (2026-11-04, packaging).

Run from source, both are the hand-mouse folder, as before. In the installed app (PyInstaller,
`sys.frozen`) the program folder is replaced by every update, so the user's config, calibration
profiles, logs and certificates live in %APPDATA%\\MANUX instead and survive updates.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(__file__).resolve().parents[1]          # bundled: models/, config/gestures.json, src/*.qml
DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "MANUX" if FROZEN else APP_DIR
CONFIG_DIR = DATA_DIR / "config"
LOG_DIR = DATA_DIR / "logs"
CERT_DIR = DATA_DIR / "certs" if FROZEN else APP_DIR / "models" / "certs"

BUNDLED_CONFIG = APP_DIR / "config" / "gestures.json"
_STAMP = ".bundled-gestures.sha256"   # hash of the bundled gestures.json last copied to the user


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def user_config_path() -> Path:
    """The gestures.json the app reads and saves.

    Installed: the bundled file is copied to the user's folder on first run, and again after an
    update brings a new one, unless the user changed their copy since (then theirs is kept)."""
    if not FROZEN:
        return BUNDLED_CONFIG
    target = CONFIG_DIR / "gestures.json"
    stamp = CONFIG_DIR / _STAMP
    try:
        bundled = _sha(BUNDLED_CONFIG)
        untouched = target.exists() and stamp.exists() and _sha(target) == stamp.read_text().strip()
        if not target.exists() or (untouched and bundled != stamp.read_text().strip()):
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(BUNDLED_CONFIG, target)
            stamp.write_text(bundled)
    except OSError as exc:
        print(f"[Warn] Could not prepare {target}: {exc}; using the bundled settings read-only.",
              file=sys.stderr)
        return BUNDLED_CONFIG
    return target
