"""The pose guard inside the real Engine: events reach the Snapshot, nothing is sent when it vetoes, and a
silent or failing assist leaves every output exactly as it was (the off / transparency regression)."""
from __future__ import annotations

import random
import sys
from dataclasses import asdict
from pathlib import Path
from typing import List

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perception"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features"))
import pose_hands as H   # noqa: E402
from test_pose import AGAINST_RING, CLASSES, MIDDLE, RING, SAYS_INDEX, SAYS_RING, SCROLL, FakeModel, make_card, probs  # noqa: E402

from manux.actions.bindings import Binding  # noqa: E402
from manux.actions.router import Router  # noqa: E402
from manux.core.contracts import Activity, FrameInfo, HandObservation, Intent, OutputResult, OutputTarget, Stage  # noqa: E402
from manux.engine.commands import SetControl, SetStage  # noqa: E402
from manux.engine.pipeline import Engine, EngineConfig  # noqa: E402
from manux.features.pose import PoseFeature, PoseGuard  # noqa: E402

FPS = 30.0
WORLD = tuple(tuple(p) for p in H.make_hand())


class Sink:
    def __init__(self, target):
        self.target = target
        self.applied: List[tuple] = []
        self.releases: List[str] = []

    def apply(self, cmd):
        self.applied.append((cmd.action, cmd.phase))
        return OutputResult(True)

    def release_all(self, reason):
        self.releases.append(reason)

    def close(self):
        pass


class Script:
    """A rule feature that emits what the test queues (`pinch` rules: taps and a scroll grab)."""

    id = "pinch"
    modes = frozenset({"pinch"})
    gestures = (RING, MIDDLE, SCROLL)

    def __init__(self):
        self.queue = []

    def step(self, hand, ctx):
        out, self.queue = self.queue, []
        return [Intent(t=ctx.frame.t, source="pinch", gesture=g, phase=p) for g, p in out]

    def activity(self):
        return Activity()

    def reset(self, reason):
        self.queue = []


class DirectScript(Script):
    """Stands in for the direct TCN feature: source `tcn`, a drag span of its own."""

    id = "tcn"
    modes = frozenset({"pinch"})
    gestures = ("custom.drag",)

    def step(self, hand, ctx):
        out, self.queue = self.queue, []
        return [Intent(t=ctx.frame.t, source="tcn", gesture=g, phase=p) for g, p in out]


def frame(k):
    return FrameInfo(seq=k, t=k / FPS, width=1280, height=720, mirrored=True)


def hand(f, world=True):
    return HandObservation(f, [(0.5, 0.5, 0.0)] * 21, "Right", 0.9, world=WORLD if world else None)


class Rig:
    def __init__(self, pose=True, model=None, **guard_settings):
        self.rule = Script()
        self.direct = DirectScript()
        self.model = model or FakeModel()
        self.sinks = {t: Sink(t) for t in OutputTarget}
        features, filters = [self.rule, self.direct], []
        if pose:
            self.feature = PoseFeature(self.model, make_card(), 0.9)
            self.guard = PoseGuard(self.feature, guard_settings)
            features.append(self.feature)
            filters.append(self.guard)
        router = Router({RING: Binding("mouse.right_click"), MIDDLE: Binding("mouse.left_click"),
                         SCROLL: Binding("mouse.scroll"), "custom.drag": Binding("mouse.drag")})
        self.engine = Engine(features, filters, router, self.sinks, settings_for=lambda fid: {},
                             config=EngineConfig(base_mode="pinch"))
        self.engine.commands.put(SetStage(Stage.MAIN))
        self.engine.commands.put(SetControl(True))
        self.k = 0
        self.step()                                  # enter main with control on
        self.sinks[OutputTarget.WINDOWS].applied.clear()

    def step(self, intents=(), out=None, with_hand=True, world=True, direct=()):
        self.k += 1
        if out is not None:
            self.model.out = out
        self.rule.queue = list(intents)
        self.direct.queue = list(direct)
        f = frame(self.k)
        return self.engine.step(f, hand(f, world) if with_hand else None)

    @property
    def sent(self):
        return self.sinks[OutputTarget.WINDOWS].applied


# ---------------------------------------------------------------- the guard in the engine

def test_a_veto_shows_up_in_the_snapshot_and_nothing_reaches_the_output():
    rig = Rig()
    for _ in range(3):
        rig.step(out=AGAINST_RING)
    snap = rig.step([(RING, "tap")], out=AGAINST_RING)
    assert "pose:veto:pinch.thumb_ring:tap" in snap.events
    assert snap.intents == () and snap.results == () and not snap.errors
    assert rig.sent == []


def test_an_agreeing_pose_lets_the_right_click_through():
    rig = Rig()
    for _ in range(3):
        rig.step(out=SAYS_RING)
    snap = rig.step([(RING, "tap")], out=SAYS_RING)
    assert snap.events == () and snap.intents == ((RING, "tap"),)
    assert rig.sent == [("mouse.right_click", "tap")]


def test_a_vetoed_scroll_grab_sends_no_scroll_at_all():
    rig = Rig(veto_gestures=f"{RING},{SCROLL}")
    for _ in range(3):
        rig.step(out=AGAINST_RING)
    for phase in ("begin", "hold", "hold", "end"):
        rig.step([(SCROLL, phase)], out=AGAINST_RING)
    assert rig.sent == []


def test_rest_evidence_drops_a_direct_tcn_begin_with_its_hold_and_end_but_rule_taps_pass():
    rig = Rig()
    for _ in range(3):
        rig.step(out=AGAINST_RING)                            # the pose says "no gesture" for k frames
    snaps = [rig.step(direct=[("custom.drag", phase)], out=AGAINST_RING) for phase in ("begin", "hold", "end")]
    assert "pose:idle:custom.drag:begin" in snaps[0].events and rig.sent == []
    rig.step([(MIDDLE, "tap")], out=AGAINST_RING)             # a rule tap is not a `tcn` intent: it passes
    assert rig.sent == [("mouse.left_click", "tap")]
    for _ in range(3):
        rig.step(out=SAYS_RING)                               # evidence changes: the next direct begin passes
    rig.step(direct=[("custom.drag", "begin")], out=SAYS_RING)
    assert ("mouse.drag", "begin") in rig.sent


def test_the_pose_feature_is_listed_but_never_holds_and_cannot_change_modes():
    rig = Rig()
    snap = rig.step(out=SAYS_RING)
    assert ("pose_assist", False) in snap.activities and snap.mode == "pinch"


def test_a_model_that_fails_every_frame_never_stops_a_frame():
    rig = Rig(model=FakeModel())
    rig.model.out = None
    for _ in range(5):
        snap = rig.step([(RING, "tap")], out=None)
        assert not snap.errors
    assert rig.feature.errors == 5 and rig.sent.count(("mouse.right_click", "tap")) == 5


# ---------------------------------------------------------------- off / transparency regression

def _random_run(rig: Rig, seed: int, frames: int = 120):
    rng = random.Random(seed)
    options = [(RING, "tap"), (MIDDLE, "tap"), (SCROLL, "begin"), (SCROLL, "hold"), (SCROLL, "end")]
    snaps = []
    for _ in range(frames):
        intents = [rng.choice(options) for _ in range(rng.randint(0, 2))]
        snaps.append(rig.step(intents, with_hand=rng.random() < 0.9, world=rng.random() < 0.95))
    return snaps


def _comparable(snap):
    data = asdict(snap)
    data["activities"] = tuple(a for a in data["activities"] if a[0] != "pose_assist")
    return data


@pytest.mark.parametrize("silent_model", ["below_threshold", "always_failing", "no_class_agrees_enough"])
def test_a_silent_or_failing_assist_changes_nothing(silent_model):
    """Same frames, same intents: with the assist attached but never speaking, every Snapshot field, every
    command and every release equals the engine built without it (only the extra activity row differs)."""
    base = Rig(pose=False)
    model = FakeModel()
    model.out = {"below_threshold": probs(thumb_ring=0.6, none=0.4), "always_failing": None,
                 "no_class_agrees_enough": np.full(len(CLASSES), 1.0 / len(CLASSES))}[silent_model]
    assisted = Rig(pose=True, model=model)
    for seed in (1, 2, 3):
        a, b = _random_run(base, seed), _random_run(assisted, seed)
        assert [_comparable(x) for x in a] == [_comparable(x) for x in b]
    assert base.sinks[OutputTarget.WINDOWS].applied == assisted.sinks[OutputTarget.WINDOWS].applied
    assert base.sinks[OutputTarget.WINDOWS].releases == assisted.sinks[OutputTarget.WINDOWS].releases
    assert (assisted.feature.errors > 0) == (silent_model == "always_failing")


def test_the_regression_test_can_fail_a_speaking_assist_does_change_the_output():
    """Guard against a vacuous comparison: an assist that does speak must differ from the engine without it."""
    base, assisted = Rig(pose=False), Rig(pose=True)
    assisted.model.out = AGAINST_RING
    a, b = _random_run(base, 1), _random_run(assisted, 1)
    assert [_comparable(x) for x in a] != [_comparable(x) for x in b]
    assert any(e.startswith("pose:") for snap in b for e in snap.events)
    assert len(base.sent) > len(assisted.sent)


# ---------------------------------------------------------------- the optional filter hook

class Noisy:
    id = "noisy"

    def observe(self, hand, ctx):
        pass

    def filter(self, intents, ctx):
        return intents

    def drain_events(self):
        return ["noisy:one", "noisy:two"]

    def reset(self, reason):
        pass


class Silent:
    id = "silent"

    def observe(self, hand, ctx):
        pass

    def filter(self, intents, ctx):
        return intents

    def reset(self, reason):
        pass


def test_a_filters_notes_reach_the_snapshot_events_and_a_filter_without_notes_is_fine():
    rig = Rig(pose=False)
    rig.engine.filters = [Noisy(), Silent()]
    snap = rig.step()
    assert snap.events == ("noisy:one", "noisy:two")


def test_a_filter_that_raises_while_giving_notes_is_contained_like_any_filter_error():
    class Bad(Noisy):
        def drain_events(self):
            raise RuntimeError("boom")

    rig = Rig(pose=False)
    rig.engine.filters = [Bad()]
    snap = rig.step([(MIDDLE, "tap")])
    assert [e.source for e in snap.errors] == ["noisy"] and snap.intents == ()
