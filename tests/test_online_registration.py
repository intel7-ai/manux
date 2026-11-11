"""Phase 3: registering a gesture step by step, and the encoder in the app (a small untrained encoder is enough for the plumbing)."""
import numpy as np
import pytest

from src.online.memory import PrototypeMemory
from src.online.registration import (MIN_GOOD_DEMOS, RegistrationConfig, RegistrationError, RegistrationSession, USER_SOURCE, check_name)
from src.online.samples import SUPERVISED_SOURCES, SampleStore, USER_CONFIRMED
from tests.test_online_data import hand_world


def base_memory():
    memory = PrototypeMemory("ncm")
    rng = np.random.default_rng(0)
    for i, name in enumerate(("none", "thumb_index")):
        c = np.zeros(8)
        c[i] = 1.0
        memory.add_class(name, c + rng.normal(0, .02, (40, 8)), demo_ids=np.arange(40) % 4, source="guided")
    return memory


def fake_embed(frames):
    """Pinched hand -> axis 2, open hand -> axis 0 (the "none" class), as a stand-in for the encoder."""
    out = np.zeros((len(frames), 8), np.float32)
    rng = np.random.default_rng(len(frames))
    for i, f in enumerate(frames):
        w = np.asarray(f["world"])
        out[i, 2 if np.linalg.norm(w[8] - w[4]) < .03 else 0] = 1.0
    return out + rng.normal(0, .01, out.shape).astype(np.float32)


def as_lm(world):
    return (world * .5 + np.array([.5, .5, 0])).tolist()


def drive(session, pinch_in_do=True, hand=True, fps=30):
    """Runs the session to its end, doing the gesture whenever it asks."""
    t, steps = 0.0, 0
    while session.active and steps < 3000:
        world = hand_world(pinch=1.0 if (session.state == "do" and pinch_in_do) else 0.0)
        session.update(t, as_lm(world) if hand else None, world.tolist() if hand else None)
        t += 1 / fps
        steps += 1
    return steps


@pytest.fixture
def session(tmp_path):
    return RegistrationSession("내 동작", memory=base_memory(), embed=fake_embed, store=SampleStore(tmp_path / "s"), meta={"pc": "pc-a"})


def test_the_session_walks_through_every_demonstration_then_asks(session):
    states = []
    t = 0.0
    while session.active:
        states.append((session.state, session.demo))
        world = hand_world(pinch=1.0 if session.state == "do" else 0.0)
        session.update(t, as_lm(world), world.tolist())
        t += 1 / 30
    assert session.state == "review" and session.demo == 5 and len(session.demos) == 5
    assert [s for s, _ in states if s == "do"] and states[0] == ("prep", 0)
    assert sum(1 for a, b in zip(states, states[1:]) if a[0] == "prep" and b[0] == "do") == 5
    assert t == pytest.approx(5 * 4.5, abs=1.0)
    assert session.view()["report"]["recognised"] == 5 and session.report["reliable"]


def test_a_lost_hand_stops_the_clock(session):
    world = hand_world()
    session.update(0.0, as_lm(world), world.tolist())
    left = session.view()["seconds"]
    for i in range(60):
        session.update(.03 * (i + 1), None, None)
    assert session.view()["seconds"] == left and session.state == "prep" and not session.view()["hand"]


def test_nothing_is_kept_until_the_person_approves(session):
    drive(session)
    assert "내 동작" not in session.memory.classes and session.store.stats()["samples"] == 0
    session.cancel()
    assert session.state == "cancelled" and "내 동작" not in session.memory.classes and session.store.stats()["samples"] == 0
    with pytest.raises(RegistrationError):
        session.approve()


def test_approval_adds_the_class_at_once_and_stores_confirmed_demonstrations(session, tmp_path):
    drive(session)
    before = {k: v.prototypes.copy() for k, v in session.memory.classes.items()}
    done = session.approve(tmp_path / "memory.json")
    assert done == {"event": "class_added", "label": "내 동작", "prototypes": 1, "neural_weights_changed": False}
    assert session.state == "done" and session.memory.classes["내 동작"].source == USER_SOURCE
    assert all(np.array_equal(before[k], session.memory.classes[k].prototypes) for k in before)
    z = np.zeros(8, np.float32)
    z[2] = 1.0
    assert session.memory.classify(z)["label"] == "내 동작"
    stored = session.store.samples(sources=SUPERVISED_SOURCES)
    assert len(stored) == 5 and {s.label_source for s in stored} == {USER_CONFIRMED} and all(s.approved for s in stored)
    assert stored[0].pc == "pc-a" and stored[0].extra["reason"] == "registration" and stored[0].extra["context_frames"] > 0
    assert PrototypeMemory.load(tmp_path / "memory.json").labels[-1] == "내 동작"


def test_a_gesture_the_person_did_not_really_do_is_not_called_reliable(tmp_path):
    session = RegistrationSession("x", memory=base_memory(), embed=fake_embed, store=SampleStore(tmp_path / "s"))
    drive(session, pinch_in_do=False)        # the "gesture" is just the open hand, which the rest looks like too
    assert session.state == "review" and not session.report["reliable"]


def test_too_few_good_demonstrations_fail(tmp_path):
    session = RegistrationSession("x", memory=base_memory(), embed=fake_embed, store=SampleStore(tmp_path / "s"),
                                  config=RegistrationConfig(do_seconds=.3))
    drive(session)
    assert session.state == "failed" and session.message and MIN_GOOD_DEMOS == 3


def test_names_are_checked():
    memory = base_memory()
    assert check_name("  새 동작 ", memory) == "새 동작"
    for bad in ("", "  ", "none", "unknown", "thumb_index", "가" * 31):
        with pytest.raises(RegistrationError):
            check_name(bad, memory)


# ---------------------------------------------------------------- the encoder in the app
torch = pytest.importorskip("torch")

from src.online.live import LiveEncoder, LiveRecognizer, encoder_ready, save_encoder  # noqa: E402
from src.online.models import make_model  # noqa: E402


@pytest.fixture
def encoder(tmp_path):
    torch.manual_seed(0)
    model = make_model("tcn", 3, 15, 16)
    assert not encoder_ready(tmp_path)
    version = save_encoder(model, {"kind": "tcn", "window": 15, "embedding": 16, "classes": ["a", "b", "c"]}, tmp_path)
    enc = LiveEncoder(tmp_path)
    assert enc.version == version
    return enc


def test_the_loaded_encoder_gives_the_saved_answers_and_one_row_per_frame(encoder):
    frames = [{"t": i / 30, "lm": as_lm(hand_world()), "world": hand_world().tolist()} for i in range(20)]
    frames[3] = {"t": 3 / 30, "lm": None, "world": None}
    z = encoder.embed_frames(frames, .5625)
    assert z.shape == (20, 16) and np.isfinite(z).all()
    assert encoder.embed_frames([], .5625).shape == (0, 16)


def test_an_encoder_for_other_features_is_refused(encoder, tmp_path):
    import json
    path = tmp_path / "encoder.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["feature_version"] = "old"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError):
        LiveEncoder(tmp_path)


def test_the_recognizer_needs_frames_in_a_row_and_a_lost_hand_clears_it(encoder):
    world = hand_world()
    frame = [as_lm(world), world.tolist()]
    z = encoder.embed_frames([{"t": 0.0, "lm": frame[0], "world": frame[1]}] * 5, .5625)
    memory = PrototypeMemory("ncm", threshold_factor=100.0)
    memory.add_class("g", z)
    live = LiveRecognizer(encoder, memory)
    fired = [live.step(i / 30, *frame) for i in range(6)]
    assert fired[:3] == [None, None, None] and fired[3] == "g" and fired[4:] == [None, None] and live.current == "g"
    assert live.step(.3, None, None) is None and live.current is None


def test_a_known_class_needs_the_classifier_to_agree_and_a_new_one_needs_it_unsure(encoder):
    world = hand_world()
    frame = [as_lm(world), world.tolist()]
    z = encoder.embed_frames([{"t": 0.0, "lm": frame[0], "world": frame[1]}] * 3, .5625)[-1:]
    probs = encoder.head_probabilities(z)[0]
    top = encoder.info["classes"][int(probs.argmax())]
    other = [c for c in encoder.info["classes"] if c != top][0]
    wide = dict(threshold_factor=1000.0, min_threshold=2.0)
    memory = PrototypeMemory("ncm", **wide)
    memory.add_class(top, z)
    assert LiveRecognizer(encoder, memory, confidence=0.0).decide(z) == top
    assert LiveRecognizer(encoder, memory, confidence=1.01).decide(z) == "unknown"
    memory = PrototypeMemory("ncm", **wide)
    memory.add_class(other, z)                 # the nearest prototype says one class, the classifier another
    assert LiveRecognizer(encoder, memory, confidence=0.0).decide(z) == "unknown"
    memory = PrototypeMemory("ncm", **wide)
    memory.add_class("내 동작", z)
    assert LiveRecognizer(encoder, memory, confidence=1.01).decide(z) == "내 동작"
    assert LiveRecognizer(encoder, memory, confidence=0.0).decide(z) == "unknown"
    assert encoder.confident(z, 0.0).all() and not encoder.confident(z, 1.01).any()


def test_a_demonstration_taken_for_another_class_is_reported_only_when_it_really_was(tmp_path):
    memory = base_memory()
    session = RegistrationSession("x", memory=memory, embed=fake_embed, store=SampleStore(tmp_path / "s"))
    drive(session)
    assert session.report["confused_with"] == "" and session.report["conflicts"] == []
    # one demonstration of three lies on an old class, the others elsewhere: judged by the others it is that old class
    like_old, elsewhere = np.zeros((30, 8), np.float32), np.zeros((30, 8), np.float32)
    like_old[:, 1] = 1.0
    elsewhere[:, 3] = 1.0
    other = RegistrationSession("y", memory=base_memory(), embed=fake_embed, store=SampleStore(tmp_path / "t"))
    report = other.check([like_old, elsewhere, elsewhere])
    assert report["confused_with"] == "thumb_index" and report["recognised"] == 2


def test_a_classifier_sure_of_an_old_class_stops_the_new_one_in_the_check(tmp_path):
    session = RegistrationSession("x", memory=base_memory(), embed=fake_embed, store=SampleStore(tmp_path / "s"),
                                  confident=lambda z: np.ones(len(z), bool))
    drive(session)
    assert session.report["recognised"] == 0 and not session.report["reliable"]


def test_frame_by_frame_gives_the_same_embedding_as_the_whole_sequence(encoder, monkeypatch):
    """A window's first frame needs a real change since the frame before: without it the live model saw other numbers than in training."""
    frames = []
    for i in range(30):
        world = hand_world(pinch=(i % 12) / 12)
        frames.append({"t": i / 30, "lm": as_lm(world), "world": world.tolist()})
    batch = encoder.embed_frames(frames, .5625)
    seen = []
    real = encoder.embed_windows
    monkeypatch.setattr(encoder, "embed_windows", lambda x: (seen.append(real(x)), seen[-1])[1])
    memory = PrototypeMemory("ncm")
    memory.add_class("g", batch)
    live = LiveRecognizer(encoder, memory, aspect=.5625)
    for f in frames:
        live.step(f["t"], f["lm"], f["world"])
    assert np.allclose(np.concatenate(seen), batch, atol=1e-5)


def test_an_unknown_frame_breaks_the_run(encoder):
    world = hand_world()
    frame = [as_lm(world), world.tolist()]
    memory = PrototypeMemory("ncm", threshold_factor=1.0, min_threshold=0.0)
    other = np.zeros((5, 16), np.float32)
    other[:, 0] = 1.0
    memory.add_class("far", other)
    live = LiveRecognizer(encoder, memory)
    assert all(live.step(i / 30, *frame) is None for i in range(8)) and live.current is None
