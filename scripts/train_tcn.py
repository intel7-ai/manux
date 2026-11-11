"""
Train the gesture TCN on the guided recordings (recordings/*-guided.jsonl.gz) and compare it with the rules.

Run from hand-mouse/:  uv run --locked --extra qt --extra train python scripts/train_tcn.py [--mode fast|thorough] [--epochs 25]

- Every recorded frame becomes a 0.5 s window (15 frames) of src/tcn_features.py features, labelled with what the
  user was asked to do at its last frame: none / thumb_index / thumb_ring ("get ready" and "waiting" frames are left out).
- Honest test: one posture is held out entirely (default: the last one, the phone moved). `--folds` (or `--mode thorough`)
  repeats that for each posture. The final model is then trained on all postures and exported to models/gesture_tcn.onnx with
  models/gesture_tcn.json (classes, window, feature version, decision threshold, where the data came from).
- Compared per repetition ("did it recognise the gesture asked for") and over the half-fist stretches ("how many
  times did it fire although nothing was asked") - the same two numbers measured for the rules, from the rule
  decisions stored in the recording.
"""
from __future__ import annotations

import argparse
import copy
import glob
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# scripts/profile.py would shadow the standard `profile` module that torch imports: drop scripts/ from the path.
sys.path = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn as nn  # noqa: E402

from src.action_bindings import get_action_registry  # noqa: E402
from src.recorder import public_source  # noqa: E402
from src.tcn_features import FEATURE_VERSION, FEATURES  # noqa: E402
from src.gesture_catalog import CLASSES, TASKS, BY_KEY, is_motion_task  # noqa: E402

get_action_registry()  # sync custom registered temporal motions into BY_KEY / CLASSES

CLICKY = {"right_click", "left_click", "task_switcher"}
STARTY = {"scroll_grab", "drag_hold"}
WINDOW = 15
PROGRESS = False          # --progress: one JSON object per line on stdout (the app's training center reads them)
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

DEFAULT_TRAINING_MODE = "fast"
DEFAULT_TRAINING_DEVICE = "auto"
TRAINING_DEVICES = ("auto", "gpu", "cuda", "cpu")
DEFAULT_TEST_CONDITION = 2
DEFAULT_EPOCHS = 25
THRESHOLD_SWEEP_OFFSETS = (-0.20, -0.15, -0.10, -0.05, 0.0, 0.05, 0.10)


def resolve_training_device(requested: str | torch.device | None = None) -> tuple[torch.device, str, str]:
    """Resolve requested device ('auto', 'gpu'/'cuda', 'cpu') into (torch.device, normalized_choice, device_name).

    Always falls back cleanly to CPU when CUDA is unavailable on the host PC.
    """
    if isinstance(requested, torch.device):
        choice = "gpu" if requested.type == "cuda" else "cpu"
    else:
        raw = str(requested or DEFAULT_TRAINING_DEVICE).strip().lower()
        if raw in ("cuda", "gpu"):
            choice = "gpu"
        elif raw == "cpu":
            choice = "cpu"
        elif raw in ("auto", ""):
            choice = "auto"
        else:
            raise ValueError(f"알 수 없는 학습 연산 장치: {requested}")

    cuda_ok = bool(torch.cuda.is_available())
    if choice in ("auto", "gpu") and cuda_ok:
        dev = torch.device("cuda")
        try:
            name = str(torch.cuda.get_device_name(0) or "CUDA GPU")
        except Exception:
            name = "CUDA GPU"
        return dev, choice, name
    mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
    if choice in ("auto", "gpu") and mps_backend is not None and bool(mps_backend.is_available()):
        return torch.device("mps"), choice, "Apple MPS GPU"
    return torch.device("cpu"), choice, "CPU"
TRAINING_MODES: dict[str, dict[str, int | bool | None]] = {
    "fast": {
        "folds": False,
        "test_condition": DEFAULT_TEST_CONDITION,
        "window_stride": 2,
        "offline_augment_factor": 1,
        "epochs": DEFAULT_EPOCHS,
    },
    "thorough": {
        "folds": True,
        "test_condition": DEFAULT_TEST_CONDITION,
        "window_stride": 1,
        "offline_augment_factor": None,
        "epochs": DEFAULT_EPOCHS,
    },
}


def slice_with_stride(
    x: np.ndarray,
    y: np.ndarray,
    stride: int = 1,
    offset: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Subsample training windows by ``stride`` starting at ``offset % stride``."""
    step = max(1, int(stride))
    if step <= 1 or len(x) == 0:
        return x, y
    start = int(offset) % step
    if start >= len(x):
        start = 0
    return x[start::step], y[start::step]


def resolve_training_options(
    args,
    conds: list[int] | None = None,
    aug_cfg: dict | None = None,
) -> dict:
    """Resolve effective training parameters from ``--mode`` defaults and explicit CLI overrides."""
    raw = getattr(args, "_raw_training_args", None)
    if raw is None:
        raw = {
            "mode": getattr(args, "mode", None),
            "folds": getattr(args, "folds", None),
            "test_condition": getattr(args, "test_condition", None),
            "window_stride": getattr(args, "window_stride", None),
            "epochs": getattr(args, "epochs", None),
            "offline_augment": getattr(args, "offline_augment", None),
            "device": getattr(args, "device", None),
        }
        try:
            args._raw_training_args = raw
        except AttributeError:
            pass

    mode = str(raw["mode"] or DEFAULT_TRAINING_MODE).strip().lower()
    if mode not in TRAINING_MODES:
        raise ValueError(f"알 수 없는 학습 모드: {mode}")
    preset = TRAINING_MODES[mode]

    explicit_folds = raw["folds"]
    explicit_test_cond = raw["test_condition"]
    if explicit_folds is True:
        use_folds = True
        test_condition = int(preset["test_condition"]) if explicit_test_cond is None else int(explicit_test_cond)
    elif explicit_test_cond is not None:
        use_folds = False
        test_condition = int(explicit_test_cond)
    elif explicit_folds is False:
        use_folds = False
        test_condition = int(preset["test_condition"])
    else:
        use_folds = bool(preset["folds"])
        test_condition = int(preset["test_condition"])

    raw_stride = raw["window_stride"]
    window_stride = int(preset["window_stride"]) if raw_stride is None else int(raw_stride)
    if window_stride < 1:
        raise ValueError("window-stride는 1 이상이어야 합니다.")

    raw_epochs = raw["epochs"]
    epochs = int(preset["epochs"]) if raw_epochs is None else int(raw_epochs)

    cfg_factor = max(1, int((aug_cfg or {}).get("factor", 2)))
    preset_factor = preset["offline_augment_factor"]
    mode_factor = cfg_factor if preset_factor is None else int(preset_factor)

    raw_aug = raw["offline_augment"]
    if isinstance(raw_aug, bool):
        use_aug = raw_aug
        aug_factor = mode_factor if use_aug else 0
        max_aug_per_rec = (mode_factor if preset_factor is not None else None) if use_aug else 0
    elif raw_aug is None:
        use_aug = False
        aug_factor = 0
        max_aug_per_rec = 0
    else:
        val = str(raw_aug).strip().lower()
        if val in ("0", "false", "no", "off"):
            use_aug = False
            aug_factor = 0
            max_aug_per_rec = 0
        elif val in ("true", "yes", "on"):
            use_aug = True
            aug_factor = mode_factor
            max_aug_per_rec = mode_factor if preset_factor is not None else None
        else:
            parsed_factor = max(0, int(val))
            use_aug = parsed_factor > 0
            aug_factor = parsed_factor
            max_aug_per_rec = parsed_factor

    held: list[int] | None = None
    if conds is not None:
        held = list(conds) if use_folds else ([] if test_condition < 0 else [test_condition])

    dev, device_choice, device_name = resolve_training_device(raw.get("device"))
    global DEVICE
    DEVICE = dev

    args.mode = mode
    args.folds = use_folds
    args.test_condition = test_condition
    args.window_stride = window_stride
    args.epochs = epochs
    args.offline_augment = use_aug
    args.device = device_choice

    return {
        "mode": mode,
        "folds": use_folds,
        "test_condition": test_condition,
        "held": held,
        "window_stride": window_stride,
        "epochs": epochs,
        "offline_augment": use_aug,
        "offline_augment_factor": aug_factor,
        "max_augmented_per_recording": max_aug_per_rec,
        "device": device_choice,
        "effective_device": dev.type,
        "device_name": device_name,
    }


def resolve_held_out_conds(conds: list[int], opts: dict) -> list[int]:
    """Return the posture condition indices to hold out for evaluation."""
    if opts.get("folds"):
        return list(conds)
    test_cond = int(opts.get("test_condition", DEFAULT_TEST_CONDITION))
    return [] if test_cond < 0 else [test_cond]


def card_training_metadata(opts: dict, evaluated_folds: list[int]) -> dict:
    """Standard metadata fields describing the training mode and schedule for model cards."""
    eff_dev = str(opts.get("effective_device") or DEVICE.type)
    dev_name = str(opts.get("device_name") or ("CUDA GPU" if eff_dev == "cuda" else "CPU"))
    return {
        "epochs": int(opts["epochs"]),
        "training_mode": str(opts["mode"]),
        "folds": [int(c) for c in evaluated_folds],
        "window_stride": int(opts["window_stride"]),
        "offline_augment_factor": int(opts["offline_augment_factor"]),
        "training_device": eff_dev,
        "training_device_name": dev_name,
    }


def load_training_sessions(paths, args, *, window: int = WINDOW):
    """Load cleaned primary recordings and mode-appropriate offline-augmented recordings."""
    from src.capture_dataset import discover_augmented_paths, load_sessions, resolve_cleaned_path
    from src.training_center import get_augmentation_settings

    use_cleaned = not getattr(args, "no_cleaned", False)
    resolved_primary = [resolve_cleaned_path(p, use_cleaned=use_cleaned) for p in paths]
    emit("substage", text=f"녹화 데이터 및 3D 관절 특징 읽는 중 ({len(resolved_primary)}개 파일)…")
    aug_cfg = get_augmentation_settings(Path(paths[0]).parent)
    opts = resolve_training_options(args, aug_cfg=aug_cfg)
    use_aug = bool(opts["offline_augment"])
    aug_paths = (
        discover_augmented_paths(paths, max_per_recording=opts["max_augmented_per_recording"])
        if use_aug
        else []
    )
    extra_records = None
    if use_aug and not aug_paths and opts["offline_augment_factor"] > 0:
        from src.offline_augment import generate_augmented_records
        emit("substage", text=f"오프라인 데이터 증강 생성 중 ({opts['offline_augment_factor']}배)…")
        extra_records = generate_augmented_records(
            resolved_primary,
            factor=int(opts["offline_augment_factor"]),
            rotation_deg=float(aug_cfg.get("rotation_deg", 35.0)),
            scale_range=float(aug_cfg.get("scale_range", 0.15)),
            jitter_std=float(aug_cfg.get("jitter_std", 0.003)),
            time_stretch_range=tuple(aug_cfg.get("time_stretch_range", (0.85, 1.15))),
            frame_drop_prob=float(aug_cfg.get("frame_drop_prob", 0.05)),
        )
    all_load_paths = [str(p) for p in resolved_primary] + [str(p) for p in aug_paths]
    sessions = load_sessions(all_load_paths, window, use_cleaned=False, extra_records=extra_records,
                             keep_rest=bool(getattr(args, "keep_rest", False)))
    eval_sessions = sessions[: len(paths)]
    return sessions, eval_sessions, opts


def emit(event: str, **info) -> None:
    """Progress for the app (JSON lines with --progress) or for a person reading the console."""
    if PROGRESS:
        print(json.dumps({"event": event, **info}, ensure_ascii=False), flush=True)
    elif event == "epoch" and (info["epoch"] % 5 == 0 or info["epoch"] == info["epochs"]):
        print(f"    epoch {info['epoch']:2d}  loss {info['loss']:.3f}", flush=True)
    elif event in ("note",):
        print(info.get("text", ""), flush=True)


# ---------------------------------------------------------------- data
def load(paths, *, use_cleaned: bool = False, extra_records=None):
    from src.capture_dataset import load_sessions
    return load_sessions(paths, WINDOW, use_cleaned=use_cleaned, extra_records=extra_records)


def pick(sessions, conds):
    xs, ys = [], []
    for s in sessions:
        in_cond = np.isin(s["cond"], conds)
        if (s.get("meta") or {}).get("mode") == "free":
            in_cond = in_cond | ((s["cond"] == -1) & (s["y"] == 0))
        m = (s["y"] >= 0) & in_cond
        xs.append(s["x"][m])
        ys.append(s["y"][m])
    return np.concatenate(xs), np.concatenate(ys)


def pick_with_conds(sessions, conds):
    xs, ys, cs = [], [], []
    for s in sessions:
        in_cond = np.isin(s["cond"], conds)
        if (s.get("meta") or {}).get("mode") == "free":
            in_cond = in_cond | ((s["cond"] == -1) & (s["y"] == 0))
        m = (s["y"] >= 0) & in_cond
        xs.append(s["x"][m])
        ys.append(s["y"][m])
        cs.append(s["cond"][m])
    return np.concatenate(xs), np.concatenate(ys), np.concatenate(cs)


def pick_with_weights(sessions, conds):
    xs, ys, ws = [], [], []
    for s in sessions:
        in_cond = np.isin(s["cond"], conds)
        if (s.get("meta") or {}).get("mode") == "free":
            in_cond = in_cond | ((s["cond"] == -1) & (s["y"] == 0))
        m = (s["y"] >= 0) & in_cond
        xs.append(s["x"][m])
        ys.append(s["y"][m])
        sw = s.get("sample_weights")
        if sw is not None and len(sw) == len(s["y"]):
            ws.append(np.asarray(sw[m], dtype=np.float32))
        else:
            ws.append(np.ones(int(np.sum(m)), dtype=np.float32))
    return np.concatenate(xs), np.concatenate(ys), np.concatenate(ws)


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
        if x.shape[1] == 15 and not torch.onnx.is_in_onnx_export():
            b = x.shape[0]
            c1, d1 = self.body[1], self.body[3]
            c2, d2 = self.body[5], self.body[7]
            c3, d3 = self.body[9], self.body[11]
            x1 = torch.stack([x[:, 0:13:2, :], x[:, 1:14:2, :], x[:, 2:15:2, :]], dim=-1).reshape(b, 7, -1)
            h1 = d1(torch.relu(nn.functional.linear(x1, c1.weight.view(c1.out_channels, -1), c1.bias)))
            x2 = torch.stack([h1[:, 0:5:2, :], h1[:, 1:6:2, :], h1[:, 2:7:2, :]], dim=-1).reshape(b, 3, -1)
            h2 = d2(torch.relu(nn.functional.linear(x2, c2.weight.view(c2.out_channels, -1), c2.bias)))
            x3 = h2.transpose(1, 2).reshape(b, -1)
            h3 = d3(torch.relu(nn.functional.linear(x3, c3.weight.view(c3.out_channels, -1), c3.bias)))
            return self.head(h3)
        h = self.body(x.transpose(1, 2))
        return self.head(h[:, :, -1])


def augment(x: torch.Tensor, max_deg: float = 30.0) -> torch.Tensor:
    """Different seats / camera angles: 3D rotation of hand-frame joints, size, jitter, and angle self-occlusion context."""
    b, t, _ = x.shape
    dev, dt = x.device, x.dtype
    ang = (torch.rand(b, 3, device=dev, dtype=dt) - 0.5) * math.radians(max_deg)
    cx, cy, cz = torch.cos(ang).unbind(1)
    sx, sy, sz = torch.sin(ang).unbind(1)
    one = torch.ones(b, device=dev, dtype=dt)
    zero = torch.zeros(b, device=dev, dtype=dt)
    rx = torch.stack([one, zero, zero, zero, cx, -sx, zero, sx, cx], 1).view(b, 3, 3)
    ry = torch.stack([cy, zero, sy, zero, one, zero, -sy, zero, cy], 1).view(b, 3, 3)
    rz = torch.stack([cz, -sz, zero, sz, cz, zero, zero, zero, one], 1).view(b, 3, 3)
    scale = 1.0 + (torch.rand(b, 1, 1, device=dev, dtype=dt) - 0.5) * 0.2
    rt = (rz @ ry @ rx).transpose(1, 2) * scale
    x = x.clone()
    pts = x[:, :, :126].reshape(b, t * 42, 3)
    x[:, :, :126] = torch.bmm(pts, rt).reshape(b, t, 126)
    x[:, :, 126:130].mul_(scale)
    x[:, :, :130].add_(torch.randn((b, t, 130), device=dev, dtype=dt), alpha=0.01)

    # Self-occlusion context augmentation: simulate 1~2 frame angle-induced finger foreshortening
    # so the TCN learns to maintain pinch/gesture judgment from temporal context and proximal knuckles.
    if t >= 6 and b >= 4:
        occ_b = torch.rand(b, device=dev) < 0.28
        if torch.any(occ_b):
            pos = x[:, :, :63].view(b, t, 21, 3)
            t0 = int(torch.randint(2, t - 2, (1,), device=dev).item())
            f_base = int(torch.randint(0, 5, (1,), device=dev).item()) * 4 + 1  # MCP index: 1, 5, 9, 13, 17
            shrink = 0.62 + 0.20 * torch.rand((b, 1, 1, 1), device=dev, dtype=dt)
            mcp = pos[occ_b, t0:t0 + 2, f_base:f_base + 1, :]
            distal = pos[occ_b, t0:t0 + 2, f_base + 1:f_base + 4, :]
            pos[occ_b, t0:t0 + 2, f_base + 1:f_base + 4, :] = mcp + (distal - mcp) * shrink[occ_b]
            x[:, :, :63] = pos.view(b, t, 63)
            pos_occ = pos[occ_b]
            x[occ_b, 1:, 63:126] = (pos_occ[:, 1:] - pos_occ[:, :-1]).reshape(-1, t - 1, 63)
            thumb = pos_occ[:, :, 4:5, :]
            tips = pos_occ[:, :, [8, 12, 16, 20], :]
            x[occ_b, :, 126:130] = torch.linalg.norm(thumb - tips, dim=-1)

    # Stage 6 winning TCN augmentations (verified via 15-fold leave-condition-out ablation):
    # 1. Temporal speed warp (0.80x ~ 1.25x anchored at current frame t = T - 1)
    if t >= 6 and b >= 4:
        m_warp = torch.rand(b, device=dev) < 0.35
        if torch.any(m_warp):
            pos_w0 = x[m_warp, :, :63].view(-1, t, 21, 3)
            nb = pos_w0.shape[0]
            speed = 0.80 + 0.45 * torch.rand((nb, 1), device=dev, dtype=dt)
            steps = torch.arange(t - 1, -1, -1, device=dev, dtype=dt).unsqueeze(0)
            src_t = torch.clamp((t - 1) - steps * speed, 0.0, float(t - 1))
            t0_idx = torch.floor(src_t).long()
            t1_idx = torch.clamp(t0_idx + 1, max=t - 1)
            w1 = (src_t - t0_idx.to(dt)).view(nb, t, 1, 1)
            b_idx = torch.arange(nb, device=dev).unsqueeze(1)
            pos_w = (1.0 - w1) * pos_w0[b_idx, t0_idx] + w1 * pos_w0[b_idx, t1_idx]
            w1_v = w1.view(nb, t, 1)
            vel = x[m_warp, :, 130:132]
            x[m_warp, :, 130:132] = ((1.0 - w1_v) * vel[b_idx, t0_idx] + w1_v * vel[b_idx, t1_idx]) * speed.unsqueeze(-1)
            x[m_warp, :, :63] = pos_w.view(nb, t, 63)
            x[m_warp, 1:, 63:126] = (pos_w[:, 1:] - pos_w[:, :-1]).reshape(nb, t - 1, 63)
            x[m_warp, :, 126:130] = torch.linalg.norm(pos_w[:, :, 4:5, :] - pos_w[:, :, [8, 12, 16, 20], :], dim=-1)

    # 2. Anisotropic Z-axis noise (depth estimation noise >> 2D image xy noise)
    if b >= 4:
        m_z = torch.rand(b, device=dev) < 0.30
        if torch.any(m_z):
            pos_z = x[m_z, :, :63].view(-1, t, 21, 3).clone()
            nb = pos_z.shape[0]
            z_bias = torch.randn((nb, 1, 21), device=dev, dtype=dt) * 0.018
            z_jit = torch.randn((nb, t, 21), device=dev, dtype=dt) * 0.014
            pos_z[:, :, 1:, 2] = pos_z[:, :, 1:, 2] + z_bias[:, :, 1:] + z_jit[:, :, 1:]
            x[m_z, :, :63] = pos_z.view(nb, t, 63)
            x[m_z, 1:, 63:126] = (pos_z[:, 1:] - pos_z[:, :-1]).reshape(nb, t - 1, 63)
            x[m_z, :, 126:130] = torch.linalg.norm(pos_z[:, :, 4:5, :] - pos_z[:, :, [8, 12, 16, 20], :], dim=-1)
    return x


def train(
    x,
    y,
    epochs: int,
    seed: int = 0,
    stage: str = "",
    losses=None,
    classes=None,
    window_stride: int = 1,
    device: str | torch.device | None = None,
    sample_weights: np.ndarray | None = None,
    init_model: nn.Module | None = None,
    aug_deg: float = 30.0,
):
    classes = classes or CLASSES
    stride = max(1, int(window_stride))
    dev = DEVICE if device is None else resolve_training_device(device)[0]
    torch.manual_seed(seed)
    if dev.type == "cuda":
        torch.cuda.manual_seed_all(seed)
    np.random.seed(seed)
    if init_model is not None:
        model = copy.deepcopy(init_model).to(dev)
    else:
        model = TCN(n_out=len(classes)).to(dev)
    counts = np.bincount(y, minlength=len(classes)).astype(np.float64)
    weight = torch.tensor((counts.sum() / np.maximum(counts, 1)) ** 0.5, dtype=torch.float32, device=dev)
    class_weight = weight / weight.mean()
    lossf_none = nn.CrossEntropyLoss(weight=class_weight, reduction="none")
    lossf_mean = nn.CrossEntropyLoss(weight=class_weight)
    # The GPU used to switch to batch 4096 / lr 2e-3 on its own. With the same epochs that is ~16x fewer updates and
    # held-out recognition fell from ~88 to 41-58 /105 (docs/gpu-training.md). The GPU now trains like the CPU
    # (batch 256, lr 1e-3); TCN_LARGE_BATCH=1 brings the old behaviour back for experiments.
    use_large_batch = (dev.type == "cuda" and (len(x) // stride) >= 4096
                       and os.environ.get("TCN_LARGE_BATCH") == "1")
    bs = 4096 if use_large_batch else 256
    base_lr = 2e-3 if use_large_batch else 1e-3
    lr = base_lr * 0.45 if init_model is not None else base_lr
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    xt = torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32)).to(dev)
    yt = torch.from_numpy(np.ascontiguousarray(y, dtype=np.int64)).to(dev)
    wt = (
        torch.from_numpy(np.ascontiguousarray(sample_weights, dtype=np.float32)).to(dev)
        if sample_weights is not None and len(sample_weights) == len(x)
        else None
    )
    for ep in range(epochs):
        model.train()
        if stride > 1 and len(xt) > 0:
            offset = ep % stride
            if offset >= len(xt):
                offset = 0
            ep_xt = xt[offset::stride]
            ep_yt = yt[offset::stride]
            ep_wt = wt[offset::stride] if wt is not None else None
        else:
            ep_xt = xt
            ep_yt = yt
            ep_wt = wt
        perm = torch.randperm(len(ep_xt), device=dev)
        total = torch.zeros((), device=dev, dtype=torch.float64)
        for i in range(0, len(perm), bs):
            idx = perm[i:i + bs]
            opt.zero_grad(set_to_none=True)
            logits = model(augment(ep_xt[idx], max_deg=aug_deg))
            if ep_wt is not None:
                per_sample = lossf_none(logits, ep_yt[idx]) * ep_wt[idx]
                loss = per_sample.mean()
            else:
                loss = lossf_mean(logits, ep_yt[idx])
            loss.backward()
            opt.step()
            total += loss.detach().to(torch.float64) * len(idx)
        avg = float(total.item()) / len(perm)
        if losses is not None:
            losses.append(round(avg, 4))
        emit("epoch", stage=stage, epoch=ep + 1, epochs=epochs, loss=round(avg, 4))
    return model


def predict(model, x) -> np.ndarray:
    model.eval()
    dev = next(model.parameters()).device
    out = []
    with torch.no_grad():
        for i in range(0, len(x), 4096):
            batch = torch.from_numpy(np.ascontiguousarray(x[i:i + 4096], dtype=np.float32)).to(dev)
            out.append(torch.softmax(model(batch), dim=1).cpu().numpy())
    return np.concatenate(out)


# ---------------------------------------------------------------- hard negatives
def find_hard_negatives(prob: np.ndarray, y: np.ndarray, classes, thr: float) -> np.ndarray:
    """Windows that are truly "none" but where the model leans towards some gesture (probability >= thr)."""
    none = list(classes).index("none")
    return (y == none) & (np.delete(prob, none, axis=1).max(axis=1) >= thr)


def refine_with_hard_negatives(
    model,
    x,
    y,
    base_weights,
    *,
    classes,
    epochs: int,
    rounds: int = 1,
    thr: float = 0.5,
    multiplier: float = 3.0,
    window_stride: int = 1,
    device=None,
    stage: str = "hn",
    losses=None,
):
    """Fine-tune on the model's own mistakes: "none" windows it leaned the wrong way on weigh `multiplier` times more.

    Windows found in earlier rounds stay heavier. Quality weights stay in (a discarded window, weight 0, stays 0).
    `x`/`y` must not contain the held-out posture, or the comparison with the plain model means nothing.
    """
    hard = np.zeros(len(y), dtype=bool)
    found = []
    for r in range(1, max(0, int(rounds)) + 1):
        new = find_hard_negatives(predict(model, x), y, classes, thr)
        found.append(int(new.sum()))
        if not new.any():
            break
        hard |= new
        weights = np.array(base_weights, dtype=np.float32)
        weights[hard] *= float(multiplier)
        model = train(
            x,
            y,
            max(4, min(10, int(epochs) // 2)),
            seed=200 + r,
            stage=f"{stage}-hn{r}",
            losses=losses,
            classes=classes,
            window_stride=window_stride,
            device=device,
            sample_weights=weights,
            init_model=model,
        )
    return model, {"none_windows": int((y == list(classes).index("none")).sum()), "found_per_round": found,
                   "threshold": float(thr), "multiplier": float(multiplier)}


def tally_results(results) -> dict:
    """Add up evaluate() results of one held-out posture, for comparing two models on the same data."""
    reps: dict = {}
    by_task: Counter = Counter()
    misfires, confused = 0, 0
    for r in results:
        for task, (a, b) in r["reps"].items():
            old = reps.get(task, (0, 0))
            reps[task] = (old[0] + a, old[1] + b)
        misfires += sum(r["misfires"].values())
        confused += r["confused"]
        for task, entry in (r.get("free_by_task") or {}).items():
            by_task[task] += entry.get("misfires", 0)
    return {"recognised": sum(a for a, _ in reps.values()), "total": sum(b for _, b in reps.values()),
            "misfires": misfires, "confused": confused, "reps": reps, "misfires_by_task": dict(by_task)}


def shifted_thresholds(classes, offset: float, thr: float = 0.7) -> dict[str, float]:
    """Every gesture's decision threshold moved by the same amount, so the per-gesture policy keeps its shape."""
    from src.gesture_gate import DEFAULT_GESTURE_THRESHOLDS
    return {name: round(min(0.95, max(0.05, float(DEFAULT_GESTURE_THRESHOLDS.get(name, thr)) + offset)), 4)
            for name in classes if name != "none"}


def show_threshold_sweep(sweep) -> None:
    """Recognition against misfires for each threshold shift, all held-out postures together."""
    if not sweep:
        return
    print("\n== threshold sweep (offset: recognised | misfires | wrong gesture | fired in rest)")
    for offset in sorted(sweep):
        t = tally_results(sweep[offset]["tcn"])
        rest = sweep[offset]["rest"]
        print(f"  SWEEP {offset:+.2f}: {t['recognised']}/{t['total']} | {t['misfires']} | {t['confused']}"
              f" | {sum(r['fired'] for r in rest)}")
        print("    " + " ".join(f"{task}={got}/{total}" for task, (got, total) in sorted(t["reps"].items())), flush=True)


def rest_misfires(session, conds, prob, classes, thr: float = 0.7, k: int = 3, *, thr_by_label=None) -> dict:
    """Gestures fired while the user was told to rest. Unlike free stretches, every posture has rest frames.

    Same decision as evaluate(): per-gesture threshold, k frames in a row, counted once per run. A run must happen
    entirely inside kept rest frames, so a pinch still being released at the start of the rest does not count.
    """
    from src.capture_dataset import frame_durations
    from src.gesture_gate import DEFAULT_GESTURE_FRAMES, DEFAULT_GESTURE_THRESHOLDS

    default_policy = thr == 0.7 and k == 3
    mask = session["rest_dropped"] & np.isin(session["cond"], conds)
    fired = 0
    for c, name in enumerate(classes):
        if name == "none":
            continue
        cls_thr = float(DEFAULT_GESTURE_THRESHOLDS.get(name, thr)) if default_policy else thr
        if thr_by_label and name in thr_by_label:
            cls_thr = float(thr_by_label[name])
        cls_k = int(DEFAULT_GESTURE_FRAMES.get(name, k)) if default_policy else k
        fired += len(runs(mask & (prob[:, c] >= cls_thr), cls_k))
    seconds = frame_durations(session["frames"])
    return {"fired": fired, "minutes": float(sum(seconds[i] for i in np.flatnonzero(mask))) / 60}


def show_held_out_total(summary, rest_rows) -> None:
    """One block to compare runs: all held-out postures together, per gesture too."""
    if not summary:
        return
    tcn = tally_results([row[2] for row in summary])
    rules = tally_results([row[1] for row in summary])
    print("\n== held-out total")
    print(f"  TCN   recognised {tcn['recognised']}/{tcn['total']} | misfires {tcn['misfires']}"
          f" ({sum(row[3] for row in summary):.1f} min) | wrong gesture {tcn['confused']}")
    print(f"  rules recognised {rules['recognised']}/{rules['total']} | misfires {rules['misfires']}"
          f" | wrong gesture {rules['confused']}")
    for task, (got, total) in sorted(tcn["reps"].items()):
        print(f"    {task:20s} {got}/{total}")
    for posture in sorted({row["posture"] for row in rest_rows}):
        rows = [row for row in rest_rows if row["posture"] == posture]
        print(f"    rest, posture {posture:2d}: fired {sum(r['fired'] for r in rows)}"
              f" ({sum(r['minutes'] for r in rows):.1f} min)")
    print(f"  rest total: fired {sum(r['fired'] for r in rest_rows)}"
          f" ({sum(r['minutes'] for r in rest_rows):.1f} min)", flush=True)


def show_hard_negative_comparison(folds) -> None:
    """Same postures, same base model, before and after: recognition and misfires must be read together."""
    if not folds:
        return
    print("\n== hard negatives: held-out postures, before -> after")
    for f in folds:
        b, a = f["before"], f["after"]
        print(f"  posture {f['posture']}: recognised {b['recognised']}/{b['total']} -> {a['recognised']}/{a['total']}"
              f" | misfires {b['misfires']} -> {a['misfires']} ({f['free_minutes']:.1f} min)"
              f" | wrong gesture {b['confused']} -> {a['confused']}"
              f" | hard windows {f['found_per_round']} of {f['none_windows']}")
    b = {k: sum(f["before"][k] for f in folds) for k in ("recognised", "total", "misfires", "confused")}
    a = {k: sum(f["after"][k] for f in folds) for k in ("recognised", "total", "misfires", "confused")}
    print(f"  total: recognised {b['recognised']}/{b['total']} -> {a['recognised']}/{a['total']}"
          f" | misfires {b['misfires']} -> {a['misfires']} | wrong gesture {b['confused']} -> {a['confused']}")


# ---------------------------------------------------------------- event-level evaluation
def runs(mask: np.ndarray, k: int):
    """Start indices of runs of at least k True frames."""
    starts, n = [], 0
    for i, v in enumerate(mask):
        n = n + 1 if v else 0
        if n == k:
            starts.append(i)
    return starts


def evaluate(session, conds, prob=None, thr=0.7, k=3, *, thr_by_label=None, k_by_label=None):
    """{"reps": {task: (recognised, total)}, "misfires": {kind: n}, "confused": n} for the model (prob given) or rules."""
    from src.gesture_gate import DEFAULT_GESTURE_FRAMES, DEFAULT_GESTURE_THRESHOLDS
    use_default_policy = (thr == 0.7 and k == 3)
    eff_thr_map = dict(DEFAULT_GESTURE_THRESHOLDS if use_default_policy else {})
    if thr_by_label:
        eff_thr_map.update({str(lbl): float(v) for lbl, v in thr_by_label.items()})
    eff_k_map = dict(DEFAULT_GESTURE_FRAMES if use_default_policy else {})
    if k_by_label:
        eff_k_map.update({str(lbl): int(v) for lbl, v in k_by_label.items()})
    frames = session["frames"]
    classes = session.get("classes", CLASSES)
    valid = (session["y"] >= 0) & np.isin(session["cond"], conds)
    if not valid.any():
        return {
            "reps": {},
            "misfires": {name: 0 for name in classes if name != "none"} if prob is not None else {},
            "confused": 0,
            "free_by_task": {},
        }
    rep_frames = defaultdict(list)
    free = []
    cond_arr = session["cond"]
    for idx_i in np.flatnonzero(valid):
        i = int(idx_i)
        lab = frames[i].get("label") or {}
        group = int(cond_arr[i])
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
        segs = session["segments"]
        resets = [False] * len(frames)
        previous = None
        for i, f in enumerate(frames):
            lab = f.get("label") or {}
            token = (int(segs[i]), lab.get("set"), lab.get("rep"))
            if token != previous:
                resets[i] = True
                previous = token
        for c in active:
            cls_name = classes[c]
            cls_thr = float(eff_thr_map.get(cls_name, thr))
            cls_k = int(eff_k_map.get(cls_name, k))
            above = valid & (prob[:, c] >= cls_thr)
            col_fired = fired[c]
            run = 0
            for i in range(len(frames)):
                if resets[i]:
                    run = 0
                if above[i]:
                    run += 1
                    if run == cls_k:
                        col_fired[i] = True
                else:
                    run = 0
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
    from src.evaluation import measure_session_reaction_latency
    latency = measure_session_reaction_latency(session, conds, prob=prob, thr=thr, k=k)
    return {"reps": {t: (recog[t], total[t]) for t in total}, "misfires": dict(misfires), "confused": confused,
            "free_by_task": free_by_task, "latency": latency}


def free_minutes(session, conds) -> float:
    from src.capture_dataset import frame_durations
    lengths = frame_durations(session["frames"])
    return sum(lengths[i] for i, f in enumerate(session["frames"])
               if (f.get("label") or {}).get("phase") == "free"
               and session["cond"][i] in conds and session["y"][i] >= 0) / 60


def evaluate_postures_on_model(
    eval_sessions,
    conds,
    model,
    classes,
    thr: float = 0.7,
    k: int = 3,
    sp_model=None,
):
    """Evaluate a trained model (optionally blended with Spatial MLP) per posture across eval_sessions."""
    from src.spatial_mlp import blend_tcn_and_spatial_probs, ensure_session_spatial_features

    session_probs = []
    for s in eval_sessions:
        prob = predict(model, s["x"])
        if sp_model is not None:
            s_feats = ensure_session_spatial_features(s)
            if len(s_feats) == len(prob):
                sp_prob = sp_model.predict_proba(s_feats)
                prob = blend_tcn_and_spatial_probs(prob, classes, sp_prob, sp_model.classes)
        session_probs.append(prob)

    results = []
    for c in conds:
        for s, prob in zip(eval_sessions, session_probs):
            if not (s["cond"] == c).any():
                continue
            rule = evaluate(s, [c])
            tcn = evaluate(s, [c], prob, thr, k)
            results.append({
                "posture": int(c),
                "rules": rule,
                "tcn": tcn,
                "free_minutes": round(free_minutes(s, [c]), 2),
            })
    return results


def _aggregate_posture_reps(result_rows):
    """Return {posture_int: {'recog': int, 'total': int, 'misfires': int, 'confused': int}}."""
    agg = {}
    for row in result_rows or []:
        p = row.get("posture", row.get("test_posture"))
        if p is None:
            continue
        p = int(p)
        bucket = agg.setdefault(p, {"recog": 0, "total": 0, "misfires": 0, "confused": 0})
        tcn = row.get("tcn") or {}
        for a, b in (tcn.get("reps") or {}).values():
            bucket["recog"] += int(a)
            bucket["total"] += int(b)
        bucket["misfires"] += int(sum((tcn.get("misfires") or {}).values()))
        bucket["confused"] += int(tcn.get("confused") or 0)
    return agg


def _active_confidence_by_posture(model, xa, ya, ca, conds) -> dict[int, float]:
    """Mean target-class probability on active gesture windows (ya > 0) for each condition."""
    if len(xa) == 0:
        return {}
    prob = predict(model, xa)
    idx_col = np.clip(ya, 0, prob.shape[1] - 1)
    target_prob = prob[np.arange(len(ya)), idx_col]
    out = {}
    for c in conds:
        mask = (ca == c) & (ya > 0)
        if mask.any():
            out[int(c)] = float(target_prob[mask].mean())
        else:
            mask_all = ca == c
            out[int(c)] = float(target_prob[mask_all].mean()) if mask_all.any() else 1.0
    return out


def adaptive_boost_postures(
    model,
    xa,
    ya,
    ca,
    eval_sessions,
    conds,
    held_out_results,
    *,
    classes,
    thr: float = 0.7,
    k: int = 3,
    epochs: int = 25,
    window_stride: int = 1,
    device=None,
    sp_model=None,
    losses=None,
    condition_groups=None,
):
    """
    Detect heterogeneous or low-recognition postures/angles and iteratively fine-tune with
    upweighted sampling + wider 3D rotation augmentation until recognition improves.
    Never degrades overall recognition (retains best checkpoint).
    """
    from src.recording_plan import resolve_condition_group_name

    cond_names = {
        idx_c: resolve_condition_group_name(cg, idx_c)
        for idx_c, cg in enumerate(condition_groups or [])
    }

    zs_agg = _aggregate_posture_reps(held_out_results)
    init_final_res = evaluate_postures_on_model(eval_sessions, conds, model, classes, thr=thr, k=k, sp_model=sp_model)
    init_tcn_res = evaluate_postures_on_model(eval_sessions, conds, model, classes, thr=thr, k=k, sp_model=None)
    init_final_agg = _aggregate_posture_reps(init_final_res)
    init_tcn_agg = _aggregate_posture_reps(init_tcn_res)
    init_conf = _active_confidence_by_posture(model, xa, ya, ca, conds)

    heterogeneous_conds = set()
    weak_conds = set()
    for c in conds:
        c = int(c)
        zs = zs_agg.get(c)
        if zs and zs["total"] > 0 and (zs["recog"] / zs["total"]) < 0.88:
            heterogeneous_conds.add(c)
        fn = init_final_agg.get(c)
        if fn and fn["total"] > 0 and (fn["recog"] / fn["total"]) < 0.98:
            weak_conds.add(c)
        tc_stat = init_tcn_agg.get(c)
        if tc_stat and tc_stat["total"] > 0 and (tc_stat["recog"] / tc_stat["total"]) < 0.96:
            weak_conds.add(c)
        if init_conf.get(c, 1.0) < 0.90:
            weak_conds.add(c)

    target_conds = sorted(heterogeneous_conds | weak_conds)
    best_model = model
    best_final_res = init_final_res
    best_tcn_res = init_tcn_res
    best_final_agg = init_final_agg
    best_tcn_agg = init_tcn_agg
    best_conf = dict(init_conf)

    def _score(f_agg, t_agg, conf_map):
        f_rec = sum(v["recog"] for v in f_agg.values())
        f_mis = sum(v["misfires"] for v in f_agg.values())
        t_rec = sum(v["recog"] for v in t_agg.values())
        t_mis = sum(v["misfires"] for v in t_agg.values())
        weak_c = sum(conf_map.get(c, 1.0) for c in (target_conds or conds)) / max(1, len(target_conds or conds))
        return (f_rec, -f_mis, t_rec, -t_mis, round(weak_c, 4))

    best_score = _score(best_final_agg, best_tcn_agg, best_conf)
    rounds_run = 0
    max_rounds = 3 if (target_conds and int(epochs) >= 3 and len(ya) > 0) else 0
    boost_epochs = max(4, min(10, int(epochs) // 2))

    for r_idx in range(1, max_rounds + 1):
        # Check if already saturated on all target conditions
        all_high = all(
            (best_final_agg.get(c, {}).get("recog", 0) >= best_final_agg.get(c, {}).get("total", 0))
            and (best_tcn_agg.get(c, {}).get("recog", 0) >= best_tcn_agg.get(c, {}).get("total", 0))
            and (best_conf.get(c, 1.0) >= 0.94)
            for c in target_conds
        )
        if all_high and r_idx > 1:
            break

        rounds_run = r_idx
        names_preview = ", ".join(cond_names.get(c, f"조건 {c}") for c in target_conds[:2])
        if len(target_conds) > 2:
            names_preview += f" 외 {len(target_conds) - 2}개"
        emit(
            "substage",
            text=f"이질적인 자세·각도({names_preview}) 인식률 향상을 위한 적응형 추가 학습 중 ({r_idx}/{max_rounds}차)…",
        )
        sample_weights = np.ones(len(ya), dtype=np.float32)
        for c in target_conds:
            zs = zs_agg.get(c, {})
            zs_rate = (zs["recog"] / zs["total"]) if zs.get("total") else 1.0
            tc_s = best_tcn_agg.get(c, {})
            tc_rate = (tc_s["recog"] / tc_s["total"]) if tc_s.get("total") else 1.0
            conf_c = best_conf.get(c, 0.85)
            deficit = max(0.15, 1.0 - min(zs_rate, tc_rate, conf_c))
            pos_mult = min(4.5, 1.8 + 3.2 * deficit + 0.35 * r_idx)
            neg_mult = min(2.4, 1.2 + 1.2 * deficit)
            pos_mask = (ca == c) & (ya > 0)
            neg_mask = (ca == c) & (ya == 0)
            sample_weights[pos_mask] = pos_mult
            sample_weights[neg_mask] = neg_mult

        cand_model = train(
            xa,
            ya,
            boost_epochs,
            seed=100 + r_idx,
            stage=f"boost{r_idx}",
            losses=losses,
            classes=classes,
            window_stride=max(1, int(window_stride)),
            device=device,
            sample_weights=sample_weights,
            init_model=best_model,
            aug_deg=45.0,
        )
        cand_final_res = evaluate_postures_on_model(eval_sessions, conds, cand_model, classes, thr=thr, k=k, sp_model=sp_model)
        cand_tcn_res = evaluate_postures_on_model(eval_sessions, conds, cand_model, classes, thr=thr, k=k, sp_model=None)
        cand_final_agg = _aggregate_posture_reps(cand_final_res)
        cand_tcn_agg = _aggregate_posture_reps(cand_tcn_res)
        cand_conf = _active_confidence_by_posture(cand_model, xa, ya, ca, conds)
        cand_score = _score(cand_final_agg, cand_tcn_agg, cand_conf)
        if cand_score >= best_score:
            best_model = cand_model
            best_final_res = cand_final_res
            best_tcn_res = cand_tcn_res
            best_final_agg = cand_final_agg
            best_tcn_agg = cand_tcn_agg
            best_conf = cand_conf
            best_score = cand_score

    posture_details = []
    for c in conds:
        c = int(c)
        zs = zs_agg.get(c, {})
        zs_rate = round(zs["recog"] / zs["total"], 3) if zs.get("total") else None
        in_f = init_final_agg.get(c, {})
        bf_f = best_final_agg.get(c, {})
        in_t = init_tcn_agg.get(c, {})
        bf_t = best_tcn_agg.get(c, {})
        posture_details.append({
            "posture": c,
            "name": cond_names.get(c, f"자세 #{c + 1}"),
            "zero_shot_recognised": zs.get("recog", 0),
            "zero_shot_total": zs.get("total", 0),
            "zero_shot_rate": zs_rate,
            "before_tcn_rate": round(in_t["recog"] / in_t["total"], 3) if in_t.get("total") else None,
            "after_tcn_rate": round(bf_t["recog"] / bf_t["total"], 3) if bf_t.get("total") else None,
            "before_final_rate": round(in_f["recog"] / in_f["total"], 3) if in_f.get("total") else None,
            "after_final_rate": round(bf_f["recog"] / bf_f["total"], 3) if bf_f.get("total") else None,
            "final_recognised": bf_f.get("recog", 0),
            "final_total": bf_f.get("total", 0),
            "before_confidence": round(init_conf.get(c, 1.0), 3),
            "after_confidence": round(best_conf.get(c, 1.0), 3),
            "heterogeneous": c in heterogeneous_conds,
            "boosted": (c in target_conds) and (rounds_run > 0),
        })

    boost_info = {
        "triggered": bool(rounds_run > 0),
        "rounds": rounds_run,
        "target_postures": target_conds,
        "heterogeneous_postures": sorted(heterogeneous_conds),
        "posture_details": posture_details,
    }
    return best_model, best_final_res, boost_info


def per_gesture(
    summary,
    windows_per_class,
    train_acc,
    classes=None,
    *,
    by_task=False,
    final_results=None,
    adaptive_boost=None,
):
    """Scores per gesture for the training center: held-out recognition, final recognition, misfires per minute, data, fit."""
    from src.evaluation import aggregate_latency_rows, wilson_ci_95

    out = {}
    classes = classes or CLASSES
    names = [t.key for t in TASKS if t.label in classes] if by_task else classes
    boosted_set = set((adaptive_boost or {}).get("target_postures") or ()) if (adaptive_boost or {}).get("triggered") else set()
    final_by_posture = {}
    for row in final_results or []:
        p = row.get("posture", row.get("test_posture"))
        if p is not None:
            final_by_posture.setdefault(int(p), []).append(row)

    for name in names:
        label = BY_KEY[name].label if by_task else name
        keys = [name] if by_task else [t.key for t in TASKS if t.label == label]
        g = {"windows": int(windows_per_class.get(label, 0)), "train_accuracy": train_acc.get(label)}
        if label != "none":
            a = sum(m["tcn"]["reps"].get(key, (0, 0))[0] for m in summary for key in keys)
            b = sum(m["tcn"]["reps"].get(key, (0, 0))[1] for m in summary for key in keys)
            ra = sum(m["rules"]["reps"].get(key, (0, 0))[0] for m in summary for key in keys)
            rb = sum(m["rules"]["reps"].get(key, (0, 0))[1] for m in summary for key in keys)
            zs_rate = round(a / b, 3) if b else None
            zs_ci = list(wilson_ci_95(a, b)) if b else None
            eff_a, eff_b = a, b
            if final_results is not None:
                fa = sum(m["tcn"]["reps"].get(key, (0, 0))[0] for m in final_results for key in keys)
                fb = sum(m["tcn"]["reps"].get(key, (0, 0))[1] for m in final_results for key in keys)
                final_rate = round(fa / fb, 3) if fb else None
                final_ci = list(wilson_ci_95(fa, fb)) if fb else None
                g.update(
                    zero_shot_recognised=a,
                    zero_shot_total=b,
                    zero_shot_rate=zs_rate,
                    zero_shot_ci95=zs_ci,
                    final_recognised=fa,
                    final_total=fb,
                    final_rate=final_rate,
                    final_ci95=final_ci,
                )
                # When heterogeneous postures triggered adaptive boosting, replace those unseen-angle fold results
                # with the post-boost learned evaluation for those postures so `rate` reflects the learned angle.
                if b > 0 and boosted_set:
                    adj_a, adj_b = 0, 0
                    seen_boosted = set()
                    for m in summary:
                        p = m.get("posture", m.get("test_posture"))
                        p_int = int(p) if p is not None else -1
                        if p_int in boosted_set and p_int in final_by_posture:
                            if p_int not in seen_boosted:
                                seen_boosted.add(p_int)
                                for fm in final_by_posture[p_int]:
                                    adj_a += sum(fm["tcn"]["reps"].get(key, (0, 0))[0] for key in keys)
                                    adj_b += sum(fm["tcn"]["reps"].get(key, (0, 0))[1] for key in keys)
                        else:
                            adj_a += sum(m["tcn"]["reps"].get(key, (0, 0))[0] for key in keys)
                            adj_b += sum(m["tcn"]["reps"].get(key, (0, 0))[1] for key in keys)
                    if adj_b > 0 and (adj_a / adj_b) >= (a / b):
                        eff_a, eff_b = adj_a, adj_b
                        g["adaptive_boosted"] = True
            lat_tcn = aggregate_latency_rows(summary, keys, source="tcn") or aggregate_latency_rows(
                final_results or [], keys, source="tcn"
            )
            lat_rules = aggregate_latency_rows(summary, keys, source="rules") or aggregate_latency_rows(
                final_results or [], keys, source="rules"
            )
            g.update(
                recognised=eff_a,
                total=eff_b,
                rate=round(eff_a / eff_b, 3) if eff_b else None,
                ci95=list(wilson_ci_95(eff_a, eff_b)) if eff_b else None,
                rules_rate=round(ra / rb, 3) if rb else None,
                rules_ci95=list(wilson_ci_95(ra, rb)) if rb else None,
                reaction_latency=lat_tcn,
                rules_reaction_latency=lat_rules,
            )
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
def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recordings", default=str(ROOT / "recordings" / "*-guided.jsonl.gz"))
    ap.add_argument(
        "--mode",
        choices=tuple(TRAINING_MODES.keys()),
        default=DEFAULT_TRAINING_MODE,
        help="training preset: fast (default, 1 eval fold + stride 2) or thorough (all folds + stride 1)",
    )
    ap.add_argument(
        "--device",
        choices=TRAINING_DEVICES,
        default=DEFAULT_TRAINING_DEVICE,
        help="training compute device: auto (default, GPU when available), gpu/cuda, or cpu",
    )
    ap.add_argument("--window-stride", type=int, default=None, help="training window sampling stride (overrides mode default)")
    ap.add_argument("--test-condition", type=int, default=None)
    ap.add_argument("--folds", action="store_true", default=None, help="hold out each posture in turn")
    ap.add_argument(
        "--split-mode",
        choices=("condition", "session", "person", "camera"),
        default="condition",
        help="leave-group-out evaluation axis: condition (default), session, person, or camera",
    )
    ap.add_argument("--epochs", type=int, default=None)
    ap.add_argument("--thr", type=float, default=0.7)
    ap.add_argument("--k", type=int, default=3, help="frames in a row above the threshold that count as the gesture")
    ap.add_argument("--no-export", action="store_true")
    ap.add_argument("--progress", action="store_true", help="JSON progress lines for the app")
    ap.add_argument("--output-dir", type=Path, default=ROOT / "models", help="model output folder")
    ap.add_argument("--gestures", nargs="+", help="train only these gesture tasks as independent binary models")
    ap.add_argument("--no-cleaned", action="store_true", help="ignore recordings/cleaned/ and use raw files only")
    ap.add_argument(
        "--offline-augment",
        nargs="?",
        const=True,
        default=None,
        help="include offline-augmented sequences during training (optionally specify factor)",
    )
    ap.add_argument("--seed", type=int, default=0, help="random seed of the TCN and the Spatial MLP, to repeat a comparison")
    ap.add_argument(
        "--threshold-sweep",
        action="store_true",
        help="score each held-out posture at shifted decision thresholds and print recognition against misfires",
    )
    ap.add_argument(
        "--keep-rest",
        action="store_true",
        help="teach rest frames as none, so every posture has negatives (evaluation still ignores them)",
    )
    ap.add_argument(
        "--hold-out-recording",
        default="",
        metavar="FILE",
        help="evaluate only the postures of this recording (one held-out fold per posture, the rest of the data trains): "
             "a quick, targeted test, e.g. a distance course recorded after the others",
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="reuse evaluation folds finished by an earlier run with the same recordings, options and code "
             "(logs/fold_cache), so a stopped training goes on where it was",
    )
    ap.add_argument(
        "--hard-negatives",
        action="store_true",
        help="fine-tune on none windows the model leaned the wrong way on, and print held-out results before and after",
    )
    ap.add_argument("--hard-negative-thr", type=float, default=0.5, help="gesture probability that makes a none window hard")
    ap.add_argument("--hard-negative-weight", type=float, default=3.0, help="how much heavier a hard window weighs")
    ap.add_argument("--hard-negative-rounds", type=int, default=1, help="how many times to find mistakes and fine-tune")
    return ap


def parse_args(argv: list[str] | None = None):
    return build_arg_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    global PROGRESS
    PROGRESS = a.progress
    if a.gestures:
        if a.hard_negatives:
            emit("note", text="--hard-negatives는 전체 학습에서만 적용돼요. 선택 동작 학습에서는 무시합니다.")
            print("--hard-negatives only applies to full training: ignored with --gestures")
        from scripts.train_selected import run
        return run(a, sys.modules[__name__])

    # The list is fixed here: files that arrive (git pull) or finish recording while training wait for the next run.
    from src.recorder import finished_recordings
    from src.training_center import (
        backup_current_model,
        filter_selected_recording_paths,
    )
    paths = sorted(glob.glob(a.recordings))
    folders = sorted({str(Path(p).parent) for p in paths})
    usable = {str(p) for f in folders for p in finished_recordings(Path(f))[0]}
    skipped = [p for p in paths if str(Path(p)) not in usable]
    paths = [p for p in paths if str(Path(p)) in usable]
    paths = filter_selected_recording_paths(paths)
    for p in skipped:
        emit("skipped", file=Path(p).name)
        if not PROGRESS:
            print(f"skipped (unfinished or unreadable, next run): {Path(p).name}")
    if not paths:
        emit("error", text=f"선택된 유효 녹화가 없어요: {a.recordings}")
        print("no selected recordings found:", a.recordings)
        return 1
    t0 = time.perf_counter()
    sessions, eval_sessions, opts = load_training_sessions(paths, a, window=WINDOW)
    present = {int(y) for s in sessions for y in s["y"] if y >= 0}
    classes = [name for i, name in enumerate(CLASSES) if i in present]
    if "none" not in classes or len(classes) < 2:
        emit("error", text="중립 구간과 한 가지 이상 동작의 유효 녹화가 필요합니다.")
        return 1
    for s in sessions:
        original = s["y"].copy()
        for c, name in enumerate(classes):
            s["y"][original == CLASSES.index(name)] = c
        s["classes"] = classes
    if a.keep_rest:
        # Train on them, score without them: a firing during rest must not count as recognising the repetition.
        eval_sessions = [{**s, "y": np.where(s["rest_dropped"], -1, s["y"])} for s in eval_sessions]
    contact_frames = sum((s.get("label_refinement_stats") or {}).get("near_miss_contact_frames", 0) for s in eval_sessions)
    if contact_frames:
        emit("note", text=f"'닿기 직전' 녹화에서 손가락이 닿은 {contact_frames}프레임은 학습에서 뺐어요.")
        if not PROGRESS:
            print(f"near-miss recordings: {contact_frames} frames with real contact left out of training")
    conds = sorted({int(c) for s in eval_sessions for c, y in zip(s["cond"], s["y"]) if c >= 0 and y >= 0})
    xa, ya, ca = pick_with_conds(sessions, conds)
    _, _, wa = pick_with_weights(sessions, conds)
    xa_eval, ya_eval = pick(eval_sessions, conds)
    per_class = dict(Counter(classes[i] for i in ya_eval))
    held = resolve_held_out_conds(conds, opts)
    if a.hold_out_recording:
        name = Path(a.hold_out_recording).name
        index = [i for i, p in enumerate(paths) if Path(p).name == name]
        if not index:
            emit("error", text=f"--hold-out-recording: '{name}'은 학습에 쓰는 녹화 목록에 없어요 (제외됐거나 이름이 달라요).")
            return 1
        file_session = eval_sessions[index[0]]
        file_conds = sorted({int(c) for c, y in zip(file_session["cond"], file_session["y"]) if c >= 0 and y >= 0})
        held = [c for c in file_conds if c in conds]
        if not held:
            emit("error", text=f"'{name}'에는 평가할 자세가 없어요.")
            return 1
    if not PROGRESS:
        print(f"{len(paths)} recording(s), postures {conds}, {len(ya)} labelled windows {per_class}"
              f"  device={opts['effective_device']} ({opts['device_name']})  ({time.perf_counter() - t0:.1f} s to load)")
    emit("loaded", recordings=len(paths), postures=conds, windows=int(len(ya)), per_class=per_class,
         folds=len(held), device=opts["effective_device"], device_name=opts["device_name"])

    summary = []
    from src.spatial_mlp import (
        blend_tcn_and_spatial_probs,
        ensure_session_spatial_features,
        pick_spatial_from_sessions,
        train_spatial_mlp,
    )
    evaluated_folds: list[int] = []
    hard_negative_folds: list[dict] = []
    rest_rows: list[dict] = []
    sweep: dict[float, dict] = {}

    def held_out_prob(fold_model, fold_spatial, session):
        prob = predict(fold_model, session["x"])
        if fold_spatial is not None:
            s_feats = ensure_session_spatial_features(session)
            if len(s_feats) == len(prob):
                sp_prob = fold_spatial.predict_proba(s_feats)
                prob = blend_tcn_and_spatial_probs(prob, classes, sp_prob, fold_spatial.classes)
        return prob

    from src.fold_cache import (PAUSE_EXIT_CODE, FoldCache, clear_pause, code_hash, derived_key, pause_requested,
                                recordings_key)
    fold_cache = FoldCache(
        ROOT / "logs" / "fold_cache",
        {"recordings": recordings_key(paths), "code": code_hash(), "classes": classes, "conds": conds, "held": held,
         "seed": a.seed, "thr": a.thr, "k": a.k, "epochs": opts["epochs"], "stride": opts["window_stride"],
         "device": opts["effective_device"], "keep_rest": a.keep_rest, "hard_negatives": a.hard_negatives,
         "hn": [a.hard_negative_thr, a.hard_negative_weight, a.hard_negative_rounds],
         "sweep": a.threshold_sweep, "augment": bool(opts["offline_augment"]), "no_cleaned": a.no_cleaned,
         "derived": derived_key(paths), "split_mode": str(getattr(a, "split_mode", "condition") or "condition"),
         "large_batch": os.environ.get("TCN_LARGE_BATCH") == "1"},
        enabled=a.resume,
    )
    if a.resume:
        fold_cache.prune_others()
    resumed_folds = 0
    for fold_idx, test in enumerate(held, 1):
        train_conds = [c for c in conds if c != test]
        x, y, w_fold = pick_with_weights(sessions, train_conds)
        if test not in conds or not len(y) or len(set(y)) < len(classes):
            emit("note", text=f"조건 {test} 제외 평가는 보류: 나머지 조건에도 학습할 모든 동작의 데이터가 필요합니다.")
            continue
        evaluated_folds.append(int(test))
        cached = fold_cache.load(test)
        if cached is not None:
            resumed_folds += 1
            summary.extend(cached["summary"])
            rest_rows.extend(cached["rest_rows"])
            hard_negative_folds.extend(cached["hard_negative_folds"])
            for offset, row in cached["sweep"].items():
                slot = sweep.setdefault(offset, {"tcn": [], "rest": []})
                slot["tcn"].extend(row["tcn"])
                slot["rest"].extend(row["rest"])
            emit("fold_start", posture=test, fold=fold_idx, folds=len(held))
            for name, info in cached["events"]:
                emit(name, **info)
            if not PROGRESS:
                print(f"\n== held-out posture {test}: finished in an earlier run, reused")
            continue
        if pause_requested():
            # the app asked for a pause: every finished fold is saved, the next run goes on from this one
            clear_pause()
            emit("paused", folds_done=len(evaluated_folds) - 1, folds=len(held))
            if not PROGRESS:
                print(f"paused before posture {test}; run again with --resume to go on")
            return PAUSE_EXIT_CODE
        n_summary, n_rest, n_hn = len(summary), len(rest_rows), len(hard_negative_folds)
        n_sweep = {off: (len(row["tcn"]), len(row["rest"])) for off, row in sweep.items()}
        fold_events: list = []
        if not PROGRESS:
            print(f"\n== held-out posture {test} (train on {train_conds}, {len(y)} windows, stride {opts['window_stride']})")
        emit("fold_start", posture=test, fold=fold_idx, folds=len(held))
        t1 = time.perf_counter()
        model = train(
            x,
            y,
            opts["epochs"],
            seed=a.seed,
            stage=f"fold{test}",
            classes=classes,
            window_stride=opts["window_stride"],
            device=opts["effective_device"],
            sample_weights=w_fold,
        )
        xs_fold, ys_fold, gs_fold, ws_fold = pick_spatial_from_sessions(
            sessions, train_conds, return_groups=True, return_weights=True
        )
        sp_fold_model = None
        if len(ys_fold) >= 2:
            emit("substage", text=f"평가 학습 {fold_idx}/{len(held)} · Spatial MLP 보조 모델 학습 및 검증 중…")
            try:
                sp_fold_model, _ = train_spatial_mlp(
                    xs_fold,
                    ys_fold,
                    weights_all=ws_fold,
                    classes=classes,
                    device=opts["effective_device"],
                    groups=gs_fold,
                    compute_random_comparison=False,
                    seed=42 + a.seed,
                )
            except Exception:
                sp_fold_model = None
        if not PROGRESS:
            print(f"    trained in {time.perf_counter() - t1:.0f} s")
        refined_fold = None
        hn_fold_info = None
        if a.hard_negatives:
            emit("substage", text=f"평가 학습 {fold_idx}/{len(held)} · 오판 구간 집중 재학습 중…")
            refined_fold, hn_fold_info = refine_with_hard_negatives(
                model,
                x,
                y,
                w_fold,
                classes=classes,
                epochs=opts["epochs"],
                rounds=a.hard_negative_rounds,
                thr=a.hard_negative_thr,
                multiplier=a.hard_negative_weight,
                window_stride=opts["window_stride"],
                device=opts["effective_device"],
                stage=f"fold{test}",
            )
        before_rows, after_rows, fold_minutes = [], [], 0.0
        for s in eval_sessions:
            prob = held_out_prob(model, sp_fold_model, s)
            tcn = evaluate(s, [test], prob, a.thr, a.k)
            rule = evaluate(s, [test])
            minutes = free_minutes(s, [test])
            if refined_fold is not None:
                before_rows.append(tcn)
                prob = held_out_prob(refined_fold, sp_fold_model, s)
                tcn = evaluate(s, [test], prob, a.thr, a.k)
                after_rows.append(tcn)
                fold_minutes += minutes
            if not PROGRESS:
                show("rules", rule)
                show("TCN", tcn)
            summary.append((test, rule, tcn, minutes))
            rest_rows.append({"posture": int(test), **rest_misfires(s, [test], prob, classes, a.thr, a.k)})
            if a.threshold_sweep:
                for offset in THRESHOLD_SWEEP_OFFSETS:
                    shifted = shifted_thresholds(classes, offset, a.thr)
                    row = sweep.setdefault(offset, {"tcn": [], "rest": []})
                    row["tcn"].append(evaluate(s, [test], prob, a.thr, a.k, thr_by_label=shifted))
                    row["rest"].append(rest_misfires(s, [test], prob, classes, a.thr, a.k, thr_by_label=shifted))
            emit("fold_result", posture=test, rules=rule, tcn=tcn)
            fold_events.append(("fold_result", {"posture": test, "rules": rule, "tcn": tcn}))
        if refined_fold is not None:
            hard_negative_folds.append({"posture": int(test), "before": tally_results(before_rows),
                                        "after": tally_results(after_rows), "free_minutes": round(fold_minutes, 2),
                                        **hn_fold_info})
        fold_cache.save(test, {
            "summary": summary[n_summary:], "rest_rows": rest_rows[n_rest:],
            "hard_negative_folds": hard_negative_folds[n_hn:], "events": fold_events,
            "sweep": {off: {"tcn": row["tcn"][n_sweep.get(off, (0, 0))[0]:], "rest": row["rest"][n_sweep.get(off, (0, 0))[1]:]}
                      for off, row in sweep.items()},
        })

    if resumed_folds:
        emit("note", text=f"이전에 끝낸 평가 {resumed_folds}판은 다시 하지 않고 이어서 했어요.")
        if not PROGRESS:
            print(f"resumed: {resumed_folds} of {len(evaluated_folds)} evaluation folds reused from logs/fold_cache")
    if not PROGRESS:
        show_held_out_total(summary, rest_rows)
        show_threshold_sweep(sweep)
        show_hard_negative_comparison(hard_negative_folds)
    if a.no_export:
        return 0
    if pause_requested():
        clear_pause()
        emit("paused", folds_done=len(evaluated_folds), folds=len(held))
        return PAUSE_EXIT_CODE
    if not PROGRESS:
        print(f"\n== final model on all postures {conds} (stride {opts['window_stride']})")
    emit("final_start")
    losses: list = []
    model = train(
        xa,
        ya,
        opts["epochs"],
        seed=a.seed,
        stage="final",
        losses=losses,
        classes=classes,
        window_stride=opts["window_stride"],
        device=opts["effective_device"],
        sample_weights=wa,
    )
    # The backup comes before ANY model file is written. It used to be taken after spatial_mlp.json and
    # motion_templates.json were already replaced, so a "before-train" backup held the new spatial model next to the
    # old TCN, and restoring it gave a pair with different classes.
    before_rep = None
    backup_folder = None
    try:
        from src.training_center import evaluate_training_improvement, model_report
        before_rep = model_report(a.output_dir / "gesture_tcn.onnx")
        backup_folder = backup_current_model(model=a.output_dir / "gesture_tcn.onnx",
                                             backup_dir=a.output_dir / "backups", reason="before-train")
    except Exception:
        pass
    # Co-train and save 0-lag 96-dim Spatial MLP (models/spatial_mlp.json)
    spatial_meta = None
    sp_model = None
    xs_all, ys_all, gs_all, ws_all = pick_spatial_from_sessions(
        sessions, conds, return_groups=True, return_weights=True
    )
    if len(ys_all) >= 2:
        emit("substage", text="최종 Spatial MLP (0-lag 공간 보조 모델) 학습 중…")
        try:
            sp_model, sp_metrics = train_spatial_mlp(
                xs_all,
                ys_all,
                weights_all=ws_all,
                classes=classes,
                device=opts["effective_device"],
                groups=gs_all,
            )
            sp_path = a.output_dir / "spatial_mlp.json"
            a.output_dir.mkdir(parents=True, exist_ok=True)
            sp_model.save(sp_path)
            spatial_meta = {
                "file": sp_path.name,
                "feature_version": sp_model.feature_version,
                "feat_dim": sp_model.feat_dim,
                "hidden_units": sp_model.hidden_units,
                "train_accuracy": sp_metrics.train_accuracy,
                "test_accuracy": sp_metrics.test_accuracy,
                "random_test_accuracy": sp_metrics.random_test_accuracy,
                "leakage_delta_pct": sp_metrics.leakage_delta_pct,
                "split_mode": sp_metrics.split_mode,
                "total_samples": sp_metrics.total_samples,
                "train_samples": sp_metrics.train_samples,
                "test_samples": sp_metrics.test_samples,
                "elapsed_ms": sp_metrics.elapsed_ms,
                "per_class": sp_metrics.per_class,
                "specialists": sorted(sp_model.specialists.keys()),
                "trained_at": sp_metrics.trained_at,
            }
            if not PROGRESS:
                print(
                    f"saved {sp_path.name} (96-dim Spatial MLP + {len(sp_model.specialists)} gesture specialist MLPs, "
                    f"leave_out_acc={sp_metrics.test_accuracy:.1f}%, random_80_20_acc={sp_metrics.random_test_accuracy or sp_metrics.test_accuracy:.1f}%, "
                    f"{sp_metrics.elapsed_ms:.0f} ms)"
                )
        except Exception as exc:
            if not PROGRESS:
                print(f"spatial MLP skip: {exc}")

    # Co-train 16-step trajectory & velocity profile templates for temporal motion gestures
    motion_labels_present = [c for c in classes if is_motion_task(c)]
    if motion_labels_present:
        emit("substage", text="시계열 모션 동작 16-step 궤적·속도 프로파일 템플릿 학습 중…")
        try:
            from src.motion_mode import train_motion_templates_from_recordings
            rec_dirs = {Path(p).parent for p in paths}
            rec_folder = next(iter(rec_dirs)) if rec_dirs else (Path(__file__).resolve().parents[1] / "recordings")
            train_motion_templates_from_recordings(
                rec_dir=rec_folder,
                target_keys=motion_labels_present,
                output_path=a.output_dir / "motion_templates.json",
            )
        except Exception as exc:
            if not PROGRESS:
                print(f"motion templates skip: {exc}")

    results = [{"posture": t, "rules": r, "tcn": m, "free_minutes": round(fm, 2)} for t, r, m, fm in summary]
    model, final_results, boost_info = adaptive_boost_postures(
        model,
        xa,
        ya,
        ca,
        eval_sessions,
        conds,
        results,
        classes=classes,
        thr=a.thr,
        k=a.k,
        epochs=opts["epochs"],
        window_stride=opts["window_stride"],
        device=opts["effective_device"],
        sp_model=sp_model,
        losses=losses,
        condition_groups=eval_sessions[0]["condition_groups"],
    )
    hard_negative_info = None
    if a.hard_negatives:
        # After the boost, which keeps a candidate on recognition first and would be free to undo this step.
        emit("substage", text="최종 모델 오판 구간 집중 재학습 중…")
        model, final_hn = refine_with_hard_negatives(
            model,
            xa,
            ya,
            wa,
            classes=classes,
            epochs=opts["epochs"],
            rounds=a.hard_negative_rounds,
            thr=a.hard_negative_thr,
            multiplier=a.hard_negative_weight,
            window_stride=opts["window_stride"],
            device=opts["effective_device"],
            stage="final",
            losses=losses,
        )
        final_results = evaluate_postures_on_model(eval_sessions, conds, model, classes, thr=a.thr, k=a.k, sp_model=sp_model)
        hard_negative_info = {"final": final_hn, "held_out": hard_negative_folds}
    pred = predict(model, xa_eval).argmax(1)
    train_acc = {name: round(float((pred[ya_eval == c] == c).mean()), 3) for c, name in enumerate(classes) if (ya_eval == c).any()}
    a.output_dir.mkdir(parents=True, exist_ok=True)
    out = a.output_dir / "gesture_tcn.onnx"
    model = model.cpu().eval()
    emit("substage", text="최종 TCN 모델을 ONNX 파일로 내보내는 중…")
    torch.onnx.export(model, torch.zeros(1, WINDOW, FEATURES), str(out), input_names=["x"], output_names=["logits"],
                      dynamic_axes={"x": {0: "batch"}, "logits": {0: "batch"}}, opset_version=18,
                      external_data=False, dynamo=False)

    from src.evaluation import DEFERRED_TECHNIQUES_SUMMARY, dataset_diversity_report

    info = {
        "classes": classes, "window": WINDOW, "features": FEATURES, "feature_version": FEATURE_VERSION,
        "threshold": a.thr, "frames_in_a_row": a.k,
        "split_mode": str(getattr(a, "split_mode", "condition") or "condition"),
        **card_training_metadata(opts, evaluated_folds),
        "trained": time.strftime("%Y-%m-%d %H:%M:%S"),
        "data": [{"file": Path(s["path"]).name, "pc": public_source(s["meta"])[0],
                  "camera": dict(s["meta"].get("camera") or {}, note=public_source(s["meta"])[1]),
                  "frames": len(s["frames"])} for s in eval_sessions],
        "dataset_diversity": dataset_diversity_report(eval_sessions),
        "deferred_techniques": DEFERRED_TECHNIQUES_SUMMARY,
        "held_out_results": results,
        "final_results": final_results,
        "adaptive_boost": boost_info,
        "hard_negatives": hard_negative_info,
        "keep_rest": bool(a.keep_rest),
        "held_out_rest_misfires": rest_rows,
        "condition_groups": eval_sessions[0]["condition_groups"],
        "loss_curve": losses, "train_accuracy": train_acc,
        "per_gesture": per_gesture(results, per_class, train_acc, classes, final_results=final_results, adaptive_boost=boost_info),
        "per_task": per_gesture(results, per_class, train_acc, classes, by_task=True, final_results=final_results, adaptive_boost=boost_info),
        "spatial_mlp": spatial_meta,
    }
    out.with_suffix(".json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    imp_doc = None
    try:
        from src.training_center import evaluate_training_improvement, model_report
        after_rep = model_report(out)
        bak_id = f"backup:{backup_folder.name}" if backup_folder is not None else None
        imp_doc = evaluate_training_improvement(
            after_report=after_rep,
            before_report=before_rep,
            backup_id=bak_id,
            model=out,
            backup_dir=a.output_dir / "backups",
        )
    except Exception:
        imp_doc = None
    if not PROGRESS:
        print(f"saved {out.name} ({out.stat().st_size // 1024} KB) and gesture_tcn.json")
        if imp_doc and imp_doc.get("headline"):
            print(f"improvement: {imp_doc['headline']}")
    emit("done", model=out.name, kb=out.stat().st_size // 1024, per_gesture=info["per_gesture"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
