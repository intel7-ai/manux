"""API keys saved by the app, encrypted for this Windows user (DPAPI, CryptProtectData).

The encrypted text goes into QSettings; only the same Windows account on the same PC can read it back. On other
systems nothing is saved (the key then comes from the environment or is typed again each run).
"""
from __future__ import annotations

import base64
import ctypes
import sys
from typing import Optional


def read_env_key(filename: str, env_name: str) -> str:
    """Read one explicitly linked env file, without evaluating or copying its contents."""
    if not filename or not env_name:
        return ""
    from pathlib import Path
    try:
        path = Path(filename)
        if path.stat().st_size > 65536:
            return ""
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.strip().removeprefix("export ").partition("=")
            if separator and name.strip() == env_name:
                return value.strip().strip("\"'")
    except (OSError, UnicodeError, ValueError):
        pass
    return ""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def available() -> bool:
    return sys.platform == "win32"


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob._buf = buf          # keep the buffer alive with the struct
    return blob


def _take(blob: _Blob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def protect(text: str) -> Optional[str]:
    """Encrypted, base64 text; None when this system cannot encrypt."""
    if not available() or not text:
        return None
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(_blob(text.encode("utf-8"))), "MANUX", None, None, None,
                                                0, ctypes.byref(out))
    return base64.b64encode(_take(out)).decode("ascii") if ok else None


def unprotect(token: str) -> str:
    """The key back, "" when it cannot be read (another user / PC, damaged)."""
    if not available() or not token:
        return ""
    try:
        raw = base64.b64decode(token)
    except (ValueError, TypeError):
        return ""
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(_blob(raw)), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        return ""
    try:
        return _take(out).decode("utf-8")
    except UnicodeDecodeError:
        return ""
