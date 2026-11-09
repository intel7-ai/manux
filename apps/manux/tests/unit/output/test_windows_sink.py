"""WindowsOutputSink over the fake backend: every action, phase pairs, safety, never raising."""
from __future__ import annotations

import ctypes

import pytest

from manux.actions import CATALOG
from manux.core.contracts import ActionCommand, OutputSink, OutputTarget
from manux.output.windows import FakeBackend, WindowsOutputConfig, WindowsOutputSink
from manux.output.windows import win32
from manux.platform.monitors import ScreenLayout

FRAME = 1 / 30
REL = WindowsOutputConfig(movement_mode="relative", smoothing=1.0, relative_gain_min=1.0, relative_gain_max=1.0,
                          frame_aspect=0.75)


def sink(cfg=REL, pos=(960, 540)):
    return WindowsOutputSink(FakeBackend(pos), ScreenLayout.single(1920, 1080), cfg, threaded=False)


def cmd(action, phase="tap", point=None, t=0.0, gesture="test.g", **params):
    return ActionCommand(t=t, action=action, phase=phase, gesture=gesture, point=point, params=params)


def test_is_an_output_sink_for_windows():
    s = sink()
    assert isinstance(s, OutputSink) and s.target is OutputTarget.WINDOWS


# ------------------------------------------------------------------ move
def test_move_follows_the_hand_from_the_real_cursor():
    s = sink(pos=(300, 300))
    assert s.apply(cmd("mouse.move", "hold", (.5, .5), 0.0)).accepted
    assert s.backend.kinds() == []                         # first point anchors: no jump
    s.apply(cmd("mouse.move", "hold", (.55, .5), FRAME))
    assert s.backend.pos == (300 + round(0.05 * 1920), 300)


def test_move_restarts_from_the_physical_mouse_after_a_gap():
    s = sink(pos=(300, 300))
    s.apply(cmd("mouse.move", "hold", (.5, .5), 0.0))
    s.apply(cmd("mouse.move", "hold", (.55, .5), FRAME))
    s.backend.pos = (1000, 800)                            # the user moved the real mouse
    s.apply(cmd("mouse.move", "hold", (.2, .2), 1.0))      # after a gap: re-anchor, no jump
    assert s.backend.pos == (1000, 800)
    s.apply(cmd("mouse.move", "hold", (.21, .2), 1.0 + FRAME))
    assert s.backend.pos[0] > 1000


def test_switching_the_moving_gesture_re_anchors():
    s = sink()
    s.apply(cmd("mouse.move", "hold", (.5, .5), 0.0, gesture="pointer.move"))
    s.apply(cmd("mouse.move", "hold", (.52, .5), FRAME, gesture="pointer.move"))
    p = s.backend.pos
    s.apply(cmd("mouse.move", "hold", (.9, .9), 2 * FRAME, gesture="pointer.fast"))   # other point source
    assert s.backend.pos == p


def test_move_gain_param_and_bad_values():
    a, b = sink(), sink()
    for i, x in enumerate((.5, .51, .52)):
        a.apply(cmd("mouse.move", "hold", (x, .5), i * FRAME))
        b.apply(cmd("mouse.move", "hold", (x, .5), i * FRAME, gain=3.0))
    assert b.backend.pos[0] - 960 == pytest.approx(3 * (a.backend.pos[0] - 960), abs=2)
    assert a.apply(cmd("mouse.move", "hold", (.5, .5), 1.0, gain=-1)).reason.startswith("error:")
    assert a.apply(cmd("mouse.move", "hold", None, 1.0)).reason.startswith("error:")


# ------------------------------------------------------------------ clicks
def test_left_and_right_click():
    s = sink()
    assert s.apply(cmd("mouse.left_click")).accepted
    assert s.apply(cmd("mouse.right_click", t=1.0)).accepted
    assert s.backend.clicks() == ["left", "right"] and s.backend.buttons_down == set()


def test_cursor_is_held_for_the_second_click_of_a_double_click():
    s = sink()
    s.apply(cmd("mouse.move", "hold", (.5, .5), 0.0))
    s.apply(cmd("mouse.left_click", "tap", (.5, .5), 0.03))
    click_pos = s.backend.pos
    for i, x in enumerate((0.505, 0.51, 0.515)):           # drift between the pinches: same pixel
        s.apply(cmd("mouse.move", "hold", (x, .5), 0.1 + i * 0.03))
        assert s.backend.pos == click_pos
    s.apply(cmd("mouse.move", "hold", (.56, .5), 0.2))       # a clear move releases the hold, no jump
    assert s.backend.pos == click_pos
    s.apply(cmd("mouse.move", "hold", (.60, .5), 0.23))
    assert s.backend.pos[0] > click_pos[0]


def test_double_click_hold_expires():
    from dataclasses import replace
    s = sink(replace(REL, double_click_window=0.2))
    s.apply(cmd("mouse.move", "hold", (.5, .5), 0.0))
    s.apply(cmd("mouse.left_click", "tap", (.5, .5), 0.03))
    s.apply(cmd("mouse.move", "hold", (.51, .5), 0.3))
    s.apply(cmd("mouse.move", "hold", (.53, .5), 0.33))
    assert s.backend.pos[0] > 960


# ------------------------------------------------------------------ drag
def test_drag_begin_hold_end_presses_moves_and_releases():
    s = sink()
    assert s.apply(cmd("mouse.drag", "begin", (.5, .5), 0.0)).accepted
    assert s.backend.buttons_down == {"left"} and s.dragging
    for i in range(1, 5):
        assert s.apply(cmd("mouse.drag", "hold", (.5 + .02 * i, .5), i * FRAME)).accepted
    assert s.backend.pos[0] > 960
    assert s.apply(cmd("mouse.drag", "end", None, 5 * FRAME)).accepted
    assert s.backend.buttons_down == set() and not s.dragging
    downs = [e for e in s.backend.events if e[0] == "button"]
    assert downs == [("button", "left", True), ("button", "left", False)]


def test_drag_hold_without_begin_is_blocked_and_end_without_begin_is_harmless():
    s = sink()
    assert s.apply(cmd("mouse.drag", "hold", (.5, .5))).reason == "blocked"
    assert s.apply(cmd("mouse.drag", "end")).accepted
    assert s.backend.events == []


def test_click_or_key_while_dragging_releases_the_drag_first():
    s = sink()
    s.apply(cmd("mouse.drag", "begin", (.5, .5), 0.0))
    s.apply(cmd("mouse.left_click", t=0.1))
    assert s.backend.buttons_down == set() and not s.dragging
    s.apply(cmd("mouse.drag", "begin", (.5, .5), 0.2))
    s.apply(cmd("window.show_desktop", t=0.3))
    assert s.backend.buttons_down == set()
    assert s.backend.events[-4:] == [("key", "win", True), ("key", "d", True),
                                     ("key", "d", False), ("key", "win", False)]


# ------------------------------------------------------------------ keys
@pytest.mark.parametrize(("action", "keys"), [
    ("window.task_switcher", ("alt", "tab")),
    ("window.show_desktop", ("win", "d")),
    ("window.close", ("alt", "f4")),
    ("nav.back", ("alt", "left")),
    ("nav.forward", ("alt", "right")),
    ("tab.prev", ("ctrl", "shift", "tab")),
    ("tab.next", ("ctrl", "tab")),
])
def test_key_actions_press_in_order_and_release_in_reverse(action, keys):
    s = sink()
    assert s.apply(cmd(action)).accepted
    expected = [("key", k, True) for k in keys] + [("key", k, False) for k in reversed(keys)]
    assert s.backend.events == expected and s.backend.keys_down == set()


def test_a_failing_key_press_still_releases_the_pressed_modifiers():
    s = sink()

    def key(name, down, _orig=s.backend.key):
        if name == "tab" and down:
            raise OSError("refused")
        _orig(name, down)

    s.backend.key = key
    r = s.apply(cmd("window.task_switcher"))
    assert r.reason.startswith("error:") and not r.accepted
    assert s.backend.keys_down == set()


# ------------------------------------------------------------------ every catalog action, phases
@pytest.mark.parametrize("action", sorted(CATALOG))
def test_every_catalog_action_is_executed_with_its_phases(action):
    s = sink()
    phases = [p for p in ("begin", "hold", "end", "tap") if p in CATALOG[action].phases]
    if action == "mouse.move":
        s.apply(cmd(action, "hold", (.5, .5), 0.0))
    for i, phase in enumerate(phases):
        r = s.apply(cmd(action, phase, (.5, .5 + .01 * i), 0.1 + i * FRAME))
        assert r.reason == "ok", (action, phase, r)
    s.release_all("test")
    assert s.backend.buttons_down == set() and s.backend.keys_down == set()


@pytest.mark.parametrize("action", sorted(CATALOG))
def test_phases_the_catalog_does_not_allow_are_unsupported(action):
    s = sink()
    for phase in {"tap", "begin", "hold", "end"} - set(CATALOG[action].phases):
        assert s.apply(cmd(action, phase, (.5, .5))).reason == "unsupported"
    assert s.backend.events == []


def test_unknown_action_is_unsupported():
    s = sink()
    r = s.apply(cmd("mouse.teleport"))
    assert (r.accepted, r.reason) == (False, "unsupported") and s.backend.events == []


# ------------------------------------------------------------------ safety
def test_release_all_after_a_drag_and_a_scroll_is_idempotent():
    s = sink()
    s.apply(cmd("mouse.drag", "begin", (.5, .5), 0.0))
    s.release_all("hand_lost")
    assert s.backend.buttons_down == set() and not s.dragging
    n = len(s.backend.events)
    s.release_all("hand_lost")
    s.release_all("pause")
    assert len(s.backend.events) == n                       # nothing left to release: no extra input
    s.apply(cmd("mouse.scroll", "begin", (.5, .5), 1.0))
    s.release_all("mode_change")
    assert not s.scroll.active and s.apply(cmd("mouse.drag", "hold", (.5, .5), 1.1)).reason == "blocked"


def test_release_all_never_raises_even_when_the_os_refuses():
    s = sink()
    s.apply(cmd("mouse.drag", "begin", (.5, .5), 0.0))
    s.backend.fail_on = {"button", "key", "wheel", "move"}
    s.release_all("error")                                  # no exception
    assert not s.dragging and s.errors
    s.backend.fail_on = set()
    s.release_all("error")


@pytest.mark.parametrize("kind", ["move", "button", "key", "wheel", "cursor_pos"])
def test_apply_never_raises_when_the_backend_fails(kind):
    s = sink()
    s.backend.fail_on = {kind}
    t = 0.0
    for action in sorted(CATALOG):
        for phase in ("begin", "hold", "end", "tap"):
            t += FRAME
            r = s.apply(cmd(action, phase, (.5, .5 + t / 100), t))
            assert r.reason in ("ok", "unsupported", "blocked") or r.reason.startswith("error:")
            assert r.accepted == (r.reason == "ok")


def test_closed_sink_blocks_everything_and_close_is_idempotent():
    s = sink()
    s.apply(cmd("mouse.drag", "begin", (.5, .5), 0.0))
    s.close()
    assert s.backend.buttons_down == set()
    n = len(s.backend.events)
    assert s.apply(cmd("mouse.left_click", t=1.0)).reason == "blocked"
    s.close()
    s.tick(2.0)
    assert len(s.backend.events) == n


def test_next_monitor_moves_the_real_cursor():
    from manux.platform.monitors import Monitor
    screens = ScreenLayout([Monitor(0, 0, 1920, 1080, primary=True), Monitor(1920, 0, 3840, 1080)])
    s = WindowsOutputSink(FakeBackend((10, 10)), screens, REL, threaded=False)
    target = s.next_monitor()
    assert s.backend.pos == target.center == (2880, 540)


# ------------------------------------------------------------------ win32 structs (no OS calls)
def test_win32_input_struct_matches_the_windows_abi():
    # INPUT is 40 bytes on 64-bit Windows and 28 on 32-bit
    assert ctypes.sizeof(win32.INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
    up = win32.key_input("left", down=False)
    assert up.type == win32.INPUT_KEYBOARD and up.ki.wVk == 0x25
    assert up.ki.dwFlags == win32.KEYEVENTF_KEYUP | win32.KEYEVENTF_EXTENDEDKEY
    wheel = win32.mouse_input(win32.MOUSEEVENTF_WHEEL, -120)
    assert wheel.type == win32.INPUT_MOUSE and wheel.mi.mouseData == -120


def test_win32_backend_refuses_to_start_off_windows(monkeypatch):
    monkeypatch.setattr(win32.sys, "platform", "linux")
    with pytest.raises(OSError):
        win32.Win32Backend()


def test_drag_moves_with_its_gain():
    slow, fast = sink(), sink()
    for s, gain in ((slow, 1.0), (fast, 15.0)):
        s.apply(cmd("mouse.drag", "begin", (.5, .5), 0.0, gain=gain))
        for i in range(1, 4):
            s.apply(cmd("mouse.drag", "hold", (.5 + .002 * i, .5), i * FRAME, gain=gain))
    assert fast.backend.pos[0] - 960 > 5 * (slow.backend.pos[0] - 960) > 0


def test_holding_reports_a_drag_or_an_active_scroll():
    s = sink()
    assert not s.holding()
    s.apply(cmd("mouse.drag", "begin", (.5, .5), 0.0))
    assert s.holding()
    s.apply(cmd("mouse.drag", "end", None, FRAME))
    assert not s.holding()
    s.apply(cmd("mouse.scroll", "begin", (.5, .5), 2 * FRAME))
    assert s.holding()
    s.release_all("test")
    assert not s.holding()
