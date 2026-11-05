"""Grab scroll, inertia and the wheel pump. Ported from the grab-scroll / ScrollPump cases of v0.3
tests/test_gesture_mouse.py (driven through ActionCommands instead of GestureResult)."""
from __future__ import annotations

import math

from manux.core.contracts import ActionCommand
from manux.output.windows import FakeBackend, WindowsOutputConfig, WindowsOutputSink
from manux.output.windows.scroll import ScrollPump
from manux.platform.monitors import ScreenLayout

G = "pinch.thumb_index"
SCROLL = WindowsOutputConfig(scroll_gain=2400.0, scroll_deadzone=0.01, scroll_invert=False, frame_aspect=0.75)
INERTIA = WindowsOutputConfig(scroll_gain=2400.0, scroll_deadzone=0.01, frame_aspect=0.75, scroll_inertia=True,
                              scroll_inertia_time=0.4, scroll_inertia_min_speed=600.0)


def sink(cfg=SCROLL):
    return WindowsOutputSink(FakeBackend((960, 540)), ScreenLayout.single(1920, 1080), cfg, threaded=False)


def cmd(phase, point=None, t=0.0, action="mouse.scroll", gesture=G, **params):
    return ActionCommand(t=t, action=action, phase=phase, gesture=gesture, point=point, params=params)


def _scroll(dy=0.0, dx=0.0, cfg=SCROLL, frames=10):
    s = sink(cfg)
    x, y = 0.5, 0.5
    assert s.apply(cmd("begin", (x, y), 0.0)).accepted
    for i in range(1, frames + 1):
        s.apply(cmd("hold", (x + dx * i / frames, y + dy * i / frames), i / 30))
    for i in range(6):  # hold still: the smoothed point catches up
        s.apply(cmd("hold", (x + dx, y + dy), (frames + 1 + i) / 30))
    return s


def test_pull_down_scrolls_the_page_up_like_a_touchscreen():
    s = _scroll(dy=0.2)
    v, h = s.backend.wheel_total
    assert v > 0 and h == 0
    assert 200 < v < 400                     # ~0.2 * 0.75 * 2400 minus deadzone / smoothing tail
    assert "move" not in s.backend.kinds()   # the cursor stays put while scrolling
    assert _scroll(dy=-0.2).backend.wheel_total[0] < 0


def test_horizontal_drag_scrolls_sideways():
    s = _scroll(dx=0.2)
    assert s.backend.wheel_total[1] < 0 and s.backend.wheel_total[0] == 0


def test_scroll_deadzone_invert_and_gain_param():
    assert _scroll(dy=0.005).backend.wheel_total == [0, 0]
    inv = _scroll(dy=0.2, cfg=WindowsOutputConfig(scroll_gain=2400.0, scroll_invert=True, frame_aspect=0.75))
    assert inv.backend.wheel_total[0] < 0
    s = sink()
    s.apply(cmd("begin", (.5, .5), 0.0, gain=2.0))
    for i in range(1, 17):
        s.apply(cmd("hold", (.5, .5 + 0.2 * min(i, 10) / 10), i / 30))
    assert s.backend.wheel_total[0] > 1.8 * _scroll(dy=0.2).backend.wheel_total[0]


def test_scroll_stops_at_end_and_move_is_blocked_while_grabbing():
    s = _scroll(dy=0.2)
    assert s.apply(cmd("hold", (.5, .5), 1.0, action="mouse.move", gesture="pointer.move")).reason == "blocked"
    s.apply(cmd("end", None, 1.0))
    total = list(s.backend.wheel_total)
    for i in range(5):
        s.apply(cmd("hold", (0.5, 0.7 + i * 0.02), 2 + i / 30, action="mouse.move", gesture="pointer.move"))
    assert s.backend.wheel_total == total


def _flick(s, stop_before_release=False):
    """Grab, pull down fast (0.25 of the frame in ~0.2 s), release."""
    t, y = 0.0, 0.5
    s.apply(cmd("begin", (0.5, y), t))
    for _ in range(7):
        t += 1 / 30
        y += 0.035
        s.apply(cmd("hold", (0.5, y), t))
    if stop_before_release:
        for _ in range(8):
            t += 1 / 30
            s.apply(cmd("hold", (0.5, y), t))
    t += 1 / 30
    s.apply(cmd("end", None, t))
    return t


def test_flick_release_keeps_the_page_coasting_then_it_stops():
    s = sink(INERTIA)
    t = _flick(s)
    at_release = s.backend.wheel_total[0]
    assert s.scroll_coasting
    totals = []
    for i in range(1, 90):   # 3 s
        s.tick(t + i / 30)
        totals.append(s.backend.wheel_total[0])
    assert totals[5] > at_release
    assert totals[-1] == totals[-10] and not s.scroll_coasting
    assert totals[5] - at_release > totals[11] - totals[5] > 0


def test_no_inertia_when_the_hand_stopped_before_release():
    s = sink(INERTIA)
    t = _flick(s, stop_before_release=True)
    at_release = s.backend.wheel_total[0]
    for i in range(30):
        s.tick(t + i / 30)
    assert s.backend.wheel_total[0] == at_release and not s.scroll_coasting


def test_pinching_again_catches_a_coasting_page_and_clicks_stop_it():
    s = sink(INERTIA)
    t = _flick(s)
    s.apply(cmd("begin", (0.5, 0.3), t + 1 / 30))
    assert not s.scroll_coasting
    caught = s.backend.wheel_total[0]
    s.apply(cmd("hold", (0.5, 0.3), t + 2 / 30))
    assert s.backend.wheel_total[0] == caught
    s2 = sink(INERTIA)
    t2 = _flick(s2)
    s2.apply(cmd("tap", (0.5, 0.5), t2 + 1 / 30, action="mouse.left_click", gesture="pinch.thumb_middle"))
    assert not s2.scroll_coasting


def test_inertia_can_be_switched_off():
    from dataclasses import replace
    s = sink(replace(INERTIA, scroll_inertia=False))
    _flick(s)
    assert not s.scroll_coasting


def _launch(moves, **over):
    from dataclasses import replace
    s = sink(replace(WindowsOutputConfig(frame_aspect=0.75), scroll_inertia=True, **over))
    x, y, t = 0.5, 0.5, 1.0
    s.apply(cmd("begin", (x, y), t))
    for dx, dy in moves:
        t += 1 / 30
        x, y = x + dx, y + dy
        s.apply(cmd("hold", (x, y), t))
    s.apply(cmd("end", None, t + 1 / 30))
    return s


def test_inertia_follows_the_recent_flick_not_the_whole_grab():
    s = _launch([(0, 0.004)] * 20 + [(0, -0.012)] * 4)
    assert s.scroll.last_inertia["same_way"] and s.scroll_coasting
    assert s.scroll.last_inertia["used"][0] < 0


def test_inertia_stays_on_the_vertical_axis_for_a_vertical_flick():
    s = _launch([(0, 0.004)] * 10 + [(0.002, 0.012)] * 4)
    used = s.scroll.last_inertia["used"]
    assert s.scroll_coasting and used[1] == 0 and used[0] > 0
    diag = _launch([(0.012, 0.010)] * 6)
    assert diag.scroll_coasting and all(diag.scroll.last_inertia["used"])


def test_inertia_launch_speed_is_capped():
    s = _launch([(0, 0.004)] * 5 + [(0, 0.06)] * 4)
    used, raw = s.scroll.last_inertia["used"], s.scroll.last_inertia["raw"]
    assert s.scroll_coasting and math.hypot(*used) <= 3600 + 1 and raw[0] > used[0]


def test_short_gap_in_the_hold_resumes_without_a_jump():
    s = _scroll(dy=0.2)
    before = list(s.backend.wheel_total)
    s.apply(cmd("hold", (0.5, 0.2), 2.0))          # the hand comes back far away after a tracking gap
    assert s.backend.wheel_total == before
    s.apply(cmd("hold", (0.5, 0.21), 2.0 + 1 / 30))
    assert s.backend.wheel_total[0] - before[0] < 30


def test_release_all_ends_the_grab_and_the_coast():
    s = sink(INERTIA)
    t = _flick(s)
    assert s.scroll_coasting
    s.release_all("hand_lost")
    total = list(s.backend.wheel_total)
    for i in range(10):
        s.tick(t + i / 30)
    assert s.backend.wheel_total == total and not s.scroll_coasting
    assert s.apply(cmd("hold", (.5, .9), t + 1)).reason == "blocked"      # no grab open any more
    assert s.apply(cmd("end", None, t + 1)).accepted                      # a late end is harmless


# ------------------------------------------------------------------ ScrollPump
def test_scroll_pump_spreads_a_burst_over_many_small_steps_and_keeps_the_total():
    p = ScrollPump(tau=0.03)
    p.step(0.0)
    p.add(240, 0)
    sent = [p.step(i * 0.008)[0] for i in range(1, 60)]
    assert max(sent) < 90
    assert sum(1 for v in sent if v) >= 12
    assert abs(sum(sent) - 240) <= 2
    assert sent[0] > 0 and sent[:5] == sorted(sent[:5], reverse=True)


def test_scroll_pump_clear_drops_what_was_not_sent_yet():
    p = ScrollPump(tau=0.03)
    p.step(0.0)
    p.add(500, 0)
    first = p.step(0.008)[0]
    p.clear()
    assert sum(p.step(0.008 * (i + 2))[0] for i in range(40)) == 0 and first > 0
    assert p.pending() == (0.0, 0.0)


def test_scroll_pump_flush_returns_the_rest_and_zero_tau_sends_at_once():
    p = ScrollPump(tau=0.03)
    p.step(0.0)
    p.add(100, 40)
    v1, h1 = p.step(0.008)
    v, h = p.flush()
    assert abs((v1 + v) - 100) <= 1 and abs((h1 + h) - 40) <= 1 and p.pending() == (0.0, 0.0)
    q = ScrollPump(tau=0.0)
    q.step(0.0)
    q.add(120, -60)
    assert q.step(0.01, tau=0.0) == (120, -60)


def test_threaded_sink_pumps_the_wheel_in_the_background():
    """The real-time path: the pump thread delivers the whole amount in small events."""
    import time
    s = WindowsOutputSink(FakeBackend((960, 540)), ScreenLayout.single(1920, 1080), SCROLL, threaded=True)
    try:
        now = time.perf_counter()
        s.apply(cmd("begin", (.5, .5), now))
        s.apply(cmd("hold", (.5, .55), now + 0.01))   # leaves the deadzone
        s.apply(cmd("hold", (.5, .65), now + 0.02))
        deadline = time.perf_counter() + 2.0
        expected = s.scroll.total[0]
        while s.backend.wheel_total[0] < expected - 2 and time.perf_counter() < deadline:
            time.sleep(0.01)
        assert expected > 0 and abs(s.backend.wheel_total[0] - expected) <= 2   # float carry only
        assert sum(1 for e in s.backend.events if e[0] == "wheel") >= 2
    finally:
        s.close()
