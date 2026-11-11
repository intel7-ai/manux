"""Phase 2: compare encoders on the same whole-session folds. Saves nothing into models/. Results: logs/online_compare.json (runs already
there are skipped, so it can be stopped and started again).

프로젝트 폴더에서 (GPU 환경):  ..\\hand-mouse-gpuenv\\Scripts\\python.exe scripts/online_compare.py
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[0] = str(ROOT)   # scripts/profile.py would hide the standard profile module that torch imports

import numpy as np  # noqa: E402
import torch  # noqa: E402

from scripts.train_tcn import augment  # noqa: E402
from src.online.baseline import evaluate_samples, inference_ms, train_encoder  # noqa: E402
from src.online.dataset import build_dataset, session_folds, target_positions  # noqa: E402
from src.online.models import make_model  # noqa: E402
from src.online.samples import GUIDED_SCHEDULED, SampleStore  # noqa: E402
from src.online.splits import leaked_groups, split_by_session  # noqa: E402
from src.tcn_features import FEATURES  # noqa: E402

OUT = ROOT / "logs" / "online_compare.json"
CLASSES = ["none", "thumb_index", "thumb_ring", "thumb_middle", "thumb_pinky", "palm_side", "v_sign", "thumbs_up"]   # swipe_tab_left: one session only
FOLDS = 3
EPOCHS = 15


def run(data, folds, results, *, kind, window, embedding=64, supcon=0.0, seed=0, device="cuda"):
    for fold, test_sessions in enumerate(folds):
        key = f"{kind}|w{window}|e{embedding}|s{supcon}|seed{seed}|fold{fold}"
        if key in results:
            continue
        train, test = split_by_session(data.samples, test_sessions)
        assert not leaked_groups(train, test)
        positions = target_positions(data, {s.sample_id for s in train})
        model = make_model(kind, len(CLASSES), window, embedding)
        if device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        info = train_encoder(model, data, positions, window, epochs=EPOCHS, device=device, seed=seed, supcon_weight=supcon,
                             augment=lambda x: augment(x))
        vram = torch.cuda.max_memory_allocated() / 2 ** 20 if device == "cuda" else 0.0
        indexes = [i for i, s in enumerate(data.samples) if s.session in set(test_sessions)]
        report = evaluate_samples(model, data, indexes, window, device)
        model.to("cpu")
        results[key] = {"kind": kind, "window": window, "embedding": embedding, "supcon": supcon, "seed": seed, "fold": fold, **report,
                        "train_seconds": round(info["seconds"], 1), "train_windows": int(len(positions)), "final_loss": info["losses"][-1],
                        "vram_mb": round(vram, 1), "cpu_ms": round(inference_ms(model, window, FEATURES), 3),
                        "parameters": sum(p.numel() for p in model.parameters())}
        OUT.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
        print(f"{key}: macro-F1 {report['macro_f1']:.3f} reps {report['reps_hit']}/{report['reps_total']} false {report['false_fires']} "
              f"({report['none_minutes']:.1f} min) wrong {report['wrong_reps']} train {info['seconds']:.0f}s", flush=True)


def score(results, **want):
    rows = [r for r in results.values() if all(r[k] == v for k, v in want.items())]
    return float(np.mean([r["macro_f1"] for r in rows])) if rows else -1.0


def main() -> int:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    store = SampleStore()
    samples = store.samples(sources=[GUIDED_SCHEDULED])
    t0 = time.perf_counter()
    data = build_dataset(store, samples, CLASSES)
    print(f"{len(data.samples)} samples, {len(data.frames) - 1} frames, {int((data.labels >= 0).sum())} target windows, "
          f"{time.perf_counter() - t0:.0f}s to load, device {device}", flush=True)
    folds = session_folds(data.samples, CLASSES, FOLDS)
    for i, fold in enumerate(folds):
        labels = {}
        for sample in data.samples:
            if sample.session in fold:
                labels[sample.label] = labels.get(sample.label, 0) + 1
        print(f"fold {i}: {len(fold)} sessions, {labels}", flush=True)
    results = json.loads(OUT.read_text(encoding="utf-8")) if OUT.is_file() else {}
    OUT.parent.mkdir(exist_ok=True)
    # A: which family, at the window the app uses now
    for seed in (0, 1):
        for kind in ("tcn", "gru", "mlp"):
            run(data, folds, results, kind=kind, window=15, seed=seed, device=device)
    # B: longer windows
    for kind in ("tcn", "gru"):
        for window in (32, 64):
            run(data, folds, results, kind=kind, window=window, device=device)
    best = max(("tcn", "gru"), key=lambda k: score(results, kind=k, window=15, embedding=64, supcon=0.0))
    best_window = max((15, 32, 64), key=lambda w: score(results, kind=best, window=w, embedding=64, supcon=0.0, seed=0))
    print("best so far:", best, best_window, flush=True)
    # C: embedding size, D: contrastive
    for embedding in (128, 256):
        run(data, folds, results, kind=best, window=best_window, embedding=embedding, device=device)
    for seed in (0, 1):
        run(data, folds, results, kind=best, window=best_window, seed=seed, device=device)
        run(data, folds, results, kind=best, window=best_window, supcon=.2, seed=seed, device=device)
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
