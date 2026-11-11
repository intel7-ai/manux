"""Real neural updates during use: forward in train mode, loss, backward, optimizer step, on a candidate copy of the model.

methods
  naive    the new batch only: the comparison that shows forgetting, never the default
  er       the new batch plus a replayed batch, cross-entropy on both (Experience Replay)
  derpp    Buzzega et al. 2020, Algorithm 2: cross-entropy on the new batch + alpha * squared distance to logits stored in the buffer
           + beta * cross-entropy on a second, separately drawn buffer batch

Differences from the paper, on purpose: the head grows when a class is added, so the stored logits are compared only for the classes that
existed when they were saved; and the optimizer is Adam, not SGD. Those are this project's variant of DER++.
"""
from __future__ import annotations

import copy
import time
from typing import Any, Dict, List, Optional

import numpy as np
import torch
from torch import nn

from src.online.models import EncoderBase
from src.online.replay import ReservoirBuffer

METHODS = ("naive", "er", "derpp")
EVENT_HEAD = "head_updated"
EVENT_ENCODER = "encoder_updated"


def expanded(model: EncoderBase, classes: int) -> EncoderBase:
    """A copy with `classes` outputs. Existing classes keep their weights exactly."""
    out = copy.deepcopy(model)
    old = out.head
    if classes < old.out_features:
        raise ValueError("a head does not shrink")
    if classes == old.out_features:
        return out
    head = nn.Linear(old.in_features, classes).to(old.weight.device)
    with torch.no_grad():
        head.weight[:old.out_features] = old.weight
        head.bias[:old.out_features] = old.bias
    out.head = head
    return out


def parameter_change(before: Dict[str, torch.Tensor], model: nn.Module) -> Dict[str, float]:
    """Sum of absolute weight changes, kept apart for the classifier head and the encoder. A prototype change would show as zero here."""
    head = encoder = 0.0
    for name, p in model.named_parameters():
        if name not in before or before[name].shape != p.shape:
            continue
        delta = float((p.detach() - before[name]).abs().sum())
        if name.startswith("head."):
            head += delta
        else:
            encoder += delta
    return {"head": head, "encoder": encoder}


class OnlineLearner:
    """Owns a candidate model. The model in use is never touched: `candidate` is a deep copy."""

    def __init__(self, production: EncoderBase, *, method: str = "er", buffer: Optional[ReservoirBuffer] = None, lr: float = 5e-4, encoder_lr: Optional[float] = None,
                 replay_batch: int = 32, alpha: float = .5, beta: float = .5, device: str = "cpu", seed: int = 0, train_encoder: bool = True,
                 augment: Any = None) -> None:
        if method not in METHODS:
            raise ValueError(f"unknown method: {method}")
        if method != "naive" and buffer is None:
            raise ValueError(f"{method} needs a replay buffer")
        self.method, self.buffer, self.lr, self.encoder_lr = method, buffer, lr, lr if encoder_lr is None else encoder_lr
        self.replay_batch, self.alpha, self.beta = replay_batch, alpha, beta
        self.device, self.train_encoder, self.augment = torch.device(device), train_encoder, augment
        torch.manual_seed(seed)
        self.candidate = copy.deepcopy(production).to(self.device)
        self.updates = 0
        self.skipped = 0
        self.events: List[str] = []
        self._make_optimizer()

    def _make_optimizer(self) -> None:
        groups = [{"params": self.candidate.head.parameters(), "lr": self.lr}]
        encoder = self.candidate.encoder_parameters()
        for p in encoder:
            p.requires_grad_(self.train_encoder)
        if self.train_encoder and encoder:
            groups.append({"params": encoder, "lr": self.encoder_lr})
        self.optimizer = torch.optim.Adam(groups)

    def add_class(self, classes: int) -> None:
        """Grow the head. Existing weights stay; the optimizer starts again because the parameters are new objects."""
        self.candidate = expanded(self.candidate, classes).to(self.device)
        self._make_optimizer()

    def _tensor(self, x: np.ndarray) -> torch.Tensor:
        return torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32)).to(self.device)

    def update(self, x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
        """One optimizer step on a batch of confirmed examples. A batch that gives a non-finite loss changes nothing."""
        t0 = time.perf_counter()
        model = self.candidate
        model.train()
        xt, yt = self._tensor(x), torch.from_numpy(np.asarray(y, dtype=np.int64)).to(self.device)
        before = {n: p.detach().clone() for n, p in model.named_parameters()}
        inputs = self.augment(xt) if self.augment is not None else xt
        logits = model(inputs)
        loss = nn.functional.cross_entropy(logits, yt)
        parts = {"new": float(loss.detach())}
        if self.buffer is not None and self.buffer.size > 0 and self.method != "naive":
            if self.method == "er":
                rx, ry, _ = self.buffer.sample(self.replay_batch)
                replay = nn.functional.cross_entropy(model(self._tensor(rx)), torch.from_numpy(ry).to(self.device))
                loss = loss + replay
                parts["replay"] = float(replay.detach())
            else:
                dx, _, dz = self.buffer.sample(self.replay_batch)
                current = model(self._tensor(dx))
                stored = torch.from_numpy(dz).to(self.device)
                width = min(current.shape[1], stored.shape[1])     # the head may have grown since the buffer was last written
                stored, compared = stored[:, :width], current[:, :width]
                known = ~torch.isnan(stored)
                dark = ((compared[known] - stored[known]) ** 2).mean() if known.any() else current.sum() * 0.0
                cx, cy, _ = self.buffer.sample(self.replay_batch)       # the second, separate draw of Algorithm 2
                ce = nn.functional.cross_entropy(model(self._tensor(cx)), torch.from_numpy(cy).to(self.device))
                loss = loss + self.alpha * dark + self.beta * ce
                parts["dark"], parts["replay"] = float(dark.detach()), float(ce.detach())
        if not torch.isfinite(loss):
            self.skipped += 1
            return {"applied": False, "reason": "non_finite_loss", "loss": float("nan"), "seconds": time.perf_counter() - t0}
        self.optimizer.zero_grad(set_to_none=True)
        loss.backward()
        self.optimizer.step()
        self.updates += 1
        change = parameter_change(before, model)
        events = [e for e, v in ((EVENT_HEAD, change["head"]), (EVENT_ENCODER, change["encoder"])) if v > 0]
        self.events.extend(e for e in events if e not in self.events)
        if self.buffer is not None:
            self.buffer.add(np.asarray(x, dtype=np.float32), np.asarray(y), logits.detach().cpu().numpy())
        return {"applied": True, "loss": float(loss.detach()), "parts": parts, "head_change": change["head"], "encoder_change": change["encoder"],
                "events": events, "seconds": time.perf_counter() - t0}
