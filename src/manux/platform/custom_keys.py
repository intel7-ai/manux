"""Original key-name validation, without importing Windows input in the UI or parser."""

def known_key(name: str) -> bool:
    try:
        import pyautogui
        return name in pyautogui.KEYBOARD_KEYS
    except Exception:
        return name.isascii() and bool(name)
