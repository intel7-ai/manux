"""Original source action execution behind the host's output and release boundaries."""
from dataclasses import replace
from collections import deque

from manux.core.contracts import ActionCommand, OutputResult
from manux.compat.hand_mouse.action_bindings import DEFAULT_GESTURE_BINDINGS, get_action_registry
from manux.compat.hand_mouse.default_moves import HELD, plan
from manux.output.windows.custom_actions import execute_resolved_action
from manux.output.windows.custom_macro import cancel_all


class CustomOutput:
    def __init__(self, sink, foreground, system_action):
        self.sink, self.foreground, self.system_action = sink, foreground, system_action
        self.event_log = deque(maxlen=100)
        self.dry_run = False

    def cancel(self):
        cancel_all()
        from manux.output.windows.custom_commands import runner
        runner.cancel()

    def send_motion_action(self, name):
        actions = {"back": "nav.back", "forward": "nav.forward", "tab_prev": "tab.prev",
                   "tab_next": "tab.next", "close_window": "window.close"}
        return self.sink.apply(ActionCommand(0., actions[name], "tap", "custom_gestures.command")).accepted

    def close_foreground_window(self):
        return self.send_motion_action("close_window")

    def apply(self, cmd):
        key = str(cmd.params.get("gesture_key", ""))
        if not key:
            return OutputResult(False, "unsupported")
        reg = get_action_registry()
        resolved = reg.resolve_effective_action(key, self.foreground())
        native = cmd.params.get("native")
        default = DEFAULT_GESTURE_BINDINGS.get(key, {})
        source_default = not resolved.get("matched_app") and all(resolved.get(k) == default.get(k) for k in ("action_type", "action_value"))
        if native and ((key in HELD and plan(key, resolved) == "native") or cmd.params.get("fallback_default") and source_default):
            return self.sink.apply(replace(cmd, action=native["action"], params=native["params"]))
        if cmd.phase in ("hold", "end"):
            return OutputResult(True, "ok")
        if resolved.get("action_type") == "mouse_action":
            value = resolved.get("action_value")
            if value in ("toggle_control", "toggle_motion", "toggle_draw"):
                self.system_action(value)
                return OutputResult(True, "ok")
            if value == "task_switcher":
                return self.sink.apply(replace(cmd, action="window.task_switcher", phase="tap", params={}))
        ok, message = execute_resolved_action(reg, resolved, mouse_controller=self)
        return OutputResult(ok, "ok" if ok else "error:" + message)


def voice_typing():
    import pyautogui
    pyautogui.hotkey("win", "h", _pause=False)
