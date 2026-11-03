"""
Windows smoke check for the Qt dashboard and the tracking test window (no camera).

Run from hand-mouse/:  uv run --locked --extra qt python scripts/windows_smoke.py [out_dir]

Reported 2026-11-03 on Windows: the app froze while the tracking test window was open.
This drives the same dashboard calls main.py makes per frame (tracking update + Qt event
processing) for a few seconds, records how long each frame takes, the monitor layout
Windows reports vs what Qt reports (DPI), and saves a screenshot of the test window.
Exits 1 when frames are too slow (a frozen UI) or the window does not show.
"""
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Same import order as main.py: pyautogui (via the controller) sets DPI awareness first.
from src.mouse_controller import MouseController  # noqa: E402
from src.qt_dashboard import QtDashboard  # noqa: E402
from src.tracking_test import TrackingTest  # noqa: E402

FRAMES = 150
FRAME_BUDGET_MS = 50.0     # median per-frame UI cost must stay well under a 30 fps frame
FRAME_MAX_MS = 750.0       # any single frame this slow counts as a freeze


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "smoke-out")
    out.mkdir(parents=True, exist_ok=True)
    mouse = MouseController(dry_run=True)
    dash = QtDashboard(dry_run=True)
    dash.stage_name = "main"
    dash.stateChanged.emit()
    for _ in range(10):
        dash.poll()

    monitor = mouse.screens.primary
    test = TrackingTest(monitor, seed=11)
    dash.set_tracking(test)
    # Opens paused (like the app): the window must say so before control starts
    test.update(time.perf_counter(), monitor.center, hand=True, active=False)
    dash.tracking_frame(test.cursor_fraction(monitor.center))
    for _ in range(10):
        dash.poll()
    paused_status = dash.trackingStatus
    paused_flag = dash.trackingPaused
    times = []
    pos = monitor.center
    for i in range(FRAMES):
        t0 = time.perf_counter()
        target = test.current
        if target is not None:  # glide toward the target like a hand would
            cx, cy = test.center_px(target)
            pos = (pos[0] + (cx - pos[0]) * .2, pos[1] + (cy - pos[1]) * .2)
        test.update(t0, pos, hand=True, active=True, fps=30, detect_ms=12)
        dash.tracking_frame(test.cursor_fraction(pos))
        dash.poll()
        times.append((time.perf_counter() - t0) * 1000.0)
        time.sleep(max(0.0, 1 / 30 - (time.perf_counter() - t0)))

    windows = [w for w in dash.application.topLevelWindows() if w.title() == "MANUX 트래킹 측정"]
    win = windows[0] if windows else None
    shot = None
    if win is not None and win.isVisible():
        shot = out / "tracking-window.png"
        win.grabWindow().save(str(shot))
    qt_screens = [{
        "name": s.name(), "geometry": [s.geometry().x(), s.geometry().y(), s.geometry().width(), s.geometry().height()],
        "devicePixelRatio": s.devicePixelRatio(), "logicalDpi": s.logicalDotsPerInch(),
    } for s in dash.application.screens()]
    report = {
        "frames": FRAMES,
        "frame_ms_median": round(statistics.median(times), 2),
        "frame_ms_p95": round(sorted(times)[int(.95 * len(times)) - 1], 2),
        "frame_ms_max": round(max(times), 2),
        "targets_reached": test.summary()["reached"],
        "windows_monitors": [vars(m) for m in mouse.screens.monitors],
        "qt_screens": qt_screens,
        "test_window": None if win is None else {
            "visible": win.isVisible(), "visibility": str(win.visibility()),
            "geometry": [win.x(), win.y(), win.width(), win.height()],
            "screen": win.screen().name() if win.screen() else None,
        },
        "screenshot": str(shot) if shot else None,
        "paused_status": paused_status,
        "paused_flag": paused_flag,
        "test_window_active": None if win is None else win.isActive(),
    }
    dash.closeTrackingTest()
    dash.poll()
    report["test_window_hidden_after_close"] = win is not None and not win.isVisible()
    (out / "smoke-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))

    failures = []
    if win is None or not report["test_window"]["visible"]:
        failures.append("tracking test window did not show")
    if report["frame_ms_median"] > FRAME_BUDGET_MS:
        failures.append(f"median frame {report['frame_ms_median']} ms > {FRAME_BUDGET_MS} ms")
    if report["frame_ms_max"] > FRAME_MAX_MS:
        failures.append(f"a frame took {report['frame_ms_max']} ms (> {FRAME_MAX_MS} ms: UI freeze)")
    if not (paused_flag and "Space" in paused_status):
        failures.append(f"paused test window does not ask for Space: {paused_status!r}")
    if not report["test_window_hidden_after_close"]:
        failures.append("tracking test window still visible after close")
    for f in failures:
        print("FAIL", f)
    dash.preferences.sync()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
