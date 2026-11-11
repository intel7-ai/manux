"""Phase 4: replay memory, real gradient updates with weight changes checked, candidate checks, swap and rollback (no camera)."""
import copy

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from src.online.memory import PrototypeMemory  # noqa: E402
from src.online.models import make_model  # noqa: E402
from src.online.replay import BalancedBuffer, ReservoirBuffer  # noqa: E402
from src.online.trainer import EVENT_ENCODER, EVENT_HEAD, OnlineLearner, expanded, parameter_change  # noqa: E402
from src.online.versions import ModelManager, ValidationLimits, check_candidate, finite_weights  # noqa: E402
from src.tcn_features import FEATURES  # noqa: E402

W = 15


def batch(label, n=16, seed=0, classes=3):
    """Separable fake windows: each class lights its own feature."""
    rng = np.random.default_rng(seed + label * 100)
    x = rng.normal(0, .05, (n, W, FEATURES)).astype(np.float32)
    x[:, :, label] += 1.0
    return x, np.full(n, label, dtype=np.int64)


def model(classes=3):
    torch.manual_seed(0)
    return make_model("tcn", classes, W)


def accuracy(net, label, classes=3):
    x, y = batch(label, 64, seed=99)
    net.eval()
    with torch.no_grad():
        return float((net(torch.from_numpy(x)).argmax(1).numpy() == y).mean())


# ---------------------------------------------------------------- memory
def test_reservoir_keeps_capacity_and_gives_each_example_about_the_same_chance():
    stored = np.zeros(1000)
    for seed in range(300):
        buffer = ReservoirBuffer(50, seed=seed)
        x = np.arange(1000, dtype=np.float32)[:, None]
        buffer.add(x, np.zeros(1000, dtype=np.int64))
        assert buffer.size == 50 and buffer.seen == 1000
        stored[buffer.x[:50, 0].astype(int)] += 1
    assert abs(stored[:500].sum() - stored[500:].sum()) < stored.sum() * .08      # early and late examples: the same chance


def test_the_buffer_keeps_logits_and_grows_for_new_classes():
    buffer = ReservoirBuffer(10)
    buffer.add(np.ones((2, 3), np.float32), np.array([0, 1]), np.array([[1., 2.], [3., 4.]], np.float32))
    buffer.add(np.ones((1, 3), np.float32), np.array([2]), np.array([[5., 6., 7.]], np.float32))
    assert buffer.z.shape[1] == 3 and buffer.z[0, 1] == 2.0 and np.isnan(buffer.z[0, 2]) and buffer.z[2, 2] == 7.0
    assert buffer.class_counts() == {0: 1, 1: 1, 2: 1} and buffer.bytes() > 0
    x, y, z = buffer.sample(99)
    assert len(x) == 3 and z.shape == (3, 3)
    with pytest.raises(ValueError):
        ReservoirBuffer(5).sample(1)
    with pytest.raises(ValueError):
        ReservoirBuffer(0)


def test_a_rare_class_keeps_its_share_only_in_the_balanced_buffer():
    reservoir, balanced = ReservoirBuffer(100, seed=1), BalancedBuffer(100, seed=1)
    for buffer in (reservoir, balanced):
        buffer.add(np.zeros((5000, 2), np.float32), np.zeros(5000, dtype=np.int64))
        buffer.add(np.zeros((60, 2), np.float32), np.ones(60, dtype=np.int64))
    assert reservoir.class_counts().get(1, 0) < 10
    assert balanced.class_counts()[1] >= 45 and balanced.size == 100


# ---------------------------------------------------------------- real updates
@pytest.mark.parametrize("method", ["naive", "er", "derpp"])
def test_each_method_really_changes_the_weights_with_a_gradient_step(method):
    learner = OnlineLearner(model(), method=method, buffer=None if method == "naive" else ReservoirBuffer(100))
    if method != "naive":
        learner.buffer.add(*batch(0), np.zeros((16, 3), np.float32))
    before = {n: p.detach().clone() for n, p in learner.candidate.named_parameters()}
    out = learner.update(*batch(1))
    assert out["applied"] and out["head_change"] > 0 and out["encoder_change"] > 0 and learner.updates == 1
    assert set(out["events"]) == {EVENT_HEAD, EVENT_ENCODER}
    assert parameter_change(before, learner.candidate)["encoder"] == pytest.approx(out["encoder_change"], rel=1e-6)


def test_the_model_in_use_is_never_touched():
    production = model()
    before = copy.deepcopy(production.state_dict())
    learner = OnlineLearner(production, method="er", buffer=ReservoirBuffer(50))
    for i in range(5):
        learner.update(*batch(i % 3))
    assert all(torch.equal(before[k], v) for k, v in production.state_dict().items())
    assert any(not torch.equal(before[k], v) for k, v in learner.candidate.state_dict().items())


def test_head_only_training_leaves_the_encoder_exactly_as_it_was():
    learner = OnlineLearner(model(), method="naive", train_encoder=False)
    out = learner.update(*batch(1))
    assert out["head_change"] > 0 and out["encoder_change"] == 0 and out["events"] == [EVENT_HEAD]


def test_a_separate_encoder_rate_applies():
    slow = OnlineLearner(model(), method="naive", lr=1e-3, encoder_lr=1e-6)
    fast = OnlineLearner(model(), method="naive", lr=1e-3, encoder_lr=1e-3)
    assert slow.update(*batch(1))["encoder_change"] < fast.update(*batch(1))["encoder_change"] / 50


def test_a_new_class_keeps_old_weights_and_can_be_learned():
    base = model(2)
    wide = expanded(base, 3)
    assert wide.head.out_features == 3 and torch.equal(wide.head.weight[:2], base.head.weight) and base.head.out_features == 2
    with pytest.raises(ValueError):
        expanded(base, 1)
    learner = OnlineLearner(base, method="er", buffer=ReservoirBuffer(200))
    for i in range(40):
        learner.update(*batch(i % 2, seed=i))
    learner.add_class(3)
    for i in range(80):
        learner.update(*batch(2, seed=i))
    assert accuracy(learner.candidate, 2) > .9


def test_replay_keeps_old_classes_where_naive_forgets():
    def trained(method, buffer):
        learner = OnlineLearner(model(), method=method, buffer=buffer, lr=2e-3)
        for i in range(120):
            learner.update(*batch(i % 2, seed=i))
        for i in range(250):
            learner.update(*batch(2, seed=i))
        return learner.candidate
    naive = trained("naive", None)
    replayed = trained("er", ReservoirBuffer(500))
    dark = trained("derpp", ReservoirBuffer(500))
    assert accuracy(naive, 0) < .5                              # new class only: the old one is gone
    assert accuracy(replayed, 0) > .9 and accuracy(dark, 0) > .9
    assert accuracy(replayed, 2) > .9 and accuracy(dark, 2) > .9


def test_derpp_uses_stored_logits_and_only_for_classes_that_existed():
    buffer = ReservoirBuffer(50)
    learner = OnlineLearner(model(2), method="derpp", buffer=buffer, seed=1)
    for i in range(4):
        learner.update(*batch(i % 2, seed=i))
    assert buffer.z.shape[1] == 2 and buffer.size == 50 and buffer.seen == 16 * 4
    learner.add_class(3)
    out = learner.update(*batch(2))
    assert out["applied"] and "dark" in out["parts"] and np.isfinite(out["parts"]["dark"]) and "replay" in out["parts"]
    assert buffer.z.shape[1] == 3 and np.isnan(buffer.z[0, 2])


def test_a_bad_batch_changes_nothing():
    learner = OnlineLearner(model(), method="naive")
    before = {k: v.clone() for k, v in learner.candidate.state_dict().items()}
    x, y = batch(0)
    x[:] = np.nan
    out = learner.update(x, y)
    assert not out["applied"] and out["reason"] == "non_finite_loss" and learner.skipped == 1
    assert all(torch.equal(before[k], v) for k, v in learner.candidate.state_dict().items())


def test_bad_setups_are_refused():
    with pytest.raises(ValueError):
        OnlineLearner(model(), method="magic")
    with pytest.raises(ValueError):
        OnlineLearner(model(), method="er")


def test_a_prototype_change_does_not_count_as_a_neural_update():
    net = model()
    before = {n: p.detach().clone() for n, p in net.named_parameters()}
    memory = PrototypeMemory("ncm")
    memory.add_class("new", np.ones((5, 64), np.float32))
    assert parameter_change(before, net) == {"head": 0.0, "encoder": 0.0}


# ---------------------------------------------------------------- checks
def report(**kw):
    base = {"macro_f1": .8, "reps": {"a": [18, 20], "b": [16, 20]}, "false_fires": 10, "none_minutes": 5.0, "wrong_reps": 2, "reps_total": 40}
    return {**base, **kw}


def test_a_good_candidate_passes():
    assert check_candidate(report(macro_f1=.81), report(), inference_ms=1.0) == []
    assert check_candidate(report(), None) == []


@pytest.mark.parametrize("change,reason", [
    (dict(macro_f1=.7), "macro_f1_dropped"),
    (dict(reps={"a": [12, 20], "b": [16, 20]}), "forgot:a"),
    (dict(reps={"b": [16, 20]}), "forgot:a"),
    (dict(false_fires=20), "more_false_fires"),
    (dict(wrong_reps=8), "more_wrong_gestures"),
    (dict(macro_f1=float("nan")), "non_finite_metrics"),
])
def test_each_failure_is_named(change, reason):
    assert reason in check_candidate(report(**change), report())


def test_a_new_class_must_be_recognised_and_is_not_counted_as_forgotten():
    good = report(reps={"a": [18, 20], "b": [16, 20], "new": [12, 20]})
    assert check_candidate(good, report(), new_classes=["new"]) == []
    weak = report(reps={"a": [18, 20], "b": [16, 20], "new": [4, 20]})
    assert "new_class_recall:new" in check_candidate(weak, report(), new_classes=["new"])
    assert "new_class_recall:new" in check_candidate(report(), report(), new_classes=["new"])


def test_slow_or_broken_weights_are_refused():
    assert check_candidate(report(), report(), inference_ms=50.0, limits=ValidationLimits(max_inference_ms=8.0))[0].startswith("too_slow")
    bad = {"w": torch.tensor([1.0, float("inf")])}
    assert not finite_weights(bad) and "non_finite_weights" in check_candidate(report(), report(), state=bad)
    assert finite_weights({"w": torch.ones(2), "n": torch.ones(1, dtype=torch.long)})


# ---------------------------------------------------------------- swap and rollback
def weights(value):
    return {"w": torch.full((2,), float(value))}


def production_value(manager):
    return float(manager.load()["state"]["w"][0])


def test_a_failed_candidate_leaves_production_exactly_as_it_was(tmp_path):
    manager = ModelManager(tmp_path)
    manager.initialize(weights(1), {"classes": ["a"]})
    assert not manager.submit(weights(2), {"metrics": {"macro_f1": .1}}, ["forgot:a"])
    assert production_value(manager) == 1.0 and not manager.has("previous") and manager.has("candidate")
    assert manager.history[-1]["event"] == "rejected" and manager.history[-1]["reasons"] == ["forgot:a"]


def test_a_good_candidate_becomes_production_and_rollback_restores_the_one_before(tmp_path):
    manager = ModelManager(tmp_path)
    manager.initialize(weights(1), {})
    assert manager.submit(weights(2), {"metrics": {"macro_f1": .9}, "data_snapshot": "s1"}, [])
    assert production_value(manager) == 2.0 and manager.has("previous") and not manager.has("candidate")
    assert manager.rollback() and production_value(manager) == 1.0
    assert not manager.rollback()                     # one step back, and it says so when there is none
    assert [h["event"] for h in manager.history] == ["initialized", "promoted", "rolled_back"]


def test_the_history_survives_and_initialize_never_overwrites(tmp_path):
    manager = ModelManager(tmp_path)
    manager.initialize(weights(1), {})
    manager.initialize(weights(9), {})
    assert production_value(manager) == 1.0
    again = ModelManager(tmp_path)
    assert production_value(again) == 1.0 and again.history[0]["event"] == "initialized"
    assert again.load()["info"]["created"] > 0
