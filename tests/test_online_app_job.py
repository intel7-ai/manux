"""The app starts background learning in its own process, reads its progress, and says what happened (fake processes, no training)."""
import json
import sys
import time

import pytest

from src.online.app_job import OnlineJob, summary
from tests.test_online_runtime import dashboard  # noqa: F401


def fake(events, code=0):
    lines = "".join(f"print({json.dumps(json.dumps(e, ensure_ascii=False))}, flush=True);" for e in events)
    return [sys.executable, "-c", f"import sys,time;{lines}time.sleep(.05);sys.exit({code})"]


def wait(job, limit=10.0):
    end = time.time() + limit
    while job.running and time.time() < end:
        time.sleep(.05)
    return job.state


def test_progress_and_the_result_come_through(tmp_path):
    job = OnlineJob(tmp_path)
    assert job.start(command=fake([{"event": "update", "done": 5, "total": 10}, {"event": "done", "status": "promoted", "problems": []}]))
    state = wait(job)
    assert state["phase"] == "done" and state["status"] == "promoted" and not job.running and job.version >= 2


def test_a_crash_is_reported_and_only_one_round_runs_at_a_time(tmp_path):
    job = OnlineJob(tmp_path)
    assert job.start(command=[sys.executable, "-c", "import time;time.sleep(1);raise SystemExit(3)"])
    assert not job.start(command=fake([]))
    state = wait(job)
    assert state["phase"] == "error" and "3" in state["message"]
    assert job.start(command=fake([{"event": "error", "message": "boom"}], 1))
    assert wait(job)["message"] == "boom"


def test_the_summary_counts_checked_samples_and_reads_the_last_round(tmp_path):
    from src.online.samples import USER_CORRECTION, SampleStore
    root = tmp_path / "online"
    SampleStore(root).add_frames([{"t": 0.0, "lm": None, "world": None}], label="none", label_source=USER_CORRECTION, session="s")
    (root / "versions").mkdir()
    (root / "versions" / "versions.json").write_text(json.dumps([{"event": "initialized", "time": "x"}, {"event": "rejected", "time": "2026-11-10 03:00:00"}]), encoding="utf-8")
    s = summary(root)
    assert s["samples"] == 1 and s["last"]["event"] == "rejected" and not s["can_rollback"]


def test_the_dashboard_words_each_outcome(dashboard, tmp_path):  # noqa: F811
    dashboard.online_root = tmp_path
    job = dashboard._online_job()
    for state, expected in [({"phase": "update", "done": 3, "total": 9}, "3/9"), ({"phase": "checking"}, "검사"),
                            ({"phase": "done", "status": "rejected", "problems": ["forgot:thumb_ring", "more_false_fires"]}, "엄지+약지"),
                            ({"phase": "done", "status": "promoted", "problems": []}, "반영"),
                            ({"phase": "error", "message": "x"}, "하지 못했어요")]:
        job._set(**state)
        dashboard.poll_online_job()
        assert expected in dashboard.learnText
    assert ("online_reload",) in dashboard.online_commands
    dashboard.online_commands.clear()
