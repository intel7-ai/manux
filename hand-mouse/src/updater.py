"""
Automatic updates for the installed app (2026-11-04, Velopack + GitHub Releases).

`run_install_hooks()` must run first thing at startup: Velopack's installer and updater start the
app with special arguments and expect it to handle them and exit. `start_background_check()` then
looks for a newer release once, downloads it in the background and applies it after the app
exits, so the next start is the new version. The running app is never interrupted.

Nothing happens when running from source or from a build that Velopack did not install.
"""
from __future__ import annotations

import sys
import threading
from typing import Optional

from src import app_paths

REPO_URL = "https://github.com/intel7-ai/manux"

# What the last check found, for the UI: "", "checking", "current", "downloading",
# "ready" (applies after exit) or "error: ...".
status = ""
latest_version: Optional[str] = None


def run_install_hooks() -> None:
    if not app_paths.FROZEN:
        return
    try:
        import velopack
    except ImportError:
        return
    velopack.App().run()


def current_version() -> Optional[str]:
    if not app_paths.FROZEN:
        return None
    try:
        import velopack
        return velopack.UpdateManager(REPO_URL).get_current_version()
    except Exception:   # not installed by Velopack (a plain PyInstaller folder)
        return None


def _check() -> None:
    global status, latest_version
    try:
        import velopack
        manager = velopack.UpdateManager(velopack.GithubSource(REPO_URL))
    except Exception as exc:   # not installed by Velopack: nothing to update
        status = ""
        print(f"  Updates       : off ({exc})")
        return
    try:
        status = "checking"
        info = manager.check_for_updates()
        if info is None:
            status = "current"
            print(f"  Updates       : up to date ({manager.get_current_version()})")
            return
        latest_version = info.TargetFullRelease.Version
        status = "downloading"
        print(f"  Updates       : downloading {latest_version}")
        manager.download_updates(info)
        manager.wait_exit_then_apply_updates(info, silent=True, restart=False)
        status = "ready"
        print(f"  Updates       : {latest_version} installs when MANUX closes")
    except Exception as exc:   # offline, rate limited, ... : try again next start
        status = f"error: {exc}"
        print(f"[Warn] Update check failed: {exc}", file=sys.stderr)


def start_background_check() -> None:
    if not app_paths.FROZEN:
        return
    threading.Thread(target=_check, name="update-check", daemon=True).start()
