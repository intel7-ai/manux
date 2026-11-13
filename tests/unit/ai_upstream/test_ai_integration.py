"""Source feature on the current engine: additive bindings, real detector path, UI busy and history."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from manux.app import build_engine, schema
from manux.settings.store import Settings
from manux.compat.hand_mouse.action_bindings import ActionBindingRegistry, DEFAULT_GESTURE_BINDINGS
from manux.compat.hand_mouse.llm_binding import History, apply, check, gesture_list
from manux.core.contracts import FrameContext, FrameInfo, HandObservation, OutputTarget, Stage
from manux.engine.ai_customization import CustomPipeline
from manux.engine.commands import SetBusy, SetControl, SetStage
from manux.features.custom_gestures import CustomGestureFeature, CustomGestureFilter, gesture_id
from tests.unit.ai_upstream.test_sequence import v_hand


def pipeline(engine):
    return CustomPipeline(engine, CustomGestureFeature, CustomGestureFilter, gesture_id,
                          DEFAULT_GESTURE_BINDINGS,
                          lambda reg: {r["gesture_key"] for r in History().rows(reg) if r.get("current") and not r.get("app_match")})


def sample(t, n=0):
    frame = FrameInfo(n, t, 1280, 720, True)
    lm = tuple((p.x,p.y,p.z) for p in v_hand(down=True))
    return frame, HandObservation(frame, lm, "Right", 1.)


def test_desktop_composition_and_original_hand_preview_start_together():
    from types import SimpleNamespace
    import numpy as np
    from manux.desktop import attach_ai_customization

    engine = build_engine(Settings(schema()), "pinch")
    runtime = SimpleNamespace(engine=engine)
    original_bindings = dict(engine.router.bindings)
    session = attach_ai_customization(runtime)
    try:
        frame, hand = sample(1)
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        landmarks = [SimpleNamespace(x=p[0], y=p[1], z=p[2]) for p in hand.landmarks]
        assert session.make_shot(image, landmarks).shape == (160, 160, 3)
        session.probe.start(frame.t)
        session.observe(frame.t + .1, hand, image)
        view, shot = session.snapshot()
        assert view is not None and session.probe.active
        assert engine.router.bindings == original_bindings
    finally:
        session.close()


def test_no_ai_connections_leave_every_current_binding_and_feature_unchanged(tmp_path):
    engine = build_engine(Settings(schema()), "pinch")
    before, features, filters = engine.router.bindings, list(engine.features), list(engine.filters)
    p = pipeline(engine)
    p.install(ActionBindingRegistry(tmp_path / "bindings.json"))
    assert engine.router.bindings == before
    assert engine.features == features and engine.filters == filters


def test_source_hold_runs_through_host_and_busy_blocks_it(tmp_path):
    engine = build_engine(Settings(schema()), "pinch")
    reg = ActionBindingRegistry(tmp_path / "bindings.json")
    saved = reg.register_composite_gesture("", "거꾸로 V 유지", "v_sign_down", "hold", "hotkey", "f5", hold_seconds=.4)
    key = saved["key"]
    p = pipeline(engine)
    before = engine.router.bindings
    p.install(reg)
    assert all(engine.router.bindings[k] == v for k,v in before.items())
    engine.commands.put(SetStage(Stage.MAIN))
    engine.commands.put(SetControl(True))
    engine.commands.put(SetBusy("enrolling", True))
    for i in range(30):
        frame, hand = sample(i/30, i)
        snap = engine.step(frame, hand)
        assert not any(g == gesture_id(key) for g, _ in snap.intents)
    engine.commands.put(SetBusy("enrolling", False))
    engine.commands.put(SetControl(True))
    seen = []
    for i in range(30, 62):
        frame, hand = sample(i/30, i)
        snap = engine.step(frame, hand)
        assert not snap.errors
        seen.extend(g for g, _ in snap.intents if g == gesture_id(key))
    assert seen == [gesture_id(key)]


def test_edit_and_delete_use_source_history_and_restore_current_bindings(tmp_path):
    engine = build_engine(Settings(schema()), "pinch")
    reg = ActionBindingRegistry(tmp_path / "bindings.json")
    history = History()
    before = engine.router.bindings
    rows = check({"summary":"", "questions":[], "proposals":[{
        "gesture_key":"thumb_ring", "app_match":"", "action_type":"hotkey", "action_value":"ctrl+t",
        "enabled":True, "reason":""}]}, gesture_list(reg))["proposals"]
    assert apply(rows, reg, history=history)
    p = pipeline(engine)
    p.install(reg)
    assert engine.router.bindings["pinch.thumb_ring"].action == "custom.execute"
    assert all(engine.router.bindings[k] == v for k,v in before.items() if k != "pinch.thumb_ring")
    history.edit(0, reg, "hotkey", "ctrl+w")
    p.install(reg)
    assert reg.resolve_effective_action("thumb_ring")["action_value"] == "ctrl+w"
    history.undo(0, reg)
    p.install(reg)
    assert engine.router.bindings == before


def test_source_dialog_is_not_a_redesigned_or_abbreviated_copy():
    import manux.ui.ai_customization as ui
    path = Path(ui.__file__).with_name("qml") / "AiCustomizationDialog.qml"
    text = path.read_text(encoding="utf-8")
    dialog = text[text.index("    Dialog {\n        id: llmDialog"):text.rfind("\n}")].rstrip("\n")
    import manux.compat.hand_mouse as source
    manifest = json.loads(Path(source.__file__).with_name("upstream.json").read_text(encoding="utf-8"))
    # Light/dark styling may replace only these exact source color literals.
    # Reverse the typed tokens before comparing the entire original dialog;
    # handlers, conditions, data, layout and the upstream expected SHA stay protected.
    source_colors = {
        "Theme.Studio.sourceSurface": '"#F7FAFB"',
        "Theme.Studio.sourcePaper": '"#FFFFFF"',
        "Theme.Studio.sourceSetupError": '"#B3261E"',
        "Theme.Studio.sourceCaptureSurface": '"#111"',
        "Theme.Studio.sourceOverlapWarning": '"#B45309"',
        "Theme.Studio.sourceRequestInfo": '"#0369A1"',
        "Theme.Studio.sourceProposalError": '"#FDECEE"',
        "Theme.Studio.sourceHistorySurface": '"#0E1B26"',
        "Theme.Studio.sourceBoneShadow": '"rgba(0,0,0,0.55)"',
        "Theme.Studio.sourceJoint": '"#FFFFFF"',
        "Theme.Studio.sourcePreviewHighlight": '"#F2B544"',
    }
    theme = (path.parent / "ai_theme" / "Studio.qml").read_text(encoding="utf-8")
    normalized_dialog = dialog
    for token, literal in source_colors.items():
        name = token.rsplit(".", 1)[1]
        assert f"readonly property color {name}:" in theme
        normalized_dialog = normalized_dialog.replace(token, literal)
    # Authorized PC/PowerShell additions are independently exercised; the rest of the upstream UI stays protected.
    import re
    normalized_dialog = re.sub(r"^[ \t]*// COMMAND AUTOMATION BEGIN\n.*?^[ \t]*// COMMAND AUTOMATION END\n", "",
                               normalized_dialog, flags=re.MULTILINE | re.DOTALL)
    normalized_dialog = normalized_dialog.replace(', ["system_action", "PC·Windows 기능"], ["powershell", "PowerShell"]', '')
    normalized_dialog = normalized_dialog.replace('!["mouse_action", "system_action", "powershell"].includes(historyCard.editType)',
                                                 'historyCard.editType !== "mouse_action"')
    assert hashlib.sha256(normalized_dialog.encode()).hexdigest() == manifest["qml_dialog_sha256"]
    assert 'title: "말·글로 연결 (AI)"' in dialog
    assert all(name in dialog for name in ("poseTeachNew", "poseTeachMore", "editLlmConnection", "deleteLlmConnection", "setLlmMotionOnly"))


@pytest.mark.parametrize("layout", ["studio", "standard"])
def test_original_dialog_opens_on_current_ui_and_pauses_gesture_output(tmp_path, layout):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from manux.ui.bridge import Bridge
    from tests.unit.ui.test_bridge import FakeRuntime
    settings = Settings(schema())
    runtime = FakeRuntime(settings)
    runtime.request = lambda req: True
    bridge = Bridge(runtime, settings, tmp_path / "host-config", layout=layout)
    try:
        runtime.engine.commands.drain()
        modal = bridge.window.findChild(QObject, "llmDialog")
        assert modal is not None
        modal.setProperty("visible", True)
        bridge.application.processEvents()
        assert modal.property("title") == "말·글로 연결 (AI)"
        assert bridge.window.findChild(QObject, "poseTeachNew") is not None
        assert any(isinstance(c, SetBusy) and c.on for c in runtime.engine.commands.drain())
        modal.setProperty("visible", False)
        bridge.application.processEvents()
    finally:
        bridge.close()
