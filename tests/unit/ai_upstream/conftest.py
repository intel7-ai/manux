"""Original feature tests use isolated user data and fake/dry-run output only."""
import importlib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_source_paths(tmp_path, monkeypatch):
    from manux.compat.hand_mouse import paths, ports
    old = paths.DATA_ROOT
    modules = ("action_bindings", "llm_binding", "custom_postures", "posture_teach", "sequence",
               "path_gesture", "draw_mode", "motion_mode", "gesture_preview", "session", "gesture_engine")
    for name in modules:
        mod = importlib.import_module("manux.compat.hand_mouse." + name)
        module_root = getattr(mod, "ROOT", getattr(mod, "DATA_ROOT", old))
        for key, value in list(vars(mod).items()):
            if isinstance(value, Path) and value.is_relative_to(module_root):
                monkeypatch.setattr(mod, key, tmp_path / value.relative_to(module_root))
    monkeypatch.setattr(paths, "DATA_ROOT", tmp_path)
    from manux.compat.hand_mouse import action_bindings as ab
    monkeypatch.setattr(ab, "_GLOBAL_REGISTRY", None)
    monkeypatch.setattr(ab, "_BINDING_PROFILE", None)
    from manux.compat.hand_mouse import posture_teach, custom_postures
    monkeypatch.setattr(posture_teach, "REC_DIR", tmp_path / "host-recordings")
    posture_teach.reset_cache()
    custom_postures.register()
    from manux.output.windows.custom_actions import execute_resolved_action
    from manux.output.windows.custom_launch import start
    from manux.output.windows import custom_macro
    monkeypatch.setattr(ports, "execute", execute_resolved_action)
    monkeypatch.setattr(ports, "launch", start)
    monkeypatch.setattr(ports, "macro_start", custom_macro.start)
    monkeypatch.setattr(ports, "macro_cancel", custom_macro.cancel_all)
    monkeypatch.setattr(ports, "macro_running", custom_macro.running)
    yield
    custom_macro.cancel_all()
