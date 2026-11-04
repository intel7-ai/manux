import json
import zipfile

from src.log_bundle import MAX_FAILURES, REDACTED, build_bundle


def _logs(tmp_path):
    logs = tmp_path / "logs"
    (logs / "failures").mkdir(parents=True)
    (logs / "perf_latest.csv").write_text(
        "time,fps,foreground_window,frame_age_ms_avg\n"
        "10:00:00,30.0,Inbox - salary review - Outlook,0.8\n"
        "10:00:01,30.0,,0.9\n", encoding="utf-8-sig")
    (logs / "app_latest.log").write_text("started\n", encoding="utf-8")
    (logs / "tracking-20261104-1000.csv").write_text("# x\n", encoding="utf-8")
    (logs / "failures" / "index.csv").write_text("file,kind\n", encoding="utf-8")
    for n in range(MAX_FAILURES + 5):
        (logs / "failures" / f"2026-{n:03d}-lost.json").write_text("{}", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "gestures.json").write_text('{"smoothing": 0.35}', encoding="utf-8")
    (config / "profiles").mkdir()
    (config / "profiles" / "Jane Doe.json").write_text("{}", encoding="utf-8")
    return logs, config / "gestures.json"


def test_bundle_contents_and_privacy(tmp_path):
    logs, config = _logs(tmp_path)
    path = build_bundle(logs, config, tmp_path / "out", app_version="0.3.4", extra={"camera": "1280x720@30"})
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        perf = z.read("perf_latest.csv").decode("utf-8")
        info = json.loads(z.read("system.json"))
    assert {"system.json", "perf_latest.csv", "app_latest.log", "config/gestures.json",
            "tracking/tracking-20261104-1000.csv", "failures/index.csv"} <= names
    assert "salary" not in perf and REDACTED in perf          # window titles never leave the PC
    assert not any("Jane" in n or "profiles" in n for n in names)   # profile names can be people's names
    assert sum(n.startswith("failures/") and n.endswith(".json") for n in names) == MAX_FAILURES
    assert info["app_version"] == "0.3.4" and info["camera"] == "1280x720@30"


def test_bundle_with_no_logs_yet(tmp_path):
    path = build_bundle(tmp_path / "missing", None, tmp_path / "out")
    with zipfile.ZipFile(path) as z:
        assert z.namelist() == ["system.json"]
