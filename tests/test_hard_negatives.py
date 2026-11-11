"""Near-miss recordings and fine-tuning on the model's own mistakes."""
from types import SimpleNamespace

import numpy as np
import pytest

from src.capture_dataset import NEAR_MISS_CONTACT_RATIO, drop_near_miss_contact
from src.gesture_catalog import BY_KEY, CLASSES, DISPLAY_KEYS, NEAR_MISS_TIPS, sync_custom_motions
from src import training_center as tc


def frame(gap, task="near_thumb_index"):
    """Wrist at the origin, finger roots 1 away, the thumb `gap` from the index tip."""
    lm = [[0.0, 0.0, 0.0] for _ in range(21)]
    for root in (5, 9, 13, 17):
        lm[root] = [1.0, 0.0, 0.0]
    lm[8] = [0.5, 0.0, 0.0]
    lm[4] = [0.5 + gap, 0.0, 0.0]
    return {"lm": lm, "label": {"task": task, "phase": "free", "target": "none"}}


def run_drop(frames, window=3):
    targets = ["none"] * len(frames)
    weights = np.ones(len(frames), dtype=np.float32)
    dropped = drop_near_miss_contact(frames, targets, weights, 1.0, window=window)
    return targets, weights, dropped


@pytest.mark.parametrize("key", list(NEAR_MISS_TIPS))
def test_near_miss_tasks_teach_none_and_show_in_the_catalog(key):
    task = BY_KEY[key]
    assert task.free and task.label == "none" and "none" in CLASSES
    assert key in DISPLAY_KEYS
    assert "닿으면 안" in task.do


def test_near_miss_tasks_survive_custom_motion_sync():
    sync_custom_motions({})
    assert all(key in DISPLAY_KEYS and key in BY_KEY for key in NEAR_MISS_TIPS)


def test_near_miss_rows_ask_for_less_data_and_never_show_a_shortcut():
    summary = {"per_gesture": {"near_thumb_ring": {"minutes": tc.GOAL_NEAR_MISS_MIN, "reps": 0, "postures": []}}, "newest": 0}
    report = {"classes": CLASSES[:3], "per_gesture": {}, "mtime": 1}
    row = next(r for r in tc.gesture_rows(summary, report) if r["key"] == "near_thumb_ring")
    assert row["data_ratio"] == 1.0
    assert "목표 3분" in row["data_text"]
    assert row["shortcut_badge"] == "오답 억제 전용"


def test_a_close_finger_stays_as_none():
    targets, weights, dropped = run_drop([frame(NEAR_MISS_CONTACT_RATIO + 0.02) for _ in range(5)])
    assert targets == ["none"] * 5 and dropped == 0 and weights.min() == 1.0


def test_a_real_touch_and_the_windows_holding_it_leave_training():
    frames = [frame(0.5), frame(0.0), frame(0.5), frame(0.5), frame(0.5)]
    targets, weights, dropped = run_drop(frames, window=3)
    assert targets == ["none", None, None, None, "none"]
    assert dropped == 3 and weights.tolist() == [1.0, 0.0, 0.0, 0.0, 1.0]


def test_the_guard_checks_only_the_finger_of_that_task_and_only_near_miss_recordings():
    other_finger = frame(0.5, "near_thumb_ring")  # index tip far from thumb, ring tip at the wrist far from thumb too
    pinch = frame(0.0, "thumb_index")
    targets, _, dropped = run_drop([other_finger, pinch])
    assert targets == ["none", "none"] and dropped == 0


train_tcn = pytest.importorskip("scripts.train_tcn")


def test_find_hard_negatives_only_picks_none_windows_leaning_to_a_gesture():
    classes = ["none", "thumb_index", "thumb_ring"]
    prob = np.array([[.9, .05, .05], [.3, .6, .1], [.3, .1, .6], [.3, .6, .1]])
    y = np.array([0, 0, 0, 1])
    assert train_tcn.find_hard_negatives(prob, y, classes, 0.5).tolist() == [False, True, True, False]


def test_refine_weighs_only_the_mistakes_and_stops_when_there_are_none(monkeypatch):
    classes = ["none", "thumb_index"]
    y = np.array([0, 0, 1])
    base = np.array([1.0, 0.5, 1.0], dtype=np.float32)
    answers = iter([np.array([[.9, .1], [.2, .8], [.2, .8]]), np.array([[.9, .1], [.9, .1], [.2, .8]])])
    seen = []
    monkeypatch.setattr(train_tcn, "predict", lambda model, x: next(answers))

    def fake_train(x, y, epochs, **kw):
        seen.append(kw["sample_weights"].tolist())
        assert kw["init_model"] == "base"
        return "base"

    monkeypatch.setattr(train_tcn, "train", fake_train)
    model, info = train_tcn.refine_with_hard_negatives(
        "base", np.zeros((3, 1)), y, base, classes=classes, epochs=10, rounds=3, thr=0.5, multiplier=3.0)
    assert model == "base"
    assert seen == [[1.0, 1.5, 1.0]]  # the hard window is heavier; the quality weight is kept, not replaced
    assert info["found_per_round"] == [1, 0] and info["none_windows"] == 2
    assert base.tolist() == [1.0, 0.5, 1.0]


def test_tally_adds_up_one_posture_across_files():
    rows = [
        {"reps": {"thumb_ring": (2, 3)}, "misfires": {"thumb_ring": 1}, "confused": 1,
         "free_by_task": {"near_thumb_ring": {"minutes": 1.0, "misfires": 1}}},
        {"reps": {"thumb_ring": (1, 1), "thumb_index": (1, 2)}, "misfires": {"thumb_index": 2}, "confused": 0,
         "free_by_task": {"near_thumb_ring": {"minutes": 1.0, "misfires": 2}}},
    ]
    got = train_tcn.tally_results(rows)
    assert (got["recognised"], got["total"], got["misfires"], got["confused"]) == (4, 6, 3, 1)
    assert got["misfires_by_task"] == {"near_thumb_ring": 3}
    assert SimpleNamespace(**got).reps["thumb_ring"] == (3, 4)


def test_flags_are_off_unless_asked():
    args = train_tcn.parse_args([])
    assert args.hard_negatives is False
    on = train_tcn.parse_args(["--hard-negatives", "--hard-negative-weight", "2", "--hard-negative-rounds", "2"])
    assert on.hard_negatives and on.hard_negative_weight == 2.0 and on.hard_negative_rounds == 2


def test_keep_rest_teaches_rest_as_none_and_keeps_the_mask_for_scoring(tmp_path):
    from src.capture_dataset import load_sessions
    from tests.test_expanded_training import record_tasks
    file = record_tasks(tmp_path, ["none", "open_move", "fist_move", "near_thumb_ring", "thumb_ring"])
    default = load_sessions([file])[0]
    kept = load_sessions([file], keep_rest=True)[0]
    assert default["rest_dropped"].any()
    assert (default["rest_dropped"] == kept["rest_dropped"]).all()
    assert (default["y"][default["rest_dropped"]] == -1).all()
    assert (kept["y"][kept["rest_dropped"]] == CLASSES.index("none")).all()
    others = ~default["rest_dropped"]
    assert (default["y"][others] == kept["y"][others]).all()


def test_rest_misfires_count_runs_inside_kept_rest_only():
    frames = [{"t": i * 0.1, "label": {}} for i in range(9)]
    session = {
        "frames": frames,
        "cond": np.zeros(9, dtype=np.int64),
        "rest_dropped": np.array([0, 1, 1, 1, 1, 0, 1, 1, 1], dtype=bool),
    }
    prob = np.zeros((9, 2))
    prob[:, 1] = [1, 1, 1, 1, 0, 1, 1, 1, 0]  # 1-3 is a run in rest; 5-7 is cut short because frame 5 is not rest
    got = train_tcn.rest_misfires(session, [0], prob, ["none", "custom"], thr=0.5, k=3)
    assert got["fired"] == 1
    assert got["minutes"] > 0
    assert train_tcn.rest_misfires(session, [1], prob, ["none", "custom"], thr=0.5, k=3)["fired"] == 0


def test_shifted_thresholds_move_every_gesture_together_and_stay_in_range():
    from src.gesture_gate import DEFAULT_GESTURE_THRESHOLDS as D
    got = train_tcn.shifted_thresholds(["none", "thumb_ring", "swipe_left"], -0.1)
    assert got == {"thumb_ring": round(D["thumb_ring"] - 0.1, 4), "swipe_left": 0.6}
    assert train_tcn.shifted_thresholds(["thumb_ring"], -5)["thumb_ring"] == 0.05
    assert train_tcn.shifted_thresholds(["thumb_ring"], 5)["thumb_ring"] == 0.95


def test_rest_misfires_follow_the_shifted_threshold():
    session = {"frames": [{"t": i * 0.1, "label": {}} for i in range(4)], "cond": np.zeros(4, dtype=np.int64),
               "rest_dropped": np.ones(4, dtype=bool)}
    prob = np.zeros((4, 2))
    prob[:, 1] = 0.5
    classes = ["none", "custom"]
    assert train_tcn.rest_misfires(session, [0], prob, classes, thr=0.7, k=3)["fired"] == 0
    assert train_tcn.rest_misfires(session, [0], prob, classes, thr=0.7, k=3, thr_by_label={"custom": 0.4})["fired"] == 1


def test_sweep_runs_end_to_end_on_a_small_recording(tmp_path, capsys):
    from tests.test_expanded_training import record_tasks
    keys = ["none", "open_move", "fist_move", "near_thumb_ring", "thumb_ring", "thumb_index"]
    record_tasks(tmp_path, keys, condition="pose-a")
    record_tasks(tmp_path, keys, condition="pose-b")
    pattern = str(tmp_path / "*-guided.jsonl.gz")
    rc = train_tcn.main(["--recordings", pattern, "--mode", "fast", "--folds", "--no-export", "--epochs", "1",
                         "--threshold-sweep", "--keep-rest", "--seed", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("SWEEP") == len(train_tcn.THRESHOLD_SWEEP_OFFSETS)
    assert "rest total" in out
