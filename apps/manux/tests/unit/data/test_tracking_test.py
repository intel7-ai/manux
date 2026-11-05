"""Tracking test bookkeeping, driven by a simulated cursor (no camera, no Qt). Ported with data/tracking_test.py."""
import csv
import math

import pytest

from manux.data.tracking_test import TrackingTest, load_recent_results, result_line, view
from manux.platform.monitors import Monitor

MON = Monitor(0, 0, 1920, 1080, primary=True)
DT = 1 / 30


def _go_to_target(test, t, start, frames=15, hold=20, hand=lambda i: True, wobble=0.0):
    """Move in a straight line onto the current target, then hold on it."""
    target = test.current
    cx, cy = test.center_px(target)
    for i in range(frames + hold):
        k = min(1.0, (i + 1) / frames)
        x = start[0] + (cx - start[0]) * k + (wobble if i % 2 else -wobble) * (i >= frames)
        y = start[1] + (cy - start[1]) * k
        test.update(t, (x, y), hand=hand(i), active=True, fps=30, detect_ms=12)
        t += DT
        if test.current is not target:
            return t, (x, y)
    return t, (x, y)


def test_straight_moves_reach_every_target_with_full_path_efficiency():
    test = TrackingTest(MON, count=6, seed=1)
    t, pos = 0.0, MON.center
    while not test.done:
        t, pos = _go_to_target(test, t, pos)
    s = test.summary()
    assert s["reached"] == 6 and s["shown"] == 6
    assert s["median_time_s"] == pytest.approx(15 * DT, abs=2 * DT)
    assert s["median_path_efficiency"] == pytest.approx(1.0, abs=.01)
    assert s["mean_dwell_jitter_px"] == pytest.approx(0.0, abs=.01)
    assert s["lost_events"] == 0 and s["mean_fps"] == 30


def test_dwell_jitter_and_hand_loss_are_recorded():
    test = TrackingTest(MON, count=1, seed=2)
    lost = lambda i: not (3 <= i < 6)  # three frames without a hand while moving
    _go_to_target(test, 0.0, MON.center, hand=lost, wobble=3.0)
    row = test.rows()[0]
    assert row["reached"]
    assert row["lost_events"] == 1 and row["lost_s"] == pytest.approx(3 * DT, abs=1e-6)
    assert 2.5 <= row["dwell_jitter_px"] <= 3.5


def test_target_times_out_and_pause_is_not_counted():
    test = TrackingTest(MON, count=2, timeout=1.0, seed=3)
    for i in range(40):  # 1.3 s far from the target
        test.update(i * DT, (5, 5), hand=True, active=True)
    assert test.index == 1 and test.targets[0].timed_out and not test.rows()[0]["reached"]
    second = test.targets[1]
    before = (second.path_px, second.lost_s, second.lost_events, test.frames)
    test.update(2.0, (5, 5), hand=True, active=False)
    test.update(2.1, (900, 900), hand=False, active=False)   # paused: ignored
    assert (second.path_px, second.lost_s, second.lost_events, test.frames) == before


def test_targets_are_spread_out_and_inside_the_monitor():
    test = TrackingTest(MON, count=12, seed=4)
    for a, b in zip(test.targets, test.targets[1:]):
        assert math.dist((a.fx, a.fy), (b.fx, b.fy)) >= .3
    for t in test.targets:
        assert .08 <= t.fx <= .92 and .08 <= t.fy <= .92
    assert {t.size_px for t in test.targets} == {40, 70, 110}


def test_csv_has_settings_summary_and_one_row_per_target(tmp_path):
    test = TrackingTest(MON, count=3, seed=5)
    t, pos = 0.0, MON.center
    while not test.done:
        t, pos = _go_to_target(test, t, pos)
    path = test.save_csv(tmp_path, {"anchor": "index"})
    rows = list(csv.reader(open(path, encoding="utf-8")))
    meta = {r[1]: r[2] for r in rows if r and r[0] == "#"}
    assert meta["anchor"] == "index" and meta["reached"] == "3"
    table = [r for r in rows if r and r[0] != "#"]
    assert table[0][:3] == ["target", "size_px", "distance_px"] and len(table) == 4
    assert "도달 3/3" in test.summary_text()


def test_holding_tolerates_small_overshoot_past_the_edge_once_on_the_target():
    test = TrackingTest(MON, count=1, seed=6)
    target = test.current
    cx, cy = test.center_px(target)
    r = target.size_px / 2
    t = 0.0
    test.update(t, (cx, cy), hand=True, active=True)           # on the target
    for i in range(1, 20):                                       # wobble 6 px past the edge
        t += DT
        test.update(t, (cx + (r + 6 if i % 2 else 0), cy), hand=True, active=True)
    assert test.done and test.rows()[0]["reached"]
    far = TrackingTest(MON, count=1, seed=6)
    far.update(0.0, (cx + r + 6, cy), hand=True, active=True)   # never entered: not inside
    assert far.current.inside_since is None


def test_recent_results_are_read_back_from_saved_csvs(tmp_path):

    for seed, anchor in ((1, "index"), (2, "palm")):
        test = TrackingTest(MON, count=2, seed=seed)
        t, pos = 0.0, MON.center
        while not test.done:
            t, pos = _go_to_target(test, t, pos)
        path = test.save_csv(tmp_path, {"anchor": anchor, "sensitivity_pct": 100})
        path.rename(tmp_path / f"tracking-20261103-23100{seed}.csv")
    (tmp_path / "tracking-20261103-999999.csv").write_bytes(b"\xff\xfe broken")
    lines = load_recent_results(tmp_path, limit=5)
    assert len(lines) == 2                       # the unreadable file is skipped
    assert lines[0].startswith("11/03 23:10  손바닥 · 100% — 도달 2/2")
    assert "검지 끝" in lines[1] and "끊김 0회" in lines[1]
    assert load_recent_results(tmp_path / "missing") == []


def test_view_is_plain_data_for_the_window():
    test = TrackingTest(MON, count=3, seed=2)
    v = view(test, (960, 540))
    assert v["paused"] and not v["done"] and v["total"] == 3 and v["cursor"] == (0.5, 0.5)
    assert v["target"][2] == pytest.approx(test.targets[0].size_px / 1920)
    t, pos = 0.0, MON.center
    while not test.done:
        t, pos = _go_to_target(test, t, pos)
    done = view(test, pos)
    assert done["done"] and done["target"] is None and done["summary"].startswith("도달 3/3")
