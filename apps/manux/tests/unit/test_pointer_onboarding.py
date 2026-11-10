"""Automatic pointer setup through the desktop runtime and virtual target loop."""
from __future__ import annotations

import math

from manux.app import schema
from manux.core.contracts import ActionCommand, FrameInfo, HandObservation, OutputTarget, RuntimeRequest, Stage
from manux.data.pointer_setup import SETTING_KEY, read_profiles
from manux.data.tracking_test import Target, TrackingTest
from manux.desktop import build
from manux.engine.commands import SetStage
from manux.output.windows.backend import FakeBackend
from manux.output.windows.config import WindowsOutputConfig
from manux.output.windows.sink import WindowsOutputSink
from manux.platform.monitors import ScreenLayout
from manux.settings.store import Settings, load


class SyntheticSource:
    fps = 30
    source = "test camera"
    seq = 0
    point = (.5, .5)
    pitch = 0.

    def read(self, *_):
        self.seq += 1
        frame = FrameInfo(self.seq, self.seq / self.fps, 1280, 720, False)
        hand = None
        if self.point is not None:
            x, y = self.point
            # thumb tip (4) kept apart from the finger tips, or the hand would read as a held click
            landmarks = tuple((x - (.1 if i == 4 else 0.), y + (.03 if i == 0 else -.03 if i == 9 else 0.), 0.)
                              for i in range(21))
            angle = math.radians(self.pitch)
            world = ((0., 0., 0.),) + ((0., -.08 * math.sin(angle), -.08 * math.cos(angle)),) * 20
            hand = HandObservation(frame, landmarks, "Right", .95, world=world)
        return frame, None, hand, (), 2.0

    def mode_text(self):
        return "1280x720"

    def close(self):
        pass


def test_setup_measures_tests_and_only_applies_on_confirmation(tmp_path):
    settings = Settings(schema(), {"tracking.control_hand": "Right"})
    source = SyntheticSource()
    runtime = build(settings, source, windows=False)
    runtime.pointer_setup_dir = tmp_path
    backend = FakeBackend()
    screen = ScreenLayout.single(1920, 1080)
    sink = WindowsOutputSink(backend, screen, threaded=False)
    runtime.engine.sinks[OutputTarget.WINDOWS] = sink
    runtime.engine.commands.put(SetStage(Stage.TUTORIAL))
    runtime.request(RuntimeRequest("pointer_setup", "start"))
    runtime.step()
    try:
        for phase, pitch in (("rest", 0), ("horizontal", 0), ("up", 20), ("down", -15)):
            assert runtime.latest()[1].pointer_setup["phase"] == phase
            source.pitch = pitch
            runtime.request(RuntimeRequest("pointer_setup", "capture"))
            for index in range(170):
                if phase == "horizontal":
                    source.point = (.5 + .15 * math.sin(index / 10), .5)
                runtime.step()
                if runtime.latest()[1].pointer_setup["phase"] != phase:
                    break
        assert runtime.latest()[1].pointer_setup["phase"] == "aim"
        runtime.request(RuntimeRequest("pointer_setup", "adjust", {"palm_y_gain": 1.15}))
        runtime.step()
        assert runtime.latest()[1].pointer_setup["phase"] == "preview"
        runtime.request(RuntimeRequest("pointer_setup", "retest"))
        runtime.step()
        assert runtime.latest()[1].pointer_setup["phase"] == "aim"
        runtime.request(RuntimeRequest("pointer_setup", "apply"))
        runtime.step()
        assert not (tmp_path / "settings.json").exists()
        for _ in range(800):
            view = runtime.latest()[1].pointer_setup
            if view["phase"] != "aim":
                break
            tx, ty, _ = view["target"]
            source.point = (view["palm_left"] + tx * (view["palm_right"] - view["palm_left"]), .5)
            angle_fraction = .5 + (ty - .5) / view["palm_y_gain"]
            source.pitch = view["palm_up"] + angle_fraction * (view["palm_down"] - view["palm_up"])
            runtime.step()
        assert runtime.latest()[1].pointer_setup["phase"] == "review"
        assert not (tmp_path / "settings.json").exists()
        assert sink.config.palm_left == sink.config.palm_right
        assert backend.events == []
        runtime.request(RuntimeRequest("pointer_setup", "apply"))
        runtime.step()
        assert runtime.latest()[1].pointer_setup["phase"] == "saved"
        profiles = read_profiles(load(schema(), tmp_path).get(SETTING_KEY))
        assert len(profiles) == 1
        values = next(iter(profiles.values()))
        assert values["palm_y_gain"] == 1.15
        assert round(values["palm_up"]) == 20 and round(values["palm_down"]) == -15
        runtime.step()
        assert sink.config.palm_right == values["palm_right"]
        saved_profiles = settings.get(SETTING_KEY)
        runtime.request(RuntimeRequest("sensitivity", str(tmp_path), {"cursor.vertical_input": "hand_position"}))
        runtime.step()
        assert sink.config.vertical_input == "hand_position"
        assert sink.config.palm_left == sink.config.palm_right
        assert settings.get(SETTING_KEY) == saved_profiles
        assert load(schema(), tmp_path).get("cursor.vertical_input") == "hand_position"
        practice = runtime.engine.sinks[OutputTarget.PRACTICE]
        assert practice.config == sink.config
        runtime.request(RuntimeRequest("advanced", str(tmp_path), {"cursor.vertical_input": "wrist_pitch"}))
        runtime.step()
        assert sink.config.palm_right == values["palm_right"]
        assert settings.get(SETTING_KEY) == saved_profiles
        assert practice.config == sink.config
        source.source = "different camera"
        runtime.step()
        assert sink.config.palm_left == sink.config.palm_right
        source.source = "test camera"
        runtime.step()
        assert sink.config.palm_right == values["palm_right"]
        runtime.request(RuntimeRequest("sensitivity", str(tmp_path), {"cursor.smoothing": .5}))
        runtime.step()
        assert sink.config.palm_left == sink.config.palm_right
        assert sink.config.smoothing == .5
        assert read_profiles(load(schema(), tmp_path).get(SETTING_KEY)) == {}
        assert backend.events == []
    finally:
        runtime.stop()


def test_windows_palm_mapping_uses_pitch_instead_of_image_y():
    screen = ScreenLayout.single(1920, 1080)
    config = WindowsOutputConfig(palm_left=.35, palm_right=.65, palm_up=20, palm_down=-15, palm_precision=1.)
    sink = WindowsOutputSink(FakeBackend(), screen, config=config, threaded=False)

    def move(start, x, y, pitch):
        sink.set_palm_pitch(pitch)
        for i in range(30):
            sink.apply(ActionCommand(start + i / 30, "mouse.move", "hold", "pinch.move", point=(x, y)))
        return sink.cursor_pos

    assert move(1, .35, .7, 20) == (0, 0)
    assert move(2, .35, .2, 20) == (0, 0)
    assert move(3, .65, .2, -15) == (1919, 1079)
    assert move(4, .35, .9, None) == (0, 1079)


def test_lost_hand_cannot_complete_target_dwell():
    monitor = ScreenLayout.single(1920, 1080).primary
    test = TrackingTest(monitor, targets=[Target(.5, .5, 100)], dwell=.25)
    pos = monitor.center
    test.update(0., pos, hand=True, active=True)
    test.update(.1, pos, hand=False, active=True)
    test.update(.5, pos, hand=False, active=True)
    assert not test.done
    test.update(.6, pos, hand=True, active=True)
    test.update(.9, pos, hand=True, active=True)
    assert test.done


def test_fine_round_starts_through_the_runtime_request(tmp_path):
    settings = Settings(schema(), {"tracking.control_hand": "Right"})
    source = SyntheticSource()
    runtime = build(settings, source, windows=False)
    runtime.pointer_setup_dir = tmp_path
    runtime.engine.commands.put(SetStage(Stage.TUTORIAL))
    runtime.request(RuntimeRequest("pointer_setup", "start"))
    runtime.step()
    try:
        for phase, pitch in (("rest", 0), ("horizontal", 0), ("up", 20), ("down", -15)):
            source.pitch = pitch
            runtime.request(RuntimeRequest("pointer_setup", "capture"))
            for index in range(170):
                if phase == "horizontal":
                    source.point = (.5 + .15 * math.sin(index / 10), .5)
                runtime.step()
                if runtime.latest()[1].pointer_setup["phase"] != phase:
                    break
        assert runtime.latest()[1].pointer_setup["phase"] == "aim"
        assert runtime.request(RuntimeRequest("pointer_setup", "fine"))
        runtime.step()
        view = runtime.latest()[1].pointer_setup
        assert view["phase"] == "aim" and view.get("zoomed") is True
    finally:
        runtime.stop()


def test_menu_round_starts_through_the_runtime_request(tmp_path):
    settings = Settings(schema(), {"tracking.control_hand": "Right"})
    source = SyntheticSource()
    runtime = build(settings, source, windows=False)
    runtime.pointer_setup_dir = tmp_path
    runtime.engine.commands.put(SetStage(Stage.TUTORIAL))
    runtime.request(RuntimeRequest("pointer_setup", "start"))
    runtime.step()
    try:
        for phase, pitch in (("rest", 0), ("horizontal", 0), ("up", 20), ("down", -15)):
            source.pitch = pitch
            runtime.request(RuntimeRequest("pointer_setup", "capture"))
            for index in range(170):
                if phase == "horizontal":
                    source.point = (.5 + .15 * math.sin(index / 10), .5)
                runtime.step()
                if runtime.latest()[1].pointer_setup["phase"] != phase:
                    break
        assert runtime.request(RuntimeRequest("pointer_setup", "menu"))
        runtime.step()
        view = runtime.latest()[1].pointer_setup
        assert view["phase"] == "aim" and view.get("zoomed") is True
        assert runtime._pointer_setup.kind == "menu" and runtime._pointer_setup.test.dwell == 1.0
    finally:
        runtime.stop()
