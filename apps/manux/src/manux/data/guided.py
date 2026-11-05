"""Guided recording: the app says what to do, so every frame gets a clean label.

Ported from Dan's `hand-mouse-gesture-overhaul@7448e4a` `src/guided_recording.py`. The difference:
the gestures to record are **data** (`GestureSpec`s passed in), not a fixed list of three. Dan's
three are kept as `DEFAULT_SPECS`; any declared gesture can be recorded the same way.

For each posture in `conditions` and each spec, one set:
- a repeated spec: `reps` x ("get ready" `prep` s -> label `transition`, "do it" `do` s -> label
  = the spec's key, "rest" `rest` s -> label `none`);
- a free spec (`free=True`, e.g. moving the cursor with a half fist): one stretch of
  `free_seconds` s, every frame labelled with the spec's key.
Before each set the session waits for "start" (`advance`); those frames are labelled `idle`.
`transition`, `idle` (and the recorder's `no_hand`) are never training classes.

The label of a frame (`label(now)`) is what the recorder writes:
`{"target", "task", "phase", "rep", "condition", "condition_index", "set"}` plus `"gesture"` (the
gesture id the class stands for, e.g. `pinch.thumb_ring`) when the spec declares one. Training
reads classes from `target` and the class -> gesture id mapping from `gesture`.

Pure logic (no clock of its own, no Qt): `advance(now)`, `update(now)`, `label(now)`, `view(now)`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from manux.core.contracts import GESTURE_ID_RE

DEFAULTS: Dict[str, float] = {
    "reps": 15.0,           # repetitions per set
    "prep": 1.0,            # s "get ready"
    "do": 1.6,              # s "do it"
    "rest": 1.6,            # s "rest"
    "free_seconds": 180.0,  # s of a free stretch per posture
}
V03_KEYS = {"reps": "rec_reps", "prep": "rec_prep", "do": "rec_do", "rest": "rec_rest", "free_seconds": "rec_free_seconds"}

DEFAULT_CONDITIONS: Tuple[str, ...] = (
    "평소 자세",
    "몸을 뒤로 기대거나 옆으로 돌린 자세",
    "폰 높이나 위치를 바꾼 자세",
)

RESERVED = ("transition", "idle", "no_hand")   # labels that are never a class
NONE = "none"                                   # the "no gesture" class (rest frames)
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class GestureSpec:
    key: str                       # the class label of its "do it" (or free) frames
    name: str                      # on screen
    do: str                        # what to do, on screen
    free: bool = False             # one long stretch instead of repetitions
    gesture: Optional[str] = None  # the gesture id this class stands for (e.g. "pinch.thumb_ring")

    def __post_init__(self) -> None:
        if not KEY_RE.match(self.key) or self.key in RESERVED:
            raise ValueError(f"spec key must be a lowercase name other than {RESERVED}, got {self.key!r}")
        if self.gesture is not None and not GESTURE_ID_RE.match(self.gesture):
            raise ValueError(f"gesture must be a gesture id '<feature>.<name>', got {self.gesture!r}")
        if self.key == NONE and self.gesture is not None:
            raise ValueError("the 'none' class stands for no gesture")
        if self.key == NONE and not self.free:
            raise ValueError("'none' is recorded as a free stretch (its rest frames come from the other sets)")

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"key": self.key, "name": self.name, "free": self.free}
        if self.gesture:
            out["gesture"] = self.gesture
        return out


# Dan's pilot set (2026-11-05): the gestures that misfired most in the 11/04 logs.
# Gesture ids follow the v0.4 `pinch` feature names (contracts section 6 example).
DEFAULT_SPECS: Tuple[GestureSpec, ...] = (
    GestureSpec("thumb_ring", "엄지+약지 (우클릭)", "엄지를 약지 끝에 붙였다 떼세요", gesture="pinch.thumb_ring"),
    GestureSpec("thumb_index", "엄지+검지 (스크롤 잡기)", "엄지와 검지로 집어서 위나 아래로 조금 끌고 놓으세요",
                gesture="pinch.scroll"),
    GestureSpec(NONE, "반주먹 이동 (아무 동작 아님)", "반주먹으로 커서를 이리저리 움직이고, 가끔 손을 폈다 다시 쥐세요", free=True),
)


def check_specs(specs: Sequence[GestureSpec]) -> Tuple[GestureSpec, ...]:
    specs = tuple(specs)
    if not specs:
        raise ValueError("at least one gesture spec is needed")
    keys = [s.key for s in specs]
    if len(set(keys)) != len(keys):
        raise ValueError(f"spec keys must be unique, got {keys}")
    gestures = [s.gesture for s in specs if s.gesture]
    if len(set(gestures)) != len(gestures):
        raise ValueError(f"two specs stand for the same gesture id: {gestures}")
    return specs


def specs_for(focus: str = "", specs: Sequence[GestureSpec] = DEFAULT_SPECS) -> Tuple[GestureSpec, ...]:
    """All specs, or only the one the training center asked to record more of ("focused recording")."""
    picked = tuple(s for s in specs if s.key == focus)
    return picked or tuple(specs)


class GuidedSession:
    def __init__(self, specs: Sequence[GestureSpec] = DEFAULT_SPECS,
                 conditions: Sequence[str] = DEFAULT_CONDITIONS,
                 config: Optional[Mapping[str, Any]] = None) -> None:
        self.specs = check_specs(specs)
        self.conditions = tuple(conditions)
        if not self.conditions:
            raise ValueError("at least one posture (condition) is needed")
        self.config: Dict[str, float] = dict(DEFAULTS)
        for key, value in (config or {}).items():
            short = next((k for k, v in V03_KEYS.items() if v == key), key)   # v0.3 names accepted
            if short in DEFAULTS:
                self.config[short] = float(value)
        if self.config["reps"] < 1 or min(self.config[k] for k in ("prep", "do", "rest", "free_seconds")) <= 0:
            raise ValueError(f"reps must be >= 1 and durations > 0, got {self.config}")
        self.sets: List[Tuple[int, GestureSpec]] = [(c, s) for c in range(len(self.conditions)) for s in self.specs]
        self.index = 0                 # current set
        self.running = False           # False = waiting for "start"
        self._t0 = 0.0
        self.done = False

    def describe(self) -> Dict[str, Any]:
        """What this session asks for, for the recording's meta."""
        return {"specs": [s.as_dict() for s in self.specs], "conditions": list(self.conditions),
                "config": dict(self.config)}

    @property
    def reps(self) -> int:
        return int(self.config["reps"])

    @property
    def current(self) -> Optional[Tuple[int, GestureSpec]]:
        return self.sets[self.index] if self.index < len(self.sets) else None

    def advance(self, now: float) -> None:
        """Start the waiting set (the user pressed start)."""
        if not self.done and not self.running:
            self.running = True
            self._t0 = now

    def _cycle(self) -> float:
        return self.config["prep"] + self.config["do"] + self.config["rest"]

    def set_length(self, spec: GestureSpec) -> float:
        return self.config["free_seconds"] if spec.free else self.reps * self._cycle()

    def update(self, now: float) -> bool:
        """Move on when the running set is over. True when a set just finished."""
        cur = self.current
        if cur is None or not self.running:
            return False
        if now - self._t0 >= self.set_length(cur[1]):
            self.running = False
            self.index += 1
            self.done = self.index >= len(self.sets)
            return True
        return False

    def _step(self, now: float) -> Tuple[str, int, float]:
        """(phase, repetition 1.., progress of the set 0..1)."""
        _, spec = self.current  # type: ignore[misc]
        elapsed = now - self._t0
        total = self.set_length(spec)
        if spec.free:
            return "free", 0, min(1.0, elapsed / total)
        cycle = self._cycle()
        rep = min(int(elapsed // cycle), self.reps - 1)
        inner = elapsed - rep * cycle
        prep, do = self.config["prep"], self.config["do"]
        phase = "prep" if inner < prep else "do" if inner < prep + do else "rest"
        return phase, rep + 1, min(1.0, elapsed / total)

    def label(self, now: float) -> Dict[str, Any]:
        cur = self.current
        if cur is None or not self.running:
            return {"target": "idle", "phase": "waiting"}
        cond, spec = cur
        phase, rep, _ = self._step(now)
        target = {"prep": "transition", "do": spec.key, "rest": NONE, "free": spec.key}[phase]
        out: Dict[str, Any] = {"target": target, "task": spec.key, "phase": phase, "rep": rep,
                               "condition": self.conditions[cond], "condition_index": cond, "set": self.index}
        if spec.gesture and target == spec.key:
            out["gesture"] = spec.gesture
        return out

    def view(self, now: float) -> Dict[str, Any]:
        """Big prompt, smaller line, progress of the set, progress of everything, waiting for start?"""
        overall = self.index / len(self.sets)
        if self.done:
            return {"prompt": "녹화 끝! 수고하셨어요", "sub": "", "progress": 1.0, "overall": 1.0, "waiting": False}
        cond, spec = self.current  # type: ignore[misc]
        head = f"[{self.index + 1}/{len(self.sets)}] {self.conditions[cond]} · {spec.name}"
        if not self.running:
            return {"prompt": f"{self.conditions[cond]}로 앉은 뒤 [시작]을 누르세요", "sub": head,
                    "progress": 0.0, "overall": overall, "waiting": True}
        phase, rep, progress = self._step(now)
        if phase == "free":
            prompt = spec.do
            sub = f"{head} · 남은 시간 {max(0, int(self.set_length(spec) - (now - self._t0)))}초"
        else:
            prompt = {"prep": "준비…", "do": spec.do, "rest": "쉬세요 (손을 편하게)"}[phase]
            sub = f"{head} · {rep}/{self.reps}회"
        return {"prompt": prompt, "sub": sub, "progress": progress, "overall": overall + progress / len(self.sets),
                "waiting": False, "phase": phase}
