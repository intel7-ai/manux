import csv

from src.perf_log import HEADER, PerfLog


def test_stage_columns_are_appended_and_averaged(tmp_path):
    path = tmp_path / "perf.csv"
    log = PerfLog(path, interval=0.0)
    log.frame(10.0, 30.0, frame_age_ms=20.0, stages_ms=(11.0, 4.0, 2.0))
    log.frame(14.0, 34.0, frame_age_ms=40.0, stages_ms=(15.0, 6.0, 4.0))
    log.tick(30.0, 0)
    log.close()
    rows = list(csv.reader(path.open(encoding="utf-8-sig")))
    assert rows[0] == HEADER
    assert HEADER[:9] == ["time", "fps", "detect_ms_avg", "detect_ms_max", "loop_ms_avg", "loop_ms_max",
                          "camera_frames_skipped", "hand_mouse_in_front", "foreground_window"]
    row = dict(zip(rows[0], rows[1]))
    assert row["frame_age_ms_avg"] == "30.0" and row["frame_age_ms_max"] == "40.0"
    assert (row["stage_detect_ms_avg"], row["stage_decide_ms_avg"], row["stage_rest_ms_avg"]) == ("13.0", "5.0", "3.0")


def test_old_callers_leave_the_new_columns_empty(tmp_path):
    path = tmp_path / "perf.csv"
    log = PerfLog(path, interval=0.0)
    log.frame(None, 20.0)
    log.tick(30.0, 0)
    log.close()
    row = dict(zip(*list(csv.reader(path.open(encoding="utf-8-sig")))[:2]))
    assert row["frame_age_ms_avg"] == "" and row["stage_decide_ms_avg"] == ""
