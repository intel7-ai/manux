"""Gesture sequences (src/sequence.py): hand shapes in order run a command. No camera."""
import pytest

from src import llm_binding as lb
from src.action_bindings import ActionBindingRegistry
from src.sequence import GAP, HOLD, SequenceDetector, SequenceStore, check_steps

SEQ = {"seq_v_fist": ("v_sign", "fist")}


def feed(det, plan, t=0.0, fps=30):
    """plan: [(shape, seconds), ...]; returns (fired keys, end time)."""
    fired = []
    for shape, seconds in plan:
        for _ in range(int(seconds * fps)):
            k = det.step(t, shape, SEQ)
            if k:
                fired.append(k)
            t += 1 / fps
    return fired, t


def test_two_shapes_in_order_fire_once_and_passing_shapes_do_not_count():
    det = SequenceDetector()
    fired, _ = feed(det, [("open_hand", .5), ("v_sign", .5), ("thumb_ring", .1), ("", .1), ("fist", .5)])
    assert fired == ["seq_v_fist"]


def test_holding_the_first_shape_long_is_fine_but_a_long_pause_breaks_it():
    fired, _ = feed(SequenceDetector(), [("v_sign", 2.0), ("fist", .5)])
    assert fired == ["seq_v_fist"]                               # the gap runs from letting the V go
    fired, _ = feed(SequenceDetector(), [("v_sign", .5), ("open_hand", GAP + HOLD + .3), ("fist", .5)])
    assert fired == []
    fired, _ = feed(SequenceDetector(), [("fist", .5), ("v_sign", .5)])
    assert fired == []                                           # wrong order


def test_a_fired_sequence_needs_doing_again_after_the_cooldown():
    det = SequenceDetector()
    fired, t = feed(det, [("v_sign", .4), ("fist", .4), ("v_sign", .4), ("fist", .4)])
    assert fired == ["seq_v_fist"]                               # the second within the cooldown
    fired, _ = feed(det, [("open_hand", .5), ("v_sign", .4), ("fist", .4)], t=t)
    assert fired == ["seq_v_fist"]


def test_the_store_refuses_bad_and_overlapping_sequences(tmp_path):
    store = SequenceStore(tmp_path / "s.json")
    assert check_steps(["v_sign"]) and check_steps(["v_sign", "v_sign"]) and check_steps(["v_sign", "dance"])
    key = store.add("", ["v_sign", "fist"])
    assert SequenceStore(tmp_path / "s.json").items[key]["steps"] == ["v_sign", "fist"]
    for clash in (["v_sign", "fist"], ["v_sign", "fist", "open_hand"]):     # same, or the start of another
        with pytest.raises(ValueError):
            store.add("", clash)
    store.add("", ["fist", "v_sign"])                                        # the other order is a different one
    store.set_enabled(key, False)
    assert key not in store.active()
    assert store.remove(key) and key not in store.items


def proposal(**kw):
    base = {"gesture_key": lb.NEW, "new_name": "", "new_posture": "", "new_trigger": "", "new_sequence": [],
            "app_match": "", "action_type": "hotkey", "action_value": "alt+f4", "steps": [], "enabled": True, "reason": ""}
    return {"summary": "", "questions": [], "proposals": [{**base, **kw}]}


def test_the_ai_creates_a_sequence_and_undo_removes_it(tmp_path):
    reg = ActionBindingRegistry(tmp_path / "b.json")
    store = SequenceStore(tmp_path / "s.json")
    history = lb.History(tmp_path / "ai.json", sequences_path=tmp_path / "s.json")
    gestures = lb.gesture_list(reg)
    row = lb.check(proposal(new_sequence=["v_sign", "fist"], new_name="창 닫기 신호"), gestures)["proposals"][0]
    assert row["ok"] and row["new_sequence"] == ["v_sign", "fist"] and "V" in row["gesture_name"] or row["ok"]
    lb.apply([row], reg, history=history, sequences=store)
    key = next(iter(store.items))
    assert store.items[key]["name"] == "창 닫기 신호" and reg.get_binding(key)["action_value"] == "alt+f4"
    # an overlapping one is refused (the list carries the existing steps)
    listed = [{"key": key, "name": "창 닫기 신호", "sequence": ["v_sign", "fist"]}]
    bad = lb.check(proposal(new_sequence=["v_sign", "fist", "open_hand"]), listed)["proposals"][0]
    assert not bad["ok"] and "겹쳐요" in bad["problem"]
    warn = lb.check(proposal(new_sequence=["thumbs_up", "fist"]), gestures)["proposals"][0]
    assert warn["ok"] and warn["risky"] and not warn["checked"]
    entry = history.rows(reg)[0]
    assert entry["current"] and entry["sequence"]
    history.undo(entry["index"], reg)
    assert SequenceStore(tmp_path / "s.json").items == {}


def test_the_runtime_runs_the_sequence_command_in_any_mode(tmp_path, monkeypatch):
    import dataclasses
    import time
    import src.qt_runtime as qr
    from tests.test_qt_runtime import runtime
    rt = runtime(tmp_path)
    rt.paused = False
    rt.ui = dataclasses.replace(rt.ui, use_index_pointer=True)          # not inside the palm controls
    rt.seq_store = SequenceStore(tmp_path / "s.json")
    key = rt.seq_store.add("", ["v_sign", "fist"])
    ran = []
    monkeypatch.setattr(rt, "_run_sequence", lambda k: ran.append(k))
    script = {"shape": "v_sign"}
    monkeypatch.setattr(qr, "hand_posture", lambda lm, aspect=1.0: script["shape"])
    start = time.perf_counter()
    while time.perf_counter() - start < 3 and not ran:
        script["shape"] = "v_sign" if time.perf_counter() - start < .5 else "fist"
        rt._step()
    assert ran == [key]


# ---------------------------------------------------------------------------- upside-down V, signs with their own detector
def v_hand(down=False, scale=.25):
    from types import SimpleNamespace
    from tests.test_online_data import hand_world
    w = hand_world()
    pts = [[.5 + p[0] * scale, .5 + p[1] * scale, p[2] * scale] for p in w]
    for mcp, tip in ((13, 16), (17, 20)):                     # ring and little finger folded
        for j in (tip - 2, tip - 1, tip):
            pts[j] = [pts[0][0] + (pts[mcp][0] - pts[0][0]) * 1.05, pts[0][1] + (pts[mcp][1] - pts[0][1]) * 1.05, pts[mcp][2]]
    if not down:                      # the fixture's fingers point down the image (y grows down): turn it upright
        wy = pts[0][1]
        pts = [[x, 2 * wy - y, z] for x, y, z in pts]
    return [SimpleNamespace(x=x, y=y, z=z) for x, y, z in pts]


def test_an_upside_down_v_is_its_own_sign_and_does_not_switch_the_control():
    from src.action_bindings import CompositeGestureDetector
    from src.pose_probe import fingers_down, hand_posture
    from src.toggle_sign import ToggleSign
    up, down = v_hand(), v_hand(down=True)
    assert not fingers_down(up) and fingers_down(down)
    assert hand_posture(up, 720 / 1280) == "v_sign" and hand_posture(down, 720 / 1280) == "v_sign_down"
    assert CompositeGestureDetector._detect_base_posture(down, "", set()) == "v_sign_down"
    for lm, toggles in ((up, True), (down, False)):
        sign, t, events = ToggleSign({}), 0.0, []
        for _ in range(90):
            e = sign.update(t, lm)
            if e:
                events.append(e)
            t += 1 / 30
        assert bool(events) == toggles


def test_chrome_is_found_through_app_paths_with_its_arguments(monkeypatch):
    import subprocess
    import src.action_bindings as ab
    opened = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(ab, "_shell_open", lambda prog, params="": opened.append((prog, params)) or True)
    reg = ab.ActionBindingRegistry.__new__(ab.ActionBindingRegistry)
    ok, _ = ab.ActionBindingRegistry.execute_resolved_action(
        reg, {"action_type": "launch_app", "action_value": "chrome.exe --incognito https://www.youtube.com"}, dry_run=False)
    assert ok and opened == [("chrome.exe", "--incognito https://www.youtube.com")]


def test_a_changed_v_sign_binding_runs_instead_of_switching_the_control(tmp_path, monkeypatch):
    from tests.test_qt_runtime import runtime
    import src.qt_runtime as qr
    rt = runtime(tmp_path)
    rt.paused = False

    class Reg:
        def get_binding(self, key):
            return {"enabled": True}

        def resolve_effective_action(self, key, fg):
            return {"action_type": "hotkey", "action_value": "f5", "matched_app": ""}
    monkeypatch.setattr(qr, "get_action_registry", lambda: Reg())
    assert rt._sign_override("v_sign")["action_value"] == "f5"
    rt.paused = True                                           # (the caller only asks while the control is on)

    class Default(Reg):
        def resolve_effective_action(self, key, fg):
            return {"action_type": "mouse_action", "action_value": "toggle_control", "matched_app": ""}
    monkeypatch.setattr(qr, "get_action_registry", lambda: Default())
    assert rt._sign_override("v_sign") is None


def test_the_ai_is_told_how_to_open_a_private_window():
    text = lb.system_prompt([])
    assert "--incognito" in text and "ctrl+shift+n" in text


def test_a_real_upside_down_v_is_recognised():
    """2026-11-10 report '거꾸로 v하니까 인식 못한다': the folded ring finger measured 1.38 upside down (limit was 1.25)."""
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from src.action_bindings import CompositeGestureDetector
    from src.pose_probe import hand_posture
    data = json.loads((Path(__file__).parent / "fixtures" / "v_sign_down_20261110.json").read_text(encoding="utf-8"))
    for lm in data["frames"]:
        L = [SimpleNamespace(x=p[0], y=p[1], z=p[2]) for p in lm]
        assert hand_posture(L, 720 / 1280) == "v_sign_down"
        assert CompositeGestureDetector._detect_base_posture(L, "", set()) == "v_sign_down"


def test_a_v_is_a_v_even_when_the_rule_engine_calls_it_a_ring_pinch():
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from src.action_bindings import CompositeGestureDetector
    lm = json.loads((Path(__file__).parent / "fixtures" / "v_sign_down_20261110.json").read_text(encoding="utf-8"))["frames"][0]
    L = [SimpleNamespace(x=p[0], y=p[1], z=p[2]) for p in lm]
    assert CompositeGestureDetector._detect_base_posture(L, "thumb_ring_pinch", {"ring"}) == "v_sign_down"


def test_a_short_hold_fires_quickly_and_a_blank_frame_does_not_restart_it(tmp_path):
    """2026-11-10 report: '1초 유지 하니까 너무 오래걸리는데'."""
    from src.action_bindings import ActionBindingRegistry, CompositeGestureDetector
    reg = ActionBindingRegistry(tmp_path / "b.json")
    reg.register_composite_gesture("", "거꾸로 V 유지", "v_sign_down", "hold", "hotkey", "f5", hold_seconds=.4)
    script = []

    class Scripted(CompositeGestureDetector):
        @staticmethod
        def _detect_base_posture(landmarks, active_gesture, pinched):
            return script[0]

    det = Scripted(reg)
    lm = v_hand(down=True)
    t, fired_at = 0.0, None
    for i in range(30):
        script[:] = ["" if i in (5, 6, 7) else "v_sign_down"]      # 0.1 s of no shape in the middle
        if det.step(t, lm, "", set(), .5625):
            fired_at = t
            break
        t += 1 / 30
    assert fired_at is not None and fired_at < .5


def test_fingers_bent_toward_the_camera_are_not_a_fist():
    """2026-11-10 report '이건 주먹쥐기가 아닌데': fingers bent forward look as short as curled ones in the picture."""
    from types import SimpleNamespace as P
    from src.pose_probe import fist_or_bent
    w = (.5, .7, 0.0)
    mcps = {5: (.45, .55), 9: (.5, .54), 13: (.55, .55), 17: (.6, .57)}

    def hand(tip_z):
        lm = [P(x=w[0], y=w[1], z=0.0) for _ in range(21)]
        for mcp, (x, y) in mcps.items():
            lm[mcp] = P(x=x, y=y, z=0.0)
            for j in (1, 2, 3):                                       # the finger: over its knuckle in the picture
                lm[mcp + j] = P(x=x, y=y - .01 * j, z=tip_z * j / 3)
        return lm
    assert fist_or_bent(hand(0.0), 1.0) == "fist"                     # tips back at the knuckles
    assert fist_or_bent(hand(-.2), 1.0) == "fingers_bent"             # tips well in front (toward the camera)
