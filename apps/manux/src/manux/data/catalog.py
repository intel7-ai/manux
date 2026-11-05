"""The one capture-task catalog: what the guided recorder can ask a person to do, as data.

Ported from rasp-jun `origin/codex/rasp-jun-training-center` `src/gesture_catalog.py` (18 tasks, same
keys and training labels, so recordings made with either stay comparable) plus the extra tasks of the
tester collector (PR #63 `tools/collect_mouse.py`: double click, horizontal scroll, record-and-fling,
pointer and trigger click styles). The collector, the TCN tools and (later) the training-center screen
all read this list; none keeps its own.

A task (`GestureSpec`) has:
- `key`: the task id, written to `label.task` (what the person was asked to do);
- `target`: the training class written to `label.target` on its "do" frames (defaults to `key`;
  several tasks may share one class, e.g. click and double click are both `thumb_middle`);
- `gesture`: the v0.4 gesture id the *class* stands for (`label.gesture`, model card `gestures`).
  `None` for the `none` class and for capture-only tasks with no v0.4 feature yet
  (`v_sign`, `rock_sign`, `swipe_down`): those are recorded and can be trained, but nothing in
  v0.4 checks them yet;
- `related`: a v0.4 gesture the task performs although its class is not that gesture (cursor moves
  recorded as `none` negatives: `pinch.move`, `pinch.move_fist`, `pointer.move`);
- `category` (screen grouping), `direction` (swipes; the dataset reader drops "do" frames that did not
  move that way), `label_delay` / `min_hold` (s), `modes` (hand-control modes it belongs to; empty = all).

No action names here (D-004): what a gesture *does* is the bindings' job (contracts section 7).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from manux.core.contracts import GESTURE_ID_RE

RESERVED = ("transition", "idle", "no_hand")   # labels that are never a class
NONE = "none"                                   # the "no gesture" class (rest frames)
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DIRECTIONS = ("", "left", "right", "down")
MODES = ("pinch", "pointer", "trigger")         # hand-control styles of the collector presets


@dataclass(frozen=True)
class GestureSpec:
    key: str                       # task id (`label.task`); also the class when `target` is empty
    name: str                      # on screen
    do: str                        # what to do, on screen
    free: bool = False             # one long stretch instead of repetitions
    gesture: Optional[str] = None  # the gesture id the class stands for (e.g. "pinch.thumb_ring")
    target: str = ""               # training class of the "do" frames, if not `key`
    category: str = ""
    direction: str = ""
    related: Optional[str] = None  # gesture performed although the class is another one (see module doc)
    label_delay: float = 0.0       # s at the start of "do" still labelled `transition`
    min_hold: float = 0.0          # s: the "do" phase lasts at least this long
    modes: Tuple[str, ...] = ()    # empty = every mode

    def __post_init__(self) -> None:
        for name, value in (("key", self.key), ("target", self.label)):
            if not KEY_RE.match(value) or value in RESERVED:
                raise ValueError(f"spec {name} must be a lowercase name other than {RESERVED}, got {value!r}")
        for gid in (self.gesture, self.related):
            if gid is not None and not GESTURE_ID_RE.match(gid):
                raise ValueError(f"gesture must be a gesture id '<feature>.<name>', got {gid!r}")
        if self.label == NONE and self.gesture is not None:
            raise ValueError("the 'none' class stands for no gesture")
        if self.label == NONE and not self.free:
            raise ValueError("'none' is recorded as a free stretch (its rest frames come from the other sets)")
        if self.direction not in DIRECTIONS:
            raise ValueError(f"direction must be one of {DIRECTIONS}, got {self.direction!r}")
        if self.label_delay < 0 or self.min_hold < 0:
            raise ValueError("label_delay and min_hold must be >= 0")
        if set(self.modes) - set(MODES):
            raise ValueError(f"modes must be among {MODES}, got {self.modes}")

    @property
    def label(self) -> str:
        """The training class of this task's "do" (or free) frames."""
        return self.target or self.key

    @property
    def gesture_id(self) -> Optional[str]:
        """The v0.4 gesture this task exercises, if any (class gesture first, else `related`)."""
        return self.gesture or self.related

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"key": self.key, "name": self.name, "free": self.free}
        if self.gesture:
            out["gesture"] = self.gesture
        if self.target and self.target != self.key:
            out["target"] = self.target
        return out


def _t(key: str, name: str, do: str, category: str, **kw: Any) -> GestureSpec:
    return GestureSpec(key, name, do, category=category, **kw)


CLICK, WINDOW, MOTION, MOVE = "클릭·드래그", "창·모드", "방향 모션", "이동·중립"
CATEGORIES = ("전체", CLICK, WINDOW, MOTION, MOVE)
_PINCH = ("pinch",)

# rasp-jun's 18, in his order (the first three are Dan's pilot set and keep their labels).
TASKS: Tuple[GestureSpec, ...] = (
    _t("thumb_ring", "엄지+약지 (우클릭)", "엄지와 약지 끝을 붙여 유지하세요. 쉬기 때 떼세요", CLICK,
       gesture="pinch.thumb_ring", modes=_PINCH),
    _t("thumb_index", "엄지+검지 (스크롤)", "엄지와 검지로 집어 위아래로 끌어보세요. 쉬기 때 놓으세요", CLICK,
       gesture="pinch.scroll", modes=_PINCH),
    _t(NONE, "아무 동작 아님 (반주먹 이동)", "반주먹으로 커서를 움직이듯 손을 이리저리 움직이세요", MOVE, free=True),
    _t("thumb_middle", "엄지+중지 (좌클릭)", "엄지와 중지 끝을 붙여 유지하세요. 쉬기 때 떼세요", CLICK,
       gesture="pinch.thumb_middle", modes=_PINCH),
    _t("thumb_middle_drag", "엄지+중지 유지 (드래그)", "엄지와 중지를 붙인 채 1초 유지하고 손을 옮기세요. 쉬기 때 놓으세요",
       CLICK, target="thumb_middle", gesture="pinch.thumb_middle", related="pinch.drag", min_hold=2.6, modes=_PINCH),
    _t("thumb_index_middle", "엄지+검지+중지 (드래그)", "엄지·검지·중지 끝을 동시에 붙이고 손을 옮기세요. 쉬기 때 놓으세요",
       CLICK, gesture="pinch.drag", modes=_PINCH),
    _t("thumb_pinky", "엄지+새끼 (창 선택)", "엄지와 새끼손가락 끝을 붙여 유지하세요. 쉬기 때 떼세요", WINDOW,
       gesture="pinch.thumb_pinky", modes=_PINCH),
    _t("palm_side", "엄지 접고 손 가로 (바탕화면)", "엄지만 접고 네 손가락을 펴서 옆으로 향하게 유지하세요", WINDOW,
       gesture="pinch.thumb_tucked_open", modes=_PINCH),
    _t("v_sign", "V 손 모양 (제어 켜기·끄기)", "검지·중지만 V로 펴고 나머지는 접어 가만히 유지하세요", WINDOW),
    _t("rock_sign", "검지·새끼 펴기 (창 닫기)", "검지·새끼만 펴고 중지·약지를 접어 유지하세요. 실제 창은 닫히지 않아요", WINDOW),
    _t("thumbs_up", "엄지 위로 (모션 모드 켜기·끄기)", "네 손가락을 접고 엄지만 위로 세워 가만히 유지하세요", WINDOW,
       gesture="motion.enter"),
    _t("swipe_left", "손 펴고 왼쪽 쓸기 (뒤로)", "엄지까지 손을 펴고 왼쪽으로 한 번 쓸어 멈추세요. 쉬기 때 돌아오세요", MOTION,
       direction="left", gesture="motion.swipe_left"),
    _t("swipe_right", "손 펴고 오른쪽 쓸기 (앞으로)", "엄지까지 손을 펴고 오른쪽으로 한 번 쓸어 멈추세요. 쉬기 때 돌아오세요",
       MOTION, direction="right", gesture="motion.swipe_right"),
    _t("swipe_tab_left", "엄지 접고 왼쪽 쓸기 (이전 탭)", "엄지를 접고 네 손가락을 펴 왼쪽으로 한 번 쓸어 멈추세요. 쉬기 때 돌아오세요",
       MOTION, direction="left", gesture="motion.swipe_left_tucked"),
    _t("swipe_tab_right", "엄지 접고 오른쪽 쓸기 (다음 탭)", "엄지를 접고 네 손가락을 펴 오른쪽으로 한 번 쓸어 멈추세요. 쉬기 때 돌아오세요",
       MOTION, direction="right", gesture="motion.swipe_right_tucked"),
    _t("swipe_down", "손 펴고 아래로 쓸기 (창 닫기)", "손을 펴 아래로 한 번 쓸어 멈추세요. 쉬기 때 올리세요. 실제 창은 닫히지 않아요",
       MOTION, direction="down"),
    _t("open_move", "손 펴고 이동 (정밀 커서)", "손을 편 채 천천히 여러 방향으로 움직이세요. 손가락을 집지 마세요", MOVE,
       free=True, target=NONE, related="pinch.move", modes=_PINCH),
    _t("fist_move", "주먹 이동 (빠른 커서)", "주먹을 쥐고 여러 방향으로 움직이세요. 엄지만 세우지 마세요", MOVE,
       free=True, target=NONE, related="pinch.move_fist", modes=_PINCH),
)

# Tester-collector tasks (PR #63) that rasp-jun's list does not have.
EXTRA_TASKS: Tuple[GestureSpec, ...] = (
    _t("double_click", "엄지+중지 두 번 (더블클릭)", "엄지와 중지를 빠르게 두 번 붙였다 떼세요", CLICK,
       target="thumb_middle", gesture="pinch.thumb_middle", modes=_PINCH),
    _t("scroll_horizontal", "엄지+검지 옆으로 (가로 스크롤)", "엄지와 검지로 집어 왼쪽이나 오른쪽으로 끌고 놓으세요", CLICK,
       target="thumb_index", gesture="pinch.scroll", modes=_PINCH),
    _t("record_fling", "엄지+검지 튕기기 (관성 스크롤)", "엄지와 검지로 집어 길을 그리고, 튕기듯 놓으세요", CLICK,
       target="thumb_index", gesture="pinch.scroll", min_hold=2.4, modes=_PINCH),
    _t("pointer_move", "검지로 가리키며 이동", "검지를 펴서 가리키고 작은 모양을 그리듯 움직이세요", MOVE,
       free=True, target=NONE, related="pointer.move", modes=("pointer", "trigger")),
    _t("pointer_left", "가리킨 채 엄지+중지 (좌클릭)", "커서를 멈추고 엄지와 중지를 붙였다 떼세요", CLICK,
       gesture="pointer.left_click", modes=("pointer",)),
    _t("pointer_double_click", "가리킨 채 엄지+중지 두 번 (더블클릭)", "커서를 멈추고 엄지와 중지를 빠르게 두 번 붙였다 떼세요",
       CLICK, target="pointer_left", gesture="pointer.left_click", modes=("pointer",)),
    _t("pointer_right", "가리킨 채 엄지+약지 (우클릭)", "커서를 멈추고 엄지와 약지를 붙였다 떼세요", CLICK,
       gesture="pointer.right_click", modes=("pointer",)),
    _t("trigger_left", "검지 옆 누르기 (좌클릭)", "검지를 편 채 엄지로 검지 옆을 짧게 누르세요", CLICK,
       gesture="pointer.left_click", modes=("trigger",)),
    _t("trigger_double_click", "검지 옆 두 번 누르기 (더블클릭)", "검지를 편 채 엄지로 검지 옆을 두 번 누르세요", CLICK,
       target="trigger_left", gesture="pointer.left_click", modes=("trigger",)),
    _t("trigger_right", "검지 옆 누르고 유지 (우클릭)", "엄지로 검지 옆을 누른 채 가만히 유지하세요", CLICK,
       gesture="pointer.right_click", label_delay=0.55, modes=("trigger",)),
    _t("trigger_drag", "검지 옆 누르고 이동 (드래그)", "엄지로 검지 옆을 누른 채 손을 옮기고 놓으세요", CLICK,
       gesture="pointer.drag", modes=("trigger",)),
)

ALL_TASKS: Tuple[GestureSpec, ...] = TASKS + EXTRA_TASKS
BY_KEY: Dict[str, GestureSpec] = {t.key: t for t in ALL_TASKS}
# rasp-jun's training-center order (left click first).
DISPLAY_KEYS: Tuple[str, ...] = (
    "thumb_middle", "thumb_index", "thumb_ring", "thumb_middle_drag", "thumb_index_middle", "thumb_pinky",
    "palm_side", "v_sign", "rock_sign", "thumbs_up", "swipe_left", "swipe_right", "swipe_tab_left",
    "swipe_tab_right", "swipe_down", "none", "open_move", "fist_move")
PILOT_KEYS: Tuple[str, ...] = ("thumb_ring", "thumb_index", NONE)   # Dan's three (guided.DEFAULT_SPECS)
CAPTURE_ONLY: Tuple[str, ...] = tuple(t.key for t in ALL_TASKS if t.gesture_id is None and t.label != NONE)


def check_tasks(tasks: Iterable[GestureSpec]) -> Tuple[GestureSpec, ...]:
    """One key per task; a class stands for one gesture id; a gesture id has one class."""
    tasks = tuple(tasks)
    keys = [t.key for t in tasks]
    if len(set(keys)) != len(keys):
        raise ValueError(f"task keys must be unique, got {keys}")
    by_class: Dict[str, Optional[str]] = {}
    by_gesture: Dict[str, str] = {}
    for t in tasks:
        if by_class.setdefault(t.label, t.gesture) != t.gesture:
            raise ValueError(f"class {t.label!r} stands for two gesture ids: {by_class[t.label]}, {t.gesture}")
        if t.gesture and by_gesture.setdefault(t.gesture, t.label) != t.label:
            raise ValueError(f"two classes stand for the same gesture id {t.gesture}")
    return tasks


def classes(tasks: Sequence[GestureSpec] = TASKS) -> List[str]:
    """Training classes of these tasks: `none` first, then in task order."""
    return list(dict.fromkeys([NONE, *(t.label for t in tasks)]))


def class_gestures(tasks: Sequence[GestureSpec] = ALL_TASKS) -> Dict[str, str]:
    """class -> gesture id, for the classes that stand for one (model card `gestures`)."""
    return {t.label: t.gesture for t in tasks if t.gesture}


def in_mode(task: GestureSpec, mode: str) -> bool:
    return not task.modes or mode in task.modes


def tasks_for(focus: Union[str, Sequence[str]] = "", *, category: str = "",
              mode: str = "") -> Tuple[GestureSpec, ...]:
    """The tasks to record.

    - `focus` a task key: only that task ("focused recording"); an unknown key falls back to all (as
      rasp-jun's). A list of keys: those, in that order (unknown keys raise).
    - `category`: only that screen group (`"전체"` / empty = all).
    - `mode`: tasks of that hand-control mode from the whole catalog; empty = rasp-jun's 18.
    """
    if mode and mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    pool = tuple(t for t in ALL_TASKS if in_mode(t, mode)) if mode else TASKS
    if isinstance(focus, str):
        if focus in BY_KEY:
            return (BY_KEY[focus],)
        picked = pool
    else:
        unknown = [k for k in focus if k not in BY_KEY]
        if unknown:
            raise ValueError(f"unknown task(s): {', '.join(unknown)}")
        picked = tuple(BY_KEY[k] for k in dict.fromkeys(focus)) or pool
    if category and category != CATEGORIES[0]:
        if category not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}, got {category!r}")
        picked = tuple(t for t in picked if t.category == category)
    return picked


def is_free(key: str) -> bool:
    return key in BY_KEY and BY_KEY[key].free
