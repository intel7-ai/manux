"""Scroll region under the cursor: cache, thread hand-off, and how the mouse controller uses it (no UI Automation)."""
import threading
import time

from src.gesture_engine import GestureEngine, GestureResult
from src.mouse_controller import MouseController
from src.scroll_target import ScrollTarget, ScrollTargetFinder

WINDOW = (0, 0, 1920, 1080)
SIDEBAR = ScrollTarget((0, 100, 300, 900), "ListControl:sidebar", True, WINDOW)          # a pane: 12 % of the window
MAIN = ScrollTarget((300, 100, 1500, 900), "GroupControl:messages", True, WINDOW)        # a big pane: 46 %
PAGE = ScrollTarget((0, 0, 1920, 1080), "DocumentControl:page", True, WINDOW)            # the page itself: 100 %


def fake_probe(x, y):
    for region in (SIDEBAR, MAIN):
        if region.contains(x, y):
            return region
    return None


def finder_with(probe=fake_probe):
    f = ScrollTargetFinder(probe_factory=lambda: probe)
    f.available = True
    f.start = lambda: None            # no UI Automation thread in tests: the tests play the thread
    return f


def test_the_innermost_region_geometry():
    assert SIDEBAR.center == (150, 500) and MAIN.center == (900, 500)
    assert 0.1 < SIDEBAR.area_fraction < 0.15 and 0.4 < MAIN.area_fraction < 0.5 and PAGE.area_fraction == 1.0
    assert ScrollTarget((0, 0, 10, 10)).area_fraction == 0.0           # unknown window
    assert MAIN.contains(900, 500) and not MAIN.contains(100, 500) and MAIN.contains(298, 500, margin=4)


def test_the_cache_answers_inside_the_region_and_only_while_it_is_fresh():
    f = finder_with()
    f._pos = (900, 400)
    f.poll_once(fake_probe)
    assert f.cached(950, 450) == MAIN and f.cached(100, 450) is None        # inside / in the other region
    f._cache = (MAIN, time.perf_counter() - 3.0)
    assert f.cached(950, 450) is None                                       # too old


def test_target_at_waits_for_the_thread_and_returns_the_region():
    f = finder_with()

    def thread():
        time.sleep(0.04)
        f.poll_once(fake_probe)

    f._pos = (900, 400)
    threading.Thread(target=thread).start()
    t0 = time.perf_counter()
    assert f.target_at(900, 400, wait=0.5) == MAIN
    assert time.perf_counter() - t0 < 0.4


def test_nothing_found_returns_none_at_once_and_a_silent_wait_gives_up():
    f = finder_with()
    f._pos = (1700, 400)
    f.poll_once(fake_probe)                                   # nothing scrollable at that spot
    t0 = time.perf_counter()
    assert f.target_at(1700, 400, wait=0.5) is None
    assert time.perf_counter() - t0 < 0.1                      # it does not wait the full time for a known "nothing"
    g = finder_with()
    t0 = time.perf_counter()
    assert g.target_at(900, 400, wait=0.08) is None            # the thread never answers
    assert 0.07 < time.perf_counter() - t0 < 0.3


def test_a_probe_that_raises_does_not_break_the_finder():
    f = finder_with(lambda x, y: 1 / 0)
    f._pos = (10, 10)
    f.poll_once(f._probe_factory())
    assert f.cached(10, 10) is None and f.probes == 1


# ---------------------------------------------------------------------- the controller uses the region under the cursor
class FakeFinder:
    def __init__(self):
        self.asked = []
        self.hints = []

    def target_at(self, x, y, wait=0.15):
        self.asked.append((x, y))
        return fake_probe(x, y)

    def hint(self, x, y):
        self.hints.append((x, y))


def browse_ctrl(start):
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.frame_aspect = 0.75
    ctrl.scroll_target_finder = FakeFinder()
    ctrl.window_probe = lambda x, y: (960, 540, "Chrome_WidgetWin_1")
    ctrl.last_screen_pos = start
    return ctrl


def test_the_target_is_the_region_under_the_cursor_not_the_whole_window():
    cfg = dict(GestureEngine().config)
    ctrl = browse_ctrl((900, 700))                             # the cursor rests over the main page
    ctrl._grab_scroll((0.5, 0.5), cfg, 1.0)
    assert ctrl.last_screen_pos == MAIN.center
    assert ctrl.browse_target[2].startswith("region:") and "scroll_center:900,500:region" in ctrl.event_log
    ctrl2 = browse_ctrl((120, 300))                            # ... or over the sidebar of the same window
    ctrl2._grab_scroll((0.5, 0.5), cfg, 1.0)
    assert ctrl2.last_screen_pos == SIDEBAR.center


def test_moving_the_cursor_to_another_region_selects_it_for_the_next_browse():
    cfg = dict(GestureEngine().config)
    ctrl = browse_ctrl((900, 700))
    ctrl._grab_scroll((0.5, 0.5), cfg, 1.0)
    ctrl._end_scroll(cfg, 1.5)
    assert ctrl.last_screen_pos == MAIN.center
    ctrl.apply(GestureResult(active_gesture="no_pinch", action="move_cursor", cursor_point=(0.2, 0.5), is_moving=True),
               cfg, now=2.0)                                   # still inside the session hold: the cursor stays
    assert ctrl.last_screen_pos == MAIN.center
    ctrl.last_screen_pos = (120, 300)                          # the user moved on to the sidebar (after the hold)
    ctrl._browse_centered = False
    ctrl._grab_scroll((0.5, 0.5), cfg, 5.0)
    assert ctrl.last_screen_pos == SIDEBAR.center


def test_without_a_region_the_window_middle_is_used_and_it_can_be_switched_off():
    cfg = dict(GestureEngine().config)
    cfg = dict(cfg, scroll_center_fallback_window=True)
    ctrl = browse_ctrl((1700, 400))                            # nothing scrollable exposed here
    ctrl._grab_scroll((0.5, 0.5), cfg, 1.0)
    assert ctrl.last_screen_pos == (960, 540) and ctrl.browse_target[2].startswith("window:")


def test_the_finder_is_told_where_the_cursor_is_until_a_session_locks_it():
    cfg = dict(GestureEngine().config)
    ctrl = browse_ctrl((900, 700))
    ctrl.apply(GestureResult(active_gesture="no_pinch", action="move_cursor", cursor_point=(0.5, 0.5), is_moving=True),
               cfg, now=1.0)
    assert ctrl.scroll_target_finder.hints
    n = len(ctrl.scroll_target_finder.hints)
    ctrl._grab_scroll((0.5, 0.5), cfg, 2.0)
    ctrl.apply(GestureResult(active_gesture="no_pinch", action="move_cursor", cursor_point=(0.5, 0.5), is_moving=True),
               cfg, now=2.1)
    assert len(ctrl.scroll_target_finder.hints) == n           # centred and locked: no probing needed


def test_a_page_sized_region_is_not_centred_but_the_cursor_is_still_kept_where_the_user_put_it():
    """The page may hold inner scroll areas the application does not expose; moving to the page's middle would
    leave them. The cursor position picks the label, so it stays."""
    cfg = dict(GestureEngine().config)
    ctrl = browse_ctrl((1700, 500))
    ctrl.scroll_target_finder.target_at = lambda x, y, wait=0.15: PAGE
    ctrl._grab_scroll((0.5, 0.5), cfg, 1.0)
    assert ctrl.last_screen_pos == (1700, 500) and ctrl.browse_target[2].startswith("page:")
    assert ctrl._browse_centered                                   # ... and held there for the session
    ctrl.apply(GestureResult(active_gesture="no_pinch", action="move_cursor", cursor_point=(0.2, 0.5), is_moving=True),
               cfg, now=1.05)
    assert ctrl.last_screen_pos == (1700, 500)


def test_a_label_the_application_does_not_expose_is_browsed_where_the_cursor_is():
    """A web app's own panels are mostly not exposed (UIA says nothing about them): the old fallback jumped to the window
    middle, which made every other label unreachable. Now the cursor just stays on the label it is over."""
    cfg = dict(GestureEngine().config)
    ctrl = browse_ctrl((1700, 400))                            # nothing exposed here; the window probe would say (960, 540)
    ctrl.scroll_target_finder.target_at = lambda x, y, wait=0.15: None
    ctrl._grab_scroll((0.5, 0.5), cfg, 1.0)
    assert ctrl.last_screen_pos == (1700, 400) and ctrl.browse_target == (1700, 400, "kept")
    for i in range(1, 9):                                      # grabbing and moving the hand: the cursor does not drift
        ctrl._grab_scroll((0.5, 0.5 + 0.01 * i), cfg, 1.0 + i / 30)
        ctrl.apply(GestureResult(active_gesture="no_pinch", action="move_cursor", cursor_point=(0.3, 0.5), is_moving=True),
                   cfg, now=1.0 + i / 30)
    assert ctrl.last_screen_pos == (1700, 400)
    assert not any(e.startswith("scroll_center") for e in ctrl.event_log)
    ctrl2 = browse_ctrl((1700, 400))                           # the old behaviour stays available as an option
    ctrl2.scroll_target_finder.target_at = lambda x, y, wait=0.15: None
    ctrl2._grab_scroll((0.5, 0.5), dict(cfg, scroll_center_fallback_window=True), 1.0)
    assert ctrl2.last_screen_pos == (960, 540)


# ---------------------------------------------------------------------- continuous browsing returns to the first spot
def series_ctrl(start=(900, 700), region=None):
    ctrl = browse_ctrl(start)
    ctrl.scroll_target_finder.target_at = lambda x, y, wait=0.15: region
    return ctrl


def drift_to(ctrl, x, y, t0, t1, steps=6):
    """The cursor travels with the returning hand from the current position to (x, y) between t0 and t1."""
    x0, y0 = ctrl.last_screen_pos
    for i in range(1, steps + 1):
        ctrl._clock = t0 + (t1 - t0) * i / steps
        ctrl._move_os_cursor(int(x0 + (x - x0) * i / steps), int(y0 + (y - y0) * i / steps))


def one_browse(ctrl, cfg, t0, t1):
    ctrl._grab_scroll((0.5, 0.5), cfg, t0)
    for i in range(1, 6):
        ctrl._grab_scroll((0.5, 0.5 + 0.01 * i), cfg, t0 + (t1 - t0) * i / 6)
    ctrl._end_scroll(cfg, t1)


def test_the_next_grab_of_a_series_sends_the_cursor_back_to_where_the_series_began():
    cfg = dict(GestureEngine().config, scroll_inertia=False)
    ctrl = series_ctrl()
    one_browse(ctrl, cfg, 1.0, 1.6)
    assert ctrl.last_screen_pos == (900, 700) and ctrl._series_anchor == (900, 700)
    drift_to(ctrl, 400, 300, 3.0, 3.3)                         # after the hold the cursor drifts with the returning hand
    ctrl._grab_scroll((0.5, 0.5), cfg, 3.35)                   # ... and the hand pinches again at once
    assert ctrl.last_screen_pos == (900, 700) and "scroll_return:900,700" in ctrl.event_log
    assert ctrl.browse_target[2] == "series"
    one_browse(ctrl, cfg, 3.35, 3.9)
    drift_to(ctrl, 1200, 200, 4.5, 4.8)                        # a third round in the same series
    ctrl._grab_scroll((0.5, 0.5), cfg, 4.85)
    assert ctrl.last_screen_pos == (900, 700)


def test_a_cursor_parked_on_another_label_starts_a_new_series_instead_of_snapping_back():
    cfg = dict(GestureEngine().config, scroll_inertia=False)
    ctrl = series_ctrl()
    one_browse(ctrl, cfg, 1.0, 1.6)
    drift_to(ctrl, 400, 300, 3.0, 3.3)
    ctrl._grab_scroll((0.5, 0.5), cfg, 3.9)                    # the cursor stood still for 0.6 s: the user put it there
    assert ctrl.last_screen_pos == (400, 300) and ctrl._series_anchor == (400, 300)
    assert not any(e.startswith("scroll_return") for e in ctrl.event_log)


def test_a_long_pause_ends_the_series():
    cfg = dict(GestureEngine().config, scroll_inertia=False)
    ctrl = series_ctrl()
    one_browse(ctrl, cfg, 1.0, 1.6)
    drift_to(ctrl, 400, 300, 5.4, 5.7)
    ctrl._grab_scroll((0.5, 0.5), cfg, 5.75)                   # 4.1 s after the last browsing, the cursor still moving
    assert ctrl.last_screen_pos == (400, 300) and ctrl._series_anchor == (400, 300)


def test_the_series_returns_to_a_pane_centre_when_the_first_grab_centred_on_one():
    cfg = dict(GestureEngine().config, scroll_inertia=False)
    ctrl = series_ctrl(start=(120, 300), region=SIDEBAR)
    one_browse(ctrl, cfg, 1.0, 1.6)
    assert ctrl.last_screen_pos == SIDEBAR.center
    drift_to(ctrl, 1300, 600, 3.0, 3.3)
    ctrl._grab_scroll((0.5, 0.5), cfg, 3.35)
    assert ctrl.last_screen_pos == SIDEBAR.center
