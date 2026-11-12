"""Offline diagnostic checks use synthetic profiles and actual installed codecs."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest

from manux.ml.spatial_mlp import SpatialMlpModel
from manux.settings.paths import AppPaths


@pytest.fixture
def diagnostic():
    spec = importlib.util.spec_from_file_location(
        "manux_bundle_diagnostic", Path(__file__).resolve().parents[3] / "packaging/bundle_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshots(folder):
    return {p.relative_to(folder): hashlib.sha256(p.read_bytes()).digest()
            for p in folder.rglob("*") if p.is_file()}


def model(folder):
    folder.mkdir(parents=True)
    mlp = SpatialMlpModel(np.zeros((4, 96)), np.zeros(4), np.zeros((2, 4)), np.zeros(2),
                          classes=("none", "thumb_middle"))
    mlp.save(folder / "synthetic.json")
    (folder / "active_model.json").write_text('{"active_model_id":"synthetic.json"}', encoding="utf-8")


def test_missing_optional_profile_is_reported_without_creating_data(diagnostic, tmp_path):
    paths = AppPaths(tmp_path / "bundle", tmp_path / "unconfigured", True)
    result = diagnostic._profile(paths)
    assert result["ok"]
    assert not any(result[key] for key in ("config_present", "active_profile_selected",
                                          "selected_mlp_loaded", "gateway_config_present", "gateway_dpapi_readable"))
    assert not paths.data_dir.exists()


def test_existing_settings_profile_and_mlp_are_read_without_changes(diagnostic, tmp_path):
    paths = AppPaths(tmp_path / "bundle", tmp_path / "user-data", True, True)
    paths.profiles_dir.mkdir(parents=True)
    (paths.config_dir / "settings.json").write_text(
        '{"version":1,"values":{"cursor.relative_gain_min":1.15}}', encoding="utf-8")
    (paths.profiles_dir / "synthetic.json").write_text(
        '{"values":{"cursor.smoothing":0.35}}', encoding="utf-8")
    (paths.config_dir / "active_profile.txt").write_text("synthetic", encoding="utf-8")
    model(paths.spatial_models_dir)
    before = snapshots(paths.data_dir)
    result = diagnostic._profile(paths)
    assert result["ok"] and result["config_present"] and result["active_profile_selected"]
    assert result["selected_mlp_loaded"] and not result["gateway_config_present"]
    assert snapshots(paths.data_dir) == before
    serialized = json.dumps(result)
    assert "synthetic" not in serialized and str(paths.data_dir) not in serialized
    assert "1.15" not in serialized and "0.35" not in serialized


@pytest.mark.skipif(sys.platform != "win32", reason="Windows current-user DPAPI")
def test_protected_gateway_is_checked_without_reporting_credential_or_origin(diagnostic, tmp_path):
    from manux.platform.fixed_gateway import bootstrap_gateway
    paths = AppPaths(tmp_path / "bundle", tmp_path / "user-data", True, True)
    origin = "https://synthetic-private-gateway.example.com"
    bootstrap_gateway(paths.data_dir, origin)
    before = snapshots(paths.data_dir)
    result = diagnostic._profile(paths)
    assert result["ok"] and result["gateway_config_present"] and result["gateway_dpapi_readable"]
    assert origin not in json.dumps(result) and str(paths.data_dir) not in json.dumps(result)
    assert snapshots(paths.data_dir) == before


def test_corrupt_optional_config_fails_without_leaking_its_contents(diagnostic, tmp_path):
    paths = AppPaths(tmp_path / "bundle", tmp_path / "user-data", True, True)
    paths.config_dir.mkdir(parents=True)
    private = "synthetic-private-value-must-not-be-reported"
    (paths.config_dir / "settings.json").write_text(private, encoding="utf-8")
    before = snapshots(paths.data_dir)
    result = diagnostic._profile(paths)
    assert not result["ok"] and result["checks"]["settings"]["error_type"] == "ValueError"
    assert private not in json.dumps(result) and str(paths.data_dir) not in json.dumps(result)
    assert snapshots(paths.data_dir) == before


def test_real_codecs_load_and_missing_codec_is_a_redacted_failure(diagnostic):
    pytest.importorskip("av")
    checks = {}
    for codec in ("libx264", "libvpx"):
        diagnostic._attempt(checks, codec, lambda codec=codec: diagnostic._codec(codec))
        assert checks[codec]["ok"]
    diagnostic._attempt(checks, "unavailable", lambda: diagnostic._codec("synthetic-absent-encoder"))
    assert checks["unavailable"] == {"ok": False, "error_type": "UnknownCodecError"}
    assert "synthetic-absent" not in json.dumps(checks)
