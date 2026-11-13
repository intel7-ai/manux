# Original a700452 synthetic unit tests; host runtime tests live in test_ai_integration.py.
"""Gestures connected in words (src/llm_binding.py, src/llm_providers.py). No network: providers are mocked."""
import io
import json
import sys
from types import SimpleNamespace

import pytest

from manux.compat.hand_mouse.action_bindings import ActionBindingRegistry
from manux.compat.hand_mouse import llm_binding as lb
from manux.compat.hand_mouse import llm_providers as lp


@pytest.fixture
def registry(tmp_path):
    return ActionBindingRegistry(tmp_path / "bindings.json")


def gestures(registry):
    return lb.gesture_list(registry)


def test_the_gesture_list_has_real_gestures_but_not_the_background_ones(registry):
    keys = {g["key"] for g in gestures(registry)}
    assert {"thumb_ring", "thumb_middle", "thumbs_up"} <= keys
    assert "none" not in keys and not any(k.startswith("near_thumb") for k in keys)


def test_only_catalogue_values_pass_and_program_launches_start_unticked(registry):
    answer = {"summary": "s", "questions": ["어느 앱?"], "proposals": [
        {"gesture_key": "thumb_ring", "app_match": "YouTube", "action_type": "youtube_key", "action_value": "yt_captions",
         "enabled": True, "reason": "자막"},
        {"gesture_key": "thumb_middle", "app_match": "", "action_type": "hotkey", "action_value": "ctrl+t",
         "enabled": True, "reason": ""},
        {"gesture_key": "made_up", "app_match": "", "action_type": "hotkey", "action_value": "ctrl+t", "enabled": True, "reason": ""},
        {"gesture_key": "thumb_middle", "app_match": "", "action_type": "mouse_action", "action_value": "explode",
         "enabled": True, "reason": ""},
        {"gesture_key": "thumb_middle", "app_match": "", "action_type": "hotkey", "action_value": "ctrl+notakey",
         "enabled": True, "reason": ""},
        {"gesture_key": "thumb_middle", "app_match": "", "action_type": "launch_app", "action_value": "calc.exe",
         "enabled": True, "reason": ""},
    ]}
    rows = lb.check(answer, gestures(registry))["proposals"]
    assert [r["ok"] for r in rows] == [True, True, False, False, False, True]
    assert rows[0]["checked"] and rows[1]["checked"]
    assert rows[5]["risky"] and not rows[5]["checked"]          # runs a command: the user must tick it
    assert rows[0]["before"] == "(없음)" and "자막" in rows[0]["after"]


def test_applying_saves_only_ticked_valid_rows_and_app_rules_keep_the_default(registry):
    before = registry.get_binding("thumb_ring")
    answer = {"summary": "", "questions": [], "proposals": [
        {"gesture_key": "thumb_ring", "app_match": "YouTube", "action_type": "youtube_key", "action_value": "yt_captions",
         "enabled": True, "reason": ""},
        {"gesture_key": "thumb_middle", "app_match": "", "action_type": "launch_app", "action_value": "calc.exe",
         "enabled": True, "reason": ""},
        {"gesture_key": "thumbs_up", "app_match": "", "action_type": "", "action_value": "", "enabled": False, "reason": ""},
    ]}
    rows = lb.check(answer, gestures(registry))["proposals"]
    done = lb.apply(rows, registry)
    assert len(done) == 2
    ring = registry.get_binding("thumb_ring")
    assert (ring["action_type"], ring["action_value"]) == (before["action_type"], before["action_value"])
    assert ring["app_overrides"][0]["app_match"] == "YouTube"
    assert registry.get_binding("thumb_middle")["action_type"] != "launch_app"     # not ticked: not saved
    assert registry.get_binding("thumbs_up")["enabled"] is False


def test_the_schema_only_allows_listed_gesture_keys():
    s = lb.schema(["a", "b"])
    item = s["properties"]["proposals"]["items"]
    assert item["properties"]["gesture_key"]["enum"] == ["a", "b", lb.NEW]      # + "create a new gesture"
    assert item["additionalProperties"] is False and set(item["required"]) == set(item["properties"])


def test_answers_in_fences_or_with_text_around_are_read():
    assert lp.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert lp.parse_json('네, 여기요: {"a": 2} 끝') == {"a": 2}
    with pytest.raises(lp.LlmError):
        lp.parse_json("모르겠어요")


def test_openai_compatible_providers_send_the_key_and_retry_without_json_mode(monkeypatch):
    sent = []

    def fake_urlopen(request, timeout=0):
        body = json.loads(request.data.decode())
        sent.append((request.full_url, request.headers.get("Authorization"), body))
        if "response_format" in body:
            import urllib.error
            raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, io.BytesIO(b'{"error": {"message": "no json"}}'))
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": '{"summary": "ok", "proposals": [], "questions": []}'}}]}
        return SimpleNamespace(read=lambda: json.dumps(payload).encode(), __enter__=lambda s: s, __exit__=lambda *a: None)

    class Resp:
        def __init__(self, r): self.r = r
        def __enter__(self): return self.r
        def __exit__(self, *a): return False

    monkeypatch.setattr(lp.urllib.request, "urlopen", lambda req, timeout=0: Resp(fake_urlopen(req)))
    cfg = lp.LlmConfig(provider="openrouter", model="some/model", api_key="k-123")
    out = lp.complete_json(cfg, "sys", "hi", {"type": "object"})
    assert out["summary"] == "ok"
    assert sent[0][0] == "https://openrouter.ai/api/v1/chat/completions" and sent[0][1] == "Bearer k-123"
    assert "response_format" in sent[0][2] and "response_format" not in sent[1][2]


def test_ollama_needs_no_key_and_a_missing_key_is_told_plainly(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(lp.LlmError, match="키가 없어요"):
        lp.complete_json(lp.LlmConfig(provider="openai", model="m"), "s", "u", {})
    with pytest.raises(lp.LlmError, match="모델 이름"):
        lp.complete_json(lp.LlmConfig(provider="ollama"), "s", "u", {})


def test_claude_uses_structured_outputs_and_reports_a_refusal(monkeypatch):
    calls = {}

    class FakeMessages:
        def create(self, **kw):
            calls.update(kw)
            return SimpleNamespace(stop_reason=calls.get("_stop", "end_turn"),
                                   content=[SimpleNamespace(type="text", text='{"summary": "", "proposals": [], "questions": []}')])

    class FakeClient:
        def __init__(self, **kw):
            self.messages = FakeMessages()
            self.beta = SimpleNamespace(messages=FakeMessages())

    fake = SimpleNamespace(Anthropic=FakeClient, APIError=Exception)
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    cfg = lp.LlmConfig(provider="claude", api_key="k")
    assert lp.complete_json(cfg, "s", "u", {"type": "object"})["proposals"] == []
    assert calls["model"] == "claude-opus-5-5"
    assert calls["output_config"]["format"]["type"] == "json_schema"
    assert calls["fallbacks"] == "default"

    def refuse(self, **kw):
        return SimpleNamespace(stop_reason="refusal", content=[])
    monkeypatch.setattr(FakeMessages, "create", refuse)
    with pytest.raises(lp.LlmError, match="답하지 않았어요"):
        lp.complete_json(cfg, "s", "u", {})


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_a_saved_key_round_trips_for_this_user_only():
    from manux.compat.hand_mouse import secret_store
    token = secret_store.protect("sk-test-123")
    assert token and "sk-test" not in token
    assert secret_store.unprotect(token) == "sk-test-123"
    assert secret_store.unprotect("not-base64!!") == ""


# ---------------------------------------------------------------------------- "이 손모양이면 저렇게 해줘"
from manux.compat.hand_mouse.key_capture import ComboTracker, capture
from manux.compat.hand_mouse.pose_probe import PoseProbe


def run_probe(postures_after_countdown):
    probe, t = PoseProbe(), 0.0
    probe.start(t)
    while t < probe.COUNTDOWN:
        probe.step(t, "open_hand")          # getting ready: not counted
        t += 1 / 30
    for p in postures_after_countdown:
        probe.step(t, p)
        t += 1 / 30
    probe.finish_if_due(t + probe.HOLD)
    return probe.view(t + probe.HOLD)


def test_the_shown_shape_is_the_most_frequent_posture_after_the_countdown():
    view = run_probe(["thumb_ring"] * 50 + ["open_hand"] * 10)
    assert view["state"] == "done" and view["gesture_key"] == "thumb_ring" and view["share"] > .8
    assert run_probe(["fist"] * 40)["everyday"] is True and run_probe(["fist"] * 40)["gesture_key"] == ""
    assert run_probe(["thumb_ring"] * 3)["state"] == "failed"                      # no hand
    # no clear shape: "not in the list", never the nearest one (2026-11-11 rule; it was "approx" for a day)
    mixed = run_probe(["v_sign", "fist", "thumb_ring", ""] * 15)
    assert mixed["state"] == "done" and mixed["unknown"] and mixed["posture"] == "" and not mixed["approx"]
    none = run_probe([""] * 60)
    assert none["unknown"] and none["name"] == "목록에 없는 손 모양" and none["gesture_key"] == ""
    # the AI is told there is no such shape and gets no stand-in gesture for it
    assert "목록에 없는" in lb.context_block(none, "", []) and lb.with_shown([], none) == [] and lb.shown_key(none, []) == ""


def test_a_key_combination_is_taken_once_all_keys_are_let_go_and_held_keys_at_start_are_ignored():
    t = ComboTracker(frozenset({0x01}))                      # (mouse button still down from the click: not a key here)
    assert t.feed(frozenset({0x11})) is None                  # ctrl
    assert t.feed(frozenset({0x11, 0x10})) is None            # + shift
    assert t.feed(frozenset({0x11, 0x10, 0x54})) is None      # + t
    assert t.feed(frozenset({0x11})) is None
    assert t.feed(frozenset()) == "ctrl+shift+t"
    t = ComboTracker(frozenset({0x11}))                       # ctrl already held when it started
    assert t.feed(frozenset({0x11, 0x41})) is None
    assert t.feed(frozenset()) == "a"
    t = ComboTracker()
    assert t.feed(frozenset({0x5B})) is None and t.feed(frozenset()) is None   # Win alone is not a combination
    frames = iter([frozenset(), frozenset({0x5B, 0x44}), frozenset()])
    clock = iter(range(100))
    assert capture(5, held=lambda: next(frames, frozenset()), clock=lambda: next(clock), sleep=lambda s: None) == "win+d"


def test_the_shown_shape_and_key_reach_the_ai_and_a_new_shape_is_registered_on_apply(registry):
    gs = gestures(registry)
    shown = {"state": "done", "posture": "fist", "name": "주먹 쥐기", "share": .9, "gesture_key": "", "everyday": True}
    block = lb.context_block(shown, "ctrl+shift+t", gs)
    assert lb.SHOWN in block and "ctrl+shift+t" in block
    known = {"state": "done", "posture": "thumb_ring", "name": "엄지+약지", "share": .9, "gesture_key": "thumb_ring"}
    assert '"thumb_ring"' in lb.context_block(known, "", gs) and lb.SHOWN not in lb.context_block(known, "", gs)
    with_new = lb.with_shown(gs, shown)
    answer = {"summary": "", "questions": [], "proposals": [
        {"gesture_key": lb.SHOWN, "app_match": "", "action_type": "hotkey", "action_value": "ctrl+shift+t", "enabled": True,
         "reason": ""}]}
    rows = lb.check(answer, with_new)["proposals"]
    assert rows[0]["ok"] and rows[0]["risky"] and not rows[0]["checked"] and "평소" in rows[0]["warning"]
    rows[0]["checked"] = True
    done = lb.apply(rows, registry)
    assert done and any(c["base_posture"] == "fist" and c["trigger_type"] == "hold" for c in registry.composite_gestures.values())


def test_a_folder_or_a_document_is_opened_not_started_as_a_program(tmp_path, monkeypatch):
    """2026-11-11 report: "바탕화면에 user 폴더를 열어달라는 신호명령 등록해봤는데 안된다" (the value was the Desktop folder)."""
    import os
    import subprocess
    import manux.compat.hand_mouse.action_bindings as ab
    folder = tmp_path / "my folder"
    folder.mkdir()
    doc = folder / "notes.txt"
    doc.write_text("x", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert ab.local_target(str(folder)) == str(folder) and ab.local_target(f'"{doc}"') == str(doc)
    assert ab.local_target("%USERPROFILE%\\my folder" if os.name == "nt" else "$USERPROFILE/my folder") == str(folder)
    assert ab.local_target("calc.exe") == "" and ab.local_target("chrome.exe --incognito https://www.youtube.com") == ""
    assert ab.local_target(str(tmp_path / "missing")) == ""
    if os.name != "nt":
        return
    opened, started = [], []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    reg = ab.ActionBindingRegistry(tmp_path / "b.json")
    value = r"%USERPROFILE%\my folder"
    ok, badge = reg.execute_resolved_action({"action_type": "launch_app", "action_value": value})
    assert ok and opened == [str(folder)] and not started and badge == "폴더 열기: my folder"


def test_hand_posture_from_landmarks_alone():
    from types import SimpleNamespace as P
    from manux.compat.hand_mouse.pose_probe import hand_posture
    from tests.unit.ai_upstream.hands import open_hand, pinched_hand
    assert hand_posture(pinched_hand(.5, .5), 720 / 1280) == "thumb_index"
    lm = pinched_hand(.5, .5)
    w = lm[0]
    for mcp, tip in ((5, 8), (9, 12), (13, 16), (17, 20)):   # fold every finger back onto its knuckle
        lm[tip] = P(x=w.x + (lm[mcp].x - w.x) * .9, y=w.y + (lm[mcp].y - w.y) * .9, z=0)
        lm[tip - 1] = lm[tip - 2] = lm[tip]
    lm[4] = P(x=lm[5].x, y=lm[5].y + .01, z=0)
    assert hand_posture(lm, 720 / 1280) in ("fist", "thumbs_up")
    assert hand_posture(open_hand(.5, .5), 720 / 1280) != "thumb_index"


def test_the_probe_sees_the_hand_in_any_mode_not_only_inside_the_palm_controls(tmp_path):
    # Same source assertion, using the current camera-to-session adapter. No Windows output.
    from manux.compat.hand_mouse.session import CustomizationSession
    from manux.core.contracts import FrameInfo, HandObservation
    from tests.unit.ai_upstream.hands import pinched_hand
    session = CustomizationSession(lambda r: None, lambda image, lm: None)
    session.probe.COUNTDOWN, session.probe.HOLD, session.probe.MIN_FRAMES = 0., .15, 3
    session.probe.start(1.)
    lm = tuple((p.x, p.y, p.z) for p in pinched_hand(.5,.5))
    for n in range(8):
        frame = FrameInfo(n, 1.+n*.03, 1280, 720, True)
        session.observe(frame.t, HandObservation(frame, lm, 'Right', 1.))
    view, shot = session.snapshot()
    assert view and view['state'] == 'done', view
    assert view['gesture_key'] == 'thumb_index'



def test_a_bare_web_address_opens_in_the_browser_and_programs_stay_programs(registry):
    """2026-11-10: "youtube.com" as a program launch was started as a program and failed."""
    from manux.compat.hand_mouse.action_bindings import web_address
    assert web_address("youtube.com") == "https://youtube.com"
    assert web_address("www.naver.com/news") == "https://www.naver.com/news"
    for program in ("calc.exe", "notepad", r"C:\Windows\notepad.exe", "https://a.example", "run.bat"):
        assert web_address(program) == program
    answer = {"summary": "", "questions": [], "proposals": [
        {"gesture_key": "thumb_index", "app_match": "", "action_type": "launch_app", "action_value": "youtube.com",
         "enabled": True, "reason": ""}]}
    row = lb.check(answer, gestures(registry))["proposals"][0]
    assert row["action_value"] == "https://youtube.com" and row["risky"] and not row["checked"]


# ---------------------------------------------------------------------------- macros, new gestures, drawing shapes, history
def test_macro_steps_are_checked_described_and_cancellable():
    import time
    from manux.compat.hand_mouse import macro
    steps = macro.parse([{"do": "open", "value": "youtube.com"}, {"do": "wait", "value": "2"}, {"do": "key", "value": "F"}])
    assert steps[0]["value"] == "https://youtube.com" and steps[2]["value"] == "f"
    assert "youtube.com" in macro.describe(steps) and macro.risky(steps) == "웹사이트를 엽니다"
    assert macro.risky(macro.parse([{"do": "key", "value": "alt+f4"}])) == "창이나 탭을 닫습니다"
    for bad in ([], [{"do": "fly", "value": "x"}], [{"do": "wait", "value": "60"}], [{"do": "mouse", "value": "toggle_draw"}],
                [{"do": "key", "value": "ctrl+notakey"}], [{"do": "key", "value": "a"}] * 11):
        with pytest.raises(macro.MacroError):
            macro.parse(bad)
    ok, _, log = macro.start([{"do": "wait", "value": "5"}, {"do": "key", "value": "f5"}], dry_run=True)
    assert ok
    time.sleep(.1)
    macro.cancel_all()                     # Pause: the rest is not run
    deadline = time.time() + 2
    while macro.running() and time.time() < deadline:
        time.sleep(.02)
    assert not macro.running() and log[-1] == "cancelled" and "key:f5" not in log


def ai(item, **kw):
    base = {"gesture_key": "thumb_ring", "new_name": "", "new_posture": "", "new_trigger": "", "new_sequence": [], "app_match": "",
            "action_type": "hotkey", "action_value": "f5", "steps": [], "enabled": True, "reason": ""}
    return {"summary": "", "questions": [], "proposals": [{**base, **item}]}


def test_a_macro_proposal_is_saved_as_steps_and_runs_through_the_binding(registry):
    answer = ai({"action_type": "macro", "action_value": "", "steps": [
        {"do": "key", "value": "ctrl+c"}, {"do": "wait", "value": "1"}, {"do": "type", "value": "안녕"}]})
    row = lb.check(answer, gestures(registry))["proposals"][0]
    assert row["ok"] and row["checked"] and len(row["steps"]) == 3 and row["after"].startswith("매크로")
    lb.apply([row], registry)
    b = registry.get_binding("thumb_ring")
    assert b["action_type"] == "macro" and b["badge"].startswith("매크로")
    ok, message = registry.execute_resolved_action({"action_type": "macro", "action_value": b["action_value"]}, dry_run=True)
    assert ok


def test_the_ai_can_create_a_composite_gesture_but_not_a_duplicate(registry):
    answer = ai({"gesture_key": lb.NEW, "new_name": "집고 오른쪽", "new_posture": "thumb_index", "new_trigger": "swipe_right",
                 "action_type": "hotkey", "action_value": "ctrl+tab"})
    row = lb.check(answer, gestures(registry))["proposals"][0]
    assert row["ok"] and row["checked"] and row["new_trigger"] == "swipe_right"
    lb.apply([row], registry)
    comp = [c for c in registry.composite_gestures.values() if c["trigger_type"] == "swipe_right"]
    assert comp and comp[0]["base_posture"] == "thumb_index" and comp[0]["name"] == "집고 오른쪽"
    again = lb.check(answer, gestures(registry))["proposals"][0]
    assert not again["ok"] and "이미 있는" in again["problem"]
    risky = lb.check(ai({"gesture_key": lb.NEW, "new_posture": "thumb_middle", "new_trigger": "double_pinch"}),
                     gestures(registry))["proposals"][0]
    assert risky["ok"] and risky["risky"] and not risky["checked"]          # overlaps the click
    bad = lb.check(ai({"gesture_key": lb.NEW, "new_posture": "any", "new_trigger": "hold"}), gestures(registry))["proposals"][0]
    assert not bad["ok"]


def test_drawing_shapes_are_in_the_ai_list(registry):
    keys = {g["key"] for g in gestures(registry)}
    assert {"draw_circle", "draw_zigzag"} <= keys


def test_history_lists_what_the_ai_changed_and_undoes_it(registry, tmp_path):
    history = lb.History(tmp_path / "ai.json")
    before = registry.get_binding("thumb_ring")
    rows = lb.check(ai({"action_value": "ctrl+shift+t"}), gestures(registry))["proposals"]
    lb.apply(rows, registry, history=history, request="엄지+약지면 닫은 탭 복구")
    listed = history.rows(registry)
    assert len(listed) == 1 and listed[0]["current"] and listed[0]["request"].startswith("엄지+약지")
    assert listed[0]["before_text"] == lb.format_action_badge(before["action_type"], before["action_value"])
    history.undo(listed[0]["index"], registry)
    after = registry.get_binding("thumb_ring")
    assert (after["action_type"], after["action_value"]) == (before["action_type"], before["action_value"])
    assert history.rows(registry) == []
    # a new gesture: undo deletes it; a later manual change is not undone over
    rows = lb.check(ai({"gesture_key": lb.NEW, "new_posture": "rock_sign", "new_trigger": "swipe_up"}), gestures(registry))["proposals"]
    lb.apply(rows, registry, history=history)
    entry = history.rows(registry)[0]
    assert entry["created"] and entry["gesture_key"] in registry.composite_gestures
    history.undo(entry["index"], registry)
    assert entry["gesture_key"] not in registry.composite_gestures
    rows = lb.check(ai({"action_value": "f6"}), gestures(registry))["proposals"]
    lb.apply(rows, registry, history=history)
    registry.set_binding("thumb_ring", "hotkey", "f7")
    entry = history.rows(registry)[0]
    assert not entry["current"]
    with pytest.raises(ValueError):
        history.undo(entry["index"], registry)


def test_the_ai_can_create_a_time_series_motion_and_undo_it(registry, tmp_path):
    history = lb.History(tmp_path / "ai.json")
    answer = ai({"gesture_key": lb.NEW, "new_name": "손 펴고 오른쪽 위로", "new_posture": "open_hand",
                 "new_trigger": "motion_diag_up_right", "action_type": "hotkey", "action_value": "win+tab"})
    row = lb.check(answer, gestures(registry))["proposals"][0]
    assert row["ok"] and row["new_trigger"] == "motion_diag_up_right"
    row["checked"] = True
    lb.apply([row], registry, history=history)
    motion = [m for m in registry.custom_motions.values() if m["direction"] == "diag_up_right"]
    assert motion and motion[0]["posture"] == "open_hand" and motion[0]["require_motion_mode"]
    dup = lb.check(answer, gestures(registry))["proposals"][0]
    assert not dup["ok"] and "이미 있는" in dup["problem"]
    entry = history.rows(registry)[0]
    assert entry["current"] and entry["motion"]
    history.undo(entry["index"], registry)
    assert not [m for m in registry.custom_motions.values() if m["direction"] == "diag_up_right"]


def test_a_shown_shape_with_a_hold_gesture_already_changes_that_gesture(registry):
    """2026-11-10: showing the upside-down V again registered a new 'hold' gesture over the one made before."""
    registry.register_composite_gesture("", "거꾸로 V 1초", "v_sign_down", "hold", "launch_app", "https://youtube.com")
    gs = gestures(registry)
    shown = {"state": "done", "posture": "v_sign_down", "name": "거꾸로 V", "share": 1.0, "gesture_key": ""}
    key = lb.shown_key(shown, gs)
    assert key.startswith("comp_v_sign_down_hold")
    assert lb.SHOWN not in lb.context_block(shown, "", gs) and key in lb.context_block(shown, "", gs)
    assert not any(g["key"] == lb.SHOWN for g in lb.with_shown(gs, shown))


def test_the_ai_is_told_that_a_full_window_means_opening_it_maximised():
    text = lb.system_prompt([])
    assert "win+up" in text and "yt_fullscreen" in text and "f11" in text


def test_a_shown_movement_is_a_shape_plus_a_direction_and_reaches_the_ai(registry):
    """2026-11-10 report: '움직이는 동작은 알아먹질 않는데' (a moving hand came out as a mix of shapes)."""
    probe, t = PoseProbe(), 0.0
    probe.COUNTDOWN = 0.0
    probe.start(t)
    for i in range(60):                                   # a fist moving right by 3 hand sizes, changing shape on the way
        shape = "fist" if i % 3 else "thumb_index"
        probe.step(t, shape, (1.0 + i * .05, 2.0), 1.0)
        t += 1 / 30
    probe.finish_if_due(t + 1)
    view = probe.view(t + 1)
    assert view["state"] == "done" and view["kind"] == "motion" and view["posture"] == "fist" and view["direction"] == "right"
    block = lb.context_block(view, "", gestures(registry))
    assert "motion_right" in block and "swipe_right" in block
    still, t = PoseProbe(), 0.0
    still.COUNTDOWN = 0.0
    still.start(t)
    for i in range(60):
        still.step(t, "v_sign", (1.0 + (i % 2) * .05, 2.0), 1.0)    # trembling in place: a held shape
        t += 1 / 30
    still.finish_if_due(t + 1)
    assert still.view(t + 1).get("kind") != "motion" and still.view(t + 1)["posture"] == "v_sign"


def test_delete_removes_the_connection_and_forget_only_the_line(registry, tmp_path):
    history = lb.History(tmp_path / "ai.json")
    default = registry.get_binding("thumb_ring")
    lb.apply(lb.check(ai({"action_value": "f6"}), gestures(registry))["proposals"], registry, history=history)
    lb.apply(lb.check(ai({"action_value": "f7"}), gestures(registry))["proposals"], registry, history=history)
    newest = history.rows(registry)[0]
    history.delete(newest["index"], registry)               # not back to f6: the built-in command
    b = registry.get_binding("thumb_ring")
    assert (b["action_type"], b["action_value"]) == (default["action_type"], default["action_value"])
    older = history.rows(registry)[0]
    assert not older["current"]
    history.forget(older["index"])
    assert history.rows(registry) == []
    lb.apply(lb.check(ai({"app_match": "chrome.exe", "action_value": "f8"}), gestures(registry))["proposals"], registry,
             history=history)
    history.delete(history.rows(registry)[0]["index"], registry)
    assert not registry.get_binding("thumb_ring").get("app_overrides")


def test_a_circle_shown_to_the_probe_is_the_drawn_circle_not_a_diagonal(registry):
    """2026-11-10 report: drawing a circle came out as '주먹 쥐기 모양으로 우하단 대각선'."""
    import math
    probe, t = PoseProbe(), 0.0
    probe.COUNTDOWN = 0.0
    probe.start(t)
    for i in range(60):                                   # a fist drawing a circle (2 hand sizes across), started at the top
        a = -math.pi / 2 + 2 * math.pi * i / 59
        probe.step(t, "fist", (5 + math.cos(a), 5 + math.sin(a)), 1.0)
        t += 1 / 30
    probe.finish_if_due(t + 1)
    view = probe.view(t + 1)
    assert view["state"] == "done" and view["kind"] == "drawn" and view["shape_key"] == "draw_circle", view
    block = lb.context_block(view, "", gestures(registry))
    assert '"draw_circle"' in block and "그리기 모드" in block


def test_the_ai_is_told_not_to_add_steps_nobody_asked_for():
    """2026-11-10: '구글 로그인 페이지로 이동' came back with 'wait 2 s, Win+Up' added."""
    text = lb.system_prompt([])
    assert "no extra steps" in text and "ONE launch_app" in text


def test_a_row_that_cannot_be_saved_does_not_lose_the_history_of_the_rows_before_it(tmp_path):
    """A new sequence that clashes with one that is switched off passes the check (the list has only the enabled ones)
    and fails on saving. The row before it was already saved: it must still be in the history, to be undone."""
    import manux.compat.hand_mouse.action_bindings as ab
    import manux.compat.hand_mouse.llm_binding as lb
    from manux.compat.hand_mouse.sequence import SequenceStore
    reg = ab.ActionBindingRegistry(tmp_path / "b.json")
    seqs = SequenceStore(tmp_path / "s.json")
    seqs.set_enabled(seqs.add("old", ["v_sign", "fist"]), False)
    gestures = lb.gesture_list(reg)
    res = lb.check({"proposals": [
        {"gesture_key": "thumb_ring", "action_type": "hotkey", "action_value": "ctrl+t"},
        {"gesture_key": lb.NEW, "new_sequence": ["v_sign", "fist"], "action_type": "hotkey", "action_value": "ctrl+w"},
    ]}, gestures)
    rows = [{**r, "checked": True} for r in res["proposals"]]
    assert all(r["ok"] for r in rows)
    history = lb.History(tmp_path / "h.json", tmp_path / "s.json")
    done = lb.apply(rows, reg, history, "x", sequences=seqs)
    assert len(done) == 2 and done[1].startswith("저장하지 못함")
    assert reg.get_binding("thumb_ring")["action_value"] == "ctrl+t"
    saved = history.load()
    assert [e["gesture_key"] for e in saved] == ["thumb_ring"]
    history.undo(0, reg)
    assert reg.get_binding("thumb_ring")["action_value"] == "right_click"


# ---------------------------------------------------------------------------- changing a connection later (2026-11-11)
def _new_hold(registry, history, posture):
    rows = lb.check(ai({"gesture_key": lb.NEW, "new_posture": posture, "new_trigger": "hold",
                        "action_type": "hotkey", "action_value": "f9"}), gestures(registry))["proposals"]
    for r in rows:
        r["checked"] = True
    lb.apply(rows, registry, history=history)
    return history.rows(registry)[0]


def test_a_new_hold_starts_at_its_minimum_and_the_user_can_lengthen_it(registry, tmp_path):
    history = lb.History(tmp_path / "ai.json")
    entry = _new_hold(registry, history, "rock_sign")
    key = entry["gesture_key"]
    assert registry.composite_gestures[key]["hold_seconds"] == lb.HOLD_MIN == entry["hold_seconds"] == entry["hold_min"]
    assert entry["editable"] and entry["hold_max"] == lb.HOLD_MAX
    msg = history.edit(entry["index"], registry, "hotkey", "f9", 1.2)
    assert "유지 1.2초" in msg and registry.composite_gestures[key]["hold_seconds"] == 1.2
    # never below the minimum nor above the maximum
    history.edit(entry["index"], registry, "hotkey", "f9", 0.1)
    assert registry.composite_gestures[key]["hold_seconds"] == lb.HOLD_MIN
    history.edit(entry["index"], registry, "hotkey", "f9", 9)
    assert registry.composite_gestures[key]["hold_seconds"] == lb.HOLD_MAX
    # saved to disk: a fresh registry (the running app reloads the file) sees it
    assert ActionBindingRegistry(registry.path).composite_gestures[key]["hold_seconds"] == lb.HOLD_MAX


def test_everyday_shapes_keep_a_longer_minimum(registry, tmp_path):
    history = lb.History(tmp_path / "ai.json")
    entry = _new_hold(registry, history, "fist")
    assert entry["hold_min"] == lb.HOLD_MIN_EVERYDAY == registry.composite_gestures[entry["gesture_key"]]["hold_seconds"]
    history.edit(entry["index"], registry, "hotkey", "f9", 0.4)
    assert registry.composite_gestures[entry["gesture_key"]]["hold_seconds"] == lb.HOLD_MIN_EVERYDAY


def test_the_command_can_be_changed_and_the_line_stays_in_force(registry, tmp_path):
    history = lb.History(tmp_path / "ai.json")
    entry = _new_hold(registry, history, "rock_sign")
    key = entry["gesture_key"]
    history.edit(entry["index"], registry, "launch_app", "notepad.exe", None)
    b = registry.get_binding(key)
    assert (b["action_type"], b["action_value"]) == ("launch_app", "notepad.exe")
    row = history.rows(registry)[0]
    assert row["current"] and row["edited"] and row["action_value"] == "notepad.exe"
    with pytest.raises(ValueError):
        history.edit(row["index"], registry, "hotkey", "", None)                  # empty
    with pytest.raises(ValueError):
        history.edit(row["index"], registry, "mouse_action", "fly_away", None)    # not a known command
    assert registry.get_binding(key)["action_value"] == "notepad.exe"            # a refused edit changes nothing
    # an existing gesture (no hold): only the command; undo still goes back to what was there before the AI
    before = registry.get_binding("thumb_ring")
    lb.apply(lb.check(ai({"action_value": "ctrl+shift+t"}), gestures(registry))["proposals"], registry, history=history)
    row = history.rows(registry)[0]
    assert "hold_seconds" not in row
    with pytest.raises(ValueError):
        history.edit(row["index"], registry, "hotkey", "f8", 1.0)                 # no hold time on this one
    history.edit(row["index"], registry, "hotkey", "f8", None)
    assert registry.get_binding("thumb_ring")["action_value"] == "f8"
    history.undo(history.rows(registry)[0]["index"], registry)
    assert registry.get_binding("thumb_ring")["action_value"] == before["action_value"]
