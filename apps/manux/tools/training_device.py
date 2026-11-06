"""Shared single-device FP32 training support. torch stays exclusively in tools/worker."""
from __future__ import annotations

from dataclasses import asdict
from time import perf_counter
import math

from manux.core.contracts import ExecutionDevice

DEVICES = ("auto", "cpu", "cuda", "xpu")
_checked = {}


def synchronize(device):
    import torch
    kind = str(device).split(":")[0]
    if kind in ("cuda", "xpu"): getattr(torch, kind).synchronize()


def _probe(torch, kind):
    if kind != "cpu" and not (hasattr(torch, kind) and getattr(torch, kind).is_available()):
        raise ValueError(f"{kind} is unavailable in this torch build or driver")
    key = (torch.__version__, kind)
    if key in _checked: return _checked[key]
    cpu_state = torch.random.get_rng_state()
    accelerator_state = getattr(torch, kind).get_rng_state() if kind != "cpu" else None
    started = perf_counter()
    try:
        model = torch.nn.Sequential(torch.nn.ConstantPad1d((2, 0), 0), torch.nn.Conv1d(133, 64, 3),
                                    torch.nn.ReLU(), torch.nn.Flatten(), torch.nn.Linear(64*15, 2)).to(kind)
        optimizer = torch.optim.Adam(model.parameters(), lr=.001)
        x = torch.zeros(2, 133, 15, device=kind)
        y = torch.tensor([0, 1], device=kind)
        optimizer.zero_grad()
        loss = torch.nn.functional.cross_entropy(model(x), y)
        loss.backward()
        optimizer.step()
        synchronize(kind)
        if not torch.isfinite(loss).item(): raise ValueError("non-finite device probe")
        name = (getattr(torch, kind).get_device_name(0) if kind != "cpu" else "CPU")
        _checked[key] = (name, (perf_counter()-started)*1000)
        return _checked[key]
    finally:
        torch.random.set_rng_state(cpu_state)
        if accelerator_state is not None: getattr(torch, kind).set_rng_state(accelerator_state)


def select(requested="cpu"):
    import torch
    if requested not in DEVICES: raise ValueError("training device must be auto/cpu/cuda/xpu")
    reasons = []
    for kind in (("cuda", "xpu", "cpu") if requested == "auto" else (requested,)):
        try:
            name, probe_ms = _probe(torch, kind)
            return ExecutionDevice(requested, kind, "torch " + torch.__version__, name, "; ".join(reasons))
        except Exception as error:
            reason = f"{kind}: {type(error).__name__} · build/driver or training operators unavailable"
            if requested != "auto": raise ValueError(reason) from error
            reasons.append(reason)
    raise ValueError("no working training device")


def augment(batch):
    """The existing rotation/scale/jitter, with every tensor on batch's device and dtype."""
    import torch
    b = batch.shape[0]
    ang = (torch.rand(b, 3, device=batch.device, dtype=batch.dtype)-.5)*math.radians(30)
    cx, cy, cz = torch.cos(ang).unbind(1)
    sx, sy, sz = torch.sin(ang).unbind(1)
    one, zero = batch.new_ones(b), batch.new_zeros(b)
    rx = torch.stack([one,zero,zero,zero,cx,-sx,zero,sx,cx],1).view(b,3,3)
    ry = torch.stack([cy,zero,sy,zero,one,zero,-sy,zero,cy],1).view(b,3,3)
    rz = torch.stack([cz,-sz,zero,sz,cz,zero,zero,zero,one],1).view(b,3,3)
    r = rz @ ry @ rx
    batch = batch.clone()
    for lo in (0, 63):
        points = batch[:,:,lo:lo+63].reshape(b,batch.shape[1],21,3)
        batch[:,:,lo:lo+63] = torch.einsum("btjk,blk->btjl",points,r).reshape(b,batch.shape[1],63)
    batch[:,:,:130] *= 1+(torch.rand(b,1,1,device=batch.device,dtype=batch.dtype)-.5)*.2
    batch[:,:,:130] += torch.randn_like(batch[:,:,:130])*.01
    return batch


def cpu_export(model):
    from copy import deepcopy
    return deepcopy(model).cpu().eval()


def record(device): return asdict(device)
