"""
Run the real `main.py --ui qt` loop for a few seconds with a fake 30 fps camera.

Run from hand-mouse/:  uv run --locked --extra qt python scripts/loop_smoke.py
No webcam and no hand: this catches wiring errors in the main loop (dashboard, pointer
engine, crop, failure recording, tracking test hooks) on the machine it runs on.
Exits non-zero if the loop raises or does not finish.
"""
import runpy
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.camera as cam  # noqa: E402

FRAMES = 90


def fake_open(self):
    return True


def fake_read(self):
    time.sleep(1 / 30)
    self._update_fps()
    return True, np.full((720, 1280, 3), 60, np.uint8)


cam.CameraStream.open = fake_open
cam.CameraStream.read = fake_read
cam.CameraStream.release = lambda self: None
cam.CameraStream.actual_mode = lambda self: (1280, 720, 30.0)

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")
sys.argv = ["main.py", "--ui", "qt", "--dry-run", "--source", "0", "--max-frames", str(FRAMES)]
start = time.perf_counter()
try:
    runpy.run_path(str(ROOT / "main.py"), run_name="__main__")
    code = 0
except SystemExit as exc:
    code = exc.code or 0
elapsed = time.perf_counter() - start
print(f"[loop smoke] {FRAMES} frames, exit {code}, {elapsed:.1f} s")
if code != 0 or elapsed > 60:
    sys.exit(1)
