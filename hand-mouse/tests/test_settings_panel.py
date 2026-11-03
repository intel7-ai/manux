"""Tests for the settings window logic (no Tk window is opened)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.gesture_engine import DEFAULT_CONFIG, GestureEngine
from src.settings_panel import GROUPS, SETTINGS, SETTINGS_BY_KEY, SettingsPanel, apply_setting, reset_to_defaults


def test_every_setting_exists_in_config_and_default_is_in_range():
    cfg = GestureEngine().config
    assert len(SETTINGS_BY_KEY) == len(SETTINGS)
    group_ids = {g for g, _ in GROUPS}
    for st in SETTINGS:
        assert st.key in cfg, st.key
        assert st.key in DEFAULT_CONFIG, st.key
        assert st.lo <= float(cfg[st.key]) <= st.hi, st.key
        assert st.group in group_ids
        assert st.label and st.hint


def test_clamp_snaps_to_step_and_range():
    st = SETTINGS_BY_KEY["pinch_threshold"]
    assert st.clamp(0.0567) == pytest.approx(0.057)
    assert st.clamp(-1) == pytest.approx(st.lo)
    assert st.clamp(99) == pytest.approx(st.hi)
    assert st.fmt(0.057) == "0.057"
    assert SETTINGS_BY_KEY["switcher_timeout"].fmt(15) == "15"
    assert SETTINGS_BY_KEY["fling_strength"].fmt(1) == "1.00"


def test_apply_setting_keeps_dependent_values_consistent():
    cfg = GestureEngine().config
    apply_setting(cfg, SETTINGS_BY_KEY["pinch_threshold"], 0.07)
    assert cfg["release_threshold"] == pytest.approx(0.09)
    apply_setting(cfg, SETTINGS_BY_KEY["relative_gain_min"], 2.5)
    assert cfg["relative_gain_max"] >= cfg["relative_gain_min"]
    apply_setting(cfg, SETTINGS_BY_KEY["record_gain_max"], 0.5)  # clamped to 1.0
    assert cfg["record_gain_max"] == pytest.approx(1.0)
    assert cfg["record_gain_min"] <= cfg["record_gain_max"]


def test_panel_set_value_updates_live_engine_without_opening_a_window():
    engine = GestureEngine()
    panel = SettingsPanel(engine)
    assert panel.set_value("fist_dpi_multiplier", 8.04) == pytest.approx(8.0)
    assert engine.config["fist_dpi_multiplier"] == pytest.approx(8.0)
    assert panel.value("fist_dpi_multiplier") == pytest.approx(8.0)
    assert panel.is_open is False and panel._thread is None


def test_reset_to_defaults():
    cfg = GestureEngine().config
    apply_setting(cfg, SETTINGS_BY_KEY["fling_strength"], 4.0)
    apply_setting(cfg, SETTINGS_BY_KEY["switcher_step"], 0.15)
    reset_to_defaults(cfg)
    assert cfg["fling_strength"] == pytest.approx(DEFAULT_CONFIG["fling_strength"])
    assert cfg["switcher_step"] == pytest.approx(DEFAULT_CONFIG["switcher_step"])


def test_record_toggle_is_a_checkbox_setting():
    st = SETTINGS_BY_KEY["record_fling_enabled"]
    assert st.kind == "bool" and st.group == "FLING"
    engine = GestureEngine()
    panel = SettingsPanel(engine)
    assert panel.set_value("record_fling_enabled", 0) is False
    assert engine.config["record_fling_enabled"] is False
    assert panel.set_value("record_fling_enabled", 1) is True
    assert engine.config["record_fling_enabled"] is True

