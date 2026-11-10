"""Supported sensitivity controls from Dan's settings_panel.py at a60241d.

Presentation metadata and validation only; features and the Windows sink own behaviour.
Opening the editor never retunes existing values. Apply accepts only edited keys.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from manux.settings.schema import Setting
from manux.settings.store import Settings


@dataclass(frozen=True)
class Control:
    key: str
    label: str
    low: float
    high: float
    step: float
    group: str
    hint: str


CONTROLS = (
    Control("pinch.pinch_threshold", "엄지·검지 핀치 감도 · 스크롤", .02, .12, .001, "pinch", "높을수록 쉽게 잡힙니다. 놓는 기준은 +0.02로 함께 조절됩니다."),
    Control("pinch.click_threshold", "엄지·중지 클릭 감도 · 좌클릭", .02, .12, .001, "pinch", "높을수록 손가락을 덜 붙여도 클릭됩니다."),
    Control("pinch.ring_click_threshold", "엄지·약지 클릭 감도 · 우클릭", .02, .12, .001, "pinch", "높을수록 손가락을 덜 붙여도 클릭됩니다."),
    Control("pinch.pinky_click_threshold", "엄지·새끼 핀치 감도 · 창 전환", .02, .12, .001, "pinch", "높을수록 창 전환 핀치가 쉽게 잡힙니다."),
    Control("pinch.pinch_release_grace", "핀치 유지 보정 (초)", 0, .5, .01, "pinch", "짧게 손가락이 벌어져도 핀치를 유지하는 시간입니다."),
    Control("pinch.pinch_fistlike_confirm", "주먹 모양 핀치 확인 (초)", 0, .4, .01, "pinch", "주먹과 핀치를 구분하기 위해 기다리는 시간입니다."),
    Control("pinch.click_release_margin", "클릭 핀치 유지 여유", 0, .06, .002, "pinch", "높을수록 클릭 중 손가락이 조금 벌어져도 유지됩니다."),
    Control("pinch.click_release_grace", "클릭 핀치 끊김 보정 (초)", 0, .2, .01, "pinch", "짧은 추적 흔들림으로 클릭이 끊기는 것을 줄입니다."),
    Control("pinch.click_confirm_time", "클릭 확인 시간 (초)", 0, .3, .01, "pinch", "짧을수록 빠르게 반응하고 길수록 우발적인 클릭을 줄입니다."),
    Control("cursor.relative_gain_min", "이동 감도 (천천히)", .05, 3, .05, "cursor", "손을 천천히 움직일 때의 커서 이동 배율입니다."),
    Control("cursor.relative_gain_max", "이동 감도 (빠르게)", .5, 6, .1, "cursor", "손을 빠르게 움직일 때의 커서 이동 배율입니다."),
    Control("cursor.smoothing", "이동 부드러움", .05, 1, .01, "cursor", "낮을수록 부드럽고, 높을수록 손의 움직임에 빠르게 반응합니다."),
    Control("cursor.scroll_gain", "스크롤 휠 속도", 1000, 30000, 500, "cursor", "엄지+검지 스크롤 속도입니다. 낮을수록 천천히, 정밀하게 스크롤됩니다."),
    Control("pinch.fist_curl_enter", "주먹 판정 기준", .5, 1.3, .05, "cursor", "주먹 이동으로 인식하는 손가락 굽힘 기준입니다."),
    Control("pinch.fist_confirm_time", "주먹 확인 시간 (초)", 0, .5, .01, "cursor", "주먹 이동으로 전환하기 전에 확인하는 시간입니다."),
    Control("pinch.fist_dpi_multiplier", "주먹 정밀 이동 배율", .05, 30, .05, "cursor", "1보다 작으면 주먹 이동이 느려져 세밀하게 조작할 수 있습니다."),
    Control("pinch.long_press_drag_multiplier", "길게 누르기 드래그 배율", .1, 5, .05, "cursor", "좌클릭 핀치를 유지해 드래그할 때의 이동 배율입니다. 기본 1.0은 일반 이동과 같습니다."),
)
BY_KEY = {c.key: c for c in CONTROLS}
CURSOR_SETTINGS = (
    Setting("cursor.relative_gain_min", .35, minimum=.05, maximum=3),
    Setting("cursor.relative_gain_max", 1.2, minimum=.5, maximum=6),
    Setting("cursor.smoothing", .35, minimum=.05, maximum=1),
)


def rows(settings: Settings):
    return [dict(key=c.key, label=c.label, minimum=c.low, maximum=c.high, step=c.step,
                 group=c.group, hint=c.hint, value=settings.get(c.key),
                 defaultValue=settings.schema.get(c.key).default,
                 editable=not settings.is_overridden(c.key)) for c in CONTROLS]


def prepare(settings: Settings, edits: Mapping[str, float]) -> dict[str, float]:
    """Validate a whole edit before any save; enforce Dan's coupled release/gain limits."""
    result = {}
    for key, value in edits.items():
        control = BY_KEY.get(key)
        if control is None or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("올바른 민감도 숫자를 입력하세요.")
        if not control.low <= value <= control.high:
            raise ValueError(f"{control.label}: {control.low}~{control.high} 범위로 입력하세요.")
        if settings.is_overridden(key):
            raise ValueError("프로필이 지정한 감도입니다. 기본 프로필에서 변경하세요.")
        result[key] = round(control.low + round((value - control.low) / control.step) * control.step, 6)
    if "pinch.pinch_threshold" in result:
        result["pinch.release_threshold"] = round(result["pinch.pinch_threshold"] + .02, 6)
    low, high = "cursor.relative_gain_min", "cursor.relative_gain_max"
    if result.get(low, settings.get(low)) > result.get(high, settings.get(high)):
        if low in result and high not in result:
            result[high] = result[low]
        elif high in result and low not in result:
            result[low] = result[high]
        else:
            raise ValueError("빠른 이동 감도는 느린 이동 감도 이상으로 설정하세요.")
    if any(settings.is_overridden(key) for key in result):
        raise ValueError("함께 조절할 값이 프로필에 지정되어 있습니다. 기본 프로필에서 변경하세요.")
    return result
