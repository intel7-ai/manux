"""Practice-only onboarding driven by engine actions, never by QML gesture rules."""
from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

from manux.ui.cards import MOTION, Card

MOVE_SECONDS = 1.0
ADVANCE_AFTER = 1.2
ATTEMPT_SECONDS = 15.0
MAX_FAILURES = 3
TARGET_RADIUS = .09
TARGETS = ((.25, .35), (.75, .35), (.5, .72))


def onboarding_steps(cards: Sequence[Card]) -> list[Card]:
    """Teach everyday controls first; keep the full catalog on the main screen."""
    by_key = {str(c["key"]): c for c in cards}
    result = [{"key": "camera", "title": "1. 카메라와 조작 손 확인",
               "how": "화면의 관절 21점이 손을 따라오는지 확인하세요. 손을 잠깐 보여주면 다음으로 진행합니다.",
               "actions": ()}]
    if "click" in by_key:
        result.append({"key": "sensitivity", "title": "2. 내 손에 맞는 속도",
                       "how": "편 손은 기본 속도, 주먹은 빠르게 가속해 움직입니다. 속도를 조절하고 화면의 목표 3개를 손짓으로 클릭하세요. 실제 PC에는 입력하지 않습니다.",
                       "actions": ("mouse.move", "mouse.left_click"), "require_precision": "scroll" in by_key})
        click = dict(by_key["click"])
        click.update(title="3. 클릭 연습")
        result.append(click)
    else:
        result.append(dict(by_key.get("move", {"key": "move", "title": "커서 이동", "how": "손을 움직이세요.", "actions": ("mouse.move",)})))
    if "scroll" in by_key:
        scroll = dict(by_key["scroll"])
        scroll.update(title="4. 스크롤 연습")
        result.append(scroll)
    result.append({"key": "control", "title": "V로 제어 켜기와 끄기",
                   "how": "검지·중지만 펴고 나머지는 접어 V를 0.7초 유지하세요. 손을 풀고 1.5초 뒤 한 번 더 해보세요. 연습에서는 실제 입력 상태를 바꾸지 않습니다.",
                   "actions": ("control.toggle",)})
    if "desktop" in by_key:
        desktop = dict(by_key["desktop"])
        desktop.update(title="선택: 바탕화면 보기", optional=True)
        result.append(desktop)
    if "motion" in by_key:
        motion = dict(by_key["motion"])
        motion.update(title="선택: 모션 모드", how=str(motion["how"]) + " 모드가 켜지면 손을 펴서 한 번 쓸어 보세요.",
                      actions=("nav.back", "nav.forward", "tab.prev", "tab.next"), optional=True)
        result.append(motion)
    return result


class Tutorial:
    def __init__(self, steps: Sequence[Card]) -> None:
        self.steps = list(steps)
        self.index = 0
        self.succeeded_at: Optional[float] = None
        self.done = [False] * len(self.steps)
        self.skipped = [False] * len(self.steps)
        self._moving = 0.0
        self._last_t: Optional[float] = None
        self._last: frozenset = frozenset()
        self._attempt_since: Optional[float] = None
        self.failures = 0
        self._target_done = [False] * len(TARGETS)
        self.cursor: Optional[tuple[float, float]] = None
        self._control_count = 0
        self.precision_seen = False

    @property
    def finished(self) -> bool:
        return self.index >= len(self.steps)

    @property
    def current(self) -> Optional[Card]:
        return None if self.finished else self.steps[self.index]

    @property
    def step_key(self) -> str:
        return str(self.current["key"]) if self.current else ""

    @property
    def targets(self) -> list[dict]:
        return [dict(x=x, y=y, done=self._target_done[i]) for i, (x, y) in enumerate(TARGETS)]

    @property
    def can_skip(self) -> bool:
        return self.failures >= MAX_FAILURES or bool(self.current and self.current.get("optional"))

    @property
    def help_text(self) -> str:
        if self.step_key == "sensitivity" and all(self._target_done) and self.current.get("require_precision") and not self.precision_seen:
            return "목표 3개를 클릭했습니다. 주먹을 쥐고 짧게 움직여 빠른 가속(정밀) 이동도 확인하세요."
        if self.failures >= MAX_FAILURES:
            return "3번의 연습 시간 안에 성공을 확인하지 못했습니다. 손이 관절 표시 안에 있는지, 손가락을 완전히 떼었는지 확인하세요. 다시 시도하거나 이 단계만 건너뛸 수 있습니다."
        if self.failures:
            return f"아직 성공을 확인하지 못했습니다 ({self.failures}/3). 손을 편 상태로 돌아간 뒤 다시 시도하세요."
        return "손을 놓치면 조명과 카메라 거리를 먼저 확인하세요. 나중에 배우기로 연습을 마칠 수도 있습니다."

    @property
    def progress(self) -> float:
        if self.current is None or self.succeeded_at is not None:
            return 1.0
        if self.step_key in ("move", "camera"):
            return min(1.0, self._moving / MOVE_SECONDS)
        if self.step_key == "sensitivity":
            targets = sum(self._target_done) / len(TARGETS)
            return min(targets, .9) if self.current.get("require_precision") and not self.precision_seen else targets
        if self.step_key == "control":
            return self._control_count / 2
        return 0.0

    def observe(self, actions: Iterable[str], motion_on: bool, now: float, *,
                hand_tracked: bool = False, point: Optional[tuple[float, float]] = None,
                precision: bool = False) -> bool:
        """Consume accepted practice actions and an engine-composed practice cursor."""
        actions = frozenset(actions)
        card = self.current
        dt = 0.0 if self._last_t is None else max(0.0, min(.2, now-self._last_t))
        self._last_t = now
        rising = actions - self._last
        self._last = actions
        self.cursor = point if point and all(math.isfinite(v) for v in point) else None
        if card is None or self.succeeded_at is not None:
            return False
        if self._attempt_since is None:
            self._attempt_since = now
        wanted = set(card["actions"])
        if self.step_key == "camera":
            self._moving = self._moving + dt if hand_tracked else 0.0
            hit = self._moving >= MOVE_SECONDS
        elif self.step_key == "sensitivity":
            self.precision_seen |= precision
            if "mouse.left_click" in rising and self.cursor is not None:
                for i, (x, y) in enumerate(TARGETS):
                    if not self._target_done[i] and math.hypot(self.cursor[0]-x, self.cursor[1]-y) <= TARGET_RADIUS:
                        self._target_done[i] = True
                        self._attempt_since = now
                        break
            hit = all(self._target_done) and (self.precision_seen or not card.get("require_precision"))
        elif self.step_key == "control":
            if "control.toggle" in rising:
                self._control_count += 1
            hit = self._control_count >= 2
        elif self.step_key == "motion" and MOTION not in wanted:
            hit = motion_on and bool(rising & wanted)
        elif MOTION in wanted:
            hit = motion_on
        elif self.step_key == "move":
            self._moving = self._moving+dt if actions & wanted else max(0.0, self._moving-dt/2)
            hit = self._moving >= MOVE_SECONDS
        else:
            hit = bool(rising & wanted)
        if hit:
            self.succeeded_at = now
            self.done[self.index] = True
            return True
        if now-self._attempt_since >= ATTEMPT_SECONDS and self.failures < MAX_FAILURES:
            self.failures += 1
            self._attempt_since = now
            return True
        return False

    def tick(self, now: float) -> bool:
        if self.succeeded_at is not None and now-self.succeeded_at >= ADVANCE_AFTER:
            self.next()
            return True
        return False

    def next(self) -> None:
        if not self.finished:
            self.skipped[self.index] = not self.done[self.index]
            self.index += 1
        self._reset_step()

    def back(self) -> None:
        self.index = max(0, self.index-1)
        self._reset_step()

    def retry(self) -> None:
        self._reset_step()

    def _reset_step(self) -> None:
        self.succeeded_at = None
        self._moving = 0.0
        self._last_t = None
        self._last = frozenset()
        self._attempt_since = None
        self.failures = 0
        self._target_done = [False] * len(TARGETS)
        self._control_count = 0
        self.precision_seen = False
        self.cursor = None
