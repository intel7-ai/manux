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


def test_a_missing_camera_is_a_message_not_a_crash(tmp_path):
    settings = Settings(schema())
    source = CameraInput(settings, tmp_path / "missing.task", source="99")
    runtime = build(settings, source, windows=False)
    runtime.engine.commands.put(SetControl(True))
    runtime.step()
    _, view = runtime.latest()
    assert not view.camera_ok and view.message
    runtime.stop()
