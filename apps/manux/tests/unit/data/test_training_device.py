"""Shared training-device behavior, including real optional CUDA tensor operations."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/"tools"))
import training_device


def test_auto_records_fallback_but_explicit_device_fails(monkeypatch):
    def probe(torch,device):
        if device!="cpu": raise RuntimeError("synthetic unavailable")
        return "synthetic CPU",1.
    monkeypatch.setattr(training_device,"_probe",probe)
    monkeypatch.setitem(sys.modules,"torch",SimpleNamespace(__version__="synthetic"))
    choice=training_device.select("auto")
    assert choice.actual=="cpu" and "cuda" in choice.reason and "xpu" in choice.reason
    with pytest.raises(ValueError,match="cuda"):training_device.select("cuda")


def test_cpu_probe_preserves_rng_state_and_augmentation_gradients():
    torch=pytest.importorskip("torch")
    before=torch.random.get_rng_state().clone()
    assert training_device.select("cpu").actual=="cpu"
    assert torch.equal(before,torch.random.get_rng_state())
    batch=torch.ones(3,15,133,requires_grad=True)
    output=training_device.augment(batch)
    assert output.device==batch.device and output.dtype==batch.dtype
    output.sum().backward()
    assert batch.grad is not None and torch.isfinite(batch.grad).all()


def test_worker_reports_explicit_unavailable_device_before_touching_data(tmp_path,monkeypatch):
    import json
    import app_worker
    def fail(device): raise ValueError("cuda unavailable in this runtime")
    monkeypatch.setattr(training_device,"select",fail)
    messages=[]
    monkeypatch.setattr(app_worker,"emit",lambda event,**data:messages.append({"event":event,**data}))
    path=tmp_path/"request.json"
    path.write_text(json.dumps({"schema":"manux-worker-job/1","kind":"train","device":"cuda"}),encoding="utf-8")
    assert app_worker.main(["--request",str(path)])==1
    assert messages==[{"event":"error","message":"cuda unavailable in this runtime"}]


def test_cuda_augmentation_and_adam_on_real_device_when_installed():
    torch=pytest.importorskip("torch")
    if not torch.cuda.is_available(): pytest.skip("optional CUDA runtime/device unavailable in this environment")
    choice=training_device.select("cuda")
    assert choice.actual=="cuda"
    batch=torch.ones(3,15,133,device="cuda",requires_grad=True)
    output=training_device.augment(batch)
    output.sum().backward()
    assert output.is_cuda and batch.grad.is_cuda and torch.isfinite(batch.grad).all()
