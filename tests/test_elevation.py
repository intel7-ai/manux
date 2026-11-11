"""Admin windows (Task Manager): detection must never raise, and the restart script waits for this app to close first."""
from src.elevation import blocked_by_elevated_window, foreground_is_elevated, is_elevated, relaunch_script


def test_detection_returns_plain_values_and_never_raises():
    assert isinstance(is_elevated(), bool)
    assert foreground_is_elevated() in (True, False, None)
    assert isinstance(blocked_by_elevated_window(), bool)


def test_the_restart_script_waits_then_starts_the_app_with_its_arguments():
    python = r"C:\app\.venv\Scripts\python.exe"
    text = relaunch_script(python, r"C:\app\main.py", ["--ui", "qt", "--source", "my cam"], r"C:\app", wait_seconds=4)
    lines = text.split("\r\n")
    assert lines[0] == "@echo off"
    assert lines[1] == "ping -n 5 127.0.0.1 >nul"                       # about 4 s: the camera is freed first
    assert lines[2] == r'cd /d "C:\app"'
    assert lines[3] == python + r" C:\app\main.py --ui qt --source " + '"my cam"'
