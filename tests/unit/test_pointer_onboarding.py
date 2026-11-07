"""Automatic pointer setup through the desktop runtime and virtual target loop."""
from __future__ import annotations

import math

from manux.app import schema
from manux.core.contracts import FrameInfo, HandObservation, OutputTarget, RuntimeRequest, Stage
from manux.data.pointer_setup import SETTING_KEY, read_profiles
from manux.data.tracking_test import Target, TrackingTest
from manux.desktop import build
from manux.engine.commands import SetStage
from manux.output.windows.backend import FakeBackend
from manux.output.windows.sink import WindowsOutputSink
from manux.platform.monitors import ScreenLayout
from manux.settings.store import Settings, load


class SyntheticSource:
    fps = 30
    source = "test camera"
    seq = 0
    point = (.5, .5)

    def read(self, *_):
        self.seq += 1
        frame = FrameInfo(self.seq, self.seq / self.fps, 1280, 720, False)
        hand = None
        if self.point is not None:
            x, y = self.point
            landmarks = tuple((x, y + (.03 if i == 0 else -.03 if i == 9 else 0.), 0.) for i in range(21))
            hand = HandObservation(frame, landmarks, "Right", .95)
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
        for index in range(350):
            phase = runtime.latest()[1].pointer_setup["phase"]
            if phase == "aim":
                break
            wave = math.sin(index / 6)
            if phase == "horizontal":
                source.point = (.5 + .11 * wave, .5)
            elif phase == "vertical":
                source.point = (.5 + .014 * wave, .5 + .073 * wave)
            else:
                source.point = (.5, .5)
            runtime.step()
        assert runtime.latest()[1].pointer_setup["phase"] == "aim"
        runtime.request(RuntimeRequest("pointer_setup", "apply"))
        runtime.step()
        assert not (tmp_path / "settings.json").exists()
        for _ in range(1600):
            view = runtime.latest()[1].pointer_setup
            if view["phase"] != "aim":
                break
            tx, ty, _ = view["target"]
            cx, cy = view["cursor"]
            # Follow each target with a damped hand trajectory using actual virtual cursor feedback.
            x, y = source.point
            source.point = (x + max(-.018, min(.018, .16 * (tx - cx))),
                            y + max(-.018, min(.018, .16 * (ty - cy))))
            runtime.step()
        assert runtime.latest()[1].pointer_setup["phase"] == "review"
        assert not (tmp_path / "settings.json").exists()
        assert sink.config.relative_gain_y == 1
        assert backend.events == []
        runtime.request(RuntimeRequest("pointer_setup", "apply"))
        runtime.step()
        assert runtime.latest()[1].pointer_setup["phase"] == "saved"
        profiles = read_profiles(load(schema(), tmp_path).get(SETTING_KEY))
        assert len(profiles) == 1
        values = next(iter(profiles.values()))
        assert 1.35 < values["relative_gain_y"] < 1.75
        assert values["relative_y_cross"] > .15
        runtime.step()
        assert sink.config.relative_gain_y == values["relative_gain_y"]
        source.source = "different camera"
        runtime.step()
        assert sink.config.relative_gain_y == 1
        source.source = "test camera"
        runtime.step()
        assert sink.config.relative_gain_y == values["relative_gain_y"]
        runtime.request(RuntimeRequest("sensitivity", str(tmp_path), {"cursor.smoothing": .5}))
        runtime.step()
        assert sink.config.relative_gain_y == 1
        assert sink.config.smoothing == .5
        assert read_profiles(load(schema(), tmp_path).get(SETTING_KEY)) == {}
        assert backend.events == []
    finally:
        runtime.stop()


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
