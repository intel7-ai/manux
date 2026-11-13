"""Reproduce connection-save outcomes without a provider call or OS input."""
from manux.compat.hand_mouse import custom_postures, llm_binding as lb, posture_teach as pt
from manux.compat.hand_mouse.action_bindings import ActionBindingRegistry


def proposal(**values):
    return {"summary": "synthetic", "questions": [], "proposals": [{
        "gesture_key": lb.NEW, "new_name": "합성 테스트 동작", "new_trigger": "hold",
        "action_type": "hotkey", "action_value": "ctrl+t", "enabled": True, **values,
    }]}


def test_named_shape_connection_saves_without_recordings_or_a_trained_model(tmp_path):
    registry = ActionBindingRegistry(tmp_path / "bindings.json")
    key = custom_postures.add("합성 테스트 손 모양")
    assert not pt.load_examples() and not pt.MODEL_PATH.exists() and not pt.REC_DIR.exists()
    rows = lb.check(proposal(new_posture=key), lb.gesture_list(registry))["proposals"]
    assert rows[0]["ok"] and rows[0]["checked"]
    history = lb.History(tmp_path / "history.json")
    saved = lb.apply(rows, registry, history=history)
    assert saved and not any(line.startswith("저장하지 못함") for line in saved)
    restored = ActionBindingRegistry(registry.path)
    assert any(c["base_posture"] == key and c["action_value"] == "ctrl+t"
               for c in restored.composite_gestures.values())
    assert not pt.MODEL_PATH.exists(), "Connecting a command does not train a recognizer"


def test_a_shape_id_used_as_a_gesture_id_is_rejected_before_saving(tmp_path):
    registry = ActionBindingRegistry(tmp_path / "bindings.json")
    key = custom_postures.add("합성 테스트 손 모양")
    row = lb.check(proposal(gesture_key=key), lb.gesture_list(registry))["proposals"][0]
    assert not row["ok"] and row["problem"] == "없는 동작이에요"
    assert lb.apply([row], registry) == []
    assert not registry.composite_gestures


def test_existing_connection_can_be_changed_without_training_data(tmp_path):
    registry = ActionBindingRegistry(tmp_path / "bindings.json")
    rows = lb.check(proposal(gesture_key="thumb_ring"), lb.gesture_list(registry))["proposals"]
    assert rows[0]["ok"]
    assert lb.apply(rows, registry)
    assert registry.get_binding("thumb_ring")["action_value"] == "ctrl+t"
