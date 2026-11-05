"""Automatic updates for the installed app (S6; port of hand-mouse/src/updater.py, Velopack + GitHub Releases).

`run_install_hooks()` must run first thing in the installed app: Velopack's installer and updater start
the app with special arguments and expect it to handle them and exit. `start_background_check()` looks
for a newer release once, downloads it in the background and applies it after the app exits, so the
next start is the new version. The running app is never interrupted.

What a build is comes from `build_info.json`, written next to the bundled files by
`packaging/build.ps1`: version, Velopack package id and channel, and whether it updates at all.
PR/preview builds (`MANUXPreview`) never update. Nothing happens when running from source.
"""
from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

REPO_URL = "https://github.com/intel7-ai/manux"
BUILD_INFO = "build_info.json"


@dataclass(frozen=True)
class BuildInfo:
    version: str = "0.4.0.dev0"
    pack_id: str = ""            # Velopack package id: "MANUX" (release) or "MANUXPreview" (PR builds)
    channel: str = ""
    updates: bool = False        # check GitHub Releases for this pack id / channel
    source_sha: str = ""

    @property
    def label(self) -> str:
        return self.version if self.pack_id in ("", "MANUX") else f"{self.version} ({self.pack_id})"


def read_build_info(app_dir: Path, default_version: str = "0.4.0.dev0") -> BuildInfo:
    """The bundled build_info.json, or a source-run default (no updates)."""
    try:
        raw: Mapping[str, Any] = json.loads((Path(app_dir) / BUILD_INFO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return BuildInfo(version=default_version)
    return BuildInfo(version=str(raw.get("version") or default_version), pack_id=str(raw.get("pack_id") or ""),
                     channel=str(raw.get("channel") or ""), updates=bool(raw.get("updates", False)),
                     source_sha=str(raw.get("source_sha") or ""))


class Updater:
    """One background check per start. `status` is for the UI and the logs:
    "" (off), "checking", "current", "downloading", "ready" (applies after exit) or "error: ...".
    """

    def __init__(self, info: BuildInfo, frozen: bool, *, log: Callable[[str], None] = print,
                 velopack: Any = None) -> None:
        self.info = info
        self.frozen = frozen
        self.status = ""
        self.latest_version: Optional[str] = None
        self._log = log
        self._velopack = velopack           # injected in tests; imported lazily otherwise

    @property
    def enabled(self) -> bool:
        return self.frozen and self.info.updates

    def _module(self):
        if self._velopack is None:
            import velopack
            self._velopack = velopack
        return self._velopack

    def check(self) -> None:
        if not self.enabled:
            self._log(f"Updates: off ({'source run' if not self.frozen else 'this build does not update'})")
            return
        try:
            vp = self._module()
            manager = vp.UpdateManager(vp.GithubSource(REPO_URL))
        except Exception as exc:            # not installed by Velopack (a plain PyInstaller folder)
            self.status = ""
            self._log(f"Updates: off ({exc})")
            return
        try:
            self.status = "checking"
            info = manager.check_for_updates()
            if info is None:
                self.status = "current"
                self._log(f"Updates: up to date ({self.info.version})")
                return
            self.latest_version = str(info.TargetFullRelease.Version)
            self.status = "downloading"
            self._log(f"Updates: downloading {self.latest_version}")
            manager.download_updates(info)
            manager.wait_exit_then_apply_updates(info, silent=True, restart=False)
            self.status = "ready"
            self._log(f"Updates: {self.latest_version} installs when MANUX closes")
        except Exception as exc:            # offline, rate limited ...: try again next start
            self.status = f"error: {exc}"
            self._log(f"Updates: check failed ({exc})")

    def start(self) -> Optional[threading.Thread]:
        if not self.enabled:
            return None
        thread = threading.Thread(target=self.check, name="update-check", daemon=True)
        thread.start()
        return thread


def run_install_hooks(frozen: Optional[bool] = None) -> None:
    """Velopack's install/update/uninstall hooks. Call before anything else in the installed app."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not frozen:
        return
    try:
        import velopack
    except ImportError:
        return
    velopack.App().run()
