"""Connect gestures to commands by saying or typing it (2026-11-10).

"유튜브에서 엄지+약지면 자막 켜고, 파워포인트에선 다음 슬라이드" -> proposals on the gesture bindings
(src/action_bindings.py). The model only proposes: every proposal is checked here against the real catalogue (gesture
keys, command types, mouse actions, YouTube keys, key names), shown to the user, and only the ones the user ticks are
saved. Program launches (`launch_app` runs a command) are never ticked by default.

What is sent to the provider (src/llm_providers.py): the user's sentence, the gesture names and their current commands,
the command catalogue. Nothing from the camera or the screen.
"""
from __future__ import annotations

from manux.compat.hand_mouse.paths import DATA_ROOT

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from manux.compat.hand_mouse.action_bindings import (MOUSE_ACTION_LABELS, WINDOWS_HOTKEY_PRESETS, YOUTUBE_KEY_PRESETS, format_action_badge,
                                 parse_hotkey_keys)

ACTION_TYPES = ("hotkey", "youtube_key", "launch_app", "mouse_action", "macro", "system_action", "powershell")
TYPE_NAMES = {"hotkey": "단축키", "youtube_key": "유튜브·미디어 키", "launch_app": "프로그램·웹사이트 실행",
              "mouse_action": "마우스·시스템 동작", "macro": "여러 단계 (매크로)",
              "system_action": "PC·Windows 기능", "powershell": "PowerShell 명령"}
MAX_PROPOSALS = 20
NEW = "__new__"              # gesture key of a composite gesture the AI proposes to create (posture + movement)
from manux.compat.hand_mouse.sequence import POSTURES as SEQ_POSTURES  # noqa: E402

# How long a hand shape must be held before its command runs (2026-11-11, user: "the shortest by default, longer only
# if I want"). A new hold gesture starts at its minimum; the user can raise it (up to HOLD_MAX) in the AI list.
HOLD_MIN = .4           # the user's upside-down V ran well at 0.4 s; below that a passing shape fires
HOLD_MIN_EVERYDAY = 1.0  # shapes the hand is in during normal use (open hand, fist, thumb tucked): shorter fires all day
EVERYDAY_POSTURES = ("open_hand", "open_hand_back", "open_hand_down", "open_hand_up", "fist", "thumb_tucked")
HOLD_MAX = 3.0
HOLD_SECONDS = HOLD_MIN  # kept for older callers


def hold_minimum(posture: str) -> float:
    return HOLD_MIN_EVERYDAY if posture in EVERYDAY_POSTURES else HOLD_MIN
CLICK_POSTURES = {"thumb_index", "thumb_middle", "thumb_ring", "thumb_pinky", "thumb_index_middle"}


def _postures() -> List[str]:
    from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
    return [k for k in POSTURE_NAMES_KO if k != "any"]


def _triggers() -> List[str]:
    """Composite movements (posture held + swipe / hold / double pinch) and time-series motions ("motion_<direction>":
    one sweep of the hand in motion mode, src/motion_mode.py custom motions)."""
    from manux.compat.hand_mouse.action_bindings import DIRECTION_NAMES_KO, TRIGGER_NAMES_KO
    return list(TRIGGER_NAMES_KO) + ["motion_" + d for d in DIRECTION_NAMES_KO] + _draw_shapes() + ["rotate_cw", "rotate_ccw"]


def _draw_shapes() -> List[str]:
    """Drawing-mode shapes, usable as "hold a hand shape and draw this" (src/path_gesture.py)."""
    try:
        from manux.compat.hand_mouse.draw_mode import ShapeStore
        return [s["key"] for s in ShapeStore().shapes() if s["enabled"]]
    except Exception:
        return []


def trigger_name(trigger: str) -> str:
    from manux.compat.hand_mouse.action_bindings import DIRECTION_NAMES_KO, TRIGGER_NAMES_KO
    if trigger.startswith("draw_") or trigger.startswith("rotate_"):
        from manux.compat.hand_mouse.path_gesture import shape_name
        return shape_name(trigger)
    if trigger.startswith("motion_"):
        return "모션: " + DIRECTION_NAMES_KO.get(trigger[7:], trigger[7:])
    return TRIGGER_NAMES_KO.get(trigger, trigger)


def schema(gesture_keys: Sequence[str]) -> Dict[str, Any]:
    """The answer's shape (Claude: enforced by structured outputs; other providers: checked in `check`)."""
    from manux.compat.hand_mouse.macro import STEP_KINDS
    keys = list(dict.fromkeys(list(gesture_keys) + [NEW]))
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One or two Korean sentences: what will change."},
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "gesture_key": {"type": "string", "enum": keys},
                        "new_name": {"type": "string", "description": "only for __new__: a short Korean name"},
                        "new_posture": {"type": "string", "enum": [""] + _postures()},
                        "new_trigger": {"type": "string", "enum": [""] + _triggers()},
                        "new_sequence": {"type": "array", "description": "only for a new sequence: 2-3 hand shapes in order",
                                         "items": {"type": "string", "enum": list(SEQ_POSTURES)}},
                        "app_match": {"type": "string",
                                      "description": "\"\" = every program; else a process name or window-title word"},
                        "action_type": {"type": "string", "enum": list(ACTION_TYPES)},
                        "action_value": {"type": "string", "description": "\"\" for macro (the steps say it)"},
                        "steps": {
                            "type": "array",
                            "description": "only for macro: the commands in order",
                            "items": {
                                "type": "object",
                                "properties": {"do": {"type": "string", "enum": list(STEP_KINDS)}, "value": {"type": "string"}},
                                "required": ["do", "value"],
                                "additionalProperties": False,
                            },
                        },
                        "enabled": {"type": "boolean"},
                        "motion_only": {"type": "boolean",
                                        "description": "only for __new__: true = the new gesture counts only in motion mode"},
                        "reason": {"type": "string", "description": "Short Korean reason"},
                    },
                    "required": ["gesture_key", "new_name", "new_posture", "new_trigger", "new_sequence", "app_match",
                                 "action_type",
                                 "action_value", "steps", "enabled", "motion_only", "reason"],
                    "additionalProperties": False,
                },
            },
            "questions": {"type": "array", "items": {"type": "string"},
                          "description": "Korean questions when the request is unclear or impossible"},
        },
        "required": ["summary", "proposals", "questions"],
        "additionalProperties": False,
    }


def system_prompt(gestures: Sequence[Dict[str, Any]]) -> str:
    """The catalogue the model may choose from. Stable text (gestures sorted) so it can be cached."""
    from manux.compat.hand_mouse.action_bindings import DIRECTION_NAMES_KO, POSTURE_NAMES_KO, TRIGGER_NAMES_KO
    from manux.compat.hand_mouse.macro import MAX_STEPS, MAX_TOTAL_WAIT, MAX_WAIT, MOUSE_STEPS
    lines = [
        "You configure MANUX, a Windows app that turns hand gestures seen by a webcam into commands.",
        "The user (Korean) says which gesture should do what, possibly only in some programs.",
        "Propose binding changes. Use ONLY the listed gesture keys and enumerated action IDs; never invent keys.",
        "Hotkeys, program targets and PowerShell scripts are extensible, not restricted to the example presets.",
        "If a required gesture/target is missing or the request is ambiguous, add a precise Korean question",
        "to `questions` instead of guessing. Do not change bindings the user did not ask about.",
        "",
        "Command types (action_type / action_value):",
        "- mouse_action: one of the mouse/system actions below.",
        "- youtube_key: one of the YouTube/media keys below (works in the focused player).",
        "- hotkey: a key combination like ctrl+shift+t, alt+left, win+d, f5 (lowercase, '+' between).",
        "- launch_app: a program path/command, a web address to open, or a folder / document to open. To start an "
        "installed program write just its name as the user said it, in Latin letters when it has one (\"arduino ide\", "
        "\"kakaotalk\", \"notepad\"): the app finds it on the computer. Never invent a path. For the user's own "
        "folders write %USERPROFILE% (their home folder, the one named after the user): %USERPROFILE%\\Desktop, "
        "%USERPROFILE%\\Downloads, %USERPROFILE%\\Documents, %USERPROFILE%\\Desktop\\<folder name>. Never guess "
        "a user name or a drive.",
        f"- macro: several commands in order, in `steps` (action_value \"\"). At most {MAX_STEPS} steps. Step kinds:",
        "  key (a hotkey as above), type (text to type, any language), wait (seconds, at most "
        f"{MAX_WAIT:g}; all waits at most {MAX_TOTAL_WAIT:g}), open (a program or web address),",
        f"  mouse ({', '.join(MOUSE_STEPS)}), youtube (a YouTube/media key value below).",
        "  Use a macro only when the user asks for more than one thing; add a short wait after `open` before keys.",
        "Opening a web page in a private window: ONE open step with the browser and its flag, e.g.",
        "  chrome.exe --incognito https://www.youtube.com   (Edge: msedge.exe -inprivate <address>).",
        "  Add --start-maximized for a maximised window, or a key step f11 (after a wait) for full screen.",
        "  Never open the page first and then press ctrl+shift+n: that opens an empty private window.",
        "The user must have said or shown WHICH gesture runs the command (a gesture named in the request, or the hand",
        "  shape / movement in the app's confirmed information). If neither is there, make NO proposal and do not invent",
        "  a gesture: ask in `questions` which hand shape to use and tell them to press [손 모양·움직임 보여주기] first.",
        "Do exactly what the user asks: no extra steps. Opening one page or program is ONE launch_app command, not a",
        "  macro; add waits, maximising or full screen ONLY when the user asks for them in this request (earlier",
        "  connections in the list are not a pattern to copy).",
        "Opening something 'in a full window / 전체창 / 큰 창' means maximised: a macro with the open step, a wait of 2",
        "  seconds, then key win+up. '전체화면 / full screen' means the same with key f11 instead of win+up.",
        "  (Browser start flags like --start-maximized are ignored when the browser is already running.)",
        "  When the user names a site or program to open, they want it OPENED that way: a YouTube key (yt_fullscreen)",
        "  only acts on a YouTube page that is already open and focused, so never answer an 'open ...' request with it.",
        "For anything that is not a macro, `steps` is [].",
        "",
        "New gestures: if the user describes a gesture that is not in the list but is a hand posture plus a movement,",
        "use gesture_key \"__new__\" with new_posture, new_trigger and a short Korean new_name; the app registers it.",
        "For every other proposal new_name, new_posture and new_trigger are \"\". Never create a gesture that is already",
        "in the list (same posture and movement): use the existing key.",
        "Postures: " + ", ".join(f"{k} ({v})" for k, v in POSTURE_NAMES_KO.items() if k != "any"),
        "The open hand is four shapes by where the palm faces: open_hand = the palm to the camera (a plain 손 펴기),",
        "open_hand_down = the palm to the floor (손바닥을 바닥으로), open_hand_up = the palm up (손바닥을 위로),",
        "open_hand_back = the BACK of the hand to the camera (손등). Use the last three only when the user says so or",
        "the confirmed information shows it.",
        "Movements: " + ", ".join(f"{k} ({v})" for k, v in TRIGGER_NAMES_KO.items()),
        "Time-series motions (motion_<direction>): one sweep of the hand in that direction while holding the posture,",
        "recognised in motion mode (the user turns motion mode on first, e.g. thumbs-up): "
        + ", ".join(f"motion_{k} ({v})" for k, v in DIRECTION_NAMES_KO.items()) + ".",
        "Use a motion when the user describes sweeping/waving the hand; use a composite swipe when they hold a pinch",
        "and move.",
        "Shape drawing with a hand shape (draw_<shape> as new_trigger): hold the posture and draw that shape with the",
        "palm, e.g. a fist drawing a circle = new_posture \"fist\", new_trigger \"draw_circle\". Works without the",
        "drawing mode. (The drawing mode's own draw_* gestures are drawn with the pointing index finger in that mode.)",
        "Turning the hand with a hand shape held (new_trigger rotate_cw / rotate_ccw, clockwise / counter-clockwise as",
        "seen on the screen, about 90 degrees): e.g. thumbs-up turned like a dial = new_posture \"thumbs_up\",",
        "new_trigger \"rotate_cw\". Works without the drawing mode.",
        "Sequences: hand shapes done one after another (each held a moment, the next within 1 s), e.g. 'V 사인 다음 주먹'.",
        "Use gesture_key \"__new__\" with new_sequence = the shapes in order (2-3, no shape twice in a row) and",
        "new_posture/new_trigger \"\". For every other proposal new_sequence is []. Shapes: " + ", ".join(SEQ_POSTURES) + ".",
        "motion_only: true makes a NEW gesture (posture + swipe/hold/double pinch, a sequence, or shape drawing) count",
        "only while motion mode is on; set it only when the user asks for that ('모션 모드에서만', '모션 모드일 때만').",
        "Otherwise false (motion_<direction> gestures are motion-mode gestures anyway). It cannot change a gesture that",
        "already exists: tell the user in `summary` to press [모션 모드에서만] on that gesture's card in the list.",
        "",
        "app_match: \"\" applies everywhere (the default command). A non-empty app_match (process name such as",
        "chrome.exe, POWERPNT.EXE, or a window-title word such as YouTube) adds a program-specific command that is",
        "used only while that program is in front; the default stays as it is. A new gesture is created for every",
        "program (app_match \"\").",
        "enabled=false switches the gesture off (keep its current command as action_type/action_value).",
        "Shapes drawn in the drawing mode (keys draw_*) are gestures too: connect commands to them the same way.",
        "The user message may end with a [앱이 확인한 정보] block: facts the app measured (a hand shape the user showed,",
        "a key combination the user just pressed). Use it to resolve words like 이/이거/이 손모양 and 저렇게/이 키/그거.",
        "If such a word has no fact to point to, ask in `questions` instead of guessing.",
        "",
        "MANUX has two separate hand modes, 가상 손 모드 and 손바닥 모드: a gesture marked with one of them never",
        "works in the other. 모든 손 모드 gestures work in both; 모션 모드 gestures only while motion mode is on.",
        "The [앱이 확인한 정보] block says which hand mode the user is using now. Connect only gestures that work in",
        "that mode, unless the user names the other mode. If nothing fitting is free there, make a new gesture from a",
        "hand shape (it works in every hand mode) or ask - never fall back to a gesture of the other hand mode.",
        "",
        "Gestures (key | name | mode | how to do it | current command):",
    ]
    for g in sorted(gestures, key=lambda g: str(g.get("key", ""))):
        current = format_action_badge(str(g.get("action_type", "")), str(g.get("action_value", "")))
        if not g.get("enabled", True):
            current += " (꺼짐)"
        for ov in g.get("app_overrides") or []:
            current += f"; {ov.get('app_match')}: {format_action_badge(str(ov.get('action_type')), str(ov.get('action_value')))}"
        lines.append(f"- {g['key']} | {g.get('name', g['key'])} | {g.get('mode_name', '모든 손 모드')} | {g.get('instruction', '')} | {current}")
    lines += ["", "mouse_action values:"]
    lines += [f"- {k}: {v['label']}" for k, v in MOUSE_ACTION_LABELS.items()]
    lines += ["", "youtube_key values:"]
    seen = set()
    for k, v in YOUTUBE_KEY_PRESETS.items():
        if len(k) > 1 and v["label"] not in seen:
            seen.add(v["label"])
            lines.append(f"- {k}: {v['label']}")
    lines += ["", "Common hotkeys (others are allowed):"]
    lines += [f"- {p['value']}: {p['label']}" for p in WINDOWS_HOTKEY_PRESETS]
    from manux.compat.hand_mouse.pc_commands import capability_prompt
    lines += ["", capability_prompt()]
    return "\n".join(lines)


def _known_key(name: str) -> bool:
    from manux.platform.custom_keys import known_key
    return known_key(name)



def _check_value(action_type: str, value: str) -> Optional[str]:
    """A Korean problem with this command, None when it is fine."""
    if action_type not in ACTION_TYPES:
        return f"모르는 명령 형식: {action_type}"
    if action_type == "powershell":
        from manux.compat.hand_mouse.pc_commands import script_problem
        return script_problem(value)
    if action_type == "system_action":
        from manux.compat.hand_mouse.pc_commands import SYSTEM_ACTIONS
        return None if value in SYSTEM_ACTIONS else f"모르는 PC 기능: {value}"
    if action_type == "macro":
        from manux.compat.hand_mouse.macro import MacroError, parse
        try:
            parse(value)
        except MacroError as exc:
            return str(exc)
        return None
    if action_type == "mouse_action":
        return None if value in MOUSE_ACTION_LABELS else f"모르는 마우스 동작: {value}"
    if action_type == "youtube_key":
        if value in YOUTUBE_KEY_PRESETS:
            return None
        keys = parse_hotkey_keys(value)
        return None if keys and all(_known_key(k) for k in keys) else f"모르는 유튜브 키: {value}"
    if action_type == "hotkey":
        keys = parse_hotkey_keys(value)
        if not keys:
            return "단축키가 비어 있어요"
        bad = [k for k in keys if not _known_key(k)]
        return f"모르는 키: {', '.join(bad)}" if bad else None
    return None if value.strip() else "실행할 프로그램이 비어 있어요"


def _new_gesture(item: Dict[str, Any], gestures: Sequence[Dict[str, Any]], taken: set
                 ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """A composite gesture the AI proposes to create: the stand-in and a problem (None = fine)."""
    from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO, TRIGGER_NAMES_KO
    from manux.compat.hand_mouse.pose_probe import EVERYDAY_POSTURES
    posture = str(item.get("new_posture", "") or "").strip()
    trigger = str(item.get("new_trigger", "") or "").strip()
    if trigger.startswith("draw_") or trigger.startswith("rotate_"):
        from manux.compat.hand_mouse.sequence import POSTURES as PATH_POSTURES
        if posture not in PATH_POSTURES:
            return None, f"그리기에 쓸 수 없는 손 모양: {posture or '(없음)'}"
    if posture not in _postures():
        return None, f"모르는 손 모양: {posture or '(없음)'}"
    if trigger not in _triggers():
        return None, f"모르는 움직임: {trigger or '(없음)'}"
    for g in gestures:
        if g.get("posture") == posture and g.get("trigger") == trigger:
            return None, f"이미 있는 동작이에요: {g['name']} (그 동작에 연결해 주세요)"
    if (posture, trigger) in taken:
        return None, "같은 새 동작이 두 번 제안됐어요"
    taken.add((posture, trigger))
    name = str(item.get("new_name", "") or "").strip()[:30] or f"{POSTURE_NAMES_KO[posture]} + {trigger_name(trigger)}"
    warning = ""
    if posture in EVERYDAY_POSTURES or posture == "open_hand":
        warning = "평소에 자주 하는 손 모양이라 의도치 않게 실행될 수 있어요"
    elif posture in CLICK_POSTURES and trigger in ("hold", "double_pinch"):
        warning = "이미 쓰는 클릭·스크롤 손 모양과 겹칠 수 있어요"
    if trigger.startswith("motion_") and not warning:
        warning = ""          # motions only count in motion mode: no clash with the everyday controls
    if trigger.startswith("draw_") or trigger.startswith("rotate_"):
        warning = ""          # drawing a whole shape / turning the hand is deliberate even with an everyday hand shape
    return {"key": NEW, "name": f"{name} (새 동작)", "new_name": name, "new_posture": posture, "new_trigger": trigger,
            "action_type": "mouse_action", "action_value": "none", "enabled": True, "app_overrides": [],
            "warning": warning}, None


def _new_sequence(item: Dict[str, Any], gestures: Sequence[Dict[str, Any]], taken: set
                  ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """A gesture sequence the AI proposes to create (src/sequence.py)."""
    from manux.compat.hand_mouse.sequence import check_steps, steps_text
    steps = [str(x) for x in (item.get("new_sequence") or [])]
    problem = check_steps(steps)
    if problem:
        return None, problem
    for g in gestures:
        other = list(g.get("sequence") or [])
        n = min(len(other), len(steps))
        if other and other[:n] == steps[:n]:
            return None, f"이미 있는 이어 하기와 겹쳐요: {g['name']}"
    if tuple(steps) in taken:
        return None, "같은 이어 하기가 두 번 제안됐어요"
    taken.add(tuple(steps))
    name = str(item.get("new_name", "") or "").strip()[:30] or steps_text(steps)
    warning = "엄지 위로는 제어 켜기/끄기·모션 모드 신호이기도 해요" if "thumbs_up" in steps else ""
    return {"key": NEW, "name": f"{name} (새 이어 하기: {steps_text(steps)})", "new_name": name, "new_sequence": steps,
            "action_type": "mouse_action", "action_value": "none", "enabled": True, "app_overrides": [],
            "warning": warning}, None


def check(answer: Dict[str, Any], gestures: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The model's answer -> what the screen shows: each proposal with `ok`, `problem`, `risky`, `checked` and the
    before/after text. Anything not in the catalogue is marked, never applied."""
    by_key = {str(g["key"]): g for g in gestures}
    raw = answer.get("proposals")
    raw = raw if isinstance(raw, list) else []
    rows: List[Dict[str, Any]] = []
    taken: set = set()
    for item in raw[:MAX_PROPOSALS]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("gesture_key", "")).strip()
        atype = str(item.get("action_type", "")).strip()
        value = str(item.get("action_value", "")).strip()
        app = str(item.get("app_match", "") or "").strip()
        enabled = item.get("enabled", True) is not False
        steps: List[Dict[str, str]] = []
        problem: Optional[str] = None
        if key == NEW and item.get("new_sequence"):
            g, problem = _new_sequence(item, gestures, taken)
        elif key == NEW:
            g, problem = _new_gesture(item, gestures, taken)
        else:
            g = by_key.get(key)
            if g is None:
                problem = "없는 동작이에요"
        if g is not None and not enabled and (not value or value == "none") and atype != "macro":
            atype, value = str(g.get("action_type", "mouse_action")), str(g.get("action_value", "none"))
        found = ""
        if atype == "launch_app":
            from manux.compat.hand_mouse.action_bindings import web_address
            value = web_address(value)
            if problem is None and enabled and value and value != "none":
                # the AI does not see this computer: what it wrote is looked up here, and a program that is not on
                # this PC is refused now instead of failing silently later (2026-11-11: "arduino", a folder, …)
                from manux.compat.hand_mouse import app_launch
                target = app_launch.resolve(value)
                if not target.ok:
                    problem = target.note
                elif target.note:
                    found = target.note
                    if target.kind == "run" or target.path.lower().endswith(".lnk"):
                        value = " ".join([f'"{target.path}"'] + list(target.args))
        if atype == "macro" and problem is None:
            from manux.compat.hand_mouse.macro import MacroError, parse
            try:
                steps = parse(item.get("steps") or value)
                value = json.dumps(steps, ensure_ascii=False)
            except MacroError as exc:
                problem = str(exc)
        if problem is None:
            problem = _check_value(atype, value)
        if problem is None and not enabled and app:
            problem = "특정 프로그램에서만 끄기는 아직 지원하지 않아요"
        if problem is None and g is not None and (g.get("new_posture") or g.get("new_sequence")) and app:
            problem = "새 동작은 먼저 모든 프로그램용으로 등록한 뒤 프로그램별 연결을 더해 주세요"
        if problem is None and g is not None and (g.get("new_posture") or g.get("new_sequence")) and not enabled:
            problem = "꺼 둔 채로 새 동작을 만들 수는 없어요"
        warning = ""
        if atype == "powershell":
            warning = "이 동작이 인식되면 아래 PowerShell 명령을 현재 Windows 계정 권한으로 실행합니다"
        elif atype == "system_action":
            from manux.compat.hand_mouse.pc_commands import SYSTEM_ACTIONS
            warning = SYSTEM_ACTIONS.get(value, {}).get("warning", "")
        elif atype == "launch_app":
            warning = f"프로그램을 실행합니다: {value}" + (f" ({found})" if found else "")
        elif atype == "macro" and steps:
            from manux.compat.hand_mouse.macro import risky as macro_risky
            warning = macro_risky(steps)
        if not warning and g is not None and str(g.get("action_value")) in ("toggle_control", "toggle_motion") \
                and (atype, value) != (g.get("action_type"), g.get("action_value")):
            warning = ("이 동작은 " + ("제어 켜기/끄기" if g.get("action_value") == "toggle_control" else "모션 모드")
                       + " 신호예요. 바꾸면 그 신호로는 쓸 수 없어요 (V 사인은 제어가 꺼져 있을 때만 다시 켜기로 동작)")
        if not warning and g is not None:
            warning = g.get("warning", "") or ("평소에 자주 하는 손 모양이라 의도치 않게 실행될 수 있어요" if g.get("everyday") else "")
        risky = bool(warning)
        if risky:
            warning += " · 직접 확인하고 체크하세요"
        # "motion mode only" (2026-11-11): for a gesture being created; a motion_<direction> gesture is one anyway
        motion_only = bool(item.get("motion_only") is True and g is not None
                           and (g.get("new_sequence") or (g.get("new_posture")
                                                          and not str(g.get("new_trigger", "")).startswith("motion_"))))
        current = "" if g is None else format_action_badge(str(g.get("action_type", "")), str(g.get("action_value", "")))
        if g is not None and (g.get("new_posture") or g.get("new_sequence") or key == NEW):
            current = "(새로 만듦)"
        elif g is not None and app:
            for ov in g.get("app_overrides") or []:
                if str(ov.get("app_match", "")).lower() == app.lower():
                    current = format_action_badge(str(ov.get("action_type")), str(ov.get("action_value")))
                    break
            else:
                current = "(없음)"
        rows.append({
            "gesture_key": key,
            "gesture_name": (str(g.get("name", key)) + (" · 모션 모드에서만" if motion_only else "")) if g else key,
            "motion_only": motion_only,
            "app_match": app, "action_type": atype, "action_value": value, "enabled": enabled,
            "type_name": TYPE_NAMES.get(atype, atype),
            "steps": steps,
            "before": current,
            "after": ("끄기" if not enabled else format_action_badge(atype, value)) if problem is None else "",
            "reason": str(item.get("reason", ""))[:200],
            "ok": problem is None, "problem": problem or "", "risky": risky, "warning": warning,
            "new_posture": str(g.get("new_posture", "")) if g else "",
            "new_trigger": str(g.get("new_trigger", "hold") if g and g.get("new_posture") else ""),
            "new_name": str(g.get("new_name", "")) if g else "",
            "new_sequence": list(g.get("new_sequence") or []) if g else [],
            "checked": problem is None and not risky,
        })
    questions = [str(q)[:300] for q in (answer.get("questions") or []) if isinstance(q, str) and q.strip()][:5]
    return {"summary": str(answer.get("summary", ""))[:500], "proposals": rows, "questions": questions}


def _apply_row(r: Dict[str, Any], registry, request: str, when: str, sequences, paths,
               done: List[str], log: List[Dict[str, Any]]) -> None:
    key, app = r["gesture_key"], r.get("app_match", "")
    entry = {"time": when, "request": request[:300], "gesture_key": key, "gesture_name": r["gesture_name"],
             "app_match": app, "action_type": r["action_type"], "action_value": r["action_value"],
             "enabled": bool(r["enabled"]), "after": r["after"], "steps": r.get("steps") or []}
    only = bool(r.get("motion_only"))
    only_text = " (모션 모드에서만)" if only else ""
    if r.get("new_sequence"):
        from manux.compat.hand_mouse.sequence import SequenceStore
        store = sequences if sequences is not None else SequenceStore()
        seq_key = store.add(r.get("new_name", ""), r["new_sequence"])
        registry.set_binding(seq_key, r["action_type"], r["action_value"], track="sequence")
        store.set_motion_only(seq_key, only)
        name = store.items[seq_key]["name"]
        done.append(f"새 이어 하기 '{name}' 등록 → {r['after']}{only_text}")
        log.append({**entry, "gesture_key": seq_key, "gesture_name": name, "created": True, "sequence": True,
                    "before": {}, "before_text": "(새로 만듦)"})
        return
    if r.get("new_posture") and str(r.get("new_trigger", "")).startswith(("draw_", "rotate_")):
        from manux.compat.hand_mouse.path_gesture import PathStore
        store = paths if paths is not None else PathStore()
        path_key = store.add(r.get("new_name", ""), r["new_posture"], r["new_trigger"])
        registry.set_binding(path_key, r["action_type"], r["action_value"], track="path")
        store.set_motion_only(path_key, only)
        name = store.items[path_key]["name"]
        done.append(f"새 손 모양 그리기 '{name}' 등록 → {r['after']}{only_text}")
        log.append({**entry, "gesture_key": path_key, "gesture_name": name, "created": True, "path": True,
                    "before": {}, "before_text": "(새로 만듦)"})
        return
    if r.get("new_posture"):
        trigger = r.get("new_trigger") or "hold"
        if trigger.startswith("motion_"):
            comp = registry.register_motion_gesture("", r.get("new_name", ""), r["new_posture"], trigger[7:],
                                                    r["action_type"], r["action_value"])
            done.append(f"새 모션 '{comp['name']}' 등록 → {r['after']} (모션 모드에서)")
            log.append({**entry, "gesture_key": comp["key"], "gesture_name": comp["name"], "created": True,
                        "motion": True, "before": {}, "before_text": "(새로 만듦)"})
            return
        comp = registry.register_composite_gesture("", r.get("new_name", ""), r["new_posture"], trigger,
                                                   r["action_type"], r["action_value"],
                                                   hold_seconds=hold_minimum(r["new_posture"]) if trigger == "hold" else .55)
        if only:
            registry.set_composite_motion_only(comp["key"], True)
        done.append(f"새 동작 '{comp['name']}' 등록 → {r['after']}{only_text}")
        log.append({**entry, "gesture_key": comp["key"], "gesture_name": comp["name"], "created": True,
                    "before": {}, "before_text": "(새로 만듦)"})
        return
    current = registry.get_binding(key)
    if app:
        before = next(({"action_type": ov.get("action_type"), "action_value": ov.get("action_value")}
                       for ov in current.get("app_overrides") or []
                       if str(ov.get("app_match", "")).lower() == app.lower()), {})
        registry.add_app_override(key, app, r["action_type"], r["action_value"])
        done.append(f"{r['gesture_name']} · {app}에서 → {r['after']}")
    else:
        before = {"action_type": current.get("action_type"), "action_value": current.get("action_value"),
                  "enabled": bool(current.get("enabled", True))}
        registry.set_binding(key, r["action_type"], r["action_value"], enabled=bool(r["enabled"]),
                             track=current.get("track"))
        done.append(f"{r['gesture_name']} → {r['after']}")
    entry["before"] = before
    entry["before_text"] = (format_action_badge(str(before.get("action_type", "")), str(before.get("action_value", "")))
                            if before else "(없음)")
    log.append(entry)


def apply(rows: Sequence[Dict[str, Any]], registry, history: Optional["History"] = None, request: str = "",
          sequences=None, paths=None) -> List[str]:
    """Save the ticked, valid proposals. Returns a Korean line per saved change; each is also written to `history`
    (with what was there before, for undo)."""
    done: List[str] = []
    log: List[Dict[str, Any]] = []
    when = time.strftime("%Y-%m-%d %H:%M")
    for r in rows:
        if not (r.get("ok") and r.get("checked")):
            continue
        # one row that cannot be saved (e.g. it clashes with a sequence that is switched off, which the check does not
        # see) must not lose the history of the rows saved before it: they could then not be undone
        try:
            _apply_row(r, registry, request, when, sequences, paths, done, log)
        except (ValueError, KeyError, OSError) as exc:
            done.append(f"저장하지 못함: {r.get('gesture_name', r.get('gesture_key', ''))} ({exc})")
    if history is not None:
        history.add(log)
    return done


def gesture_list(registry) -> List[Dict[str, Any]]:
    """Every gesture a command can be connected to, with its current binding: the catalogue's gestures (not the
    background / near-miss ones, which only teach the model what is NOT a gesture), registered motions and composites."""
    from manux.compat.hand_mouse import gesture_catalog as gc
    from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO, TRIGGER_NAMES_KO
    out: List[Dict[str, Any]] = []
    seen = set()

    from manux.compat.hand_mouse.action_bindings import MODE_NAMES_KO, VIRTUAL_GESTURE_NAMES, gesture_mode

    def add(key: str, name: str, how: str) -> None:
        if key in seen:
            return
        seen.add(key)
        b = registry.get_binding(key)
        mode = gesture_mode(key, registry)
        out.append({"key": key, "name": name, "instruction": how, "mode": mode, "mode_name": MODE_NAMES_KO[mode],
                    "action_type": str(b.get("action_type", "mouse_action")), "action_value": str(b.get("action_value", "none")),
                    "enabled": bool(b.get("enabled", True)), "app_overrides": list(b.get("app_overrides") or [])})

    for t in gc.TASKS:
        if t.free or t.label == "none" or t.key in gc.NEAR_MISS_TIPS:
            continue
        add(t.key, plain_name(t.name), t.do)
    # the hand on the screen has its own gestures (2026-11-12: they were missing here, so a request made in that mode
    # could only be connected to a palm-mode pinch, which does nothing there)
    for key, name in VIRTUAL_GESTURE_NAMES.items():
        add(key, name, "가상 손 모드의 동작")
    for key, m in registry.custom_motions.items():
        add(key, str(m.get("name", key)), "등록한 모션 (모션 모드에서)")
        out[-1]["posture"], out[-1]["trigger"] = str(m.get("posture", "")), "motion_" + str(m.get("direction", ""))
    for key, c in registry.composite_gestures.items():
        posture = POSTURE_NAMES_KO.get(str(c.get("base_posture", "")), str(c.get("base_posture", "")))
        trigger = TRIGGER_NAMES_KO.get(str(c.get("trigger_type", "")), str(c.get("trigger_type", "")))
        add(key, str(c.get("name", key)), f"복합 동작: {posture} + {trigger}" + (" (모션 모드에서만)" if c.get("motion_only") else ""))
        out[-1]["posture"], out[-1]["trigger"] = str(c.get("base_posture", "")), str(c.get("trigger_type", ""))
    try:
        from manux.compat.hand_mouse.path_gesture import PathStore
        for row in PathStore().rows():
            if row["enabled"]:
                add(row["key"], row["name"], row["steps_text"])
                out[-1]["posture"], out[-1]["trigger"] = row["posture"], row["shape"]
    except Exception:
        pass
    try:
        from manux.compat.hand_mouse.sequence import SequenceStore
        for row in SequenceStore().rows():
            if row["enabled"]:
                add(row["key"], row["name"], "이어 하기: " + row["steps_text"])
                out[-1]["sequence"] = row["steps"]
    except Exception:
        pass
    try:
        from manux.compat.hand_mouse.draw_mode import ShapeStore
        for shape in ShapeStore().shapes():
            if shape["enabled"]:
                add(shape["key"], shape["name"], "그리기 모드에서 그리는 모양")
    except Exception:
        pass
    return out


OPEN_FAMILY = ("open_hand", "open_hand_back", "open_hand_down", "open_hand_up")


def plain_name(name: str) -> str:
    """A catalogue gesture's name without the command it had when it was recorded: "엄지+새끼 (창 선택)" ->
    "엄지+새끼". 2026-11-12 report: the card of a gesture connected to Ctrl+W still said "창 선택"."""
    name = str(name or "")
    return name.split(" (")[0] if name.endswith(")") and " (" in name else name


def mark_modes(rows: List[Dict[str, Any]], gestures: Sequence[Dict[str, Any]], hand_mode: str = "") -> List[Dict[str, Any]]:
    """Each proposal with the world its gesture lives in ("mode", "mode_name"). A gesture of the OTHER hand mode is
    said so and left unticked (2026-11-12: "가상손 모드랑 손바닥 모드는 완전 별개의 세계관 … 커스텀 때도 해당 동작이
    어떤 모드에서 돌아가는 건지 명확하게"): it would be saved and never run in the mode in use."""
    from manux.compat.hand_mouse.action_bindings import MODE_NAMES_KO, works_in
    by_key = {str(g["key"]): g for g in gestures}
    for row in rows:
        g = by_key.get(str(row.get("gesture_key", "")))
        if row.get("new_posture") or row.get("new_sequence"):
            mode = "motion" if row.get("motion_only") or str(row.get("new_trigger", "")).startswith("motion_") else "all"
        else:
            mode = str((g or {}).get("mode") or "all")
        row["mode"], row["mode_name"] = mode, MODE_NAMES_KO.get(mode, mode)
        if row.get("ok") and hand_mode and not works_in(mode, hand_mode):
            now = {"virtual": "가상 손 모드", "palm": "손바닥 모드", "index": "검지 포인터 모드"}.get(hand_mode, hand_mode)
            text = f"이 동작은 {row['mode_name']} 전용이라 지금 쓰는 {now}에서는 동작하지 않아요. 그래도 연결하려면 체크하세요"
            old = str(row.get("warning") or "").replace(" · 직접 확인하고 체크하세요", "")
            row["warning"] = (old + " · " if old else "") + text
            row["risky"], row["checked"], row["other_mode"] = True, False, True
    return rows


SHOWN = "__shown__"          # gesture key of a shown hand shape that is not a gesture yet (registered on apply)


def shown_key(shown: Optional[Dict[str, Any]], gestures: Sequence[Dict[str, Any]]) -> str:
    """The existing gesture a shown shape stands for: its catalogue gesture, else a "hold" gesture made for that
    shape before (2026-11-10: registering it again replaced the earlier one silently), else ""."""
    if not (shown and shown.get("state") == "done") or shown.get("unknown"):
        return ""            # unknown: no listed shape / movement (2026-11-11: not forced into the nearest one)
    if shown.get("kind") == "drawn":            # a drawn path: the drawing mode's shape
        return str(shown.get("shape_key", ""))
    if shown.get("kind") == "rotate":           # a turn: a "hold the shape and turn" gesture if one exists
        for g in gestures:
            if g.get("posture") == shown.get("posture") and g.get("trigger") == shown.get("rotate"):
                return g["key"]
        return ""
    if shown.get("kind") == "motion":           # a shown movement: a motion gesture with that shape and direction
        trig = "motion_" + str(shown.get("direction", ""))
        for g in gestures:
            if g.get("posture") == shown.get("posture") and g.get("trigger") == trig:
                return g["key"]
        return ""
    gk = shown.get("gesture_key") or ""
    if gk and any(g["key"] == gk for g in gestures):
        return gk
    for g in gestures:
        if g.get("posture") == shown.get("posture") and g.get("trigger") == "hold":
            return g["key"]
    return ""


def context_block(shown: Optional[Dict[str, Any]], key: str, gestures: Sequence[Dict[str, Any]]) -> str:
    """What the app saw, appended to the user's text so the AI can resolve 이/저 (no image, only names)."""
    lines = []
    if shown and shown.get("state") == "done" and shown.get("unknown"):
        lines.append("- 사용자가 손 모양·움직임을 보여 주었지만 앱의 목록에 없는 것이었어요. 사용자가 이름을 지어 새 손 모양으로 "
                     "가르친 뒤에야 쓸 수 있으니, 그 손 모양('이 손모양', '이 동작')에 대한 제안은 하지 말고 그 사실을 알려 주세요.")
    elif shown and shown.get("state") == "done" and shown.get("kind") == "rotate":
        gk = shown_key(shown, gestures)
        what = f"{shown['name']} 모양으로 {shown['rotate_name']}"
        if gk:
            lines.append(f"- 사용자가 보여준 움직임('이 동작', '이렇게 돌리면'): {what} → gesture_key \"{gk}\"")
        else:
            lines.append(f"- 사용자가 보여준 움직임('이 동작', '이렇게 돌리면'): {what}. 새로 만들려면 gesture_key \"{NEW}\", "
                         f"new_posture \"{shown['posture']}\", new_trigger \"{shown['rotate']}\".")
    elif shown and shown.get("state") == "done" and shown.get("kind") == "drawn":
        posture = str(shown.get("posture") or "")
        lines.append(f"- 사용자가 보여준 움직임('이 동작', '이렇게 그리면'): {shown.get('name') or '손'} 모양으로 "
                     f"'{shown['shape_name']}'을 그림. 그리기 모드 없이 이 손 모양으로 그리면 실행되게 하려면 gesture_key \"{NEW}\", "
                     f"new_posture \"{posture}\", new_trigger \"{shown['shape_key']}\" (기본으로 이것을 쓰세요; 같은 것이 목록에 "
                     f"있으면 그 key). 그리기 모드에서 검지로 그리는 모양에 연결하려면 gesture_key \"{shown['shape_key']}\".")
    elif shown and shown.get("state") == "done" and shown.get("kind") == "motion":
        gk = shown_key(shown, gestures)
        what = f"{shown['name']} 모양으로 {shown['direction_name']}"
        if gk:
            lines.append(f"- 사용자가 보여준 움직임('이 동작', '이렇게 하면'): {what} → gesture_key \"{gk}\"")
        else:
            d = shown["direction"]
            swipe = f" 또는 그 모양을 유지한 채 쓸기라면 new_trigger \"swipe_{d}\"" if d in ("left", "right", "up", "down") else ""
            lines.append(f"- 사용자가 보여준 움직임('이 동작', '이렇게 하면'): {what}. 아직 동작 목록에 없음. 새로 만들려면 "
                         f"gesture_key \"{NEW}\", new_posture \"{shown['posture']}\", new_trigger \"motion_{d}\"(모션 모드에서 한 번 쓸기)"
                         f"{swipe}.")
    elif shown and shown.get("state") == "done":
        gk = shown_key(shown, gestures)
        if gk and any(g["key"] == gk for g in gestures):
            lines.append(f"- 사용자가 보여준 손 모양('이 손모양', '이거'): {shown['name']} → gesture_key \"{gk}\"")
        else:
            lines.append(f"- 사용자가 보여준 손 모양('이 손모양', '이거'): {shown['name']}. 아직 동작 목록에 없음. 이 손 모양에 연결하려면 "
                         f"gesture_key \"{SHOWN}\"를 쓰세요 (앱이 '{shown['name']}을 잠깐 유지' 동작을 새로 등록함).")
    if key:
        lines.append(f"- 사용자가 방금 누른 키('저렇게', '이 키', '이렇게'): {key} → action_type \"hotkey\", action_value \"{key}\"")
    return ("\n\n[앱이 확인한 정보]\n" + "\n".join(lines)) if lines else ""


def with_shown(gestures: Sequence[Dict[str, Any]], shown: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The gesture list plus a stand-in for a shown shape that is not a gesture yet."""
    out = list(gestures)
    if (shown and shown.get("state") == "done" and not shown.get("unknown")
            and shown.get("kind") not in ("motion", "drawn", "rotate")):
        if not shown_key(shown, gestures):
            out.append({"key": SHOWN, "name": f"{shown['name']} 유지 (새 동작)", "instruction": "보여준 손 모양",
                        "action_type": "mouse_action", "action_value": "none", "enabled": True, "app_overrides": [],
                        "new_posture": shown["posture"], "new_trigger": "hold", "new_name": f"{shown['name']} 유지",
                        "everyday": bool(shown.get("everyday"))})
    return out


def ask(cfg, text: str, gestures: Sequence[Dict[str, Any]], shown: Optional[Dict[str, Any]] = None,
        key: str = "", hand_mode: str = "") -> Dict[str, Any]:
    """One request to the provider and the checked result."""
    from manux.compat.hand_mouse.llm_providers import LlmError, complete_json
    text = (text or "").strip()
    if not text:
        raise LlmError("무엇을 연결할지 적거나 말해 주세요.")
    if len(text) > 2000:
        raise LlmError("요청이 너무 길어요 (2000자까지).")
    gestures = [g for g in gestures if g.get("key")]
    if not gestures:
        raise LlmError("연결할 수 있는 동작 목록이 비어 있어요.")
    base = gestures
    gestures = with_shown(gestures, shown)
    # the system prompt lists only the real gestures (stable, cacheable); the stand-in is described in the user turn
    now = {"virtual": "가상 손 모드", "palm": "손바닥 모드", "index": "검지 포인터 모드 (손 모양 동작은 쓰이지 않음)"}.get(hand_mode, "")
    mode_line = f"\n- 사용자가 지금 쓰는 손 방식: {now}" if now else ""
    block = context_block(shown, key, base)
    if mode_line:
        block = (block + mode_line) if block else "\n\n[앱이 확인한 정보]" + mode_line
    answer = complete_json(cfg, system_prompt(base), text + block, schema([str(g["key"]) for g in gestures]))
    result = check(answer, gestures)
    mark_modes(result.get("proposals") or [], gestures, hand_mode)
    return result


def describe_request(cfg, text: str) -> str:
    """What leaves the PC, for the screen (shown under the send button)."""
    if cfg.provider == "ollama":
        return "이 PC의 Ollama로만 보냅니다 (인터넷으로 나가지 않음)."
    return (f"{cfg.spec.label}로 보내는 것: 적은 문장, 동작 이름과 현재 명령, 명령 목록. "
            "카메라 영상·관절·창 제목은 보내지 않아요.")


def to_json(result: Dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------- what the AI connected (a readable list + undo)
HISTORY_PATH = DATA_ROOT / "config" / "ai_connections.json"
HISTORY_MAX = 200


class History:
    """Every change saved from an AI proposal: the request, the gesture, the new command and what was there before (so
    it can be undone). config/ai_connections.json; nothing else (no keys, no raw provider answers)."""

    def __init__(self, path: Optional[Path] = None, sequences_path: Optional[Path] = None,
                 paths_path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else HISTORY_PATH
        self.sequences_path = sequences_path       # tests: a temporary sequences file
        self.paths_path = paths_path

    def load(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []

    def _save(self, entries: List[Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries[-HISTORY_MAX:], ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, items: Sequence[Dict[str, Any]]) -> None:
        if items:
            self._save(self.load() + list(items))

    def rows(self, registry) -> List[Dict[str, Any]]:
        """Newest first, with whether each is still in force (a later change may have replaced it)."""
        out = []
        modes = self._mode_holders(registry)
        for i, e in enumerate(self.load()):
            if e.get("undone"):
                continue
            row = {**e, "index": i, "current": still_in_force(e, registry, self.sequences_path, self.paths_path)}
            # "motion mode only" (2026-11-11): a switch on the gestures that can have it; a motion gesture always is
            holder = modes.get(e.get("gesture_key", ""))
            row["mode_option"] = bool(row["current"]) and holder is not None and not e.get("app_match")
            row["motion_only"] = bool(holder.get("motion_only")) if holder is not None else (
                e.get("gesture_key", "") in registry.custom_motions)
            comp = registry.composite_gestures.get(e.get("gesture_key", ""))    # created here or an existing hold gesture
            if comp is not None and comp.get("trigger_type") == "hold" and not e.get("motion"):
                posture = str(comp.get("base_posture", ""))
                row.update(hold_seconds=round(float(comp.get("hold_seconds", .55)), 2),
                           hold_min=hold_minimum(posture), hold_max=HOLD_MAX)
            row["editable"] = bool(row["current"]) and e.get("enabled", True) is not False
            # which world the gesture lives in, and its name without the command it was recorded with (2026-11-12)
            from manux.compat.hand_mouse.action_bindings import MODE_NAMES_KO, gesture_mode
            row["mode"] = gesture_mode(e.get("gesture_key", ""), registry)
            row["mode_name"] = MODE_NAMES_KO[row["mode"]]
            from manux.compat.hand_mouse import gesture_catalog as _gc
            if e.get("gesture_key", "") in _gc.BY_KEY:
                row["gesture_name"] = plain_name(row.get("gesture_name", ""))
            # the hand shape this gesture starts from: [수정] can show it again as one more example for the taught
            # model (2026-11-12 request: "수정 메뉴에 mlp 추가 학습시키기 옵션도")
            row["teach_posture"] = str(comp.get("base_posture", "")) if comp is not None and row["editable"] else ""
            # not for the open hand in any of its facings (2026-11-12): the taught model's features are relative to the
            # palm, so it cannot tell where the palm faces - showing it more looks would teach it nothing
            if row["teach_posture"] in OPEN_FAMILY:
                row["teach_posture"] = ""
            out.append(row)
        return list(reversed(out))

    def _mode_holders(self, registry) -> Dict[str, Dict[str, Any]]:
        """Gesture key -> its stored definition, for the gestures that can be "motion mode only": composite gestures
        (shape + swipe / hold / double pinch), sequences and shape drawings."""
        out: Dict[str, Dict[str, Any]] = dict(registry.composite_gestures)
        try:
            from manux.compat.hand_mouse.sequence import SequenceStore
            out.update(SequenceStore(self.sequences_path).items)
            from manux.compat.hand_mouse.path_gesture import PathStore
            out.update(PathStore(self.paths_path).items)
        except Exception:
            pass
        return out

    def set_motion_only(self, index: int, registry, on: bool) -> str:
        """Make the gesture of this list line count only while motion mode is on (or everywhere again)."""
        entries = self.load()
        if not 0 <= index < len(entries) or entries[index].get("undone"):
            raise ValueError("바꿀 항목이 없어요.")
        e = entries[index]
        key = e["gesture_key"]
        if not still_in_force(e, registry, self.sequences_path, self.paths_path):
            raise ValueError("그 뒤에 다른 곳에서 바뀌어서 여기서는 바꾸지 않았어요.")
        from manux.compat.hand_mouse.path_gesture import PathStore
        from manux.compat.hand_mouse.sequence import SequenceStore
        done = registry.set_composite_motion_only(key, on)
        done = done or SequenceStore(self.sequences_path).set_motion_only(key, on)
        done = done or PathStore(self.paths_path).set_motion_only(key, on)
        if not done:
            raise ValueError("이 동작은 모드를 정할 수 없어요 (손 모양으로 새로 만든 동작만 돼요).")
        name = e.get("gesture_name", key)
        return (f"'{name}': 이제 모션 모드에서만 동작해요 (엄지 위로 또는 [모션 모드] 버튼으로 켠 뒤)." if on
                else f"'{name}': 다시 평소 조작 중에 동작해요.")

    def edit(self, index: int, registry, action_type: str, action_value: str,
             hold_seconds: Optional[float] = None) -> str:
        """Change the command of an AI connection (and, for a created hold gesture, how long it must be held) later,
        by hand (2026-11-11). The list line follows, so it stays "적용 중" and undo still goes to what was there before
        the AI."""
        entries = self.load()
        if not 0 <= index < len(entries) or entries[index].get("undone"):
            raise ValueError("고칠 항목이 없어요.")
        e = entries[index]
        if not still_in_force(e, registry, self.sequences_path, self.paths_path):
            raise ValueError("그 뒤에 다른 곳에서 바뀌어서 여기서는 고치지 않았어요.")
        atype, value = str(action_type or "").strip(), str(action_value or "").strip()
        steps: List[Any] = []
        if atype == "macro":
            from manux.compat.hand_mouse.macro import MacroError, parse
            try:
                steps = parse(value)
            except MacroError as exc:
                raise ValueError(str(exc)) from exc
            value = json.dumps(steps, ensure_ascii=False)

        problem = _check_value(atype, value)
        if problem:
            raise ValueError(problem)
        key, app = e["gesture_key"], e.get("app_match", "")
        notes = []
        if hold_seconds is not None:
            comp = registry.composite_gestures.get(key)
            if comp is None or comp.get("trigger_type") != "hold":
                raise ValueError("유지 시간은 손 모양을 유지하는 동작에서만 바꿀 수 있어요.")
            low = hold_minimum(str(comp.get("base_posture", "")))
            seconds = round(max(low, min(HOLD_MAX, float(hold_seconds))), 2)
            if abs(float(comp.get("hold_seconds", .55)) - seconds) > 1e-6:
                comp["hold_seconds"] = seconds
                notes.append(f"유지 {seconds:g}초")
        if (atype, value) != (e["action_type"], e["action_value"]):
            if app:
                registry.add_app_override(key, app, atype, value)
            else:
                current = registry.get_binding(key)
                registry.set_binding(key, atype, value, enabled=bool(current.get("enabled", True)),
                                     track=current.get("track"))
            notes.append(format_action_badge(atype, value))
        else:
            registry.save()            # the hold time alone
        if not notes:
            return "바뀐 것이 없어요."
        entries[index] = {**e, "action_type": atype, "action_value": value, "after": format_action_badge(atype, value),
                          "steps": steps, "edited": time.strftime("%Y-%m-%d %H:%M")}
        self._save(entries)
        return f"고쳤어요: {e.get('gesture_name', key)} → " + " · ".join(notes)

    def delete(self, index: int, registry) -> str:
        """Remove the connection itself (not "go back one step"): a gesture the AI created is deleted, a program-specific
        command removed, an existing gesture goes back to its built-in command (2026-11-10: there was only 되돌리기)."""
        entries = self.load()
        if not 0 <= index < len(entries) or entries[index].get("undone"):
            raise ValueError("지울 항목이 없어요.")
        e = entries[index]
        key, app = e["gesture_key"], e.get("app_match", "")
        if still_in_force(e, registry, self.sequences_path, self.paths_path):
            if e.get("created") and e.get("sequence"):
                from manux.compat.hand_mouse.sequence import SequenceStore
                SequenceStore(self.sequences_path).remove(key)
            elif e.get("created") and e.get("path"):
                from manux.compat.hand_mouse.path_gesture import PathStore
                PathStore(self.paths_path).remove(key)
            elif e.get("created") and e.get("motion"):
                registry.delete_motion_gesture(key)
            elif e.get("created"):
                registry.delete_composite_gesture(key)
            elif app:
                registry.remove_app_override(key, app)
            else:
                registry.reset_binding(key)
                from manux.compat.hand_mouse.action_bindings import DEFAULT_GESTURE_BINDINGS
                if key not in DEFAULT_GESTURE_BINDINGS:          # no built-in command: nothing
                    registry.set_binding(key, "mouse_action", "none", track=registry.get_binding(key).get("track"))
        entries[index] = {**e, "undone": True}
        self._save(entries)
        return f"지웠어요: {e.get('gesture_name', key)}"

    def forget(self, index: int) -> None:
        """Only the list line (the connection changed later by other means)."""
        entries = self.load()
        if 0 <= index < len(entries):
            entries[index] = {**entries[index], "undone": True}
            self._save(entries)

    def undo(self, index: int, registry) -> str:
        entries = self.load()
        if not 0 <= index < len(entries) or entries[index].get("undone"):
            raise ValueError("되돌릴 항목이 없어요.")
        e = entries[index]
        if not still_in_force(e, registry, self.sequences_path, self.paths_path):
            raise ValueError("그 뒤에 다시 바뀌어서 되돌리지 않았어요 (지금 연결을 직접 확인해 주세요).")
        key, app, before = e["gesture_key"], e.get("app_match", ""), e.get("before") or {}
        if e.get("created") and e.get("sequence"):
            from manux.compat.hand_mouse.sequence import SequenceStore
            SequenceStore(self.sequences_path).remove(key)
        elif e.get("created") and e.get("path"):
            from manux.compat.hand_mouse.path_gesture import PathStore
            PathStore(self.paths_path).remove(key)
        elif e.get("created") and e.get("motion"):
            registry.delete_motion_gesture(key)
        elif e.get("created"):
            registry.delete_composite_gesture(key)
        elif app:
            if before:
                registry.add_app_override(key, app, before["action_type"], before["action_value"])
            else:
                registry.remove_app_override(key, app)
        else:
            registry.set_binding(key, before.get("action_type", "mouse_action"), before.get("action_value", "none"),
                                 enabled=bool(before.get("enabled", True)), track=registry.get_binding(key).get("track"))
        entries[index] = {**e, "undone": True}
        self._save(entries)
        return f"되돌렸어요: {e.get('gesture_name', key)}"


def still_in_force(e: Dict[str, Any], registry, sequences_path: Optional[Path] = None,
                   paths_path: Optional[Path] = None) -> bool:
    if e.get("created") and e.get("sequence"):
        from manux.compat.hand_mouse.sequence import SequenceStore
        if e["gesture_key"] not in SequenceStore(sequences_path).items:
            return False
    elif e.get("created") and e.get("path"):
        from manux.compat.hand_mouse.path_gesture import PathStore
        if e["gesture_key"] not in PathStore(paths_path).items:
            return False
    elif e.get("created") and e["gesture_key"] not in (registry.custom_motions if e.get("motion") else registry.composite_gestures):
        return False
    b = registry.get_binding(e["gesture_key"])
    app = e.get("app_match", "")
    if app:
        for ov in b.get("app_overrides") or []:
            if str(ov.get("app_match", "")).lower() == app.lower():
                return (ov.get("action_type"), ov.get("action_value")) == (e["action_type"], e["action_value"])
        return False
    return ((b.get("action_type"), b.get("action_value"), bool(b.get("enabled", True)))
            == (e["action_type"], e["action_value"], bool(e.get("enabled", True))))
