import pytest

from manux.app import schema
from manux.output.windows.config import WindowsOutputConfig
from manux.settings.advanced import EXTRA_SETTINGS, prepare, rows
from manux.settings.store import Settings


def configured(values=None):
    return Settings(schema(), values)


def test_scroll_edit_reaches_existing_output_config_without_changing_other_values():
    settings = configured({"pinch.click_threshold": .0537})
    before = settings.stored()
    changes = prepare(settings, {"cursor.scroll_gain": 42000., "cursor.scroll_invert": True,
                                 "cursor.scroll_inertia": False})
    assert settings.stored() == before
    for key, value in changes.items():
        assert settings.set(key, value)
    output = WindowsOutputConfig(**settings.section("cursor"))
    assert output.scroll_gain == 42000
    assert output.scroll_invert and not output.scroll_inertia
    assert settings.get("pinch.click_threshold") == .0537


@pytest.mark.parametrize("edits", [
    {"cursor.scroll_gain": 42000, "cursor.scroll_inertia": 1},
    {"cursor.scroll_gain": float("nan")}, {"cursor.scroll_gain": float("inf")},
    {"cursor.scroll_gain": -1}, {"cursor.scroll_gain": "42000"},
    {"cursor.movement_mode": "typo"}, {"camera.source": "1"},
    {"cursor.relative_gain_min": 2.5, "cursor.relative_gain_max": 1},
    {"cursor.relative_accel_speed_low": .8, "cursor.relative_accel_speed_high": .8},
    {"pinch.pinch_threshold": .08},
    {"motion.tab_frames": 1.5}, {"motion.tab_frames": 0},
    {"control.hold": 0}, {"control.mlp_confidence": 1.1},
])
def test_invalid_batch_cannot_partially_mutate_settings(edits):
    settings = configured()
    before = settings.stored()
    with pytest.raises(ValueError):
        prepare(settings, edits)
    assert settings.stored() == before


def test_explicit_release_and_start_can_be_tuned_together_without_hidden_overwrite():
    settings = configured()
    result = prepare(settings, {"pinch.pinch_threshold": .06, "pinch.release_threshold": .09})
    assert result == {"pinch.pinch_threshold": .06, "pinch.release_threshold": .09}
    assert settings.stored() == {}


def test_profile_overrides_are_visible_and_cannot_be_written_through_editor():
    settings = configured()
    settings.apply_profile({"cursor.scroll_gain": 45000})
    row = next(r for r in rows(settings) if r["key"] == "cursor.scroll_gain")
    assert row["value"] == 45000 and not row["editable"]
    with pytest.raises(ValueError, match="프로필"):
        prepare(settings, {"cursor.scroll_gain": 42000})
    assert settings.get("cursor.scroll_gain") == 45000


def test_reading_all_rows_keeps_unsnapped_values_and_internal_camera_values_private():
    settings = configured({"pinch.click_threshold": .0537, "v03.unclaimed": 99})
    before = settings.stored()
    data = rows(settings)
    assert next(row for row in data if row["key"] == "pinch.click_threshold")["value"] == .0537
    assert not any(row["key"].startswith(("camera.", "tracking.", "v03.")) for row in data)
    assert settings.stored() == before


def test_group_defaults_form_a_valid_batch_and_match_existing_output_defaults():
    settings = configured()
    assert prepare(settings, {row["key"]: row["defaultValue"] for row in rows(settings)})
    output = WindowsOutputConfig()
    assert all(setting.default == getattr(output, setting.key.split(".", 1)[1]) for setting in EXTRA_SETTINGS)
