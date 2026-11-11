import pytest
from pathlib import Path

from manux.data.custom_gestures import (
    load_custom_gestures, save_custom_gesture, remove_custom_gesture,
    add_custom_gesture_hook, clear_custom_gesture_hooks,
    PRESET_COMMANDS, DIRECTION_CHOICES
)
from manux.data.catalog import BY_KEY, clear_custom_tasks
from manux.actions.catalog import register_custom_action, TAP, spec
from manux.output.windows.buttons import register_chord, KEY_CHORDS


@pytest.fixture(autouse=True)
def clean_custom_state():
    clear_custom_tasks()
    clear_custom_gesture_hooks()
    def _hook(item):
        action_id = item.get("action", f"custom.{item['key']}")
        keys = item.get("keys", [])
        register_custom_action(action_id, TAP)
        register_chord(action_id, keys)
    add_custom_gesture_hook(_hook)
    yield
    clear_custom_tasks()
    clear_custom_gesture_hooks()


def test_custom_gestures_lifecycle(tmp_path):
    # 1. Initially empty
    assert load_custom_gestures(tmp_path) == []

    # 2. Save a custom gesture
    item = save_custom_gesture(
        tmp_path,
        name="창 닫기 (Ctrl+W)",
        direction="up",
        keys=["ctrl", "w"],
        instruction="손을 펴고 위로 쓸어 올리세요"
    )
    assert item["key"] == "custom_g_1"
    assert item["direction"] == "up"
    assert item["keys"] == ["ctrl", "w"]
    assert item["action"] == "custom.custom_g_1"

    # 3. Verify it is registered in catalog, buttons, and action specs
    assert item["key"] in BY_KEY
    assert spec("custom.custom_g_1") is not None
    assert KEY_CHORDS.get("custom.custom_g_1") == ("ctrl", "w")

    # 4. Load from disk
    loaded = load_custom_gestures(tmp_path)
    assert len(loaded) == 1
    assert loaded[0]["name"] == "창 닫기 (Ctrl+W)"

    # 5. Save a second custom gesture
    item2 = save_custom_gesture(
        tmp_path,
        name="새로고침 (F5)",
        direction="down",
        keys=["f5"]
    )
    assert item2["key"] == "custom_g_2"
    assert len(load_custom_gestures(tmp_path)) == 2

    # 6. Remove first gesture
    assert remove_custom_gesture(tmp_path, "custom_g_1")
    remaining = load_custom_gestures(tmp_path)
    assert len(remaining) == 1
    assert remaining[0]["key"] == "custom_g_2"
    assert "custom_g_1" not in BY_KEY


def test_presets_and_directions():
    assert len(PRESET_COMMANDS) >= 5
    assert any(c["id"] == "ctrl_w" for c in PRESET_COMMANDS)
    assert len(DIRECTION_CHOICES) == 4
