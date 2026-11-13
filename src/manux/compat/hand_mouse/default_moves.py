"""The app's own moves that used to be fixed, now connectable like any other hand signal (2026-11-11, user: "지금 이 앱에서
기본적으로 설정한 움직임도 커스텀 가능하게 … 학습센터 5 동작 연결 안에서 … 기본 동작 명령이라는 걸 알 수 있게 태그").

Two kinds:
- HELD: a move that lasts while the hand holds it (the fist scrolling the page, the palm-mode drags and grab scroll).
  Left at its default it does what it always did. Connected to another command, that command runs ONCE when the hold
  starts and the move itself is off (no scroll, no drag). Switched off (or "none"), the hand shape does nothing.
- POINT: pointing mode's dwell clicks. Connected to another command, the dwell runs it instead of the click.
Moving the cursor stays fixed: it is what every other move rides on.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# held move -> its own (continuous) default
HELD: Dict[str, str] = {
    "vh_fist": "scroll_grab",             # virtual hand: fist holds the page
    "vh_drag": "drag_hold",               # virtual hand: thumb + index kept together = drag (2026-11-12)
    "thumb_index": "scroll_grab",         # palm mode: thumb + index grabs the page
    "thumb_middle_drag": "drag_hold",     # palm mode: thumb + middle held long = drag
    "thumb_index_middle": "drag_hold",    # palm mode: thumb + index + middle = drag
}
POINT: Dict[str, str] = {"point_double": "double_click", "point_right": "right_click"}
# mouse actions that only make sense while a hand shape is held: a held move keeps its own, nothing else takes them
CONTINUOUS = frozenset({"scroll_grab", "drag_hold", "move_cursor"})

NAMES: Dict[str, str] = {
    "vh_fist": "주먹 쥐고 움직이기",
    "vh_drag": "엄지+검지 붙인 채 유지",
    "thumb_index": "엄지+검지 집고 움직이기",
    "thumb_middle_drag": "엄지+중지 길게 집기",
    "thumb_index_middle": "엄지+검지+중지 함께 집기",
    "point_double": "엄지 위로 + 검지 비스듬히 (포인팅)",
    "point_right": "엄지 위로 + 검지·중지 붙여 비스듬히 (포인팅)",
}

# what the binding registry knows them by when nothing is saved (action_bindings.DEFAULT_GESTURE_BINDINGS)
DEFAULTS: Dict[str, Dict[str, Any]] = {
    "vh_fist": {"gesture_key": "vh_fist", "track": "virtual", "action_type": "mouse_action", "action_value": "scroll_grab",
                "enabled": True, "app_overrides": []},
    "vh_drag": {"gesture_key": "vh_drag", "track": "virtual", "action_type": "mouse_action", "action_value": "drag_hold",
                "enabled": True, "app_overrides": []},
    **{k: {"gesture_key": k, "track": "point", "action_type": "mouse_action", "action_value": v, "enabled": True,
           "app_overrides": []} for k, v in POINT.items()},
}


def check(key: str, action_type: str, action_value: str) -> str:
    """Why this command cannot be connected to `key` ("" = fine)."""
    if action_type != "mouse_action" or action_value not in CONTINUOUS:
        return ""
    if key in HELD and action_value == HELD[key]:
        return ""
    if key in HELD:
        return "이 동작에는 그 동작의 기본(" + ("페이지 스크롤" if HELD[key] == "scroll_grab" else "끌기") + \
               ")이나 한 번 실행되는 명령(단축키·클릭 등)만 연결할 수 있어요"
    if key in POINT:
        if action_value == "drag_hold":
            return ""            # 2026-11-12 ("끌기 바꾸기 만들기"): pointing may drag - see plan() "drag"
        return "포인팅에는 끌기나 한 번 실행되는 명령(클릭·단축키 등)만 연결할 수 있어요"
    return ""


def plan(key: str, resolved: Dict[str, Any]) -> str:
    """"native" (do the move as always), "off" (do nothing), "command" (run `resolved` instead) or, for a pointing
    click connected to the drag, "drag": resting on the file takes hold of it, letting the pointing hand go drops it."""
    atype, value = str(resolved.get("action_type", "")), str(resolved.get("action_value", ""))
    if atype == "none" or value in ("", "none"):
        return "off"
    if key in POINT and atype == "mouse_action" and value == "drag_hold":
        return "drag"
    if atype == "mouse_action" and value == (HELD.get(key) or POINT.get(key)):
        return "native"
    if atype == "mouse_action" and value in CONTINUOUS:
        return "native"          # (not allowed on save: check()) an old value never makes the move silently vanish
    return "command"


class HoldRemap:
    """Per held move: whether the move runs this frame, and the one frame its connected command fires.

    step(key, on, resolved, now, frames) -> (mode, fire): mode "native" | "off" | "command"; fire True on the frame the
    command runs - after `frames` frames of the hold in a row (the fist grip needs 5, like virtual_hand.Grip.START), and
    once per hold: it runs again only after the shape has been gone REARM seconds (a flickering fist is one hold)."""

    REARM = .4

    def __init__(self) -> None:
        self._run: Dict[str, int] = {}
        self._fired: Dict[str, bool] = {}
        self._seen: Dict[str, float] = {}

    def reset(self) -> None:
        self._run.clear()
        self._fired.clear()
        self._seen.clear()

    def step(self, key: str, on: bool, resolved: Optional[Dict[str, Any]], now: float, frames: int = 1) -> Tuple[str, bool]:
        if not on:
            self._run[key] = 0
            if now - self._seen.get(key, -1e9) > self.REARM:
                self._fired[key] = False
            return "native", False
        self._seen[key] = now
        self._run[key] = self._run.get(key, 0) + 1
        mode = plan(key, resolved or {})
        if mode != "command":
            return mode, False
        fire = self._run[key] >= max(1, int(frames)) and not self._fired.get(key, False)
        if fire:
            self._fired[key] = True
        return mode, fire
