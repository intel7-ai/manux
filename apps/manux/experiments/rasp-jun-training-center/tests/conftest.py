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


def pytest_configure(config):
    config.addinivalue_line("markers", "record_off: run with Record & Fling at its shipped default (off)")
