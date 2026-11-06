"""Direct decisions on synthetic hands. This measures behavior, not recognition accuracy."""
from pathlib import Path

import numpy as np
import pytest

from manux.app import build_engine, schema
from manux.core.contracts import FrameContext, FrameInfo, HandObservation, OutputTarget, Stage
from manux.engine.commands import SetBaseMode, SetBusy, SetStage
from manux.features.tcn import TcnFeature
from manux.ml.card import validate_card
from manux.ml.predictor import LoadedModel
from manux.settings.store import Settings


def card(**changes):
    raw = dict(classes=["none", "ring", "scroll"], window=1, features=133, feature_version="local3d-v1",
               threshold=.7, frames_in_a_row=2, trained="synthetic", data=[], held_out_results=[],
               kind="personal", gestures={"ring": "pinch.thumb_ring", "scroll": "pinch.scroll"},
               decisions={"ring": {"kind": "tap", "modes": ["pinch"], "point": "none", "frames_in_a_row": 3},
                          "scroll": {"kind": "span", "modes": ["pinch"], "point": "palm"}})
    raw.update(changes)
    return validate_card(raw, "local3d-v1", 133)


class Predictor:
    def __init__(self, model):
        self.classes = model.classes
        self.name = "none"
        self.error = None
        self.calls = 0

    def __call__(self, window):
        self.calls += 1
        if self.error:
            raise self.error
        row = np.full(len(self.classes), .025)
        row[self.classes.index(self.name)] = 1 - .025 * (len(self.classes) - 1)
        return row


def hand(t, seq=0):
    frame = FrameInfo(seq, t, 640, 480, True)
    lm = tuple((.4 + i * .005, .6 - i * .005, 0.) for i in range(21))
    world = [(i % 5 * .01, i // 5 * .01, i % 2 * .001) for i in range(21)]
    world[0], world[9], world[5], world[17] = (0., 0., 0.), (0., .1, 0.), (-.04, .08, 0.), (.04, .08, 0.)
    return HandObservation(frame, lm, "Right", .95, tuple(world))


class Run:
    def __init__(self, model=None):
        self.card = model or card()
        self.predictor = Predictor(self.card)
        self.feature = TcnFeature(self.predictor, self.card)
        self.t = 0.

    def step(self, name="none", *, missing=False, output=OutputTarget.PRACTICE, mode="pinch", dt=.03):
        self.t += dt
        h = hand(self.t)
        self.predictor.name = name
        return self.feature.step(None if missing else h, FrameContext(h.frame, Stage.TUTORIAL, output, mode))

    def repeat(self, name, n):
        return [i for _ in range(n) for i in self.step(name)]


@pytest.mark.parametrize("policy", ["rules", "shadow"])
def test_non_applied_gesture_has_no_intents_activity_or_authority(policy):
    run = Run()
    run.feature = TcnFeature(run.predictor, run.card, {"pinch.thumb_ring": policy, "pinch.scroll": "apply"})
    run.repeat("none", 2)
    assert run.repeat("ring", 10) == []
    assert not run.feature.activity().holding
    assert run.feature.authoritative == ("pinch.scroll",)
    assert run.feature.last_prediction[0] == "ring"
    run.repeat("none", 2)
    assert [i.phase for i in run.repeat("scroll", 2)] == ["begin"]


def test_continuous_training_and_live_inference_receive_identical_windows():
    from manux.perception.temporal import inputs
    run = Run(card(window=3, training_pipeline="continuous-v2"))
    frames, observed = [], []
    original = run.predictor
    def infer(window):
        observed.append(window.copy())
        return original(window)
    run.feature.predictor = infer
    for i, t in enumerate([.03, .06, .09, .12, .15, .18, .21, .24, .7, .73, .76]):
        h = hand(t)
        frame = {"t": t, "world": h.world, "lm": h.landmarks,
                 "label": {"target": "none" if i < 3 else "ring", "phase": "prep" if i < 3 else "do"}}
        if i == 4:
            frame.pop("world")
        frames.append(frame)
        run.feature.step(None if i == 4 else h, FrameContext(h.frame, Stage.TUTORIAL, OutputTarget.PRACTICE, "pinch"))
    windows, valid = inputs(frames, .75, 3)
    assert list(np.flatnonzero(valid)) == [2, 3, 7, 10]
    np.testing.assert_allclose(np.array(observed), windows[valid])


def test_runtime_policy_switch_releases_open_span_and_changes_only_authority(tmp_path):
    from types import SimpleNamespace
    from manux.core.contracts import RuntimeRequest
    from manux.desktop import DesktopRuntime
    from manux.settings.tcn_policy import GesturePolicies
    model = card(model_sha256="a" * 64, class_default_modes={"ring": "shadow", "scroll": "shadow"})
    predictor = Predictor(model)
    loaded = LoadedModel(Path("synthetic.onnx"), model, predictor)
    policies = GesturePolicies(tmp_path / "policies.json")
    settings = Settings(schema())
    engine = build_engine(settings, "pinch", direct_model=loaded, policies=policies.modes(model.raw))
    runtime = DesktopRuntime(engine, SimpleNamespace(close=lambda: None), settings, policy_store=policies, current_model=loaded)
    try:
        assert engine.gesture_sources == {}
        policies.set(model.raw, "pinch.scroll", "apply", "a" * 64)
        runtime.request(RuntimeRequest("tcn_policy"))
        runtime._drain_runtime_requests()
        assert engine.gesture_sources == {"pinch.scroll": "tcn"}
        for i, name in enumerate(["none", "none", "scroll", "scroll", "scroll"]):
            predictor.name = name
            h = hand(1 + i * .03)
            engine.step(h.frame, h)
        feature = engine.features[-1]
        assert feature.activity().holding
        assert "TCN 적용 예측" in runtime._model_view_status()
        policies.set(model.raw, "pinch.scroll", "rules", "a" * 64)
        runtime.request(RuntimeRequest("tcn_policy"))
        runtime._drain_runtime_requests()
        assert engine.gesture_sources == {} and not feature.activity().holding
        assert not engine.sink().holding()
        assert runtime.current_model is loaded
    finally:
        runtime.stop()


def test_tap_requires_neutral_then_class_specific_count_and_rearms_only_on_neutral():
    run = Run()
    assert run.repeat("ring", 8) == []
    assert run.repeat("none", 2) == []
    assert run.repeat("ring", 2) == []
    events = run.repeat("ring", 1)
    assert [(i.source, i.gesture, i.phase) for i in events] == [("tcn", "pinch.thumb_ring", "tap")]
    assert run.repeat("ring", 10) == []
    assert run.repeat("scroll", 4) == []
    run.repeat("none", 2)
    assert len(run.repeat("ring", 3)) == 1


def test_span_pairs_release_on_loss_and_need_neutral_before_restart():
    run = Run()
    run.repeat("none", 2)
    assert [i.phase for i in run.repeat("scroll", 3)] == ["begin", "hold"]
    assert run.feature.activity().holding
    assert [i.phase for i in run.step(missing=True)] == ["end"]
    assert not run.feature.activity().holding
    assert run.repeat("scroll", 4) == []
    run.repeat("none", 2)
    assert [i.phase for i in run.repeat("scroll", 2)] == ["begin"]
    assert [i.phase for i in run.step("ring")] == ["end"]


@pytest.mark.parametrize("interruption", ["pause", "gap", "stale", "mode", "reset"])
def test_interruptions_disarm_a_previously_armed_feature(interruption):
    run = Run()
    run.repeat("none", 2)
    if interruption == "pause":
        run.step("ring", output=OutputTarget.NONE)
    elif interruption == "gap":
        run.step("ring", dt=1.)
    elif interruption == "stale":
        run.step("ring", dt=0.)
    elif interruption == "mode":
        run.step("ring", mode="motion")
    else:
        run.feature.reset("settings")
    assert run.repeat("ring", 6) == []


def test_class_specific_threshold_can_reject_a_confident_global_prediction():
    raw = dict(card().raw)
    raw["decisions"] = {"ring": {"kind": "tap", "modes": ["pinch"], "point": "none", "threshold": .99},
                        "scroll": {"kind": "span", "modes": ["pinch"], "point": "palm"}}
    run = Run(validate_card(raw, "local3d-v1", 133))
    run.repeat("none", 2)
    assert run.repeat("ring", 8) == []


def test_worldless_hand_does_not_query_the_model_or_arm_it():
    run = Run()
    original = hand(1.)
    worldless = HandObservation(original.frame, original.landmarks, "Right", .9)
    ctx = FrameContext(original.frame, Stage.TUTORIAL, OutputTarget.PRACTICE, "pinch")
    assert run.feature.step(worldless, ctx) == [] and run.predictor.calls == 0


def test_engine_routes_model_span_once_and_releases_on_inference_error_and_busy():
    model = card()
    predictor = Predictor(model)
    loaded = LoadedModel(Path("synthetic.onnx"), model, predictor)
    engine = build_engine(Settings(schema()), "pinch", direct_model=loaded)
    try:
        for i, name in enumerate(["none", "none", "scroll", "scroll", "scroll"]):
            predictor.name = name
            h = hand(1 + i * .03, i)
            snap = engine.step(h.frame, h)
        assert snap.results == (("mouse.scroll", "hold", "ok"),)
        predictor.error = RuntimeError("synthetic failure")
        h = hand(1.16)
        snap = engine.step(h.frame, h)
        assert any(e.source == "tcn" for e in snap.errors)
        assert not engine.sink().holding()
        predictor.error = None
        engine.commands.put(SetStage(Stage.MAIN))
        engine.commands.put(SetBusy("recording", True))
        h = hand(1.2)
        snap = engine.step(h.frame, h)
        assert snap.output is OutputTarget.NONE
        assert not any(i[0] == "pinch.scroll" for i in snap.intents)
    finally:
        engine.shutdown()


def test_model_claims_do_not_fall_back_to_a_rule():
    from manux.core.contracts import Activity, Intent
    from manux.engine.pipeline import Engine
    from manux.actions.router import Router
    from manux.output.null import NullSink
    from manux.output.practice import PracticeSink
    from manux.actions.bindings import Binding

    class Rule:
        id = "pinch"
        modes = frozenset({"pinch"})
        gestures = ("pinch.thumb_ring",)
        def step(self, hand, ctx):
            return [Intent(ctx.frame.t, self.id, self.gestures[0], "tap")]
        def reset(self, reason): pass
        def activity(self): return Activity()

    class Rejecting(Rule):
        id = "tcn"
        def step(self, hand, ctx): return []

    engine = Engine([Rule(), Rejecting()], [], Router({"pinch.thumb_ring": Binding("mouse.right_click")}),
                    {OutputTarget.NONE: NullSink(), OutputTarget.PRACTICE: PracticeSink()},
                    lambda _: {}, gesture_sources={"pinch.thumb_ring": "tcn"})
    engine.commands.put(SetStage(Stage.TUTORIAL))
    engine.commands.put(SetBaseMode("pinch"))
    h = hand(1.)
    assert engine.step(h.frame, h).results == ()
    engine.shutdown()


@pytest.mark.parametrize("bound", [False, True])
def test_custom_gesture_uses_only_an_explicit_action_binding(bound):
    model = card(gestures={"ring": "custom.rock", "scroll": "pinch.scroll"})
    predictor = Predictor(model)
    engine = build_engine(Settings(schema()), "pinch", direct_model=LoadedModel(Path("synthetic"), model, predictor),
                          binding_overrides={"custom.rock": "nav.back"} if bound else None)
    try:
        found = []
        for i, name in enumerate(["none", "none", "ring", "ring", "ring", "ring"]):
            predictor.name = name
            h = hand(1 + i * .03)
            found.append(engine.step(h.frame, h))
        assert sum(("custom.rock", "tap") in s.intents for s in found) == 1
        assert found[-1].results == ()  # a held model tap cannot leak a rule cursor or another action
        if bound:
            assert sum(("nav.back", "tap", "ok") in s.results for s in found) == 1
        else:
            assert sum(("custom.rock", "unbound") in s.unrouted for s in found) == 1
            assert not any(a == "nav.back" for s in found for a, _, _ in s.results)
    finally:
        engine.shutdown()
