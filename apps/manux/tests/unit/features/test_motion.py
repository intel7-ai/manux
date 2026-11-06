"""Feature `motion`: parity with v0.3 `MotionMode`, mode handling through the engine's Arbiter, contract rules."""
from __future__ import annotations

import math

import pytest

from manux.actions.bindings import resolve_bindings
from manux.core.contracts import Activity, Feature
from manux.engine.arbiter import Arbiter
from manux.features.motion import (
    DEFAULT_BINDINGS, ENTER, EXIT, GESTURE_PHASES, GESTURES, SETTINGS, SWIPE_LEFT, SWIPE_LEFT_TUCKED, SWIPE_RIGHT,
    SWIPE_RIGHT_TUCKED, MotionFeature,
)
from manux.features.motion.detector import MotionCore

from pinch_hands import ctx, motion_hand, obs, swipe



class Driver:
    """The engine's part for one feature: the Arbiter decides the mode from motion.enter / motion.exit."""

    def __init__(self, feature=None, settings=None):
        self.f = feature or MotionFeature()
        self.arbiter = Arbiter("pinch")
        self.settings = settings or {}
        self.others = {}

    def step(self, t, hand):
        intents = list(self.f.step(obs(t, hand) if hand is not None else None,
                                   ctx(t, mode=self.arbiter.mode, settings=self.settings, others=self.others)))
        _, changes = self.arbiter.handle(t, intents, {**self.others, "motion": self.f.activity()})
        return intents, changes

    def run(self, hands, t0=0.0, fps=30):
        out, t = [], t0
        for h in hands:
            out.extend(self.step(t, h)[0])
            t += 1 / fps
        return out, t


def entered(d=None):
    d = d or Driver()
    out, t = d.run([motion_hand(fingers="folded")] * 30)
    assert [i.gesture for i in out] == [ENTER] and d.arbiter.mode == "motion"
    return d, t


def taps(intents):
    return [i.gesture for i in intents]


def test_declarations_fit_the_contract_and_the_engine():
    f = MotionFeature()
    assert isinstance(f, Feature) and f.modes == frozenset({"pointer", "pinch", "motion"})
    assert ENTER not in GESTURES and EXIT not in GESTURES          # mode requests are consumed by the engine
    assert set(GESTURES) == set(GESTURE_PHASES) == set(DEFAULT_BINDINGS)
    bindings, problems = resolve_bindings(DEFAULT_BINDINGS, gesture_phases=GESTURE_PHASES)
    assert not problems
    assert {g: b.action for g, b in bindings.items()} == {
        SWIPE_LEFT: "nav.back", SWIPE_RIGHT: "nav.forward", SWIPE_LEFT_TUCKED: "tab.prev", SWIPE_RIGHT_TUCKED: "tab.next"}


# ----------------------------------------------------------------------------- behaviour
def test_thumbs_up_asks_to_enter_and_swipes_follow():
    d, t = entered()
    out, t = d.run(swipe(0.7, 0.4), t0=t)
    assert taps(out) == [SWIPE_LEFT] and out[0].phase == "tap" and out[0].point is None
    out, t = d.run([motion_hand(cx=0.4)] * 40 + swipe(0.4, 0.7), t0=t)
    assert taps(out) == [SWIPE_RIGHT]
    out, t = d.run([motion_hand(cx=0.7, thumb="tucked")] * 40 + swipe(0.7, 0.4, thumb="tucked"), t0=t)
    assert taps(out) == [SWIPE_LEFT_TUCKED]
    assert d.f.activity().holding is False and d.f.activity().last_release_t > 0


def test_swipes_are_ignored_outside_motion_mode():
    d = Driver()
    out, _ = d.run(swipe(0.3, 0.7) * 3)
    assert out == [] and d.arbiter.mode == "pinch"


def test_a_refused_entry_starts_the_hold_over():
    d = Driver()
    d.others = {"pinch": Activity(holding=True)}
    out, t = d.run([motion_hand(fingers="folded")] * 30)
    assert taps(out) == [ENTER] and d.arbiter.mode == "pinch"      # asked, the arbiter refused
    out, t = d.run([motion_hand(fingers="folded")] * 20, t0=t)
    assert out == []                                                # a new 0.8 s hold, not an immediate retry
    d.others = {"pinch": Activity(last_release_t=-math.inf)}
    out, t = d.run([motion_hand(fingers="folded")] * 10, t0=t)
    assert taps(out) == [ENTER] and d.arbiter.mode == "motion"


def test_the_pose_held_again_asks_to_exit():
    d, t = entered()
    out, t = d.run([motion_hand(fingers="folded")] * 60, t0=t)      # still the pose that switched it on
    assert out == []
    out, t = d.run([motion_hand()] * 6 + [motion_hand(fingers="folded")] * 30, t0=t)
    assert taps(out) == [EXIT] and out[0].info["why"] == "pose" and d.arbiter.mode == "pinch"


def test_a_ui_request_switches_without_intents():
    d = Driver()
    d.arbiter.request("motion")
    out, t = d.run([motion_hand()] * 3)
    assert out == [] and d.f.core.active
    d.arbiter.request(None)
    out, _ = d.run([motion_hand()] * 3, t0=t)
    assert out == [] and not d.f.core.active


def test_disabled_motion_never_enters_and_leaves_if_on():
    d = Driver(settings={"enabled": False})
    out, _ = d.run([motion_hand(fingers="folded")] * 40)
    assert out == []
    d, t = entered()
    d.settings = {"enabled": False}
    out, _ = d.run([motion_hand()], t0=t)
    assert taps(out) == [EXIT] and d.arbiter.mode == "pinch"


# ----------------------------------------------------------------------------- contract rules
def test_no_hand_keeps_the_mode_and_hand_lost_reset_keeps_it_too():
    d, t = entered()
    out, t = d.run([None] * 150, t0=t)                               # 5 s without a hand: it is a toggle
    assert out == [] and d.f.core.active
    d.f.reset("hand_lost")
    out, t = d.run([motion_hand(cx=0.7)] * 20 + swipe(0.7, 0.4), t0=t)
    assert d.f.core.active and taps(out) == [SWIPE_LEFT]


def test_reset_clears_the_state():
    f = MotionFeature()
    for i in range(10):
        f.step(obs(i / 30, motion_hand(fingers="folded")), ctx(i / 30))
    assert f.core._enter_since is not None
    f.reset("shutdown")
    assert f.core._enter_since is None and not f.core.active and f.activity() == Activity()
    d, t = entered()
    d.f.reset("stage_change")                                        # the mode is the arbiter's: stays on, fresh
    assert d.f.core.active and not d.f.core._hist and d.f.core._exit_armed is False
