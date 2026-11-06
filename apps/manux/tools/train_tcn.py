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
import os
from time import perf_counter
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from manux.data.recorder import public_source
from manux.data.recording import read
from manux.ml.card import CLASS_RE, validate_card
from manux.perception.local3d import FEATURES, FEATURE_VERSION
from manux.settings.paths import resolve
from tcn_data import AXES, class_mapping, class_names, evaluate, groups, load
from training_inputs import sample_data

from training_device import select, record, synchronize, cpu_export

WINDOW = 15
AUGMENT = True


def train(segments, classes, epochs, *, progress=None, other_as_none=False, device="cpu", execution=None):
    import torch
    from torch import nn

    execution = execution or select(device)
    prepared = perf_counter()
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    x, y, sampling = sample_data(segments, classes, WINDOW, other_as_none=other_as_none)
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

    from tcn_augmentation import augment

    torch.manual_seed(0)
    model = TCN().to(execution.actual)
    loss_fn = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    inputs, labels = torch.from_numpy(x), torch.from_numpy(y)
    weights = torch.from_numpy(sampling)
    data_ms = (perf_counter()-prepared)*1000
    synchronize(execution.actual)
    started, transfer_ms = perf_counter(), 0.
    for _ in range(epochs):
        total_loss, total_seen = 0., 0
        for batch in torch.multinomial(weights, min(len(inputs), 16384), replacement=True).split(256):
            optimizer.zero_grad()
            transfer = perf_counter()
            xb, yb = inputs[batch].to(execution.actual), labels[batch].to(execution.actual)
            transfer_ms += (perf_counter()-transfer)*1000
            loss = loss_fn(model(augment(xb) if AUGMENT else xb), yb)
            loss.backward()
            optimizer.step()
            total_loss += float(loss.detach().cpu()) * len(batch)
            total_seen += len(batch)
        if progress is not None:
            progress(_ + 1, epochs, total_loss / total_seen)
    synchronize(execution.actual)
    model.execution = record(execution)
    model.timings = {"data_prepare_ms":data_ms,"batch_transfer_ms":transfer_ms,"training_ms":(perf_counter()-started)*1000}
    model.export_probe = x[:8].copy()
    predicted = predict(model.eval(), x).argmax(1)
    model.fit_rates = {name: float((predicted[y == i] == i).mean()) for i, name in enumerate(classes)}
    if min(model.fit_rates.values()) < .5:
        raise ValueError("후보 모델이 한 클래스로 치우쳤습니다. 이전 모델을 유지합니다. 녹화 라벨과 다양한 중립 자료를 확인하세요.")
    return model.eval()


def predict(model, inputs):
    import torch

    with torch.inference_mode():
        batches = torch.from_numpy(inputs).split(1024)
        return torch.cat([model(batch.to(next(model.parameters()).device)).softmax(dim=1).cpu() for batch in batches]).numpy()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--classes", nargs="+")
    parser.add_argument("--kind", choices=("base", "personal"), default="personal")
    parser.add_argument("--hold-out", choices=("person", "posture", "condition", "both", "all"), default="all",
                        help="held-out folds: by person, by posture index, by physical condition (capture-plan condition_key); both = person + posture; all = the three")
    parser.add_argument("--device", choices=("auto","cpu","cuda","xpu"), default=os.environ.get("MANUX_TRAIN_DEVICE","auto"))
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--threshold", type=float, default=0.7)
    parser.add_argument("--frames-in-a-row", type=int, default=3)
    parser.add_argument("--decisions", type=Path, help="class -> direct intent specification JSON (no actions)")
    parser.add_argument("--cache-dir", type=Path, default=resolve().data_dir / "features")
    parser.add_argument("--no-augment", action="store_true", help="disable coherent hand-depth, joint and causal-speed augmentation")
    parser.add_argument("--negative-other-classes", action="store_true", help="treat other recorded gestures as hard negatives")
    parser.add_argument("--events", action="store_true", help="emit progress events for the app worker")
    parser.add_argument("--skip-folds", action="store_true", help="app worker uses a separate eval set instead")
    args = parser.parse_args(argv)
    global AUGMENT
    AUGMENT = not args.no_augment
    if args.output.suffix.lower() != ".onnx" or args.epochs < 1:
        parser.error("use an .onnx output and at least one epoch")
    from manux.data.repository import repository_root
    repo = repository_root(Path(__file__)) if not getattr(sys, "frozen", False) else None
    if repo is not None and (args.output.resolve().is_relative_to(repo) or args.cache_dir.resolve().is_relative_to(repo)):
        parser.error("write models and feature caches outside the repository; D-006")
    try:
        execution = select(args.device)
        if args.events: print(json.dumps({"event":"device", **record(execution)}), flush=True)
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
            from manux.data.studio import capture_hashes
            sources.append({"file": path.name, "sha256": digest, "capture_sha256": sorted(capture_hashes(path, rec)), "pc": pc, "person": rec.meta.get("person"),
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
        if args.skip_folds:
            axes = ()
        for axis in axes:
            held_out = groups(segments, axis)
            if len(held_out) < 2:
                results.append({"axis": axis, "status": "insufficient_groups", "groups": len(held_out)})
                continue
            for key, test in held_out.items():
                fit = [s for other, rows in held_out.items() if other != key for s in rows]
                try:
                    fold = train(fit, classes, args.epochs, other_as_none=args.negative_other_classes, execution=execution)
                except ValueError as error:
                    results.append({"axis": axis, "group": key, "status": "insufficient_data", "reason": str(error)})
                    continue
                metrics = evaluate(test, classes, mapping, lambda x: predict(fold, x),
                                   WINDOW, args.threshold, args.frames_in_a_row, continuous=True)
                results.append({"axis": axis, "group": key, "status": "evaluated", **metrics})
        progress = (lambda epoch, epochs, loss: print(json.dumps({"event": "epoch", "epoch": epoch,
                     "epochs": epochs, "loss": loss}), flush=True)) if args.events else None
        model = train(segments, classes, args.epochs, progress=progress, other_as_none=args.negative_other_classes, execution=execution)
        import torch

        if any(sha256(path.read_bytes()).hexdigest() != digest for path, digest in recording_hashes.items()):
            raise ValueError("training recordings changed during training")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        exported = cpu_export(model)
        export_started = perf_counter()
        torch.onnx.export(exported, torch.zeros(1, WINDOW, FEATURES), str(args.output),
                          input_names=["features"], output_names=["logits"], opset_version=17, dynamo=False)
        with args.output.open("rb") as file:
            card["model_sha256"] = file_digest(file, "sha256").hexdigest()
        from compose_tcn import component_id
        card.update(training_pipeline="continuous-v2", fit_accuracy=model.fit_rates,
                    class_model_ids={name: component_id(card["model_sha256"], classes) for name in classes if name != "none"},
                    class_default_modes={name: "shadow" for name in classes if name != "none"})
        args.output.with_suffix(".json").write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
        from manux.ml.predictor import load_model
        checked = load_model(args.output, FEATURE_VERSION, FEATURES, device="cpu")
        expected = predict(model, model.export_probe)
        actual = np.stack([checked.predictor(window) for window in model.export_probe])
        error = float(np.max(np.abs(expected-actual)))
        if not np.allclose(expected, actual, rtol=1e-4, atol=1e-5):
            raise ValueError("ONNX export differs from training-device predictions")
        card.update(execution=record(execution), seed=0, timings={**model.timings, "export_check_ms": (perf_counter()-export_started)*1000}, export_max_abs_error=error)
        args.output.with_suffix(".json").write_text(json.dumps(card, indent=2)+"\n",encoding="utf-8")
        print(json.dumps(results, indent=2))
    except (ValueError, OSError, ImportError) as error:
        if args.events:
            raise
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
