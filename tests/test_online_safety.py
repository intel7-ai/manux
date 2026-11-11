"""The learned model as the mouse's safety gate: rule clicks go through only with a confirmed, clear, stable gesture (no camera)."""
from types import SimpleNamespace

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from src.online.safety import OnlineGate  # noqa: E402
from tests.test_online_data import hand_world  # noqa: E402

CLASSES = ["none", "thumb_index", "thumb_ring", "thumb_middle"]


class Fixed(torch.nn.Module):
    """A model that always says the same probabilities."""

    def __init__(self, probs):
        super().__init__()
        self.logits = torch.log(torch.tensor(probs, dtype=torch.float32))

    def forward(self, x):
        return self.logits.expand(x.shape[0], -1)


def gate(probs, **kw):
    predictor = SimpleNamespace(model=Fixed(probs), classes=CLASSES, window=15, loaded=0.0)
    return OnlineGate(None, {}, predictor=predictor, **kw)


def hand():
    w = hand_world()
    lm = w * .5 + np.array([.5, .5, 0])
    return SimpleNamespace(landmarks=[SimpleNamespace(x=p[0], y=p[1], z=p[2]) for p in lm], world=[tuple(p) for p in w], score=.98)


def click(g, seconds=1.5, start=0.0, action="right_click"):
    """Feeds frames, then asks about a rule click. Returns what the mouse would get."""
    t = start
    h = hand()
    while t < start + seconds:
        g.step(t, h, .5625)
        t += 1 / 30
    return g.filter(t, SimpleNamespace(action=action, active_gesture="thumb_ring_pinch", is_moving=False)).action


def test_a_click_the_model_confirms_goes_through():
    assert click(gate([.02, .02, .94, .02])) == "right_click"


def test_a_click_the_model_does_not_see_is_held_back():
    g = gate([.9, .04, .03, .03])
    assert click(g) == "none" and g.last_rejection_reason == "below_threshold"


def test_a_close_call_between_two_gestures_is_held_back():
    g = gate([.0, .01, .60, .39], margin=.25)
    out = click(g)
    assert out == "none" and g.last_rejection_reason == "close_call" and "확실하지" in g.rejection_reason_ko()


def test_nothing_passes_right_after_the_model_was_swapped():
    g = gate([.02, .02, .94, .02], swap_block=1.0)
    assert click(g, seconds=.5) == "none" and g.last_rejection_reason == "model_swap" and "바꿔" in g.rejection_reason_ko()


def test_a_lost_hand_withholds_the_click():
    g = gate([.02, .02, .94, .02])
    click(g)
    g.step(2.0, None, .5625)
    out = g.filter(2.0, SimpleNamespace(action="right_click", active_gesture="thumb_ring_pinch", is_moving=False)).action
    assert out == "none"


def test_pointer_movement_and_unknown_actions_pass_untouched():
    g = gate([.9, .04, .03, .03])
    h = hand()
    for i in range(10):
        g.step(i / 30, h, .5625)
    moving = g.filter(.4, SimpleNamespace(action="move_cursor", active_gesture="none", is_moving=True))
    assert moving.action == "move_cursor" and moving.is_moving


def test_the_runtime_switches_to_the_learned_model_and_reloads_after_a_promotion(tmp_path):
    import dataclasses
    from src.online.live import save_encoder
    from src.online.models import make_model
    from src.online.versions import ModelManager
    from tests.test_online_runtime import runtime, with_world
    root = tmp_path / "online"
    torch.manual_seed(0)
    model = make_model("tcn", len(CLASSES), 15, 16)
    save_encoder(model, {"kind": "tcn", "window": 15, "embedding": 16, "classes": CLASSES}, root)
    ModelManager(root).initialize(model.state_dict(), {"kind": "tcn", "window": 15, "embedding": 16, "classes": CLASSES})
    rt = runtime(tmp_path)
    with_world(rt)
    rt.ui = dataclasses.replace(rt.ui, use_index_pointer=False, tcn_gate=True, gate_model="online")
    rt._step()
    assert isinstance(rt.gate, OnlineGate)
    first = rt.gate
    rt.post("online_reload")
    rt._step()
    assert isinstance(rt.gate, OnlineGate) and rt.gate is not first
    rt.ui = dataclasses.replace(rt.ui, gate_model="tcn")
    rt._step()
    assert not isinstance(rt.gate, OnlineGate)
    if rt.online:
        rt.online.close()


def test_without_a_learned_model_the_gate_says_so_and_stays_off(tmp_path):
    import dataclasses
    from tests.test_online_runtime import runtime
    rt = runtime(tmp_path)
    rt.ui = dataclasses.replace(rt.ui, use_index_pointer=False, tcn_gate=True, gate_model="online")
    rt._step()
    assert rt.gate is None and rt._gate_failed
