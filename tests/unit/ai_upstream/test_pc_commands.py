"""Connection, review, persistence and isolated execution of the PC command extension."""
import base64
import json
import subprocess
import threading
from types import SimpleNamespace

import pytest

from manux.compat.hand_mouse import llm_binding as lb
from manux.compat.hand_mouse.action_bindings import ActionBindingRegistry, WINDOWS_HOTKEY_PRESETS
from manux.compat.hand_mouse.pc_commands import SYSTEM_ACTIONS
from manux.output.windows import custom_commands as commands


def proposal(kind, value):
    return dict(gesture_key="thumb_ring", action_type=kind, action_value=value, enabled=True, reason="합성 시험")


@pytest.mark.parametrize("kind,value", [("powershell", "Get-Process | Select-Object -First 5"), ("system_action", "shutdown")])
def test_proposal_requires_review_then_saves_without_executing(tmp_path, monkeypatch, kind, value):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Saving must not execute a command"))
    reg = ActionBindingRegistry(tmp_path / "bindings.json")
    before = reg.get_binding("thumb_ring").copy()
    history = lb.History(tmp_path / "history.json")
    rows = lb.check(dict(proposals=[proposal(kind, value)]), lb.gesture_list(reg))["proposals"]
    assert rows[0]["ok"] and rows[0]["risky"] and not rows[0]["checked"]
    assert not lb.apply(rows, reg, history=history)
    assert reg.get_binding("thumb_ring") == before
    rows[0]["checked"] = True
    assert lb.apply(rows, reg, history=history)
    restored = ActionBindingRegistry(reg.path).get_binding("thumb_ring")
    assert (restored["action_type"], restored["action_value"]) == (kind, value)
    history.edit(0, reg, "powershell", "Write-Output '한글'", None)
    assert reg.get_binding("thumb_ring")["action_value"] == "Write-Output '한글'"


def test_prompt_schema_cover_catalogue_and_unlisted_powershell(tmp_path):
    reg = ActionBindingRegistry(tmp_path / "bindings.json")
    prompt = lb.system_prompt(lb.gesture_list(reg))
    assert all(p["value"] + ": " + p["label"] in prompt for p in WINDOWS_HOTKEY_PRESETS)
    assert all(key + ": " + row["label"] in prompt for key, row in SYSTEM_ACTIONS.items())
    assert "presets are examples, NOT the limit" in prompt
    kinds = lb.schema(["thumb_ring"])["properties"]["proposals"]["items"]["properties"]["action_type"]["enum"]
    assert {"powershell", "system_action"} <= set(kinds)
    assert lb._check_value("powershell", "$p=Get-Process\n$p | Select-Object Name") is None


@pytest.mark.parametrize("kind,value", [("system_action", "made_up"), ("powershell", " "),
                                        ("powershell", "a\0b"), ("powershell", "가" * 8001),
                                        ("powershell", "😀" * 4001)])
def test_invalid_command_cannot_be_saved(kind, value):
    assert lb._check_value(kind, value)


def test_every_builtin_dry_run_is_side_effect_free(monkeypatch):
    from manux.output.windows.custom_actions import execute_resolved_action
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Dry run launched a process"))
    for key in SYSTEM_ACTIONS:
        assert execute_resolved_action(None, dict(action_type="system_action", action_value=key), dry_run=True)[0], key
    for key in ("shutdown", "restart"):
        script = SYSTEM_ACTIONS[key]["value"]
        assert "/t 0" in script and "/f" not in script


def test_multiline_and_unicode_are_preserved_without_a_command_shell():
    script = '$text = "한글 \'test\'"\n$text | Write-Output'
    args = commands.command_line("pwsh.exe", script)
    assert args[:4] == ["pwsh.exe", "-NoLogo", "-NoProfile", "-NonInteractive"]
    decoded = base64.b64decode(args[-1]).decode("utf-16-le")
    assert script in decoded
    assert "ExecutionPolicy" not in " ".join(args)
    with pytest.raises(ValueError):
        commands.command_line("pwsh.exe", "")


@pytest.mark.parametrize("code", [0, 5])
def test_background_runner_reports_real_exit_and_output(monkeypatch, code):
    seen = []

    def popen(args, **kwargs):
        seen.append((args, kwargs))
        kwargs["stdout"].write("시험 출력\n".encode("utf-8"))
        return SimpleNamespace(poll=lambda: code)

    monkeypatch.setattr(commands, "powershell_path", lambda: "pwsh.exe")
    monkeypatch.setattr(subprocess, "Popen", popen)
    runner = commands.CommandRunner()
    ok, _ = runner.start("Write-Output '시험 출력'", wait=True)
    result = runner.status()
    assert ok == (code == 0) and result["exit_code"] == code
    assert result["output"] == "시험 출력\n" and not result["running"]
    assert seen[0][1]["shell"] is False
    assert seen[0][1]["stdin"] == subprocess.DEVNULL


def test_only_owned_running_command_is_cancelled_and_duplicates_are_refused(monkeypatch):
    launched = threading.Event()
    process = SimpleNamespace(code=None)
    process.poll = lambda: process.code
    process.terminate = lambda: setattr(process, "code", -15)
    process.wait = lambda timeout: process.code

    def popen(*args, **kwargs):
        launched.set()
        return process

    monkeypatch.setattr(commands, "powershell_path", lambda: "pwsh.exe")
    monkeypatch.setattr(subprocess, "Popen", popen)
    runner = commands.CommandRunner()
    assert runner.start("Start-Sleep 100")[0]
    assert launched.wait(1)
    assert not runner.start("Write-Output 'second'")[0]
    runner.cancel()
    runner._thread.join(2)
    assert process.code == -15
    assert "중지" in runner.status()["message"] and runner.status()["exit_code"] is None


def test_macro_waits_for_powershell_and_stops_before_following_key_on_failure(monkeypatch):
    from manux.compat.hand_mouse import action_bindings
    from manux.output.windows import custom_macro
    calls = []
    monkeypatch.setattr(commands.runner, "start", lambda script, **kwargs: (calls.append(kwargs) or False, "실패"))
    monkeypatch.setattr(action_bindings, "get_action_registry", lambda: SimpleNamespace(
        execute_resolved_action=lambda *a, **k: pytest.fail("Next macro step must not execute")))
    ok, _, log = custom_macro.start(json.dumps([dict(do="powershell", value="throw 'test'"),
                                                dict(do="key", value="ctrl+w")]), wait=True)
    assert ok and calls[0]["wait"] is True and "failed:실패" in log


def test_execution_status_and_catalogue_reach_ui_without_execution(tmp_path, monkeypatch):
    from PySide6.QtCore import QObject
    from manux.compat.hand_mouse import ports
    from manux.ui.ai_customization import AiCustomizationBridge
    from PySide6.QtWidgets import QApplication
    application = QApplication.instance() or QApplication([])
    state = dict(running=False, message="합성 완료", output="결과", exit_code=0)
    monkeypatch.setattr(ports, "command_status", lambda: state)
    owner = QObject()
    bridge = AiCustomizationBridge(owner, dry_run=True)
    try:
        bridge._poll()
        application.processEvents()
        assert bridge.commandExecution == state
        assert any(row["value"] == "shutdown" for row in bridge.systemActionPresets)
    finally:
        bridge.close()
