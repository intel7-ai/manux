import copy
import pytest

from manux.settings.tcn_policy import GesturePolicies


def card():
    return {"gestures": {"left": "pinch.thumb_middle", "right": "pinch.thumb_ring"},
            "class_model_ids": {"left": "left-v1", "right": "right-v1"},
            "class_default_modes": {"left": "shadow", "right": "shadow"}}


def test_choices_persist_but_retraining_only_resets_changed_classifier(tmp_path):
    path = tmp_path / "policy.json"
    policy = GesturePolicies(path)
    before = card()
    assert set(policy.modes(before).values()) == {"shadow"}
    policy.set(before, "pinch.thumb_middle", "apply", "left-v1")
    policy.set(before, "pinch.thumb_ring", "rules", "right-v1")
    after = copy.deepcopy(before)
    after["class_model_ids"]["left"] = "left-v2"
    reloaded = GesturePolicies(path)
    assert reloaded.modes(before) == {"pinch.thumb_middle": "apply", "pinch.thumb_ring": "rules"}
    assert reloaded.modes(after) == {"pinch.thumb_middle": "shadow", "pinch.thumb_ring": "rules"}
    saved = path.read_bytes()
    with pytest.raises(ValueError, match="모델이 바뀌"):
        reloaded.set(after, "pinch.thumb_middle", "apply", "left-v1")
    assert path.read_bytes() == saved


def test_failed_save_preserves_memory_and_disk(tmp_path, monkeypatch):
    import manux.settings.tcn_policy as module
    policy = GesturePolicies(tmp_path / "policy.json")
    policy.set(card(), "pinch.thumb_ring", "rules", "right-v1")
    before = policy.path.read_bytes()
    monkeypatch.setattr(module.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("busy")))
    with pytest.raises(OSError):
        policy.set(card(), "pinch.thumb_ring", "apply", "right-v1")
    assert policy.path.read_bytes() == before
    assert policy.modes(card())["pinch.thumb_ring"] == "rules"
    assert list(tmp_path.glob("*.tmp")) == []
