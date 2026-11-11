"""Tests always run against the built-in defaults, never the user's tuned config file."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.gesture_engine as gesture_engine


@pytest.fixture(autouse=True)
def _default_config_only(tmp_path, monkeypatch, request):
    monkeypatch.setattr(gesture_engine, "DEFAULT_CONFIG_PATH", tmp_path / "no_user_config.json")
    # Record & Fling ships OFF; most gesture tests exercise it, so they switch it on.
    # Tests marked `record_off` keep the real default.
    if request.node.get_closest_marker("record_off") is None:
        monkeypatch.setitem(gesture_engine.DEFAULT_CONFIG, "record_fling_enabled", True)


@pytest.fixture(autouse=True)
def _own_fold_cache(tmp_path, monkeypatch):
    """A test that starts a (fake) training must not leave an 'unfinished training' in the real logs/fold_cache."""
    import src.fold_cache as fold_cache
    monkeypatch.setattr(fold_cache, "CACHE_ROOT", tmp_path / "fold_cache")


def pytest_configure(config):
    config.addinivalue_line("markers", "record_off: run with Record & Fling at its shipped default (off)")


@pytest.fixture(autouse=True)
def _own_binding_files(tmp_path, monkeypatch):
    """Never the user's gesture bindings, motions, drawings, sequences or AI history (2026-11-10: a motion the user
    registered changed the task list two dataset tests count; tests could also have written to config/)."""
    import src.action_bindings as ab
    import src.draw_mode as dm
    import src.gesture_catalog as gc
    import src.llm_binding as lb
    import src.path_gesture as pg
    import src.sequence as sq
    monkeypatch.setattr(ab, "DEFAULT_BINDINGS_PATH", tmp_path / "gesture_bindings.json")
    monkeypatch.setattr(ab, "_GLOBAL_REGISTRY", None)
    monkeypatch.setattr(dm, "DEFAULT_PATH", tmp_path / "draw_shapes.json")
    monkeypatch.setattr(sq, "DEFAULT_PATH", tmp_path / "sequences.json")
    monkeypatch.setattr(pg, "DEFAULT_PATH", tmp_path / "path_gestures.json")
    monkeypatch.setattr(lb, "HISTORY_PATH", tmp_path / "ai_connections.json")
    gc.sync_custom_motions({})
    yield
    gc.sync_custom_motions({})
