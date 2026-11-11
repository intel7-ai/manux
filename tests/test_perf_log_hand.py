"""The per-second log says how well the hand was held, without a recording."""
import csv

from src.perf_log import HEADER, PerfLog
from tests.test_qt_runtime import runtime, steps


def rows(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_one_second_counts_found_followed_and_refused_frames(tmp_path):
    log = PerfLog(tmp_path / "perf.csv", interval=0.0)
    log.hand(found=True, raw=True, size=.02)
    log.hand(found=True, raw=True, followed=True, size=.03)
    log.hand(found=False, raw=True)
    log.hand(found=False, raw=False)
    log.tick(30.0, 0)
    log.tick(30.0, 0)
    log.close()
    first, second = rows(tmp_path / "perf.csv")
    assert set(HEADER) <= set(first)
    assert (first["main_frames"], first["hand_frames"], first["followed_frames"], first["refused_frames"]) == ("4", "2", "1", "1")
    assert first["hand_size"] == "0.0300"
    assert second["main_frames"] == second["hand_size"] == ""          # the counters start again, and no main screen = empty


def test_the_runtime_reports_the_hand_it_holds(tmp_path):
    rt = runtime(tmp_path)
    rt.perf_log = PerfLog(tmp_path / "perf.csv", interval=0.0)
    steps(rt, 3)
    rt.tracker.present = False
    steps(rt, 2)
    rt.perf_log.close()
    data = rows(tmp_path / "perf.csv")
    assert sum(int(r["main_frames"]) for r in data) == 5
    assert sum(int(r["hand_frames"]) for r in data) == 3
    assert float(next(r for r in data if r["hand_size"])["hand_size"]) > 0
