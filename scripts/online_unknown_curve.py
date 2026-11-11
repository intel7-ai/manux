"""Spec 8: the false acceptance / false rejection trade-off of a registered gesture's distance limit (src/online/pose_memory.py).

For each gesture G of the stored guided samples: the other gestures are the known classes (from every session but G's registration
session), G is registered from DEMOS repetitions of one session, and the limit factor is swept. On the other sessions:
  FRR = share of G's repetitions it never recognises (RUN_FRAMES frames in a row, as the app),
  FAR = share of the other pieces (other gestures and free "none" stretches) taken as G.
The app's factor is LIMIT_FACTOR. Writes logs/online_unknown_curve.json. No torch.

프로젝트 폴더에서:  .venv\\Scripts\\python.exe scripts/online_unknown_curve.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[0] = str(ROOT)

import numpy as np  # noqa: E402

from scripts.online_build_pose import KNOWN_K, poses_of  # noqa: E402
from src.online.pose_memory import LIMIT_FACTOR, LIMIT_QUANTILE, RUN_FRAMES, PoseMemory, distances, pose_kmeans  # noqa: E402
from src.online.samples import GUIDED_SCHEDULED, SampleStore  # noqa: E402

DEMOS = 5
FACTORS = (.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)
OUT = ROOT / "logs" / "online_unknown_curve.json"


def recognised(memory: PoseMemory, poses: np.ndarray, label: str, run: int = RUN_FRAMES) -> bool:
    if not len(poses):
        return False
    index, _, _ = memory.classify_arrays(poses)
    hit = index == memory.labels.index(label)
    count = 0
    for h in hit:
        count = count + 1 if h else 0
        if count >= run:
            return True
    return False


def main() -> int:
    store = SampleStore()
    samples = store.samples(sources=[GUIDED_SCHEDULED])
    poses = {s.sample_id: poses_of(store, s) for s in samples}
    gestures = sorted({s.label for s in samples} - {"none"})
    results = {}
    for g in gestures:
        mine = [s for s in samples if s.label == g and len(poses[s.sample_id])]
        sessions = sorted({s.session for s in mine}, key=lambda x: -sum(s.session == x for s in mine))
        if len(sessions) < 2:
            print(f"  {g}: 세션이 하나뿐이라 건너뜀")
            continue
        reg_session = sessions[0]
        demos = sorted((s for s in mine if s.session == reg_session), key=lambda s: s.created)[:DEMOS]
        if len(demos) < 3:
            continue
        test = [s for s in samples if s.session != reg_session and len(poses[s.sample_id])]
        known = PoseMemory()
        for label in sorted({s.label for s in samples} - {g}):
            rows = [poses[s.sample_id] for s in samples if s.label == label and s.session != reg_session and len(poses[s.sample_id])]
            if rows:
                known.add_known(label, np.concatenate(rows), k=KNOWN_K.get(label, 4))
        rows = [poses[s.sample_id] for s in demos]
        ids = np.concatenate([np.full(len(r), i) for i, r in enumerate(rows)])
        known.add_class(g, np.concatenate(rows), demo_ids=ids, source="user_registered")
        entry = known.classes[g]
        base = entry.threshold / LIMIT_FACTOR          # the LIMIT_QUANTILE spread before the factor
        curve = []
        for factor in FACTORS:
            entry.threshold = base * factor
            pos = [s for s in test if s.label == g]
            neg = [s for s in test if s.label != g]
            frr = 1 - sum(recognised(known, poses[s.sample_id], g) for s in pos) / max(1, len(pos))
            far = sum(recognised(known, poses[s.sample_id], g) for s in neg) / max(1, len(neg))
            curve.append({"factor": factor, "frr": round(frr, 3), "far": round(far, 3), "positives": len(pos), "negatives": len(neg)})
        results[g] = {"registered_from": reg_session, "demos": len(demos), "curve": curve}
        print(f"  {g}: " + "  ".join(f"x{c['factor']}: FRR {c['frr']:.2f} FAR {c['far']:.3f}" for c in curve), flush=True)
    mean = []
    for i, factor in enumerate(FACTORS):
        rows = [r["curve"][i] for r in results.values()]
        if rows:
            mean.append({"factor": factor, "frr": round(float(np.mean([r["frr"] for r in rows])), 3),
                         "far": round(float(np.mean([r["far"] for r in rows])), 3)})
    print("평균: " + "  ".join(f"x{m['factor']}: FRR {m['frr']:.2f} FAR {m['far']:.3f}" for m in mean))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"limit_quantile": LIMIT_QUANTILE, "app_factor": LIMIT_FACTOR, "demos": DEMOS, "run_frames": RUN_FRAMES,
                               "gestures": results, "mean": mean}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
