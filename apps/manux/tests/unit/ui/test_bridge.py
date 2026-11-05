"""The QML bridge with an offscreen Qt and a fake runtime. Skipped when PySide6 (the `ui` extra) is missing."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtQml")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from manux.app import schema  # noqa: E402
from manux.core.contracts import Stage  # noqa: E402
from manux.engine.commands import CommandQueue, RequestMode, SetBaseMode, SetControl, SetStage  # noqa: E402
from manux.settings.store import Settings  # noqa: E402
from manux.ui.view import SeenHand, ViewFrame  # noqa: E402


class FakeEngine:
    def __init__(self):
        self.commands = CommandQueue()


class FakeRuntime:
    def __init__(self, settings):
        self.engine = FakeEngine()
        self.settings = settings
        self.control_hand = ""
        self.use_crop = False
        self.want_image = True
        self.gain = None
        self.overrides = {}
        self.view = (0, ViewFrame(None))

    def set_pointer_gain(self, gain):
        self.gain = gain

    def set_override(self, fid, key, value):
        self.overrides[(fid, key)] = value

    def latest(self):
        return self.view


@pytest.fixture()
def bridge(tmp_path):
    from manux.ui.bridge import Bridge
    settings = Settings(schema())
    runtime = FakeRuntime(settings)
    b = Bridge(runtime, settings, tmp_path)
    yield b, runtime
    b.close()


def drained(runtime):
    return runtime.engine.commands.drain()


def test_starts_on_the_splash_with_pinch_as_base_mode(bridge):
    b, rt = bridge
    cmds = drained(rt)
    assert SetBaseMode("pinch") in cmds and SetStage(Stage.SPLASH) in cmds
    assert b.stage == "splash" and rt.gain == 1.0
    b.finishSplash()
    assert b.stage == "welcome" and SetStage(Stage.WELCOME) in drained(rt)   # no hand chosen yet


def test_space_turns_control_on_and_settings_changes_pause_it(bridge, tmp_path):
    b, rt = bridge
    b.control_hand = "Right"
    b.finishSplash()
    drained(rt)
    b.command(32)
    assert SetControl(True) in drained(rt) and b.controlLabel in ("일시정지", "제어 시작")
    b.setClickStyle("trigger")
    assert SetControl(False) in drained(rt)
    assert b.clickStyle == "trigger" and b.settings.get("pointer.click_style") == "trigger"
    assert (tmp_path / "settings.json").exists()                   # saved to the user folder


def test_hand_choice_saves_the_hand_and_starts_the_tutorial(bridge):
    b, rt = bridge
    b.beginHandSetup()
    t0 = __import__("time").perf_counter()
    b.choice.since = t0 - 10                                      # pretend the hand was held long enough
    for _ in range(6):
        b.choice.last_at = __import__("time").perf_counter()
        b.choice.side = "Left"
        b.choice.frames += 1
    b._choose(ViewFrame(None, hands=(SeenHand("Left", 0.95),)))
    assert b.chosenHand == "왼손" and rt.control_hand == "Left"
    assert b.stage == "tutorial" and b.tutorialCount > 0
    assert SetStage(Stage.TUTORIAL) in drained(rt)


def test_motion_toggle_and_card_switches(bridge):
    b, rt = bridge
    b.control_hand = "Right"
    b.finishSplash()
    drained(rt)
    b.toggleMotion()
    assert RequestMode("motion") in drained(rt)
    b.setCardEnabled("drag", False)
    assert b.settings.get("pinch.long_press_drag") is False
    assert "drag" not in [c[0] for c in b.gestureCards]
