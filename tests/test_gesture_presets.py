"""The ready-made shortcuts and gesture recipes must all be usable as they are."""
import pyautogui
import pytest

from src.action_bindings import (DIRECTION_NAMES_KO, MOUSE_ACTION_LABELS, POSTURE_NAMES_KO, TRIGGER_NAMES_KO,
                                 WINDOWS_HOTKEY_PRESETS, YOUTUBE_KEY_PRESETS, format_hotkey_badge, parse_hotkey_keys)
from src.gesture_presets import HOTKEY_GROUPS, RECIPE_GROUPS, RECIPES, recipes_of

KNOWN_KEYS = set(pyautogui.KEYBOARD_KEYS)


def test_every_hotkey_preset_is_listed_once_in_a_known_group_and_uses_keys_pyautogui_knows():
    values = [p["value"] for p in WINDOWS_HOTKEY_PRESETS]
    assert len(values) == len(set(values))
    assert len(values) >= 100                      # the point of the exercise: many ready-made choices
    for p in WINDOWS_HOTKEY_PRESETS:
        assert p["group"] in HOTKEY_GROUPS, p
        assert p["label"] and p["badge"], p
        keys = parse_hotkey_keys(p["value"])
        assert keys, p
        bad = [k for k in keys if k not in KNOWN_KEYS]
        assert not bad, (p["value"], bad)
    assert set(HOTKEY_GROUPS) == {p["group"] for p in WINDOWS_HOTKEY_PRESETS}      # no empty group chip
    assert format_hotkey_badge("ctrl+r") == "Ctrl + R"


def test_every_youtube_and_media_key_is_a_key_pyautogui_knows():
    for name, preset in YOUTUBE_KEY_PRESETS.items():
        assert preset["label"] and preset["badge"], name
        assert all(k in KNOWN_KEYS for k in preset["keys"]), (name, preset["keys"])
    for name in ("media_next", "media_prev", "media_stop", "yt_miniplayer"):
        assert name in YOUTUBE_KEY_PRESETS


def _action_exists(kind, value):
    if kind == "hotkey":
        return all(k in KNOWN_KEYS for k in parse_hotkey_keys(value))
    if kind == "youtube_key":
        return value in YOUTUBE_KEY_PRESETS
    if kind == "mouse_action":
        return value in MOUSE_ACTION_LABELS
    return False


def test_every_recipe_fills_the_form_with_choices_that_exist():
    assert len(RECIPES) >= 45
    for r in RECIPES:
        assert r["group"] in RECIPE_GROUPS, r
        assert r["name"], r
        assert _action_exists(r["action_type"], r["action_value"]), r
        assert r["posture"] in POSTURE_NAMES_KO and r["posture"] != "any", r
        if r["kind"] == "motion":
            assert r["direction"] in DIRECTION_NAMES_KO, r
        else:
            assert r["trigger"] in TRIGGER_NAMES_KO, r
    assert set(RECIPE_GROUPS) == {r["group"] for r in RECIPES}


def test_no_two_motion_recipes_share_a_hand_shape_and_direction_and_none_clashes_with_a_built_in_motion():
    pairs = [(r["posture"], r["direction"]) for r in RECIPES if r["kind"] == "motion"]
    assert len(pairs) == len(set(pairs))
    built_in = {("open_hand", "left"), ("open_hand", "right"), ("open_hand", "down"),
                ("thumb_tucked", "left"), ("thumb_tucked", "right")}
    assert not built_in & set(pairs)


def test_recipes_that_reuse_a_built_in_sign_carry_a_warning_and_the_others_do_not():
    for r in RECIPES:
        if r["kind"] != "motion":
            continue
        assert bool(r["note"]) == (r["posture"] in ("v_sign", "rock_sign", "thumbs_up")), r


def test_recipes_get_stable_ids_per_kind():
    motion, composite = recipes_of("motion"), recipes_of("composite")
    assert {r["id"] for r in motion}.isdisjoint({r["id"] for r in composite})
    assert motion[0]["id"] == "motion-0" and composite[0]["id"] == "composite-0"
