"""Geometry-consistent augmentation in canonical hand coordinates (training only)."""
import torch


def augment(x):
    b, length, _ = x.shape
    device = x.device
    # Causal time scaling, anchored at the labelled last frame. Screen movement
    # keeps its sign: a left swipe must never turn into a right swipe.
    speed = .75 + torch.rand(b, 1, device=device) * .5
    pos = length - 1 - torch.arange(length - 1, -1, -1, device=device)[None] * speed
    inside = pos >= 0
    lo = pos.floor().clamp(0, length - 1).long()
    hi = (lo + 1).clamp(max=length - 1)
    mix = (pos - pos.floor()).unsqueeze(-1)
    batch = torch.arange(b, device=device)[:, None]
    a, z = x[batch, lo], x[batch, hi]
    out = a * (1 - mix) + z * mix
    valid = inside & (a[:, :, -1] > .5) & ((z[:, :, -1] > .5) | (mix[:, :, 0] == 0))
    pts = out[:, :, :63].reshape(b, length, 21, 3).clone()
    # Opposite hand has mirrored local depth after canonicalisation. This does
    # not mirror the image-space motion or alter swipe labels.
    depth = (.85 + torch.rand(b, 1, 1, device=device) * .3)
    depth *= torch.where(torch.rand(b, 1, 1, device=device) > .5, 1., -1.)
    pts[:, :, :, 2] *= depth
    jitter = torch.randn(b, 1, 21, 3, device=device) * .008 + torch.randn_like(pts) * .002
    jitter[:, :, [0, 5, 9, 17], :] = 0  # preserve the axes used for canonicalisation
    pts += jitter
    pts *= valid[:, :, None, None]
    out[:, :, :63] = pts.reshape(b, length, 63)
    delta = torch.zeros_like(pts)
    delta[:, 0] = out[:, 0, 63:126].reshape(b, 21, 3)
    delta[:, 0, :, 2] *= depth[:, 0]
    delta[:, 1:] = (pts[:, 1:] - pts[:, :-1]) * valid[:, :-1, None, None]
    out[:, :, 63:126] = delta.reshape(b, length, 63)
    out[:, :, 126:130] = torch.linalg.vector_norm(pts[:, :, [8, 12, 16, 20]] - pts[:, :, 4:5], dim=-1)
    out[:, :, 130:132] *= speed.unsqueeze(-1)
    out[:, :, -1] = valid
    out *= valid.unsqueeze(-1)
    return out
