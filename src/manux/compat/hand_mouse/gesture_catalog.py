"""Shared recording tasks and model labels. Task names describe gestures, not remappable OS keys."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    key: str
    name: str
    do: str
    free: bool = False
    category: str = "클릭·드래그"
    target: str = ""
    action: str = ""
    direction: str = ""

    @property
    def label(self):
        return self.target or self.key

    @property
    def track(self) -> str:
        if self.free or self.label == "none":
            return "free"
        if self.category == "방향 모션" or bool(self.direction) or self.key.startswith(("swipe_", "motion_")):
            return "motion"
        return "static"


# Keep the original first three tasks/labels compatible with existing recordings.
BASE_TASKS = (
    Task("thumb_ring", "엄지+약지 (우클릭)", "엄지와 약지 끝을 붙인 채 천천히 조금씩 움직이며 유지하세요", action="right_click"),
    Task("thumb_index", "엄지+검지 (스크롤)", "엄지와 검지를 붙인 채 위아래로 천천히 끌어보세요", action="scroll_grab"),
    Task("none", "아무 동작 아님 (반주먹 이동)", "반주먹으로 커서를 움직이듯 손을 이리저리 움직이세요", True, "이동·중립"),
    Task("thumb_middle", "엄지+중지 (좌클릭)", "엄지와 중지 끝을 붙인 채 천천히 조금씩 움직이며 유지하세요", action="left_click"),
    Task("thumb_middle_drag", "엄지+중지 유지 (드래그)", "엄지와 중지를 붙인 채 손을 여러 방향으로 천천히 옮기세요", target="thumb_middle", action="drag_hold"),
    Task("thumb_index_middle", "엄지+검지+중지 (드래그)", "엄지·검지·중지 끝을 동시에 붙인 채 손을 천천히 옮기세요", action="drag_hold"),
    Task("thumb_pinky", "엄지+새끼 (창 선택)", "엄지와 새끼손가락 끝을 붙인 채 천천히 조금씩 움직이며 유지하세요", category="창·모드", action="task_switcher"),
    Task("palm_side", "엄지 접고 손 가로 (바탕화면)", "엄지만 접고 네 손가락을 펴서 옆으로 향하게 유지하세요", category="창·모드", action="show_desktop"),
    Task("v_sign", "V 손 모양 (제어 켜기·끄기)", "검지·중지만 V로 펴고 나머지는 접어 가만히 유지하세요", category="창·모드"),
    Task("rock_sign", "검지·새끼 펴기 (창 닫기)", "검지·새끼만 펴고 중지·약지를 접어 유지하세요. 실제 창은 닫히지 않아요", category="창·모드"),
    Task("thumbs_up", "엄지 위로 (모션 모드 켜기·끄기)", "네 손가락을 접고 엄지만 위로 세워 가만히 유지하세요", category="창·모드"),
    Task("swipe_left", "손 펴고 왼쪽 쓸기 (뒤로)", "엄지까지 손을 펴고 왼쪽으로 한 번 쓸어 멈추세요. 쉬기 때 돌아오세요", category="방향 모션", direction="left"),
    Task("swipe_right", "손 펴고 오른쪽 쓸기 (앞으로)", "엄지까지 손을 펴고 오른쪽으로 한 번 쓸어 멈추세요. 쉬기 때 돌아오세요", category="방향 모션", direction="right"),
    Task("swipe_tab_left", "엄지 접고 왼쪽 쓸기 (이전 탭)", "엄지를 접고 네 손가락을 펴 왼쪽으로 한 번 쓸어 멈추세요. 쉬기 때 돌아오세요", category="방향 모션", direction="left"),
    Task("swipe_tab_right", "엄지 접고 오른쪽 쓸기 (다음 탭)", "엄지를 접고 네 손가락을 펴 오른쪽으로 한 번 쓸어 멈추세요. 쉬기 때 돌아오세요", category="방향 모션", direction="right"),
    Task("swipe_down", "손 펴고 아래로 쓸기 (창 내리기)", "손을 펴 아래로 한 번 쓸어 멈추세요. 쉬기 때 올리세요. 실제 창은 내려가지 않아요", category="방향 모션", direction="down"),
    Task("open_move", "손 펴고 이동 (정밀 커서)", "손을 편 채 천천히 여러 방향으로 움직이세요. 손가락을 집지 마세요", True, "이동·중립", "none"),
    Task("fist_move", "주먹 이동 (빠른 커서)", "주먹을 쥐고 여러 방향으로 움직이세요. 엄지만 세우지 마세요", True, "이동·중립", "none"),
    # Near misses: the hard negatives of each pinch. Taught as "none" so a finger that only comes close stops firing.
    Task("near_thumb_index", "엄지+검지 닿기 직전 (오판 방지)", "엄지와 검지 끝을 닿을 듯 가까이 가져갔다 벌리기를 반복하세요. 끝이 닿으면 안 돼요", True, "이동·중립", "none"),
    Task("near_thumb_middle", "엄지+중지 닿기 직전 (오판 방지)", "엄지와 중지 끝을 닿을 듯 가까이 가져갔다 벌리기를 반복하세요. 끝이 닿으면 안 돼요", True, "이동·중립", "none"),
    Task("near_thumb_ring", "엄지+약지 닿기 직전 (오판 방지)", "엄지와 약지 끝을 닿을 듯 가까이 가져갔다 벌리기를 반복하세요. 끝이 닿으면 안 돼요", True, "이동·중립", "none"),
    Task("near_thumb_pinky", "엄지+새끼 닿기 직전 (오판 방지)", "엄지와 새끼손가락 끝을 닿을 듯 가까이 가져갔다 벌리기를 반복하세요. 끝이 닿으면 안 돼요", True, "이동·중립", "none"),
)
TASKS = BASE_TASKS
# Near-miss task -> landmark index of the fingertip that must not touch the thumb.
NEAR_MISS_TIPS = {"near_thumb_index": 8, "near_thumb_middle": 12, "near_thumb_ring": 16, "near_thumb_pinky": 20}
FREE_DISPLAY_KEYS = ["none", "open_move", "fist_move", *NEAR_MISS_TIPS]
BASE_DISPLAY_KEYS = [
    "thumb_middle", "thumb_index", "thumb_ring", "thumb_middle_drag", "thumb_index_middle",
    "thumb_pinky", "palm_side", "v_sign", "rock_sign", "thumbs_up", "swipe_left", "swipe_right",
    "swipe_tab_left", "swipe_tab_right", "swipe_down", *FREE_DISPLAY_KEYS,
]
BY_KEY = {t.key: t for t in TASKS}
DISPLAY_KEYS = list(BASE_DISPLAY_KEYS)
NAMES = {key: BY_KEY[key].name for key in DISPLAY_KEYS}
CLASSES = ["none", "thumb_index", "thumb_ring"] + list(dict.fromkeys(t.label for t in TASKS if t.label not in {"none", "thumb_index", "thumb_ring"}))
CATEGORIES = ["전체", "클릭·드래그", "창·모드", "방향 모션", "이동·중립"]
BASE_MOTION_TARGETS = {
    "back": "swipe_left",
    "forward": "swipe_right",
    "tab_prev": "swipe_tab_left",
    "tab_next": "swipe_tab_right",
    "window_down": "swipe_down",
}
MOTION_TARGETS = dict(BASE_MOTION_TARGETS)


# Unit vectors (image coordinates: +x right, +y down) for every direction a motion can be registered with
_D = 0.7071067811865476
DIRECTION_VECTORS = {
    "left": (-1.0, 0.0), "right": (1.0, 0.0), "up": (0.0, -1.0), "down": (0.0, 1.0),
    "diag_up_left": (-_D, -_D), "diag_up_right": (_D, -_D),
    "diag_down_left": (-_D, _D), "diag_down_right": (_D, _D),
}


def direction_speed(direction: str, vx: float, vy: float):
    """Velocity along a motion's direction (positive = the intended way). None for an unknown direction."""
    v = DIRECTION_VECTORS.get(direction)
    if v is None:
        return None
    return v[0] * float(vx) + v[1] * float(vy)


def is_motion_task(key: str) -> bool:
    t = BY_KEY.get(key)
    return bool(t is not None and t.track == "motion")


def sync_custom_motions(custom_motions: dict | None = None) -> None:
    """Synchronize user-registered temporal motion gestures into the global catalog structures in-place."""
    global TASKS
    custom_tasks = []
    if isinstance(custom_motions, dict):
        for k, m in custom_motions.items():
            if not isinstance(m, dict) or k in {t.key for t in BASE_TASKS}:
                continue
            name = str(m.get("name") or k)
            instr = str(m.get("instruction") or f"{name} 모션을 한 번 수행한 뒤 멈추세요.")
            direction = str(m.get("direction") or "right")
            custom_tasks.append(
                Task(
                    key=k,
                    name=name,
                    do=instr,
                    free=False,
                    category="방향 모션",
                    target="",
                    action=k,
                    direction=direction,
                )
            )
    TASKS = BASE_TASKS + tuple(custom_tasks)
    BY_KEY.clear()
    for t in TASKS:
        BY_KEY[t.key] = t

    # Insert custom motion keys right after built-in motion keys and before free/neutral keys
    head_keys = [k for k in BASE_DISPLAY_KEYS if k not in FREE_DISPLAY_KEYS]
    custom_keys = [t.key for t in custom_tasks]
    DISPLAY_KEYS.clear()
    DISPLAY_KEYS.extend(head_keys + custom_keys + FREE_DISPLAY_KEYS)

    NAMES.clear()
    for k in DISPLAY_KEYS:
        if k in BY_KEY:
            NAMES[k] = BY_KEY[k].name

    CLASSES.clear()
    CLASSES.extend(
        ["none", "thumb_index", "thumb_ring"]
        + list(dict.fromkeys(t.label for t in TASKS if t.label not in {"none", "thumb_index", "thumb_ring"}))
    )

    MOTION_TARGETS.clear()
    MOTION_TARGETS.update(BASE_MOTION_TARGETS)
    for t in custom_tasks:
        MOTION_TARGETS[t.key] = t.key


def tasks_for(focus=""):
    return (BY_KEY[focus],) if focus in BY_KEY else TASKS


def is_free(key):
    return key in BY_KEY and BY_KEY[key].free
