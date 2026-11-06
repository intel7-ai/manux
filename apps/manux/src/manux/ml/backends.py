"""Explicit inference execution backends behind the existing ONNX session protocol."""
from __future__ import annotations

from dataclasses import asdict
import os
from time import perf_counter
from types import SimpleNamespace

import numpy as np

from manux.core.contracts import ExecutionDevice
from manux.ml.card import ModelError

DEVICES = ("cpu", "cuda", "openvino-cpu", "openvino-gpu", "openvino-npu", "auto")


def execution_devices(value):
    """NPU returns a string in some releases; GPU/CPU return a string vector."""
    rows = [value] if isinstance(value, str) else list(value)
    if not rows or any(not isinstance(row, str) or not row for row in rows):
        raise ModelError("backend", "runtime did not report execution devices")
    return rows


class OpenVinoSession:
    def __init__(self, path, device, requested):
        import openvino as ov
        started = perf_counter()
        core = ov.Core()
        if not any(str(d).split(".")[0] == device for d in core.available_devices):
            raise ModelError("backend", f"OpenVINO {device} device is unavailable")
        model = core.read_model(str(path))
        if len(model.inputs) != 1 or len(model.outputs) != 1:
            raise ModelError("shape", "OpenVINO expects one input and output")
        self.compiled = core.compile_model(model, device, {"PERFORMANCE_HINT": "LATENCY"})
        actual = execution_devices(self.compiled.get_property("EXECUTION_DEVICES"))
        if not actual or any(str(d).split(".")[0] != device for d in actual):
            raise ModelError("backend", f"OpenVINO did not execute exclusively on {device}")
        self.request = self.compiled.create_infer_request()
        self.inputs = [SimpleNamespace(name=p.get_any_name(), shape=list(p.shape)) for p in model.inputs]
        self.outputs = [SimpleNamespace(name=p.get_any_name(), shape=list(p.shape)) for p in model.outputs]
        self.execution = ExecutionDevice(requested, "openvino-"+device.lower(), "openvino "+ov.__version__,
                                         str(core.get_property(actual[0], "FULL_DEVICE_NAME")))
        self.execution_devices = actual
        self.startup_ms = (perf_counter()-started)*1000

    def get_inputs(self): return self.inputs
    def get_outputs(self): return self.outputs
    def run(self, names, feeds):
        self.request.infer({0: np.asarray(feeds[self.inputs[0].name], dtype=np.float32)})
        return [np.array(self.request.get_output_tensor(0).data, copy=True)]


def _ort(path, device, requested):
    import onnxruntime as ort
    provider = "CUDAExecutionProvider" if device == "cuda" else "CPUExecutionProvider"
    if provider not in ort.get_available_providers():
        raise ModelError("backend", provider+" is not installed in this environment")
    options = ort.SessionOptions()
    if device == "cuda": options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
    started = perf_counter()
    session = ort.InferenceSession(str(path), sess_options=options, providers=[provider])
    session.disable_fallback()
    if session.get_providers()[0] != provider:
        raise ModelError("backend", "requested execution provider was not loaded")
    session.execution = ExecutionDevice(requested, device, "onnxruntime "+ort.__version__, provider)
    session.execution_devices = [provider]
    session.startup_ms = (perf_counter()-started)*1000
    return session


def session(path, requested=None):
    requested = requested or os.environ.get("MANUX_INFERENCE_DEVICE", "cpu")
    if requested not in DEVICES: raise ModelError("backend", "unknown inference device")
    try:
        if requested == "auto":
            # Keep auto conservative. Hardware use requires an explicit, benchmarked choice.
            value = _ort(path, "cpu", requested)
            value.execution = ExecutionDevice("auto", "cpu", value.execution.runtime, value.execution.name,
                                              "auto keeps the CPU baseline; select an accelerator explicitly after comparison")
            return value
        return (OpenVinoSession(path, requested.split("-")[1].upper(), requested)
                if requested.startswith("openvino-") else _ort(path, requested, requested))
    except ModelError: raise
    except ImportError as error:
        raise ModelError("runtime_missing" if requested in ("cpu","auto") else "backend", f"{requested} runtime is not installed") from error
    except Exception as error:
        if requested in ("cpu","auto"):
            raise ModelError("unreadable", f"model load failed ({type(error).__name__})") from error
        raise ModelError("backend", f"{requested} initialization failed ({type(error).__name__}); check driver and model operator support") from error


def describe(predictor):
    value = getattr(getattr(predictor, "session", None), "execution", None)
    return asdict(value) if value is not None else {"actual": "custom", "runtime": "injected session"}
