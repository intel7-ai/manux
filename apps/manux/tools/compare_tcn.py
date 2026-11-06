"""Compare TCN architectures on the same recordings (decision support for multi-TCN, 2026-11-05).

    cd apps/manux
    uv run --python 3.12 --extra train python tools/compare_tcn.py <recordings...> [--axis posture|person|condition]

Every architecture uses the same cached local3d features, Dan's TCN body (3 causal dilated convs,
64 channels, window 15) and his augmentation, and is scored with the shared event metric
(`tcn_data.evaluate`: one event per prediction run, threshold + frames in a row) on held-out groups.

Part 1, all classes known:
  A  one multi-class TCN (softmax: classes exclude each other)
  B  one binary TCN per gesture, all other classes as negatives (Dan's per-gesture briefing);
     arbitration = highest score above the threshold; also counts "double fires" (two binary models
     above the threshold for `count` frames at the same moment) that independent models would cause.
Part 2, adding a gesture (the last class plays the new "custom" gesture):
  A-retrain   retrain the multi-class model on all classes
  B-add       keep the old binary models (never saw the new gesture), add a binary model for it
  B-retrain   retrain every binary model with the new gesture as a negative
  C-head      freeze a trunk trained on the old classes; train only a small head for the new gesture
              on cached embeddings (all other classes negative); combine with the old head
Reports recall of the new gesture, old models firing on new-gesture attempts, misfires on `none`,
and the time each option needs to add the gesture. One person's data can rule options out, it
cannot show that one generalises (D-005).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np

from manux.data.catalog import NONE
from manux.perception.local3d import FEATURES, FEATURE_VERSION, windows
from tcn_data import AXES, class_mapping, class_names, evaluate, groups, load, predicted_events, samples

from training_device import select, record, synchronize, augment

TRAINING_DEVICE = "cpu"
WINDOW = 15


# ----------------------------------------------------------------------------- model pieces (torch)
def _torch():
    import torch
    from torch import nn
    return torch, nn


def body_module():
    torch, nn = _torch()
    layers, channels = [], FEATURES
    for dilation in (1, 2, 4):
        layers += [nn.ConstantPad1d((2 * dilation, 0), 0), nn.Conv1d(channels, 64, 3, dilation=dilation),
                   nn.ReLU(), nn.Dropout(0.2)]
        channels = 64
    return nn.Sequential(*layers)


class Net:
    """body + linear head; `embed` gives the body's last-step 64 values."""

    def __init__(self, n_out: int, body=None):
        torch, nn = _torch()
        self.body = body if body is not None else body_module()
        self.head = nn.Linear(64, n_out)

    def embed(self, x):
        return self.body(x.transpose(1, 2))[:, :, -1]

    def __call__(self, x):
        return self.head(self.embed(x))

    def parameters(self, head_only=False):
        return list(self.head.parameters()) + ([] if head_only else list(self.body.parameters()))

    def train_mode(self, on, head_only=False):
        self.head.train(on)
        self.body.train(on and not head_only)


def fit(x, y, n_out, epochs, *, body=None, head_only=False, seed=0):
    torch, nn = _torch()
    torch.manual_seed(seed)
    execution = select(TRAINING_DEVICE)
    net = Net(n_out, body)
    net.body.to(execution.actual)
    net.head.to(execution.actual)
    counts = np.bincount(y, minlength=n_out).astype(np.float64)
    if not counts.all():
        raise ValueError("a class has no training windows")
    weights = torch.tensor(counts ** -0.5, dtype=torch.float32, device=execution.actual)
    loss_fn = nn.CrossEntropyLoss(weight=weights / weights.mean())
    optimizer = torch.optim.Adam(net.parameters(head_only), lr=1e-3, weight_decay=1e-4)
    inputs, labels = torch.from_numpy(x), torch.from_numpy(y)
    net.train_mode(True, head_only)
    for _ in range(epochs):
        for batch in torch.randperm(len(inputs)).split(256):
            optimizer.zero_grad()
            xb = augment(inputs[batch].to(execution.actual))
            if head_only:
                with torch.no_grad():
                    emb = net.embed(xb)
                out = net.head(emb)
            else:
                out = net(xb)
            loss_fn(out, labels[batch].to(execution.actual)).backward()
            optimizer.step()
    synchronize(execution.actual)
    net.train_mode(False)
    return net


def softmax_of(net, inputs):
    torch, _ = _torch()
    with torch.inference_mode():
        return torch.cat([net(b.to(next(net.head.parameters()).device)).softmax(1).cpu() for b in torch.from_numpy(inputs).split(2048)]).numpy()


# ----------------------------------------------------------------------------- architectures
def train_multi(segs, classes, epochs):
    x, y = samples(segs, classes, WINDOW)
    net = fit(x, y, len(classes), epochs)
    return lambda w: softmax_of(net, w)


def train_binary(segs, classes, gesture, epochs, negatives=None):
    """Binary model for `gesture`; negatives = the other classes it is allowed to see."""
    seen = [NONE] + sorted(set(negatives if negatives is not None else classes) - {NONE, gesture}) + [gesture]
    x, y = samples(segs, seen, WINDOW)
    yb = (y == len(seen) - 1).astype(np.int64)
    net = fit(x, yb, 2, epochs)
    return lambda w: softmax_of(net, w)[:, 1]


def combine_binary(scores: dict, classes):
    """Per-gesture yes-scores -> class probabilities: highest gesture wins, none = 1 - best."""
    def infer(w):
        cols = {g: f(w) for g, f in scores.items()}
        n = len(next(iter(cols.values())))
        out = np.zeros((n, len(classes)), dtype=np.float32)
        best = np.zeros(n, dtype=np.float32)
        for g, p in cols.items():
            out[:, classes.index(g)] = p
            best = np.maximum(best, p)
        out[:, classes.index(NONE)] = 1.0 - best
        return out
    return infer


def double_fires(segs, scores: dict, threshold, count):
    """Count overlap episodes where at least two binary models have confirmed runs."""
    total = 0
    for seg in segs:
        w = windows(seg.features, WINDOW)
        active = np.zeros(len(w), dtype=np.int64)
        for f in scores.values():
            p = f(w)
            run = 0
            for i, value in enumerate(p):
                run = run + 1 if value >= threshold and value > 0.5 else 0
                active[i] += run >= count
        overlap = active >= 2
        total += int(np.count_nonzero(overlap & ~np.r_[False, overlap[:-1]]))
    return total


def summary(result, classes):
    per = result["per_gesture"]
    rec = {g: f"{v['tcn_hits']}/{v['attempts']}" for g, v in per.items()}
    rule = {g: f"{v['rule_hits']}/{v['attempts']}" for g, v in per.items()}
    rate = result["tcn_misfires_per_minute"]
    rule_rate = result["rule_misfires_per_minute"]
    return {"recall": rec, "rule_recall": rule,
            "neutral_seconds": result["neutral_seconds"],
            "misfires_per_min": round(rate, 2) if rate is not None else None,
            "rule_misfires_per_min": round(rule_rate, 2) if rule_rate is not None else None}


def misfires_on(segs, infer, classes, target, threshold, count, wrong):
    """How many attempts of `target` made `wrong` fire (an old model reacting to a gesture it never saw)."""
    hits = 0
    attempts = 0
    for seg in segs:
        labels = seg.labels()
        fired = dict(predicted_events(infer(windows(seg.features, WINDOW)), classes, threshold, count))
        i = 0
        while i < len(labels):
            j = i
            while j < len(labels) and labels[j] == labels[i]:
                j += 1
            if labels[i] == target:
                attempts += 1
                hits += any(fired.get(k) == wrong for k in range(i, j))
            i = j
    return hits, attempts


# ----------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("recordings", nargs="+", type=Path)
    ap.add_argument("--axis", choices=AXES, default="posture")
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--threshold", type=float, default=0.7)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--json", type=Path, help="also write the full results here (outside the repository)")
    ap.add_argument("--device", choices=("auto","cpu","cuda","xpu"), default=os.environ.get("MANUX_TRAIN_DEVICE","auto"))
    args = ap.parse_args(argv)

    if args.epochs < 1 or args.count < 1 or not 0 < args.threshold <= 1:
        ap.error("epochs and count must be positive; threshold must be within (0, 1]")

    t0 = time.perf_counter()
    try:
        segments = load(list(dict.fromkeys(path.resolve() for path in args.recordings)))
        classes = [NONE] + [c for c in class_names(segments) if c != NONE]
        if len(classes) < 3:
            raise ValueError("comparison needs none and at least two gesture classes")
        mapping = class_mapping(segments, classes)
        if not segments or any(not getattr(s, args.axis) for s in segments):
            raise ValueError(f"every segment needs {args.axis} metadata for held-out comparison")
        folds = groups(segments, args.axis)
        if len(folds) < 2:
            raise ValueError(f"held-out comparison needs at least two {args.axis} groups")
        for name, test in folds.items():
            held = {id(s) for s in test}
            train = [s for s in segments if id(s) not in held]
            present = {target for s in train for target, feature in zip(s.targets(), s.features)
                       if feature[-1] == 1}
            missing = set(classes) - present
            if missing:
                raise ValueError(f"held-out {name}: no valid training frames for {', '.join(sorted(missing))}")
    except (ValueError, OSError) as error:
        ap.error(str(error))
    load_s = time.perf_counter() - t0
    global TRAINING_DEVICE
    try: execution = select(args.device)
    except (ImportError, ValueError) as error: ap.error(str(error))
    TRAINING_DEVICE = execution.actual
    gestures = classes[1:]
    new = gestures[-1]
    old_classes = [c for c in classes if c != new]
    print(f"features computed once in {load_s:.1f}s: {len(segments)} segments, classes {classes}, "
          f"{len(folds)} {args.axis} folds; new-gesture scenario adds {new!r}")
    thr, k, ep = args.threshold, args.count, args.epochs
    report = {"execution": record(execution), "classes": classes, "gestures": mapping, "axis": args.axis,
              "window": WINDOW, "feature_version": FEATURE_VERSION, "features": FEATURES,
              "epochs": ep, "threshold": thr, "frames_in_a_row": k, "seed": 0, "folds": {}}
    for name, test in sorted(folds.items()):
        held = {id(s) for s in test}
        train = [s for s in segments if id(s) not in held]
        row = {"train_segments": len(train), "test_segments": len(test)}
        # ---- part 1
        t = time.perf_counter()
        multi = train_multi(train, classes, ep)
        row["A_multi_s"] = round(time.perf_counter() - t, 1)
        row["A_multi"] = summary(evaluate(test, classes, mapping, multi, WINDOW, thr, k), classes)
        t = time.perf_counter()
        binaries = {g: train_binary(train, classes, g, ep) for g in gestures}
        row["B_binary_s"] = round(time.perf_counter() - t, 1)
        row["B_binary"] = summary(evaluate(test, classes, mapping, combine_binary(binaries, classes), WINDOW, thr, k), classes)
        row["B_double_fires"] = double_fires(test, binaries, thr, k)
        # ---- part 2: add `new`
        old_bins = {g: train_binary(train, classes, g, ep, negatives=old_classes) for g in gestures if g != new}
        t = time.perf_counter()
        new_bin = train_binary(train, classes, new, ep)
        add_b_s = time.perf_counter() - t
        b_add = combine_binary({**old_bins, new: new_bin}, classes)
        row["B_add"] = summary(evaluate(test, classes, mapping, b_add, WINDOW, thr, k), classes)
        row["B_add_s"] = round(add_b_s, 1)
        row["B_add_old_fire_on_new"] = {g: "%d/%d" % misfires_on(
            test, combine_binary({g: f}, [NONE, g]), [NONE, g], new, thr, k, g) for g, f in old_bins.items()}
        row["B_retrain_s"] = row["B_binary_s"]
        row["B_retrain"] = row["B_binary"]
        row["A_retrain_s"] = row["A_multi_s"]
        # C: trunk on old classes, frozen; head for the new gesture on embeddings
        xo, yo = samples(train, old_classes, WINDOW)
        trunk = fit(xo, yo, len(old_classes), ep)
        x, y = samples(train, classes, WINDOW)
        t = time.perf_counter()
        head = fit(x, (y == classes.index(new)).astype(np.int64), 2, max(3, ep // 2), body=trunk.body, head_only=True)
        row["C_head_s"] = round(time.perf_counter() - t, 1)

        def c_infer(w, trunk=trunk, head=head):
            base = softmax_of(trunk, w)                     # old classes
            p_new = softmax_of(head, w)[:, 1]
            out = np.zeros((len(w), len(classes)), dtype=np.float32)
            for i, c in enumerate(old_classes):
                out[:, classes.index(c)] = base[:, i] * (1.0 - p_new)
            out[:, classes.index(new)] = p_new
            return out
        row["C_head"] = summary(evaluate(test, classes, mapping, c_infer, WINDOW, thr, k), classes)
        row["C_old_fire_on_new"] = {g: "%d/%d" % misfires_on(test, c_infer, classes, new, thr, k, g)
                                    for g in gestures if g != new}
        report["folds"][name] = row
        print(f"\n== held-out {args.axis} {name}")
        print(json.dumps(row, ensure_ascii=False, indent=1))
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
