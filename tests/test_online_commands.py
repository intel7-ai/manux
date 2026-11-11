"""Connecting a registered gesture to a command: the small safety gate, saving, and the runtime (no OS input: dry run)."""
import time

import numpy as np
import pytest

from src.action_bindings import ActionBindingRegistry
from src.online import commands
from src.online.commands import CommandError, CommandGate, check_hotkey, choices, command_of, command_text, forget_command, set_command


@pytest.fixture
def registry(tmp_path, monkeypatch):
    reg = ActionBindingRegistry(tmp_path / "bindings.json")
    monkeypatch.setattr(commands, "get_action_registry", lambda: reg)
    monkeypatch.setattr("src.qt_runtime.get_action_registry", lambda: reg)
    monkeypatch.setattr("src.qt_runtime.foreground_window_title", lambda: "Notepad")   # not whatever is really in front
    return reg


def run(gate, seconds, label="g", allowed=True, start=0.0, fps=30):
    fired = []
    for i in range(int(seconds * fps)):
        t = start + i / fps
        if gate.step(t, label, allowed):
            fired.append(t)
    return fired


def test_a_command_fires_once_after_the_hold():
    gate = CommandGate()
    fired = run(gate, 2.0)
    assert len(fired) == 1 and fired[0] == pytest.approx(.5, abs=.05)
    assert gate.progress == 1.0


def test_a_short_gesture_does_not_fire():
    gate = CommandGate()
    assert run(gate, .4) == []
    assert gate.step(1.0, None) is None and gate.progress == 0.0


def test_a_flicker_does_not_restart_the_hold_but_a_real_break_does():
    gate = CommandGate()
    assert run(gate, .3) == []
    gate.step(.31, None)
    assert run(gate, .3, start=.35)            # came back within the break: still the same hold
    fresh = CommandGate()
    assert run(fresh, .3) == []
    fresh.step(.31, None)
    fresh.step(.6, None)
    assert run(fresh, .3, start=.65) == []


def test_it_fires_again_only_after_release_and_the_cooldown():
    gate = CommandGate()
    assert len(run(gate, 1.0)) == 1
    for i in range(10):
        gate.step(1.0 + i / 30, None)
    assert len(run(gate, 1.0, start=1.4)) == 1
    release = CommandGate(hold=.1)
    assert len(run(release, .3)) == 1
    for i in range(6):
        release.step(.3 + i / 30, None)
    assert run(release, .3, start=.5) == []     # released, but within the cooldown


def test_nothing_fires_while_paused_and_holding_through_a_pause_does_not_count():
    gate = CommandGate()
    assert run(gate, 2.0, allowed=False) == []
    assert run(gate, .4, start=2.0) == []
    assert len(run(gate, .2, start=2.4)) == 1


def test_another_gesture_starts_its_own_hold():
    gate = CommandGate()
    assert len(run(gate, 1.0, "a")) == 1
    assert run(gate, .4, "b", start=2.0) == []
    assert len(run(gate, .2, "b", start=2.4)) == 1


def test_hotkeys_are_checked_and_dangerous_ones_refused():
    assert check_hotkey("Ctrl+X") == "ctrl+x"
    for bad in ("", "alt+f4", "ctrl+w", "win+l", "delete", "shift+delete", "ctrl+alt+delete"):
        with pytest.raises(CommandError):
            check_hotkey(bad)


def test_a_command_is_saved_found_and_forgotten(registry, tmp_path):
    assert command_of("잘라내기") == ""
    assert "Ctrl" in set_command("잘라내기", "ctrl+x")
    assert command_of("잘라내기") == "ctrl+x"
    assert ActionBindingRegistry(tmp_path / "bindings.json").bindings["user:잘라내기"]["action_value"] == "ctrl+x"
    assert set_command("잘라내기", "") == "표시만" and command_of("잘라내기") == ""
    forget_command("잘라내기")
    assert "user:잘라내기" not in registry.bindings
    with pytest.raises(CommandError):
        set_command("x", "alt+f4")
    assert command_of("x") == ""


def test_the_words_on_the_screen():
    assert command_text("") == "표시만"
    assert command_text("ctrl+x").startswith("잘라내기")
    assert "Ctrl" in command_text("ctrl+b")
    assert {c["value"] for c in choices()} >= {"ctrl+x", "ctrl+c", "ctrl+v"}


# ---------------------------------------------------------------- inside the runtime
from src.online.pose_memory import pose_features  # noqa: E402
from src.online.registration import USER_SOURCE  # noqa: E402
from tests.test_online_runtime import dashboard, ready_runtime  # noqa: E402,F401


def with_gesture(tmp_path):
    rt = ready_runtime(tmp_path)
    hand = rt.tracker.detect(np.zeros((720, 1280, 3), np.uint8))[0]
    pose = pose_features([{"t": 0.0, "lm": [(p.x, p.y, p.z) for p in hand.landmarks], "world": hand.world}], .5625)
    assert rt._online_load()
    rt.online_memory.add_class("집기", np.repeat(pose, 6, axis=0), demo_ids=np.arange(6) % 3, source=USER_SOURCE)
    return rt


def steps_for(rt, seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        rt._step()
        time.sleep(.01)


def hotkeys(rt):
    return [e for e in rt.mouse.event_log if str(e).startswith("hotkey:")]


def test_the_command_runs_only_when_control_is_on(tmp_path, registry):
    rt = with_gesture(tmp_path)
    set_command("집기", "ctrl+x")
    rt.paused = True
    rt._step()          # the first step loads torch (seconds when this file runs alone): not part of the timing below
    steps_for(rt, .8)
    assert rt._snap.online_gesture == "집기" and hotkeys(rt) == []
    rt.paused = False
    steps_for(rt, 1.0)
    assert hotkeys(rt) == ["hotkey:ctrl+x"]
    assert "잘라내기" in rt._snap.online_gesture
    steps_for(rt, .8)
    assert hotkeys(rt) == ["hotkey:ctrl+x"]        # one hold, one command
    rt.online.close()


def test_a_command_is_not_sent_into_the_app_itself(tmp_path, registry, monkeypatch):
    monkeypatch.setattr("src.qt_runtime.foreground_window_title", lambda: "Hand-Mouse · MANUX Qt")
    rt = with_gesture(tmp_path)
    set_command("집기", "q")
    rt.paused = False
    steps_for(rt, 1.0)
    assert hotkeys(rt) == [] and "보내지 않아요" in rt._snap.online_gesture
    rt.online.close()


def press(window, key, modifier=None):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    QTest.keyClick(window, key, modifier or Qt.KeyboardModifier.NoModifier)
    QTest.qWait(50)


def test_pressing_keys_saves_a_command_and_a_letter_does_not_reach_the_window_shortcuts(dashboard, registry):  # noqa: F811
    from PySide6.QtCore import QMetaObject, QObject, Q_ARG, Qt
    from PySide6.QtTest import QTest
    window = dashboard.engine.rootObjects()[0]
    popup = window.findChild(QObject, "commandPopup")
    window.requestActivate()
    QTest.qWait(100)
    QMetaObject.invokeMethod(popup, "openFor", Q_ARG("QVariant", "집기"))
    QTest.qWait(100)
    popup.setProperty("listening", True)
    before = len(dashboard.commands)
    press(window, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
    assert command_of("집기") == "ctrl+b" and not popup.property("listening")
    QMetaObject.invokeMethod(popup, "openFor", Q_ARG("QVariant", "집기"))
    QTest.qWait(100)
    popup.setProperty("listening", True)
    press(window, Qt.Key.Key_Q)
    assert command_of("집기") == "q"
    assert len(dashboard.commands) == before       # Q did not become the window's quit command
    QMetaObject.invokeMethod(popup, "openFor", Q_ARG("QVariant", "집기"))
    QTest.qWait(100)
    popup.setProperty("listening", True)
    press(window, Qt.Key.Key_F4, Qt.KeyboardModifier.AltModifier)
    assert command_of("집기") == "q" and "위험" in dashboard.commandMessage    # refused, the old command stays
    popup.setProperty("visible", False)


def palm_mode(rt):
    import dataclasses
    rt.ui = dataclasses.replace(rt.ui, use_index_pointer=False)
    rt.paused = False


def moving(rt, seconds, step=.01):
    start = list(rt.mouse.last_screen_pos)
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        rt.tracker.dx += step
        rt._step()
        time.sleep(.01)
    return abs(rt.mouse.last_screen_pos[0] - start[0]) + abs(rt.mouse.last_screen_pos[1] - start[1])


def test_the_cursor_stays_still_while_a_registered_gesture_is_made_and_moves_again_after(tmp_path, registry):
    plain = ready_runtime(tmp_path / "plain")
    palm_mode(plain)
    moving(plain, .2)                              # let the engine start
    assert moving(plain, .4) > 0                   # no gesture registered: the hand moves the cursor
    plain.online.close()

    rt = with_gesture(tmp_path / "held")
    palm_mode(rt)
    moving(rt, .15)                                # the first frames, before it counts as a gesture
    assert rt.recognizer.candidate == "집기"
    assert moving(rt, .4) == 0
    rt.online_memory.remove_class("집기")           # no longer that gesture
    moving(rt, .35)                                # the short hold after it fades
    assert not rt.recognizer.candidate
    assert moving(rt, .4) > 0
    rt.online.close()


def test_no_click_is_judged_while_a_gesture_is_made(tmp_path, registry, monkeypatch):
    rt = with_gesture(tmp_path)
    palm_mode(rt)
    applied = []
    real = rt.mouse.apply
    monkeypatch.setattr(rt.mouse, "apply", lambda result, *a, **k: (applied.append(result.action), real(result, *a, **k))[1])
    real_process = rt.engine.process

    def clicking(landmarks):
        out = real_process(landmarks)
        out.action = "left_click"
        return out

    monkeypatch.setattr(rt.engine, "process", clicking)
    steps_for(rt, .3)
    assert applied and applied[-1] == "none" and "left_click" in applied[:3]
    rt.online.close()


def test_a_gesture_without_a_command_only_shows(tmp_path, registry):
    rt = with_gesture(tmp_path)
    rt.paused = False
    steps_for(rt, .9)
    assert rt._snap.online_gesture == "집기" and hotkeys(rt) == []
    rt.online.close()


def test_deleting_a_gesture_removes_it_its_demonstrations_and_its_command(tmp_path, registry):
    from src.online.pose_memory import PoseMemory
    from src.online.samples import USER_CONFIRMED, SampleStore
    rt = with_gesture(tmp_path)
    rt.online_memory.save(tmp_path / "online" / "pose_memory.json")
    store = SampleStore(tmp_path / "online")
    store.add_frames([{"t": 0.0, "lm": None, "world": None}], label="집기", label_source=USER_CONFIRMED, session="s")
    set_command("집기", "ctrl+x")
    rt.post("gesture_delete", "집기")
    rt._step()
    assert "집기" not in rt.online_memory.classes
    assert "집기" not in PoseMemory.load(tmp_path / "online" / "pose_memory.json").classes
    assert SampleStore(tmp_path / "online").samples(label="집기") == [] and command_of("집기") == ""
    rt.post("gesture_delete", "none")          # a known class is not the person's to delete
    rt._step()
    assert "none" in rt.online_memory.classes


def test_the_dashboard_lists_gestures_and_saves_commands(dashboard, tmp_path, registry):  # noqa: F811
    rt = with_gesture(tmp_path)
    rt.online_memory.save(tmp_path / "online" / "pose_memory.json")
    dashboard.online_root = tmp_path / "online"
    dashboard.refreshMyGestures()
    assert dashboard.myGestures == [{"label": "집기", "value": "", "text": "표시만"}]
    assert dashboard.setGestureCommand("집기", "ctrl+c")
    assert dashboard.myGestures[0]["value"] == "ctrl+c" and dashboard.myGestures[0]["text"].startswith("복사")
    assert not dashboard.setGestureCommand("집기", "alt+f4")
    assert "위험" in dashboard.commandMessage and dashboard.myGestures[0]["value"] == "ctrl+c"
    assert len(dashboard.commandChoices) >= 8
    dashboard.deleteMyGesture("집기")
    assert dashboard.myGestures == [] and dashboard.online_commands[-1] == ("gesture_delete", "집기")
    dashboard.online_commands.clear()
    rt.online.close() if rt.online else None


def test_win_combinations_are_offered_as_buttons_and_kept():
    """Windows acts on Win+D before the app sees the keys, so it cannot be pressed in the popup: it is a button, and Win+L stays out."""
    values = [c["value"] for c in choices()]
    assert "win+d" in values and "win+shift+s" in values
    assert check_hotkey("win+d") == "win+d" and check_hotkey("Windows+D") == "win+d"
    try:
        check_hotkey("win+l")
        raise AssertionError("win+l must be refused")
    except CommandError:
        pass
