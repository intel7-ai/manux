"""tools/pose_data.py: segments, leak-free splits, coverage warnings and frame-level metrics (numpy only)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import pose_data as pd  # noqa: E402
from pose_recordings import Run, standard_runs, write_recording  # noqa: E402


def load(tmp_path, runs, **kw):
    path = write_recording(tmp_path / "a.jsonl.gz", runs, **kw)
    return path, pd.load_segments([path])


# ---------------------------------------------------------------- segments

def test_runs_become_segments_and_changes_of_label_or_gaps_split_them(tmp_path):
    _, loaded = load(tmp_path, [Run("none", 20), Run("thumb_ring", 20), Run("thumb_ring", 20, gap=1.0),
                                Run("none", 10, gap=0.1)])
    labels = [(s.label, s.frames) for s in loaded.segments]
    # run 2 -> 3: the same label but a 1 s gap: two segments; run 3 -> 4: 0.1 s is below the 0.5 s gap but the label changes
    assert labels == [("none", 20), ("thumb_ring", 20), ("thumb_ring", 20), ("none", 10)]
    assert [s.index for s in loaded.segments] == [0, 1, 2, 3]
    assert all(s.rows.shape[1] == 33 and s.rows.dtype == np.float32 and np.isfinite(s.rows).all() for s in loaded.segments)


def test_a_dropped_frame_ends_a_segment(tmp_path):
    runs = [Run("thumb_ring", 10), Run("thumb_ring", 1, world=False), Run("thumb_ring", 10)]
    _, loaded = load(tmp_path, runs)
    assert [s.frames for s in loaded.segments] == [10, 10]


@pytest.mark.parametrize("label", ["transition", "idle", "no_hand"])
def test_reserved_and_unlabelled_frames_are_not_used(tmp_path, label):
    runs = [Run("none", 10), Run(label, 10, gesture=None), Run("none", 5, target=None), Run("thumb_ring", 10)]
    _, loaded = load(tmp_path, runs)
    assert [(s.label, s.frames) for s in loaded.segments] == [("none", 10), ("thumb_ring", 10)]


def test_frames_without_usable_world_joints_are_left_out(tmp_path):
    _, loaded = load(tmp_path, [Run("none", 8, world=False), Run("thumb_index", 8)])
    assert [(s.label, s.frames) for s in loaded.segments] == [("thumb_index", 8)]


def test_gestures_come_from_the_labels_never_for_none(tmp_path):
    _, loaded = load(tmp_path, [Run("none", 5), Run("thumb_ring", 5), Run("thumb_index", 5),
                                Run("thumb_middle", 5, gesture=None)])
    assert loaded.gestures == {"thumb_ring": "pinch.thumb_ring", "thumb_index": "pinch.scroll"}


def test_dans_legacy_class_names_fall_back_to_their_gestures(tmp_path):
    _, loaded = load(tmp_path, [Run("thumb_ring", 5, gesture=None), Run("thumb_index", 5, gesture=None)])
    assert loaded.gestures == {"thumb_ring": "pinch.thumb_ring", "thumb_index": "pinch.scroll"}


def test_two_gesture_ids_for_one_class_are_refused(tmp_path):
    with pytest.raises(ValueError, match="conflicting gesture IDs"):
        load(tmp_path, [Run("thumb_ring", 5), Run("thumb_ring", 5, gesture="pinch.other")])


def test_incomplete_and_wrong_split_recordings_are_refused(tmp_path):
    path = write_recording(tmp_path / "x.jsonl.gz", [Run("none", 5)], complete=False)
    with pytest.raises(ValueError, match="incomplete"):
        pd.load_segments([path])
    evalrec = write_recording(tmp_path / "e.jsonl.gz", [Run("none", 5)], split="eval")
    with pytest.raises(ValueError, match="expected a train"):
        pd.load_segments([evalrec], split="train")
    assert pd.load_segments([evalrec], split="eval").segments


def test_the_same_content_twice_is_one_recording(tmp_path):
    a = write_recording(tmp_path / "a.jsonl.gz", [Run("none", 5)])
    b = tmp_path / "copy.jsonl.gz"
    b.write_bytes(a.read_bytes())
    loaded = pd.load_segments([a, b])
    assert len(loaded.segments) == 1 and len(loaded.sources) == 1


def test_sources_are_public_facts_and_the_person_is_an_alias(tmp_path):
    _, loaded = load(tmp_path, [Run("none", 5)], person="tester01")
    (source,) = loaded.sources
    assert source["pc"] != "pc-test" and len(source["pc"]) == 7 and source["pc"].startswith("pc-")   # a code, not the name
    assert len(source["sha256"]) == 64 and source["used_frames"] == 5
    assert source["person"] == loaded.segments[0].person and "tester01" not in str(loaded.sources)   # no real name in the card
    assert loaded.segments[0].person not in ("", "tester01") and len(loaded.segments[0].person) == 12


def test_condition_keys_are_kept_on_segments(tmp_path):
    _, loaded = load(tmp_path, [Run("none", 5, condition="pose-aaaa"), Run("none", 5, gap=1.0)])
    assert [s.condition for s in loaded.segments] == ["pose-aaaa", ""]


def test_class_names_put_none_first(tmp_path):
    _, loaded = load(tmp_path, [Run("thumb_ring", 5), Run("none", 5), Run("thumb_index", 5)])
    assert pd.class_names(loaded.segments) == ["none", "thumb_index", "thumb_ring"]
    assert pd.class_names([s for s in loaded.segments if s.label != "none"]) == ["thumb_index", "thumb_ring"]


# ---------------------------------------------------------------- splits never cut a segment

def many(tmp_path, per_class=8, classes=("none", "thumb_ring", "thumb_index"), **kw):
    return pd.load_segments([write_recording(tmp_path / "m.jsonl.gz", standard_runs(per_class, 20, classes), **kw)]).segments


def test_a_split_moves_whole_segments_and_keeps_every_class_in_both_sides(tmp_path):
    segments = many(tmp_path)
    train, held = pd.split_segments(segments, 0.25, seed=1)
    assert len(train) + len(held) == len(segments) and not {s.key for s in train} & {s.key for s in held}
    pd.check_disjoint(train, held)
    for name in ("none", "thumb_ring", "thumb_index"):
        assert any(s.label == name for s in train) and any(s.label == name for s in held)
    assert all(s.frames == 20 for s in train + held)                                  # none was cut


def test_the_split_is_deterministic_and_the_seed_matters(tmp_path):
    segments = many(tmp_path)
    keys = lambda split: [s.key for s in split[1]]  # noqa: E731
    assert keys(pd.split_segments(segments, 0.25, 3)) == keys(pd.split_segments(segments, 0.25, 3))
    assert keys(pd.split_segments(segments, 0.25, 3)) != keys(pd.split_segments(segments, 0.25, 4))


def test_a_class_with_one_segment_stays_in_training(tmp_path):
    segments = many(tmp_path, per_class=6) + pd.load_segments([write_recording(
        tmp_path / "r.jsonl.gz", [Run("thumb_middle", 20)])]).segments
    train, held = pd.split_segments(segments, 0.3)
    assert any(s.label == "thumb_middle" for s in train) and not any(s.label == "thumb_middle" for s in held)


def test_a_bad_fraction_is_refused(tmp_path):
    for bad in (0.0, 1.0, -1, 2):
        with pytest.raises(ValueError):
            pd.split_segments(many(tmp_path, 3), bad)


def test_overlap_checks_catch_a_segment_or_a_file_on_both_sides(tmp_path):
    segments = many(tmp_path, 4)
    with pytest.raises(ValueError, match="both training and evaluation"):
        pd.check_disjoint(segments[:5], segments[4:])
    pd.check_files_disjoint(["a", "b"], ["c"])
    with pytest.raises(ValueError, match="also training"):
        pd.check_files_disjoint(["a", "b"], ["b"])


# ---------------------------------------------------------------- coverage warnings

def test_few_segments_and_one_person_are_reported(tmp_path):
    segments = many(tmp_path, per_class=3)
    train, held = pd.split_segments(segments, 0.3)
    text = "\n".join(pd.coverage_warnings(train, held))
    assert "only 2 independent training segment(s)" in text and "held-out segment(s)" in text and "one person" in text


def test_enough_segments_from_two_people_give_no_warning(tmp_path):
    a = pd.load_segments([write_recording(tmp_path / "a.jsonl.gz", standard_runs(12, 10), person="p1", seed=1)]).segments
    b = pd.load_segments([write_recording(tmp_path / "b.jsonl.gz", standard_runs(12, 10), person="p2", seed=2)]).segments
    train, held = pd.split_segments(a + b, 0.25)
    assert pd.coverage_warnings(train, held) == []


def test_the_minimums_are_parameters(tmp_path):
    segments = many(tmp_path, per_class=4)
    train, held = pd.split_segments(segments, 0.25)
    assert pd.coverage_warnings(train, held, minimum=2, minimum_held_out=1)  # still the one-person warning
    assert all("independent training" not in w for w in pd.coverage_warnings(train, held, minimum=2, minimum_held_out=1))


# ---------------------------------------------------------------- stack and metrics

def test_stack_filters_to_the_classes_and_refuses_nothing(tmp_path):
    segments = many(tmp_path, 2)
    x, y = pd.stack(segments, ["none", "thumb_ring"])
    assert x.shape == (len(y), 33) and x.dtype == np.float32 and set(y.tolist()) == {0, 1}
    with pytest.raises(ValueError, match="no labelled frames"):
        pd.stack(segments, ["nope"])


def test_confusion_per_class_and_coverage_on_known_numbers():
    y = np.array([0, 0, 0, 1, 1, 2])
    probs = np.array([[.95, .03, .02], [.6, .3, .1], [.2, .7, .1], [.1, .85, .05], [.5, .45, .05], [.05, .05, .9]])
    cm = pd.confusion(y, probs.argmax(axis=1), 3)
    assert cm.tolist() == [[2, 1, 0], [1, 1, 0], [0, 0, 1]]
    stats = pd.per_class(cm, ["a", "b", "c"])
    assert stats["a"] == {"frames": 3, "recall": 0.6667, "precision": 0.6667}
    assert stats["c"] == {"frames": 1, "recall": 1.0, "precision": 1.0}
    cov = {row["threshold"]: row for row in pd.coverage_accuracy(probs, y, (0.0, 0.8, 0.99))}
    assert cov[0.0] == {"threshold": 0.0, "coverage": 1.0, "accuracy": 0.6667}
    assert cov[0.8] == {"threshold": 0.8, "coverage": 0.5, "accuracy": 1.0}      # frames 0, 3, 5 are that sure
    assert cov[0.99] == {"threshold": 0.99, "coverage": 0.0, "accuracy": None}


def test_the_veto_study_counts_missed_and_prevented_gestures():
    classes = ["none", "ring", "index"]
    y = np.array([1, 1, 1, 1, 0, 0, 2, 2])
    probs = np.array([
        [.01, .98, .01], [.02, .97, .01], [.95, .03, .02], [.4, .5, .1],       # ring frames: 2 fine, 1 confidently "none", 1 unsure
        [.97, .02, .01], [.2, .7, .1],                                          # other frames: confident rest, a ring-like one
        [.02, .02, .96], [.5, .3, .2]])                                         # other frames: confident index, unsure
    study = pd.veto_study(probs, y, classes, "ring", tau=0.9)
    assert study["own_frames"] == 4 and study["wrongly_vetoed"] == 0.25          # only frame 2
    assert study["other_frames"] == 4 and study["correctly_vetoed"] == 0.5       # frames 4 and 6; 5 looks like a ring, 7 is unsure
    result = pd.evaluate(probs, y, classes, veto_classes=["ring", "missing"], tau=0.9)
    assert result["level"] == "frame" and result["frames"] == 8 and [v["gesture_class"] for v in result["veto"]] == ["ring"]
    assert result["accuracy"] == pytest.approx(0.625) and len(result["confusion"]) == 3
