"""Fine (one cell at a time) setup round: it runs, writes a replayable trace, and replay reproduces it."""
from __future__ import annotations

import sys
from pathlib import Path

from manux.data.pointer_setup import FINE_CELL_PX, FINE_PATH, PointerSetup
from manux.output.windows.config import WindowsOutputConfig
from manux.output.windows.cursor import CursorMapper
from manux.platform.monitors import ScreenLayout

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import pointer_trace  # noqa: E402

VALUES = dict(palm_left=.35, palm_right=.65, palm_up=115., palm_down=65., palm_x_gain=1., palm_y_gain=1.,
              smoothing=.3, palm_hold=.015, palm_precision=1.)  # a scripted hand points exactly; it would not
# move further the way a person does under low-speed precision


def fine_round(tmp_path):
    monitor = ScreenLayout.single(1920, 1080).primary
    layout = ScreenLayout([monitor])
    setup = PointerSetup(monitor, 1280, lambda v: CursorMapper(layout, WindowsOutputConfig(**v)))
    setup.values = dict(VALUES)
    setup.trace_dir = tmp_path
    setup.begin_aim(fine=True)
    t = 10.
    views = []
    for _ in range(3000):
        if setup.phase != "aim":
            break
        target = setup.test.current
        # A steady hand that points exactly at the current cell
        x = VALUES["palm_left"] + target.fx * (VALUES["palm_right"] - VALUES["palm_left"])
        pitch = VALUES["palm_up"] + target.fy * (VALUES["palm_down"] - VALUES["palm_up"])
        t += 1 / 30
        setup.step(t, (x, pitch))
        views.append(setup.view())
    return setup, views


def test_fine_round_reaches_cells_and_saves_a_trace(tmp_path):
    setup, views = fine_round(tmp_path)
    assert setup.phase == "review"
    assert setup.note.startswith(f"한 칸 이동 {len(FINE_PATH)}/{len(FINE_PATH)}"), setup.note
    files = list(tmp_path.glob("pointer-fine-*.json"))
    assert len(files) == 1
    trace = pointer_trace.load(files[0])
    assert trace["kind"] == "fine" and len(trace["targets"]) == len(FINE_PATH)
    assert all(t["size_px"] == FINE_CELL_PX for t in trace["targets"])
    # zoomed view: the 40 px cell is drawn ~3x larger than on the whole-screen view
    zoomed = [v for v in views if v.get("target")]
    assert all(v["zoomed"] and 0 <= v["cursor"][0] <= 1 and 0 <= v["cursor"][1] <= 1 for v in zoomed)
    assert zoomed[0]["target"][2] == FINE_CELL_PX / 640


def test_replay_with_same_settings_matches_the_recorded_cursor(tmp_path):
    fine_round(tmp_path)
    trace = pointer_trace.load(next(tmp_path.glob("pointer-fine-*.json")))
    assert pointer_trace.replay(trace) == pointer_trace.recorded(trace)
    base = pointer_trace.score(trace, pointer_trace.recorded(trace))
    assert base["reached"] == len(FINE_PATH)
    slower = pointer_trace.score(trace, pointer_trace.replay(trace, {"smoothing": .05}))
    assert slower["median_time_s"] >= base["median_time_s"]


def test_cli_prints_recorded_replay_and_variant(tmp_path, capsys):
    fine_round(tmp_path)
    path = next(tmp_path.glob("pointer-fine-*.json"))
    assert pointer_trace.main([str(path), "--set", "palm_hold=0"]) == 0
    out = capsys.readouterr().out
    assert "recorded" in out and "replay" in out and "with palm_hold=0" in out


def test_trace_marks_clicks_and_measures_the_shift(tmp_path):
    trace = {"version": 1, "kind": "menu", "pinch": "click_ratio", "monitor": [0, 0, 1920, 1080], "values": {}, "dwell": 1.0,
             "targets": [{"fx": .5, "fy": .5, "size_px": 24}],
             "frames": []}
    for i in range(60):                                       # hold at 960, then pinch at t=1.0 tilts the cursor 15 px
        t = i / 30
        y = 540 + (15 if t >= .95 else 0)
        pinch = .5 if 1.0 <= t < 1.3 else 2.
        trace["frames"].append([t, .5, 90., 960, y, 0, pinch])
    assert pointer_trace.click_times(trace) == [1.0]
    shift = pointer_trace.click_shifts(trace, pointer_trace.recorded(trace))[0]
    assert shift["shift_px"] == 15 and shift["with_rollback_px"] == 0
    assert pointer_trace.score(trace, pointer_trace.recorded(trace))["rows"][0]["clicks"] == 1
