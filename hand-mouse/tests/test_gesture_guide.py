from src import gesture_guide
from src.gesture_engine import DEFAULT_CONFIG


def test_every_palm_card_but_move_can_be_switched_and_back():
    config = {"gestures": {}, "long_press_drag": True}
    for card in gesture_guide.PALM_CARDS:
        key = card["key"]
        if key == "move":
            assert not gesture_guide.set_card_enabled(config, key, False)    # the cursor always moves
            continue
        assert gesture_guide.card_enabled(config, key)
        assert gesture_guide.set_card_enabled(config, key, False)
        assert not gesture_guide.card_enabled(config, key)
        gesture_guide.set_card_enabled(config, key, True)
        assert gesture_guide.card_enabled(config, key)


def test_switches_name_real_engine_settings():
    for key, switches in gesture_guide.SWITCHES.items():
        for kind, name in switches:
            if kind == "gesture":
                assert name in DEFAULT_CONFIG["gestures"], (key, name)
            else:
                assert name in DEFAULT_CONFIG, (key, name)


def test_drag_off_also_stops_the_long_press_drag():
    config = {"gestures": {"thumb_index_middle_pinch": {"enabled": True}}, "long_press_drag": True}
    gesture_guide.set_card_enabled(config, "drag", False)
    assert config["long_press_drag"] is False and config["gestures"]["thumb_index_middle_pinch"]["enabled"] is False
