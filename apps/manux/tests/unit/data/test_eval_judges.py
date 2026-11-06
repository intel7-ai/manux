"""독립 녹화/시도/이벤트 평가. 실제 녹화나 사용자 식별자는 fixture에 넣지 않는다."""
import gzip
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

APP = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(APP / "tools"))
import eval_judges as ev
from manux.ml.spatial_mlp import train_spatial_mlp
from manux.perception.pose_angles import frame_pose_angles


def frame(t, target="none", rep=0):
    # A nondegenerate synthetic palm, varied fingers; no person's captured joints.
    lm = [[.4 + .015 * (i % 4), .6 - .02 * (i // 4), .002 * (i % 3)] for i in range(21)]
    lm[0] = [.4, .65, 0]
    lm[5] = [.44, .56, .005]
    lm[9] = [.4, .55, 0]
    lm[17] = [.36, .56, -.005]
    return {"type": "frame", "t": t, "lm": lm, "world": lm, "hand": ["Left", .99],
            "label": {"target": target, "rep": rep, "task": "test", "condition_index": 0,
                      "gesture": "pinch.thumb_ring" if target == "thumb_ring" else None},
            "rule": {"action": "right_click" if target == "thumb_ring" else None}}


def recording(tmp_path, name, *, person="synthetic-a", session=None, condition=0, role="train", offset=0):
    meta = {"type": "meta", "schema": "hand-mouse-recording/1", "person": person,
            "split": role, "preset": "pinch", "camera": {"mode": [640, 480, 30]}}
    if session:
        meta["session_id"] = session
    frames = [frame(i / 30 + offset, "none" if i % 60 < 30 else "thumb_ring", i // 60) for i in range(120)]
    for f in frames:
        f["label"]["condition_index"] = condition
    path = tmp_path / name
    with gzip.open(path, "wt", encoding="utf-8") as out:
        out.write("\n".join(json.dumps(x) for x in [meta, *frames, {"type": "end", "frames": len(frames)}]))
    return path


def test_reject_path_and_recompressed_content_overlap(tmp_path):
    train = ev.load_dataset(recording(tmp_path, "train.jsonl.gz"), "train")
    with pytest.raises(ValueError, match="경로"):
        list(ev.folds([train], [train], "session"))
    # Same frame data, different meta/filename/gzip wrapper: still a leaked recording.
    duplicate = ev.load_dataset(recording(tmp_path, "copy.jsonl.gz", role="eval"), "eval")
    assert train.digest != duplicate.digest
    with pytest.raises(ValueError, match="also training"):
        list(ev.folds([train], [duplicate], "session"))


@pytest.mark.parametrize("axis", ["session", "condition", "person"])
def test_whole_recording_leave_one_group_out(tmp_path, axis):
    a = ev.load_dataset(recording(tmp_path, "a.gz", person="synthetic-a", session="s1"), "train")
    b = ev.load_dataset(recording(tmp_path, "b.gz", person="synthetic-b", session="s2", condition=1, offset=.01), "train")
    e = ev.load_dataset(recording(tmp_path, "e.gz", person="synthetic-a", session="s1", role="eval", offset=.02), "eval")
    folds = list(ev.folds([a, b], [e], axis))
    assert len(folds) == 1
    assert folds[0][1] == [b] and folds[0][2] == [e]


def test_refuse_empty_holdout_and_missing_group(tmp_path):
    a = ev.load_dataset(recording(tmp_path, "a.gz"), "train")
    e = ev.load_dataset(recording(tmp_path, "e.gz", role="eval", offset=.02), "eval")
    with pytest.raises(ValueError, match="학습 파일이 없"):
        list(ev.folds([a], [e], "person"))
    e.person = ""
    with pytest.raises(ValueError, match="그룹 정보"):
        list(ev.folds([a], [e], "person"))
    with pytest.raises(ValueError, match="session"):
        list(ev.folds([a], [e], "random"))


def test_condition_holdout_excludes_entire_multi_condition_file(tmp_path):
    a = ev.load_dataset(recording(tmp_path, "a.gz", session="a", condition=0), "train")
    b = ev.load_dataset(recording(tmp_path, "b.gz", session="b", condition=1, offset=.01), "train")
    c = ev.load_dataset(recording(tmp_path, "c.gz", session="c", condition=2, offset=.02), "train")
    e = ev.load_dataset(recording(tmp_path, "e.gz", session="e", role="eval", offset=.03), "eval")
    e.conditions = a.conditions | b.conditions
    for _, training, held in ev.folds([a, b, c], [e], "condition"):
        assert training == [c] and held == [e]


def test_attempts_false_starts_confusion_latency_and_jitter():
    frames = [frame(i / 10, target, rep) for i, (target, rep) in enumerate(
        [("none", 0)] * 3 + [("thumb_ring", 1)] * 4 + [("thumb_ring", 2)] * 3 + [("none", 3)] * 3)]
    classes = ["none", "thumb_ring", "other"]
    events = [(1, "other"), (4, "thumb_ring"), (5, "thumb_ring"), (7, "other"), (9, "thumb_ring"), (11, "other")]
    scored = ev.score_events(frames, events, classes)
    result = ev.combine_metrics([scored], classes)
    gesture = result["per_gesture"]["thumb_ring"]
    assert (gesture["attempts"], gesture["hits"], gesture["recall"]) == (2, 2, 1.)
    assert gesture["latency_median_ms"] == pytest.approx(150)
    assert gesture["latency_p95_ms"] == pytest.approx(195)
    assert result["false_starts"] == 2
    assert result["negative_seconds"] == pytest.approx(.5)
    assert result["false_starts_per_negative_minute"] == pytest.approx(240)
    assert result["latency_median_ms"] == pytest.approx(150)
    assert result["latency_p95_ms"] == pytest.approx(195)
    assert result["repeated_starts"] == 2
    assert result["confusion"]["thumb_ring"] == {"thumb_ring": 3, "other": 1}


def test_miss_no_negative_and_unlabelled_events_not_silently_counted():
    frames = [frame(0., "thumb_ring"), frame(.1, "thumb_ring"), frame(.2, "transition")]
    result = ev.combine_metrics([ev.score_events(frames, [(2, "thumb_ring")], ["none", "thumb_ring"])], ["none", "thumb_ring"])
    assert result["confusion"]["thumb_ring"]["miss"] == 1
    assert result["false_starts_per_negative_minute"] is None
    assert result["latency_median_ms"] is None
    assert result["ignored_starts"] == 1


def test_confirmed_events_require_neutral_not_label_boundaries():
    classes = ["none", "thumb_ring"]
    frames = [frame(i / 10, "thumb_ring", i // 3) for i in range(12)]
    # starts at frame 5 then 11; repeated high frames don't create starts.
    scores = np.array([[.99, .01]] * 3 + [[.01, .99]] * 3 + [[.99, .01]] * 3 + [[.01, .99]] * 3)
    assert ev.confirmed_starts(scores, classes, frames, np.ones(12, bool), .7, 3) == [(5, "thumb_ring"), (11, "thumb_ring")]
    valid = np.ones(12, bool)
    valid[8] = False  # lost hand cancels rearming; label says nothing about this.
    assert ev.confirmed_starts(scores, classes, frames, valid, .7, 3) == [(5, "thumb_ring")]


def test_gap_is_not_negative_exposure_or_one_attempt():
    frames = [frame(0., "thumb_ring"), frame(.1, "thumb_ring"), frame(2., "thumb_ring"), frame(2.1, "thumb_ring")]
    part = ev.score_events(frames, [(1, "thumb_ring"), (3, "thumb_ring")], ["none", "thumb_ring"])
    assert part["per_gesture"]["thumb_ring"]["attempts"] == 2
    assert part["repeated_starts"] == 0


def test_numpy_trainer_uses_every_training_frame_without_random_test_split():
    x = np.zeros((20, 33), dtype=np.float32)
    x[10:, 0] = 1
    y = np.array([0] * 10 + [1] * 10)
    model, metrics = train_spatial_mlp(x, y, classes=["none", "custom"], epochs=1, feature_version="pose-angles-v1")
    assert metrics.train_samples == 20 and metrics.test_samples == 0
    assert metrics.test_accuracy is None
    assert model.predict_proba(x).shape == (20, 2)


def test_features_and_budget_preserve_recording_segments(tmp_path):
    train = ev.load_dataset(recording(tmp_path, "a.gz"), "train")
    classes = ["none", "thumb_ring"]
    segments, used = ev.budget_segments([train], .5, classes)
    assert all(v <= .5 + 1e-9 for v in used.values())
    assert len(segments) >= 2
    for feature, dims in (("angles33", 33), ("spatial96", 96)):
        x, y = ev.mlp_samples(segments, classes, feature)
        assert x.shape[1] == dims and set(y) == {0, 1}
    rows, valid = frame_pose_angles(train.recording.frames)
    assert valid.all() and rows.shape == (120, 33)


def test_outputs_refuse_shared_checkout_and_worktree(tmp_path):
    with pytest.raises(ValueError, match="저장소"):
        ev.outside_repo(APP / ".local" / "eval")
    common = Path(ev.subprocess.check_output(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=APP, text=True).strip()).parent
    with pytest.raises(ValueError, match="저장소"):
        ev.outside_repo(common / ".local" / "eval")
    assert ev.outside_repo(tmp_path / "eval") == (tmp_path / "eval").resolve()


@pytest.mark.parametrize("feature", ["angles33", "spatial96"])
def test_complete_cli_synthetic_all_five_judges(tmp_path, monkeypatch, feature):
    # CI doesn't need torch; exercise orchestration and output with a deterministic predictor.
    # A separate real torch smoke run is recorded in the PR, never called real accuracy.
    def train_tcn(segments, classes, epochs):
        def infer(w):
            p = np.zeros((len(w), len(classes)))
            p[:, 0] = 1
            return p
        return infer
    monkeypatch.setattr(ev, "train_multi", train_tcn)
    train = recording(tmp_path, "train.gz")
    evaluation = recording(tmp_path, "eval.gz", role="eval", offset=.01)
    out = tmp_path / "out"
    assert ev.main(["--train", str(train), "--eval", str(evaluation), "--out", str(out),
                    "--features", feature, "--mlp-epochs", "1", "--tcn-epochs", "1", "--train-seconds", ".5,1"]) == 0
    result = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert len(result["folds"][0]["curves"]) == 2
    curve = result["folds"][0]["curves"][0]
    assert set(curve["judges"]) == set(ev.JUDGES)
    assert all(row["per_gesture"]["thumb_ring"]["attempts"] == 2 for row in curve["judges"].values())
    assert "혼동표" in (out / "results.md").read_text(encoding="utf-8")
