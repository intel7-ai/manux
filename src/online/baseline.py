"""Offline training and evaluation of encoders on stored samples (Phase 2 of docs/migration-plan.md). This is ordinary batch training
to compare representations; updates during use come in Phase 4."""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Sequence

import numpy as np
import torch
from torch import nn

from src.online.dataset import WindowData, window_positions
from src.online.models import EncoderBase, supervised_contrastive_loss

THRESHOLD = .7
RUN_FRAMES = 4


def train_encoder(model: EncoderBase, data: WindowData, positions: np.ndarray, window: int, *, epochs: int = 15, batch: int = 256,
                  lr: float = 1e-3, stride: int = 2, seed: int = 0, device: str = "cpu", supcon_weight: float = 0.0,
                  augment: Optional[Callable[[torch.Tensor], torch.Tensor]] = None) -> Dict[str, Any]:
    """Cross-entropy (class-weighted as in scripts/train_tcn.py), optionally plus supervised contrastive loss on the embedding."""
    torch.manual_seed(seed)
    dev = torch.device(device)
    model.to(dev)
    frames = torch.from_numpy(data.frames).to(dev)
    labels = torch.from_numpy(data.labels).to(dev)
    counts = np.bincount(data.labels[positions], minlength=len(data.classes)).astype(np.float64)
    weight = torch.tensor((counts.sum() / np.maximum(counts, 1)) ** .5, dtype=torch.float32, device=dev)
    loss_fn = nn.CrossEntropyLoss(weight=weight / weight.mean())
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    t0 = time.perf_counter()
    losses, generator = [], np.random.default_rng(seed)
    for epoch in range(epochs):
        model.train()
        chosen = positions[epoch % max(1, stride)::max(1, stride)]
        chosen = generator.permutation(chosen)
        total = 0.0
        for i in range(0, len(chosen), batch):
            rows = window_positions(data.starts, chosen[i:i + batch], window)
            x = frames[torch.from_numpy(rows).to(dev)]
            y = labels[torch.from_numpy(chosen[i:i + batch]).to(dev)]
            if augment is not None:
                x = augment(x)
            z = model.embed(x)
            loss = loss_fn(model.head(z), y)
            if supcon_weight > 0:
                loss = loss + supcon_weight * supervised_contrastive_loss(z, y)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            total += float(loss.detach()) * len(y)
        losses.append(round(total / max(1, len(chosen)), 4))
    return {"seconds": time.perf_counter() - t0, "losses": losses}


@torch.no_grad()
def predict_probabilities(model: EncoderBase, data: WindowData, rows: np.ndarray, window: int, device: str = "cpu", batch: int = 4096) -> np.ndarray:
    model.eval()
    dev = torch.device(device)
    frames = torch.from_numpy(data.frames).to(dev)
    out = []
    for i in range(0, len(rows), batch):
        index = window_positions(data.starts, rows[i:i + batch], window)
        out.append(torch.softmax(model(frames[torch.from_numpy(index).to(dev)]), dim=1).cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, len(data.classes)))


def _first_run(hit: np.ndarray, run: int) -> int:
    """Index of the frame that completes the first `run` frames in a row, or -1."""
    count = 0
    for i, value in enumerate(hit):
        count = count + 1 if value else 0
        if count == run:
            return i
    return -1


def evaluate_samples(model: EncoderBase, data: WindowData, sample_indexes: Sequence[int], window: int, device: str = "cpu",
                     threshold: float = THRESHOLD, run: int = RUN_FRAMES) -> Dict[str, Any]:
    """Whole-session test. Window scores, then the way the app would use them: a repetition is recognised when its class reaches the
    threshold `run` frames in a row; a fire of a gesture during a "none" piece is a false fire."""
    classes = data.classes
    none = classes.index("none") if "none" in classes else -1
    n = len(classes)
    confusion = np.zeros((n, n), dtype=np.int64)
    reps: Dict[str, list] = {}
    reaction, wrong = [], 0
    false_fires, none_seconds = 0, 0.0
    for s in sample_indexes:
        rows = np.flatnonzero(data.sample_of == s)
        prob = predict_probabilities(model, data, rows, window, device)
        y = data.labels[rows]
        known = y >= 0
        np.add.at(confusion, (y[known], prob[known].argmax(axis=1)), 1)
        label = classes.index(data.samples[s].label)
        start = int(np.searchsorted(rows, data.do_start[s]))
        tail = prob[start:]
        times = data.times[rows][start:]
        if label == none:
            none_seconds += float(times[-1] - times[0]) if len(times) > 1 else 0.0
            for c in range(n):
                if c != none:
                    false_fires += _count_fires(tail[:, c] >= threshold, run)
            continue
        at = _first_run(tail[:, label] >= threshold, run)
        reps.setdefault(data.samples[s].label, []).append(at >= 0)
        if at >= 0:
            reaction.append(float(times[at] - times[0]))
        if any(_first_run(tail[:, c] >= threshold, run) >= 0 for c in range(n) if c not in (label, none)):
            wrong += 1
    f1 = {}
    for c, name in enumerate(classes):
        tp, fp, fn = confusion[c, c], confusion[:, c].sum() - confusion[c, c], confusion[c].sum() - confusion[c, c]
        if confusion[c].sum() > 0:
            f1[name] = float(2 * tp / max(1, 2 * tp + fp + fn))
    total = int(confusion.sum())
    return {"accuracy": float(np.trace(confusion) / max(1, total)), "macro_f1": float(np.mean(list(f1.values()))) if f1 else 0.0, "f1": f1,
            "windows": total, "reps": {k: [int(sum(v)), len(v)] for k, v in reps.items()},
            "reps_hit": int(sum(sum(v) for v in reps.values())), "reps_total": int(sum(len(v) for v in reps.values())),
            "wrong_reps": wrong, "false_fires": int(false_fires), "none_minutes": none_seconds / 60,
            "reaction_median": float(np.median(reaction)) if reaction else None, "confusion": confusion.tolist()}


def _count_fires(hit: np.ndarray, run: int) -> int:
    fires, count = 0, 0
    for value in hit:
        count = count + 1 if value else 0
        if count == run:
            fires += 1
    return fires


def inference_ms(model: EncoderBase, window: int, n_in: int, device: str = "cpu", repeats: int = 200) -> float:
    model.to(device).eval()
    x = torch.randn(1, window, n_in, device=device)
    times = []
    with torch.no_grad():
        for _ in range(repeats):
            t0 = time.perf_counter()
            model(x)
            if device.startswith("cuda"):
                torch.cuda.synchronize()
            times.append((time.perf_counter() - t0) * 1000)
    return float(np.median(times[repeats // 10:]))
