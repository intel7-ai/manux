"""tcn_gate as an IntentFilter (contracts section 6), with a fake predictor (no onnxruntime)."""
from __future__ import annotations

import json
import random
from types import SimpleNamespace

import numpy as np
import pytest

from manux.core.contracts import FrameContext, FrameInfo, Intent, IntentFilter, OutputTarget, Stage
from manux.data.rule_replay import RuleIntents
from manux.features.tcn_gate import LEGACY_GESTURES, SETTINGS, TcnGate, create_gate, gestures_for
from manux.ml import validate_card

CLASSES = ["none", "thumb_index", "thumb_ring"]
RING, INDEX = "pinch.thumb_ring", "pinch.scroll"
FPS = 30.0


def card(**changes):
    data = {"classes": CLASSES, "window": 15, "features": 133, "feature_version": "local3d-v1", "threshold": 0.7,
            "frames_in_a_row": 3, "trained": "x", "data": [], "held_out_results": []}
    data.update(changes)
    return validate_card(data, "local3d-v1", 133)


class FakeModel:
    """Says whatever class `self.cls` is (None = raise), with probability 1."""

    def __init__(self, cls=0, n=3):
        self.cls, self.n = cls, n
        self.classes = CLASSES[:n]
        self.windows = []

    def __call__(self, window):
        self.windows.append(window.shape)
        if self.cls is None:
            raise RuntimeError("inference failed")
        p = np.zeros(self.n)
        p[self.cls] = 1.0
        return p


def ctx(t):
    return FrameContext(FrameInfo(seq=int(round(t * FPS)), t=t, width=1280, height=720, mirrored=True),
                        Stage.MAIN, OutputTarget.WINDOWS, "pointer")


def tap(t, g=RING):
    return Intent(t=t, source=g.split(".")[0], gesture=g, phase="tap")


def span(t, phase, g=INDEX, point=(0.5, 0.5)):
    return Intent(t=t, source=g.split(".")[0], gesture=g, phase=phase, point=point)


class Runner:
    def __init__(self, model=None, **kw):
        self.model = model or FakeModel()
        self.gate = TcnGate(self.model, card(**kw.pop("card", {})), **kw)
        self.frame = 0

    def step(self, intents=(), cls=None):
        """One frame: observe (model output `cls` if given) then filter `intents(t)`."""
        t = self.frame / FPS
        self.frame += 1
        if cls is not None:
            self.model.cls = cls
        c = ctx(t)
        self.gate.observe(None, c)
        ins = [f(t) for f in intents]
        return [(i.gesture, i.phase) for i in self.gate.filter(ins, c)], ins

    def run(self, n, intents=(), cls=None):
        return [self.step(intents, cls)[0] for _ in range(n)]


def test_protocol_and_settings():
    gate = Runner().gate
    assert isinstance(gate, IntentFilter) and gate.id == "tcn_gate"
    assert dict(SETTINGS) == {"tcn_gate.wait": 0.3, "tcn_gate.lookback": 0.5}
    assert gate.threshold == 0.7 and gate.k == 3
    g = TcnGate(FakeModel(), card(), {"tcn_gate.threshold": 0.9, "tcn_gate.frames": 2, "tcn_gate.wait": 0.5})
    assert (g.threshold, g.k, g.wait) == (0.9, 2, 0.5)


def test_a_tap_needs_the_model():
    r = Runner()
    r.run(5, cls=0)
    out, _ = r.step([tap])
    assert out == []
    assert sum(r.run(20), []) == []                                # never confirmed -> dropped after wait
    assert r.gate.blocked == 1 and r.gate.status()["waiting"] == []

    r = Runner()
    r.run(5, cls=2)                                                 # model already sees thumb_ring
    assert r.step([tap])[0] == [(RING, "tap")]


def test_a_held_tap_goes_out_late_once_and_keeps_the_intent():
    r = Runner()
    r.run(3, cls=0)
    _, sent = r.step([tap])
    out = r.run(2, cls=2)                                           # 2 frames: not yet k=3
    assert out == [[], []]
    late = r.step(cls=2)
    assert late[0] == [(RING, "tap")]
    assert sum(r.run(10), []) == []                                 # only once
    # the late intent is the same intent with only t moved
    r2 = Runner()
    r2.run(3, cls=0)
    t0 = r2.frame / FPS
    original = tap(t0)
    r2.gate.observe(None, ctx(t0))
    assert r2.gate.filter([original], ctx(t0)) == []
    r2.frame += 1
    got = []
    for _ in range(3):
        t = r2.frame / FPS
        r2.frame += 1
        r2.model.cls = 2
        r2.gate.observe(None, ctx(t))
        got += r2.gate.filter([], ctx(t))
    assert len(got) == 1 and got[0].t == pytest.approx(t) and got[0].t != original.t
    assert (got[0].gesture, got[0].phase, got[0].point, dict(got[0].info)) == (original.gesture, original.phase, None, {})


def test_a_new_tap_replaces_the_waiting_one():
    r = Runner()
    r.run(3, cls=0)
    r.step([tap])
    r.step([tap])
    assert len(r.gate.status()["waiting"]) == 1
    assert sum(r.run(3, cls=2), []) == [(RING, "tap")]


def test_a_span_waits_then_runs_to_its_end():
    r = Runner()
    r.run(3, cls=0)
    assert r.step([lambda t: span(t, "begin")])[0] == []
    assert r.step([lambda t: span(t, "hold")])[0] == []                     # holds are not queued
    r.model.cls = 1
    assert r.step([lambda t: span(t, "hold")])[0] == []
    assert r.step([lambda t: span(t, "hold")])[0] == []
    assert r.step([lambda t: span(t, "hold")])[0] == [(INDEX, "begin"), (INDEX, "hold")]   # confirmed (3 frames)
    out = r.run(5, [lambda t: span(t, "hold")], cls=0)                       # model changes its mind: keeps running
    assert out == [[(INDEX, "hold")]] * 5
    assert r.step([lambda t: span(t, "end")])[0] == [(INDEX, "end")]
    assert r.step([lambda t: span(t, "hold")])[0] == []                     # no begin -> nothing


def test_an_unconfirmed_span_is_dropped_whole():
    r = Runner()
    r.run(3, cls=0)
    r.step([lambda t: span(t, "begin")])
    out = r.run(15, [lambda t: span(t, "hold")])                             # past wait (0.3 s)
    assert sum(out, []) == []
    r.model.cls = 1
    assert sum(r.run(5, [lambda t: span(t, "hold")]), []) == []             # too late: stays dropped
    assert r.step([lambda t: span(t, "end")])[0] == []
    assert r.gate.blocked == 1
    # an end before confirmation drops the begin too
    r = Runner()
    r.run(3, cls=0)
    r.step([lambda t: span(t, "begin")])
    assert r.step([lambda t: span(t, "end")])[0] == []
    assert sum(r.run(5, cls=1), []) == []


def test_other_gestures_pass_untouched():
    r = Runner()
    r.run(2, cls=0)
    move = lambda t: Intent(t=t, source="pointer", gesture="pointer.move", phase="hold", point=(0.1, 0.2))
    click = lambda t: Intent(t=t, source="pinch", gesture="pinch.thumb_middle", phase="tap")
    out, sent = r.step([move, click])
    assert out == [("pointer.move", "hold"), ("pinch.thumb_middle", "tap")]
    c = ctx(r.frame / FPS)
    ins = [move(c.frame.t)]
    assert list(r.gate.filter(ins, c))[0] is ins[0]


def test_inference_errors_count_as_not_confirmed():
    r = Runner(FakeModel(cls=None))
    r.run(5)
    assert r.gate.errors == 5 and r.gate.last is None
    assert r.step([tap])[0] == [] and sum(r.run(15), []) == []


def test_reset_clears_everything():
    r = Runner()
    r.run(5, cls=1)
    r.step([lambda t: span(t, "begin")])
    r.gate.reset("hand_lost")
    assert r.gate.status()["open"] == [] and r.gate.status()["waiting"] == []
    r.model.cls = 0
    assert r.step([lambda t: span(t, "hold")])[0] == []                       # nothing open after a reset


def test_the_card_decides_which_gestures_are_checked():
    assert gestures_for(card()) == dict(LEGACY_GESTURES)                     # Dan's card: legacy names
    custom = card(classes=["none", "fist", "swipe_left"], gestures={"swipe_left": "motion.swipe_left"})
    gate = TcnGate(FakeModel(n=3), custom)
    assert gate.checks == {"motion.swipe_left": 2}
    with pytest.raises(ValueError):
        TcnGate(FakeModel(), card(classes=["none", "a", "b"]))                 # no mapping at all


def test_the_gate_never_creates_or_unbalances_gestures():
    """Random intent streams and model outputs: output ⊆ input gestures, begin -> hold/end, no mutation."""
    rnd = random.Random(7)
    for trial in range(30):
        r = Runner()
        opened_in = False
        seen = set()
        out_open = False
        for i in range(200):
            t = r.frame / FPS
            ins = []
            if rnd.random() < 0.05:
                ins.append(tap(t))
            if not opened_in and rnd.random() < 0.05:
                ins.append(span(t, "begin"))
                opened_in = True
            elif opened_in:
                if rnd.random() < 0.1:
                    ins.append(span(t, "end"))
                    opened_in = False
                else:
                    ins.append(span(t, "hold", point=(rnd.random(), rnd.random())))
            r.model.cls = rnd.choice([0, 0, 1, 2])
            c = ctx(t)
            r.frame += 1
            r.gate.observe(None, c)
            frozen = [(x.gesture, x.phase, x.point, x.t) for x in ins]
            for x in ins:
                seen.add((x.gesture, x.phase, x.point))
            out = r.gate.filter(ins, c)
            assert [(x.gesture, x.phase, x.point, x.t) for x in ins] == frozen      # inputs unchanged
            for x in out:
                assert (x.gesture, x.phase, x.point) in seen                        # nothing new
                assert x.t == pytest.approx(t)
                if x.gesture == INDEX:
                    if x.phase == "begin":
                        assert not out_open
                        out_open = True
                    else:
                        assert out_open, (trial, i, x.phase)
                        if x.phase == "end":
                            out_open = False
            assert len(r.gate.status()["waiting"]) <= 2


def test_replaying_recorded_rule_decisions():
    """Rule decisions from a (synthetic) recording, through the gate, with a model that agrees on some."""
    # frames: (rule action, what the model would see)
    plan = ([("move_cursor", 0)] * 10 + [("right_click", 0)] + [("move_cursor", 0)] * 15     # misfire -> dropped
            + [("none", 2)] * 3 + [("right_click", 2)] + [("none", 0)] * 5                     # agreed -> passes
            + [("scroll_grab", 1)] * 8 + [("none", 0)] * 20                                    # agreed grab
            + [("scroll_grab", 0)] * 12 + [("none", 0)] * 3)                                   # unconfirmed grab
    frames = [{"t": i / FPS, "rule": {"action": a}} for i, (a, _) in enumerate(plan)]
    rules = RuleIntents()
    model = FakeModel()
    gate = TcnGate(model, card())
    got = []
    for f, (_, cls) in zip(frames, plan):
        model.cls = cls
        c = ctx(f["t"])
        gate.observe(None, c)
        got += [(round(x.t * FPS), x.gesture, x.phase) for x in gate.filter(rules.step(f["t"], f["rule"]["action"]), c)]
    taps = [g for g in got if g[2] == "tap"]
    assert taps == [(29, RING, "tap")]
    spans = [g for g in got if g[1] == INDEX]
    # the grab starts at frame 35; the model needs 3 frames, so it goes out (late) at 37 with that frame's hold
    assert spans[0] == (37, INDEX, "begin") and spans[-1] == (43, INDEX, "end")
    assert [p for _, _, p in spans] == ["begin"] + ["hold"] * 6 + ["end"]
    assert gate.blocked == 2


def test_create_gate_never_raises(tmp_path):
    assert create_gate(tmp_path / "missing.onnx").reason == "missing"
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    info = {"classes": CLASSES, "window": 15, "features": 133, "feature_version": "local3d-v1", "threshold": 0.7,
            "frames_in_a_row": 3, "trained": "x", "data": [], "held_out_results": []}
    (tmp_path / "m.json").write_text(json.dumps({**info, "feature_version": "local3d-v0"}), encoding="utf-8")
    assert create_gate(model).reason == "feature_version"

    class Session:
        def get_inputs(self):
            return [SimpleNamespace(name="x", shape=["batch", 15, 133])]

        def get_outputs(self):
            return [SimpleNamespace(name="logits", shape=["batch", 3])]

        def run(self, names, feeds):
            return [np.array([[5.0, 0.0, 0.0]])]

    (tmp_path / "m.json").write_text(json.dumps(info), encoding="utf-8")
    load = create_gate(model, session_factory=lambda p: Session())
    assert load.gate is not None and load.reason == "ok" and load.gate.card.kind == "base"
    assert set(load.gate.checks) == {RING, INDEX}
    (tmp_path / "m.json").write_text(json.dumps({**info, "classes": ["none", "a", "b"]}), encoding="utf-8")
    load = create_gate(model, session_factory=lambda p: Session())
    assert load.gate is None and load.reason == "no_gestures" and load.text == "모델이 확인할 동작이 없음"
    # without onnxruntime (CI) the reason says so; with it, the fake file is unreadable
    (tmp_path / "m.json").write_text(json.dumps(info), encoding="utf-8")
    assert create_gate(model).reason in ("runtime_missing", "unreadable")
