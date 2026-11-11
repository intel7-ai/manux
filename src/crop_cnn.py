"""A small CNN that classifies the hand picture (src/crop_recorder.py) instead of its joints (docs/far-hand-loss.md, "손 조각 CNN").

Input: the last FRAMES pictures of the hand (every STEP-th hand frame) stacked as the channels of one 48 x 48 image, so the net
sees the fingers closing as well as the shape. Output: a class per frame: none, or the pinch asked for. The data come from guided
recordings: the `do` frames are the asked pinch, `prep` and `rest` frames are "none".
"""
from __future__ import annotations

import gzip
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from src.crop_recorder import SIZE, crops_path, load_crops
from src.gesture_catalog import BY_KEY

FRAMES = 6      # pictures stacked per decision
STEP = 2        # every 2nd hand frame (0.07 s at 30 fps): the stack covers about 0.33 s


class CropNet(nn.Module):
    """Three conv blocks like a digit classifier, then two linear layers."""

    def __init__(self, n_classes: int, frames: int = FRAMES, size: int = SIZE) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(frames, 16, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(16, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Flatten(), nn.Dropout(0.3),
            nn.Linear(64 * (size // 8) ** 2, 128), nn.ReLU(), nn.Linear(128, n_classes))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # light and contrast must not decide: every sample is standardised on its own
        x = (x - x.mean(dim=(1, 2, 3), keepdim=True)) / (x.std(dim=(1, 2, 3), keepdim=True) + 1e-3)
        return self.net(x)


# ----------------------------------------------------------------------------------------------------------- data
def read_frames(path: Path) -> List[Dict[str, Any]]:
    """The "frame" lines of a recording, in order (the n-th one is `frame_index` n of the pictures)."""
    frames = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") == "frame":
                frames.append(r)
    return frames


def stack_indexes(n: int, frames: int = FRAMES, step: int = STEP) -> np.ndarray:
    """(n, frames) positions of the pictures that make the stack of picture i: i, i-step, ... (the first picture repeats)."""
    base = np.arange(n)[:, None] - step * np.arange(frames)[None, :]
    return np.maximum(base, 0)[:, ::-1]          # oldest first


def build_samples(recordings: Sequence[Path], frames: int = FRAMES, step: int = STEP) -> Dict[str, Any]:
    """Pictures and labels of recordings that have a crops file. Frames of the asked pinch are labelled with the pinch, prep and rest
    frames "none", anything else (waiting, paused) is left out."""
    pics, idx, labels, cond, tokens, times, pic_sizes = [], [], [], [], [], [], []
    offset = 0
    names: Dict[str, str] = {}
    for path in recordings:
        cp = crops_path(path)
        if not cp.is_file():
            continue
        frame_index, crops = load_crops(cp)
        recorded = read_frames(path)
        order = stack_indexes(len(crops), frames, step) + offset
        pics.append(crops)
        for fi in frame_index:
            lm = recorded[int(fi)].get("lm") if fi < len(recorded) else None
            pic_sizes.append(_hand_size(lm) if lm else 0.0)
        for k, fi in enumerate(frame_index):
            if fi >= len(recorded):
                continue
            lab = recorded[int(fi)].get("label") or {}
            task = BY_KEY.get(lab.get("task"))
            phase = lab.get("phase")
            if task is None or task.free:
                continue
            if phase == "do":
                name = task.label
            elif phase in ("prep", "rest"):
                name = "none"
            else:
                continue
            idx.append(order[k])
            labels.append(name)
            cond.append(str(lab.get("condition_key") or lab.get("condition") or path.name))
            tokens.append((path.name, lab.get("set"), lab.get("rep"), phase == "do"))
            times.append(float(recorded[int(fi)]["t"]))
        offset += len(crops)
        try:
            from src.pinch_report import condition_names, read_recording
            names.update(condition_names(read_recording(path)[0]))
        except Exception:
            pass
    if not pics:
        raise ValueError("손 조각이 저장된 녹화가 없어요 (설정 rec_save_crops를 켜고 안내 녹화를 하세요).")
    classes = ["none"] + sorted({x for x in labels if x != "none"})
    return {"pics": np.concatenate(pics), "idx": np.asarray(idx), "y": np.asarray([classes.index(x) for x in labels]),
            "classes": classes, "cond": np.asarray(cond), "tokens": tokens, "t": np.asarray(times), "names": names,
            "pic_size": np.asarray(pic_sizes, dtype=np.float32)}


def _hand_size(lm) -> float:
    """Wrist to the farthest knuckle as a share of the frame width (the same measure the reports use)."""
    from src.pinch_report import hand_size
    return hand_size(lm)


def adaptive_crop(pics: np.ndarray, sizes: np.ndarray, big: float, small_side: int = 36, out: int = 40) -> np.ndarray:
    """A small (far) hand is cut to its middle `small_side` pixels, a big (near) one is kept whole, and both are resized to
    out x out so one net can take them. `sizes` is the hand size of each picture (data["pic_size"]) (hand_size); `big` the size from which a hand
    counts as near. Far hands lose the background, near hands keep their fingertips."""
    import cv2
    full = pics.shape[-1]
    lo = (full - small_side) // 2
    result = np.empty((len(pics), out, out), dtype=np.uint8)
    for i, pic in enumerate(pics):
        part = pic if sizes[i] >= big else pic[lo:lo + small_side, lo:lo + small_side]
        result[i] = cv2.resize(part, (out, out), interpolation=cv2.INTER_CUBIC if part.shape[0] < out else cv2.INTER_AREA)
    return result


def centre_crop(pics: np.ndarray, side: int) -> np.ndarray:
    """Only the middle `side` x `side` pixels of every picture (0 or the full size keeps them whole). The pictures are saved wide
    (the box of the joints x 1.35) so the background can be cut away here instead of at recording time."""
    full = pics.shape[-1]
    if side <= 0 or side >= full:
        return pics
    lo = (full - side) // 2
    return np.ascontiguousarray(pics[:, lo:lo + side, lo:lo + side])


# --------------------------------------------------------------------------------------------------------- training
def _batch(pics: np.ndarray, idx: np.ndarray, rows: np.ndarray, device) -> torch.Tensor:
    return torch.from_numpy(pics[idx[rows]].astype(np.float32) / 255.0).to(device)


def augment(x: torch.Tensor, shift: float = 0.10, scale: float = 0.10, rotate_deg: float = 10.0) -> torch.Tensor:
    """The same small shift, zoom and turn for every picture of a stack (one random affine per sample), plus a little noise."""
    b = x.shape[0]
    ang = (torch.rand(b, device=x.device) * 2 - 1) * math.radians(rotate_deg)
    sc = 1 + (torch.rand(b, device=x.device) * 2 - 1) * scale
    tx, ty = ((torch.rand(b, device=x.device) * 2 - 1) * shift for _ in range(2))
    theta = torch.stack([torch.stack([torch.cos(ang) / sc, -torch.sin(ang) / sc, tx], dim=1),
                         torch.stack([torch.sin(ang) / sc, torch.cos(ang) / sc, ty], dim=1)], dim=1)
    grid = F.affine_grid(theta, list(x.shape), align_corners=False)
    out = F.grid_sample(x, grid, padding_mode="border", align_corners=False)
    return out + 0.02 * torch.randn_like(out)


def train_cnn(pics: np.ndarray, idx: np.ndarray, y: np.ndarray, n_classes: int, epochs: int = 12, device: str = "cpu",
              seed: int = 0, batch_size: int = 256) -> CropNet:
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    model = CropNet(n_classes, frames=idx.shape[1], size=pics.shape[-1]).to(device)
    counts = np.bincount(y, minlength=n_classes).astype(np.float64)
    weight = torch.tensor((counts.sum() / np.maximum(counts, 1)) ** 0.5, dtype=torch.float32, device=device)
    loss_fn = nn.CrossEntropyLoss(weight=weight / weight.mean())
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    yt = torch.from_numpy(y).long().to(device)
    for _ in range(epochs):
        model.train()
        order = rng.permutation(len(y))
        for i in range(0, len(order), batch_size):
            rows = order[i:i + batch_size]
            x = augment(_batch(pics, idx, rows, device))
            opt.zero_grad()
            loss_fn(model(x), yt[rows]).backward()
            opt.step()
    return model.eval()


@torch.no_grad()
def predict_cnn(model: CropNet, pics: np.ndarray, idx: np.ndarray, rows: np.ndarray, device: str = "cpu") -> np.ndarray:
    out = []
    for i in range(0, len(rows), 512):
        out.append(torch.softmax(model(_batch(pics, idx, rows[i:i + 512], device)), dim=1).cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, model.net[-1].out_features), dtype=np.float32)


# -------------------------------------------------------------------------------------------------------- evaluation
def auc(scores: np.ndarray, positive: np.ndarray) -> float:
    """Area under the ROC curve (how often a positive scores above a negative; ties count half)."""
    pos, neg = scores[positive], scores[~positive]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    ranks = np.argsort(np.argsort(np.concatenate([pos, neg]))) + 1.0
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def run_fires(prob: np.ndarray, thr: float, k: int, tokens: Sequence) -> List[int]:
    """Sample positions where `k` samples in a row reached `thr` (the count restarts when the repetition changes)."""
    fired, run, prev = [], 0, None
    for i, p in enumerate(prob):
        if tokens[i] != prev:
            run, prev = 0, tokens[i]
        run = run + 1 if p >= thr else 0
        if run == k:
            fired.append(i)
    return fired


def evaluate_condition(prob_class: np.ndarray, is_do: np.ndarray, rep_of: Sequence, t: np.ndarray, tokens: Sequence,
                       thr: float = 0.7, k: int = 4) -> Dict[str, Any]:
    """For one held-out posture and one class: reps recognised, fires away from the asked pinch (per minute), AUC."""
    fired = set(run_fires(prob_class, thr, k, tokens))
    reps: Dict[Any, List[int]] = {}
    for i, (do, rep) in enumerate(zip(is_do, rep_of)):
        if do:
            reps.setdefault(rep, []).append(i)
    hit = sum(1 for rows in reps.values() if any(i in fired for i in rows))
    do_times = t[is_do]
    quiet = np.array([i for i in range(len(t)) if not is_do[i] and (len(do_times) == 0 or np.min(np.abs(do_times - t[i])) > 0.7)], dtype=int)
    minutes = sum(t[i + 1] - t[i] for i in quiet if i + 1 < len(t) and 0 < t[i + 1] - t[i] < 0.5) / 60
    far = [i for i in fired if i in set(quiet.tolist())]
    return {"reps": len(reps), "hit": hit, "false_fires": len(far), "quiet_minutes": minutes,
            "false_per_min": len(far) / minutes if minutes > 0 else float("nan"), "auc": auc(prob_class, is_do)}
