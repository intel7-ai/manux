"""UI logic without Qt: gesture cards, tutorial, finger HUD (ports of v0.3 gesture_guide/tutorial/finger_overlay)."""
from __future__ import annotations

from manux.actions import CATALOG
from manux.ui import cards
from manux.ui.finger import overlay_frame
from manux.ui.tutorial import ADVANCE_AFTER, MOVE_SECONDS, Tutorial

HAND = tuple((0.5 + 0.02 * (i % 5), 0.5 + 0.03 * (i // 5), 0.0) for i in range(21))


def test_cards_name_real_actions_and_switches_name_real_settings():
    from manux.app import schema
    known = schema()
    for card in cards.PALM_CARDS + cards.index_cards("trigger", "index"):
        for action in card["actions"]:
            assert action == cards.MOTION or action in CATALOG, action
    for key, names in cards.SWITCHES.items():
        assert names and all(name in known for name in names), key


def test_active_card_follows_the_frame_actions_and_motion_mode():
    palm = cards.cards(False)
    assert cards.active_key(palm, ["mouse.left_click"]) == "click"
    assert cards.active_key(palm, []) == ""
    assert cards.active_key(palm, ["mouse.move"], motion_on=True) == "motion"
    assert [c["key"] for c in cards.cards(True, "off")] == ["move"]


def test_card_switches_read_and_write_settings():
    values = {name: True for names in cards.SWITCHES.values() for name in names}
    assert cards.card_enabled(values.get, "drag")
    values["pinch.long_press_drag"] = False
    assert not cards.card_enabled(values.get, "drag")
    assert cards.switch_values("drag", True) == {"pinch.enable_thumb_index_middle_pinch": True,
                                                 "pinch.long_press_drag": True}
    assert cards.switch_values("move", False) == {}


def test_tutorial_moves_on_after_success_and_counts_movement_time():
    t = Tutorial(cards.cards(False))
    now, dt = 0.0, 1 / 30
    while t.index == 0:
        now += dt
        t.observe(["mouse.move"], False, now)
        t.tick(now)
        assert now < MOVE_SECONDS + ADVANCE_AFTER + 0.2
    assert t.done[0] and t.current["key"] == "click"
    assert t.observe(["mouse.left_click"], False, now + dt)   # the rising click counts
    assert t.done[1]
    assert not t.observe(["mouse.left_click"], False, now + 2 * dt)   # already succeeded: no second count
    t.next()
    t.back()
    assert t.index == 1 and t.succeeded_at is None


def test_tutorial_motion_step_needs_motion_mode():
    t = Tutorial([cards.find(cards.PALM_CARDS, "motion")])
    assert not t.observe(["nav.back"], False, 1.0)
    assert t.observe([], True, 1.1)


def test_finger_overlay_frame():
    frame = overlay_frame(HAND, mirrored=True, actions=["mouse.scroll"])
    assert frame["mode"] == "scroll" and len(frame["tips"]) == 5 and frame["contact"] in (-1, 1, 2, 3, 4)
    flipped = overlay_frame(HAND, mirrored=False)
    assert flipped["tips"][1][0] == -frame["tips"][1][0] and flipped["mode"] == "idle"
    assert overlay_frame(HAND[:20], True) is None
