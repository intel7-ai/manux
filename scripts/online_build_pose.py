"""Builds data/online/pose_memory.json: the poses of the known classes from stored samples, and every gesture a person registered from
its stored demonstrations. No training, no torch. Safe to run again.

프로젝트 폴더에서:  .venv\\Scripts\\python.exe scripts/online_build_pose.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[0] = str(ROOT)

import numpy as np  # noqa: E402

from src.online.dataset import SKIP_DO_FRAMES  # noqa: E402
from src.online.pose_memory import POSE_FILE, PoseMemory, pose_features  # noqa: E402
from src.online.registration import USER_SOURCE  # noqa: E402
from src.online.samples import DEFAULT_ROOT, GUIDED_SCHEDULED, USER_CONFIRMED, SampleStore  # noqa: E402

KNOWN_K = {"none": 24}


def poses_of(store: SampleStore, sample) -> np.ndarray:
    skip = int((sample.extra or {}).get("context_frames", 0)) + (SKIP_DO_FRAMES if (sample.extra or {}).get("kind") == "do" else 0)
    frames = store.frames(sample.sample_id)[skip:]
    keep = [f for f in frames if f["lm"] is not None]
    return pose_features(keep, sample.aspect) if keep else np.zeros((0, 63), np.float32)


def main() -> int:
    store = SampleStore()
    memory = PoseMemory()
    by_label = {}
    for sample in store.samples(sources=[GUIDED_SCHEDULED]):
        by_label.setdefault(sample.label, []).append(poses_of(store, sample))
    for label, rows in sorted(by_label.items()):
        x = np.concatenate(rows)
        entry = memory.add_known(label, x, k=KNOWN_K.get(label, 4))
        print(f"  known {label}: {len(entry.prototypes)} poses from {len(x)} frames", flush=True)
    custom = {}
    for sample in store.samples(sources=[USER_CONFIRMED]):
        if (sample.extra or {}).get("reason") == "registration":
            custom.setdefault(sample.label, []).append(sample)
    for label, samples in custom.items():
        rows = [poses_of(store, s) for s in samples]
        ids = np.concatenate([np.full(len(r), i) for i, r in enumerate(rows)])
        entry = memory.add_class(label, np.concatenate(rows), demo_ids=ids, source=USER_SOURCE)
        print(f"  registered {label}: {len(samples)} demonstrations, limit {entry.threshold:.3f}", flush=True)
    path = memory.save(DEFAULT_ROOT / POSE_FILE)
    print("saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
