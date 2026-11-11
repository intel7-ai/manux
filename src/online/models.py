"""Encoders for online learning: each maps a window (batch, time, features) to an embedding, and a linear head to classes.

The app's running model (scripts/train_tcn.py TCN) is untouched. Which of these replaces it is decided by measurement
(docs/online-learning-phase2.md), not assumed: TCN, GRU and a one-frame MLP are compared on the same splits.
"""
from __future__ import annotations

from typing import Sequence

import torch
from torch import nn

from src.tcn_features import FEATURES


class EncoderBase(nn.Module):
    def __init__(self, embedding: int, n_classes: int) -> None:
        super().__init__()
        self.embedding = embedding
        self.head = nn.Linear(embedding, n_classes)

    def embed(self, x: torch.Tensor) -> torch.Tensor:
        raise NotImplementedError

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.head(self.embed(x))

    def encoder_parameters(self):
        return [p for name, p in self.named_parameters() if not name.startswith("head.")]


class CausalTCN(EncoderBase):
    """Dilated convolutions padded on the left only, so a time step never sees a later one (Bai et al. 2018 for the TCN idea)."""

    def __init__(self, n_classes: int, n_in: int = FEATURES, channels: int = 64, embedding: int = 64,
                 dilations: Sequence[int] = (1, 2, 4), kernel: int = 3, dropout: float = .2) -> None:
        super().__init__(embedding, n_classes)
        layers, c = [], n_in
        for d in dilations:
            layers += [nn.ConstantPad1d(((kernel - 1) * d, 0), 0.0), nn.Conv1d(c, channels, kernel, dilation=d), nn.ReLU(), nn.Dropout(dropout)]
            c = channels
        self.body = nn.Sequential(*layers)
        self.proj = nn.Linear(channels, embedding)
        self.receptive_field = 1 + (kernel - 1) * sum(dilations)

    def sequence(self, x: torch.Tensor) -> torch.Tensor:
        """(batch, channels, time): one output per time step."""
        return self.body(x.transpose(1, 2))

    def embed(self, x: torch.Tensor) -> torch.Tensor:
        return torch.relu(self.proj(self.sequence(x)[:, :, -1]))


class GRUEncoder(EncoderBase):
    def __init__(self, n_classes: int, n_in: int = FEATURES, hidden: int = 64, embedding: int = 64, layers: int = 1) -> None:
        super().__init__(embedding, n_classes)
        self.gru = nn.GRU(n_in, hidden, num_layers=layers, batch_first=True)
        self.proj = nn.Linear(hidden, embedding)

    def embed(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.gru(x)
        return torch.relu(self.proj(out[:, -1]))


class FrameMLP(EncoderBase):
    """Looks at the last frame only (its joints and their change since the frame before): the no-memory baseline."""

    def __init__(self, n_classes: int, n_in: int = FEATURES, hidden: int = 128, embedding: int = 64, dropout: float = .2) -> None:
        super().__init__(embedding, n_classes)
        self.net = nn.Sequential(nn.Linear(n_in, hidden), nn.ReLU(), nn.Dropout(dropout), nn.Linear(hidden, embedding), nn.ReLU())

    def embed(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x[:, -1])


def dilations_for(window: int) -> Sequence[int]:
    """The fewest doublings whose receptive field covers `window` frames with kernel 3 (15 -> 1,2,4; 31 -> 1,2,4,8; 63 -> ...,16)."""
    dilations, field = [1], 3
    while field < window:
        dilations.append(dilations[-1] * 2)
        field += 2 * dilations[-1]
    return tuple(dilations)


def make_model(kind: str, n_classes: int, window: int, embedding: int = 64) -> EncoderBase:
    if kind == "tcn":
        return CausalTCN(n_classes, embedding=embedding, dilations=dilations_for(window))
    if kind == "gru":
        return GRUEncoder(n_classes, embedding=embedding)
    if kind == "mlp":
        return FrameMLP(n_classes, embedding=embedding)
    raise ValueError(f"unknown model: {kind}")


def supervised_contrastive_loss(z: torch.Tensor, labels: torch.Tensor, temperature: float = .1) -> torch.Tensor:
    """L_out of Khosla et al. 2020 (equation 2) on one view per sample: for each anchor, the mean log-likelihood of its positives
    against all other samples. Anchors without a positive in the batch are left out; with none at all the loss is a differentiable 0."""
    z = nn.functional.normalize(z, dim=1)
    n = z.shape[0]
    if n < 2:
        return z.sum() * 0.0
    logits = z @ z.T / temperature
    self_mask = torch.eye(n, dtype=torch.bool, device=z.device)
    logits = logits - logits.masked_fill(self_mask, float("-inf")).max(dim=1, keepdim=True).values.detach()
    positives = (labels[:, None] == labels[None, :]) & ~self_mask
    counts = positives.sum(dim=1)
    has_positive = counts > 0
    if not has_positive.any():
        return z.sum() * 0.0
    log_prob = logits - torch.logsumexp(logits.masked_fill(self_mask, float("-inf")), dim=1, keepdim=True)
    per_anchor = (log_prob * positives).sum(dim=1)[has_positive] / counts[has_positive]
    return -per_anchor.mean()
