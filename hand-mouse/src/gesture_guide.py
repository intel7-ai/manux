"""
What the user can do with the current settings, in plain words (2026-11-04).

One list feeds both the main screen's gesture cards and the guided onboarding, so the two never
disagree. Each card names the engine actions (`GestureResult.action`) that mean the user just did
it: the main screen lights the card up, the onboarding counts it as a success.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

Card = Dict[str, object]   # key, title, how, actions (tuple of engine action ids)

PALM_CARDS: List[Card] = [   # Dan's engine: the default
    {"key": "move", "title": "커서 이동", "how": "손을 편 채 움직이세요. 주먹을 쥘수록 빨라집니다.",
     "actions": ("move_cursor",)},
    {"key": "click", "title": "클릭", "how": "엄지 끝을 중지 끝에 붙였다 떼세요.",
     "actions": ("left_click",)},
    {"key": "right", "title": "우클릭", "how": "엄지 끝을 약지 끝에 붙였다 떼세요.",
     "actions": ("right_click",)},
    {"key": "scroll", "title": "스크롤", "how": "엄지와 검지를 붙인 채 위아래로 움직이세요.",
     "actions": ("scroll_grab", "grab")},
    {"key": "drag", "title": "드래그", "how": "엄지와 중지를 1초 붙이고 있다가, 붙인 채 움직이세요.",
     "actions": ("drag_hold", "drag_toggle")},
    {"key": "switch", "title": "창 고르기", "how": "엄지와 새끼를 붙여 창 목록을 열고, 가리킨 뒤 엄지+중지로 엽니다.",
     "actions": ("task_switcher", "alt_tab")},
    {"key": "desktop", "title": "바탕화면", "how": "엄지를 손바닥 쪽으로 접고 네 손가락을 편 채 잠깐 멈추세요.",
     "actions": ("show_desktop",)},
    {"key": "motion", "title": "뒤로 · 탭 넘기기", "how": "엄지 척을 1초 하면 모션 모드가 켜집니다. 편 손을 좌우로 쓸면 뒤로/앞으로, 엄지를 접고 쓸면 탭을 넘깁니다.",
     "actions": ("motion",)},
]


def index_cards(click_style: str, anchor: str) -> List[Card]:
    """Index-pointer engine (experimental alternative in settings)."""
    move = {"key": "move", "title": "커서 이동",
            "how": "손바닥이 따라갑니다." if anchor == "palm" else "검지만 펴고 움직이세요.",
            "actions": ("move_cursor",)}
    if click_style == "off":
        return [move]
    if click_style == "trigger":
        return [move,
                {"key": "click", "title": "클릭", "how": "검지를 편 채 엄지로 검지 옆을 짧게 누르세요.", "actions": ("left_click",)},
                {"key": "right", "title": "우클릭", "how": "엄지로 누른 채 가만히 있으세요.", "actions": ("right_click",)},
                {"key": "drag", "title": "드래그", "how": "엄지로 누른 채 움직이세요.", "actions": ("drag_hold",)}]
    return [move,
            {"key": "click", "title": "클릭", "how": "멈춘 뒤 엄지를 중지에 대세요.", "actions": ("left_click",)},
            {"key": "right", "title": "우클릭", "how": "멈춘 뒤 엄지를 약지에 대세요.", "actions": ("right_click",)}]


def cards(index_pointer: bool, click_style: str = "pinch", anchor: str = "index") -> List[Card]:
    return index_cards(click_style, anchor) if index_pointer else [dict(c) for c in PALM_CARDS]


def active_key(card_list: Sequence[Card], action: str, motion_on: bool = False) -> str:
    """Key of the card the user is doing right now ("" for none)."""
    if motion_on:
        action = "motion"
    for card in card_list:
        if action in card["actions"]:
            return str(card["key"])
    return ""


def for_qml(card_list: Sequence[Card]) -> List[List[str]]:
    return [[str(c["key"]), str(c["title"]), str(c["how"])] for c in card_list]


def find(card_list: Sequence[Card], key: str) -> Optional[Card]:
    return next((c for c in card_list if c["key"] == key), None)
