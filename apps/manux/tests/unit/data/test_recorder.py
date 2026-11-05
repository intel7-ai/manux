"""Recorder (writer), guided sessions and rule replay, on synthetic data only (D-006)."""
from __future__ import annotations

import gzip
import json

import pytest

from manux.core.contracts import FrameInfo, HandObservation
from manux.data.guided import DEFAULT_SPECS, GestureSpec, GuidedSession, specs_for
from manux.data.recorder import SCHEMA, SessionRecorder, pc_label, public_source
from manux.data.recording import read, replay
from manux.data.rule_replay import RuleIntents, parse_rules, rule_intents

LM = tuple((0.4 + 0.01 * i, 0.5 - 0.005 * i, -0.001 * i) for i in range(21))
WORLD = tuple((0.001 * i, -0.002 * i, 0.0005 * i) for i in range(21))


def hand(t=0.0, side="Right", world=WORLD):
    return HandObservation(FrameInfo(seq=0, t=t, width=1280, height=720, mirrored=True), LM, side, 0.93, world=world)


def flat(points):
    return [v for p in points for v in p]


def lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(x) for x in f]


def test_round_trip_with_the_reader(tmp_path):
    camera = {"source": "0", "mode": [1280, 720, 30.0], "mirror": True, "note": "synthetic"}
    rec = SessionRecorder(tmp_path, "guided", {"camera": camera, "frame_aspect": 0.5625}, now=10.0,
                          alias="test-pc", stamp="20261105-120000")
    assert rec.part.exists() and not rec.path.exists()                 # .part while recording
    rec.frame(10.0, None, label={"target": "idle", "phase": "waiting"})
    rec.frame(10.033, hand(), label={"target": "thumb_ring", "gesture": "pinch.thumb_ring"},
              rule={"action": "right_click"}, cursor=(100, 200))
    rec.frame(10.066, hand(side="Left", world=None))
    rec.event(10.07, "mark")
    path = rec.close()
    assert rec.close() == path and rec.closed                           # idempotent
    assert path.name == "20261105-120000-test-pc-guided.jsonl.gz" and not rec.part.exists()

    raw = lines(path)
    assert raw[0]["schema"] == SCHEMA and raw[0]["pc"] == "test-pc" and raw[0]["pc_named"] == "alias"
    assert raw[2]["hand"] == ["Left", 0.93]                             # MediaPipe label on a mirrored camera
    assert raw[-1] == {"type": "end", "frames": 3, "counts": {"idle": 0, "no_hand": 1, "thumb_ring": 1, "none": 1}, "marks": 1} \
        or raw[-1]["counts"] == {"no_hand": 1, "thumb_ring": 1, "none": 1}

    got = read(path)
    assert got.complete and len(got.frames) == 3 and len(got.events) == 1
    frames = list(replay(got))
    assert frames[0].hand is None
    h = frames[1].hand
    assert h.handedness == "Right" and h.score == 0.93
    assert flat(h.landmarks) == pytest.approx(flat(LM), abs=1e-4) and flat(h.world) == pytest.approx(flat(WORLD), abs=1e-5)
    assert h.frame.t == pytest.approx(0.033) and frames[1].record["rule"]["action"] == "right_click"
    assert frames[1].record["cursor"] == [100, 200]
    assert frames[2].hand.handedness == "Left" and frames[2].hand.world is None


def test_unmirrored_camera_keeps_the_side(tmp_path):
    with SessionRecorder(tmp_path, "free", {"camera": {"mirror": False}}, now=0.0, alias="a") as rec:
        rec.frame(0.1, hand(side="Right"))
    assert lines(rec.path)[1]["hand"][0] == "Right"
    assert list(replay(read(rec.path)))[0].hand.handedness == "Right"


def test_an_error_leaves_only_the_part_file(tmp_path):
    with pytest.raises(RuntimeError):
        with SessionRecorder(tmp_path, "free", now=0.0, alias="a") as rec:
            rec.frame(0.1, hand())
            raise RuntimeError("camera lost")
    assert rec.part.exists() and not rec.path.exists()
    with pytest.raises(ValueError):
        rec.frame(0.2, hand())


def test_no_real_names(tmp_path):
    code = pc_label("", hostname="MY-REAL-PC")
    assert code.startswith("pc-") and "REAL" not in code and code == pc_label("", hostname="MY-REAL-PC")
    assert pc_label("home pc!/") == "homepc"
    rec = SessionRecorder(tmp_path, "free", {"pc": "MY-REAL-PC"}, now=0.0, hostname="MY-REAL-PC")
    path = rec.close()
    text = gzip.open(path, "rt", encoding="utf-8").read()
    assert "MY-REAL-PC" not in text and "MY-REAL-PC" not in path.name
    for bad in ({"username": "x"}, {"Hostname": "x"}):
        with pytest.raises(ValueError):
            SessionRecorder(tmp_path, "free", bad, now=0.0, alias="a")
    with pytest.raises(ValueError):
        SessionRecorder(tmp_path, "other", now=0.0, alias="a")
    # older recordings that kept a real name show only a code
    pc, note = public_source({"pc": "MY-REAL-PC", "camera": {"note": "MY-REAL-PC webcam"}})
    assert pc.startswith("pc-") and "MY-REAL-PC" not in note


# ---------------------------------------------------------------- guided sessions

def run_labels(session, until, step=0.1):
    out, t = [], 0.0
    while t < until:
        session.update(t)
        out.append(session.label(t))
        t = round(t + step, 6)
    return out


def test_default_specs_are_dans_three():
    assert [s.key for s in DEFAULT_SPECS] == ["thumb_ring", "thumb_index", "none"]
    assert DEFAULT_SPECS[2].free and DEFAULT_SPECS[0].gesture == "pinch.thumb_ring"
    session = GuidedSession()
    assert len(session.sets) == 9 and session.reps == 15


def test_any_declared_gesture_can_be_recorded():
    specs = [GestureSpec("thumb_pinky", "엄지+새끼", "붙였다 떼세요", gesture="pinch.thumb_pinky"),
             GestureSpec("swipe_left", "왼쪽 스와이프", "손을 왼쪽으로", gesture="motion.swipe_left"),
             GestureSpec("fist_hold", "주먹 유지", "주먹을 쥐고 계세요", free=True, gesture="fist.hold")]
    s = GuidedSession(specs, conditions=["앉아서", "누워서"],
                      config={"reps": 2, "prep": 0.5, "do": 1.0, "rest": 0.5, "free_seconds": 1.0})
    assert [(c, sp.key) for c, sp in s.sets] == [(0, "thumb_pinky"), (0, "swipe_left"), (0, "fist_hold"),
                                                  (1, "thumb_pinky"), (1, "swipe_left"), (1, "fist_hold")]
    assert s.label(0.0) == {"target": "idle", "phase": "waiting"}
    s.advance(0.0)
    seen = []
    for t10 in range(0, 41):
        t = t10 / 10
        if s.update(t):
            break
        seen.append(s.label(t))
    targets = [x["target"] for x in seen]
    assert set(targets) == {"transition", "thumb_pinky", "none"}
    assert {x["rep"] for x in seen} == {1, 2}
    assert all(x["gesture"] == "pinch.thumb_pinky" for x in seen if x["target"] == "thumb_pinky")
    assert not any("gesture" in x for x in seen if x["target"] != "thumb_pinky")
    assert s.index == 1 and not s.running and s.view(4.0)["waiting"]
    s.advance(10.0)
    s.update(14.0)
    s.advance(20.0)
    lab = s.label(20.5)
    assert lab["target"] == "fist_hold" and lab["phase"] == "free" and lab["gesture"] == "fist.hold"
    assert s.update(21.0) and s.index == 3
    assert s.describe()["specs"][0] == {"key": "thumb_pinky", "name": "엄지+새끼", "free": False, "gesture": "pinch.thumb_pinky"}


def test_session_until_done():
    s = GuidedSession([GestureSpec("a", "A", "do a")], conditions=["x"],
                      config={"rec_reps": 1, "rec_prep": 0.1, "rec_do": 0.1, "rec_rest": 0.1})  # v0.3 names
    assert s.reps == 1
    s.advance(0.0)
    assert not s.update(0.2) and s.update(0.31) and s.done
    assert s.label(1.0) == {"target": "idle", "phase": "waiting"} and s.view(1.0)["overall"] == 1.0


@pytest.mark.parametrize("bad", [
    lambda: GestureSpec("transition", "x", "x"),
    lambda: GestureSpec("Thumb", "x", "x"),
    lambda: GestureSpec("a", "x", "x", gesture="right_click"),
    lambda: GestureSpec("none", "x", "x"),                       # none must be free
    lambda: GestureSpec("none", "x", "x", free=True, gesture="pinch.none"),
    lambda: GuidedSession([]),
    lambda: GuidedSession([GestureSpec("a", "A", "a"), GestureSpec("a", "B", "b")]),
    lambda: GuidedSession([GestureSpec("a", "A", "a", gesture="p.x"), GestureSpec("b", "B", "b", gesture="p.x")]),
    lambda: GuidedSession(conditions=[]),
    lambda: GuidedSession(config={"reps": 0}),
])
def test_bad_specs(bad):
    with pytest.raises(ValueError):
        bad()


def test_focus():
    assert [s.key for s in specs_for("thumb_index")] == ["thumb_index"]
    assert specs_for("unknown") == DEFAULT_SPECS


# ---------------------------------------------------------------- rule replay

def test_rule_decisions_become_intents():
    actions = ["none", "right_click", "move_cursor", "scroll_grab", "scroll_grab", "none", "scroll_grab", "left_click"]
    out = [(i, [(x.gesture, x.phase) for x in xs]) for i, xs in
           rule_intents([{"t": i / 30, "rule": {"action": a}} for i, a in enumerate(actions)])]
    flat = [(i, g, p) for i, xs in out for g, p in xs]
    assert flat == [(1, "pinch.thumb_ring", "tap"), (3, "pinch.scroll", "begin"), (4, "pinch.scroll", "hold"),
                    (5, "pinch.scroll", "end"), (6, "pinch.scroll", "begin"), (7, "pinch.scroll", "end")]


def test_rule_map_from_the_command_line():
    rules = parse_rules(["left_click=pinch.thumb_middle", "drag_hold=pinch.drag:span"])
    conv = RuleIntents(rules)
    assert [(x.gesture, x.phase) for x in conv.step(0.0, "drag_hold")] == [("pinch.drag", "begin")]
    assert [(x.gesture, x.phase) for x in conv.finish(0.1)] == [("pinch.drag", "end")]
    with pytest.raises(ValueError):
        parse_rules(["oops"])
    with pytest.raises(ValueError):
        RuleIntents({"x": ("a.b", "press")})
