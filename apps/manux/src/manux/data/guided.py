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

The specs normally come from the one catalog (`manux.data.catalog`); the conditions either from a
plain list of posture names (old recordings) or from a capture plan (`manux.data.capture_plan`,
`plan=`), whose enabled rows give each condition its own physical setup and timing.

The label of a frame (`label(now)`) is what the recorder writes:
`{"target", "task", "phase", "rep", "condition", "condition_index", "set"}` plus `"gesture"` (the
gesture id the class stands for, e.g. `pinch.thumb_ring`) when the spec declares one, and
`"condition_key"` (the physical-condition hash of the plan row) when a plan is used.
`target` is the spec's class (`spec.label`, which several tasks may share); `task` is the spec's key.
Training reads classes from `target`, the class -> gesture id mapping from `gesture`, and the
held-out condition group from `condition_key` (`legacy-<condition_index>` for older recordings).

Pure logic (no clock of its own, no Qt): `advance(now)`, `update(now)`, `label(now)`, `view(now)`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from manux.data import capture_plan as plans
from manux.data.catalog import BY_KEY, KEY_RE, NONE, PILOT_KEYS, RESERVED, GestureSpec, check_tasks

__all__ = ["DEFAULTS", "V03_KEYS", "DEFAULT_CONDITIONS", "RESERVED", "NONE", "KEY_RE", "GestureSpec",
           "DEFAULT_SPECS", "check_specs", "specs_for", "GuidedSession"]

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


# Dan's pilot set (2026-11-05): the gestures that misfired most in the 11/04 logs. From the catalog.
DEFAULT_SPECS: Tuple[GestureSpec, ...] = tuple(BY_KEY[k] for k in PILOT_KEYS)


def check_specs(specs: Sequence[GestureSpec]) -> Tuple[GestureSpec, ...]:
    specs = tuple(specs)
    if not specs:
        raise ValueError("at least one gesture spec is needed")
    return check_tasks(specs)


def specs_for(focus: str = "", specs: Sequence[GestureSpec] = DEFAULT_SPECS) -> Tuple[GestureSpec, ...]:
    """All specs, or only the one the training center asked to record more of ("focused recording")."""
    picked = tuple(s for s in specs if s.key == focus)
    return picked or tuple(specs)


class GuidedSession:
    """Sets = conditions x specs. With `plan` (a `manux-capture-plan/1`), the conditions are its enabled
    rows, each with its own timing, and every label carries `condition_key`.

    `settle` (s, default 0): the first part of each "rest" is still `transition` (the hand on its way
    back). A spec's `label_delay` does the same at the start of "do"; `min_hold` lengthens "do".
    `update(now, available=False)` (no usable 3D hand) or `paused = True` stops the clock; those frames
    are labelled `idle` / phase `paused` and never become training data."""

    def __init__(self, specs: Sequence[GestureSpec] = DEFAULT_SPECS,
                 conditions: Sequence[str] = DEFAULT_CONDITIONS,
                 config: Optional[Mapping[str, Any]] = None, *,
                 plan: Optional[Mapping[str, Any]] = None, settle: float = 0.0) -> None:
        self.specs = check_specs(specs)
        self.plan = plans.normalise(plan) if plan is not None else None
        self.rows: List[Dict[str, Any]] = plans.enabled(self.plan) if self.plan else []
        self.keys: Tuple[str, ...] = tuple(plans.condition_key(r) for r in self.rows)
        self.conditions = tuple(r["name"] for r in self.rows) if self.rows else tuple(conditions)
        if not self.conditions:
            raise ValueError("at least one posture (condition) is needed")
        self.config: Dict[str, float] = dict(DEFAULTS)
        for key, value in (config or {}).items():
            short = next((k for k, v in V03_KEYS.items() if v == key), key)   # v0.3 names accepted
            if short in DEFAULTS:
                self.config[short] = float(value)
        if self.config["reps"] < 1 or min(self.config[k] for k in ("prep", "do", "rest", "free_seconds")) <= 0:
            raise ValueError(f"reps must be >= 1 and durations > 0, got {self.config}")
        if settle < 0:
            raise ValueError("settle must be >= 0")
        self.settle = float(settle)
        self.sets: List[Tuple[int, GestureSpec]] = [(c, s) for c in range(len(self.conditions)) for s in self.specs]
        self.index = 0                 # current set
        self.running = False           # False = waiting for "start"
        self._t0 = 0.0
        self.done = False
        self.paused = False            # set by the screen ("pause" button)
        self.hold_reason = ""
        self._last_update: Optional[float] = None
        self._held = False

    def describe(self) -> Dict[str, Any]:
        """What this session asks for, for the recording's meta."""
        out: Dict[str, Any] = {"specs": [s.as_dict() for s in self.specs], "conditions": list(self.conditions),
                               "config": dict(self.config)}
        if self.plan:
            out["capture_plan"] = self.plan
            out["condition_keys"] = list(self.keys)
        return out

    def timing(self, cond: Optional[int] = None) -> Dict[str, float]:
        """reps / prep / do / rest / free_seconds of a condition (the current one by default)."""
        if not self.rows:
            return dict(self.config)
        if cond is None:
            cond = self.current[0] if self.current else 0
        r = self.rows[cond]
        return {"reps": float(r["reps"]), "prep": float(r["prep"]), "do": float(r["hold"]),
                "rest": float(r["rest"]), "free_seconds": float(r["free_seconds"])}

    @property
    def reps(self) -> int:
        return int(self.timing()["reps"])

    @property
    def current(self) -> Optional[Tuple[int, GestureSpec]]:
        return self.sets[self.index] if self.index < len(self.sets) else None

    def advance(self, now: float) -> None:
        """Start the waiting set (the user pressed start)."""
        if not self.done and not self.running:
            self.running = True
            self._t0 = now
            self._last_update = now
            self._held = False

    def phases(self, spec: GestureSpec, cond: Optional[int] = None) -> Tuple[float, float, float]:
        """(prep, do, rest) seconds of one repetition of `spec`."""
        t = self.timing(cond)
        return t["prep"], max(t["do"], spec.min_hold), t["rest"]

    def set_length(self, spec: GestureSpec, cond: Optional[int] = None) -> float:
        t = self.timing(cond)
        return t["free_seconds"] if spec.free else t["reps"] * sum(self.phases(spec, cond))

    def update(self, now: float, available: bool = True) -> bool:
        """Move on when the running set is over. True when a set just finished.

        While paused or not `available`, the set's clock stands still."""
        cur = self.current
        if cur is None or not self.running:
            return False
        held = self.paused or not available
        if held or self._held:
            self._t0 += max(0.0, now - (self._last_update if self._last_update is not None else now))
        self._last_update = now
        self._held = held
        self.hold_reason = ("일시정지 · 계속을 눌러주세요" if self.paused
                            else "손의 3D 관절을 찾는 중 · 타이머가 멈췄어요" if not available else "")
        if held:
            return False
        if now - self._t0 >= self.set_length(cur[1]):
            self.running = False
            self.index += 1
            self.done = self.index >= len(self.sets)
            return True
        return False

    def _step(self, now: float) -> Tuple[str, int, float, float]:
        """(phase, repetition 1.., progress of the set 0..1, seconds into the phase)."""
        _, spec = self.current  # type: ignore[misc]
        elapsed = now - self._t0
        total = self.set_length(spec)
        progress = min(1.0, elapsed / total)
        if spec.free:
            return "free", 0, progress, elapsed
        prep, do, rest = self.phases(spec)
        cycle = prep + do + rest
        rep = min(int(elapsed // cycle), self.reps - 1)
        inner = elapsed - rep * cycle
        if inner < prep:
            return "prep", rep + 1, progress, inner
        if inner < prep + do:
            return "do", rep + 1, progress, inner - prep
        return "rest", rep + 1, progress, inner - prep - do

    def label(self, now: float) -> Dict[str, Any]:
        cur = self.current
        if cur is None or not self.running:
            return {"target": "idle", "phase": "waiting"}
        cond, spec = cur
        phase, rep, _, into = self._step(now)
        target = {"prep": "transition", "do": spec.label, "rest": NONE, "free": spec.label}[phase]
        if (phase == "do" and into < spec.label_delay) or (phase == "rest" and into < self.settle):
            target = "transition"
        if self._held:
            target, phase = "idle", "paused"
        out: Dict[str, Any] = {"target": target, "task": spec.key, "phase": phase, "rep": rep,
                               "condition": self.conditions[cond], "condition_index": cond, "set": self.index}
        if self.rows:
            out["condition_key"] = self.keys[cond]
        if spec.gesture and target == spec.label:
            out["gesture"] = spec.gesture
        return out

    def view(self, now: float) -> Dict[str, Any]:
        """Big prompt, smaller line, progress of the set, progress of everything, waiting for start?"""
        overall = self.index / len(self.sets)
        if self.done:
            return {"prompt": "녹화 끝! 수고하셨어요", "sub": "", "progress": 1.0, "overall": 1.0, "waiting": False}
        cond, spec = self.current  # type: ignore[misc]
        head = f"[{self.index + 1}/{len(self.sets)}] {self.conditions[cond]} · {spec.name}"
        extra: Dict[str, Any] = {"condition_detail": plans.description(self.rows[cond]) if self.rows else "",
                                 "position": self.rows[cond]["position"] if self.rows else "자유 이동",
                                 "paused": self.paused}
        if not self.running:
            wait = (f"{self.conditions[cond]} 준비 후 [시작]을 누르세요" if self.rows
                    else f"{self.conditions[cond]}로 앉은 뒤 [시작]을 누르세요")
            return {"prompt": wait, "sub": head, "progress": 0.0, "overall": overall, "waiting": True, **extra}
        phase, rep, progress, _ = self._step(now)
        if phase == "free":
            prompt = spec.do
            sub = f"{head} · 남은 시간 {max(0, int(self.set_length(spec) - (now - self._t0)))}초"
        else:
            rest = "쉬세요 · 시작 위치로 천천히 돌아오세요" if spec.direction else "쉬세요 (손을 편하게)"
            prompt = {"prep": "준비…", "do": spec.do, "rest": rest}[phase]
            sub = f"{head} · {rep}/{self.reps}회"
        return {"prompt": self.hold_reason or prompt, "sub": sub, "progress": progress,
                "overall": overall + progress / len(self.sets), "waiting": False, "phase": phase, **extra}
