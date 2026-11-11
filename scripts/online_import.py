"""Guided recordings -> data/online samples (labels from the schedule: `guided_scheduled`). Safe to run again.

프로젝트 폴더에서:  .venv\\Scripts\\python.exe scripts/online_import.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[0] = str(ROOT)   # scripts/profile.py would hide the standard profile module

from src.online.import_guided import import_recordings  # noqa: E402
from src.online.samples import SampleStore  # noqa: E402
from src.training_center import filter_selected_recording_paths  # noqa: E402


def main() -> int:
    paths = filter_selected_recording_paths([str(p) for p in sorted((ROOT / "recordings").glob("*.jsonl.gz"))])
    store = SampleStore()
    total = import_recordings(store, paths, progress=lambda name, c: print(f"{name[:40]:40s} {c}", flush=True))
    print("합계:", total)
    print("저장소:", store.stats())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
