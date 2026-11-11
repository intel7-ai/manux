"""Interference during guided capture is what the rule engine itself read besides the target (docs/far-hand-loss.md)."""
from types import SimpleNamespace

from src.capture_rules import detect_interfering_signals
from src.gesture_catalog import BY_KEY


def test_another_gesture_the_engine_fired_is_interference_the_target_itself_is_not():
    other = BY_KEY["thumb_index"]
    target = BY_KEY["thumb_ring"]
    assert other.action and other.action != target.action
    engine_read_other = SimpleNamespace(action=other.action, active_gesture=f"{other.label}_pinch")
    assert "thumb_index" in detect_interfering_signals(None, engine_read_other, "thumb_ring")
    engine_read_target = SimpleNamespace(action=target.action, active_gesture=f"{target.label}_pinch")
    assert "thumb_ring" not in detect_interfering_signals(None, engine_read_target, "thumb_ring")


def test_nothing_read_means_no_interference():
    idle = SimpleNamespace(action="none", active_gesture="none")
    assert detect_interfering_signals(None, idle, "thumb_ring") == []
