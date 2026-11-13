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
STEP_KINDS = ("key", "type", "wait", "open", "mouse", "youtube", "powershell", "system")
MOUSE_STEPS = ("left_click", "right_click", "double_click", "back", "forward", "tab_prev", "tab_next")
KIND_NAMES = {"key": "단축키", "type": "글자 입력", "wait": "기다리기", "open": "열기", "mouse": "마우스", "youtube": "유튜브 키",
              "powershell": "PowerShell", "system": "PC 기능"}

_cancel = threading.Event()
_lock = threading.Lock()
_running: Optional[threading.Thread] = None


class MacroError(ValueError):
    pass


def parse(value: Any) -> List[Dict[str, str]]:
    """Steps from JSON text or a list; raises MacroError (Korean) when anything is off."""
    from manux.compat.hand_mouse.action_bindings import MOUSE_ACTION_LABELS, YOUTUBE_KEY_PRESETS, parse_hotkey_keys, web_address
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
        elif kind in ("powershell", "system"):
            from manux.compat.hand_mouse.pc_commands import SYSTEM_ACTIONS, script_problem
            problem = script_problem(val) if kind == "powershell" else (None if val in SYSTEM_ACTIONS else "모르는 PC 기능")
            if problem:
                raise MacroError(f"{i}단계: {problem}")
        steps.append({"do": kind, "value": val})
    if total_wait > MAX_TOTAL_WAIT:
        raise MacroError(f"기다리기 합계는 {MAX_TOTAL_WAIT:g}초까지예요")
    _ = MOUSE_ACTION_LABELS
    return steps


def _known_key(name: str) -> bool:
    from manux.platform.custom_keys import known_key
    return known_key(name)



def describe(steps: Sequence[Dict[str, str]], short: bool = False) -> str:
    from manux.compat.hand_mouse.action_bindings import MOUSE_ACTION_LABELS, YOUTUBE_KEY_PRESETS, format_hotkey_badge
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
        elif kind in ("powershell", "system"):
            from manux.compat.hand_mouse.action_bindings import format_action_badge
            parts.append(format_action_badge("system_action" if kind == "system" else kind, val))
        else:
            parts.append(YOUTUBE_KEY_PRESETS.get(val, {}).get("badge", val))
    text = " → ".join(parts)
    return ("매크로 " + str(len(steps)) + "단계") if short and len(text) > 40 else ("매크로: " + text)


def risky(steps: Sequence[Dict[str, str]]) -> str:
    """Why this macro needs the user's own tick ("" when it does not)."""
    from manux.compat.hand_mouse.pc_commands import SYSTEM_ACTIONS
    if any(s["do"] == "powershell" or (s["do"] == "system" and SYSTEM_ACTIONS.get(s["value"], {}).get("warning")) for s in steps):
        return "PowerShell 또는 PC 전원·세션 명령을 실행합니다"
    if any(s["do"] == "open" and not s["value"].startswith(("http://", "https://")) for s in steps):
        return "프로그램을 실행합니다"
    if any(s["do"] == "key" and s["value"] in ("alt+f4", "ctrl+w", "ctrl+shift+w", "win+l") for s in steps):
        return "창이나 탭을 닫습니다"
    if any(s["do"] == "open" for s in steps):
        return "웹사이트를 엽니다"
    return ""



def start(value, dry_run=False, mouse_controller=None, wait=False):
    from manux.compat.hand_mouse import ports
    return ports.macro_start(value, dry_run=dry_run, mouse_controller=mouse_controller, wait=wait)

def cancel_all():
    from manux.compat.hand_mouse import ports
    return ports.macro_cancel()

def running():
    from manux.compat.hand_mouse import ports
    return ports.macro_running()
