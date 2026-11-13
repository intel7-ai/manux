"""Gesture sequences: hand shapes done one after another run a command (2026-11-10).

"V 사인 다음 1초 안에 주먹이면 창 닫기" = steps ["v_sign", "fist"]. Each step is a hand shape held at least HOLD s
(src/pose_probe.hand_posture, from the landmarks alone, in every mode); the next step must come within GAP s. Doing
shapes in order on purpose is rare by accident, so a sequence suits commands that must not fire by mistake.

The command is an ordinary binding (key "seq_*", track "sequence"); the sequences themselves are in
config/sequences.json (name and steps). Created from the AI helper (src/llm_binding.py), listed in [설정].
"""
from __future__ import annotations

from manux.compat.hand_mouse.paths import DATA_ROOT

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = DATA_ROOT
DEFAULT_PATH = ROOT / "config" / "sequences.json"
HOLD = .25               # s a shape must be steady to count as a step (passing shapes while changing are shorter)
GAP = 1.0                # s at most from one step to the next
COOLDOWN = 1.0           # s after a sequence fired before another can
MIN_STEPS, MAX_STEPS = 2, 3
POSTURES = ["thumb_index", "thumb_middle", "thumb_ring", "thumb_pinky", "thumb_index_middle", "v_sign", "two_fingers", "v_sign_down",
            "rock_sign", "index_point", "l_sign", "pinky_up", "shaka", "thumbs_down",
            "thumbs_up", "fist", "fingers_bent", "open_hand", "open_hand_back", "open_hand_down", "open_hand_up", "knife_hand", "thumb_tucked"]     # + the shapes the user named (src/custom_postures.register)


def steps_text(steps: Sequence[str]) -> str:
    from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
    return " → ".join(POSTURE_NAMES_KO.get(s, s) for s in steps)


def check_steps(steps: Sequence[str]) -> Optional[str]:
    """A Korean problem with these steps, None when fine."""
    steps = list(steps)
    if not MIN_STEPS <= len(steps) <= MAX_STEPS:
        return f"이어 하기는 {MIN_STEPS}~{MAX_STEPS}단계예요"
    bad = [s for s in steps if s not in POSTURES]
    if bad:
        return f"모르는 손 모양: {', '.join(bad)}"
    if any(a == b for a, b in zip(steps, steps[1:])):
        return "같은 손 모양을 연달아 쓸 수는 없어요 (바뀌는 순간을 알아봐요)"
    return None


class SequenceStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.items: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        items = data.get("sequences") if isinstance(data, dict) else None
        self.items = {k: v for k, v in (items or {}).items()
                      if isinstance(v, dict) and check_steps(v.get("steps") or []) is None}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"sequences": self.items}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def conflict(self, steps: Sequence[str]) -> Optional[str]:
        """An existing sequence that is the same, or one is the start of the other (the shorter would always win)."""
        steps = list(steps)
        for v in self.items.values():
            other = list(v["steps"])
            n = min(len(other), len(steps))
            if other[:n] == steps[:n]:
                return v["name"]
        return None

    def add(self, name: str, steps: Sequence[str]) -> str:
        problem = check_steps(steps)
        if problem:
            raise ValueError(problem)
        clash = self.conflict(steps)
        if clash:
            raise ValueError(f"이미 있는 이어 하기와 겹쳐요: {clash}")
        name = str(name or "").strip()[:30] or steps_text(steps)
        base = "seq_" + "_".join(re.sub(r"[^a-z0-9]+", "", s) for s in steps)
        key, i = base, 2
        while key in self.items:
            key, i = f"{base}_{i}", i + 1
        self.items[key] = {"name": name, "steps": list(steps), "enabled": True}
        self.save()
        return key

    def remove(self, key: str) -> bool:
        if key in self.items:
            del self.items[key]
            self.save()
            return True
        return False

    def set_enabled(self, key: str, on: bool) -> None:
        if key in self.items:
            self.items[key]["enabled"] = bool(on)
            self.save()

    def set_motion_only(self, key: str, on: bool) -> bool:
        """Only while motion mode is on (2026-11-11 request). Off = in every mode, as before."""
        if key not in self.items:
            return False
        if on:
            self.items[key]["motion_only"] = True
        else:
            self.items[key].pop("motion_only", None)
        self.save()
        return True

    def rows(self) -> List[Dict[str, Any]]:
        return [{"key": k, "name": v["name"], "steps": list(v["steps"]), "steps_text": steps_text(v["steps"]),
                 "enabled": bool(v.get("enabled", True)), "motion_only": bool(v.get("motion_only"))}
                for k, v in self.items.items()]

    def active(self, motion: Optional[bool] = None) -> Dict[str, Tuple[str, ...]]:
        """`motion`: is motion mode on? False leaves out the "motion mode only" ones; None = all of them."""
        return {k: tuple(v["steps"]) for k, v in self.items.items()
                if v.get("enabled", True) and (motion is not False or not v.get("motion_only"))}


class SequenceDetector:
    """Feed the hand shape every frame; returns a sequence's key the frame its last step counts."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.current = ""
        self.since = 0.0
        self.counted = False
        self.steps: List[List[Any]] = []              # [shape, when it counted, when it was last seen]
        self.cooldown_until = float("-inf")

    def step(self, t: float, posture: Optional[str], sequences: Dict[str, Tuple[str, ...]]) -> Optional[str]:
        from manux.compat.hand_mouse.pose_probe import unless_bound
        # the back of an open hand counts as the open hand unless a sequence asks for it
        posture = unless_bound(posture or "", {s for seq in sequences.values() for s in seq})
        if posture != self.current:
            self.current, self.since, self.counted = posture, t, False
        if self.counted:
            if self.steps and self.steps[-1][0] == posture:
                self.steps[-1][2] = t                 # still holding the step: the gap runs from when it is let go
            return None
        if not posture or t - self.since < HOLD:
            return None
        self.counted = True
        recent = [s for s in self.steps if t - s[2] <= GAP + HOLD]
        self.steps = recent[-(MAX_STEPS - 1):] + [[posture, t, t]]
        if t < self.cooldown_until:
            return None
        for key, seq in sequences.items():
            n = len(seq)
            tail = self.steps[-n:]
            if len(tail) == n and tuple(s[0] for s in tail) == seq                     and all(b[1] - a[2] <= GAP + HOLD for a, b in zip(tail, tail[1:])):
                self.steps.clear()
                self.cooldown_until = t + COOLDOWN
                return key
        return None

    def lost(self, t: float) -> None:
        """No hand: the shape being held ends (the steps already done stay for the GAP)."""
        self.current, self.counted = "", False
