"""Bundled profile selection uses synthetic paths and never touches real app data."""
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


@pytest.fixture
def entry(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "manux_packaged_entry", Path(__file__).resolve().parents[3] / "packaging" / "entry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("MANUX_DATA_DIR", raising=False)
    (tmp_path / "bundle").mkdir()
    return module


def build_info(tmp_path, **values):
    (tmp_path / "bundle" / "build_info.json").write_text(json.dumps(values), encoding="utf-8")


@pytest.mark.parametrize("profile", ["MANUX-dev/synthetic-current", r"MANUX-dev\synthetic-current"])
def test_packaged_profile_reuses_existing_data_without_copy_or_rewrite(entry, tmp_path, profile):
    selected = tmp_path / "appdata" / "MANUX-dev" / "synthetic-current"
    selected.mkdir(parents=True)
    sentinel = selected / "existing-data.txt"
    sentinel.write_bytes(b"synthetic unchanged data")
    build_info(tmp_path, pack_id="MANUXPreview", data_profile=profile)
    entry._preview_data_dir()
    assert Path(os.environ["MANUX_DATA_DIR"]) == selected.resolve()
    assert sentinel.read_bytes() == b"synthetic unchanged data"
    assert not (tmp_path / "appdata" / "MANUXPreview").exists()


def test_explicit_environment_overrides_even_invalid_bundled_profile(entry, tmp_path, monkeypatch):
    explicit = str(tmp_path / "explicit-choice")
    monkeypatch.setenv("MANUX_DATA_DIR", explicit)
    build_info(tmp_path, pack_id="MANUXPreview", data_profile="../invalid")
    entry._preview_data_dir()
    assert os.environ["MANUX_DATA_DIR"] == explicit


@pytest.mark.parametrize("profile", [None, 42, "", ".", "..", "../outside", r"MANUX-dev\..\outside",
    "/absolute", r"\absolute", r"C:\outside", "C:outside", r"\\server\share", "profile:stream",
    "profile//child", "profile/./child", "profile\x00", "profile.", "profile "])
def test_invalid_packaged_profile_fails_without_selecting_another_profile(entry, tmp_path, profile):
    build_info(tmp_path, pack_id="MANUXPreview", data_profile=profile)
    with pytest.raises(ValueError, match="profile"):
        entry._preview_data_dir()
    assert "MANUX_DATA_DIR" not in os.environ
    assert not (tmp_path / "appdata").exists()


@pytest.mark.parametrize("pack_id,selected", [("MANUXPreview", "MANUXPreview"), ("MANUX", None), ("", None)])
def test_omitted_profile_preserves_prior_preview_and_release_defaults(entry, tmp_path, pack_id, selected):
    build_info(tmp_path, pack_id=pack_id)
    entry._preview_data_dir()
    if selected is None:
        assert "MANUX_DATA_DIR" not in os.environ
    else:
        assert Path(os.environ["MANUX_DATA_DIR"]) == tmp_path / "appdata" / selected / "v04"


def test_missing_build_info_preserves_source_default(entry):
    entry._preview_data_dir()
    assert "MANUX_DATA_DIR" not in os.environ
