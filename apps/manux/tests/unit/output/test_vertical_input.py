"""Synthetic hand translation and wrist rotation must remain independent inputs."""
import pytest

from manux.core.contracts import ActionCommand
from manux.output.practice import PracticeSink
from manux.output.windows.backend import FakeBackend
from manux.output.windows.config import WindowsOutputConfig
from manux.output.windows.cursor import CursorMapper
from manux.output.windows.sink import WindowsOutputSink
from manux.platform.monitors import ScreenLayout


def outputs(mode, **values):
    layout = ScreenLayout.single(1920, 1080)
    config = WindowsOutputConfig(vertical_input=mode, smoothing=1., relative_gain_min=1.,
                                 relative_gain_max=1., **values)
    windows = WindowsOutputSink(FakeBackend((960, 540)), layout, config=config, threaded=False)
    practice = PracticeSink(cursor=CursorMapper(layout, config, (960, 540)), config=config)
    return windows, practice


@pytest.mark.parametrize("mode", ["hand_position", "wrist_pitch"])
@pytest.mark.parametrize("action", ["mouse.move", "mouse.drag"])
def test_translation_rotation_and_drag_use_selected_input_in_both_outputs(mode, action):
    sinks = outputs(mode)
    try:
        for sink in sinks:
            def move(t, point, pitch, phase="hold"):
                sink.set_palm_pitch(pitch)
                result = sink.apply(ActionCommand(t, action, phase, "pinch.move", point=point))
                assert result.accepted
                return sink.cursor.pos

            start = move(1., (.5, .5), 0., "begin" if action == "mouse.drag" else "hold")
            translated = move(1.03, (.5, .54), 0.)
            assert (translated[1] > start[1]) == (mode == "hand_position")
            rotated = move(1.06, (.5, .54), 10.)
            assert (rotated[1] < translated[1]) == (mode == "wrist_pitch")
            horizontal = move(1.09, (.52, .54), 10.)
            assert horizontal[0] - rotated[0] == 38
        assert sinks[0].cursor.pos == sinks[1].cursor.pos
    finally:
        for sink in sinks:
            sink.close()


@pytest.mark.parametrize("movement", ["relative", "absolute"])
def test_hand_position_ignores_wrist_calibration_and_missing_world_landmarks(movement):
    sinks = outputs("hand_position", movement_mode=movement,
                    palm_left=.3, palm_right=.7, palm_up=20, palm_down=-15)
    try:
        for sink in sinks:
            sink.set_palm_pitch(None)
            sink.apply(ActionCommand(1., "mouse.move", "hold", "pinch.move", point=(.5, .5)))
            before = sink.cursor.pos
            sink.apply(ActionCommand(1.03, "mouse.move", "hold", "pinch.move", point=(.5, .55)))
            assert sink.cursor.pos[1] > before[1]
        assert sinks[0].cursor.pos == sinks[1].cursor.pos
    finally:
        for sink in sinks:
            sink.close()


@pytest.mark.parametrize("mode", ["hand_position", "wrist_pitch"])
def test_double_click_hold_releases_only_for_selected_vertical_input(mode):
    sink, practice = outputs(mode)
    try:
        sink.set_palm_pitch(0.)
        sink.apply(ActionCommand(1., "mouse.move", "hold", "pinch.move", point=(.5, .5)))
        sink.apply(ActionCommand(1.01, "mouse.left_click", "tap", "pinch.thumb_middle", point=(.5, .5)))
        for t, y in ((1.02, .5), (1.04, .56), (1.06, .60)):
            sink.apply(ActionCommand(t, "mouse.move", "hold", "pinch.move", point=(.5, y)))
        assert (sink.cursor.pos[1] > 540) == (mode == "hand_position")
    finally:
        sink.close()
        practice.close()


def test_index_pointer_still_uses_position_when_wrist_mode_is_selected():
    sinks = outputs("wrist_pitch")
    try:
        for sink in sinks:
            sink.set_palm_pitch(0.)
            sink.apply(ActionCommand(1., "mouse.move", "hold", "pointer.move", point=(.5, .5)))
            sink.apply(ActionCommand(1.03, "mouse.move", "hold", "pointer.move", point=(.5, .54)))
            assert sink.cursor.pos[1] > 540
    finally:
        for sink in sinks:
            sink.close()
