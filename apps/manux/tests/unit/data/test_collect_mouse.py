import json
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
from collect_mouse import PRESETS, Take
from manux.data import capture_plan as plans
from tcn_data import class_mapping, class_names, load, samples


def frame_hand(t):
    frame = FrameInfo(0, t, 640, 480, True)
    lm = tuple((0.3 + i * 0.01, 0.5, 0.0) for i in range(21))
    world = tuple((i * 0.001, (i % 4) * 0.001, 0.0) for i in range(21))
    return frame, HandObservation(frame, lm, "Right", 0.95, world)


def plan(**timing):
    rows = plans.preset("basic")["conditions"]
    return plans.normalise({"schema": plans.SCHEMA, "conditions": [{**row, **timing} for row in rows]})


TIMING = dict(reps=1, prep=1.0, hold=1.6, rest=1.6, free_seconds=10)


def test_presets_cover_declared_bindings_and_keep_pilot_labels():
    for preset, feature in (("pinch", pinch), ("pointer", pointer), ("trigger", pointer)):
        tasks = PRESETS[preset]
        assert len({task.key for task in tasks}) == len(tasks)
        required = set(feature.DEFAULT_BINDINGS) | set(motion.DEFAULT_BINDINGS) | {motion.ENTER}
        if preset == "pointer":
            required.remove(pointer.DRAG)  # available in trigger mode
        assert {task.gesture_id for task in tasks} == required | {None}
    primary = {task.key: task.gesture for task in PRESETS["pinch"]}
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
    capture = plan(**TIMING)
    for _ in range(2):
        take = Take(task, capture, 2, tmp_path, meta, 0.0)
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
    assert rec.frames[1]["label"]["condition_index"] == 2
    assert rec.frames[1]["label"]["condition_key"] == plans.condition_key(capture["conditions"][2])
    assert rec.meta["guided"]["capture_plan"] == capture
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
    assert task.key == "none" and task.free
    canceled = Take(task, plan(**TIMING), 0, tmp_path, {}, 0.0)
    canceled.abort()
    empty = Take(task, plan(**TIMING), 0, tmp_path, {}, 0.0)
    empty.session.update(10.1)
    assert empty.finish(10.1)[0] is None
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
    args = SimpleNamespace(profile="synthetic", preset="pinch", split="eval")
    task = next(task for task in PRESETS["pinch"] if task.key == "thumb_middle")
    monkeypatch.setattr(collect_mouse, "draw", lambda *_: None)
    try:
        collect_mouse.capture(screen, camera, tracker, engine, "Right", [(0, task)], args, tmp_path, settings,
                              plan(**TIMING))
    finally:
        engine.shutdown()
    path, = tmp_path.glob("*.jsonl.gz")
    assert read(path).meta["split"] == "eval"
    assert class_names(load([path], split="eval")) == ["none", "thumb_middle"]


def test_untracked_frames_pause_the_take_clock(tmp_path):
    task = next(task for task in PRESETS["pinch"] if task.key == "thumb_ring")
    take = Take(task, plan(**TIMING), 0, tmp_path, {}, 0.0)
    assert not take.session.update(0.9)
    assert not take.session.update(1.2, available=False)
    assert take.session.label(1.2)["target"] == "idle"
    assert not take.session.update(5.0, available=True)      # 0.9 s counted, the 4.1 s without tracking did not
    assert take.session.label(5.2)["target"] == "thumb_ring"
    take.abort()


def cli(*argv):
    parser, args = collect_mouse.parse_args(list(argv))
    return parser, args


def test_cli_list_uses_the_catalog_and_the_plan(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MANUX_DATA_DIR", str(tmp_path))
    assert collect_mouse.main(["--list", "--plan", "positions", "--focus", "v_sign", "swipe_left"]) == 0
    out = capsys.readouterr().out
    assert "v_sign" in out and "capture-only" in out and "motion.swipe_left" in out
    assert out.count("condition ") == 9 and "pose-" in out


def test_cli_plan_choices(tmp_path, capsys):
    parser, args = cli("--plan", "diverse", "--condition", "2", "1", "2", "--reps", "3")
    capture, picked = collect_mouse.resolve_plan(parser, args, tmp_path)
    assert picked == [1, 0] and {r["reps"] for r in capture["conditions"]} == {3}
    saved = plans.preset("basic")
    saved["conditions"][1]["enabled"] = False
    plans.save_plan(saved, "thumb_ring", plans.plans_file(tmp_path))
    parser, args = cli("--plan", "saved", "--focus", "thumb_ring")
    capture, picked = collect_mouse.resolve_plan(parser, args, tmp_path)
    assert picked == [0, 1] and [r["name"] for r in plans.enabled(capture)] == ["정면 · 중앙", "오른쪽 방향 · 오른쪽"]
    file = tmp_path / "one.json"
    file.write_text(json.dumps(plans.preset("positions")), encoding="utf-8")
    parser, args = cli("--plan", str(file), "--condition", "화면 위")
    assert collect_mouse.resolve_plan(parser, args, tmp_path)[1] == [2]
    for bad in (("--plan", "nowhere"), ("--condition", "9"), ("--reps", "0"), ("--free-seconds", "5")):
        parser, args = cli(*bad)
        with pytest.raises(SystemExit):
            collect_mouse.resolve_plan(parser, args, tmp_path)
    for focus in ("no_such_task", "pointer_left"):                  # pointer_left is not a pinch-preset task
        parser, args = cli("--focus", focus)
        with pytest.raises(SystemExit):
            collect_mouse.resolve_tasks(parser, args)
    parser, args = cli("--preset", "pointer", "--focus", "pointer_left", "none")
    assert [t.key for t in collect_mouse.resolve_tasks(parser, args)] == ["pointer_left", "none"]
    capsys.readouterr()
