"""Execution selection must report actual hardware and cannot silently fall back."""
from types import SimpleNamespace
import sys

import pytest

from manux.ml.backends import execution_devices, session
from manux.ml.card import ModelError


def test_openvino_scalar_npu_and_vector_gpu_device_reports_are_normalized():
    assert execution_devices("NPU") == ["NPU"]
    assert execution_devices(["GPU.0"]) == ["GPU.0"]
    with pytest.raises(ModelError): execution_devices([])
    with pytest.raises(ModelError): execution_devices([""])


def test_explicit_cuda_cannot_run_on_an_installed_cpu_provider(monkeypatch):
    fake = SimpleNamespace(get_available_providers=lambda:["CPUExecutionProvider"])
    monkeypatch.setitem(sys.modules,"onnxruntime",fake)
    with pytest.raises(ModelError, match="not installed"):
        session("synthetic.onnx","cuda")


def test_cuda_provider_initialization_cannot_silently_select_cpu(monkeypatch):
    options=[]
    class Options:
        def add_session_config_entry(self,key,value): options.append((key,value))
    fake=SimpleNamespace(SessionOptions=Options,get_available_providers=lambda:["CUDAExecutionProvider","CPUExecutionProvider"],
        InferenceSession=lambda *a,**kw:SimpleNamespace(disable_fallback=lambda:None,get_providers=lambda:["CPUExecutionProvider"]))
    monkeypatch.setitem(sys.modules,"onnxruntime",fake)
    with pytest.raises(ModelError,match="not loaded"): session("synthetic.onnx","cuda")
    assert ("session.disable_cpu_ep_fallback","1") in options


def test_unknown_backend_is_not_treated_as_cpu():
    with pytest.raises(ModelError,match="unknown"):session("synthetic.onnx","npu_typo")
