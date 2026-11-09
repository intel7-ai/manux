"""Onboarding end to end: real runtime + engine + practice cursor + bridge judging the lesson, with an open hand.

Catches what unit tests of single steps missed: a step whose cursor never shows, a choice that looks selected but
does not count, a lesson that does not come back after the cursor setup."""
from __future__ import annotations

import math
import pathlib

import pytest

pytest.importorskip("PySide6.QtQml")

from manux.app import schema  # noqa: E402
from manux.core.contracts import FrameInfo, HandObservation  # noqa: E402
from manux.desktop import build  # noqa: E402
from manux.settings.store import Settings  # noqa: E402
from manux.ui.bridge import Bridge  # noqa: E402
from manux.ui.tutorial import TARGETS  # noqa: E402


class OpenHand:
    """Open right hand facing the camera: wrist, thumb, four straight fingers (image coords) and wrist pitch."""
    fps, source, seq = 30, "test camera", 0

    def __init__(self):
        self.point, self.pitch, self.click = (.5, .55), 80., False

    def read(self, *_):
        self.seq += 1
        frame = FrameInfo(self.seq, self.seq / self.fps, 1280, 720, False)
        x, y = self.point
        lm = [(x, y + .06, 0.)] * 21
        lm[1:5] = [(x - .03, y + .04, 0.), (x - .05, y + .02, 0.), (x - .07, y, 0.), (x - .09, y - .02, 0.)]
        for root, dx in {5: -.04, 9: -.013, 13: .013, 17: .04}.items():
            for k in range(4):
                lm[root + k] = (x + dx, y - k * .035, 0.)
        if self.click:                                   # thumb tip on the middle fingertip (left click)
            lm[4] = lm[12]
        a = math.radians(self.pitch)
        world = ((0., 0., 0.),) + ((0., -.08 * math.sin(a), -.08 * math.cos(a)),) * 20
        return frame, None, HandObservation(frame, tuple(lm), "Right", .95, world=world), (), 2.0

    def mode_text(self):
        return "1280x720"

    def close(self):
        pass


@pytest.fixture
def lesson(tmp_path, monkeypatch):
    clock = [100.]
    monkeypatch.setattr("manux.ui.bridge.time.perf_counter", lambda: clock[0])
    settings = Settings(schema(), {"tracking.control_hand": "Right"})
    src = OpenHand()
    rt = build(settings, src, windows=False)
    rt.pointer_setup_dir = tmp_path / "config"
    b = Bridge(rt, settings, tmp_path, layout="studio")
    b.control_hand = rt.control_hand = "Right"

    def tick(n=1):
        for _ in range(n):
            clock[0] += 1 / 30
            rt.step()
            b.poll()
    yield b, src, settings, tick
    rt.stop()
    b.close()


def steer_through_positions(b, src, method, tick):
    for n, (tx, ty) in enumerate(TARGETS):
        for _ in range(300):
            c = b.tutorialCursor
            if c and len(c) == 2:
                ex, ey = tx - c[0], ty - c[1]
                px, py = src.point
                px = min(.9, max(.1, px + max(-.012, min(.012, .15 * ex))))
                if method == "hand_position":
                    py = min(.9, max(.15, py + max(-.012, min(.012, .15 * ey))))
                else:
                    src.pitch -= max(-2., min(2., 25 * ey))
                src.point = (px, py)
            tick()
            if b.tutorial.step_key != "vertical" or b.tutorial._target_done[n]:
                break
        assert b.tutorial.step_key != "vertical" or b.tutorial._target_done[n], f"position {n + 1} not reached"


@pytest.mark.parametrize("method", ["wrist_pitch", "hand_position"])
def test_lesson_moves_clicks_chooses_up_down_and_reaches_positions(lesson, method):
    b, src, settings, tick = lesson
    b.beginTutorial()
    assert b.tutorial.step_key == "move"
    for i in range(60):                                       # 1. move the cursor
        src.point = (.5 + .1 * math.sin(i / 6), .55)
        tick()
    tick(45)
    assert b.tutorial.step_key == "click"                    # 2. click: a real thumb+middle touch
    for _ in range(3):
        src.click = True
        tick(6)
        src.click = False
        tick(12)
        if b.tutorial.step_key != "click":
            break
    tick(45)
    assert b.tutorial.done[1] and not b.tutorial.skipped[1]    # completed by an engine-confirmed click
    assert b.tutorial.step_key == "vertical" and len(b.tutorialCursor) == 2   # the cursor is drawn here too
    assert b.tutorial.vertical_chosen                                           # the selected method counts as is
    if method != b.tutorial.vertical_method:
        b.tutorialChooseVertical(method)
        tick(10)
        assert settings.get("cursor.vertical_input") == method
    steer_through_positions(b, src, method, tick)             # 2. rest on positions 1, 2, 3 with that method
    tick(45)
    if method == "wrist_pitch":                               # 3. optional cursor fit, then back to the lesson
        assert b.tutorial.step_key == "pointer_setup"
        lesson_obj = b.tutorial
        b.tutorialOpenPointerSetup()
        tick(5)
        assert b.pointerSetupActive and b.pointerSetup.get("phase") == "intro"
        b.pointerSetupAction("skip")
        tick(45)
        assert b.tutorial is lesson_obj and b.stage_name == "tutorial"
    assert b.tutorial.step_key == "sensitivity"               # hand position passes over the wrist-only fit
