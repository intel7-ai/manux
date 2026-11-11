"""Macros: one gesture, several commands in order (2026-11-10).

A binding with action_type "macro" keeps its steps as JSON in action_value:
    [{"do": "open", "value": "https://youtube.com"}, {"do": "wait", "value": "2"}, {"do": "key", "value": "f"}]
Steps: key (a hotkey like ctrl+shift+t), type (text; Korean goes through the clipboard), wait (seconds), open (a
program or a web address), mouse (clicks and back/forward/tab moves only), youtube (a YouTube/media key preset).

Runs in its own thread so a wait never stops the camera loop. One macro at a time; Pause (the emergency stop) and
the drawing/motion mode switches cancel it between steps (cancel_all).
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

MAX_STEPS = 10
MAX_WAIT = 10.0          # s per wait step
MAX_TOTAL_WAIT = 30.0
MAX_TEXT = 200
STEP_KINDS = ("key", "type", "wait", "open", "mouse", "youtube")
MOUSE_STEPS = ("left_click", "right_click", "double_click", "back", "forward", "tab_prev", "tab_next")
KIND_NAMES = {"key": "단축키", "type": "글자 입력", "wait": "기다리기", "open": "열기", "mouse": "마우스", "youtube": "유튜브 키"}

_cancel = threading.Event()
_lock = threading.Lock()
_running: Optional[threading.Thread] = None


class MacroError(ValueError):
    pass


def parse(value: Any) -> List[Dict[str, str]]:
    """Steps from JSON text or a list; raises MacroError (Korean) when anything is off."""
    from src.action_bindings import MOUSE_ACTION_LABELS, YOUTUBE_KEY_PRESETS, parse_hotkey_keys, web_address
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise MacroError("매크로 형식이 잘못됐어요") from exc
    if not isinstance(value, list) or not value:
        raise MacroError("매크로 단계가 비어 있어요")
    if len(value) > MAX_STEPS:
        raise MacroError(f"매크로는 {MAX_STEPS}단계까지예요")
    steps, total_wait = [], 0.0
    for i, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            raise MacroError(f"{i}단계 형식이 잘못됐어요")
        kind, val = str(raw.get("do", "")).strip(), str(raw.get("value", "")).strip()
        if kind not in STEP_KINDS:
            raise MacroError(f"{i}단계: 모르는 단계 '{kind}'")
        if kind == "key":
            keys = parse_hotkey_keys(val)
            if not keys or not all(_known_key(k) for k in keys):
                raise MacroError(f"{i}단계: 모르는 단축키 '{val}'")
            val = "+".join(keys)
        elif kind == "type":
            if not val or len(val) > MAX_TEXT:
                raise MacroError(f"{i}단계: 입력할 글자는 1~{MAX_TEXT}자")
        elif kind == "wait":
            try:
                sec = float(val)
            except ValueError as exc:
                raise MacroError(f"{i}단계: 기다릴 초가 숫자가 아니에요") from exc
            if not 0 < sec <= MAX_WAIT:
                raise MacroError(f"{i}단계: 기다리기는 0~{MAX_WAIT:g}초")
            total_wait += sec
            val = f"{sec:g}"
        elif kind == "open":
            if not val:
                raise MacroError(f"{i}단계: 열 것이 비어 있어요")
            val = web_address(val)
        elif kind == "mouse":
            if val not in MOUSE_STEPS:
                raise MacroError(f"{i}단계: 매크로에서 쓸 수 있는 마우스 동작이 아니에요 ({val})")
        elif kind == "youtube" and val not in YOUTUBE_KEY_PRESETS:
            raise MacroError(f"{i}단계: 모르는 유튜브 키 '{val}'")
        steps.append({"do": kind, "value": val})
    if total_wait > MAX_TOTAL_WAIT:
        raise MacroError(f"기다리기 합계는 {MAX_TOTAL_WAIT:g}초까지예요")
    _ = MOUSE_ACTION_LABELS
    return steps


def _known_key(name: str) -> bool:
    try:
        import pyautogui
        return name in pyautogui.KEYBOARD_KEYS
    except Exception:
        return name.isascii() and bool(name)


def describe(steps: Sequence[Dict[str, str]], short: bool = False) -> str:
    from src.action_bindings import MOUSE_ACTION_LABELS, YOUTUBE_KEY_PRESETS, format_hotkey_badge
    parts = []
    for s in steps:
        kind, val = s["do"], s["value"]
        if kind == "key":
            parts.append(format_hotkey_badge(val))
        elif kind == "type":
            parts.append(f"'{val[:12]}{'…' if len(val) > 12 else ''}' 입력")
        elif kind == "wait":
            parts.append(f"{val}초 기다림")
        elif kind == "open":
            parts.append(f"{val} 열기")
        elif kind == "mouse":
            parts.append(MOUSE_ACTION_LABELS.get(val, {}).get("badge", val))
        else:
            parts.append(YOUTUBE_KEY_PRESETS.get(val, {}).get("badge", val))
    text = " → ".join(parts)
    return ("매크로 " + str(len(steps)) + "단계") if short and len(text) > 40 else ("매크로: " + text)


def risky(steps: Sequence[Dict[str, str]]) -> str:
    """Why this macro needs the user's own tick ("" when it does not)."""
    if any(s["do"] == "open" and not s["value"].startswith(("http://", "https://")) for s in steps):
        return "프로그램을 실행합니다"
    if any(s["do"] == "key" and s["value"] in ("alt+f4", "ctrl+w", "ctrl+shift+w", "win+l") for s in steps):
        return "창이나 탭을 닫습니다"
    if any(s["do"] == "open" for s in steps):
        return "웹사이트를 엽니다"
    return ""


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
    from src.action_bindings import get_action_registry
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
