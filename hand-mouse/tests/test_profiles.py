"""Calibration profiles: storage, selection, and the overlay on the engine config."""
import json

import pytest

from src import profiles
from src.gesture_engine import DEFAULT_CONFIG, GestureEngine


@pytest.fixture(autouse=True)
def _no_run_override(monkeypatch):
    monkeypatch.setattr(profiles, "RUN_OVERRIDE", None)


def make_config(tmp_path, **base):
    path = tmp_path / "gestures.json"
    path.write_text(json.dumps(base), encoding="utf-8")
    return path


def test_names_are_validated_and_cannot_escape_the_folder(tmp_path):
    assert profiles.valid_name("홍길동") and profiles.valid_name("Kim 2") and profiles.valid_name("a-b_c.d")
    for bad in ("", "../evil", "a/b", "a\\b", "..", ".", "x" * 33, "name."):
        assert not profiles.valid_name(bad)
    with pytest.raises(ValueError):
        profiles.save_profile(tmp_path, "../evil", {"pinch_threshold": 0.02})


def test_save_list_replace_keeps_a_backup_and_delete_clears_active(tmp_path):
    profiles.save_profile(tmp_path, "민수", {"pinch_threshold": 0.02, "bad": "text", "flag": True})
    profiles.save_profile(tmp_path, "하나", {"pinch_threshold": 0.03})
    assert profiles.list_profiles(tmp_path) == ["민수", "하나"]
    assert profiles.load_profile(tmp_path, "민수") == {"pinch_threshold": 0.02}   # non-numbers are dropped
    profiles.save_profile(tmp_path, "민수", {"pinch_threshold": 0.025})
    backup = json.loads((tmp_path / "profiles" / "민수.json.bak").read_text(encoding="utf-8"))
    assert backup["updates"] == {"pinch_threshold": 0.02}
    profiles.set_active(tmp_path, "민수")
    assert profiles.get_active(tmp_path) == "민수"
    assert profiles.delete_profile(tmp_path, "민수") and profiles.get_active(tmp_path) is None
    assert profiles.list_profiles(tmp_path) == ["하나"]


def test_engine_overlays_the_active_profile_on_the_base_config(tmp_path):
    cfg = make_config(tmp_path, pinch_threshold=0.025, smoothing=0.5)
    profiles.save_profile(tmp_path, "민수", {"pinch_threshold": 0.018, "tremor_deadzone": 0.004, "nonsense_key": 1.0})
    profiles.set_active(tmp_path, "민수")
    e = GestureEngine(config_path=cfg)
    assert e.profile_name == "민수"
    assert e.config["pinch_threshold"] == 0.018 and e.config["tremor_deadzone"] == 0.004
    assert e.config["smoothing"] == 0.5 and "nonsense_key" not in e.config      # only known keys


def test_two_people_get_their_own_values_and_none_means_the_base(tmp_path):
    cfg = make_config(tmp_path, pinch_threshold=0.025)
    profiles.save_profile(tmp_path, "작은손", {"pinch_threshold": 0.012})
    profiles.save_profile(tmp_path, "큰손", {"pinch_threshold": 0.03})
    e = GestureEngine(config_path=cfg)
    assert e.profile_name is None and e.config["pinch_threshold"] == 0.025
    assert e.set_profile("작은손") and e.config["pinch_threshold"] == 0.012
    assert e.set_profile("큰손") and e.config["pinch_threshold"] == 0.03
    assert e.set_profile(None) and e.config["pinch_threshold"] == 0.025 and e.profile_name is None
    assert not e.set_profile("없는사람") and e.config["pinch_threshold"] == 0.025
    assert GestureEngine(config_path=cfg).profile_name is None                 # None was remembered


def test_run_override_beats_the_remembered_choice(tmp_path, monkeypatch):
    cfg = make_config(tmp_path, pinch_threshold=0.025)
    profiles.save_profile(tmp_path, "a", {"pinch_threshold": 0.01})
    profiles.save_profile(tmp_path, "b", {"pinch_threshold": 0.02})
    profiles.set_active(tmp_path, "a")
    monkeypatch.setattr(profiles, "RUN_OVERRIDE", "b")
    assert GestureEngine(config_path=cfg).config["pinch_threshold"] == 0.02
    monkeypatch.setattr(profiles, "RUN_OVERRIDE", "")
    assert GestureEngine(config_path=cfg).profile_name is None


def test_a_missing_or_broken_profile_falls_back_to_the_base(tmp_path):
    cfg = make_config(tmp_path, pinch_threshold=0.025)
    (tmp_path / "active_profile.txt").write_text("사라짐", encoding="utf-8")
    assert GestureEngine(config_path=cfg).config["pinch_threshold"] == 0.025
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "깨짐.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "active_profile.txt").write_text("깨짐", encoding="utf-8")
    assert GestureEngine(config_path=cfg).profile_name is None


def test_saving_the_config_does_not_copy_the_profile_into_the_base_file(tmp_path):
    cfg = make_config(tmp_path, pinch_threshold=0.025, smoothing=0.5)
    profiles.save_profile(tmp_path, "민수", {"pinch_threshold": 0.018, "tremor_deadzone": 0.004})
    profiles.set_active(tmp_path, "민수")
    e = GestureEngine(config_path=cfg)
    e.config["smoothing"] = 0.4                                   # the user tuned an unrelated setting
    e.save_config()
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved["smoothing"] == 0.4
    assert saved["pinch_threshold"] == 0.025                      # base value kept, not the profile's
    assert saved["tremor_deadzone"] == DEFAULT_CONFIG["tremor_deadzone"]
    e.config["pinch_threshold"] = 0.02                            # the user changed a profile-owned key
    e.save_config()
    assert json.loads(cfg.read_text(encoding="utf-8"))["pinch_threshold"] == 0.02
