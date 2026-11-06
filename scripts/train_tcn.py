"""
Train the gesture TCN on the guided recordings (recordings/*-guided.jsonl.gz) and compare it with the rules.

Run from hand-mouse/:  uv run --locked --extra qt --extra train python scripts/train_tcn.py [--folds] [--epochs 25]

- Every recorded frame becomes a 0.5 s window (15 frames) of src/tcn_features.py features, labelled with what the
  user was asked to do at its last frame: none / thumb_index / thumb_ring ("get ready" and "waiting" frames are left out).
- Honest test: one posture is held out entirely (default: the last one, the phone moved). `--folds` repeats that
  for each posture. The final model is then trained on all postures and exported to models/gesture_tcn.onnx with
  models/gesture_tcn.json (classes, window, feature version, decision threshold, where the data came from).
- Compared per repetition ("did it recognise the gesture asked for") and over the half-fist stretches ("how many
  times did it fire although nothing was asked") - the same two numbers measured for the rules, from the rule
  decisions stored in the recording.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# scripts/profile.py would shadow the standard `profile` module that torch imports: drop scripts/ from the path.
sys.path = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn as nn  # noqa: E402

from src.recorder import public_source  # noqa: E402
from src.tcn_features import FEATURE_VERSION, FEATURES, PIPELINE_VERSION  # noqa: E402
from src.tcn_augmentation import augment  # noqa: E402
from src.gesture_catalog import CLASSES, TASKS, BY_KEY  # noqa: E402

CLICKY = {"right_click", "left_click", "task_switcher"}
STARTY = {"scroll_grab", "drag_hold"}
WINDOW = 15
PROGRESS = False          # --progress: one JSON object per line on stdout (the app's training center reads them)


def emit(event: str, **info) -> None:
    """Progress for the app (JSON lines with --progress) or for a person reading the console."""
    if PROGRESS:
        print(json.dumps({"event": event, **info}, ensure_ascii=False), flush=True)
    elif event == "epoch" and (info["epoch"] % 5 == 0 or info["epoch"] == info["epochs"]):
        print(f"    epoch {info['epoch']:2d}  loss {info['loss']:.3f}", flush=True)
    elif event in ("note",):
        print(info.get("text", ""), flush=True)


# ---------------------------------------------------------------- data
def load(paths, selected_tasks=None):
    from src.capture_dataset import load_sessions
    return load_sessions(paths, WINDOW, selected_tasks=selected_tasks)


def pick(sessions, conds):
    xs, ys = [], []
    for s in sessions:
        m = sample_mask(s, conds)
        xs.append(s["x"][m])
        ys.append(s["y"][m])
    return np.concatenate(xs), np.concatenate(ys)


def sample_mask(session, conds):
    eligible = np.isin(session["cond"], conds)
    # Purge windows crossing a held-out posture; preserving physical context
    # must not leak that posture into training through an adjacent window.
    safe = eligible.copy()
    for offset in range(1, WINDOW + 1):
        safe[offset:] &= eligible[:-offset] | (session["segments"][offset:] != session["segments"][:-offset])
    return (session["y"] >= 0) & safe


def sample_weights(sessions, conds):
    groups = []
    for si, s in enumerate(sessions):
        mask = sample_mask(s, conds)
        groups += [(int(y), si, int(c)) for y, c in zip(s["y"][mask], s["cond"][mask])]
    counts = Counter(groups)
    per_label = Counter(key[0] for key in counts)
    positives = max(1, len(per_label) - 1)
    # Most real use is movement/neutral. Equal positive/neutral class priors
    # produced too many gesture activations in free movement; retain that prior
    # while balancing sessions and poses *within* each class.
    neutral_prior = .5 if positives == 1 else .65
    return np.asarray([(neutral_prior if key[0] == 0 else (1 - neutral_prior) / positives) /
                       (counts[key] * per_label[key[0]]) for key in groups], np.float64)


# ---------------------------------------------------------------- model
class TCN(nn.Module):
    """Causal dilated 1-D convolutions over 15 frames (receptive field 15); the last step is classified."""

    def __init__(self, n_in: int = FEATURES, ch: int = 64, n_out: int = len(CLASSES), drop: float = 0.2):
        super().__init__()
        layers, c = [], n_in
        for d in (1, 2, 4):
            layers += [nn.ConstantPad1d((2 * d, 0), 0.0), nn.Conv1d(c, ch, 3, dilation=d), nn.ReLU(), nn.Dropout(drop)]
            c = ch
        self.body = nn.Sequential(*layers)
        self.head = nn.Linear(ch, n_out)

    def forward(self, x):                      # x: (batch, time, features)
        h = self.body(x.transpose(1, 2))
        return self.head(h[:, :, -1])


def train(x, y, epochs: int, seed: int = 0, stage: str = "", losses=None, classes=None, weights=None, samples_per_epoch=16384):
    classes = classes or CLASSES
    torch.manual_seed(seed)
    np.random.seed(seed)
    model = TCN(n_out=len(classes))
    counts = np.bincount(y, minlength=len(classes)).astype(np.float64)
    weight = torch.tensor((counts.sum() / np.maximum(counts, 1)) ** 0.5, dtype=torch.float32)
    lossf = nn.CrossEntropyLoss(weight=weight / weight.mean() if weights is None else None)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    xt, yt = torch.from_numpy(x), torch.from_numpy(y)
    for ep in range(epochs):
        model.train()
        count = min(len(xt), samples_per_epoch)
        perm = (torch.randperm(len(xt))[:count] if weights is None else
                torch.multinomial(torch.as_tensor(weights), count, replacement=True))
        total = 0.0
        for i in range(0, len(perm), 256):
            idx = perm[i:i + 256]
            opt.zero_grad()
            loss = lossf(model(augment(xt[idx])), yt[idx])
            loss.backward()
            opt.step()
            total += float(loss.detach()) * len(idx)
        avg = total / len(perm)
        if losses is not None:
            losses.append(round(avg, 4))
        emit("epoch", stage=stage, epoch=ep + 1, epochs=epochs, loss=round(avg, 4))
    return model


def predict(model, x) -> np.ndarray:
    model.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(x), 2048):
            out.append(torch.softmax(model(torch.from_numpy(x[i:i + 2048])), dim=1).numpy())
    return np.concatenate(out)


# ---------------------------------------------------------------- event-level evaluation
def runs(mask: np.ndarray, k: int):
    """Start indices of runs of at least k True frames."""
    starts, n = [], 0
    for i, v in enumerate(mask):
        n = n + 1 if v else 0
        if n == k:
            starts.append(i)
    return starts


def evaluate(session, conds, prob=None, thr=0.7, k=3):
    """{"reps": {task: (recognised, total)}, "misfires": {kind: n}, "confused": n} for the model (prob given) or rules."""
    frames = session["frames"]
    classes = session.get("classes", CLASSES)
    rep_frames = defaultdict(list)
    free = []
    for i, f in enumerate(frames):
        lab = f.get("label") or {}
        group = int(session["cond"][i])
        if group not in conds or session["y"][i] < 0:
            continue
        task = BY_KEY.get(lab.get("task"))
        if task and not task.free and task.label in classes and lab.get("phase") in ("do", "rest"):
            rep_frames[(group, lab["task"], lab.get("set"), lab["rep"])].append(i)
        elif task and task.free and lab.get("phase") == "free":
            free.append(i)
    # Rest alone is not a performed repetition.
    rep_frames = {key: idx for key, idx in rep_frames.items()
                  if any(frames[i]["label"].get("phase") == "do" and session["y"][i] == classes.index(BY_KEY[key[1]].label) for i in idx)}
    recog, total, confused = Counter(), Counter(), 0
    misfires = Counter()
    from src.capture_dataset import frame_durations
    durations = frame_durations(frames)
    free_by_task = {}
    for i in free:
        entry = free_by_task.setdefault(frames[i]["label"]["task"], {"minutes": 0., "misfires": 0})
        entry["minutes"] += durations[i] / 60
    if prob is not None:
        active = [c for c, name in enumerate(classes) if name != "none"]
        fired = {c: np.zeros(len(frames), dtype=bool) for c in active}
        valid = (session["y"] >= 0) & np.isin(session["cond"], conds)
        for c in active:
            run, previous = 0, None
            for i, f in enumerate(frames):
                lab = f.get("label") or {}
                token = (session["segments"][i], lab.get("set"), lab.get("rep"))
                if token != previous:
                    run = 0
                run = run + 1 if valid[i] and prob[i, c] >= thr else 0
                fired[c][i] = run == k
                previous = token
        for (_, task, _, _), idx in rep_frames.items():
            total[task] += 1
            want = classes.index(BY_KEY[task].label)
            if fired[want][idx].any():
                recog[task] += 1
            if any(fired[c][idx].any() for c in active if c != want):
                confused += 1
        free_set = np.zeros(len(frames), dtype=bool)
        free_set[free] = True
        for c in active:
            misfires[classes[c]] = int((fired[c] & free_set).sum())
        for i in free:
            free_by_task[frames[i]["label"]["task"]]["misfires"] += sum(int(fired[c][i]) for c in active)
    else:
        acts = [(f.get("rule") or {}).get("action") for f in frames]
        for (_, task, _, _), idx in rep_frames.items():
            observed = [frames[i].get("rule") or {} for i in idx]
            modern = any(r.get("evaluated") for r in observed)
            if not modern and not BY_KEY[task].action:
                continue  # old recordings never ran the sign/motion detectors
            total[task] += 1
            a = {acts[i] for i in idx}
            targets = {key for r in observed for key in r.get("targets", [])}
            if ((task in targets) if modern else (BY_KEY[task].action in a)):
                recog[task] += 1
            if ((targets - {task}) if modern else (a & (CLICKY | STARTY) - {BY_KEY[task].action})):
                confused += 1
        prev, previous_i = None, -2
        for i in free:
            a = acts[i]
            if i != previous_i + 1:
                prev = None
            count = 0
            if a in CLICKY:
                misfires[a] += 1
                count += 1
            elif a in STARTY and prev != a:
                misfires[a] += 1
                count += 1
            for target in (frames[i].get("rule") or {}).get("targets", []):
                if target in BY_KEY and not BY_KEY[target].action:
                    misfires[target] += 1
                    count += 1
            free_by_task[frames[i]["label"]["task"]]["misfires"] += count
            prev = a
            previous_i = i
    return {"reps": {t: (recog[t], total[t]) for t in total}, "misfires": dict(misfires), "confused": confused,
            "free_by_task": free_by_task}


def free_minutes(session, conds) -> float:
    from src.capture_dataset import frame_durations
    lengths = frame_durations(session["frames"])
    return sum(lengths[i] for i, f in enumerate(session["frames"])
               if (f.get("label") or {}).get("phase") == "free"
               and session["cond"][i] in conds and session["y"][i] >= 0) / 60


def per_gesture(summary, windows_per_class, train_acc, classes=None, *, by_task=False):
    """Scores per gesture for the training center: held-out recognition, misfires per minute, data, fit."""
    out = {}
    classes = classes or CLASSES
    names = [t.key for t in TASKS if t.label in classes] if by_task else classes
    for name in names:
        label = BY_KEY[name].label if by_task else name
        keys = [name] if by_task else [t.key for t in TASKS if t.label == label]
        g = {"windows": int(windows_per_class.get(label, 0)), "train_accuracy": train_acc.get(label)}
        if label != "none":
            a = sum(m["tcn"]["reps"].get(key, (0, 0))[0] for m in summary for key in keys)
            b = sum(m["tcn"]["reps"].get(key, (0, 0))[1] for m in summary for key in keys)
            ra = sum(m["rules"]["reps"].get(key, (0, 0))[0] for m in summary for key in keys)
            rb = sum(m["rules"]["reps"].get(key, (0, 0))[1] for m in summary for key in keys)
            g.update(recognised=a, total=b, rate=round(a / b, 3) if b else None,
                     rules_rate=round(ra / rb, 3) if rb else None)
        else:
            if by_task:
                entries = [m["tcn"].get("free_by_task", {}).get(name, {}) for m in summary]
                mins = sum(e.get("minutes", 0) for e in entries)
                tcn = sum(e.get("misfires", 0) for e in entries)
                rules = sum(m["rules"].get("free_by_task", {}).get(name, {}).get("misfires", 0) for m in summary)
            else:
                mins = sum(m.get("free_minutes", 0.0) for m in summary)
                tcn = sum(sum(m["tcn"]["misfires"].values()) for m in summary)
                rules = sum(sum(m["rules"]["misfires"].values()) for m in summary)
            g.update(free_minutes=round(mins, 2), misfires=tcn, rules_misfires=rules,
                     misfires_per_min=round(tcn / mins, 2) if mins else None,
                     rules_misfires_per_min=round(rules / mins, 2) if mins else None)
        out[name] = g
    return out


def show(name, r):
    reps = "  ".join(f"{t} {a}/{b}" for t, (a, b) in sorted(r["reps"].items()))
    mis = sum(r["misfires"].values())
    print(f"  {name:6s} recognised: {reps}  | wrong gesture in reps: {r['confused']}  | fired during half-fist moving: {mis} {r['misfires']}")


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recordings", default=str(ROOT / "recordings" / "*-guided.jsonl.gz"))
    ap.add_argument("--test-condition", type=int, default=2)
    ap.add_argument("--folds", action="store_true", help="test every posture in grouped cross-validation")
    ap.add_argument("--validation-folds", type=int, default=5, help="number of disjoint posture groups; every posture is tested once")
    ap.add_argument("--samples-per-epoch", type=int, default=16384, help="fresh balanced samples per epoch (limits redundant overlapping frames)")
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 1), help="CPU training threads")
    ap.add_argument("--thr", type=float, default=0.7)
    ap.add_argument("--k", type=int, default=3, help="frames in a row above the threshold that count as the gesture")
    ap.add_argument("--no-export", action="store_true")
    ap.add_argument("--progress", action="store_true", help="JSON progress lines for the app")
    ap.add_argument("--output-dir", type=Path, default=ROOT / "models", help="model output folder")
    ap.add_argument("--tasks", "--gestures", dest="tasks", nargs="+", help="train only these tasks; preserve all other learned gestures")
    a = ap.parse_args()
    global PROGRESS
    PROGRESS = a.progress
    if a.epochs < 1:
        ap.error("--epochs must be at least 1")
    if a.threads < 1:
        ap.error("--threads must be at least 1")
    if a.validation_folds < 2 or a.samples_per_epoch < 1:
        ap.error("--validation-folds must be >=2 and --samples-per-epoch >=1")
    torch.set_num_threads(a.threads)
    from src.gesture_models import training_lock
    try:
        with training_lock(a.output_dir / "gestures"):
            return run_training(a)
    except (ValueError, OSError) as exc:
        emit("error", text=str(exc))
        return 1


def run_training(a):
    selected_tasks, selected_classes = None, None
    if a.tasks is not None:
        from src.capture_dataset import training_selection
        try:
            selected_tasks, selected_classes = training_selection(a.tasks)
        except ValueError as exc:
            emit("error", text=str(exc))
            return 1

    # The list is fixed here: files that arrive (git pull) or finish recording while training wait for the next run.
    from src.recorder import finished_recordings
    paths = sorted(glob.glob(a.recordings))
    folders = sorted({str(Path(p).parent) for p in paths})
    usable = {str(p) for f in folders for p in finished_recordings(Path(f))[0]}
    skipped = [p for p in paths if str(Path(p)) not in usable]
    paths = [p for p in paths if str(Path(p)) in usable]
    for p in skipped:
        emit("skipped", file=Path(p).name)
        if not PROGRESS:
            print(f"skipped (unfinished or unreadable, next run): {Path(p).name}")
    if not paths:
        emit("error", text=f"녹화가 없어요: {a.recordings}")
        print("no recordings found:", a.recordings)
        return 1
    t0 = time.perf_counter()
    sessions = load(paths, selected_tasks)
    source_classes = selected_classes or CLASSES
    present = {int(y) for s in sessions for y in s["y"] if y >= 0}
    classes = [name for i, name in enumerate(source_classes) if i in present]
    if selected_classes and set(selected_classes) - set(classes):
        missing = set(selected_classes) - set(classes)
        names = [BY_KEY[c].name for c in selected_classes if c in missing]
        emit("error", text="유효한 녹화가 부족해 학습하지 않았어요: " + ", ".join(names))
        return 1
    if "none" not in classes or len(classes) < 2:
        emit("error", text="중립 구간과 한 가지 이상 동작의 유효 녹화가 필요합니다.")
        return 1
    for s in sessions:
        original = s["y"].copy()
        for c, name in enumerate(classes):
            s["y"][original == source_classes.index(name)] = c
        s["classes"] = classes
    sessions = [s for s in sessions if (s["y"] >= 0).any()]
    conds = sorted({int(c) for s in sessions for c, y in zip(s["cond"], s["y"]) if c >= 0 and y >= 0})
    xa, ya = pick(sessions, conds)
    per_class = dict(Counter(classes[i] for i in ya))
    if not PROGRESS:
        print(f"{len(paths)} recording(s), postures {conds}, {len(ya)} labelled windows {per_class}"
              f"  ({time.perf_counter() - t0:.1f} s to load)")
    n_folds = min(a.validation_folds, len(conds))
    held = ([conds[i::n_folds] for i in range(n_folds)] if a.folds else
            ([] if a.test_condition < 0 else [[a.test_condition]]))
    emit("loaded", recordings=len(paths), postures=conds, windows=int(len(ya)), per_class=per_class, folds=len(held))
    summary = []
    interaction_reports = []
    for test in held:
        train_conds = [c for c in conds if c not in test]
        x, y = pick(sessions, train_conds)
        if not set(test) <= set(conds) or not len(y) or len(set(y)) < len(classes):
            emit("note", text=f"조건 {test} 제외 평가는 보류: 나머지 조건에도 학습할 모든 동작의 데이터가 필요합니다.")
            emit("fold_skip", posture=test, fold=held.index(test) + 1)
            continue
        if not PROGRESS:
            print(f"\n== held-out posture {test} (train on {train_conds}, {len(y)} windows)")
        emit("fold_start", posture=test, fold=held.index(test) + 1, folds=len(held))
        t1 = time.perf_counter()
        model = train(x, y, a.epochs, stage=f"fold{held.index(test) + 1}", classes=classes,
                      weights=sample_weights(sessions, train_conds), samples_per_epoch=a.samples_per_epoch)
        if not PROGRESS:
            print(f"    trained in {time.perf_counter() - t1:.0f} s")
        emit("evaluation_start", posture=test, fold=held.index(test) + 1)
        for s in sessions:
            prob = predict(model, s["x"])
            rule = evaluate(s, test)
            tcn = evaluate(s, test, prob, a.thr, a.k)
            from src.tcn_evaluation import replay_interactions
            interaction_reports.append(replay_interactions(s, test, prob, a.thr, a.k))
            if not PROGRESS:
                show("rules", rule)
                show("TCN", tcn)
            summary.append((test, rule, tcn, free_minutes(s, test)))
            emit("fold_result", posture=test, rules=rule, tcn=tcn)
        emit("evaluation_done", posture=test, fold=held.index(test) + 1)

    if a.no_export:
        return 0
    if not PROGRESS:
        print(f"\n== final model on all postures {conds}")
    emit("final_start")
    losses: list = []
    model = train(xa, ya, a.epochs, stage="final", losses=losses, classes=classes,
                  weights=sample_weights(sessions, conds), samples_per_epoch=a.samples_per_epoch)
    emit("evaluation_start")
    pred = predict(model, xa).argmax(1)
    train_acc = {name: round(float((pred[ya == c] == c).mean()), 3) for c, name in enumerate(classes) if (ya == c).any()}
    output_dir = a.output_dir
    from src.tcn_models import registry_path
    independent_output = True  # immutable model versions, including full-catalog training
    if independent_output:
        output_dir = output_dir / "selected" / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "gesture_tcn.onnx"
    emit("export_start")
    model.eval()
    torch.onnx.export(model, torch.zeros(1, WINDOW, FEATURES), str(out), input_names=["x"], output_names=["logits"],
                      dynamic_axes={"x": {0: "batch"}, "logits": {0: "batch"}}, opset_version=18,
                      external_data=False)
    results = [{"posture": t, "rules": r, "tcn": m, "free_minutes": round(fm, 2)} for t, r, m, fm in summary]
    from src.tcn_evaluation import summarise_interactions
    info = {
        "classes": classes, "window": WINDOW, "features": FEATURES, "feature_version": FEATURE_VERSION,
        "pipeline_version": PIPELINE_VERSION, "augmentation": "canonical-mirror-depth-jitter-speed-v2",
        "sampling": "neutral-50pct-binary-65pct-multiclass; balanced-session-condition", "deployment": "shadow",
        "samples_per_epoch": min(len(ya), a.samples_per_epoch), "validation_groups": held,
        "threshold": a.thr, "frames_in_a_row": a.k, "epochs": a.epochs,
        "trained": time.strftime("%Y-%m-%d %H:%M:%S"),
        "data": [{"file": Path(s["path"]).name, "pc": public_source(s["meta"])[0],
                  "camera": dict(s["meta"].get("camera") or {}, note=public_source(s["meta"])[1]),
                  "frames": len(s["frames"]), "windows": int((s["y"] >= 0).sum()),
                  "size": Path(s["path"]).stat().st_size, "mtime_ns": Path(s["path"]).stat().st_mtime_ns} for s in sessions],
        "held_out_results": results,
        "interaction_validation": summarise_interactions(interaction_reports, classes),
        "interaction_validation_tasks": summarise_interactions(interaction_reports, [t.key for t in TASKS if t.label in classes and not t.free]),
        "validation_scope": "held-out-condition; recorded instruction labels; no independent user/session guarantee",
        "condition_groups": sessions[0]["condition_groups"],
        "loss_curve": losses, "train_accuracy": train_acc,
        "per_gesture": per_gesture(results, per_class, train_acc, classes),
        "per_task": per_gesture(results, per_class, train_acc, classes, by_task=True),
        "training_tasks": selected_tasks,
        "task_targets": {task.key: task.label for task in TASKS},
    }
    out.with_suffix(".json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    from src.tcn_models import load_predictor
    from src.tcn_validation import publication_issues
    load_predictor(out)
    issues = publication_issues(info)
    if independent_output and not issues:
        from src.tcn_models import publish_selected
        publish_selected(a.output_dir / "gesture_tcn.onnx", out, [c for c in classes if c != "none"])
    if issues:
        emit("note", text="모델 구분 성능이 기준에 못 미쳐 기존 모델을 유지합니다. " + " / ".join(issues))
    if not PROGRESS:
        print(f"saved {out.name} ({out.stat().st_size // 1024} KB) and gesture_tcn.json")
    emit("done", model=str(out), kb=out.stat().st_size // 1024, per_gesture=info["per_gesture"], tasks=selected_tasks,
         published=not issues, publication_issues=issues)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
