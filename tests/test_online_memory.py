"""Phase 3 of online learning: the prototype memory and unknown detection (no camera, no neural network)."""
import numpy as np
import pytest

from src.online.memory import MODES, UNKNOWN_LABEL, PrototypeMemory, kmeans, normalize


def cluster(centre, n=40, noise=.03, seed=0):
    rng = np.random.default_rng(seed)
    c = np.zeros(8)
    c[centre] = 1.0
    return c + rng.normal(0, noise, (n, 8))


def filled(mode="ncm", **kw):
    memory = PrototypeMemory(mode, **kw)
    memory.add_class("a", cluster(0), demo_ids=np.arange(40) % 4)
    memory.add_class("b", cluster(1, seed=1), demo_ids=np.arange(40) % 4)
    return memory


@pytest.mark.parametrize("mode", MODES)
def test_each_mode_tells_two_classes_apart_and_a_new_class_works_at_once(mode):
    memory = filled(mode)
    assert memory.classify(cluster(0, 1, seed=5)[0])["label"] == "a"
    assert memory.classify(cluster(1, 1, seed=6)[0])["label"] == "b"
    assert memory.classify(cluster(2, 1, seed=7)[0])["label"] == UNKNOWN_LABEL
    memory.add_class("c", cluster(2, seed=2), demo_ids=np.arange(40) % 4)
    assert memory.classify(cluster(2, 1, seed=7)[0])["label"] == "c"
    assert memory.classify(cluster(0, 1, seed=5)[0])["label"] == "a"


def test_an_unknown_window_is_not_forced_onto_the_nearest_class():
    memory = filled()
    between = normalize(np.array([1.0, 1.0, 0, 0, 0, 0, 0, 0]))
    assert memory.classify(between)["label"] == UNKNOWN_LABEL
    assert memory.classify(np.array([0, 0, 0, 0, 0, 0, 0, 1.0]))["label"] == UNKNOWN_LABEL


def test_a_required_margin_rejects_a_close_call():
    memory = filled(threshold_factor=50.0)
    between = normalize(np.array([1.0, .98, 0, 0, 0, 0, 0, 0]))
    assert memory.classify(between)["label"] != UNKNOWN_LABEL
    memory.min_margin = .05
    assert memory.classify(between)["label"] == UNKNOWN_LABEL


def test_multi_covers_a_gesture_done_in_two_ways_where_one_mean_cannot():
    ways = np.concatenate([cluster(0), cluster(1, seed=1)])
    probe = cluster(0, 1, seed=9)[0]
    one = PrototypeMemory("ncm", min_threshold=0.0)
    one.add_class("g", ways)
    two = PrototypeMemory("multi", k=2, min_threshold=0.0)
    two.add_class("g", ways)
    assert len(two.classes["g"].prototypes) == 2 and len(one.classes["g"].prototypes) == 1
    assert two.distances(probe)[0, 0] < one.distances(probe)[0, 0] * .2


def test_exemplars_keep_one_prototype_per_demonstration():
    memory = PrototypeMemory("exemplar")
    entry = memory.add_class("g", cluster(0), demo_ids=np.arange(40) % 5)
    assert len(entry.prototypes) == 5 and entry.demos == 5 and entry.samples == 40


def test_kmeans_never_gives_more_or_emptier_centres_than_it_can():
    assert len(kmeans(cluster(0, 2), 5)) <= 2
    assert np.allclose(np.linalg.norm(kmeans(cluster(0), 3), axis=1), 1.0, atol=1e-5)


def test_adding_the_same_label_replaces_it_and_removing_works():
    memory = filled()
    memory.add_class("a", cluster(3, seed=3))
    assert memory.classify(cluster(3, 1, seed=8)[0])["label"] == "a"
    assert memory.classify(cluster(0, 1, seed=5)[0])["label"] == UNKNOWN_LABEL
    assert memory.remove_class("a") and not memory.remove_class("a") and memory.labels == ["b"]


def test_one_class_alone_has_no_margin_problem():
    memory = PrototypeMemory("ncm", min_margin=.1)
    memory.add_class("a", cluster(0))
    assert memory.classify(cluster(0, 1, seed=5)[0])["label"] == "a"


def test_conflicts_report_a_class_that_is_too_close():
    memory = filled()
    assert memory.conflicts("a") == []
    memory.add_class("twin", cluster(0, seed=4))
    found = memory.conflicts("twin")
    assert found[0]["label"] == "a" and found[0]["cosine"] > .9


def test_a_bad_class_is_refused():
    memory = PrototypeMemory()
    with pytest.raises(ValueError):
        memory.add_class(UNKNOWN_LABEL, cluster(0))
    with pytest.raises(ValueError):
        memory.add_class("", cluster(0))
    with pytest.raises(ValueError):
        memory.add_class("a", np.zeros((0, 8)))
    with pytest.raises(ValueError):
        PrototypeMemory("magic")


def test_a_saved_memory_gives_the_same_answers(tmp_path):
    memory = filled("multi", encoder_version="enc-1")
    path = memory.save(tmp_path / "x" / "memory.json")
    again = PrototypeMemory.load(path)
    assert again.mode == "multi" and again.encoder_version == "enc-1" and again.labels == memory.labels
    probe = cluster(0, 10, seed=5)
    assert (again.classify_arrays(probe)[0] == memory.classify_arrays(probe)[0]).all()
    assert again.classes["a"].samples == 40
    with pytest.raises(ValueError):
        PrototypeMemory.from_dict({"schema": "other"})


def test_adding_a_class_changes_no_other_class():
    memory = filled()
    before = memory.classes["a"].prototypes.copy()
    memory.add_class("c", cluster(2, seed=2))
    assert np.array_equal(before, memory.classes["a"].prototypes)


# ---------------------------------------------------------------- plain pose space
from src.online.pose_memory import PoseMemory, PoseRecognizer, distances, pose_features, pose_kmeans  # noqa: E402
from tests.test_online_data import as_frame, hand_world  # noqa: E402


def poses(pinch=0.0, n=30, noise=.002, seed=0):
    rng = np.random.default_rng(seed)
    frames = [as_frame(i / 30, hand_world(pinch=pinch) + rng.normal(0, noise, (21, 3))) for i in range(n)]
    return pose_features(frames, .5625)


def demos(pinch, count=5):
    x = np.concatenate([poses(pinch, seed=i) for i in range(count)])
    return x, np.repeat(np.arange(count), 30)


def pose_base():
    memory = PoseMemory()
    memory.add_known("none", poses(0.0), k=2)
    return memory


def test_a_registered_pose_wins_on_itself_and_loses_to_a_known_class():
    memory = pose_base()
    x, ids = demos(1.0)
    entry = memory.add_class("집기", x, demo_ids=ids, source="user_registered")
    assert entry.demos == 5 and np.isfinite(entry.threshold) and len(entry.prototypes) == 3
    assert memory.classify(poses(1.0, seed=9)[:1])["label"] == "집기"
    assert memory.classify(poses(0.0, seed=9)[:1])["label"] == "none"


def test_a_pose_far_from_a_registered_gesture_is_not_it_even_with_no_known_class_nearby():
    memory = PoseMemory()
    x, ids = demos(1.0)
    memory.add_class("집기", x, demo_ids=ids)
    assert memory.classify(poses(0.0, seed=9)[:1])["label"] == "unknown"


def test_a_tight_gesture_gets_a_tight_limit_and_a_loose_one_a_wider_limit():
    tight, loose = PoseMemory(), PoseMemory()
    x, ids = demos(1.0)
    tight_entry = tight.add_class("a", x, demo_ids=ids)
    wide = np.concatenate([poses(1.0 - .3 * i, seed=i) for i in range(4)])
    loose_entry = loose.add_class("b", wide, demo_ids=np.repeat(np.arange(4), 30))
    assert loose_entry.threshold > tight_entry.threshold


def test_the_recognizer_reports_only_registered_gestures_after_a_run():
    memory = pose_base()
    x, ids = demos(1.0)
    memory.add_class("집기", x, demo_ids=ids, source="user_registered")
    live = PoseRecognizer(memory, .5625)
    frame = as_frame(0.0, hand_world(pinch=1.0))
    fired = [live.step(i / 30, frame["lm"], frame["world"]) for i in range(6)]
    assert fired == [None, None, None, "집기", None, None] and live.current == "집기"
    assert live.step(.3, None, None) is None and live.current is None
    open_hand = as_frame(0.0, hand_world())
    assert all(live.step(i / 30, open_hand["lm"], open_hand["world"]) is None for i in range(8))


def test_a_saved_pose_memory_gives_the_same_answers_and_other_features_are_refused(tmp_path):
    memory = pose_base()
    x, ids = demos(1.0)
    memory.add_class("집기", x, demo_ids=ids, source="user_registered")
    path = memory.save(tmp_path / "p.json")
    again = PoseMemory.load(path)
    probe = np.concatenate([poses(1.0, seed=8), poses(0.0, seed=8)])
    assert (again.classify_arrays(probe)[0] == memory.classify_arrays(probe)[0]).all()
    assert np.isinf(again.classes["none"].threshold) and again.classes["집기"].source == "user_registered"
    memory.feature_version = "old"
    memory.save(path)
    with pytest.raises(ValueError):
        PoseMemory.load(path)
    with pytest.raises(ValueError):
        PoseMemory.from_dict({"schema": "other"})
    with pytest.raises(ValueError):
        memory.add_class("", x)


def test_pose_kmeans_and_distances_behave():
    x = np.concatenate([poses(0.0), poses(1.0)])
    assert len(pose_kmeans(x, 2)) == 2 and len(pose_kmeans(x[:1], 3)) == 1
    assert distances(x[:1], x[:1])[0] == pytest.approx(0.0, abs=1e-5)
