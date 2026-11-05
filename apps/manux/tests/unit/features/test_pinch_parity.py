"""Parity: the ported pinch feature gives the same per-frame output as v0.3 `GestureEngine.process`.

v0.3 is loaded only here, by path (pinch_hands.v03_modules). Inputs are synthetic random walks over the
pinch poses (pinch_hands.pinch_sequence): no recorded data. Compared per frame with exact equality (the port
copies the arithmetic): every `GestureResult` field, the Activity inputs (v0.3 `_motion_blocked` read the same
private fields), and the intents (v0.3 results mapped by the same IntentMapper vs the feature's own output).

Hand loss: v0.3 reset itself inside `process(None)` after `hand_lost_reset`; in v0.4 the engine calls
`reset("hand_lost")`. The loop below does what the engine does, before the frame's step (pipeline order).
"""
from __future__ import annotations

import math
from collections import Counter

import pytest

from manux.features.pinch import IntentMapper, PinchFeature
from manux.features.pinch.settings import DEFAULTS

from pinch_hands import check_pairs, ctx, obs, pinch_sequence, v03_modules, v03_shipped_config

FIELDS = ("active_gesture", "action", "cursor_point", "pinch_distance", "distances", "is_moving", "pinch_point",
          "dpi_multiplier", "releasing", "hand_shape")
FRAMES = 3000

# v0.3 gestures.<id>.enabled -> v0.4 enable_<id>
ENABLE = ("fist", "no_pinch", "thumb_tucked_open", "thumb_middle_pinch", "thumb_ring_pinch",
          "thumb_index_middle_pinch", "thumb_pinky_pinch")

VARIANTS = {
    "shipped": {},
    "scroll_off": {"grab_scroll_enabled": False},
    "some_off": {"enable_fist": False, "enable_thumb_ring_pinch": False, "long_press_drag": False,
                 "shape_dpi_enabled": False, "side_pinch_exclusive": False},
}


def _v03_engine(v03, overrides):
    config = v03_shipped_config(v03.ge)
    for key, value in overrides.items():
        if key.startswith("enable_"):
            config["gestures"][key[len("enable_"):]]["enabled"] = value
        else:
            config[key] = value
    v03.profiles.RUN_OVERRIDE = None
    legacy = v03.ge.GestureEngine()
    legacy.config = config
    values = {key: config.get(key, default) for key, default in DEFAULTS.items()}
    values.update({f"enable_{g}": config["gestures"][g]["enabled"] for g in ENABLE})
    values["desktop_cooldown"] = config["gestures"]["thumb_tucked_open"]["cooldown"]
    return legacy, values, config


def _run(v03, seed, overrides, seen: Counter):
    legacy, values, config = _v03_engine(v03, overrides)
    feature = PinchFeature()
    mapper = IntentMapper()
    lost_since = None
    received = []
    for seq, (t, points) in enumerate(pinch_sequence(seed, FRAMES)):
        if points is None:
            lost_since = t if lost_since is None else lost_since
            if t - lost_since >= config["hand_lost_reset"]:
                feature.reset("hand_lost")          # the engine: release_all + reset, before the step
                mapper.clear()
        else:
            lost_since = None
        result = legacy.process(points, t)
        expected = mapper.map(t, result if points is not None else None)
        hand = obs(t, points, seq) if points is not None else None
        actual = feature.step(hand, ctx(t, settings=values, seq=seq))
        where = (seed, seq, t)
        if points is not None:
            frame = feature.last_frame
            for name in FIELDS:
                assert getattr(frame, name) == getattr(result, name), (where, name)
            assert (feature.core.last_hold, feature.core.last_pinky, feature.core.last_desktop) == (
                legacy.last_hold, legacy.last_pinky, legacy.last_desktop), where
            seen[(result.active_gesture, result.action, result.releasing)] += 1
        # Activity: what v0.3 `_motion_blocked` read from the engine's private fields
        pressing = legacy._index_pinched or bool(legacy._secondary_since)
        last = max(legacy._index_released_at, legacy._scroll_released_at, legacy._pinky_fired_at,
                   max(legacy._last_trigger_times.values(), default=-1e9))
        act = feature.activity()
        assert act.holding == (pressing or mapper.open is not None), where
        assert act.last_release_t == (last if last > -1e8 else -math.inf), where
        assert actual == expected, (where, result, feature.last_frame)
        received.extend(actual)
        if lost_since is not None and t - lost_since >= config["hand_lost_reset"]:
            received.clear()                        # the engine's release_all closed the open spans
        check_pairs(received)


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_pinch_replay_matches_v03(variant):
    seen: Counter = Counter()
    with v03_modules() as v03:
        for seed in range(4):
            _run(v03, seed, VARIANTS[variant], seen)
    if variant == "shipped":
        # the comparison is not vacuous: every v0.3 outcome the feature maps occurred
        for outcome in [("no_pinch", "move_cursor", False), ("fist", "move_cursor", False),
                        ("thumb_index_pinch", "scroll_grab", False), ("thumb_index_pinch", "scroll_grab", True),
                        ("thumb_index_middle_pinch", "drag_hold", False), ("thumb_middle_pinch", "drag_hold", False),
                        ("thumb_middle_pinch", "left_click", False), ("thumb_ring_pinch", "right_click", False),
                        ("thumb_pinky_pinch", "task_switcher", False), ("thumb_tucked_open", "show_desktop", False),
                        ("motion_pose", "none", False)]:
            assert seen[outcome] >= 1, (outcome, seen)
