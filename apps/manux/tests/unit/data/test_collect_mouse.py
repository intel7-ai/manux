import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from manux.app import build_engine, schema
from manux.core.contracts import FrameInfo, HandObservation, OutputTarget
from manux.data.recording import read
from manux.features import motion, pinch, pointer
from manux.settings.store import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import collect_mouse
from collect_mouse import CONDITIONS, PRESETS, Take
from tcn_data import class_mapping, class_names, load, samples


def frame_hand(t):
    frame = FrameInfo(0, t, 640, 480, True)
    lm = tuple((0.3 + i * 0.01, 0.5, 0.0) for i in range(21))
    world = tuple((i * 0.001, (i % 4) * 0.001, 0.0) for i in range(21))
    return frame, HandObservation(frame, lm, "Right", 0.95, world)


def test_presets_cover_declared_bindings_and_keep_pilot_labels():
    for preset, feature in (("pinch", pinch), ("pointer", pointer), ("trigger", pointer)):
        tasks = PRESETS[preset]
        assert len({task.key for task in tasks}) == len(tasks)
        required = set(feature.DEFAULT_BINDINGS) | set(motion.DEFAULT_BINDINGS) | {motion.ENTER}
        if preset == "pointer":
            required.remove(pointer.DRAG)  # available in trigger mode
        assert {task.spec.gesture for task in tasks} == required | {None}
    primary = {task.key: task.spec.gesture for task in PRESETS["pinch"]}
    assert primary["thumb_index"] == "pinch.scroll"
    assert primary["thumb_ring"] == "pinch.thumb_ring"
    assert primary["none"] is None


def test_recording_has_no_output_target():
    frame, hand = frame_hand(1.0)
    engine = build_engine(Settings(schema()), "pinch", recording=True)
    try:
        result = engine.step(frame, hand)
        assert result.output is OutputTarget.NONE
        assert result.busy == ("recording",)
    finally:
        engine.shutdown()


def test_completed_takes_append_and_feed_existing_tcn(tmp_path):
    task = next(task for task in PRESETS["pinch"] if task.key == "double_click")
    meta = {"person": "tester01", "preset": "pinch", "split": "train"}
    files = []
    for _ in range(2):
        take = Take(task, "camera_shift", 1, tmp_path, meta, 0.0)
        for t in (0.1, 1.05, 1.10, 1.15, 2.7, 3.2):
            take.add(*frame_hand(t), [(pinch.MOVE, "hold"), (pinch.THUMB_MIDDLE, "tap")])
        take.session.update(4.3)
        path, _ = take.finish(4.3)
        files.append(path)
    assert files[0] != files[1]
    rec = read(files[0])
    assert rec.complete and rec.meta["person"] == "tester01"
    assert [f["label"]["target"] for f in rec.frames] == [
        "transition", "thumb_middle", "thumb_middle", "thumb_middle", "transition", "none",
    ]
    assert rec.frames[1]["label"]["task"] == "double_click"
    assert rec.frames[1]["rule"]["gesture"] == pinch.THUMB_MIDDLE
    assert rec.frames[1]["label"]["condition_index"] == list(CONDITIONS).index("camera_shift")
    segments = load(files, split="train")
    classes = class_names(segments)
    assert classes == ["none", "thumb_middle"]
    assert class_mapping(segments, classes) == {"thumb_middle": pinch.THUMB_MIDDLE}
    inputs, targets = samples(segments, classes, 15)
    assert inputs.shape == (8, 15, 133)
    assert targets.tolist().count(1) == 6
    with pytest.raises(ValueError, match="expected a eval recording"):
        load(files, split="eval")


def test_canceled_or_untracked_takes_never_finalize(tmp_path):
    task = PRESETS["pinch"][0]
    canceled = Take(task, "usual", 1, tmp_path, {}, 0.0)
    canceled.abort()
    empty = Take(task, "usual", 1, tmp_path, {}, 0.0)
    empty.session.update(12.1)
    assert empty.finish(12.1)[0] is None
    assert len(list(tmp_path.glob("*.part"))) == 2
    assert not list(tmp_path.glob("*.jsonl.gz"))


def test_camera_loop_saves_a_guided_set(tmp_path, monkeypatch):
    keys = iter([32])
    screen = SimpleNamespace(
        WINDOW_NORMAL=0, WND_PROP_VISIBLE=0,
        namedWindow=lambda *_: None, imshow=lambda *_: None, destroyAllWindows=lambda: None,
        waitKey=lambda _: next(keys, -1), getWindowProperty=lambda *_: 1,
    )
    ticks = iter((0.0, 1.0, 2.05, 2.10, 2.15, 4.2, 5.3))
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    camera = SimpleNamespace(read=lambda **_: (frame_hand(next(ticks))[0], image),
                             actual_mode=lambda: (640, 480, 30.0))
    tracker = SimpleNamespace(step=lambda frame, *_: SimpleNamespace(hand=frame_hand(frame.t)[1]))
    settings = Settings(schema())
    engine = build_engine(settings, "pinch", recording=True)
    args = SimpleNamespace(profile="synthetic", preset="pinch", split="eval", condition="usual", reps=1)
    task = next(task for task in PRESETS["pinch"] if task.key == "thumb_middle")
    monkeypatch.setattr(collect_mouse, "draw", lambda *_: None)
    try:
        collect_mouse.capture(screen, camera, tracker, engine, "Right", [task], args, tmp_path, settings)
    finally:
        engine.shutdown()
    path, = tmp_path.glob("*.jsonl.gz")
    assert read(path).meta["split"] == "eval"
    assert class_names(load([path], split="eval")) == ["none", "thumb_middle"]
