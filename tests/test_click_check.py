"""The TCN's second look at the virtual hand's thumb + index clicks (no model file needed: a fake gate)."""
import numpy as np

from src.click_check import ClickCheck


class FakeGate:
    classes = ["none", "thumb_index"]

    def __init__(self):
        self.p = 0.0
        self.last_tcn = None

    def step(self, now, hand, aspect):
        self.last_tcn = np.array([1 - self.p, self.p])

    def reset(self):
        pass


def check(threshold=.5):
    gate = FakeGate()
    return ClickCheck(threshold, loader=lambda: gate, background=False), gate


def test_a_press_the_model_sees_passes_and_one_it_does_not_is_dropped():
    c, gate = check()
    for i, p in enumerate((.1, .2, .7, .4)):
        gate.p = p
        c.step(i / 30, None, .5625)
    assert c.allows(2 / 30, 3 / 30)                      # the peak .7 during the press
    c.reset()
    for i, p in enumerate((.1, .2, .3, .2)):
        gate.p = p
        c.step(i / 30, None, .5625)
    assert not c.allows(2 / 30, 3 / 30) and c.blocked == 1


def test_the_approach_just_before_the_contact_counts():
    c, gate = check()
    for i, p in enumerate((.8, .1, .1)):                 # the model fired as the fingers came together
        gate.p = p
        c.step(i / 30, None, .5625)
    assert c.allows(2 / 30, 2 / 30)


def test_without_a_model_every_click_passes():
    def broken():
        raise RuntimeError("onnxruntime missing")
    c = ClickCheck(.5, loader=broken, background=False)
    c.step(0.0, None, .5625)
    assert not c.ready and "onnxruntime" in c.failed and c.allows(0.0, 0.1)


def test_the_runtime_drops_a_thumb_index_tap_the_model_does_not_confirm(tmp_path):
    from tests.test_virtual_hand import box_runtime, pinch_hand
    rt = box_runtime(tmp_path)
    rt.engine.config["virtual_click_check"] = True         # off by default
    c, gate = check()
    rt.click_check = c
    rt._step()
    opened = pinch_hand(rt)
    rt._step(); rt._step()
    rt.tracker.hand[:] = opened
    rt._step()
    assert "left_click" not in rt.mouse.event_log       # the model said .0 the whole time
    gate.p = .9
    import time
    time.sleep(.8)                                       # past the double-click window
    pinch_hand(rt)
    rt._step(); rt._step()
    rt.tracker.hand[:] = opened
    rt._step()
    assert rt.mouse.event_log.count("left_click") == 1


def test_the_check_is_off_unless_switched_on(tmp_path):
    from tests.test_virtual_hand import box_runtime
    rt = box_runtime(tmp_path)
    assert rt._click_check(rt.engine.config) is None
