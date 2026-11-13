"""Hand shapes the user named (2026-11-11: "리스트에 없는 동작은 … 없다고 하고 신규 동작으로 하고 유저에게 이름을 정하게").

[손 모양·움직임 보여주기] answers "목록에 없는 손 모양" for a look that is none of the listed shapes. The user then gives it
a name; the look's joints are kept as the first example of that shape (src/posture_teach.py), and after [학습] ->
[적용] the taught model tells it. No rule knows a named shape: without the applied model it is never recognised.

The names live in config/custom_postures.json ({"custom_1": "이름", ...}). `register()` adds them to the lists the rest
of the app reads (action_bindings.POSTURE_NAMES_KO, sequence.POSTURES), so a named shape can be used wherever a listed
one can: hold / swipe composites, motions, sequences, drawing with a held shape, the AI's proposals.
"""
from __future__ import annotations

from manux.compat.hand_mouse.paths import DATA_ROOT

import json
import os
from pathlib import Path
from typing import Dict, Optional

ROOT = DATA_ROOT
PATH = ROOT / "config" / "custom_postures.json"
PREFIX = "custom_"
MAX_NAME = 20


def is_custom(key: str) -> bool:
    return str(key).startswith(PREFIX)


def load(path: Optional[Path] = None) -> Dict[str, str]:
    try:
        data = json.loads(Path(path or PATH).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if is_custom(k) and str(v).strip()}


def _save(names: Dict[str, str], path: Optional[Path] = None) -> None:
    path = Path(path or PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def register(path: Optional[Path] = None) -> Dict[str, str]:
    """The saved names into the app's posture lists (again after add / remove). Returns them."""
    from manux.compat.hand_mouse import sequence
    from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
    names = load(path)
    for k in [k for k in POSTURE_NAMES_KO if is_custom(k) and k not in names]:
        del POSTURE_NAMES_KO[k]
    sequence.POSTURES[:] = [p for p in sequence.POSTURES if not is_custom(p)] + list(names)
    any_name = POSTURE_NAMES_KO.pop("any", None)          # "any" stays the last entry of the lists on the screen
    POSTURE_NAMES_KO.update(names)
    if any_name is not None:
        POSTURE_NAMES_KO["any"] = any_name
    return names


def add(name: str, path: Optional[Path] = None) -> str:
    """A new named shape; a name the user gave before (spaces aside) is that shape again. Returns its key.
    ValueError (a sentence for the screen) for an empty name or a listed shape's name."""
    from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
    name = " ".join(str(name or "").split())[:MAX_NAME]
    if not name:
        raise ValueError("새 손 모양의 이름을 적어 주세요.")
    names = load(path)
    squash = lambda t: "".join(str(t).split())
    for key, old in names.items():        # "옆 따봉" and "옆따봉" are one shape (2026-11-11: two were made by a space)
        if squash(old) == squash(name):
            return key
    if any(squash(name) in (squash(v), squash(v.split(" (")[0])) for k, v in POSTURE_NAMES_KO.items() if not is_custom(k)):
        raise ValueError(f"'{name}'은 이미 있는 손 모양 이름이에요. 목록에서 골라 주세요.")
    n = 1
    while f"{PREFIX}{n}" in names:
        n += 1
    key = f"{PREFIX}{n}"
    names[key] = name
    _save(names, path)
    register(path)
    return key


def remove(key: str, path: Optional[Path] = None) -> bool:
    names = load(path)
    if key not in names:
        return False
    del names[key]
    _save(names, path)
    register(path)
    return True


def uses(key: str, registry=None) -> list:
    """The gestures made with a named shape: [(kind, gesture key, name)], kind = composite | motion | sequence | path."""
    from manux.compat.hand_mouse.action_bindings import get_action_registry
    from manux.compat.hand_mouse.path_gesture import PathStore
    from manux.compat.hand_mouse.sequence import SequenceStore
    reg = registry or get_action_registry()
    out = [("composite", k, str(c.get("name") or k)) for k, c in reg.composite_gestures.items() if c.get("base_posture") == key]
    out += [("motion", k, str(m.get("name") or k)) for k, m in reg.custom_motions.items() if m.get("posture") == key]
    out += [("sequence", r["key"], r["name"]) for r in SequenceStore().rows() if key in r["steps"]]
    out += [("path", r["key"], r["name"]) for r in PathStore().rows() if r["posture"] == key]
    return out


def delete(key: str, registry=None, path: Optional[Path] = None) -> Dict[str, int]:
    """A named shape and everything that hangs on it (2026-11-11 request: "잘못 지은 손 모양 이름 지우는 화면"): the
    gestures made with it, its examples, its place in the taught model(s), its name. Returns what was removed."""
    from manux.compat.hand_mouse import posture_teach
    from manux.compat.hand_mouse.action_bindings import get_action_registry
    from manux.compat.hand_mouse.path_gesture import PathStore
    from manux.compat.hand_mouse.sequence import SequenceStore
    if not is_custom(key) or key not in load(path):
        return {}
    reg = registry or get_action_registry()
    made = uses(key, reg)
    for kind, gesture, _ in made:
        if kind == "composite":
            reg.delete_composite_gesture(gesture)
        elif kind == "motion":
            reg.delete_motion_gesture(gesture)
        elif kind == "sequence":
            SequenceStore().remove(gesture)
        else:
            PathStore().remove(gesture)
    examples = posture_teach.delete_examples(key)
    posture_teach.forget(key)
    remove(key, path)
    return {"gestures": len(made), "examples": examples}
