"""tools/train_pose.py: the pure parts always, the model / ONNX parts when the `train` extra is installed."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import pose_data  # noqa: E402
import train_pose as tp  # noqa: E402
from pose_recordings import POSES, Run, standard_runs, write_recording  # noqa: E402

from manux.ml.card import validate_card  # noqa: E402
from manux.perception.pose_angles import FEATURE_VERSION, FEATURES  # noqa: E402


# ---------------------------------------------------------------- pure parts

def test_penalty_arguments():
    assert tp.parse_penalties(["thumb_ring=1.65", "none=2"]) == {"thumb_ring": 1.65, "none": 2.0}
    for bad in ("thumb_ring", "thumb_ring=0.5", "thumb_ring=x", "Bad=2", "=2", "a=500", "a=nan"):
        with pytest.raises(ValueError):
            tp.parse_penalties([bad])


def test_class_weights():
    counts = np.array([100, 400, 25])
    assert tp.class_weights(counts, "none") is None
    sqrt, balanced = tp.class_weights(counts, "sqrt"), tp.class_weights(counts, "balanced")
    assert sqrt.mean() == pytest.approx(1.0) and balanced.mean() == pytest.approx(1.0)
    assert balanced[2] > balanced[0] > balanced[1] and sqrt[2] > sqrt[1]
    assert (balanced[2] / balanced[1]) > (sqrt[2] / sqrt[1])                 # balanced evens out harder


def test_the_card_is_a_valid_one_frame_pose_card():
    classes = ["none", "thumb_ring"]
    card = tp.build_card(classes, {"thumb_ring": "pinch.thumb_ring", "other": "x.y"}, [{"file": "a"}], [{"axis": "segment"}],
                         kind="personal", threshold=0.9, frames=3, warnings=["w"], per_class_segments={"none": 9},
                         split="segments", settings={"epochs": 1})
    parsed = validate_card(card, FEATURE_VERSION, FEATURES)
    assert parsed.window == 1 and parsed.features == 33 and parsed.feature_version == "pose-angles-v1"
    assert card["role"] == "pose_evidence" and parsed.gestures == {"thumb_ring": "pinch.thumb_ring"}   # classes only
    assert card["pose"]["idle_class"] == "none" and card["pose"]["warnings"] == ["w"] and card["pose"]["level"] == "frame"
    assert "decisions" not in card                                            # evidence, not a direct decider
    assert "click" not in json.dumps(card) and "mouse." not in json.dumps(card)   # no action names (D-004)


def test_the_card_has_no_idle_class_when_there_is_no_none_class():
    card = tp.build_card(["a", "b"], {}, [], [], kind="base", threshold=0.9, frames=3, warnings=[],
                         per_class_segments={}, split="segments", settings={})
    assert card["pose"]["idle_class"] is None


# ---------------------------------------------------------------- the model (needs torch / onnx)

torch = pytest.importorskip("torch")
pytest.importorskip("onnx")
ort = pytest.importorskip("onnxruntime")

from manux.features.pose import create_pose_assist  # noqa: E402
from manux.ml.predictor import load_model  # noqa: E402


def recordings(tmp_path, per_class=10, classes=("none", "thumb_ring", "thumb_index"), person="tester01", seed=0):
    return write_recording(tmp_path / f"{person}.jsonl.gz", standard_runs(per_class, 24, classes), person=person, seed=seed)


def run_tool(tmp_path, *args, name="pose.onnx", train=None, capsys=None):
    out = tmp_path / "out" / name
    tp.main([str(train or recordings(tmp_path)), "--output", str(out), "--epochs", "25", *args])
    return out


def test_training_exports_a_model_the_app_loads_and_a_card_that_describes_what_was_held_out(tmp_path, capsys):
    out = run_tool(tmp_path)
    card = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert card["classes"] == ["none", "thumb_index", "thumb_ring"] and card["window"] == 1 and card["features"] == 33
    assert card["role"] == "pose_evidence" and card["model_sha256"] and card["pose"]["split"] == "segments"
    (result,) = card["held_out_results"]
    assert result["status"] == "evaluated" and result["level"] == "frame" and result["accuracy"] > 0.95
    assert {v["gesture_class"] for v in result["veto"]} == {"thumb_index", "thumb_ring"}
    assert card["pose"]["training"]["held_out_segments"] > 0 and card["gestures"]["thumb_ring"] == "pinch.thumb_ring"
    model = load_model(out, FEATURE_VERSION, FEATURES)                       # the real ml loader and onnxruntime
    assert model.card.window == 1 and tuple(model.predictor.classes) == tuple(card["classes"])
    load = create_pose_assist(out)
    assert load.assist is not None and load.reason == "ok"


def test_the_exported_model_gives_the_same_probabilities_as_the_trained_one(tmp_path):
    loaded = pose_data.load_segments([recordings(tmp_path)])
    classes = pose_data.class_names(loaded.segments)
    x, y = pose_data.stack(loaded.segments, classes)
    model = tp.train(x, y, classes, epochs=15)
    out = tmp_path / "o" / "m.onnx"
    tp.export_onnx(model, out)
    session = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])
    logits = np.concatenate([session.run(None, {"features": x[i:i + 1][None]})[0] for i in range(0, 200)])
    probs = np.exp(logits - logits.max(axis=1, keepdims=True))
    probs /= probs.sum(axis=1, keepdims=True)
    np.testing.assert_allclose(probs, tp.predict(model, x[:200]), atol=1e-4)


def test_the_trained_model_speaks_through_the_feature_and_the_guard_on_new_hands(tmp_path):
    """S2 + S3 + S4 on synthetic hands: evidence follows the pose, and a contradicting pose vetoes."""
    from manux.core.contracts import FrameContext, FrameInfo, HandObservation, OutputTarget, Stage, Intent
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perception"))
    import pose_hands as H

    out = run_tool(tmp_path, train=recordings(tmp_path, per_class=12, classes=("none", "thumb_ring", "thumb_index")))
    assist = create_pose_assist(out, {"pose_assist.k_frames": 3}).assist
    assert assist is not None and assist.guard.veto_gestures == ("pinch.thumb_ring",)

    def frame(name, k):
        t = k / 30
        info = FrameInfo(seq=k, t=t, width=1280, height=720, mirrored=True)
        world = tuple(tuple(p) for p in H.move(H.make_hand(POSES[name]), H.rotation(0.3, 0.2, -0.4), 1.2, (0.1, 0.0, 0.2)))
        hand = HandObservation(info, [(0.5, 0.5, 0.0)] * 21, "Right", 0.9, world=world)
        ctx = FrameContext(info, Stage.MAIN, OutputTarget.WINDOWS, "pinch")
        return hand, ctx

    seen = {}
    k = 0
    for name in ("none", "thumb_ring", "thumb_index"):
        for _ in range(5):
            k += 1
            hand, ctx = frame(name, k)
            assist.feature.step(hand, ctx)
            assist.guard.observe(hand, ctx)
            seen[name] = assist.feature.evidence.top if assist.feature.evidence else None
        if name == "thumb_index":                                    # the pose is another gesture's: a right click is vetoed
            tap = Intent(t=ctx.frame.t, source="pinch", gesture="pinch.thumb_ring", phase="tap")
            assert assist.guard.filter([tap], ctx) == []
        if name == "thumb_ring":                                     # the pose agrees: it passes
            tap = Intent(t=ctx.frame.t, source="pinch", gesture="pinch.thumb_ring", phase="tap")
            assert assist.guard.filter([tap], ctx) == [tap]
    assert seen == {"none": "none", "thumb_ring": "thumb_ring", "thumb_index": "thumb_index"}


def test_held_out_recordings_must_not_be_training_recordings(tmp_path):
    train = recordings(tmp_path, person="p1")
    with pytest.raises(SystemExit) as stop:
        tp.main([str(train), "--eval", str(train), "--output", str(tmp_path / "o" / "m.onnx")])
    assert stop.value.code == 1


def test_separate_evaluation_recordings_are_used_as_the_held_out_side(tmp_path):
    train = recordings(tmp_path, person="p1", seed=1)
    held = write_recording(tmp_path / "h.jsonl.gz", standard_runs(6, 24), person="p2", split="eval", seed=5)
    out = tmp_path / "o" / "m.onnx"
    tp.main([str(train), "--eval", str(held), "--output", str(out), "--epochs", "20"])
    card = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert card["pose"]["split"] == "eval-recordings" and card["held_out_results"][0]["accuracy"] > 0.9


def test_few_segments_are_reported_in_the_card_and_strict_refuses(tmp_path, capsys):
    train = write_recording(tmp_path / "few.jsonl.gz", standard_runs(3, 20), person="p1")
    out = tmp_path / "o" / "m.onnx"
    tp.main([str(train), "--output", str(out), "--epochs", "5"])
    card = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert any("independent training segment" in w for w in card["pose"]["warnings"])
    assert "warning:" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        tp.main([str(train), "--output", str(tmp_path / "o" / "s.onnx"), "--epochs", "5", "--strict"])


def test_without_eval_recordings_the_same_session_leak_is_warned_about(tmp_path, capsys):
    out = run_tool(tmp_path)
    card = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert any("same session" in w for w in card["pose"]["warnings"]) and "same session" in capsys.readouterr().err
    assert all(src["person"] and src["person"] != "tester01" for src in card["data"])   # an alias, not the name
    held = write_recording(tmp_path / "h.jsonl.gz", standard_runs(6, 24), person="p2", split="eval", seed=5)
    out2 = tmp_path / "o2" / "m.onnx"
    tp.main([str(recordings(tmp_path, person="p1", seed=1)), "--eval", str(held), "--output", str(out2), "--epochs", "5"])
    assert not any("same session" in w for w in json.loads(out2.with_suffix(".json").read_text(encoding="utf-8"))["pose"]["warnings"])


def test_a_model_must_not_be_written_inside_the_repository(tmp_path):
    repo = Path(tp.__file__).resolve().parents[3]
    with pytest.raises(SystemExit) as stop:
        tp.main([str(recordings(tmp_path)), "--output", str(repo / "pose_test_should_not_exist.onnx")])
    assert stop.value.code != 0 and not (repo / "pose_test_should_not_exist.onnx").exists()


def test_a_class_with_no_training_frames_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        tp.main([str(recordings(tmp_path)), "--output", str(tmp_path / "o" / "m.onnx"), "--classes", "none", "ghost"])


def test_penalties_and_weights_run_and_change_the_card(tmp_path):
    out = run_tool(tmp_path, "--penalize", "thumb_ring=1.65", "--class-weights", "sqrt")
    training = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))["pose"]["training"]
    assert training["penalize"] == {"thumb_ring": 1.65} and training["class_weights"] == "sqrt"
