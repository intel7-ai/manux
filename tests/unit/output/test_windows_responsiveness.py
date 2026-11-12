"""Cursor cadence and confirmed pinch contacts must not gain extra output-side waits."""
import pytest

from manux.core.contracts import ActionCommand
from manux.output.windows import FakeBackend, WindowsOutputConfig, WindowsOutputSink
from manux.output.windows.interp import CursorInterpolator
from manux.platform.monitors import ScreenLayout


@pytest.fixture
def sink():
    result = WindowsOutputSink(
        FakeBackend((500, 400)), ScreenLayout.single(1920, 1080),
        WindowsOutputConfig(smoothing=1., relative_gain_min=1., relative_gain_max=2.),
        threaded=False,
    )
    yield result
    result.close()


def apply(sink, action, phase, gesture, t, point=None, **params):
    assert sink.apply(ActionCommand(t, action, phase, gesture, point, params)).accepted


@pytest.mark.parametrize("fps", [30, 60])
def test_continuous_targets_move_during_both_halves_of_each_frame(monkeypatch, fps):
    it = CursorInterpolator(lambda x, y: None)
    monkeypatch.setattr(it, "_ensure_thread", lambda: None)
    interval = 1 / fps
    # Settle cadence before checking: simulate enough pixels to exclude integer quantization.
    for n in range(40):
        it.set_target(20 * n, 0, n * interval)
    t = 39 * interval
    samples = [it.position_at(t + k * interval / 8)[0] for k in range(9)]
    assert all(a < b for a, b in zip(samples, samples[1:]))
    assert samples[-1] == pytest.approx(780, abs=.01)
    assert it.position_at(t + 1.) == (780., 0.)  # no prediction after input stops


def test_duplicate_target_does_not_restart_glide_but_tracks_frame_cadence(monkeypatch):
    it = CursorInterpolator(lambda x, y: None)
    monkeypatch.setattr(it, "_ensure_thread", lambda: None)
    it.set_target(0, 0, 0.)
    it.set_target(100, 0, 1 / 30)
    it.set_target(100, 0, 1 / 30 + 1 / 120)
    assert it.position_at(2 / 30) == (100., 0.)
    for n in range(3, 40):
        it.set_target(100, 0, n / 30)
    it.set_target(200, 0, 40 / 30)
    assert it.position_at(40 / 30 + 1 / 60)[0] == pytest.approx(150, abs=.01)


def test_sink_delivers_unchanged_positions_to_interpolator(sink):
    calls = []
    class Interpolator:
        def set_target(self, x, y, now):
            calls.append((x, y))
        def snap(self, *args):
            pass
        def close(self):
            pass
    sink._interp = Interpolator()
    for n in range(4):
        apply(sink, "mouse.move", "hold", "pinch.move", n / 30, (.5, .5))
    assert calls == [(500, 400)] * 4


@pytest.mark.parametrize("gesture", ["pinch.move", "pinch.move_fist"])
def test_click_release_does_not_freeze_subsequent_pinch_movement(sink, gesture):
    apply(sink, "mouse.move", "hold", gesture, 0., (.5, .5))
    apply(sink, "mouse.left_click", "tap", "pinch.thumb_middle", .03)
    apply(sink, "mouse.move", "hold", gesture, .07, (.5, .5))
    apply(sink, "mouse.move", "hold", gesture, .10, (.505, .5))
    assert sink.backend.pos[0] > 500  # well inside the old .03 displacement / .45 s hold
    assert sink.backend.clicks() == ["left"] and not sink.holding()


@pytest.mark.parametrize("button,gesture", [
    ("left", "pinch.thumb_middle"), ("right", "pinch.thumb_ring"),
])
def test_two_confirmed_contacts_within_200ms_both_reach_windows(sink, button, gesture):
    for t in (1., 1.15):
        apply(sink, f"mouse.{button}_click", "tap", gesture, t)
        apply(sink, f"mouse.{button}_click", "tap", gesture, t + .01)  # same contact is idempotent
        apply(sink, "mouse.move", "hold", "pinch.move", t + .05, (.5, .5))
    assert sink.backend.events == [
        ("button", button, True), ("button", button, False),
        ("button", button, True), ("button", button, False),
    ]


def test_pinch_drag_after_click_presses_without_a_520ms_or_8px_wait(sink):
    apply(sink, "mouse.left_click", "tap", "pinch.thumb_middle", 1.)
    apply(sink, "mouse.move", "hold", "pinch.move", 1.05, (.5, .5))
    apply(sink, "mouse.drag", "begin", "pinch.drag", 1.1, (.5, .5))
    assert sink.backend.buttons_down == {"left"}
    apply(sink, "mouse.drag", "hold", "pinch.drag", 1.13, (.502, .5))
    assert sink.backend.pos[0] > 500 and sink.backend.buttons_down == {"left"}
    apply(sink, "mouse.drag", "end", "pinch.drag", 1.16)
    assert not sink.holding()


def test_open_fist_switch_keeps_every_movement_delta_and_new_gain(sink):
    apply(sink, "mouse.move", "hold", "pinch.move", 0., (.5, .5))
    apply(sink, "mouse.move", "hold", "pinch.move", .03, (.51, .5))
    apply(sink, "mouse.move", "hold", "pinch.move_fist", .06, (.52, .5), gain=2.)
    assert sink.backend.pos[0] == pytest.approx(500 + .03 * 1920, abs=1)
    apply(sink, "mouse.move", "hold", "pinch.move", .09, (.53, .5))
    assert sink.backend.pos[0] == pytest.approx(500 + .04 * 1920, abs=1)


def test_open_fist_switch_after_tracking_gap_still_reanchors(sink):
    apply(sink, "mouse.move", "hold", "pinch.move", 0., (.5, .5))
    sink.backend.pos = (600, 450)
    apply(sink, "mouse.move", "hold", "pinch.move_fist", .4, (.9, .9), gain=2.)
    assert sink.backend.pos == (600, 450)
