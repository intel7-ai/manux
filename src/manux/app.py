"""Compose the v0.4 engine for headless camera runs and recorded replay."""
from __future__ import annotations

import argparse
import json
import os
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
from manux.features import control, motion, pinch, pointer, tcn_gate
from manux.features.tcn import TcnFeature, SETTINGS as TCN_SETTINGS
from manux.ml.card import ModelError
from manux.ml.predictor import LoadedModel, load_model
from manux.ml.store import load_active
from manux.perception.local3d import FEATURES, FEATURE_VERSION
from manux.output.null import NullSink
from manux.output.practice import PracticeSink
from manux.settings.builtin import ALL
from manux.settings.sensitivity import CURSOR_SETTINGS
from manux.settings.advanced import EXTRA_SETTINGS
from manux.settings.paths import resolve
from manux.settings.schema import Schema, Setting
from manux.settings.store import Settings, load
from manux.tracking.landmarker import MediaPipeLandmarker, options_from
from manux.tracking.tracker import HandTracker

_FEATURES = (
    (control, control.ControlFeature),
    (pointer, pointer.PointerFeature),
    (pinch, pinch.PinchFeature),
    (motion, motion.MotionFeature),
)
_SETTING_TYPES = {bool: "bool", int: "int", float: "float", str: "str"}


def configure_acceleration(args, settings):
    from manux.ml.backends import DEVICES
    for attribute, variable, setting, choices in (
        ("train_device", "MANUX_TRAIN_DEVICE", "app.training_device", ("auto","cpu","cuda","xpu")),
        ("inference_device", "MANUX_INFERENCE_DEVICE", "app.inference_device", DEVICES)):
        value = getattr(args, attribute, None) or os.environ.get(variable) or settings.get(setting)
        if value not in choices: raise ValueError(f"invalid {attribute}: choose from {', '.join(choices)}")
        os.environ[variable] = value


def schema() -> Schema:
    settings = Schema(ALL + CURSOR_SETTINGS + EXTRA_SETTINGS)
    for module in [*(module for module, _ in _FEATURES), tcn_gate]:
        settings.add(Setting(key, default, kind=_SETTING_TYPES[type(default)])
                     for key, default in module.SETTINGS.items() if default is not None)
    settings.add(Setting(key, value, kind="float") for key, value in TCN_SETTINGS.items())
    return settings


def build_engine(settings: Settings, mode: str = "pointer", filters: tuple[IntentFilter, ...] = (),
                 *, recording: bool = False, direct_model: LoadedModel | None = None,
                 binding_overrides=None, policies=None) -> Engine:
    features, router, sources = pipeline_parts(direct_model, binding_overrides, policies)
    if direct_model is not None and any(getattr(f, "id", "") != "spatial_pose_guard" for f in filters):
        raise ValueError("direct mode and confirmation filters cannot be used together")
    engine = Engine(features, filters, router,
                    {OutputTarget.NONE: NullSink(), OutputTarget.PRACTICE: PracticeSink()},
                    settings.section, EngineConfig(hand_lost_reset=settings.get("app.hand_lost_reset"),
                                                   base_mode=mode, motion_quiet=settings.get("motion.enter_quiet")),
                    gesture_sources=sources)
    if recording:
        engine.commands.put(SetBusy("recording", True))
    engine.commands.put(SetStage(Stage.MAIN if recording else Stage.TUTORIAL))
    return engine


def pipeline_parts(direct_model=None, binding_overrides=None, policies=None):
    """Assemble recognizers and checked bindings; no stage or output side effects."""
    defaults = {}
    phases = {}
    for module, _ in _FEATURES:
        defaults.update(module.DEFAULT_BINDINGS)
        phases.update(module.GESTURE_PHASES)
    from manux.features.spatial_pose import DEFAULT_BINDINGS as POSE_BINDINGS, GESTURE_PHASES as POSE_PHASES
    defaults.update(POSE_BINDINGS)
    phases.update(POSE_PHASES)
    features = [factory() for _, factory in _FEATURES]
    sources = {}
    if direct_model is not None:
        direct = TcnFeature(direct_model.predictor, direct_model.card, policies)
        if control.V_SIGN in direct.authoritative:
            raise ValueError("control.v_sign uses the V hold recognizer and optional MLP, not direct TCN")
        phases.update(direct.gesture_phases)
        sources = {g: direct.id for g in direct.authoritative}
        if any(mode != "rules" for mode in direct.policies.values()):
            features.append(direct)
    bindings, problems = resolve_bindings(defaults, binding_overrides, gesture_phases=phases)
    if problems:
        raise ValueError(f"invalid bindings: {problems}")
    return features, Router(bindings), sources


def read_bindings(path: Path | None):
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("bindings must be an object mapping gesture IDs to actions")
    return data


def training_specs():
    """Gesture/phase metadata for the external trainer; actions stay in bindings."""
    result = {}
    for module, _ in _FEATURES:
        if module.ID == control.ID:
            continue  # V control has its own timed rule + optional MLP contract, not direct TCN decisions.
        mode = module.ID
        for gid, phases in module.GESTURE_PHASES.items():
            if phases == frozenset({"tap"}):
                result[gid] = {"kind": "tap", "modes": [mode], "point": "none"}
            elif phases == frozenset({"begin", "hold", "end"}):
                result[gid] = {"kind": "span", "modes": [mode], "point": "index" if mode == "pointer" else "palm"}
    return result


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
    parser.add_argument("--tcn-mode", choices=("gate", "direct"), default="gate")
    parser.add_argument("--tcn-store", type=Path, help="verified direct personal model store")
    parser.add_argument("--bindings", type=Path, help="user gesture -> action bindings JSON")
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--mode", choices=("pointer", "pinch"), default="pointer")
    parser.add_argument("--control-hand", choices=("Left", "Right"))
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--inference-device", choices=("auto","cpu","cuda","openvino-cpu","openvino-gpu","openvino-npu"))
    args = parser.parse_args()
    if args.max_frames is not None and args.max_frames < 1:
        parser.error("--max-frames must be positive")
    paths = resolve()
    settings = load(schema(), args.config_dir or paths.config_dir)
    configure_acceleration(args, settings)
    for warning in settings.warnings:
        print(warning, file=sys.stderr)
    filters = ()
    if args.tcn_store is not None and (args.tcn_model is not None or args.tcn_mode != "direct"):
        parser.error("--tcn-store requires direct mode and cannot be combined with --tcn-model")
    if args.tcn_mode == "direct" and args.tcn_model is None and args.tcn_store is None:
        parser.error("--tcn-mode direct needs --tcn-model or --tcn-store")
    if args.tcn_model is not None and args.tcn_mode == "gate":
        eligible = {gid for module, _ in _FEATURES for gid in module.DEFAULT_BINDINGS
                    if module.GESTURE_PHASES[gid] & {"tap", "begin"}}
        loaded = tcn_gate.create_gate(args.tcn_model, settings.section("tcn_gate"), eligible=eligible)
        if loaded.gate is None:
            print(f"TCN: {loaded.text}", file=sys.stderr)
        else:
            filters = (loaded.gate,)

    try:
        with ExitStack() as resources, ExitStack() as output:
            direct = None
            if args.tcn_mode == "direct":
                direct = (load_active(args.tcn_store, FEATURE_VERSION, FEATURES) if args.tcn_store is not None
                          else load_model(args.tcn_model, FEATURE_VERSION, FEATURES))
            engine = build_engine(settings, args.mode, filters, direct_model=direct,
                                  binding_overrides=read_bindings(args.bindings))
            output.callback(engine.shutdown)
            if args.replay is not None:
                snapshots = replay_snapshots(engine, args.replay)
            else:
                model_path = args.hand_model or paths.hand_model
                camera, tracker = resources.enter_context(camera_session(settings, args.source, model_path))
                hand = args.control_hand or settings.get("tracking.control_hand") or "Right"
                snapshots = camera_snapshots(engine, camera, tracker, hand)
            for index, snapshot in enumerate(snapshots, start=1):
                print(json.dumps(asdict(snapshot), ensure_ascii=True))
                if index == args.max_frames:
                    break
    except KeyboardInterrupt:
        return 130
    except (ImportError, OSError, ValueError, ModelError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
