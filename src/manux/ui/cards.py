"""What the user can do with the current settings, in plain words (port of hand-mouse/src/gesture_guide.py).

One list feeds the main screen's gesture cards and the guided tutorial, so the two never disagree.
Each card names the v0.4 action ids (contracts section 7) that mean the user just did it; "motion"
is special: it is lit while motion mode is on. The text is the v0.3 text unchanged.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

Card = Dict[str, object]   # key, title, how, actions (tuple of action ids, or ("motion",))

MOTION = "motion"

PALM_CARDS: List[Card] = [   # the pinch feature (v0.3 "Dan's engine", the default)
    {"key": "move", "title": "커서 이동", "how": "손을 편 채 기본 속도로 움직이고, 주먹을 쥐거나 손을 접으면 빠르게 가속해 움직입니다.",
     "actions": ("mouse.move",)},
    {"key": "click", "title": "클릭", "how": "엄지 끝을 중지 끝에 붙였다 떼세요.", "actions": ("mouse.left_click",)},
    {"key": "right", "title": "우클릭", "how": "엄지 끝을 약지 끝에 붙였다 떼세요.", "actions": ("mouse.right_click",)},
    {"key": "scroll", "title": "스크롤", "how": "엄지와 검지를 붙인 채 위아래로 움직이세요.", "actions": ("mouse.scroll",)},
    {"key": "drag", "title": "드래그", "how": "엄지와 중지를 1초 붙이고 있다가, 붙인 채 움직이세요.",
     "actions": ("mouse.drag",)},
    {"key": "switch", "title": "창 고르기", "how": "엄지와 새끼를 붙여 창 목록을 엽니다.",
     "actions": ("window.task_switcher",)},
    {"key": "desktop", "title": "바탕화면", "how": "엄지를 손바닥 쪽으로 접고 네 손가락을 편 채 잠깐 멈추세요.",
     "actions": ("window.show_desktop",)},
    {"key": "motion", "title": "뒤로 · 탭 넘기기",
     "how": "엄지 척을 1초 하면 모션 모드가 켜집니다. 편 손을 좌우로 쓸면 뒤로/앞으로, 엄지를 접고 쓸면 탭을 넘깁니다.",
     "actions": (MOTION,)},
]


def index_cards(click_style: str, anchor: str) -> List[Card]:
    """The pointer feature (v0.3 index pointer)."""
    move = {"key": "move", "title": "커서 이동",
            "how": "손바닥이 따라갑니다." if anchor == "palm" else "검지만 펴고 움직이세요.",
            "actions": ("mouse.move",)}
    if click_style == "off":
        return [move]
    if click_style == "trigger":
        return [move,
                {"key": "click", "title": "클릭", "how": "검지를 편 채 엄지로 검지 옆을 짧게 누르세요.",
                 "actions": ("mouse.left_click",)},
                {"key": "right", "title": "우클릭", "how": "엄지로 누른 채 가만히 있으세요.", "actions": ("mouse.right_click",)},
                {"key": "drag", "title": "드래그", "how": "엄지로 누른 채 움직이세요.", "actions": ("mouse.drag",)}]
    return [move,
            {"key": "click", "title": "클릭", "how": "멈춘 뒤 엄지를 중지에 대세요.", "actions": ("mouse.left_click",)},
            {"key": "right", "title": "우클릭", "how": "멈춘 뒤 엄지를 약지에 대세요.", "actions": ("mouse.right_click",)}]


def cards(index_pointer: bool, click_style: str = "pinch", anchor: str = "index") -> List[Card]:
    return index_cards(click_style, anchor) if index_pointer else [dict(c) for c in PALM_CARDS]


def active_key(card_list: Sequence[Card], actions: Iterable[str], motion_on: bool = False) -> str:
    """Key of the card the user is doing right now ("" for none). `actions` = this frame's action ids."""
    if motion_on:
        actions = [MOTION]
    for action in actions:
        for card in card_list:
            if action in card["actions"]:
                return str(card["key"])
    return ""


def for_qml(card_list: Sequence[Card]) -> List[List[str]]:
    return [[str(c["key"]), str(c["title"]), str(c["how"])] for c in card_list]


# ------------------------------------------------------------------ turning a gesture on / off
# The v0.4 settings behind each palm card (v0.3 SWITCHES, renamed). A card is on when all of its
# settings are on; turning it off clears all of them. "move" has none: the cursor always moves.
SWITCHES: Mapping[str, Tuple[str, ...]] = {
    "click": ("pinch.enable_thumb_middle_pinch",),
    "right": ("pinch.enable_thumb_ring_pinch",),
    "scroll": ("pinch.grab_scroll_enabled",),
    "drag": ("pinch.enable_thumb_index_middle_pinch", "pinch.long_press_drag"),
    "switch": ("pinch.enable_thumb_pinky_pinch",),
    "desktop": ("pinch.enable_thumb_tucked_open",),
    "motion": ("motion.enabled",),
}


def card_enabled(get, key: str) -> bool:
    """`get(setting key)` -> value. True when every switch behind the card is on."""
    return all(bool(get(name)) for name in SWITCHES.get(key, ()))


def switch_values(key: str, enabled: bool) -> Dict[str, bool]:
    """The setting values that turn the card on or off ({} for a card that cannot be switched)."""
    return {name: bool(enabled) for name in SWITCHES.get(key, ())}


def find(card_list: Sequence[Card], key: str) -> Optional[Card]:
    return next((c for c in card_list if c["key"] == key), None)
