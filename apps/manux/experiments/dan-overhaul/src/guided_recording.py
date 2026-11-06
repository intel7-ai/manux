"""
Guided recording (2026-11-05): the app tells the user what to do, so every frame gets a clean label.

First pilot set - the gestures that misfired most in the 2026-11-04 logs:
- thumb + ring (right click) and thumb + index (scroll grab): repeated `reps` times per set as
  "get ready" (label "transition", not used for training) -> "do it" (label = the gesture) -> "rest" (label "none");
- moving the cursor with a half fist and opening the hand now and then: one long stretch labelled "none" - the
  scenes those two misfired in.
Each set is done in every posture of `CONDITIONS` (camera angle / seated position), posture by posture, so the model
sees several angles. Before each set the session waits for the user ("start"), so they can settle into the posture.

Pure logic: `advance(now)` starts the next set, `update(now)` moves through the steps, `label(now)` is the label of
the current frame, `view(now)` what the screen shows.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from src.recording_plan import normalise, condition_key, description
from src.gesture_catalog import Task, TASKS, tasks_for

DEFAULTS: Dict[str, float] = {
    "rec_reps": 15.0,          # repetitions per set
    "rec_prep": 1.0,           # s "get ready"
    "rec_do": 1.6,             # s "do it"
    "rec_rest": 1.6,           # s "rest"
    "rec_free_seconds": 180.0,  # s of a free (unlabelled-gesture) stretch per posture
}

CONDITIONS = (
    "평소 자세",
    "몸을 뒤로 기대거나 옆으로 돌린 자세",
    "폰 높이나 위치를 바꾼 자세",
)


class GuidedSession:
    def __init__(self, config: Optional[Dict[str, Any]] = None, tasks=TASKS, conditions=CONDITIONS, plan=None) -> None:
        self.config: Dict[str, float] = dict(DEFAULTS)
        if config:
            self.config.update({k: float(v) for k, v in config.items() if k in DEFAULTS})
        self.plan = normalise(plan) if plan is not None else None
        self.rows = [r for r in self.plan["conditions"] if r["enabled"]] if self.plan else []
        if self.rows:
            conditions = tuple(r["name"] for r in self.rows)
        self.sets: List[Tuple[int, Task]] = [(c, t) for c in range(len(conditions)) for t in tasks]
        self.conditions = conditions
        self.index = 0                 # current set
        self.running = False           # False = waiting for "start"
        self._t0 = 0.0
        self.done = False
        self.paused = False
        self.hold_reason = ""
        self._last_update = None
        self._held = False

    def cfg(self, key: str) -> float:
        names = {"rec_reps": "reps", "rec_prep": "prep", "rec_do": "hold", "rec_rest": "rest", "rec_free_seconds": "free_seconds"}
        if self.rows and self.current is not None:
            return float(self.rows[self.current[0]][names[key]])
        return float(self.config[key])

    @property
    def current(self) -> Optional[Tuple[int, Task]]:
        return self.sets[self.index] if self.index < len(self.sets) else None

    def advance(self, now: float) -> None:
        """Start the waiting set (the user pressed start)."""
        if not self.done and not self.running:
            self.running = True
            self._t0 = now
            self._last_update = now
            self._held = False

    def _set_length(self, task: Task) -> float:
        if task.free:
            return self.cfg("rec_free_seconds")
        return self.cfg("rec_reps") * (self.cfg("rec_prep") + self.cfg("rec_do") + self.cfg("rec_rest"))

    def update(self, now: float, available=True) -> bool:
        """Move on when the running set is over. True when a set just finished."""
        cur = self.current
        if cur is None or not self.running:
            return False
        held = self.paused or not available
        if held or self._held:
            self._t0 += max(0.0, now - (self._last_update if self._last_update is not None else now))
        self._last_update = now
        self._held = held
        self.hold_reason = "일시정지 · 계속을 눌러주세요" if self.paused else "손의 3D 관절을 찾는 중 · 타이머가 멈췄어요" if not available else ""
        if held:
            return False
        if now - self._t0 >= self._set_length(cur[1]):
            self.running = False
            self.index += 1
            self.done = self.index >= len(self.sets)
            return True
        return False

    def _step(self, now: float) -> Tuple[str, int, float]:
        """(phase, repetition 1.., progress of the set 0..1)."""
        cond, task = self.current
        elapsed = now - self._t0
        total = self._set_length(task)
        if task.free:
            return "free", 0, min(1.0, elapsed / total)
        cycle = self.cfg("rec_prep") + self.cfg("rec_do") + self.cfg("rec_rest")
        rep = int(elapsed // cycle)
        inner = elapsed - rep * cycle
        phase = "prep" if inner < self.cfg("rec_prep") else "do" if inner < self.cfg("rec_prep") + self.cfg("rec_do") else "rest"
        return phase, rep + 1, min(1.0, elapsed / total)

    def label(self, now: float) -> Dict[str, Any]:
        cur = self.current
        if cur is None or not self.running:
            return {"target": "idle", "phase": "waiting"}
        cond, task = cur
        phase, rep, _ = self._step(now)
        target = {"prep": "transition", "do": task.label, "rest": "none", "free": task.label}[phase]
        label = {"target": "idle" if self._held else target, "task": task.key, "phase": "paused" if self._held else phase,
                 "rep": rep, "condition": self.conditions[cond], "condition_index": cond, "set": self.index}
        if self.rows:
            label["condition_key"] = condition_key(self.rows[cond])
            pos = self.rows[cond].get("position")
            if pos:
                label["position"] = str(pos)
        return label

    def view(self, now: float) -> Dict[str, Any]:
        """Big prompt, smaller line, progress of the set, progress of everything, waiting for start?"""
        overall = self.index / len(self.sets) if self.sets else 1.0
        if self.done:
            return {"prompt": "녹화 끝! 수고하셨어요", "sub": "", "progress": 1.0, "overall": 1.0, "waiting": False}
        cond, task = self.current
        head = f"[{self.index + 1}/{len(self.sets)}] {self.conditions[cond]} · {task.name}"
        details = description(self.rows[cond]) if self.rows else ""
        extra = {"condition_detail": details, "paused": self.paused,
                 "position": self.rows[cond]["position"] if self.rows else "자유 이동"}
        if not self.running:
            return {"prompt": f"{self.conditions[cond]} 준비 후 [시작]을 누르세요", "sub": head,
                    "progress": 0.0, "overall": overall, "waiting": True, **extra}
        phase, rep, progress = self._step(now)
        if phase == "free":
            prompt = task.do
            sub = f"{head} · 남은 시간 {max(0, int(self._set_length(task) - (now - self._t0)))}초"
        else:
            prompt = {"prep": "준비… " + task.do, "do": task.do,
                      "rest": "쉬세요 · 시작 위치로 천천히 돌아오세요" if task.direction else "쉬세요 (손을 편하게)"}[phase]
            sub = f"{head} · {rep}/{int(self.cfg('rec_reps'))}회"
        return {"prompt": self.hold_reason or prompt, "sub": sub, "progress": progress,
                "overall": overall + progress / len(self.sets), "waiting": False, "phase": phase, **extra}
