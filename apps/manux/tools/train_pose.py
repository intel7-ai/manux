"""Train the pose-evidence MLP (one frame in, class probabilities out) and export ONNX + a model card.

    cd apps/manux
    uv run --python 3.12 --extra train python tools/train_pose.py <recordings...> --output <outside the repo>/pose.onnx
    uv run --python 3.12 --extra train python tools/train_pose.py <train...> --eval <eval...> --output .../pose.onnx

The model reads the 33 `pose-angles-v1` values of one frame (`perception.pose_angles`) and gives a
probability per class. Its card is a normal model card (`ml.card`) with `window = 1`, `features = 33`,
`feature_version = "pose-angles-v1"` and `role = "pose_evidence"`: the app uses it only as evidence
for the veto / rest guard (`features/pose`, docs/pose-assist.md), never to start a gesture.

How the data is used:
- **Segments, never frames, are split.** A segment is a run of consecutive frames of one class in one
  recording (`pose_data`). With `--eval` the held-out recordings are separate files (and must not be copies
  of training files); without it whole segments are moved to the held-out side.
- The exported model is the one that was evaluated: it did not see the held-out segments.
- Classes with too few independent segments are reported and recorded in the card (`pose.warnings`);
  `--strict` turns the warnings into an error.
- The held-out result in the card is frame level and is not a comparison with the TCN.

The default loss is the plain cross-entropy. `--class-weights` and `--penalize CLASS=FACTOR` (extra weight
on frames that are *not* CLASS while the model gives CLASS more than 5 %) are available; park's measurement
found the penalty's effect small, so it is off unless asked for.

torch is imported only here (`train` extra), never by `src/` (architecture.md section 4). Do not write
models or recordings into the repository (D-006); a model trained on one person is a personal model (D-005).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from hashlib import file_digest
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

import numpy as np

from manux.features.pose import ROLE
from manux.ml.card import CLASS_RE, validate_card
from manux.perception.pose_angles import FEATURE_VERSION, FEATURES
import pose_data

HIDDEN = 64
LEAK = 0.05          # a penalized class counts as "given" above this probability (park's rule)


def parse_penalties(items: Sequence[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for item in items:
        name, sep, factor = item.partition("=")
        try:
            value = float(factor)
        except ValueError:
            value = float("nan")
        if not sep or not CLASS_RE.fullmatch(name) or not (value >= 1.0) or value > 100.0:
            raise ValueError(f"--penalize wants CLASS=FACTOR with a factor from 1 to 100, got {item!r}")
        out[name] = value
    return out


def class_weights(counts: np.ndarray, how: str) -> Optional[np.ndarray]:
    if how == "none":
        return None
    power = {"sqrt": -0.5, "balanced": -1.0}[how]
    w = counts.astype(np.float64) ** power
    return (w / w.mean()).astype(np.float32)


def train(x: np.ndarray, y: np.ndarray, classes: Sequence[str], *, epochs: int = 30, hidden: int = HIDDEN,
          lr: float = 1e-3, weight_decay: float = 1e-4, weights: str = "none",
          penalties: Optional[Mapping[str, float]] = None, seed: int = 0, batch: int = 256, progress=None):
    """A trained `PoseMLP` (eval mode). Standardisation is part of the model, so the ONNX needs no side file."""
    import torch
    from torch import nn

    n = len(classes)
    counts = np.bincount(y, minlength=n)
    if not counts.all():
        missing = [name for name, c in zip(classes, counts) if not c]
        raise ValueError(f"no training frames for {', '.join(missing)}")
    mean = x.mean(axis=0).astype(np.float32)
    std = np.maximum(x.std(axis=0), 1e-3).astype(np.float32)

    class PoseMLP(nn.Module):
        def __init__(self):
            super().__init__()
            self.register_buffer("mean", torch.from_numpy(mean))
            self.register_buffer("std", torch.from_numpy(std))
            self.body = nn.Sequential(nn.Linear(FEATURES, hidden), nn.LeakyReLU(0.05), nn.Linear(hidden, n))

        def forward(self, inputs):                  # (batch, 1, FEATURES) -> logits (batch, classes)
            frame = inputs.reshape(inputs.shape[0], -1)
            return self.body((frame - self.mean) / self.std)

    torch.manual_seed(seed)
    model = PoseMLP()
    w = class_weights(counts, weights)
    loss_fn = nn.CrossEntropyLoss(weight=None if w is None else torch.from_numpy(w), reduction="none")
    index = {name: i for i, name in enumerate(classes)}
    pen = {index[c]: f for c, f in (penalties or {}).items() if c in index}
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    inputs, labels = torch.from_numpy(x), torch.from_numpy(y)
    for epoch in range(epochs):
        for chunk in torch.randperm(len(inputs)).split(batch):
            optimizer.zero_grad()
            logits = model(inputs[chunk][:, None, :])
            loss = loss_fn(logits, labels[chunk])
            if pen:
                probs = logits.softmax(dim=1)
                factor = torch.ones(len(chunk))
                for cls, value in pen.items():
                    wrong = (labels[chunk] != cls) & (probs[:, cls] > LEAK)
                    factor = torch.where(wrong, factor * value, factor)
                loss = loss * factor.detach()
            loss.mean().backward()
            optimizer.step()
        if progress is not None:
            progress(epoch + 1, epochs, float(loss.mean().detach()))
    return model.eval()


def predict(model, x: np.ndarray) -> np.ndarray:
    import torch

    with torch.inference_mode():
        return torch.cat([model(torch.from_numpy(x[i:i + 4096])[:, None, :]).softmax(dim=1)
                          for i in range(0, len(x), 4096)]).numpy().astype(np.float64)


def export_onnx(model, path: Path) -> None:
    import torch

    path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(model, torch.zeros(1, 1, FEATURES), str(path), input_names=["features"],
                      output_names=["logits"], opset_version=17, dynamo=False)


def build_card(classes: Sequence[str], gestures: Mapping[str, str], sources: List[dict], results: List[dict], *,
               kind: str, threshold: float, frames: int, warnings: List[str], per_class_segments: Mapping[str, int],
               split: str, settings: Mapping[str, object], eval_sources: Optional[List[dict]] = None) -> dict:
    card = {"classes": list(classes), "gestures": {c: g for c, g in gestures.items() if c in classes},
            "kind": kind, "role": ROLE, "window": 1, "features": FEATURES, "feature_version": FEATURE_VERSION,
            "threshold": threshold, "frames_in_a_row": frames, "trained": datetime.now(timezone.utc).isoformat(),
            "data": sources, "held_out_results": results,
            "pose": {"idle_class": "none" if "none" in classes else None, "split": split,
                     "segments_per_class": dict(per_class_segments), "warnings": list(warnings),
                     "training": dict(settings), "level": "frame", "eval_sources": list(eval_sources or [])}}
    validate_card(card, FEATURE_VERSION, FEATURES)
    return card


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", nargs="+", type=Path, help="training recordings")
    parser.add_argument("--eval", nargs="+", type=Path, default=[], help="separate held-out recordings")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--classes", nargs="+")
    parser.add_argument("--kind", choices=("base", "personal"), default="personal")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--hidden", type=int, default=HIDDEN)
    parser.add_argument("--threshold", type=float, default=0.9)
    parser.add_argument("--frames-in-a-row", type=int, default=3)
    parser.add_argument("--class-weights", choices=("none", "sqrt", "balanced"), default="none")
    parser.add_argument("--penalize", action="append", default=[], metavar="CLASS=FACTOR")
    parser.add_argument("--eval-fraction", type=float, default=0.2, help="held-out share when --eval is not given")
    parser.add_argument("--veto-classes", nargs="*", help="classes to run the veto study for (default: all with a gesture)")
    parser.add_argument("--min-segments", type=int, default=pose_data.MIN_SEGMENTS)
    parser.add_argument("--strict", action="store_true", help="a coverage warning is an error")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    if args.output.suffix.lower() != ".onnx" or args.epochs < 1 or args.hidden < 1:
        parser.error("use an .onnx output, at least one epoch and at least one hidden unit")
    if not 0.0 < args.threshold <= 1.0 or args.frames_in_a_row < 1:
        parser.error("--threshold must be within (0, 1] and --frames-in-a-row at least 1")
    repo = Path(__file__).resolve().parents[3] if not getattr(sys, "frozen", False) else None
    if repo is not None and args.output.resolve().is_relative_to(repo):
        parser.error("write models outside the repository; D-006")
    try:
        penalties = parse_penalties(args.penalize)
        loaded = pose_data.load_segments(args.recordings, split="train")
        if args.eval:
            held = pose_data.load_segments(args.eval, split="eval")
            pose_data.check_files_disjoint((s["sha256"] for s in loaded.sources), (s["sha256"] for s in held.sources))
            train_segments, held_segments = loaded.segments, held.segments
            for name, gesture in held.gestures.items():
                if loaded.gestures.setdefault(name, gesture) != gesture:
                    raise ValueError(f"conflicting gesture IDs for class {name!r}")
            split = "eval-recordings"
            eval_sources = held.sources
        else:
            eval_sources = []
            train_segments, held_segments = pose_data.split_segments(loaded.segments, args.eval_fraction, args.seed)
            split = "segments"
        pose_data.check_disjoint(train_segments, held_segments)
        classes = args.classes or pose_data.class_names(train_segments)
        if len(classes) < 2 or len(set(classes)) != len(classes) or not all(CLASS_RE.fullmatch(c) for c in classes):
            raise ValueError("need at least two distinct lowercase class names")
        warnings = pose_data.coverage_warnings(train_segments, held_segments, minimum=args.min_segments)
        for line in warnings:
            print(f"warning: {line}", file=sys.stderr)
        if warnings and args.strict:
            raise ValueError("coverage warnings with --strict")
        x, y = pose_data.stack(train_segments, classes)
        model = train(x, y, classes, epochs=args.epochs, hidden=args.hidden, weights=args.class_weights,
                      penalties=penalties, seed=args.seed)
        results: List[dict] = []
        if held_segments:
            hx, hy = pose_data.stack(held_segments, classes)
            veto = args.veto_classes if args.veto_classes is not None else [c for c in classes if c in loaded.gestures]
            results.append(pose_data.evaluate(predict(model, hx), hy, classes, veto_classes=veto, tau=args.threshold))
        else:
            results.append({"axis": "segment", "status": "no_held_out_segments"})
        settings = {"epochs": args.epochs, "hidden": args.hidden, "class_weights": args.class_weights,
                    "penalize": penalties, "seed": args.seed, "train_frames": int(len(y)),
                    "train_segments": len(train_segments), "held_out_segments": len(held_segments)}
        card = build_card(classes, loaded.gestures, loaded.sources, results, kind=args.kind, threshold=args.threshold,
                          frames=args.frames_in_a_row, warnings=warnings,
                          per_class_segments=pose_data.segments_per_class(train_segments), split=split, settings=settings,
                          eval_sources=eval_sources)
        export_onnx(model, args.output)
        with args.output.open("rb") as file:
            card["model_sha256"] = file_digest(file, "sha256").hexdigest()
        args.output.with_suffix(".json").write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(results, indent=2))
    except (ValueError, OSError, ImportError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
