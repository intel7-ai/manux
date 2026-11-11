"""Phase 4: does the model keep old gestures while learning new ones as a stream? Class-incremental, one pass, whole sessions held out.

Start: encoder and head trained on a few classes. Then each other gesture arrives as a stream of small batches, seen once. After each arrival
all classes so far are tested on sessions nothing trained on. Measures forgetting, compared with no replay (naive), Experience Replay, DER++,
and retraining from scratch on everything (the upper line). Saves nothing into models/. Results: logs/online_cl.json (finished runs are skipped).

프로젝트 폴더에서 (GPU 환경):  ..\\hand-mouse-gpuenv\\Scripts\\python.exe scripts/online_cl_compare.py
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[0] = str(ROOT)

import numpy as np  # noqa: E402
import torch  # noqa: E402

from scripts.train_tcn import augment  # noqa: E402
from src.online.baseline import evaluate_samples, train_encoder  # noqa: E402
from src.online.dataset import build_dataset, session_folds, target_positions, window_positions  # noqa: E402
from src.online.models import make_model  # noqa: E402
from src.online.replay import BalancedBuffer, ReservoirBuffer  # noqa: E402
from src.online.samples import GUIDED_SCHEDULED, SampleStore  # noqa: E402
from src.online.trainer import OnlineLearner  # noqa: E402

TUNE2 = "--tune2" in sys.argv
OUT = ROOT / "logs" / ("online_cl_tune2.json" if TUNE2 else "online_cl_tune.json" if "--tune" in sys.argv else "online_cl.json")
ORDER = ["none", "thumb_index", "thumb_middle", "thumb_ring", "thumb_pinky", "palm_side", "v_sign", "thumbs_up"]
BASE = 3
WINDOW, EPOCHS, STREAM_BATCH, STRIDE = 15, 15, 16, 2
TUNE = "--tune" in sys.argv
# method: (kind, capacity, buffer, passes, lr)
TUNED = {"er_res": ("er", 5000, "reservoir", 1, 5e-4), "er_bal": ("er", 5000, "balanced", 1, 5e-4), "derpp_bal": ("derpp", 5000, "balanced", 1, 5e-4),
         "er_bal_x3": ("er", 5000, "balanced", 3, 5e-4), "derpp_bal_x3": ("derpp", 5000, "balanced", 3, 5e-4), "er_bal_x3_lr1e-3": ("er", 5000, "balanced", 3, 1e-3)}
# 2026-11-10 second round, from the best of the first (ER, balanced, 3 passes, lr 1e-3):
# (kind, capacity, buffer, passes, lr, alpha, beta, replay_batch). DER++ alpha/beta were never chosen before; beta 0 = DER.
TUNED2 = {"er_ref": ("er", 5000, "balanced", 3, 1e-3, .5, .5, 32), "er_x5": ("er", 5000, "balanced", 5, 1e-3, .5, .5, 32),
          "er_rb64": ("er", 5000, "balanced", 3, 1e-3, .5, .5, 64),
          "derpp_a01_b05": ("derpp", 5000, "balanced", 3, 1e-3, .1, .5, 32), "derpp_a03_b10": ("derpp", 5000, "balanced", 3, 1e-3, .3, 1.0, 32),
          "derpp_a10_b05": ("derpp", 5000, "balanced", 3, 1e-3, 1.0, .5, 32), "der_a03_b0": ("derpp", 5000, "balanced", 3, 1e-3, .3, 0.0, 32)}
METHODS = {"none": None, "naive": ("naive", 0), "er_1000": ("er", 1000), "er_5000": ("er", 5000), "derpp_1000": ("derpp", 1000), "derpp_5000": ("derpp", 5000), "joint": None}


def test_report(model, data, classes, test_sessions, device):
    data.classes = classes
    indexes = [i for i, s in enumerate(data.samples) if s.session in test_sessions and s.label in classes]
    return evaluate_samples(model, data, indexes, WINDOW, device)


def recalls(report, classes):
    confusion = np.asarray(report["confusion"], dtype=float)
    totals = confusion.sum(axis=1)
    return {c: float(confusion[i, i] / totals[i]) for i, c in enumerate(classes) if totals[i] > 0}


def run_one(data, base_model, method, fold_sessions, seed, device):
    test_set = set(fold_sessions)
    train_samples = [s for s in data.samples if s.session not in test_set]
    by_class = {c: {s.sample_id for s in train_samples if s.label == c} for c in ORDER}
    rng = np.random.default_rng(seed)
    history = []
    t0 = time.perf_counter()
    if method == "joint":
        data.classes = ORDER
        torch.manual_seed(seed)
        model = make_model("tcn", len(ORDER), WINDOW)
        train_encoder(model, data, target_positions(data, {s.sample_id for s in train_samples}), WINDOW, epochs=EPOCHS, device=device, seed=seed, augment=lambda x: augment(x))
        report = test_report(model, data, ORDER, test_set, device)
        return {"final": strip(report), "recall": recalls(report, ORDER), "history": [], "seconds": time.perf_counter() - t0, "update_ms": None}
    frames = data.frames

    def windows(ids):
        positions = target_positions(data, ids)[::STRIDE]
        return positions

    base_classes = ORDER[:BASE]
    model = base_model
    if method == "none":
        # nothing learned after the start: the head only knows the first classes, so later classes stay at zero
        report = test_report(model, data, base_classes, test_set, device)
        return {"final": strip(report), "recall": recalls(report, base_classes), "history": [], "seconds": 0.0, "update_ms": None}
    if method in TUNED2:
        kind, capacity, store, passes, lr, alpha, beta, replay_batch = TUNED2[method]
    else:
        kind, capacity, store, passes, lr = TUNED[method] if method in TUNED else (*METHODS[method], "reservoir", 1, 5e-4)
        alpha, beta, replay_batch = .5, .5, 32
    buffer = (BalancedBuffer if store == "balanced" else ReservoirBuffer)(capacity, seed=seed) if capacity else None
    learner = OnlineLearner(model, method=kind, buffer=buffer, device=device, seed=seed, lr=lr, augment=lambda x: augment(x),
                            alpha=alpha, beta=beta, replay_batch=replay_batch)
    if buffer is not None:
        # the memory starts with what the base model was trained on, with its logits (it is what would be stored while training it)
        start = windows(set().union(*(by_class[c] for c in base_classes)))
        start = rng.permutation(start)
        learner.candidate.eval()
        for i in range(0, len(start), 256):
            part = start[i:i + 256]
            x = frames[window_positions(data.starts, part, WINDOW)]
            with torch.no_grad():
                z = learner.candidate(torch.from_numpy(x).to(device)).cpu().numpy()
            buffer.add(x, data.labels[part], z)
    times = []
    best = {}
    data.classes = base_classes
    report = test_report(learner.candidate, data, base_classes, test_set, device)
    for c, value in recalls(report, base_classes).items():
        best[c] = value
    history.append({"classes": len(base_classes), "macro_f1": report["macro_f1"], "recall": recalls(report, base_classes)})
    for count in range(BASE + 1, len(ORDER) + 1):
        added = ORDER[count - 1]
        learner.add_class(count)
        for _ in range(passes):
            stream = rng.permutation(windows(by_class[added]))
            for i in range(0, len(stream), STREAM_BATCH):
                part = stream[i:i + STREAM_BATCH]
                out = learner.update(frames[window_positions(data.starts, part, WINDOW)], data.labels[part])
                times.append(out["seconds"])
        classes = ORDER[:count]
        report = test_report(learner.candidate, data, classes, test_set, device)
        now = recalls(report, classes)
        history.append({"classes": count, "added": added, "macro_f1": report["macro_f1"], "recall": now, "stream_windows": int(len(stream))})
        for c, value in now.items():
            best[c] = max(best.get(c, 0.0), value)
    final = now
    forgetting = [best[c] - final[c] for c in final if c != ORDER[-1]]
    return {"final": strip(report), "recall": final, "forgetting_mean": float(np.mean(forgetting)), "forgetting": {c: best[c] - final[c] for c in final}, "history": history,
            "seconds": time.perf_counter() - t0, "update_ms": float(np.median(times) * 1000), "updates": learner.updates, "buffer_mb": (buffer.bytes() / 2 ** 20) if buffer else 0.0}


def strip(report):
    return {k: report[k] for k in ("macro_f1", "accuracy", "f1", "reps", "reps_hit", "reps_total", "wrong_reps", "false_fires", "none_minutes", "reaction_median")}


def main() -> int:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    store = SampleStore()
    data = build_dataset(store, store.samples(sources=[GUIDED_SCHEDULED]), ORDER)
    folds = session_folds(data.samples, ORDER, 3)
    done = json.loads(OUT.read_text(encoding="utf-8")) if OUT.is_file() else {}
    OUT.parent.mkdir(exist_ok=True)
    print(f"{len(data.samples)} samples, device {device}", flush=True)
    for seed in (0, 1):
        for f, sessions in enumerate(folds):
            test_set = set(sessions)
            base_ids = {s.sample_id for s in data.samples if s.session not in test_set and s.label in ORDER[:BASE]}
            positions = target_positions(data, base_ids)
            torch.manual_seed(seed)
            base = None
            for method in (TUNED2 if TUNE2 else TUNED if TUNE else METHODS):
                key = f"{method}|seed{seed}|fold{f}"
                if key in done:
                    continue
                if method != "joint" and base is None:  # the same start model for every method of this fold
                    data.classes = ORDER[:BASE]
                    base = make_model("tcn", BASE, WINDOW)
                    train_encoder(base, data, positions, WINDOW, epochs=EPOCHS, device=device, seed=seed, augment=lambda x: augment(x))
                done[key] = {"method": method, "seed": seed, "fold": f, **run_one(data, base, method, sessions, seed, device)}
                OUT.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
                r = done[key]
                print(f"{key}: macro-F1 {r['final']['macro_f1']:.3f} reps {r['final']['reps_hit']}/{r['final']['reps_total']} false {r['final']['false_fires']} "
                      f"forget {r.get('forgetting_mean', 0):.3f} update {r['update_ms']} ms, {r['seconds']:.0f}s", flush=True)
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
