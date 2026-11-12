"""Entry point of the installed MANUX app (PyInstaller builds MANUX.exe from this file).

Same app as `python -m manux`, with Velopack's install/update hooks first. The window build has no
console, so stdout/stderr go to <user data>/logs/app_latest.log instead of nowhere.

A preview build (package id MANUXPreview) keeps its settings in %APPDATA%/MANUXPreview/v04, apart from
the release app's %APPDATA%/MANUX/v04 and v0.3's %APPDATA%/MANUX/config.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path, PureWindowsPath


def _data_profile_path(appdata: Path, value: object) -> Path:
    """Resolve a bundled profile below APPDATA, never an absolute or parent path."""
    if not isinstance(value, str) or not value or len(value) > 240:
        raise ValueError("Invalid bundled data profile")
    relative = PureWindowsPath(value)
    parts = value.replace("\\", "/").split("/")
    if (relative.drive or relative.root or any(part in ("", ".", "..") for part in parts)
            or any(character in '<>:"|?*' or ord(character) < 32 for character in value)
            or any(part.endswith((".", " ")) for part in parts)):
        raise ValueError("Invalid bundled data profile")
    base = appdata.resolve()
    selected = base.joinpath(*relative.parts).resolve()
    if not selected.is_relative_to(base):
        raise ValueError("Bundled data profile must stay below APPDATA")
    return selected


def _preview_data_dir() -> None:
    if os.environ.get("MANUX_DATA_DIR"):
        return
    from manux.platform.updater import read_build_info
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    appdata = Path(os.environ.get("APPDATA") or Path.home())
    try:
        raw = json.loads((bundled / "build_info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if isinstance(raw, dict) and "data_profile" in raw:
        os.environ["MANUX_DATA_DIR"] = str(_data_profile_path(appdata, raw["data_profile"]))
        return
    info = read_build_info(bundled)
    if info.pack_id and info.pack_id != "MANUX":
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
    if "--bundle-check" in sys.argv:
        import argparse
        from bundle_check import run as bundle_check
        parser = argparse.ArgumentParser(description="Offline MANUX package validation")
        parser.add_argument("--bundle-check", type=Path, required=True)
        return bundle_check(parser.parse_args().bundle_check)
    _log_to_file()
    # Screenshot smoke checks must retain the stuck thread when a runner times out.
    # Normal interactive launches have no watchdog or time limit.
    import faulthandler
    diagnostic = "--screenshot" in sys.argv
    if diagnostic:
        faulthandler.dump_traceback_later(60, file=sys.stderr)
    try:
        from manux.desktop import main
        return main()
    finally:
        if diagnostic:
            faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    sys.exit(run())
