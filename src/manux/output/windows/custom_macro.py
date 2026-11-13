"""Original a700452 macro execution, isolated at the Windows output boundary."""
from __future__ import annotations
import threading, time
from typing import Any, Dict, List, Tuple
from manux.compat.hand_mouse.macro import parse, describe, MacroError
_cancel = threading.Event()
_lock = threading.Lock()
_running = None

def cancel_all() -> None:
    _cancel.set()


def running() -> bool:
    return _running is not None and _running.is_alive()


def _paste(text: str) -> bool:
    """Korean (or any non-ASCII) text: through the Windows clipboard and Ctrl+V."""
    import ctypes
    from ctypes import wintypes
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    # every handle needs its argtypes: without them ctypes passes a Python int as a 32-bit C int, and a 64-bit
    # HGLOBAL above 4 GB is cut off (GlobalLock then returns NULL and the copy crashes)
    k32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    k32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
    u32.OpenClipboard.argtypes = (wintypes.HWND,)
    u32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    u32.SetClipboardData.restype = wintypes.HANDLE
    data = text.encode("utf-16-le") + b"\0\0"
    if not u32.OpenClipboard(None):
        return False
    try:
        u32.EmptyClipboard()
        h = k32.GlobalAlloc(0x0042, len(data))
        p = k32.GlobalLock(h) if h else None
        if not p:
            if h:
                k32.GlobalFree(h)
            return False
        ctypes.memmove(p, data, len(data))
        k32.GlobalUnlock(h)
        if not u32.SetClipboardData(13, h):      # CF_UNICODETEXT; on failure the memory is still ours
            k32.GlobalFree(h)
            return False
    finally:
        u32.CloseClipboard()
    import pyautogui
    pyautogui.hotkey("ctrl", "v", _pause=False)
    return True


def _run(steps: List[Dict[str, str]], dry_run: bool, mouse_controller, log: List[str]) -> None:
    from manux.compat.hand_mouse.action_bindings import get_action_registry
    reg = get_action_registry()
    for s in steps:
        if _cancel.is_set():
            log.append("cancelled")
            return
        kind, val = s["do"], s["value"]
        log.append(f"{kind}:{val}")
        if kind == "wait":
            end = time.monotonic() + float(val)
            while time.monotonic() < end:
                if _cancel.wait(min(.05, max(0.0, end - time.monotonic()))):
                    log.append("cancelled")
                    return
            continue
        if dry_run:
            continue
        if kind in ("powershell", "system"):
            from manux.compat.hand_mouse.pc_commands import SYSTEM_ACTIONS
            from manux.output.windows.custom_commands import runner
            spec = SYSTEM_ACTIONS[val] if kind == "system" else dict(kind="powershell", value=val, label="PowerShell")
            if spec["kind"] == "powershell":
                ok, message = runner.start(spec["value"], label=spec["label"], wait=True, cancel_event=_cancel)
            else:
                ok, message = reg.execute_resolved_action({"action_type": spec["kind"], "action_value": spec["value"]},
                                                         mouse_controller=mouse_controller)
            if not ok:
                log.append("failed:" + message)
                return
            continue
        if kind == "type":
            import pyautogui
            if val.isascii():
                pyautogui.write(val, interval=.01, _pause=False)
            else:
                _paste(val)
            continue
        atype = {"key": "hotkey", "open": "launch_app", "mouse": "mouse_action", "youtube": "youtube_key"}[kind]
        reg.execute_resolved_action({"action_type": atype, "action_value": val}, dry_run=False,
                                    mouse_controller=mouse_controller)
        time.sleep(.05)                  # let the window react before the next key


def start(value: Any, dry_run: bool = False, mouse_controller=None, wait: bool = False) -> Tuple[bool, str, List[str]]:
    """Run a macro in the background (one at a time). (started, message, step log - filled as it runs)."""
    global _running
    try:
        steps = parse(value)
    except MacroError as exc:
        return False, str(exc), []
    with _lock:
        if running():
            return False, "앞의 매크로가 아직 실행 중이에요", []
        _cancel.clear()
        log: List[str] = []
        _running = threading.Thread(target=_run, args=(steps, dry_run, mouse_controller, log), daemon=True)
        _running.start()
    if wait:
        _running.join()
    return True, describe(steps, short=True), log
