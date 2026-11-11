"""Phase 2 of online learning: encoders, windows, session folds, contrastive loss, evaluation (no camera)."""
import numpy as np
import pytest

torch = pytest.importorskip("torch")

from src.online.baseline import _first_run, evaluate_samples, inference_ms, train_encoder  # noqa: E402
from src.online.dataset import build_dataset, session_folds, target_positions, window_positions  # noqa: E402
from src.online.models import CausalTCN, FrameMLP, GRUEncoder, dilations_for, make_model, supervised_contrastive_loss  # noqa: E402
from src.online.samples import GUIDED_SCHEDULED, Sample, SampleStore  # noqa: E402
from src.online.splits import leaked_groups, split_by_session  # noqa: E402
from src.tcn_features import FEATURES  # noqa: E402
from tests.test_online_data import as_frame, hand_world  # noqa: E402

CLASSES = ["none", "thumb_index"]


def fake(session, label, sid=None):
    return Sample(sample_id=sid or f"{session}-{label}", label=label, label_source=GUIDED_SCHEDULED, session=session, created=0.0,
                  duration=1.0, fps=30.0, frames=10, tracking_quality=1.0, quality_score=1.0)


# ---------------------------------------------------------------- models
@pytest.mark.parametrize("kind", ["tcn", "gru", "mlp"])
@pytest.mark.parametrize("window", [15, 32, 64])
def test_every_model_gives_logits_and_an_embedding_of_the_asked_size(kind, window):
    model = make_model(kind, 5, window, embedding=128)
    x = torch.randn(3, window, FEATURES)
    assert model(x).shape == (3, 5) and model.embed(x).shape == (3, 128)


def test_the_tcn_covers_the_window_and_the_first_size_matches_the_old_model():
    assert dilations_for(15) == (1, 2, 4) and CausalTCN(3, dilations=(1, 2, 4)).receptive_field == 15
    for window in (16, 32, 64):
        assert CausalTCN(3, dilations=dilations_for(window)).receptive_field >= window - 1


@pytest.mark.parametrize("kind", ["tcn"])
def test_a_tcn_step_never_sees_a_later_frame(kind):
    model = make_model(kind, 3, 32).eval()
    x = torch.randn(1, 32, FEATURES)
    changed = x.clone()
    changed[:, 20:] += 5.0
    with torch.no_grad():
        a, b = model.sequence(x), model.sequence(changed)
    assert torch.allclose(a[:, :, :20], b[:, :, :20])
    assert not torch.allclose(a[:, :, 20:], b[:, :, 20:])


def test_a_gru_step_never_sees_a_later_frame():
    model = GRUEncoder(3).eval()
    x = torch.randn(1, 20, FEATURES)
    changed = x.clone()
    changed[:, 12:] += 5.0
    with torch.no_grad():
        a, _ = model.gru(x)
        b, _ = model.gru(changed)
    assert torch.allclose(a[:, :12], b[:, :12])


def test_the_mlp_looks_at_the_last_frame_only():
    model = FrameMLP(3).eval()
    x = torch.randn(1, 16, FEATURES)
    changed = x.clone()
    changed[:, :-1] += 5.0
    with torch.no_grad():
        assert torch.allclose(model(x), model(changed))


@pytest.mark.parametrize("kind", ["tcn", "gru", "mlp"])
def test_one_optimizer_step_really_changes_encoder_and_head(kind):
    model = make_model(kind, 3, 16)
    before = {n: p.detach().clone() for n, p in model.named_parameters()}
    opt = torch.optim.SGD(model.parameters(), lr=.1)
    torch.nn.functional.cross_entropy(model(torch.randn(8, 16, FEATURES)), torch.randint(0, 3, (8,))).backward()
    opt.step()
    assert sum(float((p - before[n]).abs().sum()) for n, p in model.named_parameters() if n.startswith("head.")) > 0
    assert sum(float((p - before[n]).abs().sum()) for n, p in model.named_parameters() if not n.startswith("head.")) > 0
    assert model.encoder_parameters() and all(p is not model.head.weight for p in model.encoder_parameters())


def test_an_unknown_model_is_refused():
    with pytest.raises(ValueError):
        make_model("transformer", 3, 16)


# ---------------------------------------------------------------- contrastive loss
def test_supcon_prefers_clusters_to_a_mix():
    labels = torch.tensor([0, 0, 1, 1])
    together = torch.tensor([[1.0, 0], [.99, .01], [0, 1.0], [.01, .99]])
    mixed = torch.tensor([[1.0, 0], [0, 1.0], [1.0, 0], [0, 1.0]])
    assert supervised_contrastive_loss(together, labels) < supervised_contrastive_loss(mixed, labels)


def test_supcon_without_positives_is_a_differentiable_zero_and_stays_finite():
    z = torch.randn(4, 8, requires_grad=True)
    loss = supervised_contrastive_loss(z, torch.tensor([0, 1, 2, 3]))
    assert float(loss) == 0.0
    loss.backward()
    assert z.grad is not None and torch.isfinite(z.grad).all()
    single = torch.randn(1, 8, requires_grad=True)
    assert float(supervised_contrastive_loss(single, torch.tensor([0]))) == 0.0
    mixed = torch.randn(6, 8, requires_grad=True)
    out = supervised_contrastive_loss(mixed, torch.tensor([0, 0, 1, 2, 3, 4]))       # only two anchors have a positive
    out.backward()
    assert torch.isfinite(out) and torch.isfinite(mixed.grad).all()


def test_supcon_keeps_identical_vectors_finite():
    z = torch.ones(4, 8, requires_grad=True)
    out = supervised_contrastive_loss(z, torch.tensor([0, 0, 1, 1]))
    out.backward()
    assert torch.isfinite(out) and torch.isfinite(z.grad).all()


# ---------------------------------------------------------------- windows
def filled_store(tmp_path, sessions=("s1", "s2"), reps=6):
    store = SampleStore(tmp_path / "online")
    for session in sessions:
        for rep in range(reps):
            for label, pinch in (("thumb_index", 1.0), ("none", 0.0)):
                frames = [as_frame(i / 30, hand_world(pinch=pinch + (0 if label == "thumb_index" else .05 * np.sin(i)))) for i in range(45)]
                store.add_frames(frames, label=label, label_source=GUIDED_SCHEDULED, session=session,
                                 sample_id=f"{session}-{rep}-{label}", extra={"kind": "do" if label != "none" else "free",
                                                                              "context_frames": 15 if label != "none" else 0})
    return store


def test_labels_skip_the_past_and_the_first_frames_of_a_repetition(tmp_path):
    store = filled_store(tmp_path, sessions=("s1",), reps=1)
    data = build_dataset(store, store.samples(), CLASSES)
    assert data.frames.shape[1] == FEATURES and (data.frames[0] == 0).all() and data.labels[0] == -1
    gesture = [i for i, s in enumerate(data.samples) if s.label == "thumb_index"][0]
    rows = np.flatnonzero(data.sample_of == gesture)
    assert (data.labels[rows[:25]] == -1).all() and (data.labels[rows[25:]] == 1).all()
    assert data.do_start[gesture] == rows[15]
    other = np.flatnonzero(data.sample_of == 1 - gesture)
    assert (data.labels[other] == 0).all()


def test_a_sample_outside_the_classes_is_left_out_and_a_lost_hand_is_no_target(tmp_path):
    store = SampleStore(tmp_path / "s")
    frames = [as_frame(i / 30, hand_world()) for i in range(20)]
    frames[5] = {"t": 5 / 30}
    store.add_frames(frames, label="none", label_source=GUIDED_SCHEDULED, session="a", sample_id="keep")
    store.add_frames(frames, label="v_sign", label_source=GUIDED_SCHEDULED, session="a", sample_id="other")
    data = build_dataset(store, store.samples(), CLASSES)
    assert [s.sample_id for s in data.samples] == ["keep"]
    assert data.labels[6] == -1 and len(target_positions(data)) == 19


def test_a_window_never_reaches_into_the_sample_before(tmp_path):
    store = filled_store(tmp_path, sessions=("s1",), reps=1)
    data = build_dataset(store, store.samples(), CLASSES)
    second = np.flatnonzero(data.sample_of == 1)
    rows = window_positions(data.starts, second[:3], 16)
    assert (rows[:, -1] == second[:3]).all()
    assert ((rows == 0) | (rows >= second[0])).all()
    assert (rows[0, :-1] == 0).all()


def test_target_positions_keep_only_the_chosen_samples(tmp_path):
    store = filled_store(tmp_path)
    data = build_dataset(store, store.samples(), CLASSES)
    train, test = split_by_session(data.samples, ["s2"])
    positions = target_positions(data, {s.sample_id for s in train})
    assert {data.samples[i].session for i in data.sample_of[positions]} == {"s1"} and not leaked_groups(train, test)


# ---------------------------------------------------------------- folds
def test_folds_hold_every_session_once_and_spread_a_class():
    samples = [fake("a", "x"), fake("b", "x"), fake("c", "x"), fake("d", "y"), fake("e", "y"), fake("f", "z")]
    folds = session_folds(samples, ["x", "y", "z"], k=3)
    assert sorted(s for f in folds for s in f) == list("abcdef")
    assert all(len(set(f) & {"a", "b", "c"}) == 1 for f in folds)
    assert all(len(set(f) & {"d", "e"}) <= 1 for f in folds)


def test_a_session_of_other_classes_only_is_not_placed():
    assert session_folds([fake("a", "x"), fake("b", "other")], ["x"], k=2) == [["a"], []]


# ---------------------------------------------------------------- train and score
def test_first_run_finds_the_frame_that_completes_it():
    assert _first_run(np.array([1, 1, 0, 1, 1, 1, 1], bool), 4) == 6
    assert _first_run(np.array([1, 1, 1], bool), 4) == -1


@pytest.mark.parametrize("kind", ["tcn", "gru", "mlp"])
def test_a_model_learns_an_easy_pair_on_one_session_and_scores_on_another(tmp_path, kind):
    store = filled_store(tmp_path)
    data = build_dataset(store, store.samples(), CLASSES)
    train, test = split_by_session(data.samples, ["s2"])
    torch.manual_seed(0)
    model = make_model(kind, 2, 16)
    info = train_encoder(model, data, target_positions(data, {s.sample_id for s in train}), 16, epochs=16, batch=32, seed=0)
    assert len(info["losses"]) == 16 and info["losses"][-1] < info["losses"][0]
    index = [i for i, s in enumerate(data.samples) if s.session == "s2"]
    report = evaluate_samples(model, data, index, 16)
    assert report["accuracy"] > .9 and report["reps_total"] == 6 and report["reps_hit"] >= 5
    assert set(report["f1"]) == set(CLASSES) and report["none_minutes"] > 0 and report["false_fires"] <= 1
    assert report["reaction_median"] is not None
    assert np.asarray(report["confusion"]).sum() == report["windows"]


def test_training_with_supcon_runs_and_lowers_the_loss(tmp_path):
    store = filled_store(tmp_path, sessions=("s1",))
    data = build_dataset(store, store.samples(), CLASSES)
    model = make_model("tcn", 2, 16)
    info = train_encoder(model, data, target_positions(data), 16, epochs=6, batch=32, supcon_weight=.2)
    assert np.isfinite(info["losses"]).all() and info["losses"][-1] < info["losses"][0]


def test_the_same_seed_gives_the_same_training(tmp_path):
    store = filled_store(tmp_path, sessions=("s1",), reps=2)
    data = build_dataset(store, store.samples(), CLASSES)
    runs = []
    for _ in range(2):
        torch.manual_seed(3)
        model = make_model("tcn", 2, 16)
        runs.append(train_encoder(model, data, target_positions(data), 16, epochs=2, batch=16, seed=3)["losses"])
    assert runs[0] == runs[1]


def test_latency_is_measured_in_positive_milliseconds():
    assert inference_ms(make_model("mlp", 2, 16), 16, FEATURES, repeats=20) > 0
