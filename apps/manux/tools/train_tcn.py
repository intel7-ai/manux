"""Train Dan's causal TCN on the gesture classes declared in MANUX recordings.

    cd apps/manux
    uv run --python 3.12 --extra train python tools/train_tcn.py <recordings...> --output <outside the repo>/m.onnx

torch lives only here (`train` extra), never in src/. Do not write models or recordings into the
repository (D-006); a model trained on one person is a personal model, not a base model (D-005).
"""
from __future__ import annotations

import argparse
from hashlib import file_digest, sha256
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from manux.data.recorder import public_source
from manux.data.recording import read
from manux.ml.card import CLASS_RE, validate_card
from manux.perception.local3d import FEATURES, FEATURE_VERSION
from manux.settings.paths import resolve
from tcn_data import AXES, class_mapping, class_names, evaluate, groups, load, samples

WINDOW = 15
AUGMENT = True


def train(segments, classes, epochs):
    import torch
    from torch import nn

    x, y = samples(segments, classes, WINDOW)
    counts = np.bincount(y, minlength=len(classes))
    if not counts.all():
        missing = [name for name, count in zip(classes, counts) if not count]
        raise ValueError(f"no usable training windows for {', '.join(missing)}")

    class TCN(nn.Module):
        def __init__(self):
            super().__init__()
            layers = []
            channels = FEATURES
            for dilation in (1, 2, 4):
                layers.extend((nn.ConstantPad1d((2 * dilation, 0), 0), nn.Conv1d(channels, 64, 3, dilation=dilation),
                               nn.ReLU(), nn.Dropout(0.2)))
                channels = 64
            self.body = nn.Sequential(*layers)
            self.head = nn.Linear(channels, len(classes))

        def forward(self, inputs):
            return self.head(self.body(inputs.transpose(1, 2))[:, :, -1])

    def augment(batch):
        """Dan's augmentation: small 3D rotation of the hand-frame joints, size and jitter (other seats/angles)."""
        import math
        b = batch.shape[0]
        ang = (torch.rand(b, 3) - 0.5) * math.radians(30)
        cx, cy, cz = torch.cos(ang).unbind(1)
        sx, sy, sz = torch.sin(ang).unbind(1)
        one, zero = torch.ones(b), torch.zeros(b)
        rx = torch.stack([one, zero, zero, zero, cx, -sx, zero, sx, cx], 1).view(b, 3, 3)
        ry = torch.stack([cy, zero, sy, zero, one, zero, -sy, zero, cy], 1).view(b, 3, 3)
        rz = torch.stack([cz, -sz, zero, sz, cz, zero, zero, zero, one], 1).view(b, 3, 3)
        r = rz @ ry @ rx
        batch = batch.clone()
        for lo in (0, 63):
            pts = batch[:, :, lo:lo + 63].reshape(b, batch.shape[1], 21, 3)
            batch[:, :, lo:lo + 63] = torch.einsum("btjk,blk->btjl", pts, r).reshape(b, batch.shape[1], 63)
        scale = 1.0 + (torch.rand(b, 1, 1) - 0.5) * 0.2
        batch[:, :, :130] = batch[:, :, :130] * scale
        batch[:, :, :130] += torch.randn_like(batch[:, :, :130]) * 0.01
        return batch

    torch.manual_seed(0)
    model = TCN()
    weights = torch.tensor(counts ** -0.5, dtype=torch.float32)
    loss_fn = nn.CrossEntropyLoss(weight=weights / weights.mean())
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    inputs, labels = torch.from_numpy(x), torch.from_numpy(y)
    for _ in range(epochs):
        for batch in torch.randperm(len(inputs)).split(256):
            optimizer.zero_grad()
            loss_fn(model(augment(inputs[batch]) if AUGMENT else inputs[batch]), labels[batch]).backward()
            optimizer.step()
    return model.eval()


def predict(model, inputs):
    import torch

    with torch.inference_mode():
        batches = torch.from_numpy(inputs).split(1024)
        return torch.cat([model(batch).softmax(dim=1) for batch in batches]).numpy()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--classes", nargs="+")
    parser.add_argument("--kind", choices=("base", "personal"), default="personal")
    parser.add_argument("--hold-out", choices=("person", "posture", "condition", "both", "all"), default="all",
                        help="held-out folds: by person, by posture index, by physical condition (capture-plan condition_key); both = person + posture; all = the three")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--threshold", type=float, default=0.7)
    parser.add_argument("--frames-in-a-row", type=int, default=3)
    parser.add_argument("--decisions", type=Path, help="class -> direct intent specification JSON (no actions)")
    parser.add_argument("--cache-dir", type=Path, default=resolve().data_dir / "features")
    parser.add_argument("--no-augment", action="store_true", help="train without Dan's rotation/size/jitter augmentation")
    args = parser.parse_args()
    global AUGMENT
    AUGMENT = not args.no_augment
    if args.output.suffix.lower() != ".onnx" or args.epochs < 1:
        parser.error("use an .onnx output and at least one epoch")
    repo = Path(__file__).resolve().parents[3]
    if args.output.resolve().is_relative_to(repo) or args.cache_dir.resolve().is_relative_to(repo):
        parser.error("write models and feature caches outside the repository; D-006")
    try:
        recordings = list(dict.fromkeys(path.resolve() for path in args.recordings))
        recording_hashes = {path: sha256(path.read_bytes()).hexdigest() for path in recordings}
        segments = load(recordings, split="train", cache_dir=args.cache_dir)
        classes = args.classes or class_names(segments)
        if ("none" not in classes or len(classes) < 2 or len(set(classes)) != len(classes)
                or not all(CLASS_RE.fullmatch(name) for name in classes)):
            raise ValueError("classes must include none and at least one distinct lowercase gesture class")
        mapping = class_mapping(segments, classes)
        results = []
        sources = []
        for path in recordings:
            with path.open("rb") as file:
                digest = file_digest(file, "sha256").hexdigest()
            rec = read(path)
            if digest != recording_hashes[path] or sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("training recording changed while preparing the model")
            pc, note = public_source(rec.meta)        # aliases only, never real names (contracts section 9)
            camera = {k: v for k, v in (rec.meta.get("camera") or {}).items() if k in ("source", "mode", "mirror")}
            sources.append({"file": path.name, "sha256": digest, "pc": pc, "person": rec.meta.get("person"),
                            "camera": dict(camera, note=note), "frames": len(rec.frames)})
        card = {"classes": classes, "gestures": mapping, "kind": args.kind, "window": WINDOW,
                "features": FEATURES, "feature_version": FEATURE_VERSION, "threshold": args.threshold,
                "frames_in_a_row": args.frames_in_a_row, "trained": datetime.now(timezone.utc).isoformat(),
                "data": sources, "condition_groups": sorted(groups(segments, "condition")),
                "held_out_results": results}
        if args.decisions is not None:
            card["decisions"] = json.loads(args.decisions.read_text(encoding="utf-8"))
        validate_card(card, FEATURE_VERSION, FEATURES)
        axes = {"both": ("person", "posture"), "all": AXES}.get(args.hold_out, (args.hold_out,))
        for axis in axes:
            held_out = groups(segments, axis)
            if len(held_out) < 2:
                results.append({"axis": axis, "status": "insufficient_groups", "groups": len(held_out)})
                continue
            for key, test in held_out.items():
                fit = [s for other, rows in held_out.items() if other != key for s in rows]
                try:
                    fold = train(fit, classes, args.epochs)
                except ValueError as error:
                    results.append({"axis": axis, "group": key, "status": "insufficient_data", "reason": str(error)})
                    continue
                metrics = evaluate(test, classes, mapping, lambda x: predict(fold, x),
                                   WINDOW, args.threshold, args.frames_in_a_row)
                results.append({"axis": axis, "group": key, "status": "evaluated", **metrics})
        model = train(segments, classes, args.epochs)
        import torch

        if any(sha256(path.read_bytes()).hexdigest() != digest for path, digest in recording_hashes.items()):
            raise ValueError("training recordings changed during training")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        torch.onnx.export(model, torch.zeros(1, WINDOW, FEATURES), str(args.output),
                          input_names=["features"], output_names=["logits"], opset_version=17, dynamo=False)
        with args.output.open("rb") as file:
            card["model_sha256"] = file_digest(file, "sha256").hexdigest()
        args.output.with_suffix(".json").write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(results, indent=2))
    except (ValueError, OSError, ImportError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
