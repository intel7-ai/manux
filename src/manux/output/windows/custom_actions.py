"""Original a700452 action executor; the host sink is its only runtime caller."""
from __future__ import annotations
import os
from typing import Any, Dict, Tuple
from manux.compat.hand_mouse.action_bindings import format_action_badge, parse_hotkey_keys, YOUTUBE_KEY_PRESETS, web_address, _command
def _shell_open(program: str, params: str = "") -> bool:
    """ShellExecute: finds programs registered under App Paths (chrome.exe, msedge.exe, winword.exe) like the Run box."""
    import ctypes
    return int(ctypes.windll.shell32.ShellExecuteW(None, "open", program, params or None, None, 1)) > 32

def execute_resolved_action(
    self,
    resolved: Dict[str, Any],
    *,
    dry_run: bool = False,
    mouse_controller: Any = None,
) -> Tuple[bool, str]:
    """Execute a resolved custom action (`hotkey`, `youtube_key`, `launch_app`, or one-shot `mouse_action`)."""
    atype = str(resolved.get("action_type", "none")).strip()
    aval = str(resolved.get("action_value", "none")).strip()
    badge = str(resolved.get("badge") or format_action_badge(atype, aval))

    if atype == "system_action":
        from manux.compat.hand_mouse.pc_commands import SYSTEM_ACTIONS
        spec = SYSTEM_ACTIONS.get(aval)
        if spec is None:
            return False, "알 수 없는 PC 기능"
        return execute_resolved_action(self, {"action_type": spec["kind"], "action_value": spec["value"],
                                             "badge": spec["label"]}, dry_run=dry_run, mouse_controller=mouse_controller)
    if atype == "powershell":
        from manux.output.windows.custom_commands import runner
        return runner.start(aval, label=badge if resolved.get("badge") else "PowerShell", dry_run=dry_run)

    if not dry_run:
        import pyautogui  # after Qt initializes; importing must not change the host's startup DPI mode

    if atype == "none" or aval == "none":
        return False, "사용 안 함"

    if atype == "hotkey":
        keys = parse_hotkey_keys(aval)
        if not keys:
            return False, "잘못된 단축키"
        if keys == ["alt", "f4"] and mouse_controller is not None:
            if not dry_run and hasattr(mouse_controller, "close_foreground_window"):
                # asked of the window itself and watched (mouse_controller.close_foreground_window), not blind keys
                if not mouse_controller.close_foreground_window():
                    return False, "보호된 창 (닫기 거부)"
                return True, badge
            if not dry_run and hasattr(mouse_controller, "_safe_to_close_foreground") and not mouse_controller._safe_to_close_foreground():
                return False, "보호된 창 (닫기 거부)"
        if mouse_controller is not None and hasattr(mouse_controller, "event_log"):
            mouse_controller.event_log.append(f"hotkey:{'+'.join(keys)}")
        if not dry_run:
            if len(keys) == 1:
                pyautogui.press(keys[0], _pause=False)
            elif sorted(keys) == ["d", "win"]:
                from manux.platform.custom_topmost_guard import guard   # Win+D once left a window "always on top"
                guard(lambda: pyautogui.hotkey(*keys, _pause=False))
            else:
                pyautogui.hotkey(*keys, _pause=False)
        return True, badge

    if atype == "youtube_key":
        yt = YOUTUBE_KEY_PRESETS.get(aval)
        keys = list(yt["keys"]) if yt else parse_hotkey_keys(aval)
        if not keys:
            return False, "알 수 없는 YouTube 기능키"
        if mouse_controller is not None and hasattr(mouse_controller, "event_log"):
            mouse_controller.event_log.append(f"youtube:{aval}")
        if not dry_run:
            if len(keys) == 1:
                pyautogui.press(keys[0], _pause=False)
            else:
                pyautogui.hotkey(*keys, _pause=False)
        return True, badge

    if atype == "macro":
        from manux.compat.hand_mouse.macro import start
        if mouse_controller is not None and hasattr(mouse_controller, "event_log"):
            mouse_controller.event_log.append("macro")
        ok, message, _ = start(aval, dry_run=dry_run, mouse_controller=mouse_controller)
        return ok, message

    if atype == "launch_app":
        if not aval:
            return False, "실행할 프로그램 미지정"
        aval = web_address(aval)
        if mouse_controller is not None and hasattr(mouse_controller, "event_log"):
            mouse_controller.event_log.append(f"launch:{aval}")
        if not dry_run:
            # what the value starts on this computer (src/app_launch.py: a folder, a link, a program by path or
            # by name - "arduino", "kakaotalk" - found in the Start menu or under Program Files)
            from manux.compat.hand_mouse import app_launch
            target = app_launch.resolve(aval)
            try:
                if target.ok:
                    app_launch.start(target)
                elif os.name != "nt" or not _shell_open(*_command(aval)):
                    return False, f"실행 실패: {target.note}"       # (Windows' own search was the last try)
            except Exception as exc:
                return False, f"실행 실패: {exc}"
        return True, badge

    if atype == "mouse_action":
        if mouse_controller is not None:
            if aval in ("back", "forward", "tab_prev", "tab_next", "close_window"):
                ok = mouse_controller.send_motion_action(aval)
                return bool(ok), badge if ok else "실행 거부됨"
            if aval == "show_desktop":
                mouse_controller.event_log.append("show_desktop")
                if not dry_run:
                    from manux.platform.custom_topmost_guard import guard   # Win+D once left a window "always on top"
                    guard(lambda: pyautogui.hotkey("win", "d", _pause=False))
                return True, badge
            if aval == "left_click":
                mouse_controller.event_log.append("left_click")
                if not dry_run:
                    pyautogui.click(button="left", _pause=False)
                return True, badge
            if aval == "right_click":
                mouse_controller.event_log.append("right_click")
                if not dry_run:
                    pyautogui.click(button="right", _pause=False)
                return True, badge
            if aval == "double_click":
                mouse_controller.event_log.append("double_click")
                if not dry_run:
                    pyautogui.doubleClick(button="left", _pause=False)
                return True, badge
        return True, badge

    return False, f"지원하지 않는 동작 형식: {atype}"
