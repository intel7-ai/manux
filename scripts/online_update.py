"""One background learning round: the person's confirmed/corrected samples -> candidate -> checks -> production or not.
With --progress it prints one JSON object per line for the app. --rollback puts the model before the last change back.

프로젝트 폴더에서 (GPU 환경이 있으면 그쪽 python):  python scripts/online_update.py
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[0] = str(ROOT)   # scripts/profile.py would hide the standard profile module that torch imports


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--progress", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--root", default="")
    a = ap.parse_args(argv)

    def emit(event, **info):
        if a.progress:
            print(json.dumps({"event": event, **info}, ensure_ascii=False), flush=True)
        else:
            print(event, info, flush=True)

    from src.online.samples import DEFAULT_ROOT
    root = Path(a.root) if a.root else DEFAULT_ROOT
    try:
        if a.rollback:
            from src.online.versions import ModelManager
            emit("done", status="rolled_back" if ModelManager(root).rollback() else "nothing_to_roll_back")
            return 0
        from scripts.train_tcn import augment
        from src.online.update_job import run_update
        result = run_update(root, emit=emit, augment=lambda x: augment(x))
        emit("done", **result)
        return 0
    except Exception as exc:     # the app shows it; production is untouched by a crash before submit
        emit("error", message=f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
