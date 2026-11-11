"""Phase 1 of online learning: stored samples, session splits, guided import, segments, collection (no camera)."""
import gzip
import json
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.online.collector import ACTION_TO_LABEL, OnlineCollector, correction_choices, label_sample
from src.online.import_guided import import_recording
from src.online.samples import (GUIDED_SCHEDULED, SUPERVISED_SOURCES, UNLABELED_UNKNOWN, USER_CONFIRMED, USER_CORRECTION,
                                SampleStore)
from src.online.segmenter import MotionSegmenter, SegmenterConfig, evaluate_segments
from src.online.splits import chronological_split, leaked_groups, split_by_condition, split_by_session
from src.tcn_features import FEATURES, frame_features


def hand_world(pinch=0.0, wiggle=0.0):
    """21 joints of an open hand. `pinch` 1 brings the index tip to the thumb tip, `wiggle` shifts the fingertips sideways."""
    w = np.zeros((21, 3), np.float64)
    for i, (x, y) in {1: (-.3, .2), 2: (-.45, .4), 3: (-.55, .6), 4: (-.65, .8), 5: (-.25, 1.0), 6: (-.25, 1.3), 7: (-.25, 1.5),
                      8: (-.25, 1.7), 9: (0, 1.0), 10: (0, 1.35), 11: (0, 1.6), 12: (0, 1.8), 13: (.2, .95), 14: (.2, 1.25),
                      15: (.2, 1.45), 16: (.2, 1.6), 17: (.4, .85), 18: (.4, 1.05), 19: (.4, 1.2), 20: (.4, 1.3)}.items():
        w[i] = (x, y, 0)
    w[8] = w[8] * (1 - pinch) + w[4] * pinch
    w[[8, 12, 16, 20], 0] += wiggle
    return w * .08


def as_frame(t, world, x=.5):
    lm = world[:, :] * .5 + np.array([x, .5, 0])
    return {"t": t, "lm": lm.tolist(), "world": world.tolist()}


@pytest.fixture
def store(tmp_path):
    return SampleStore(tmp_path / "online")


def add(store, session="s1", n=10, **kw):
    frames = [as_frame(i / 30, hand_world()) for i in range(n)]
    return store.add_frames(frames, session=session, **kw)


# ---------------------------------------------------------------- store
def test_a_sample_comes_back_the_same_after_reopening_the_store(store, tmp_path):
    frames = [as_frame(i / 30, hand_world(pinch=i / 20)) for i in range(20)]
    frames[5] = {"t": 5 / 30, "lm": None, "world": None}
    sample = store.add_frames(frames, session="s1", task="thumb_index", pc="pc-a", quality_score=.8)
    again = SampleStore(tmp_path / "online")
    loaded = again.get(sample.sample_id)
    assert loaded.frames == 20 and loaded.pc == "pc-a" and loaded.label_source == UNLABELED_UNKNOWN
    assert loaded.tracking_quality == pytest.approx(.95) and loaded.quality_score == .8
    assert loaded.fps == pytest.approx(30, abs=.01)
    back = again.frames(sample.sample_id)
    assert back[5]["lm"] is None and back[6]["lm"] is not None
    assert np.allclose(back[7]["world"], frames[7]["world"], atol=1e-5)


def test_stored_joints_give_the_features_of_the_original_frames(store):
    frames = [as_frame(i / 30, hand_world(pinch=i / 20)) for i in range(20)]
    sample = store.add_frames(frames, session="s1")
    got = store.features(sample.sample_id)
    assert got.shape == (20, FEATURES)
    assert np.allclose(got, frame_features(frames, sample.aspect), atol=1e-3)


def test_features_refuse_another_feature_version(store):
    sample = add(store, preprocessing_version="old-v0")
    with pytest.raises(ValueError):
        store.features(sample.sample_id)


def test_labels_survive_reopening_and_a_bad_source_is_refused(store, tmp_path):
    sample = add(store)
    store.set_label(sample.sample_id, "thumb_ring", USER_CORRECTION)
    again = SampleStore(tmp_path / "online")
    assert again.get(sample.sample_id).label == "thumb_ring" and again.get(sample.sample_id).approved
    with pytest.raises(ValueError):
        store.set_label(sample.sample_id, "x", "made_up")
    with pytest.raises(ValueError):
        add(store, label_source="made_up")


def test_a_broken_index_line_or_a_missing_file_is_skipped(store, tmp_path):
    good, gone = add(store), add(store)
    (store.samples_dir / f"{gone.sample_id}.npz").unlink()
    with open(store.index_path, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    again = SampleStore(tmp_path / "online")
    assert [s.sample_id for s in again.samples()] == [good.sample_id]


def test_the_same_id_is_not_stored_twice(store):
    assert add(store, sample_id="a") is not None
    assert add(store, sample_id="a") is None
    assert store.stats()["samples"] == 1


def test_delete_and_reset_remove_the_files(store):
    a, b = add(store), add(store)
    assert store.delete([a.sample_id, "nope"]) == 1
    assert not (store.samples_dir / f"{a.sample_id}.npz").exists()
    store.reset()
    assert store.stats()["samples"] == 0 and not list(store.samples_dir.iterdir()) and not store.index_path.exists()
    assert add(store) is not None


def test_when_full_old_unlabeled_samples_go_and_labelled_ones_stay(tmp_path):
    probe = SampleStore(tmp_path / "p")
    size = probe.total_bytes() or None
    sample = add(probe)
    size = probe.total_bytes()
    store = SampleStore(tmp_path / "s", max_bytes=int(size * 2.5))
    labelled = add(store)
    store.set_label(labelled.sample_id, "none", USER_CONFIRMED)
    old = add(store)
    time.sleep(.01)
    new = add(store)
    assert new is not None and store.get(old.sample_id) is None and store.get(labelled.sample_id) is not None
    store.set_label(new.sample_id, "none", USER_CONFIRMED)
    assert add(store) is None                         # only labelled samples left: refuse rather than delete them
    assert store.get(labelled.sample_id) and store.get(new.sample_id)


def test_supervised_sources_are_only_what_a_person_checked():
    assert set(SUPERVISED_SOURCES) == {USER_CONFIRMED, USER_CORRECTION}
    assert GUIDED_SCHEDULED not in SUPERVISED_SOURCES


# ---------------------------------------------------------------- splits
def test_session_splits_never_share_a_session(store):
    for session in ("a", "a", "b", "c", "c"):
        add(store, session=session)
        time.sleep(.002)
    train, test = split_by_session(store.samples(), ["b"])
    assert {s.session for s in test} == {"b"} and not leaked_groups(train, test)
    train, test = chronological_split(store.samples(), .34)
    assert {s.session for s in test} == {"c"} and {s.session for s in train} == {"a", "b"}
    assert not leaked_groups(train, test)


def test_the_leak_check_finds_a_shared_session(store):
    a, b = add(store, session="a"), add(store, session="a")
    assert leaked_groups([a], [b]) == ["a"]


def test_a_single_session_stays_on_the_test_side_and_conditions_split_apart(store):
    add(store, session="only", condition="p1")
    add(store, session="only", condition="p2")
    assert not chronological_split(store.samples())[0]
    train, test = split_by_condition(store.samples(), "p2")
    assert [s.condition for s in test] == ["p2"] and [s.condition for s in train] == ["p1"]


# ---------------------------------------------------------------- guided import
def recording(path: Path, frames, meta=None):
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "meta", "pc": "pc-a", "frame_aspect": .5625, "control_hand": "Right", **(meta or {})}) + "\n")
        for f in frames:
            fh.write(json.dumps({"type": "frame", **f}) + "\n")


def guided_frames(task="thumb_index", reps=3, hand=True):
    frames, t = [], 0.0
    for rep in range(reps):
        for phase, n in (("prep", 20), ("do", 30), ("rest", 20)):
            for _ in range(n):
                f = as_frame(t, hand_world(pinch=1.0 if phase == "do" else 0.0)) if hand else {"t": t}
                f["label"] = {"task": task, "phase": phase, "set": 0, "rep": rep, "condition_key": "pose-1"}
                frames.append(f)
                t += 1 / 30
    return frames


def test_guided_repetitions_become_samples_marked_as_scheduled(store, tmp_path):
    path = tmp_path / "20261109-000000-home-pc-guided.jsonl.gz"
    recording(path, guided_frames())
    counts = import_recording(store, path)
    assert counts["added"] == 3
    samples = store.samples()
    assert {s.label for s in samples} == {"thumb_index"}
    assert {s.label_source for s in samples} == {GUIDED_SCHEDULED} and not any(s.approved for s in samples)
    assert {s.session for s in samples} == {"20261109-000000-home-pc-guided"} and samples[0].condition == "pose-1"
    assert samples[0].frames == 45 and samples[0].extra["context_frames"] == 15
    assert store.samples(sources=SUPERVISED_SOURCES) == []
    assert import_recording(store, path)["skipped_duplicate"] == 3      # importing again changes nothing
    assert store.stats()["samples"] == 3


def test_repetitions_without_the_hand_are_skipped(store, tmp_path):
    path = tmp_path / "r.jsonl.gz"
    recording(path, guided_frames(hand=False))
    assert import_recording(store, path) == {"added": 0, "skipped_duplicate": 0, "skipped_hand": 3, "skipped_contact": 0}


def test_free_phases_become_pieces_and_almost_touching_that_touched_is_left_out(store, tmp_path):
    frames, t = [], 0.0
    for i in range(300):
        contact = 90 <= i < 180            # the second 3-second piece really touches
        f = as_frame(t, hand_world(pinch=1.0 if contact else .3))
        f["label"] = {"task": "near_thumb_index", "phase": "free", "set": 0}
        frames.append(f)
        t += 1 / 30
    path = tmp_path / "r.jsonl.gz"
    recording(path, frames)
    counts = import_recording(store, path)
    assert counts["skipped_contact"] >= 1 and counts["added"] >= 1
    assert {s.label for s in store.samples()} == {"none"}


# ---------------------------------------------------------------- segments
def stream(segmenter, frames):
    out = []
    for f in frames:
        done = segmenter.feed(f["t"], f.get("lm"), f.get("world"), f.get("action"))
        if done:
            out.append(done)
    return out


def still(t0, n):
    return [as_frame(t0 + i / 30, hand_world()) for i in range(n)]


def test_a_pinch_becomes_one_segment_with_frames_from_before_it():
    frames = still(0, 40) + [as_frame(40 / 30 + i / 30, hand_world(pinch=1.0)) for i in range(30)] + still(70 / 30, 40)
    segments = stream(MotionSegmenter(), frames)
    assert len(segments) == 1
    assert segments[0].start == pytest.approx(41 / 30, abs=.05) and segments[0].end == pytest.approx(69 / 30, abs=.15)
    assert "contact" in segments[0].triggers and segments[0].reason == "quiet"
    assert segments[0].frames[0]["t"] < segments[0].start


def test_a_still_open_hand_makes_no_segment():
    assert stream(MotionSegmenter(), still(0, 120)) == []


def test_moving_fingers_and_the_rule_engine_start_a_segment():
    wiggling = [as_frame(i / 30, hand_world(wiggle=.2 * np.sin(i))) for i in range(30)] + still(1.0, 40)
    assert "fingers" in stream(MotionSegmenter(), wiggling)[0].triggers
    fired = still(0, 10) + [{**as_frame(10 / 30 + i / 30, hand_world()), "action": "show_desktop"} for i in range(15)] + still(25 / 30, 40)
    segment = stream(MotionSegmenter(), fired)[0]
    assert "rule" in segment.triggers and segment.top_action == "show_desktop"


def test_a_fast_palm_starts_a_segment():
    frames = [as_frame(i / 30, hand_world(), x=.2 + i * .03) for i in range(15)] + [as_frame((15 + i) / 30, hand_world(), x=.65) for i in range(40)]
    assert "palm" in stream(MotionSegmenter(), frames)[0].triggers


def test_a_hand_loss_or_a_long_hold_ends_a_segment():
    lost = [as_frame(i / 30, hand_world(pinch=1.0)) for i in range(20)] + [{"t": 20 / 30 + i / 30} for i in range(30)]
    assert stream(MotionSegmenter(), lost)[0].reason == "hand_lost"
    held = [as_frame(i / 30, hand_world(pinch=1.0)) for i in range(120)]
    assert stream(MotionSegmenter(SegmenterConfig(max_seconds=2.0)), held)[0].reason == "too_long"


def test_a_blip_is_not_a_segment_and_the_last_one_comes_out_on_flush():
    blip = still(0, 10) + [as_frame(10 / 30 + i / 30, hand_world(pinch=1.0)) for i in range(3)] + still(13 / 30, 40)
    assert stream(MotionSegmenter(), blip) == []
    seg = MotionSegmenter()
    assert stream(seg, [as_frame(i / 30, hand_world(pinch=1.0)) for i in range(30)]) == []
    assert seg.flush().reason == "flush"


def test_the_segmenter_uses_only_the_past():
    frames = still(0, 40) + [as_frame(40 / 30 + i / 30, hand_world(pinch=1.0)) for i in range(30)] + still(70 / 30, 40)
    whole = stream(MotionSegmenter(), frames)
    seg = MotionSegmenter()
    upto = []
    for i, f in enumerate(frames):
        done = seg.feed(f["t"], f.get("lm"), f.get("world"))
        if done:
            upto.append((i, done))
    assert upto and upto[0][1].end == whole[0].end
    assert frames[upto[0][0]]["t"] == pytest.approx(upto[0][1].end + SegmenterConfig().end_seconds, abs=.1)


def test_the_segment_check_counts_repetitions_against_the_schedule():
    report = evaluate_segments(guided_frames(reps=4))
    assert report["reps"] == 4 and report["covered"] == 4 and report["share_in_do"] > .7 and report["outside_segments"] == 0


# ---------------------------------------------------------------- collector
def det(world, x=.5):
    lm = world * .5 + np.array([x, .5, 0])
    return SimpleNamespace(landmarks=[SimpleNamespace(x=p[0], y=p[1], z=p[2]) for p in lm], world=[tuple(p) for p in world])


def pinch_stream(collector, t0=0.0, action=None):
    t = t0
    for pinch, n in ((0, 20), (1, 30), (0, 30)):
        for _ in range(n):
            collector.frame(t, det(hand_world(pinch=float(pinch))), action if pinch else None)
            t += 1 / 30
    return t


def test_the_collector_saves_segments_unlabeled_with_the_rule_guess(store):
    collector = OnlineCollector(store, pc="pc-a", camera="cam")
    pinch_stream(collector, action="scroll_grab")
    collector.close()
    assert collector.stats()["saved"] == 1 and collector.dropped == 0
    sample = store.samples()[0]
    assert sample.label_source == UNLABELED_UNKNOWN and sample.label == "unknown" and not sample.approved
    assert sample.suggested_label == "thumb_index" and sample.pc == "pc-a" and sample.session.startswith("use-")
    assert store.samples(sources=SUPERVISED_SOURCES) == []


def test_a_person_confirms_or_corrects_and_recent_forgets_it(store):
    collector = OnlineCollector(store)
    t = pinch_stream(collector, action="scroll_grab")
    pinch_stream(collector, t, action="scroll_grab")
    collector.close()
    newest, older = collector.recent()
    assert label_sample(store, newest.sample_id, "thumb_index").label_source == USER_CONFIRMED
    assert label_sample(store, older.sample_id, "thumb_ring").label_source == USER_CORRECTION
    assert collector.recent() == []
    assert len(store.samples(sources=SUPERVISED_SOURCES)) == 2


def test_a_full_queue_drops_segments_and_never_blocks_the_camera(store, monkeypatch):
    release = __import__("threading").Event()
    real = store.add_frames
    monkeypatch.setattr(store, "add_frames", lambda *a, **k: (release.wait(10), real(*a, **k))[1])
    collector = OnlineCollector(store)
    t0 = time.perf_counter()
    t = 0.0
    for _ in range(collector.QUEUE_SIZE + 6):
        t = pinch_stream(collector, t)
    assert time.perf_counter() - t0 < 8.0
    assert collector.dropped >= 1
    release.set()
    collector.close()


def test_correction_choices_start_with_nothing_and_have_each_label_once():
    choices = correction_choices()
    labels = [c["label"] for c in choices]
    assert labels[0] == "none" and len(labels) == len(set(labels)) and "thumb_index" in labels
    assert set(ACTION_TO_LABEL.values()) <= set(labels)


def test_two_stores_on_one_folder_do_not_drop_each_others_samples(tmp_path):
    """The collector, a registration and the update job each open their own store. One rewriting the index from its own
    memory dropped what another had added since (a registered gesture's samples, on the next F correction)."""
    from src.online.samples import USER_CONFIRMED, USER_CORRECTION, SampleStore
    frame = {"t": 0.0, "lm": [[.5, .5, 0.0]] * 21, "world": [[0.0, 0.0, 0.0]] * 21}
    frames = [dict(frame, t=i / 30) for i in range(5)]
    collector = SampleStore(tmp_path)
    mine = collector.add_frames(frames, session="s")
    registration = SampleStore(tmp_path)
    added = registration.add_frames(frames, label="주먹", label_source=USER_CONFIRMED, session="s", approved=True)
    collector.set_label(mine.sample_id, "none", USER_CORRECTION)          # rewrites the index
    again = SampleStore(tmp_path)
    assert {s.sample_id for s in again.samples()} == {mine.sample_id, added.sample_id}
    registration.delete([added.sample_id])
    collector.set_label(mine.sample_id, "none", USER_CORRECTION)          # a sample removed elsewhere stays removed
    assert {s.sample_id for s in SampleStore(tmp_path).samples()} == {mine.sample_id}
