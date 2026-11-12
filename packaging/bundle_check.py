"""Offline frozen-build diagnostic. No camera, desktop capture, input or network.

The report contains only check names, booleans and exception class names. User
settings/models/DPAPI credentials are read in place and never copied or changed.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

NATIVE_MODULES = ("aiortc", "av", "pylibsrtp", "aiohttp", "qrcode", "onnxruntime")
ENCODERS = ("libx264", "libvpx")
ASSETS = {
    "qml_standard": "manux/ui/qml/dashboard.qml",
    "qml_studio": "manux/ui/qml/studio/dashboard.qml",
    "phone_page": "manux/link/static/phone.html",
    "internet_page": "manux/link/static/internet.html",
    "hand_model": "models/hand_landmarker.task",
    "pose_model": "models/pose_landmarker_lite.task",
}


def _attempt(checks, name, operation):
    try:
        operation()
        checks[name] = {"ok": True}
    except Exception as error:
        checks[name] = {"ok": False, "error_type": type(error).__name__}


def _file(path):
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError


def _codec(name):
    import av
    # This resolves the actual native encoder implementation without capturing
    # pixels or starting a stream. Missing codec DLLs cannot pass a file check.
    context = av.CodecContext.create(name, "w")
    if context.type != "video" or not context.is_encoder:
        raise ValueError


def _selected_mlp(folder, *, required=False):
    from manux.ml.spatial_mlp import SpatialMlpModel

    active = folder / "active_model.json"
    if active.is_file():
        document = json.loads(active.read_text(encoding="utf-8"))
        name = document.get("active_model_id") if isinstance(document, dict) else None
        if (not isinstance(name, str) or Path(name).name != name
                or not name.endswith(".json") or name == "active_model.json"):
            raise ValueError
        path = folder / name
    else:
        path = folder / "spatial_pose_mlp.json"
        if not path.is_file():
            candidates = sorted(p for p in folder.glob("*.json") if p.name != "active_model.json")
            if not candidates:
                if required:
                    raise FileNotFoundError
                return False
            path = candidates[0]
    SpatialMlpModel.load(path)
    return True


def _profile(paths):
    from manux.app import schema
    from manux.platform.fixed_gateway import CONFIG_NAME, load_gateway_config
    from manux.settings import profiles
    from manux.settings.store import load

    checks = {}
    report = {"custom_data_dir": paths.custom_data_dir,
              "config_present": (paths.config_dir / "settings.json").is_file(),
              "active_profile_selected": False, "selected_mlp_loaded": False,
              "gateway_config_present": (paths.data_dir / CONFIG_NAME).is_file(),
              "gateway_dpapi_readable": False}

    def settings():
        selected = load(schema(), paths.config_dir)
        active = paths.config_dir / profiles.ACTIVE_FILE
        name = active.read_text(encoding="utf-8").strip() if active.is_file() else ""
        if name:
            if not profiles.valid_name(name):
                raise ValueError
            values = profiles.load_profile(paths.config_dir, name)
            if values is None:
                raise ValueError
            report["active_profile_selected"] = True
            selected.apply_profile(values)
        if selected.warnings:
            raise ValueError

    def model():
        report["selected_mlp_loaded"] = _selected_mlp(paths.spatial_models_dir)

    def gateway():
        report["gateway_dpapi_readable"] = load_gateway_config(paths.data_dir) is not None

    _attempt(checks, "settings", settings)
    _attempt(checks, "selected_mlp", model)
    _attempt(checks, "gateway_dpapi", gateway)
    report["checks"] = checks
    report["ok"] = all(check["ok"] for check in checks.values())
    return report


def _helper(path):
    _file(path)
    completed = subprocess.run([str(path), "--version"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=10, check=False,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                               env={key: value for key, value in os.environ.items()
                                    if key.upper() in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP")})
    if completed.returncode != 0 or b"cloudflared version" not in completed.stdout + completed.stderr:
        raise RuntimeError


def run(out: Path) -> int:
    from manux.settings.paths import resolve

    checks = {}
    profile = {"ok": False}
    result = {"ok": False, "frozen": bool(getattr(sys, "frozen", False)), "checks": checks}
    try:
        paths = resolve()
        for name in NATIVE_MODULES:
            _attempt(checks, name, lambda name=name: importlib.import_module(name))
        for encoder in ENCODERS:
            _attempt(checks, "encoder_" + encoder, lambda name=encoder: _codec(name))
        for name, relative in ASSETS.items():
            _attempt(checks, name, lambda relative=relative: _file(paths.app_dir / relative))
        _attempt(checks, "bundled_mlp", lambda: _selected_mlp(paths.app_dir / "models/spatial_mlp", required=True))
        _attempt(checks, "cloudflared", lambda: _helper(paths.app_dir / "tools/cloudflared.exe"))
        _attempt(checks, "trainer_worker", lambda: _file(Path(sys.executable).parent / "worker/MANUXWorker.exe"))
        profile = _profile(paths)
    except Exception as error:
        checks["diagnostic"] = {"ok": False, "error_type": type(error).__name__}
    checks["app_torch_not_imported"] = {"ok": not any(name == "torch" or name.startswith("torch.")
                                                    for name in sys.modules)}
    result["profile"] = profile
    result["ok"] = result["frozen"] and all(check["ok"] for check in checks.values()) and profile["ok"]
    out = Path(out)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return 1  # No traceback or local output path is written to the app log.
    return 0 if result["ok"] else 1
