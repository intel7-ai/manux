"""Entry point of the installed MANUX app (PyInstaller builds MANUX.exe from this file).

Same app as `python -m manux`, with Velopack's install/update hooks first. The window build has no
console, so stdout/stderr go to <user data>/logs/app_latest.log instead of nowhere.

A preview build (package id MANUXPreview) keeps its settings in %APPDATA%/MANUXPreview/v04, apart from
the release app's %APPDATA%/MANUX/v04 and v0.3's %APPDATA%/MANUX/config.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _preview_data_dir() -> None:
    if os.environ.get("MANUX_DATA_DIR"):
        return
    from manux.platform.updater import read_build_info
    info = read_build_info(Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)))
    if info.pack_id and info.pack_id != "MANUX":
        appdata = Path(os.environ.get("APPDATA") or Path.home())
        os.environ["MANUX_DATA_DIR"] = str(appdata / info.pack_id / "v04")


def _log_to_file() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return
    from manux.settings.paths import resolve
    log_dir = resolve().log_dir
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = open(log_dir / "app_latest.log", "w", encoding="utf-8", buffering=1)
    except OSError:
        return
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream


def run() -> int:
    from manux.platform.updater import run_install_hooks
    run_install_hooks()          # must be first: the installer starts the app with hook arguments
    _preview_data_dir()
    _log_to_file()
    from manux.desktop import main
    return main()


if __name__ == "__main__":
    sys.exit(run())
