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


HOVER_SECONDS = .3   # the vertical-method step: rest the cursor on a position this long


def onboarding_steps(cards: Sequence[Card], *, palm: bool = False, pointer_setup: bool = False) -> list[Card]:
    """Teach everyday controls first; keep the full catalog on the main screen.

    The control hand was chosen before this (hand choice screen), so there is no camera/hand step. With the palm
    cursor: 1 move the cursor, 2 click, 3 choose how up/down works (whole hand vs wrist tilt) and reach three
    positions, 4 optional cursor fit setup (wrist tilt only), then speed/clicks, scroll, V and the optional ones."""
    by_key = {str(c["key"]): c for c in cards}
    result = [{"key": "move", "title": "커서 이동",
               "how": "손바닥이 카메라에 보이게 하고 손을 움직여 연습 커서를 움직여 보세요.",
               "actions": ("mouse.move",)}]
    if "click" in by_key:            # click right after moving: the later steps use it
        click = dict(by_key["click"])
        click.update(title="클릭 연습")
        result.append(click)
    if palm:
        result.append({"key": "vertical", "title": "상하 이동 방식 고르기",
                       "how": "커서를 위아래로 움직일 방법입니다. 손 전체 이동은 손을 위아래로 옮기고, 손목 기울기는 "
                              "팔을 받친 채 손목을 위아래로 꺾습니다. 강조된 방식이 지금 방식이며, 다른 쪽을 누르면 바뀝니다. "
                              "화면의 위치 1·2·3에 커서를 잠깐씩 올려 보세요.",
                       "actions": ("mouse.move",)})
        if pointer_setup:
            result.append({"key": "pointer_setup", "title": "커서 움직임 맞춤 설정", "optional": True,
                           "how": "손목 기울기를 쓸 때, 내 손의 좌우 범위와 위아래 각도를 재서 커서를 맞춥니다. 건너뛰어도 됩니다.",
                           "actions": ()})
    if "click" in by_key:
        result.append({"key": "sensitivity", "title": "내 손에 맞는 속도",
                       "how": "편 손은 빠르게, 주먹은 느리고 정밀하게 움직입니다. 속도를 조절하고 화면의 목표 3개를 손짓으로 클릭하세요. 실제 PC에는 입력하지 않습니다.",
                       "actions": ("mouse.move", "mouse.left_click"), "require_precision": "scroll" in by_key})
    if "scroll" in by_key:
        scroll = dict(by_key["scroll"])
        scroll.update(title="스크롤 연습")
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
    number = 0
    for step in result:          # number the steps in order; optional ones say so instead
        if not str(step["title"]).startswith("선택:"):
            number += 1
            step["title"] = f"{number}. {step['title']}" + (" (선택)" if step.get("optional") else "")
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
        self.vertical_chosen = False     # set by the bridge when the user picks an up/down method
        self.skip_keys: set = set()      # steps passed over when reached (e.g. the wrist-only cursor fit)
        self.vertical_method = ""        # the method picked in this lesson ("hand_position" / "wrist_pitch")
        self._hover_since: list[Optional[float]] = [None] * len(TARGETS)

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
        if self.step_key == "vertical" and not self.vertical_chosen:
            return "먼저 위의 두 방식 중 하나를 고르세요. 나중에 설정에서 바꿀 수 있습니다."
        if self.step_key == "pointer_setup":
            return "맞춤 설정을 마치거나 건너뛰면 다음 단계로 넘어갑니다."
        if self.step_key == "sensitivity" and all(self._target_done) and self.current.get("require_precision") and not self.precision_seen:
            return "목표 3개를 클릭했습니다. 주먹을 쥐고 짧게 움직여 느린 정밀 이동도 확인하세요."
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
        if self.step_key == "vertical":
            return sum(self._target_done) / len(TARGETS) if self.vertical_chosen else 0.0
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
        elif self.step_key == "vertical":
            # rest the cursor on each of the three positions (no click needed) with the chosen method
            for i, (x, y) in enumerate(TARGETS):
                inside = (self.vertical_chosen and self.cursor is not None and not self._target_done[i]
                          and math.hypot(self.cursor[0]-x, self.cursor[1]-y) <= TARGET_RADIUS)
                if not inside:
                    self._hover_since[i] = None
                elif self._hover_since[i] is None:
                    self._hover_since[i] = now
                elif now - self._hover_since[i] >= HOVER_SECONDS:
                    self._target_done[i] = True
                    self._attempt_since = now
            hit = all(self._target_done)
        elif self.step_key == "pointer_setup":
            hit = False                          # completed by the bridge when the setup closes
            self._attempt_since = now            # no failure count while the user decides
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
        while not self.finished and self.step_key in self.skip_keys:   # steps that do not apply (set by the bridge)
            self.skipped[self.index] = True
            self.index += 1
        self._reset_step()

    def complete_current(self, now: float) -> None:
        """Mark the current step done from outside (the cursor setup closed); it advances like a success."""
        if not self.finished:
            self.done[self.index] = True
            self.succeeded_at = now - ADVANCE_AFTER   # advance on the next tick

    def start_after(self, key: str) -> None:
        """Begin past an already-confirmed step (e.g. the camera check right after the cursor setup)."""
        if self.index < len(self.steps) and self.step_key == key:
            self.done[self.index] = True
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
        self._hover_since = [None] * len(TARGETS)
        self.cursor = None
