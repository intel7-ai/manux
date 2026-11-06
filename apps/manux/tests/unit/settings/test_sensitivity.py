import pytest

from manux.app import schema
from manux.output.windows.config import WindowsOutputConfig
from manux.settings.sensitivity import CURSOR_SETTINGS, prepare, rows
from manux.settings.store import Settings


def test_editor_preserves_existing_values_and_cursor_defaults():
    settings = Settings(schema(), {"pinch.click_threshold": .0537, "unclaimed.value": 123})
    before = settings.stored()
    assert next(r["value"] for r in rows(settings) if r["key"] == "pinch.click_threshold") == .0537
    assert prepare(settings, {"cursor.smoothing": .67}) == {"cursor.smoothing": .67}
    assert settings.stored() == before
    config = WindowsOutputConfig()
    assert all(s.default == getattr(config, s.key.split(".", 1)[1]) for s in CURSOR_SETTINGS)


def test_dan_coupled_threshold_and_gains():
    settings = Settings(schema())
    assert prepare(settings, {"pinch.pinch_threshold": .0714}) == {
        "pinch.pinch_threshold": .071, "pinch.release_threshold": .091}
    assert prepare(settings, {"cursor.relative_gain_min": 2.5}) == {
        "cursor.relative_gain_min": 2.5, "cursor.relative_gain_max": 2.5}
    settings.set("cursor.relative_gain_min", 2.)
    assert prepare(settings, {"cursor.relative_gain_max": 1.}) == {
        "cursor.relative_gain_min": 1., "cursor.relative_gain_max": 1.}


@pytest.mark.parametrize("edits", [
    {"camera.source": 1}, {"pinch.click_threshold": float("nan")},
    {"pinch.click_threshold": float("inf")}, {"pinch.click_threshold": True},
    {"pinch.click_threshold": .15}, {"cursor.smoothing": 0},
    {"cursor.relative_gain_min": 3, "cursor.relative_gain_max": 1},
])
def test_invalid_edits_are_rejected_as_a_whole(edits):
    settings = Settings(schema())
    with pytest.raises(ValueError):
        prepare(settings, edits)
    assert settings.stored() == {}


def test_profile_values_and_coupled_overrides_cannot_be_silently_overwritten():
    settings = Settings(schema())
    settings.apply_profile({"pinch.click_threshold": .08, "pinch.release_threshold": .10})
    assert not next(r["editable"] for r in rows(settings) if r["key"] == "pinch.click_threshold")
    for edits in ({"pinch.click_threshold": .07}, {"pinch.pinch_threshold": .07}):
        with pytest.raises(ValueError, match="프로필"):
            prepare(settings, edits)
    assert settings.get("pinch.release_threshold") == .10
