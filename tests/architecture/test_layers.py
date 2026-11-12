"""Enforces the dependency rules of apps/manux/docs/architecture.md section 4.

If you change a rule, change the table in the doc in the same PR.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2] / "src" / "manux"

# layer -> MANUX layers it may import (besides itself)
ALLOWED = {
    "core": set(),
    "settings": {"core"},
    "capture": {"core"},
    "link": {"core", "capture"},
    "tracking": {"core"},
    "perception": {"core"},
    "ml": {"core"},
    "data": {"core"},
    "platform": {"core"},
    "actions": {"core", "settings"},
    "features": {"core", "perception", "ml"},
    "output": {"core", "actions", "platform"},
    "engine": {"core", "perception", "actions", "settings"},
    "ui": {"core", "engine", "settings"},
}
ROOT_MODULES = {"__init__.py", "app.py", "desktop.py", "__main__.py"}  # composition roots: may import every layer

# third-party module -> layers that may import it (app.py may import anything)
THIRD_PARTY = {
    "PySide6": {"ui", "platform"},
    "cv2": {"capture", "tracking"},
    "mediapipe": {"capture", "tracking"},
    "pyautogui": {"output", "platform"},
    "ctypes": {"output", "platform"},
    "uiautomation": {"output", "platform"},
    "win32api": {"output", "platform"},
    "win32con": {"output", "platform"},
    "win32gui": {"output", "platform"},
    "onnxruntime": {"ml"},
    "aiohttp": {"link"},
    "aiortc": {"link"},
    "av": {"link"},
    "cryptography": {"link"},
    "torch": set(),  # tools/ only, never inside the app
}


def _modules():
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG)
        yield path, rel


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise AssertionError(f"{path}:{node.lineno}: use absolute imports (manux.<layer>...), not relative ones")
            if node.module:
                yield node.module, node.lineno


def test_layers_match_the_doc():
    present = {p.name for p in PKG.iterdir() if p.is_dir() and not p.name.startswith("__")}
    assert present == set(ALLOWED), (
        "src/manux/ folders and the ALLOWED table (architecture.md section 4) differ: "
        f"missing={set(ALLOWED) - present}, unknown={present - set(ALLOWED)}"
    )


@pytest.mark.parametrize("path,rel", list(_modules()), ids=lambda v: str(v) if isinstance(v, Path) else None)
def test_imports_follow_the_rules(path: Path, rel: Path):
    if len(rel.parts) == 1:
        assert rel.name in ROOT_MODULES, f"unexpected top-level module {rel}"
        return
    layer = rel.parts[0]
    feature = rel.parts[1] if layer == "features" and len(rel.parts) > 2 else None
    problems = []
    for name, line in _imports(path):
        top = name.split(".")[0]
        if top == "manux":
            parts = name.split(".")
            if len(parts) < 2:
                problems.append(f"{line}: import a layer module, not the manux package itself")
                continue
            target = parts[1]
            if target in ("app", "__main__"):
                problems.append(f"{line}: only the composition root may be imported by nobody ({name})")
            elif target != layer and target not in ALLOWED[layer]:
                problems.append(f"{line}: {layer} may not import manux.{target}")
            elif layer == "features" == target and len(parts) > 2 and feature and parts[2] != feature:
                problems.append(f"{line}: feature {feature!r} may not import another feature ({name})")
            continue
        allowed = THIRD_PARTY.get(top)
        if allowed is not None and layer not in allowed:
            problems.append(f"{line}: {layer} may not import {top}")
        if layer == "core" and top not in sys.stdlib_module_names and top != "__future__":
            problems.append(f"{line}: core is standard-library only ({top})")
    assert not problems, f"{rel}:\n  " + "\n  ".join(problems)
