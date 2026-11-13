"""Foreground identity for locally evaluated app-specific connections; never sent to AI."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path


def identity():
    if os.name != "nt":
        return {}
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    window = user32.GetForegroundWindow()
    title = ctypes.create_unicode_buffer(1024)
    user32.GetWindowTextW(window, title, len(title))
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    handle = kernel.OpenProcess(0x1000, False, pid.value)
    process = ""
    if handle:
        try:
            path = ctypes.create_unicode_buffer(4096)
            size = wintypes.DWORD(len(path))
            if kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                process = Path(path.value).name
        finally:
            kernel.CloseHandle(handle)
    return {"process": process, "title_full": title.value}
