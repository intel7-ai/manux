"""Windows admin ("elevated") windows, e.g. Task Manager (docs/far-hand-loss.md, "작업관리자").

A normal program cannot move the cursor, click or type into a window that belongs to an elevated program (UIPI): the input
is silently dropped, so with Task Manager in front the whole control seems frozen. Only an app that is itself started as
administrator can control such windows. This module tells whether that is the case and restarts the app as administrator.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION_CLASS = 20
ERROR_ACCESS_DENIED = 5


class _TokenElevation(ctypes.Structure):
    _fields_ = [("TokenIsElevated", wintypes.DWORD)]


def is_elevated() -> bool:
    """This app runs as administrator."""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def foreground_is_elevated() -> Optional[bool]:
    """True when the window in front belongs to an elevated program, False when it does not, None when unknown."""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            # a normal program is refused access to the query of a higher-integrity one
            return True if ctypes.get_last_error() == ERROR_ACCESS_DENIED else None
        try:
            token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
                return True if ctypes.get_last_error() == ERROR_ACCESS_DENIED else None
            try:
                info = _TokenElevation()
                size = wintypes.DWORD()
                if not advapi32.GetTokenInformation(token, TOKEN_ELEVATION_CLASS, ctypes.byref(info),
                                                    ctypes.sizeof(info), ctypes.byref(size)):
                    return None
                return bool(info.TokenIsElevated)
            finally:
                kernel32.CloseHandle(token)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def blocked_by_elevated_window() -> bool:
    """The window in front is an admin window and this app is not: its clicks and cursor moves do nothing there."""
    return (not is_elevated()) and foreground_is_elevated() is True


def relaunch_script(python: str, script: str, args: Sequence[str], cwd: str, wait_seconds: int = 4) -> str:
    """Text of a .bat file that waits for this app to close (the camera must be free), then starts it again."""
    command = subprocess.list2cmdline([python, script, *args])
    return "\r\n".join([
        "@echo off",
        f"ping -n {int(wait_seconds) + 1} 127.0.0.1 >nul",
        f'cd /d "{cwd}"',
        command,
        "",
    ])


def relaunch_as_admin() -> bool:
    """Ask Windows for administrator rights (the UAC prompt) and start this app again; the caller closes this one.
    False when nothing was started (refused, not Windows)."""
    if sys.platform != "win32":
        return False
    try:
        script = str((ROOT / (sys.argv[0] or "main.py")).resolve()) if not os.path.isabs(sys.argv[0] or "") else sys.argv[0]
        text = relaunch_script(sys.executable, script, sys.argv[1:], str(ROOT))
        bat = Path(tempfile.gettempdir()) / "manux_restart_admin.bat"
        bat.write_bytes(text.encode("mbcs", errors="replace"))
        # > 32 means started; the user can still answer "No" to the UAC prompt (then it returns 5)
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(bat), None, str(ROOT), 1)
        return int(result) > 32
    except Exception:
        return False
