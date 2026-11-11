"""Drawing mode (src/draw_mode.py, src/stroke_recognizer.py): shapes drawn with the hand run commands. No camera."""
import math
from types import SimpleNamespace

import pytest

from src.draw_mode import DEFAULT_BINDINGS, DrawMode, ShapeStore, judge
from src.stroke_recognizer import BUILTIN
from tests.test_online_data import hand_world


@pytest.fixture
def store(tmp_path):
    return ShapeStore(tmp_path / "draw_shapes.json")


def ellipse(n=60, rx=200, ry=200, start=-math.pi / 2, turn=1):
    return [(rx * math.cos(start + turn * 2 * math.pi * i / n), ry * math.sin(start + turn * 2 * math.pi * i / n)) for i in range(n + 1)]


def test_builtin_shapes_are_recognised_either_way_round_and_a_line_is_not_a_circle(store):
    assert judge(ellipse(), store)["key"] == "draw_circle"
    assert judge(ellipse(turn=-1), store)["key"] == "draw_circle"            # counter-clockwise too
    line = [(i * 10.0, 0.0) for i in range(40)]
    assert not (judge(line, store)["ok"] and judge(line, store)["key"] == "draw_circle")
    assert judge([(0, 0)] * 3, store)["ok"] is False                         # too short


def test_a_user_shape_from_mouse_strokes_is_saved_recognised_and_removed(store, tmp_path):
    heart = [(math.sin(t) ** 3 * 16, -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)))
             for t in [i / 40 * 2 * math.pi for i in range(41)]]
    key = store.add("하트", [heart])
    again = ShapeStore(tmp_path / "draw_shapes.json")
    assert any(s["key"] == key and s["name"] == "하트" for s in again.shapes())
    big = [(x * 7 + 300, y * 7 + 100) for x, y in heart]                      # bigger, elsewhere
    assert judge(big, again)["key"] == key
    with pytest.raises(ValueError):
        store.add("하트", [heart])                                            # same name
    with pytest.raises(ValueError):
        store.add("", [heart])
    store.set_enabled(key, False)
    assert judge(big, store)["key"] != key
    assert store.remove(key) and not any(s["key"] == key for s in store.shapes())


def test_builtin_shapes_have_default_commands_without_writing_any_file():
    from src.action_bindings import ActionBindingRegistry
    import tempfile, os
    with tempfile.TemporaryDirectory() as d:
        reg = ActionBindingRegistry(os.path.join(d, "b.json"))
        b = reg.get_binding("draw_zigzag")
        assert (b["action_type"], b["action_value"]) == DEFAULT_BINDINGS["draw_zigzag"]
    assert set(DEFAULT_BINDINGS) == set(BUILTIN)


def pinched_hand(cx, cy, scale=.25):
    """A hand with thumb and index tips together, its palm centre at (cx, cy) of the camera image."""
    w = hand_world()
    pts = [[.5 + p[0] * scale, .5 + p[1] * scale] for p in w]
    pts[4] = list(pts[8])
    palm = [sum(pts[i][0] for i in (0, 5, 9, 13, 17)) / 5, sum(pts[i][1] for i in (0, 5, 9, 13, 17)) / 5]
    return [SimpleNamespace(x=x - palm[0] + cx, y=y - palm[1] + cy, z=0) for x, y in pts]


def open_hand(cx, cy):
    lm = pinched_hand(cx, cy)
    lm[4] = SimpleNamespace(x=lm[4].x - .2, y=lm[4].y + .1, z=0)
    return lm


def pointing_hand(tx, ty, scale=.25):
    """Index straight, the other three folded onto their knuckles, the index tip at (tx, ty) of the camera image."""
    w = hand_world()
    pts = [[.5 + p[0] * scale, .5 + p[1] * scale, p[2] * scale] for p in w]
    for mcp, tip in ((9, 12), (13, 16), (17, 20)):
        for j in (tip - 2, tip - 1, tip):
            pts[j] = [pts[0][0] + (pts[mcp][0] - pts[0][0]) * 1.05, pts[0][1] + (pts[mcp][1] - pts[0][1]) * 1.05, pts[mcp][2]]
    dx, dy = tx - pts[8][0], ty - pts[8][1]
    return [SimpleNamespace(x=x + dx, y=y + dy, z=z) for x, y, z in pts]


def flat_hand(tx, ty):
    lm = pointing_hand(tx, ty)
    w = hand_world()
    base = pointing_hand(tx, ty)
    out = list(base)
    # unfold the three fingers again: an open hand (pen up)
    full = [[.5 + p[0] * .25, .5 + p[1] * .25, p[2] * .25] for p in w]
    dx, dy = base[8].x - full[8][0], base[8].y - full[8][1]
    for j in range(9, 21):
        out[j] = SimpleNamespace(x=full[j][0] + dx, y=full[j][1] + dy, z=full[j][2])
    return out


def test_pointing_is_index_straight_and_the_rest_folded():
    from src.draw_mode import is_pointing
    assert is_pointing(pointing_hand(.5, .5))
    assert not is_pointing(flat_hand(.5, .5))


def draw_circle(mode, path, t, slip_at=None, lost_at=None, aspect=16 / 9):
    box, w, h = (0, 0, 1, 1), 1280, 720
    result = None
    for _ in range(3):
        mode.step(t, flat_hand(*path[0]), box, w, h, aspect); t += 1 / 30
    for i, (x, y) in enumerate(path):
        if slip_at is not None and slip_at <= i < slip_at + 4:      # 4 frames (0.13 s) read as not pointing
            r = mode.step(t, flat_hand(x, y), box, w, h, aspect)
        elif lost_at is not None and lost_at <= i < lost_at + 3:    # the hand lost for 0.1 s
            r = mode.lost(t)
        else:
            r = mode.step(t, pointing_hand(x, y), box, w, h, aspect)
        assert r is None, (i, r)
        t += 1 / 30
    for _ in range(15):                                  # open the hand: the pen lifts after the grace
        result = mode.step(t, flat_hand(*path[-1]), box, w, h, aspect) or result
        t += 1 / 30
    return result, t


def test_the_index_finger_draws_and_short_slips_do_not_break_the_line(store):
    aspect = 16 / 9
    path = [(.5 + .2 / aspect * math.cos(a), .5 + .2 * math.sin(a)) for a in [i / 60 * 2 * math.pi for i in range(61)]]
    for kw in ({}, {"slip_at": 20}, {"lost_at": 35}):
        mode = DrawMode(store)
        mode.set_active(True)
        result, t = draw_circle(mode, path, 0.0, **kw)
        assert result is not None and result["key"] == "draw_circle" and result["ok"], (kw, result)
        assert mode.view(t)["last"]["name"].startswith("원")


def test_a_long_pause_ends_the_stroke(store):
    from src.draw_mode import PEN_GRACE
    mode = DrawMode(store)
    mode.set_active(True)
    t = 0.0
    for i in range(30):
        mode.step(t, pointing_hand(.3 + i * .01, .5), (0, 0, 1, 1), 1280, 720); t += 1 / 30
    assert mode.view(t)["down"]
    ended = None
    while t < 1 + PEN_GRACE + .2:
        ended = mode.lost(t) or ended
        t += 1 / 30
    assert not mode.view(t)["down"] and mode.view(t)["stroke"] == []
