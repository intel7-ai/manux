"""Ready-made choices for building a new gesture without recording or training anything first (2026-11-09).

- HOTKEY presets: common Windows / browser / editor / presentation / meeting shortcuts, in groups.
- RECIPES: a hand shape + a direction (or trigger) + what it does, one click fills the registration form. They are
  only starting points: the registration still learns the user's own motion from a few demos when asked to.

Data only, so the dashboard and the tests can both read it. Every key name must be one PyAutoGUI knows
(tests/test_gesture_presets.py checks that).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

HOTKEY_GROUPS: List[str] = ["브라우저", "창·화면", "시스템", "편집", "파일 탐색기", "발표·문서", "화상회의", "개발", "기본 키"]

# group of the presets that already existed in action_bindings.WINDOWS_HOTKEY_PRESETS
EXISTING_HOTKEY_GROUP: Dict[str, str] = {
    "alt+left": "브라우저", "alt+right": "브라우저", "ctrl+shift+tab": "브라우저", "ctrl+tab": "브라우저",
    "ctrl+w": "브라우저", "ctrl+t": "브라우저", "ctrl+shift+t": "브라우저", "f5": "브라우저", "ctrl+f": "브라우저",
    "ctrl+plus": "브라우저", "ctrl+minus": "브라우저", "pageup": "브라우저", "pagedown": "브라우저",
    "win+d": "창·화면", "win+tab": "창·화면", "alt+tab": "창·화면", "alt+f4": "창·화면",
    "win+shift+s": "시스템", "win+e": "파일 탐색기",
    "ctrl+c": "편집", "ctrl+v": "편집", "ctrl+z": "편집",
    "space": "기본 키", "enter": "기본 키", "esc": "기본 키",
}

# (value, what it does, shown shortcut, group)
_EXTRA_HOTKEYS = [
    # 브라우저
    ("ctrl+r", "새로고침", "Ctrl + R", "브라우저"),
    ("ctrl+shift+r", "강력 새로고침(캐시 무시)", "Ctrl + Shift + R", "브라우저"),
    ("ctrl+l", "주소창으로 이동", "Ctrl + L", "브라우저"),
    ("ctrl+d", "즐겨찾기 추가", "Ctrl + D", "브라우저"),
    ("ctrl+h", "방문 기록", "Ctrl + H", "브라우저"),
    ("ctrl+j", "다운로드 목록", "Ctrl + J", "브라우저"),
    ("ctrl+n", "새 창 · 새 문서", "Ctrl + N", "브라우저"),
    ("ctrl+shift+n", "시크릿 창 · 새 폴더", "Ctrl + Shift + N", "브라우저"),
    ("ctrl+0", "화면 크기 원래대로", "Ctrl + 0", "브라우저"),
    ("f11", "전체화면 켜기/끄기", "F11", "브라우저"),
    ("f12", "개발자 도구", "F12", "브라우저"),
    ("alt+home", "홈페이지로", "Alt + Home", "브라우저"),
    ("ctrl+1", "첫 번째 탭으로", "Ctrl + 1", "브라우저"),
    ("ctrl+9", "마지막 탭으로", "Ctrl + 9", "브라우저"),
    ("ctrl+pageup", "이전 탭", "Ctrl + PgUp", "브라우저"),
    ("ctrl+pagedown", "다음 탭", "Ctrl + PgDn", "브라우저"),
    ("home", "페이지 맨 위로", "Home", "브라우저"),
    ("end", "페이지 맨 아래로", "End", "브라우저"),
    ("ctrl+shift+delete", "인터넷 사용 기록 삭제", "Ctrl + Shift + Del", "브라우저"),
    ("ctrl+shift+b", "즐겨찾기 막대 켜기/끄기", "Ctrl + Shift + B", "브라우저"),
    # 창·화면
    ("win+up", "창 최대화", "Win + ↑", "창·화면"),
    ("win+down", "창 내리기 · 최소화", "Win + ↓", "창·화면"),
    ("win+left", "창을 왼쪽 절반으로", "Win + ←", "창·화면"),
    ("win+right", "창을 오른쪽 절반으로", "Win + →", "창·화면"),
    ("win+shift+left", "창을 왼쪽 모니터로", "Win + Shift + ←", "창·화면"),
    ("win+shift+right", "창을 오른쪽 모니터로", "Win + Shift + →", "창·화면"),
    ("win+m", "모든 창 최소화", "Win + M", "창·화면"),
    ("win+shift+m", "최소화한 창 복원", "Win + Shift + M", "창·화면"),
    ("alt+space", "창 메뉴 열기", "Alt + Space", "창·화면"),
    ("alt+esc", "창을 순서대로 전환", "Alt + Esc", "창·화면"),
    ("ctrl+win+d", "새 가상 데스크톱", "Ctrl + Win + D", "창·화면"),
    ("ctrl+win+left", "이전 가상 데스크톱", "Ctrl + Win + ←", "창·화면"),
    ("ctrl+win+right", "다음 가상 데스크톱", "Ctrl + Win + →", "창·화면"),
    ("ctrl+win+f4", "현재 가상 데스크톱 닫기", "Ctrl + Win + F4", "창·화면"),
    ("win+home", "활성 창만 남기기", "Win + Home", "창·화면"),
    ("win+plus", "돋보기 켜기/확대", "Win + +", "창·화면"),
    ("win+esc", "돋보기 끄기", "Win + Esc", "창·화면"),
    # 시스템
    ("ctrl+shift+esc", "작업 관리자", "Ctrl + Shift + Esc", "시스템"),
    ("win+l", "화면 잠금", "Win + L", "시스템"),
    ("win+i", "설정", "Win + I", "시스템"),
    ("win+r", "실행 창", "Win + R", "시스템"),
    ("win+x", "빠른 링크 메뉴", "Win + X", "시스템"),
    ("win+a", "빠른 설정", "Win + A", "시스템"),
    ("win+n", "알림 센터", "Win + N", "시스템"),
    ("win+v", "클립보드 기록", "Win + V", "시스템"),
    ("win+.", "이모지 패널", "Win + .", "시스템"),
    ("win+s", "검색", "Win + S", "시스템"),
    ("win+k", "화면 전송(캐스트)", "Win + K", "시스템"),
    ("win+p", "화면 표시 모드", "Win + P", "시스템"),
    ("win+g", "게임 바", "Win + G", "시스템"),
    ("win+h", "음성 입력", "Win + H", "시스템"),
    ("printscreen", "전체 화면 캡처", "PrtSc", "시스템"),
    ("alt+printscreen", "활성 창 캡처", "Alt + PrtSc", "시스템"),
    ("win+printscreen", "화면을 파일로 저장", "Win + PrtSc", "시스템"),
    # 편집
    ("ctrl+x", "잘라내기", "Ctrl + X", "편집"),
    ("ctrl+a", "모두 선택", "Ctrl + A", "편집"),
    ("ctrl+y", "다시 실행", "Ctrl + Y", "편집"),
    ("ctrl+shift+z", "다시 실행(Shift)", "Ctrl + Shift + Z", "편집"),
    ("ctrl+s", "저장", "Ctrl + S", "편집"),
    ("ctrl+o", "열기", "Ctrl + O", "편집"),
    ("ctrl+p", "인쇄", "Ctrl + P", "편집"),
    ("ctrl+b", "굵게", "Ctrl + B", "편집"),
    ("ctrl+i", "기울임", "Ctrl + I", "편집"),
    ("ctrl+u", "밑줄 · 페이지 소스", "Ctrl + U", "편집"),
    ("ctrl+backspace", "앞 단어 지우기", "Ctrl + Backspace", "편집"),
    ("ctrl+home", "문서 맨 앞으로", "Ctrl + Home", "편집"),
    ("ctrl+end", "문서 맨 끝으로", "Ctrl + End", "편집"),
    ("ctrl+left", "단어 단위 왼쪽 이동", "Ctrl + ←", "편집"),
    ("ctrl+right", "단어 단위 오른쪽 이동", "Ctrl + →", "편집"),
    ("ctrl+shift+v", "서식 없이 붙여넣기", "Ctrl + Shift + V", "편집"),
    ("delete", "삭제", "Del", "편집"),
    ("backspace", "한 글자 지우기", "Backspace", "편집"),
    # 파일 탐색기
    ("f2", "이름 바꾸기", "F2", "파일 탐색기"),
    ("alt+up", "상위 폴더로", "Alt + ↑", "파일 탐색기"),
    ("alt+enter", "속성 보기", "Alt + Enter", "파일 탐색기"),
    ("ctrl+e", "검색 상자로", "Ctrl + E", "파일 탐색기"),
    ("alt+d", "주소 표시줄로", "Alt + D", "파일 탐색기"),
    # 발표·문서
    ("right", "다음 슬라이드·페이지 (→)", "→", "발표·문서"),
    ("left", "이전 슬라이드·페이지 (←)", "←", "발표·문서"),
    ("up", "위로 (↑)", "↑", "발표·문서"),
    ("down", "아래로 (↓)", "↓", "발표·문서"),
    ("shift+f5", "현재 슬라이드부터 시작", "Shift + F5", "발표·문서"),
    ("b", "슬라이드쇼 화면 검게", "B", "발표·문서"),
    ("w", "슬라이드쇼 화면 하얗게", "W", "발표·문서"),
    # 화상회의 (각 앱의 기본 단축키)
    ("alt+a", "Zoom 음소거 켜기/끄기", "Alt + A", "화상회의"),
    ("alt+v", "Zoom 카메라 켜기/끄기", "Alt + V", "화상회의"),
    ("alt+y", "Zoom 손 들기", "Alt + Y", "화상회의"),
    ("ctrl+shift+m", "Teams 음소거 켜기/끄기", "Ctrl + Shift + M", "화상회의"),
    ("ctrl+shift+o", "Teams 카메라 켜기/끄기", "Ctrl + Shift + O", "화상회의"),
    ("ctrl+shift+k", "Teams 손 들기", "Ctrl + Shift + K", "화상회의"),
    # 개발 (VS Code 등)
    ("ctrl+shift+p", "명령 팔레트", "Ctrl + Shift + P", "개발"),
    ("ctrl+/", "주석 켜기/끄기", "Ctrl + /", "개발"),
    ("ctrl+g", "줄 이동", "Ctrl + G", "개발"),
    ("ctrl+shift+f", "전체 파일 검색", "Ctrl + Shift + F", "개발"),
    ("f9", "중단점 켜기/끄기", "F9", "개발"),
    ("f10", "디버그 한 줄 실행", "F10", "개발"),
]

# More YouTube / media keys (same shape as action_bindings.YOUTUBE_KEY_PRESETS)
EXTRA_YOUTUBE_KEY_PRESETS: Dict[str, Dict[str, Any]] = {
    "yt_frame_back": {"keys": [","], "label": "유튜브 한 프레임 뒤로 (,)", "badge": "YT 프레임- (,)"},
    "yt_frame_forward": {"keys": ["."], "label": "유튜브 한 프레임 앞으로 (.)", "badge": "YT 프레임+ (.)"},
    "yt_miniplayer": {"keys": ["i"], "label": "유튜브 미니 플레이어 (I)", "badge": "YT 미니 (I)"},
    "yt_start": {"keys": ["0"], "label": "유튜브 처음으로 (0)", "badge": "YT 처음 (0)"},
    "yt_end": {"keys": ["end"], "label": "유튜브 영상 끝으로 (End)", "badge": "YT 끝 (End)"},
    "media_next": {"keys": ["nexttrack"], "label": "시스템 다음 곡", "badge": "다음 곡"},
    "media_prev": {"keys": ["prevtrack"], "label": "시스템 이전 곡", "badge": "이전 곡"},
    "media_stop": {"keys": ["stop"], "label": "시스템 미디어 정지", "badge": "미디어 정지"},
}


def hotkey_presets(existing: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """The existing presets (given a group) followed by the extra ones; a value is never listed twice."""
    out: List[Dict[str, str]] = []
    seen = set()
    for item in existing:
        value = item["value"]
        if value in seen:
            continue
        seen.add(value)
        out.append({**item, "group": item.get("group") or EXISTING_HOTKEY_GROUP.get(value, "기본 키")})
    for value, name, shown, group in _EXTRA_HOTKEYS:
        if value in seen:
            continue
        seen.add(value)
        out.append({"value": value, "label": f"{name} ({shown})", "badge": shown, "group": group})
    return out


# ---------------------------------------------------------------------------------------------------------------
# Recipes. kind "motion": hand shape + direction. kind "composite": a pinch/shape + a trigger.
# (posture, direction) pairs are all different from each other and from the built-in motions (open hand left / right /
# down, thumb-tucked left / right).
# caution: the shape is also a built-in sign that works when held still (V = control on/off, rock = close window,
# thumb up = motion mode), so these have to be made as quick swipes.
RECIPE_GROUPS: List[str] = ["브라우저", "창·화면", "미디어", "발표·문서", "시스템", "편집"]
_CAUTION = "V·락·엄지 위 모양은 가만히 유지하면 기본 사인(제어 켜기·끄기, 창 닫기, 모션 모드)으로도 읽혀요. 빠르게 쓸어서 쓰세요."


def _m(group, name, posture, direction, atype, value, caution=False):
    return {"kind": "motion", "group": group, "name": name, "posture": posture, "direction": direction,
            "action_type": atype, "action_value": value, "note": _CAUTION if caution else ""}


def _c(group, name, posture, trigger, atype, value, note=""):
    return {"kind": "composite", "group": group, "name": name, "posture": posture, "trigger": trigger,
            "action_type": atype, "action_value": value, "note": note}


RECIPES: List[Dict[str, Any]] = [
    # 브라우저
    _m("브라우저", "주먹 ↓ 새로고침", "fist", "down", "hotkey", "f5"),
    _m("브라우저", "주먹 ↑ 페이지 맨 위", "fist", "up", "hotkey", "home"),
    _m("브라우저", "주먹 ← 뒤로 가기", "fist", "left", "hotkey", "alt+left"),
    _m("브라우저", "주먹 → 앞으로 가기", "fist", "right", "hotkey", "alt+right"),
    _m("브라우저", "V ↑ 새 탭", "v_sign", "up", "hotkey", "ctrl+t", True),
    _m("브라우저", "V ↓ 페이지 맨 아래", "v_sign", "down", "hotkey", "end", True),
    _m("브라우저", "엄지 접고 ↑ 탭 닫기", "thumb_tucked", "up", "hotkey", "ctrl+w"),
    _m("브라우저", "엄지 접고 ↓ 닫은 탭 복구", "thumb_tucked", "down", "hotkey", "ctrl+shift+t"),
    _m("브라우저", "손 펴고 ↑ 전체화면", "open_hand", "up", "hotkey", "f11"),
    _m("브라우저", "엄지 위 ↑ 화면 확대", "thumbs_up", "up", "hotkey", "ctrl+plus", True),
    _m("브라우저", "엄지 위 ↓ 화면 축소", "thumbs_up", "down", "hotkey", "ctrl+minus", True),
    # 창·화면
    _m("창·화면", "V ← 창 왼쪽 절반", "v_sign", "left", "hotkey", "win+left", True),
    _m("창·화면", "V → 창 오른쪽 절반", "v_sign", "right", "hotkey", "win+right", True),
    _m("창·화면", "락 ↑ 창 최대화", "rock_sign", "up", "hotkey", "win+up", True),
    _m("창·화면", "락 ↓ 창 최소화", "rock_sign", "down", "hotkey", "win+down", True),
    _m("창·화면", "락 ← 이전 가상 데스크톱", "rock_sign", "left", "hotkey", "ctrl+win+left", True),
    _m("창·화면", "락 → 다음 가상 데스크톱", "rock_sign", "right", "hotkey", "ctrl+win+right", True),
    _m("창·화면", "손 펴고 ↗ 작업 보기", "open_hand", "diag_up_right", "hotkey", "win+tab"),
    _m("창·화면", "손 펴고 ↖ 창 전환", "open_hand", "diag_up_left", "hotkey", "alt+tab"),
    _m("창·화면", "손 펴고 ↙ 바탕화면 보기", "open_hand", "diag_down_left", "hotkey", "win+d"),
    _m("창·화면", "손 펴고 ↘ 모든 창 최소화", "open_hand", "diag_down_right", "hotkey", "win+m"),
    # 미디어
    _m("미디어", "엄지 접고 ↗ 볼륨 올리기", "thumb_tucked", "diag_up_right", "youtube_key", "media_vol_up"),
    _m("미디어", "엄지 접고 ↙ 볼륨 내리기", "thumb_tucked", "diag_down_left", "youtube_key", "media_vol_down"),
    _m("미디어", "주먹 ↗ 다음 곡", "fist", "diag_up_right", "youtube_key", "media_next"),
    _m("미디어", "주먹 ↖ 이전 곡", "fist", "diag_up_left", "youtube_key", "media_prev"),
    _m("미디어", "주먹 ↙ 재생/일시정지", "fist", "diag_down_left", "youtube_key", "media_play_pause"),
    _m("미디어", "주먹 ↘ 음소거", "fist", "diag_down_right", "youtube_key", "media_vol_mute"),
    _m("미디어", "V ↗ 유튜브 10초 앞으로", "v_sign", "diag_up_right", "youtube_key", "yt_forward_10", True),
    _m("미디어", "V ↖ 유튜브 10초 뒤로", "v_sign", "diag_up_left", "youtube_key", "yt_rewind_10", True),
    _m("미디어", "V ↙ 유튜브 재생/정지", "v_sign", "diag_down_left", "youtube_key", "yt_play_pause", True),
    _m("미디어", "V ↘ 유튜브 전체화면", "v_sign", "diag_down_right", "youtube_key", "yt_fullscreen", True),
    # 발표·문서
    _m("발표·문서", "락 ↗ 슬라이드쇼 시작", "rock_sign", "diag_up_right", "hotkey", "f5", True),
    _m("발표·문서", "락 ↖ 현재 슬라이드부터", "rock_sign", "diag_up_left", "hotkey", "shift+f5", True),
    _m("발표·문서", "락 ↘ 슬라이드쇼 화면 검게", "rock_sign", "diag_down_right", "hotkey", "b", True),
    _m("발표·문서", "락 ↙ 슬라이드쇼 끝내기", "rock_sign", "diag_down_left", "hotkey", "esc", True),
    _m("발표·문서", "엄지 위 → 다음 슬라이드", "thumbs_up", "right", "hotkey", "right", True),
    _m("발표·문서", "엄지 위 ← 이전 슬라이드", "thumbs_up", "left", "hotkey", "left", True),
    # 시스템
    _m("시스템", "엄지 위 ↗ 화면 영역 캡처", "thumbs_up", "diag_up_right", "hotkey", "win+shift+s", True),
    _m("시스템", "엄지 위 ↖ 작업 관리자", "thumbs_up", "diag_up_left", "hotkey", "ctrl+shift+esc", True),
    _m("시스템", "엄지 위 ↙ 클립보드 기록", "thumbs_up", "diag_down_left", "hotkey", "win+v", True),
    _m("시스템", "엄지 위 ↘ 화면 잠금", "thumbs_up", "diag_down_right", "hotkey", "win+l", True),
    # 편집
    _m("편집", "엄지 접고 ↖ 실행 취소", "thumb_tucked", "diag_up_left", "hotkey", "ctrl+z"),
    _m("편집", "엄지 접고 ↘ 다시 실행", "thumb_tucked", "diag_down_right", "hotkey", "ctrl+y"),
    # 두 번 집기 · 엄지 접고 쓸기 (composite)
    _c("브라우저", "엄지+검지 두 번 집기 → 새로고침", "thumb_index", "double_pinch", "hotkey", "f5"),
    _c("미디어", "엄지+중지 두 번 집기 → 재생/일시정지", "thumb_middle", "double_pinch", "youtube_key", "media_play_pause"),
    _c("미디어", "엄지+약지 두 번 집기 → 음소거", "thumb_ring", "double_pinch", "youtube_key", "media_vol_mute"),
    _c("창·화면", "엄지+새끼 두 번 집기 → 바탕화면 보기", "thumb_pinky", "double_pinch", "hotkey", "win+d"),
    _c("미디어", "엄지 접고 유지한 채 ↑ 볼륨 올리기", "thumb_tucked", "swipe_up", "youtube_key", "media_vol_up"),
    _c("미디어", "엄지 접고 유지한 채 ↓ 볼륨 내리기", "thumb_tucked", "swipe_down", "youtube_key", "media_vol_down"),
]


def recipes_of(kind: str) -> List[Dict[str, Any]]:
    return [dict(r, id=f"{kind}-{i}") for i, r in enumerate(x for x in RECIPES if x["kind"] == kind)]


def badge_from_label(label: str) -> str:
    match = re.search(r"\(([^)]*)\)\s*$", label)
    return match.group(1) if match else label
