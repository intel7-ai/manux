# Original a700452 synthetic unit tests; host runtime tests live in test_ai_integration.py.
"""The hand picture of a gesture in the AI connection list (src/gesture_preview.py, 2026-11-11)."""
import json

from manux.compat.hand_mouse import gesture_preview as gp
from manux.compat.hand_mouse.action_bindings import ActionBindingRegistry


def _hand(dx=0.0):
    """21 joints of a plausible hand in frame coordinates (x, y, z)."""
    return [[round(0.5 + dx + 0.01 * (i % 5), 4), round(0.7 - 0.02 * i, 4), 0.0] for i in range(21)]


def _registry(tmp_path):
    reg = ActionBindingRegistry(tmp_path / "b.json")
    reg.composite_gestures["comp_rock_sign_hold"] = {"key": "comp_rock_sign_hold", "name": "락 유지", "base_posture": "rock_sign",
                                                     "trigger_type": "hold", "enabled": True}
    reg.composite_gestures["comp_fist_swipe_up"] = {"key": "comp_fist_swipe_up", "name": "주먹 위로", "base_posture": "fist",
                                                    "trigger_type": "swipe_up", "enabled": True}
    reg.custom_motions["motion_knife"] = {"key": "motion_knife", "name": "손날", "posture": "knife_hand",
                                          "direction": "diag_down_left"}
    return reg


class _Store:
    def __init__(self, items):
        self.items = items


def test_what_names_the_hand_shapes_and_the_movement(tmp_path):
    reg = _registry(tmp_path)
    assert gp.what("comp_rock_sign_hold", reg) == (["rock_sign"], "")
    assert gp.what("comp_fist_swipe_up", reg) == (["fist"], "up")
    assert gp.what("motion_knife", reg) == (["knife_hand"], "diag_down_left")
    assert gp.what("swipe_tab_left", reg) == (["thumb_tucked"], "left")
    assert gp.what("thumb_ring", reg) == (["thumb_ring"], "")
    seqs = _Store({"seq_1": {"name": "s", "steps": ["fist", "open_hand"]}})
    assert gp.what("seq_1", reg, sequences=seqs) == (["fist", "open_hand"], "sequence")
    paths = _Store({"path_1": {"name": "p", "posture": "fist", "shape": "circle"}})
    assert gp.what("path_1", reg, paths=paths) == (["fist"], "draw_circle")
    assert gp.what("nothing_here", reg) == ([], "")


def test_every_built_in_shape_can_be_drawn_and_a_taught_one_cannot():
    for shape in gp.SHAPES:
        pts = gp.drawn(shape)
        assert len(pts) == 21, shape
        fitted = gp.fit([pts])[0]
        assert all(0 <= v <= 1 for p in fitted for v in p), shape
    assert gp.drawn("custom_2") == []
    # upside down shapes really are upside down: the fingertips below the wrist
    assert gp.drawn("v_sign_down")[8][1] > gp.drawn("v_sign_down")[0][1]
    assert gp.drawn("v_sign")[8][1] < gp.drawn("v_sign")[0][1]


def test_fit_keeps_one_scale_for_the_whole_clip():
    a = [(0.1, 0.1)] * 21
    b = [(0.3, 0.2)] * 21
    fa, fb = gp.fit([a, b])
    assert all(0 <= v <= 1 for f in (fa, fb) for p in f for v in p)
    assert fa[0] != fb[0]                       # the hand's own motion stays visible


def test_the_registration_comes_first_then_the_shown_look_then_a_real_firing_then_a_drawing(tmp_path):
    reg = _registry(tmp_path)
    ex, probe, fires = tmp_path / "ex", tmp_path / "probe", tmp_path / "fires"
    for d in (ex, probe, fires):
        d.mkdir()
    key = "comp_rock_sign_hold"
    kw = dict(examples_dir=ex, probe_dir=probe, fires_dir=fires)
    assert gp.preview(key, reg, **kw)["source"] == gp.SOURCES["drawn"]
    (fires / f"20261111-120000-{key}.json").write_text(json.dumps(
        {"key": key, "frames": [{"posture": "rock_sign", "lm": _hand()} for _ in range(5)]
         + [{"posture": "fist", "lm": _hand()}]}), encoding="utf-8")
    assert gp.preview(key, reg, **kw)["source"] == gp.SOURCES["fire"]
    (probe / "20261111-110000.json").write_text(json.dumps(
        {"result": {"posture": "rock_sign"}, "frames": [{"posture": "rock_sign", "lm": _hand()} for _ in range(6)]}),
        encoding="utf-8")
    assert gp.preview(key, reg, **kw)["source"] == gp.SOURCES["probe"]
    # a look of another shape is not taken
    (probe / "20261111-130000.json").write_text(json.dumps(
        {"result": {"posture": "fist"}, "frames": [{"posture": "fist", "lm": _hand()} for _ in range(6)]}), encoding="utf-8")
    assert gp.preview(key, reg, **kw)["source"] == gp.SOURCES["probe"]
    for name, dx in (("20261111-100000-rock_sign.json", 0.0), ("20261111-100500-rock_sign.json", 0.2)):
        (ex / name).write_text(json.dumps({"label": "rock_sign", "aspect": 0.5625,
                                           "frames": [_hand(dx) for _ in range(80)]}), encoding="utf-8")
    out = gp.preview(key, reg, **kw)
    assert out["source"] == gp.SOURCES["example"]
    assert len(out["frames"]) == gp.MAX_FRAMES and all(len(f) == 21 for f in out["frames"])
    assert out["shapes"] == ["rock_sign"] and out["move"] == ""


def test_a_sequence_shows_each_step_in_turn_and_unknown_keys_draw_nothing(tmp_path):
    reg = _registry(tmp_path)
    empty = tmp_path / "none"
    empty.mkdir()
    kw = dict(examples_dir=empty, probe_dir=empty, fires_dir=empty)
    seqs = _Store({"seq_1": {"name": "s", "steps": ["fist", "open_hand", "v_sign"]}})
    out = gp.preview("seq_1", reg, sequences=seqs, **kw)
    assert out["move"] == "sequence" and len(out["steps"]) == 3 and out["steps"][0] == 0
    assert out["steps"][1] < out["steps"][2] < len(out["frames"])
    assert gp.preview("nothing_here", reg, **kw)["frames"] == []
    reg.composite_gestures["comp_custom_9_hold"] = {"key": "comp_custom_9_hold", "base_posture": "custom_9",
                                                    "trigger_type": "hold"}
    assert gp.preview("comp_custom_9_hold", reg, **kw)["frames"] == []      # a taught shape with no saved joints


