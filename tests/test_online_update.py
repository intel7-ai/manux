"""A background learning round end to end on a small fake store (CPU): production, candidate, checks, swap, rollback."""
import numpy as np
import pytest

torch = pytest.importorskip("torch")

from src.online.live import save_encoder  # noqa: E402
from src.online.models import make_model  # noqa: E402
from src.online.samples import GUIDED_SCHEDULED, USER_CONFIRMED, USER_CORRECTION, SampleStore  # noqa: E402
from src.online.update_job import reason_ko, run_update  # noqa: E402
from src.online.versions import ModelManager  # noqa: E402
from tests.test_online_data import as_frame, hand_world  # noqa: E402

CLASSES = ["none", "thumb_index"]


def frames(kind, n=45, seed=0):
    rng = np.random.default_rng(seed)
    pinch = {"none": 0.0, "thumb_index": 1.0, "new": .5}[kind]
    wiggle = .15 if kind == "new" else 0.0
    return [as_frame(i / 30, hand_world(pinch=pinch, wiggle=wiggle) + rng.normal(0, .002, (21, 3))) for i in range(n)]


def make_root(tmp_path, train_well=True):
    root = tmp_path / "online"
    store = SampleStore(root)
    for session in ("g1", "g2", "val"):
        for rep in range(6):
            for label in CLASSES:
                store.add_frames(frames(label, seed=rep), label=label, label_source=GUIDED_SCHEDULED, session=session,
                                 sample_id=f"{session}-{rep}-{label}", extra={"kind": "do" if label != "none" else "free", "context_frames": 15 if label != "none" else 0})
    torch.manual_seed(0)
    model = make_model("tcn", 2, 15, 16)
    if train_well:
        from src.online.baseline import train_encoder
        from src.online.dataset import build_dataset, target_positions
        data = build_dataset(store, store.samples(), CLASSES)
        train_encoder(model, data, target_positions(data, {s.sample_id for s in data.samples if s.session != "val"}), 15, epochs=8, batch=32)
    save_encoder(model, {"kind": "tcn", "window": 15, "embedding": 16, "classes": CLASSES, "validation_sessions": ["val"]}, root)
    return root, store


def fast(root, **kw):
    return run_update(root, device="cpu", passes=2, min_updates=20, buffer_size=300, **kw)


def test_nothing_to_learn_says_so_and_still_sets_up_production(tmp_path):
    root, _ = make_root(tmp_path, train_well=False)
    out = fast(root)
    assert out["status"] == "nothing" and ModelManager(root).has("production")


def test_corrections_make_a_real_update_and_a_new_class(tmp_path):
    root, store = make_root(tmp_path)
    for i in range(5):
        store.add_frames(frames("new", seed=i), label="new", label_source=USER_CONFIRMED, session=f"u{i}", approved=True)
    store.add_frames(frames("thumb_index", seed=9), label="thumb_index", label_source=USER_CORRECTION, session="u9", approved=True)
    before = {k: v.clone() for k, v in ModelManager(root).load()["state"].items()} if ModelManager(root).has("production") else None
    events = []
    out = fast(root, emit=lambda e, **k: events.append(e), encoder_lr=1e-3)
    assert out["status"] in ("promoted", "rejected") and out["updates"] >= 20
    assert set(out["events"]) == {"head_updated", "encoder_updated"} and out["new_classes"] == ["new"]
    assert "update" in events and "checking" in events
    manager = ModelManager(root)
    assert manager.history[-1]["event"] == out["status"]
    if out["status"] == "promoted":
        assert manager.load()["info"]["classes"] == CLASSES + ["new"] and manager.has("previous")
        assert manager.rollback() and manager.load()["info"]["classes"] == CLASSES
    else:
        assert manager.load()["info"]["classes"] == CLASSES and out["problems"]
    assert out["metrics"]["after"] is not None and out["metrics"]["learned"] is not None   # one "new" sample was kept aside


def test_a_candidate_that_forgets_is_refused_and_production_stays(tmp_path, monkeypatch):
    root, store = make_root(tmp_path)
    for i in range(3):
        store.add_frames(frames("none", seed=i), label="thumb_index", label_source=USER_CORRECTION, session=f"u{i}", approved=True)
    import src.online.update_job as job
    monkeypatch.setattr(job, "check_candidate", lambda *a, **k: ["forgot:thumb_index"])
    fast(root)
    first = {k: v.clone() for k, v in ModelManager(root).load()["state"].items()}
    out = fast(root)
    assert out["status"] == "rejected" and "forgot:thumb_index" in out["problems"]
    after = ModelManager(root).load()["state"]
    assert all(torch.equal(first[k], after[k]) for k in first)


def test_reasons_read_in_plain_korean():
    assert reason_ko("forgot:thumb_ring", {"thumb_ring": "엄지+약지"}) == "'엄지+약지'을(를) 잊음"
    assert "느림" in reason_ko("too_slow:20ms") and reason_ko("more_false_fires") == "오작동이 늘어남"


def test_by_default_only_the_classifier_learns(tmp_path):
    root, store = make_root(tmp_path)
    for i in range(3):
        store.add_frames(frames("thumb_index", seed=i), label="thumb_index", label_source=USER_CORRECTION, session=f"u{i}", approved=True)
    out = fast(root)
    assert out["events"] == ["head_updated"]
