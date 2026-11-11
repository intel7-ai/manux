"""Everyday virtual hand clicks and the F "not a click" mark (no camera)."""
import json
from types import SimpleNamespace

from src.click_log import MARK_SECONDS, PRE_SECONDS, ClickLog, read_clicks


def hand():
    return SimpleNamespace(landmarks=[SimpleNamespace(x=.5, y=.5, z=0)] * 21, world=[[0.0, 0.0, 0.0]] * 21)


def test_a_click_keeps_the_joints_before_it_and_f_marks_only_the_last_recent_one(tmp_path):
    log = ClickLog(tmp_path, pc="pc-test")
    for i in range(90):
        log.frame(i / 30, hand())
    assert log.click("left", 3.0, pressed=2.8, tcn_peak=.7) == 1
    clicks = read_clicks(tmp_path)
    assert len(clicks) == 1 and clicks[0]["pc"] == "pc-test" and not clicks[0]["marked"]
    frames = clicks[0]["frames"]
    assert frames and frames[-1]["t"] - frames[0]["t"] <= PRE_SECONDS + 1e-6 and "world" in frames[-1]
    assert log.mark_last(3.0 + MARK_SECONDS + .1) is None             # too late
    assert log.mark_last(4.0) == "left" and log.mark_last(4.1) is None  # once
    assert read_clicks(tmp_path)[0]["marked"]
    text = next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8")
    assert json.loads(text.splitlines()[0])["type"] == "session"


def test_a_cut_off_line_is_skipped(tmp_path):
    log = ClickLog(tmp_path)
    log.click("right", 1.0)
    path = next(tmp_path.glob("*.jsonl"))
    path.write_text(path.read_text(encoding="utf-8") + '{"type": "cli', encoding="utf-8")
    assert [c["kind"] for c in read_clicks(tmp_path)] == ["right"]


def test_the_runtime_logs_a_shape_click_and_f_marks_it_without_the_question(tmp_path):
    from tests.test_virtual_hand import box_runtime, pinch_hand
    rt = box_runtime(tmp_path)
    rt._step()
    opened = pinch_hand(rt)
    rt._step(); rt._step()
    rt.tracker.hand[:] = opened
    rt._step()
    clicks = read_clicks(tmp_path / "virtual_clicks")
    assert [c["kind"] for c in clicks] == ["left"] and clicks[0]["pressed"] is not None
    asked = []
    if rt.online is not None:
        rt.online.capture_recent = lambda now: asked.append(now)
    rt.post("key", ord("f"))
    rt._step()
    assert read_clicks(tmp_path / "virtual_clicks")[0]["marked"] and not asked


def test_the_report_runs_on_a_log(tmp_path, capsys):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("vcr", Path(__file__).resolve().parents[1] / "scripts" / "virtual_click_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    log = ClickLog(tmp_path, pc="pc-test")
    log.frame(0.0, hand())
    log.click("left", .1, pressed=0.0)
    log.click("left", .5, pressed=.4)
    log.mark_last(1.0)
    assert mod.main(["--folder", str(tmp_path), "--no-tcn"]) == 0
    out = capsys.readouterr().out
    assert "pc-test" in out and "좌클릭" in out and "(50%)" in out
