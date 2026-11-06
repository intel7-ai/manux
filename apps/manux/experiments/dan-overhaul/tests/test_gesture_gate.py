from types import SimpleNamespace

import numpy as np

from src.gesture_gate import GestureGate

INFO = {"classes": ["none", "thumb_index", "thumb_ring"], "window": 15, "threshold": 0.7, "frames_in_a_row": 3}


def _gate(cls):
    probs = np.zeros(3)
    probs[cls] = 1.0
    return GestureGate(lambda x: probs, INFO)


def _run(gate, actions, t0=0.0):
    out = []
    for i, a in enumerate(actions):
        t = t0 + i / 30
        gate.step(t, None, 0.5625)
        out.append(gate.filter(t, SimpleNamespace(action=a, is_moving=a == "move_cursor")).action)
    return out


def test_right_click_and_scroll_need_the_model():
    assert _run(_gate(0), ["none"] * 5 + ["right_click"] + ["none"] * 20).count("right_click") == 0
    assert "scroll_grab" not in _run(_gate(0), ["none"] * 5 + ["scroll_grab"] * 10)
    assert _run(_gate(2), ["none"] * 5 + ["right_click"] + ["none"] * 5).count("right_click") == 1
    assert _run(_gate(1), ["none"] * 5 + ["scroll_grab"] * 10).count("scroll_grab") == 10


def test_a_held_click_goes_out_when_the_model_agrees_a_little_later_and_others_pass():
    probs = {"p": np.array([1.0, 0, 0])}
    gate = GestureGate(lambda x: probs["p"], INFO)
    out = _run(gate, ["none"] * 3 + ["right_click"])
    assert out[-1] == "none"                                   # held
    probs["p"] = np.array([0, 0, 1.0])
    out = _run(gate, ["none"] * 4, t0=0.2)
    assert out.count("right_click") == 1                       # sent once the model saw it (within 0.3 s)
    assert _run(_gate(0), ["left_click", "move_cursor"]) == ["left_click", "move_cursor"]
