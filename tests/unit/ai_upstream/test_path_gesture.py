# Original a700452 synthetic unit tests; host runtime tests live in test_ai_integration.py.
"""Shape-drawing gestures (src/path_gesture.py): a hand shape held while the palm draws, e.g. a fist drawing a circle."""
import math

import pytest

from manux.compat.hand_mouse import llm_binding as lb
from manux.compat.hand_mouse.action_bindings import ActionBindingRegistry
from manux.compat.hand_mouse.draw_mode import ShapeStore
from manux.compat.hand_mouse.path_gesture import PathDetector, PathStore

FIST_CIRCLE = {"path_fist_circle": ("fist", "draw_circle")}


@pytest.fixture
def shapes(tmp_path):
    return ShapeStore(tmp_path / "shapes.json")


def circle(n=45, r=1.5, start=-math.pi / 2):
    return [(5 + r * math.cos(start + 2 * math.pi * i / (n - 1)), 5 + r * math.sin(start + 2 * math.pi * i / (n - 1)))
            for i in range(n)]


def run(det, shapes, plan, gestures=FIST_CIRCLE, t=0.0):
    """plan: [(posture, (x, y) or None)] one per frame at 30 fps; returns the fired keys."""
    fired = []
    for posture, xy in plan:
        k = det.step(t, posture, xy, 1.0 if xy else 0.0, gestures, shapes)
        if k:
            fired.append(k)
        t += 1 / 30
    return fired


def test_a_fist_drawing_a_circle_fires_when_the_fist_opens_or_stops(shapes):
    pts = circle()
    assert run(PathDetector(), shapes, [("fist", p) for p in pts] + [("open_hand", pts[-1])] * 8) == ["path_fist_circle"]
    still = [("fist", pts[-1])] * 20                                     # keeps the fist, stops moving
    assert run(PathDetector(), shapes, [("fist", p) for p in pts] + still) == ["path_fist_circle"]
    # a blink of no shape inside the stroke is forgiven
    blinky = [("" if 10 <= i < 13 else "fist", p) for i, p in enumerate(pts)]
    assert run(PathDetector(), shapes, blinky + [("open_hand", pts[-1])] * 8) == ["path_fist_circle"]


def test_other_shapes_hands_and_lines_do_not_fire(shapes):
    pts = circle()
    line = [(5 + i * .1, 5) for i in range(45)]
    assert run(PathDetector(), shapes, [("fist", p) for p in line] + [("open_hand", line[-1])] * 8) == []
    assert run(PathDetector(), shapes, [("v_sign", p) for p in pts] + [("open_hand", pts[-1])] * 8) == []
    z = [(5, 5), (7, 5), (5, 7), (7, 7)]
    zig = [(a[0] + (b[0] - a[0]) * k / 15, a[1] + (b[1] - a[1]) * k / 15) for a, b in zip(z, z[1:]) for k in range(15)]
    assert run(PathDetector(), shapes, [("fist", p) for p in zig] + [("open_hand", zig[-1])] * 8) == []
    small = circle(r=.2)                                                  # a twitch, not a drawing
    assert run(PathDetector(), shapes, [("fist", p) for p in small] + [("open_hand", small[-1])] * 8) == []


def test_the_store_and_the_ai_create_list_and_undo_a_fist_circle(tmp_path):
    store = PathStore(tmp_path / "p.json")
    key = store.add("", "fist", "draw_circle")
    with pytest.raises(ValueError):
        store.add("", "fist", "draw_circle")
    with pytest.raises(ValueError):
        store.add("", "dance", "draw_circle")
    assert store.remove(key)
    reg = ActionBindingRegistry(tmp_path / "b.json")
    history = lb.History(tmp_path / "ai.json", paths_path=tmp_path / "p.json")
    answer = {"summary": "", "questions": [], "proposals": [{
        "gesture_key": lb.NEW, "new_name": "", "new_posture": "fist", "new_trigger": "draw_circle", "new_sequence": [],
        "app_match": "", "action_type": "hotkey", "action_value": "f5", "steps": [], "enabled": True, "reason": ""}]}
    row = lb.check(answer, lb.gesture_list(reg))["proposals"][0]
    assert row["ok"] and row["checked"], row                              # deliberate: no everyday-shape warning
    lb.apply([row], reg, history=history, paths=store)
    key = next(iter(store.items))
    assert store.items[key] == {"name": store.items[key]["name"], "posture": "fist", "shape": "draw_circle", "enabled": True}
    assert reg.get_binding(key)["action_value"] == "f5"
    entry = history.rows(reg)[0]
    assert entry["current"] and entry["path"]
    history.delete(entry["index"], reg)
    assert PathStore(tmp_path / "p.json").items == {}


def test_a_circle_shown_with_a_fist_offers_the_fist_circle():
    reg_gestures = [{"key": "thumb_ring", "name": "엄지+약지"}]
    shown = {"state": "done", "kind": "drawn", "shape_key": "draw_circle", "shape_name": "원", "score": .9,
             "posture": "fist", "name": "주먹 쥐기", "share": .9, "gesture_key": "draw_circle"}
    block = lb.context_block(shown, "", reg_gestures)
    assert '"fist"' in block and '"draw_circle"' in block and lb.NEW in block




def test_a_thumbs_up_turned_like_a_dial_is_a_rotation_in_the_probe_and_fires_its_gesture(shapes):
    from manux.compat.hand_mouse.pose_probe import PoseProbe
    probe, t = PoseProbe(), 0.0
    probe.COUNTDOWN = 0.0
    probe.start(t)
    for i in range(60):                                   # the thumb turns from up (90) to the right (0): clockwise
        a = 90 - 90 * min(1.0, i / 40)
        probe.step(t, "thumbs_up", (5.0, 5.0), 1.0, (90.0, a))
        t += 1 / 30
    probe.finish_if_due(t + 1)
    view = probe.view(t + 1)
    assert view["kind"] == "rotate" and view["rotate"] == "rotate_cw" and view["degrees"] >= 80, view
    block = lb.context_block(view, "", [])
    assert '"rotate_cw"' in block and '"thumbs_up"' in block
    det, t = PathDetector(), 0.0
    gestures = {"path_up_cw": ("thumbs_up", "rotate_cw")}
    fired = []
    for i in range(50):
        k = det.step(t, "thumbs_up", (5.0, 5.0), 1.0, gestures, shapes, angle=90 - 90 * min(1.0, i / 30))
        fired += [k] if k else []
        t += 1 / 30
    assert fired == ["path_up_cw"]
    det, t, fired = PathDetector(), 0.0, []
    for i in range(50):                                   # the other way round: not this gesture
        k = det.step(t, "thumbs_up", (5.0, 5.0), 1.0, gestures, shapes, angle=90 + 90 * min(1.0, i / 30))
        fired += [k] if k else []
        t += 1 / 30
    assert fired == []


def test_a_knife_hand_slash_that_comes_back_is_a_diagonal_stroke_and_motions_accept_the_new_shapes():
    """2026-11-10: '손날 긋기' (out and back) read as an unknown curve; motion mode did not know the new shapes."""
    from manux.compat.hand_mouse.pose_probe import PoseProbe
    from manux.compat.hand_mouse.motion_mode import custom_posture_ok, hand_features
    probe, t = PoseProbe(), 0.0
    probe.COUNTDOWN = 0.0
    probe.start(t)
    for i in range(60):                                   # out to the lower left and back
        u = i / 30 if i < 30 else (60 - i) / 30
        probe.step(t, "knife_hand", (5 - 2.5 * u, 5 + 2.5 * u), 1.0, (90.0, 90.0))
        t += 1 / 30
    probe.finish_if_due(t + 1)
    v = probe.view(t + 1)
    assert v["kind"] == "motion" and v["direction"] == "diag_down_left" and v["posture"] == "knife_hand", v
    from tests.unit.ai_upstream.test_sequence import v_hand
    down = v_hand(down=True)
    assert custom_posture_ok("v_sign_down", hand_features(down)) and not custom_posture_ok("knife_hand", hand_features(down))
