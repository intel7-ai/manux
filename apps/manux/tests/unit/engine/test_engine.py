"""Engine pipeline, arbiter and stage/output rules, with fake features and sinks."""
from __future__ import annotations

from typing import List, Optional

import pytest

from manux.actions.bindings import Binding
from manux.actions.router import Router
from manux.core.contracts import (
    Activity, FrameInfo, HandObservation, Intent, OutputResult, OutputTarget, Stage,
)
from manux.engine.commands import Mark, RequestMode, SetBaseMode, SetBusy, SetControl, SetStage
from manux.engine.pipeline import Engine, EngineConfig
from manux.engine.state import EngineState, output_target

HAND_PTS = tuple((0.5, 0.5, 0.0) for _ in range(21))


def frame(seq: int, t: float) -> FrameInfo:
    return FrameInfo(seq=seq, t=t, width=640, height=480, mirrored=True)


def hand(f: FrameInfo) -> HandObservation:
    return HandObservation(f, HAND_PTS, "Right", 0.9)


class FakeSink:
    def __init__(self, target):
        self.target = target
        self.applied: List[tuple] = []
        self.releases: List[str] = []
        self.ticks = 0
        self.closed = False

    def apply(self, cmd):
        self.applied.append((cmd.action, cmd.phase))
        return OutputResult(True)

    def release_all(self, reason):
        self.releases.append(reason)

    def tick(self, now):
        self.ticks += 1

    def close(self):
        self.closed = True


class Scripted:
    """A feature that emits whatever the test queues for the next frame."""

    def __init__(self, fid, modes, gestures):
        self.id, self.modes, self.gestures = fid, frozenset(modes), tuple(gestures)
        self.queue: List[tuple] = []
        self.holding = False
        self.last_release = float("-inf")
        self.resets: List[str] = []
        self.seen: List[Optional[HandObservation]] = []
        self.raise_next = False
        self.last_ctx = None

    def step(self, hand, ctx):
        self.seen.append(hand)
        self.last_ctx = ctx
        if self.raise_next:
            self.raise_next = False
            raise RuntimeError("boom")
        out, self.queue = self.queue, []
        return [Intent(t=ctx.frame.t, source=self.id, gesture=g, phase=p) for g, p in out]

    def activity(self):
        return Activity(self.holding, self.last_release)

    def reset(self, reason):
        self.resets.append(reason)


def build(base="pointer"):
    pointer = Scripted("pointer", {"pointer"}, ["pointer.left_click", "pointer.drag"])
    motion = Scripted("motion", {"pointer", "pinch", "motion"}, ["motion.swipe_left"])
    sinks = {t: FakeSink(t) for t in OutputTarget}
    router = Router({"pointer.left_click": Binding("mouse.left_click"), "pointer.drag": Binding("mouse.drag"),
                     "motion.swipe_left": Binding("nav.back")})
    eng = Engine([pointer, motion], [], router, sinks, settings_for=lambda fid: {"x": 1},
                 config=EngineConfig(base_mode=base))
    return eng, pointer, motion, sinks


def go_main(eng, t=0.5):
    """Main stage with control on, then forget the resets/releases that switching caused."""
    eng.commands.put(SetStage(Stage.MAIN))
    eng.commands.put(SetControl(True))
    eng.step(frame(0, t), hand(frame(0, t)))
    for f in eng.features:
        f.resets.clear()
        f.seen.clear()
    for s in eng.sinks.values():
        s.releases.clear()
        s.ticks = 0


# ---------------------------------------------------------------- state

def test_only_main_with_control_on_reaches_windows():
    assert output_target(EngineState(Stage.TUTORIAL)) is OutputTarget.PRACTICE
    assert output_target(EngineState(Stage.MAIN)) is OutputTarget.NONE
    assert output_target(EngineState(Stage.MAIN, control_on=True)) is OutputTarget.WINDOWS
    assert output_target(EngineState(Stage.MAIN, True).with_busy("recording", True)) is OutputTarget.NONE
    with pytest.raises(ValueError):
        EngineState().with_busy("napping", True)


def test_engine_needs_a_none_sink_and_unique_ids():
    with pytest.raises(ValueError):
        Engine([], [], Router({}), {}, settings_for=lambda f: {})
    f = Scripted("a", {"pointer"}, [])
    with pytest.raises(ValueError):
        Engine([f, f], [], Router({}), {OutputTarget.NONE: FakeSink(OutputTarget.NONE)}, settings_for=lambda x: {})


# ---------------------------------------------------------------- routing and output

def test_intents_route_to_the_current_targets_sink():
    eng, pointer, motion, sinks = build()
    go_main(eng)
    f = frame(0, 1.0)
    pointer.queue = [("pointer.left_click", "tap")]
    snap = eng.step(f, hand(f))
    assert sinks[OutputTarget.WINDOWS].applied == [("mouse.left_click", "tap")]
    assert snap.output is OutputTarget.WINDOWS and snap.results == (("mouse.left_click", "tap", "ok"),)
    assert sinks[OutputTarget.WINDOWS].ticks == 1


def test_tutorial_goes_to_practice_and_splash_to_none():
    eng, pointer, _, sinks = build()
    eng.commands.put(SetStage(Stage.TUTORIAL))
    f = frame(0, 1.0)
    pointer.queue = [("pointer.left_click", "tap")]
    eng.step(f, hand(f))
    assert sinks[OutputTarget.PRACTICE].applied and not sinks[OutputTarget.WINDOWS].applied


def test_unbound_and_wrong_phase_are_reported_not_executed():
    eng, pointer, _, sinks = build()
    go_main(eng)
    f = frame(0, 1.0)
    pointer.queue = [("pointer.drag", "tap")]          # mouse.drag has no tap
    snap = eng.step(f, hand(f))
    assert snap.unrouted == (("pointer.drag", "phase_mismatch"),) and not sinks[OutputTarget.WINDOWS].applied


def test_feature_settings_and_others_reach_the_context():
    eng, pointer, motion, _ = build()
    motion.holding = True
    f = frame(0, 1.0)
    eng.step(f, hand(f))
    assert pointer.last_ctx.settings == {"x": 1}
    assert pointer.last_ctx.others == {"motion": Activity(True, float("-inf"))}
    assert "pointer" not in pointer.last_ctx.others


# ---------------------------------------------------------------- safety

def test_stage_change_and_pause_release_first():
    eng, pointer, _, sinks = build()
    go_main(eng)
    f = frame(0, 1.0)
    eng.step(f, hand(f))
    eng.commands.put(SetControl(False))
    eng.step(frame(1, 1.03), hand(frame(1, 1.03)))
    assert sinks[OutputTarget.WINDOWS].releases[-1] == "pause" and pointer.resets[-1] == "pause"
    eng.commands.put(SetStage(Stage.CAPTURE))
    eng.step(frame(2, 1.06), None)
    assert pointer.resets[-1] == "stage_change"


def test_recording_turns_output_off():
    eng, pointer, _, sinks = build()
    go_main(eng)
    eng.commands.put(SetBusy("recording", True))
    f = frame(0, 1.0)
    pointer.queue = [("pointer.left_click", "tap")]
    snap = eng.step(f, hand(f))
    assert snap.output is OutputTarget.NONE and not sinks[OutputTarget.WINDOWS].applied


def test_hand_loss_releases_once_after_the_grace_time():
    eng, pointer, _, sinks = build()
    go_main(eng)
    eng.step(frame(0, 1.0), hand(frame(0, 1.0)))
    eng.step(frame(1, 1.1), None)
    eng.step(frame(2, 1.3), None)
    assert "hand_lost" not in pointer.resets           # a 0.2 s loss: a drag survives
    snap = eng.step(frame(3, 1.45), None)
    assert "hand_lost" in snap.events and sinks[OutputTarget.WINDOWS].releases == ["hand_lost"]
    eng.step(frame(4, 1.6), None)
    assert sinks[OutputTarget.WINDOWS].releases == ["hand_lost"]   # only once
    assert pointer.seen[-1] is None                    # features still see hand=None


def test_a_raising_feature_is_contained():
    eng, pointer, motion, sinks = build()
    go_main(eng)
    pointer.raise_next = True
    motion.queue = [("motion.swipe_left", "tap")]
    f = frame(0, 1.0)
    snap = eng.step(f, hand(f))
    assert snap.errors and snap.errors[0].source == "pointer"
    assert pointer.resets == ["error"] and "error" in sinks[OutputTarget.WINDOWS].releases
    assert ("nav.back", "tap") in sinks[OutputTarget.WINDOWS].applied     # the other feature still works


def test_undeclared_gestures_are_refused():
    eng, pointer, _, sinks = build()
    go_main(eng)
    pointer.gestures = ("pointer.left_click",)
    pointer.queue = [("pointer.drag", "begin")]
    snap = eng.step(frame(0, 1.0), hand(frame(0, 1.0)))
    assert snap.errors and not sinks[OutputTarget.WINDOWS].applied


# ---------------------------------------------------------------- arbiter

def test_motion_enter_switches_mode_and_resets_the_base_feature():
    eng, pointer, motion, sinks = build()
    go_main(eng)
    motion.queue = [("motion.enter", "tap")]
    snap = eng.step(frame(0, 5.0), hand(frame(0, 5.0)))
    assert snap.mode == "motion" and pointer.resets == ["mode_change"]
    assert "mode_change" in sinks[OutputTarget.WINDOWS].releases
    assert not snap.unrouted                          # mode intents are never routed
    eng.step(frame(1, 5.03), hand(frame(1, 5.03)))
    assert len(pointer.seen) == 1                     # pointer is not stepped in motion mode
    motion.queue = [("motion.exit", "tap")]
    assert eng.step(frame(2, 5.06), hand(frame(2, 5.06))).mode == "pointer"


def test_motion_enter_is_refused_while_another_feature_holds_or_just_released():
    eng, pointer, motion, _ = build()
    go_main(eng)
    pointer.holding = True
    motion.queue = [("motion.enter", "tap")]
    snap = eng.step(frame(0, 5.0), hand(frame(0, 5.0)))
    assert snap.mode == "pointer" and any("refused:pointer holding" in e for e in snap.events)
    pointer.holding, pointer.last_release = False, 4.6
    motion.queue = [("motion.enter", "tap")]
    assert eng.step(frame(1, 5.1), hand(frame(1, 5.1))).mode == "pointer"
    motion.queue = [("motion.enter", "tap")]
    assert eng.step(frame(2, 5.7), hand(frame(2, 5.7))).mode == "motion"


def test_ui_mode_request_and_base_mode():
    eng, pointer, motion, _ = build()
    eng.commands.put(RequestMode("motion"))
    assert eng.step(frame(0, 1.0), None).mode == "motion"
    eng.commands.put(RequestMode(None))
    eng.commands.put(SetBaseMode("pinch"))
    assert eng.step(frame(1, 1.03), None).mode == "pinch"


def test_mark_and_shutdown():
    eng, _, _, sinks = build()
    eng.commands.put(Mark("wrong click"))
    assert "mark:wrong click" in eng.step(frame(0, 1.0), None).events
    eng.shutdown()
    assert all(s.closed for s in sinks.values()) and "shutdown" in sinks[OutputTarget.NONE].releases


def test_camera_timeout_releases_output_and_resets_filter_once():
    eng, pointer, _, sinks = build()

    class Filter:
        def __init__(self):
            self.reasons = []

        def reset(self, reason):
            self.reasons.append(reason)

    flt = Filter()
    eng.filters.append(flt)
    eng.commands.put(SetStage(Stage.MAIN))
    eng.commands.put(SetControl(True))
    eng.frame_missing(1.0)
    eng.frame_missing(1.2)
    eng.frame_missing(1.31)
    eng.frame_missing(1.6)
    assert flt.reasons.count("hand_lost") == 1
    assert pointer.resets.count("hand_lost") == 1
    assert sinks[OutputTarget.WINDOWS].releases == ["hand_lost"]
