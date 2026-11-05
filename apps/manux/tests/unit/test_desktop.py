"""Desktop runtime without Qt or a camera: replay input -> engine -> ViewFrame, and a missing camera."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from manux.app import schema
from manux.core.contracts import Stage
from manux.desktop import CameraInput, DesktopRuntime, ReplayInput, build
from manux.engine.commands import SetControl, SetStage
from manux.settings.store import Settings

OPEN = [[0.5 + 0.04 * ((i % 5) - 2), 0.5 + 0.05 * ((i // 5) - 2), 0.0] for i in range(21)]


def recording(path: Path, frames: int = 40) -> Path:
    lines = [{"type": "meta", "schema": "hand-mouse-recording/1", "mode": "free",
              "camera": {"mode": [640, 480, 30.0], "mirror": True}}]
    for i in range(frames):
        lm = [[x + 0.004 * i, y, z] for x, y, z in OPEN]
        lines.append({"type": "frame", "t": i / 30, "lm": lm, "hand": ["Left", 0.95], "label": {"target": "none"}})
    lines.append({"type": "end", "frames": frames})
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(x) for x in lines) + "\n")
    return path


def run_frames(runtime: DesktopRuntime, n: int):
    views = []
    for _ in range(n * 4):
        runtime.step()
        seq, view = runtime.latest()
        if view.snapshot is not None and (not views or views[-1] is not view):
            views.append(view)
        if len(views) >= n:
            break
    return views


def test_replay_reaches_the_engine_and_publishes_view_frames(tmp_path):
    settings = Settings(schema(), {"tracking.control_hand": "Right"})
    runtime = build(settings, ReplayInput(recording(tmp_path / "r.jsonl.gz")), windows=False)
    runtime.engine.commands.put(SetStage(Stage.TUTORIAL))          # practice output
    views = run_frames(runtime, 20)
    assert views and all(v.snapshot.stage is Stage.TUTORIAL for v in views)
    assert all(v.landmarks is not None and v.hands[0].side == "Right" for v in views)   # mirrored label flipped
    assert any("mouse.move" in v.actions for v in views)                              # an open hand moves
    runtime.engine.shutdown()


def test_pointer_gain_and_overrides(tmp_path):
    settings = Settings(schema())
    runtime = build(settings, ReplayInput(recording(tmp_path / "r.jsonl.gz")), windows=False)
    runtime.set_pointer_gain(1.5)
    assert runtime.engine.router.bindings["pointer.move"].params["gain"] == 1.5
    runtime.set_override("pointer", "axes", (1.0, 0.0, 0.0, -1.0))
    assert runtime.settings_for("pointer")["axes"] == (1.0, 0.0, 0.0, -1.0)
    assert runtime.engine.settings_for("pointer")["axes"] == (1.0, 0.0, 0.0, -1.0)


def test_runtime_reports_capture_to_publish_age_separately_from_inference(monkeypatch):
    import pytest
    import manux.desktop as desktop
    from manux.core.contracts import FrameInfo
    class Source:
        fps = 30.0
        def read(self, *args):
            return FrameInfo(0, 100.0, 640, 480, True), None, None, (), 7.0
        def mode_text(self): return "synthetic"
        def close(self): pass
    runtime = build(Settings(schema()), Source(), windows=False)
    monkeypatch.setattr(desktop.time, "perf_counter", lambda: 100.2)
    try:
        runtime.step()
        _, view = runtime.latest()
        assert view.frame_age_ms == pytest.approx(200.0)
        assert view.detect_ms == 7.0 and view.fps == 30.0
    finally:
        runtime.stop()


def test_a_missing_camera_is_a_message_not_a_crash(tmp_path):
    settings = Settings(schema())
    source = CameraInput(settings, tmp_path / "missing.task", source="99")
    runtime = build(settings, source, windows=False)
    runtime.engine.commands.put(SetControl(True))
    runtime.step()
    _, view = runtime.latest()
    assert not view.camera_ok and view.message
    runtime.stop()


def windows_runtime(tmp_path, settings):
    from manux.core.contracts import OutputTarget
    from manux.output.windows import WindowsOutputSink
    from manux.output.windows.backend import FakeBackend
    from manux.platform.monitors import ScreenLayout
    runtime = build(settings, ReplayInput(recording(tmp_path / "r.jsonl.gz", frames=60)), windows=False)
    backend = FakeBackend((960, 540))
    runtime.engine.sinks[OutputTarget.WINDOWS] = WindowsOutputSink(backend, ScreenLayout.single(1920, 1080), threaded=False)
    return runtime, backend


def test_tracking_test_needs_windows_output(tmp_path):
    runtime = build(Settings(schema()), ReplayInput(recording(tmp_path / "r.jsonl.gz")), windows=False)
    assert runtime.start_tracking_test()            # a reason, not started
    runtime.step()
    assert runtime.tracking is None


def test_tracking_test_runs_on_the_runtime_thread_and_never_clicks(tmp_path):
    from manux.engine.commands import SetBaseMode
    settings = Settings(schema(), {"tracking.control_hand": "Right"})
    runtime, backend = windows_runtime(tmp_path, settings)
    runtime.log_dir = tmp_path / "logs"
    runtime.engine.commands.put(SetStage(Stage.MAIN))
    runtime.engine.commands.put(SetBaseMode("pointer"))
    assert runtime.start_tracking_test() == ""
    views = run_frames(runtime, 5)
    assert runtime.tracking is not None and runtime.settings_for("pointer")["tracking_only"] is True
    assert views[-1].tracking is not None and views[-1].tracking["paused"]       # starts paused (control off)
    runtime.engine.commands.put(SetControl(True))
    views = run_frames(runtime, 30)
    assert not views[-1].tracking["paused"] and runtime.tracking.frames > 0
    assert not any(e[0] == "button" for e in backend.events)                    # tracking only: no clicks
    assert any(e[0] == "move" for e in backend.events)                         # ... but the cursor follows
    runtime.tracking.index = len(runtime.tracking.targets)                     # finish: results are written once
    runtime.tracking.finished_at = runtime.tracking.started_at
    views = run_frames(runtime, 2)
    assert views[-1].tracking["done"] and runtime.tracking.saved is not None
    assert runtime.tracking.saved.parent == tmp_path / "logs" and runtime.recent
    runtime.close_tracking_test()
    views = run_frames(runtime, 2)
    assert runtime.tracking is None and views[-1].tracking is None
    assert runtime.settings_for("pointer")["tracking_only"] is False
    runtime.engine.shutdown()


def test_guided_recording_runs_with_output_none_and_writes_recording(tmp_path):
    from manux.core.contracts import OutputTarget
    from manux.data.recording import read
    settings = Settings(schema(), {"tracking.control_hand": "Right"})
    runtime, backend = windows_runtime(tmp_path, settings)
    runtime.recordings_dir = tmp_path / "recordings"
    runtime.engine.commands.put(SetStage(Stage.MAIN))
    runtime.engine.commands.put(SetControl(True))
    runtime.set_recording("guided", focus="thumb_ring")
    views = run_frames(runtime, 4)
    assert views[-1].rec is not None and views[-1].rec["waiting"] is True
    assert views[-1].snapshot.output is OutputTarget.NONE
    assert "recording" in views[-1].snapshot.busy
    assert not backend.events                                   # recording disables real Windows output
    runtime.advance_recording()
    views = run_frames(runtime, 6)
    assert views[-1].rec is not None and views[-1].rec["waiting"] is False
    assert views[-1].rec["frames"] > 0
    runtime.set_recording_paused(True)
    views = run_frames(runtime, 2)
    assert views[-1].rec["paused"] is True
    runtime.set_recording("off")
    views = run_frames(runtime, 2)
    saved = list((tmp_path / "recordings" / "profile-local" / "pinch" / "train").glob("*.jsonl.gz"))
    assert len(saved) == 1
    meta = read(saved[0]).meta
    assert (meta["person"], meta["split"], meta["preset"]) == ("local", "train", "pinch")
    rec = read(saved[0])
    assert rec.complete and rec.meta["focus"] == "thumb_ring"
    runtime.stop()

def test_recording_start_failure_is_visible_and_can_retry(tmp_path, monkeypatch):
    import manux.desktop as desktop
    runtime = build(Settings(schema()), ReplayInput(recording(tmp_path / 'input.jsonl.gz')), windows=False)
    runtime.recordings_dir = tmp_path / 'recordings'
    runtime.engine.commands.put(SetStage(Stage.MAIN))
    original = desktop.SessionRecorder
    def fail(*args, **kwargs): raise OSError('synthetic disk error')
    monkeypatch.setattr(desktop, 'SessionRecorder', fail)
    runtime.set_recording('guided', focus='thumb_ring', profile='synthetic', split='eval')
    views = run_frames(runtime, 2)
    assert views[-1].rec['mode'] == 'off'
    assert '권한' in views[-1].rec['sub']
    assert not views[-1].snapshot.control_on and 'recording' not in views[-1].snapshot.busy
    monkeypatch.setattr(desktop, 'SessionRecorder', original)
    runtime.set_recording('guided', focus='thumb_ring', profile='synthetic', split='eval')
    assert run_frames(runtime, 2)[-1].rec['mode'] == 'guided'
    runtime.stop()
