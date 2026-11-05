"""Compose the v0.4 engine for headless camera runs and recorded replay."""
from __future__ import annotations

import argparse
import json
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from pathlib import Path
from time import perf_counter, sleep
from typing import Iterator

from manux.actions.bindings import resolve_bindings
from manux.actions.router import Router
from manux.capture.camera import CameraConfig, LatestFrameCamera
from manux.core.contracts import IntentFilter, OutputTarget, Stage
from manux.data.recording import read, replay
from manux.engine.commands import SetBusy, SetStage
from manux.engine.pipeline import Engine, EngineConfig
from manux.engine.snapshot import Snapshot
from manux.features import motion, pinch, pointer, tcn_gate
from manux.output.null import NullSink
from manux.output.practice import PracticeSink
from manux.settings.builtin import ALL
from manux.settings.paths import resolve
from manux.settings.schema import Schema, Setting
from manux.settings.store import Settings, load
from manux.tracking.landmarker import MediaPipeLandmarker, options_from
from manux.tracking.tracker import HandTracker

_FEATURES = (
    (pointer, pointer.PointerFeature),
    (pinch, pinch.PinchFeature),
    (motion, motion.MotionFeature),
)
_SETTING_TYPES = {bool: "bool", int: "int", float: "float", str: "str"}


def schema() -> Schema:
    settings = Schema(ALL)
    for module in [*(module for module, _ in _FEATURES), tcn_gate]:
        settings.add(Setting(key, default, kind=_SETTING_TYPES[type(default)])
                     for key, default in module.SETTINGS.items() if default is not None)
    return settings


def build_engine(settings: Settings, mode: str = "pointer", filters: tuple[IntentFilter, ...] = (),
                 *, recording: bool = False) -> Engine:
    defaults = {}
    phases = {}
    for module, _ in _FEATURES:
        defaults.update(module.DEFAULT_BINDINGS)
        phases.update(module.GESTURE_PHASES)
    bindings, problems = resolve_bindings(defaults, gesture_phases=phases)
    if problems:
        raise ValueError(f"invalid feature defaults: {problems}")
    engine = Engine([factory() for _, factory in _FEATURES], filters, Router(bindings),
                    {OutputTarget.NONE: NullSink(), OutputTarget.PRACTICE: PracticeSink()},
                    settings.section, EngineConfig(hand_lost_reset=settings.get("app.hand_lost_reset"),
                                                   base_mode=mode, motion_quiet=settings.get("motion.enter_quiet")))
    if recording:
        engine.commands.put(SetBusy("recording", True))
    engine.commands.put(SetStage(Stage.MAIN if recording else Stage.TUTORIAL))
    return engine


def replay_snapshots(engine: Engine, path: Path) -> Iterator[Snapshot]:
    recording = read(path)
    if not recording.complete or recording.bad_lines:
        raise ValueError("replay requires a complete recording")
    for item in replay(recording):
        yield engine.step(item.frame, item.hand)


def camera_snapshots(engine: Engine, camera: LatestFrameCamera, tracker: HandTracker,
                     control_hand: str) -> Iterator[Snapshot]:
    while True:
        item = camera.read(timeout=0.05)
        if item is None:
            engine.frame_missing(perf_counter())
            sleep(0.01)
            continue
        frame, image = item
        yield engine.step(frame, tracker.step(frame, image, control_hand).hand)


def _camera_config(settings: Settings, source: str | None) -> CameraConfig:
    options = settings.section("camera")
    return CameraConfig(source=options["source"] if source is None else source, width=options["width"],
                        height=options["height"], fps=options["fps"], mirror=options["mirror"],
                        rotation=int(options["rotation"]))


@contextmanager
def camera_session(settings: Settings, source: str | None, model_path: Path):
    with ExitStack() as resources:
        camera = LatestFrameCamera(_camera_config(settings, source))
        resources.callback(camera.release)
        if not camera.open():
            raise OSError("could not open the camera")
        track = settings.section("tracking")
        landmarker = MediaPipeLandmarker(model_path, **options_from(track))
        resources.callback(landmarker.close)
        tracker = HandTracker(landmarker, use_crop=track["hand_crop"], min_score=track["min_hand_score"])
        yield camera, tracker


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--replay", type=Path)
    inputs.add_argument("--camera", action="store_true")
    parser.add_argument("--source", help="camera index or network stream URL")
    parser.add_argument("--hand-model", type=Path, help="MediaPipe hand_landmarker.task")
    parser.add_argument("--tcn-model", type=Path, help="optional TCN .onnx file with its JSON card")
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--mode", choices=("pointer", "pinch"), default="pointer")
    parser.add_argument("--control-hand", choices=("Left", "Right"))
    parser.add_argument("--max-frames", type=int)
    args = parser.parse_args()
    if args.max_frames is not None and args.max_frames < 1:
        parser.error("--max-frames must be positive")
    paths = resolve()
    settings = load(schema(), args.config_dir or paths.config_dir)
    for warning in settings.warnings:
        print(warning, file=sys.stderr)
    filters = ()
    if args.tcn_model is not None:
        eligible = {gid for module, _ in _FEATURES for gid in module.DEFAULT_BINDINGS
                    if module.GESTURE_PHASES[gid] & {"tap", "begin"}}
        loaded = tcn_gate.create_gate(args.tcn_model, settings.section("tcn_gate"), eligible=eligible)
        if loaded.gate is None:
            print(f"TCN: {loaded.text}", file=sys.stderr)
        else:
            filters = (loaded.gate,)

    try:
        with ExitStack() as resources, ExitStack() as output:
            engine = build_engine(settings, args.mode, filters)
            output.callback(engine.shutdown)
            if args.replay is not None:
                snapshots = replay_snapshots(engine, args.replay)
            else:
                model_path = args.hand_model or (paths.data_dir / "models" / "hand_landmarker.task")
                camera, tracker = resources.enter_context(camera_session(settings, args.source, model_path))
                hand = args.control_hand or settings.get("tracking.control_hand") or "Right"
                snapshots = camera_snapshots(engine, camera, tracker, hand)
            for index, snapshot in enumerate(snapshots, start=1):
                print(json.dumps(asdict(snapshot), ensure_ascii=True))
                if index == args.max_frames:
                    break
    except KeyboardInterrupt:
        return 130
    except (ImportError, OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
