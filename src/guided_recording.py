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
from src.recording_plan import normalise, condition_key, description, orientation_guide, distance_label
from src.gesture_catalog import Task, TASKS, tasks_for

DEFAULTS: Dict[str, float] = {
    "rec_reps": 1.0,           # continuous hold by default (or >1 when repeating)
    "rec_prep": 0.0,           # s "get ready" (0 = no prep interruption)
    "rec_do": 15.0,            # s "do it" (continuous gesture hold)
    "rec_rest": 0.0,           # s "rest" (0 = no rest interruption)
    "rec_free_seconds": 180.0,  # s of a free (unlabelled-gesture) stretch per posture
    # Recording alone, far from the screen: after [start] the set waits this long (the countdown is on screen) so you can
    # walk back to your place; 0 = start at once.
    "rec_start_delay": 0.0,
    # > 0: when a set ends the next one starts by itself after this many seconds (you stay in place; 0 = wait for [start]).
    "rec_auto_next": 0.0,
    # > 0: when the last set is over the recording is saved by itself after this many seconds (no trip back to press
    # "녹화 끝 (저장)"); 0 = wait for the button.
    "rec_auto_save": 0.0,
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
            self._t0 = now + max(0.0, float(self.config.get("rec_start_delay", 0.0)))   # the countdown is before _t0
            self._last_update = now
            self._held = False

    def countdown(self, now: float) -> float:
        """Seconds until the running set really starts (0 when it has started or nothing runs)."""
        return max(0.0, self._t0 - now) if (self.running and self.current is not None) else 0.0

    def _set_length(self, task: Task) -> float:
        if task.free:
            return self.cfg("rec_free_seconds")
        return self.cfg("rec_reps") * (self.cfg("rec_prep") + self.cfg("rec_do") + self.cfg("rec_rest"))

    def update(self, now: float, available=True) -> bool:
        """Move on when the running set is over. True when a set just finished."""
        cur = self.current
        if cur is None or not self.running:
            return False
        # While counting down the person is walking back to their place: the hand is not expected to be visible yet
        held = self.paused or (not available and now >= self._t0)
        if held or self._held:
            self._t0 += max(0.0, now - (self._last_update if self._last_update is not None else now))
        self._last_update = now
        self._held = held
        self.hold_reason = "일시정지 · 스페이스바 또는 [계속]을 눌러주세요" if self.paused else "손의 3D 관절을 찾는 중 · 타이머가 멈췄어요" if not available else ""
        if held:
            return False
        if now - self._t0 >= self._set_length(cur[1]):
            self.running = False
            self.index += 1
            self.done = self.index >= len(self.sets)
            auto = float(self.config.get("rec_auto_next", 0.0))
            if auto > 0 and not self.done:
                self.running = True                 # the next set starts by itself after the countdown
                self._t0 = now + auto
                self._last_update = now
                self._held = False
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
        if now < self._t0:                       # countdown before the set: not training data
            return {"target": "idle", "phase": "waiting"}
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
            return {"prompt": "녹화 끝! 수고하셨어요", "sub": "", "progress": 1.0, "overall": 1.0, "waiting": False,
                    "finished": True, "auto_save": max(0.0, float(self.config.get("rec_auto_save", 0.0)))}
        cond, task = self.current
        head = f"[{self.index + 1}/{len(self.sets)}] {self.conditions[cond]} · {task.name}"
        details = description(self.rows[cond], focus=task.key) if self.rows else ""
        extra = {"condition_detail": details, "paused": self.paused,
                 "position": self.rows[cond]["position"] if self.rows else "자유 이동"}
        if not self.running:
            orient = self.rows[cond].get("orientation", "") if self.rows else ""
            guide = orientation_guide(orient, focus=task.key)
            guide_txt = f" [{guide}]" if guide else ""
            ready_suffix = "모션 시작 위치에 손을 준비한 뒤 스페이스바(또는 [시작])를 누르세요" if task.track == "motion" else "자세를 잡은 뒤 스페이스바(또는 [시작])를 누르세요"
            return {"prompt": f"{self.conditions[cond]}{guide_txt} · {ready_suffix}", "sub": head,
                    "progress": 0.0, "overall": overall, "waiting": True, **extra}
        if now < self._t0:
            left = int(self._t0 - now) + 1
            where = str(self.rows[cond].get("distance", "")) if self.rows else ""
            spot = f"{distance_label(where)} 자리로 가세요" if where and where != "직접 지정" else "자리를 잡으세요"
            return {"prompt": f"{left}초 뒤 시작 · {spot}", "sub": head, "progress": 0.0, "overall": overall,
                    "waiting": False, "phase": "prep", "big": str(left), "big_kind": "countdown", **extra}
        phase, rep, progress = self._step(now)
        if phase == "free":
            prompt = task.do
            sub = f"{head} · 남은 시간 {max(0, int(self._set_length(task) - (now - self._t0)))}초"
        elif self.cfg("rec_prep") == 0 and self.cfg("rec_rest") == 0 and int(self.cfg("rec_reps")) == 1:
            prompt = task.do
            sub = f"{head} · 남은 시간 {max(0, int(self._set_length(task) - (now - self._t0)))}초"
        else:
            if task.track == "motion" or task.direction:
                prompt = {
                    "prep": f"준비 ({rep}/{int(self.cfg('rec_reps'))}회) · 시작 위치에 손을 두세요 ({task.name})",
                    "do": f"지금 쓸기! · {task.do}",
                    "rest": "쉬세요 · 다음 횟수를 위해 시작 위치로 천천히 돌아오세요 (복귀 구간은 학습 제외)",
                }[phase]
            else:
                prompt = {"prep": "준비… " + task.do, "do": task.do,
                          "rest": "쉬세요 (손을 편하게)"}[phase]
            sub = f"{head} · {rep}/{int(self.cfg('rec_reps'))}회"
        # What to read from 3 m away: one big word (or the countdown number), coloured by what to do now
        if self._held:
            big, kind = "멈춤", "hold"              # the timer waits (hand lost or paused)
        else:
            big, kind = {"prep": ("준비", "prep"), "do": ("지금!" if int(self.cfg("rec_reps")) > 1 or self.cfg("rec_rest") > 0 else "유지", "do"),
                         "rest": ("쉬세요", "rest"), "free": ("진행 중", "do")}[phase]
        return {"prompt": self.hold_reason or prompt, "sub": sub, "progress": progress,
                "overall": overall + progress / len(self.sets), "waiting": False, "phase": phase,
                "big": big, "big_kind": kind, **extra}
