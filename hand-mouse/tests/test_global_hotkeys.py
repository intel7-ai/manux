import sys
import threading

import pytest

from src.global_hotkeys import DEFAULTS, MOD_ALT, MOD_CONTROL, MOD_SHIFT, GlobalHotkeys, label, normalize, parse


def test_parse_and_normalize():
    assert parse("ctrl+alt+shift+q") == (MOD_CONTROL | MOD_ALT | MOD_SHIFT, ord("Q"))
    assert parse("Ctrl + F9") == (MOD_CONTROL, 0x78)
    assert normalize("Shift+Q+Ctrl+Alt") == "ctrl+alt+shift+q"
    assert label(DEFAULTS["toggle"]) == "Ctrl + Alt + Shift + Q"


@pytest.mark.parametrize("combo", ["", "q", "space", "ctrl+alt", "ctrl+q+w", "ctrl+ㅂ"])
def test_unusable_combinations(combo):
    assert parse(combo) is None and normalize(combo) is None


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey is Windows only")
def test_register_reports_taken_and_invalid_combinations():
    first = GlobalHotkeys({"toggle": lambda: None})
    first.start({"toggle": "ctrl+alt+shift+f11"})
    try:
        assert first.failed == set()
        second = GlobalHotkeys({"toggle": lambda: None, "hide": lambda: None})
        second.start({"toggle": "ctrl+alt+shift+f11", "hide": "not a shortcut"})
        try:
            assert second.failed == {"toggle", "hide"}       # taken by `first`, and unusable
            assert second.set({"toggle": "ctrl+alt+shift+f10"}) == set()
        finally:
            second.stop()
    finally:
        first.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey is Windows only")
def test_pressing_the_combination_calls_the_action():
    import ctypes
    pressed = threading.Event()
    keys = GlobalHotkeys({"toggle": pressed.set})
    keys.start({"toggle": "ctrl+alt+shift+f12"})
    try:
        # simulate WM_HOTKEY for id 1 instead of pressing real keys
        ctypes.windll.user32.PostThreadMessageW(keys._thread_id, 0x0312, 1, 0)
        assert pressed.wait(2.0)
    finally:
        keys.stop()
