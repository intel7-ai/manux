"""Integration boundaries for tuning and practice: no camera or real input."""
from dataclasses import replace
from types import SimpleNamespace

from manux.app import schema
from manux.core.contracts import ActionCommand, OutputTarget, RuntimeRequest, Stage
from manux.desktop import build
from manux.engine.commands import SetControl, SetStage
from manux.output.practice import PracticeSink
from manux.output.windows.config import WindowsOutputConfig
from manux.output.windows.cursor import CursorMapper
from manux.platform.monitors import ScreenLayout
from manux.settings.store import Settings, load


def test_practice_cursor_reuses_real_mapper_and_preserves_position_on_release():
    config = replace(WindowsOutputConfig(), relative_gain_min=.7, relative_gain_max=2.)
    sink = PracticeSink(cursor=CursorMapper(ScreenLayout.single(1920, 1080), config), config=config)
    sink.configure(config)
    mapper = CursorMapper(ScreenLayout.single(1920, 1080), config)
    for i in range(20):
        point, now, gain = (.3 + i*.006, .4 + i*.002), 1 + i/30, .35
        result = sink.apply(ActionCommand(now, "mouse.move", "hold", "pinch.move", point, {"gain": gain}))
        assert result.accepted
        assert sink.cursor.pos == mapper.step(point, now, gain)
    assert sink.precision
    pos = sink.cursor_position
    sink.release_all("settings")
    assert sink.cursor_position == pos and not sink.precision
    assert not sink.cursor.anchored and not sink.holding()


def test_advanced_save_changes_live_and_practice_config_atomically(tmp_path, monkeypatch):
    import manux.settings.store as store
    settings = Settings(schema())
    runtime = build(settings, SimpleNamespace(close=lambda: None), windows=False)
    try:
        runtime.engine.commands.put(SetStage(Stage.MAIN))
        runtime.engine.commands.put(SetControl(True))
        request = RuntimeRequest("advanced", str(tmp_path), {"cursor.scroll_inertia": False, "cursor.scroll_gain": 24000.})
        runtime.request(request)
        runtime._drain_runtime_requests()
        assert not runtime.engine.state.control_on
        assert settings.get("cursor.scroll_gain") == 24000.
        assert not runtime.engine.sinks[OutputTarget.PRACTICE].config.scroll_inertia
        assert load(schema(), tmp_path).get("cursor.scroll_gain") == 24000.
        stored = (tmp_path / "settings.json").read_bytes()
        monkeypatch.setattr(store, "save", lambda *_: (_ for _ in ()).throw(OSError("synthetic failure")))
        runtime.request(RuntimeRequest("advanced", str(tmp_path), {"cursor.scroll_gain": 10000.}))
        runtime._drain_runtime_requests()
        assert runtime.advanced_result[1] is False
        assert settings.get("cursor.scroll_gain") == 24000.
        assert (tmp_path / "settings.json").read_bytes() == stored
    finally:
        runtime.stop()


def test_invalid_advanced_pair_leaves_entire_edit_unapplied(tmp_path):
    settings = Settings(schema())
    runtime = build(settings, SimpleNamespace(close=lambda: None), windows=False)
    before = settings.stored()
    try:
        runtime.request(RuntimeRequest("advanced", str(tmp_path), {
            "cursor.relative_gain_min": 2., "cursor.relative_gain_max": 1., "cursor.scroll_inertia": False}))
        runtime._drain_runtime_requests()
        assert not runtime.advanced_result[1]
        assert settings.stored() == before
        assert not (tmp_path / "settings.json").exists()
    finally:
        runtime.stop()


def test_optional_v_model_reconnects_after_pipeline_replacement():
    from manux.app import pipeline_parts
    model = SimpleNamespace(classes=("none", "v_sign"), metadata={})
    settings = Settings(schema())
    runtime = build(settings, SimpleNamespace(close=lambda: None), windows=False, spatial_pose_model=model)
    try:
        runtime.request(RuntimeRequest("spatial_guard", "on"))
        runtime._drain_runtime_requests()
        runtime._sync_control_model()
        control = next(f for f in runtime.engine.features if f.id == "control")
        assert control.model is model
        parts = pipeline_parts()
        runtime.engine.replace_pipeline(parts[0], (), parts[1], parts[2])
        runtime._sync_control_model()
        replacement = next(f for f in runtime.engine.features if f.id == "control")
        assert replacement is not control and replacement.model is model
    finally:
        runtime.stop()


def test_practice_click_survives_latest_frame_replacement_and_reset_clears_it():
    from manux.actions.bindings import Binding
    from manux.actions.router import Router
    from manux.core.contracts import Activity, FrameInfo, HandObservation, Intent
    from manux.engine.commands import ResetPractice
    class Source:
        fps = 60.
        seq = 0
        def read(self, *_):
            frame = FrameInfo(self.seq, 1+self.seq/60, 640, 480, True)
            self.seq += 1
            hand = HandObservation(frame, ((.5, .5, 0.),)*21, "Right", .9)
            return frame, None, hand, (), 0.
        def mode_text(self): return "synthetic"
        def close(self): pass
    class Feature:
        id, modes, gestures = "script", {"pinch"}, ("script.click", "script.move")
        def step(self, hand, ctx):
            if ctx.frame.seq == 0:
                return (Intent(ctx.frame.t, self.id, "script.click", "tap"),)
            return (Intent(ctx.frame.t, self.id, "script.move", "hold", point=(.7, .7)),)
        def activity(self): return Activity()
        def reset(self, reason): pass
    runtime = build(Settings(schema()), Source(), windows=False)
    runtime.engine.replace_pipeline([Feature()], [], Router({
        "script.click": Binding("mouse.left_click"), "script.move": Binding("mouse.move")}), {})
    try:
        runtime.engine.commands.put(SetStage(Stage.TUTORIAL))
        runtime.step()
        runtime.step()  # The UI never reads the click frame.
        _, view = runtime.latest()
        assert view.actions == ("mouse.move",)
        assert len(view.practice_events) == 1
        event = view.practice_events[0]
        assert event.actions == ("mouse.left_click",) and event.cursor == (960/1919, 540/1079)
        epoch = view.practice_epoch
        runtime.engine.commands.put(ResetPractice())
        runtime.step()
        _, reset = runtime.latest()
        assert reset.practice_epoch == epoch + 1 and reset.practice_events == ()
    finally:
        runtime.stop()
