from __future__ import annotations

import json
from pathlib import Path

import pytest

from manux.settings import builtin, paths, profiles, store
from manux.settings.schema import Schema, Setting


def schema() -> Schema:
    return Schema(builtin.ALL)


def test_setting_rejects_bad_declarations():
    with pytest.raises(ValueError):
        Setting("nodot", 1.0)
    with pytest.raises(ValueError):
        Setting("a.b", 5.0, minimum=0, maximum=1)
    with pytest.raises(ValueError):
        Schema([Setting("a.b", 1.0), Setting("a.b", 2.0)])


def test_bad_values_fall_back_to_defaults_with_a_warning():
    s = store.Settings(schema(), {"tracking.min_presence_confidence": 2.0, "camera.mirror": "yes",
                                  "camera.width": 1920, "camera.rotation": "45"})
    assert s.get("tracking.min_presence_confidence") == 0.5
    assert s.get("camera.mirror") is True
    assert s.get("camera.width") == 1920
    assert s.get("camera.rotation") == "0"
    assert len(s.warnings) == 3


def test_unknown_keys_are_kept_but_cannot_be_set():
    s = store.Settings(schema(), {"v03.pinch_threshold": 0.04})
    assert s.stored()["v03.pinch_threshold"] == 0.04
    assert not s.set("pointer.nope", 1.0)
    with pytest.raises(KeyError):
        s.get("pointer.nope")


def test_profile_overlays_without_changing_stored_values():
    s = store.Settings(schema(), {"tracking.min_tracking_confidence": 0.4})
    s.apply_profile({"tracking.min_tracking_confidence": 0.3, "someone.else": 1})
    assert s.get("tracking.min_tracking_confidence") == 0.3
    assert s.stored()["tracking.min_tracking_confidence"] == 0.4
    s.apply_profile(None)
    assert s.get("tracking.min_tracking_confidence") == 0.4


def test_section_strips_the_owner_prefix():
    sec = store.Settings(schema()).section("tracking")
    assert sec["hand_crop"] is False and sec["detect_max_width"] == 640


def test_load_save_roundtrip_and_broken_files(tmp_path: Path):
    s = store.Settings(schema())
    assert s.set("camera.fps", 30)
    store.save(s, tmp_path)
    again = store.load(schema(), tmp_path)
    assert again.get("camera.fps") == 30 and not again.warnings
    (tmp_path / store.FILE_NAME).write_text("{not json", encoding="utf-8")
    broken = store.load(schema(), tmp_path)
    assert broken.get("camera.fps") == 60 and broken.warnings
    assert store.load(schema(), tmp_path / "missing").get("camera.fps") == 60


def test_failed_save_keeps_the_old_file(tmp_path: Path, monkeypatch):
    s = store.Settings(schema())
    s.set("camera.fps", 30)
    store.save(s, tmp_path)
    before = (tmp_path / store.FILE_NAME).read_text(encoding="utf-8")

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(store.json, "dump", boom)
    s.set("camera.fps", 15)
    with pytest.raises(OSError):
        store.save(s, tmp_path)
    assert (tmp_path / store.FILE_NAME).read_text(encoding="utf-8") == before
    assert [p.name for p in tmp_path.iterdir()] == [store.FILE_NAME]


def test_profiles_roundtrip_and_name_rules(tmp_path: Path):
    profiles.save_profile(tmp_path, "test user", {"camera.fps": 30})
    assert profiles.list_profiles(tmp_path) == ["test user"]
    assert profiles.load_profile(tmp_path, "test user") == {"camera.fps": 30}
    profiles.set_active(tmp_path, "test user")
    assert profiles.get_active(tmp_path) == "test user"
    assert profiles.delete_profile(tmp_path, "test user")
    assert profiles.get_active(tmp_path) is None
    with pytest.raises(ValueError):
        profiles.profile_path(tmp_path, "../escape")






def test_paths_never_point_into_the_repository(tmp_path: Path):
    p = paths.resolve(env={"APPDATA": str(tmp_path)}, frozen=False)
    assert p.data_dir == tmp_path / "MANUX-dev" / "v04"
    assert paths.resolve(env={"APPDATA": str(tmp_path)}, frozen=True, app_dir=tmp_path).data_dir == \
        tmp_path / "MANUX" / "v04"
    assert paths.resolve(env={"MANUX_DATA_DIR": str(tmp_path / "x")}, frozen=False).data_dir == tmp_path / "x"
    assert paths.v03_config_dir({"APPDATA": str(tmp_path)}) == tmp_path / "MANUX" / "config"
