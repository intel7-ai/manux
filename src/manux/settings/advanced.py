"""Advanced editor metadata and all-or-nothing validation; no gesture decisions.

The composition root registers EXTRA_SETTINGS and passes cursor.* to WindowsOutputConfig.
Only running-engine settings are editable here; cameras, models and personal data have their
own editors. Opening this editor never changes settings or snaps existing values.
"""
from __future__ import annotations

import math
from typing import Mapping

from manux.settings.schema import Setting
from manux.settings.sensitivity import BY_KEY as SENSITIVITY
from manux.settings.store import Settings


EXTRA_SETTINGS = (
    Setting("cursor.movement_mode", "relative", kind="choice", choices=("relative", "absolute")),
    Setting("cursor.active_margin", .3, minimum=0, maximum=.45),
    Setting("cursor.relative_accel_speed_low", .1, minimum=0, maximum=5),
    Setting("cursor.relative_accel_speed_high", .8, minimum=.01, maximum=10),
    Setting("cursor.double_click_window", .45, minimum=.1, maximum=.8),
    Setting("cursor.double_click_move_tolerance", .03, minimum=.005, maximum=.1),
    Setting("cursor.scroll_gain", 8000., minimum=1000, maximum=80000),
    Setting("cursor.scroll_deadzone", .01, minimum=0, maximum=.2),
    Setting("cursor.scroll_invert", False, kind="bool"),
    Setting("cursor.scroll_inertia", True, kind="bool"),
    Setting("cursor.scroll_inertia_time", .25, minimum=.01, maximum=3),
    Setting("cursor.scroll_inertia_min_speed", 600., minimum=0, maximum=10000),
    Setting("cursor.scroll_inertia_max_speed", 1200., minimum=1, maximum=20000),
    Setting("cursor.scroll_inertia_axis_lock", .5, minimum=0, maximum=1),
    Setting("cursor.scroll_smooth_tau", .03, minimum=0, maximum=.5),
)

GROUPS = {"pinch": "손바닥 · 핀치", "pointer": "검지 · 트리거", "motion": "모션",
          "cursor": "커서 · 스크롤", "control": "제어 켜기 · 끄기", "app": "안전"}
LABELS = {
    "movement_mode": "이동 좌표 방식", "active_margin": "절대좌표 가장자리 여백",
    "relative_gain_min": "기본 이동 속도", "relative_gain_max": "빠른 이동 속도",
    "relative_accel_speed_low": "가속 시작 손 속도", "relative_accel_speed_high": "최대 가속 손 속도",
    "smoothing": "커서 부드러움", "double_click_window": "더블클릭 커서 고정 시간 (초)",
    "double_click_move_tolerance": "더블클릭 고정 해제 거리",
    "scroll_gain": "스크롤 속도", "scroll_deadzone": "스크롤 시작 이동 거리",
    "scroll_invert": "스크롤 방향 반대로", "scroll_inertia": "스크롤 관성 사용",
    "scroll_inertia_time": "스크롤 관성 감속 시간 (초)", "scroll_inertia_min_speed": "관성 시작 최소 속도",
    "scroll_inertia_max_speed": "관성 최대 속도", "scroll_inertia_axis_lock": "관성 축 고정 비율",
    "scroll_smooth_tau": "스크롤 부드러움 시간 (초)", "enabled": "사용",
    "click_style": "클릭 방식", "anchor": "커서 기준점", "tracking_only": "추적만 하고 클릭하지 않기",
    "hand_lost_reset": "손 소실 후 안전 해제 시간 (초)",
    "pinch_threshold": "검지 핀치 시작 거리", "release_threshold": "검지 핀치 해제 거리",
    "pinch_hold_joint_ratio": "핀치 유지 관절 비율", "pinch_rearm_time": "핀치 재시작 대기 (초)",
    "scroll_release_margin": "스크롤 해제 거리 여유", "scroll_release_grace": "스크롤 끊김 보정 (초)",
    "scroll_freeze_ratio": "스크롤 커서 고정 비율", "scroll_freeze_delay": "스크롤 커서 고정 지연 (초)",
    "scroll_cursor_hold": "스크롤 뒤 커서 고정 시간 (초)", "grab_scroll_enabled": "엄지·검지 스크롤 사용",
    "drag_hold_threshold": "동시 핀치 드래그 거리", "drag_hold_entry_window": "동시 핀치 드래그 진입 시간 (초)",
    "click_lockout_after_grab": "스크롤 뒤 클릭 대기 (초)", "click_neighbor_ratio": "인접 손가락 클릭 구별 비율",
    "click_hold_joint_ratio": "클릭 유지 관절 비율", "click_tuck_ratio": "클릭 손가락 접힘 비율",
    "ring_tuck_ratio": "약지 접힘 비율", "pinky_tuck_ratio": "새끼 접힘 비율",
    "pinky_ring_dominance": "약지·새끼 구별 비율", "pinky_ring_min_gap": "약지·새끼 최소 거리 차이",
    "side_pinch_exclusive": "옆 손가락 핀치 중복 방지", "drag_clear_ratio": "드래그 해제 비율",
    "drag_clear_grace": "드래그 끊김 보정 (초)", "long_press_drag": "길게 누르기 드래그 사용",
    "long_press_time": "길게 누르기 시간 (초)", "index_tuck_ratio": "검지 접힘 비율",
    "shape_dpi_enabled": "손 모양에 따른 속도 사용", "shape_dpi_min": "손 모양 최소 속도 배율",
    "shape_curl_open": "손 펴짐 기준", "shape_curl_fist": "손 쥠 기준",
    "shape_dpi_curve": "손 모양 속도 변화 곡선", "shape_curl_smooth": "손 모양 변화 부드러움",
    "shape_freeze_distance": "손 모양 변화 중 커서 고정 거리", "fist_curl_exit": "주먹 해제 기준",
    "desktop_thumb_tuck": "바탕화면: 엄지 접힘 기준", "desktop_hold": "바탕화면: 유지 시간 (초)",
    "desktop_still_move": "바탕화면: 정지 이동 허용", "desktop_after_activity": "바탕화면: 이전 동작 뒤 대기 (초)",
    "desktop_extended_ratio": "바탕화면: 손가락 펴짐 기준", "desktop_min_palm_width": "바탕화면: 손바닥 폭 기준",
    "desktop_min_tip_gap": "바탕화면: 손끝 간격 기준", "desktop_cooldown": "바탕화면: 재실행 대기 (초)",
    "gesture_exclusive_time": "바탕화면·창 전환 배타 시간 (초)",
    "enable_thumb_tucked_open": "바탕화면 보기 사용", "enable_fist": "주먹 이동 사용",
    "enable_no_pinch": "핀치 없는 이동 사용", "enable_thumb_middle_pinch": "중지 좌클릭 사용",
    "enable_thumb_ring_pinch": "약지 우클릭 사용", "enable_thumb_index_middle_pinch": "동시 핀치 드래그 사용",
    "enable_thumb_pinky_pinch": "새끼 창 전환 사용", "motion_pose_freeze": "모션 진입 자세 중 커서 고정",
    "motion_fold_ratio": "모션 진입 손가락 접힘 비율", "motion_thumb_out": "모션 진입 엄지 벌림",
    "motion_thumb_up": "모션 진입 엄지 위쪽 기준", "cursor_anchor_ratio": "손바닥 커서 기준점 비율",
    "move_latch": "검지 이동 유지 시간 (초)", "move_speed": "검지 이동 시작 속도",
    "still_speed": "검지 정지 속도", "trigger_press_ratio": "트리거 누름 비율",
    "trigger_release_ratio": "트리거 해제 비율", "trigger_approach_ratio": "트리거 접근 비율",
    "trigger_long_press": "트리거 우클릭 유지 시간 (초)", "trigger_drag_move": "트리거 드래그 시작 거리",
    "trigger_rewind": "트리거 누름 전 위치 보정 시간 (초)",
    "enter_hold": "모션 진입 유지 시간 (초)", "enter_still": "모션 진입 정지 허용",
    "enter_grace": "모션 진입 끊김 보정 (초)", "enter_quiet": "다른 동작 뒤 모션 진입 대기 (초)",
    "timeout": "모션 자동 종료 시간 (초, 0은 끔)", "hand_lost_exit": "모션 손 소실 종료 시간 (초, 0은 끔)",
    "thumb_out": "모션 엄지 벌림", "thumb_up": "모션 엄지 위쪽 기준", "enter_path": "모션 진입 이동 한도",
    "fold_ratio": "모션 손가락 접힘 비율", "open_ratio": "모션 손가락 펴짐 비율",
    "open_min": "모션 손가락 최소 펴짐", "open_ratio_tab": "탭 전환 손가락 펴짐 비율",
    "open_min_tab": "탭 전환 최소 펴짐", "thumb_nav": "뒤로·앞으로 엄지 기준", "thumb_tab": "탭 전환 엄지 기준",
    "swipe_min_dx": "쓸기 최소 이동 거리", "swipe_max_ratio": "쓸기 수직 이동 허용 비율",
    "swipe_window": "쓸기 확인 구간 (초)", "swipe_min_time": "쓸기 최소 시간 (초)",
    "swipe_straight": "쓸기 직선 기준", "swipe_min_speed": "쓸기 최소 속도",
    "swipe_open_frac": "쓸기 중 손 펴짐 비율", "swipe_cooldown": "쓸기 재실행 대기 (초)",
    "reverse_pause": "반대 방향 전환 정지 시간 (초)", "pause_speed": "모션 정지 속도",
    "reverse_speed": "반대 방향 이동 속도", "return_block": "되돌아오는 손 억제 배율",
    "recent_move": "최근 이동 기준", "tab_frames": "탭 전환 확인 프레임 수", "settle_speed": "쓸기 종료 속도",
    "hold": "V 유지 시간 (초)", "cooldown": "V 재실행 대기 (초)",
    "max_gap": "V 인식 최대 프레임 간격 (초)", "mlp_confidence": "V 모양 모델 최소 신뢰도",
    "extended_ratio": "V 검지·중지 펴짐 기준", "folded_ratio": "V 약지·새끼 접힘 기준",
    "thumb_tuck": "V 엄지 접힘 기준",
}

# These settings belong to separate editors, or are derived by the runtime.
EXCLUDED = frozenset({"pointer.axes", "pointer.anchor", "pointer.click_style", "pointer.tracking_only",
                      "cursor.frame_aspect", "cursor.reanchor_gap"})


def _eligible(key: str) -> bool:
    return key not in EXCLUDED and (key.split(".", 1)[0] in GROUPS and
                                   (not key.startswith("app.") or key == "app.hand_lost_reset"))


def _bounds(setting: Setting):
    control = SENSITIVITY.get(setting.key)
    if control:
        # The advanced editor accepts the schema's widened precision multiplier, if present.
        low = setting.minimum if setting.minimum is not None else control.low
        high = setting.maximum if setting.maximum is not None else control.high
        return low, high, control.step
    if setting.kind not in ("float", "int"):
        return 0, 1, 1
    low = setting.minimum if setting.minimum is not None else 0
    high = setting.maximum if setting.maximum is not None else max(10, float(setting.default) * 10)
    if setting.key.startswith("control.") or setting.key in ("pinch.shape_dpi_curve", "pinch.shape_dpi_min"):
        low = max(low, .001)
    if setting.key == "control.mlp_confidence":
        high = 1
    if setting.key.endswith("tab_frames"):
        low, high = 1, 60
    return low, high, 1 if setting.kind == "int" else .001


def rows(settings: Settings):
    result = []
    for key in sorted(settings.schema.keys()):
        setting = settings.schema.get(key)
        if not _eligible(key) or setting.kind not in ("float", "int", "bool", "choice"):
            continue
        owner, short = key.split(".", 1)
        control = SENSITIVITY.get(key)
        low, high, step = _bounds(setting)
        hint = (control.hint if control else setting.doc) or "한 번에 한 항목씩 조절하고 연습 화면에서 확인하세요."
        if short in ("pinch_threshold", "release_threshold"):
            hint = "시작 거리보다 해제 거리를 크게 설정하세요. 고급 설정은 두 값을 독립적으로 적용합니다."
        result.append(dict(key=key, label=LABELS.get(short, control.label if control else short.replace("_", " ")),
                           group=GROUPS[owner], kind=setting.kind, minimum=low, maximum=high, step=step,
                           choices=list(setting.choices), value=settings.get(key), defaultValue=setting.default,
                           hint=hint,
                           editable=not settings.is_overridden(key)))
    return result


def prepare(settings: Settings, edits: Mapping[str, object]) -> dict[str, object]:
    """Validate without mutation. Invalid, stale or profile-owned values reject the entire edit.

    Unlike the simple sensitivity editor, advanced threshold edits are independent: a deliberately
    chosen release distance is preserved. Hysteresis and acceleration pairs must stay ordered.
    """
    if not isinstance(edits, Mapping):
        raise ValueError("설정 변경값을 확인하세요.")
    editable = {row["key"]: row for row in rows(settings)}
    result = {}
    for key, value in edits.items():
        row = editable.get(key)
        if row is None:
            raise ValueError("고급 설정에서 지원하지 않는 항목입니다.")
        if not row["editable"]:
            raise ValueError("프로필이 지정한 값입니다. 기본 프로필에서 변경하세요.")
        setting = settings.schema.get(key)
        checked, warning = setting.coerce(value)
        if warning:
            raise ValueError(f"{row['label']}: 값의 형식과 범위를 확인하세요.")
        if setting.kind in ("float", "int"):
            if not math.isfinite(checked) or not row["minimum"] <= checked <= row["maximum"]:
                raise ValueError(f"{row['label']}: {row['minimum']}~{row['maximum']} 범위로 입력하세요.")
        result[key] = checked

    def ordered(low: str, high: str, *, strict=False):
        if not ({low, high} & result.keys()) or low not in settings.schema or high not in settings.schema:
            return
        a, b = result.get(low, settings.get(low)), result.get(high, settings.get(high))
        if a > b or strict and a == b:
            raise ValueError(f"{LABELS.get(low.split('.', 1)[1], low)} 값은 {LABELS.get(high.split('.', 1)[1], high)}보다 {'작아야' if strict else '크지 않아야'} 합니다.")

    ordered("cursor.relative_gain_min", "cursor.relative_gain_max")
    ordered("cursor.relative_accel_speed_low", "cursor.relative_accel_speed_high", strict=True)
    ordered("cursor.scroll_inertia_min_speed", "cursor.scroll_inertia_max_speed")
    for owner in ("pinch", "pointer"):
        ordered(f"{owner}.pinch_threshold", f"{owner}.release_threshold", strict=True)
        ordered(f"{owner}.fist_curl_enter", f"{owner}.fist_curl_exit", strict=True)
    ordered("pointer.trigger_press_ratio", "pointer.trigger_release_ratio", strict=True)
    ordered("pointer.trigger_release_ratio", "pointer.trigger_approach_ratio")
    ordered("pinch.shape_curl_fist", "pinch.shape_curl_open", strict=True)
    ordered("control.folded_ratio", "control.extended_ratio", strict=True)
    return result
