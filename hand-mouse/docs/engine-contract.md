# 포인터 엔진 계약 (이식용)

> **v0.4 (2026-11-05):** 이 엔진은 `apps/manux/`의 `pointer` 기능으로 옮긴다([구조 §5·§8](../../apps/manux/docs/architecture.md), [계약](../../apps/manux/docs/contracts.md)). 아래 입력·출력과 24,000프레임 비교가 그 이식의 비교 기준이다. 새 기능은 v0.4 계약을 따른다.

`src/pointer_engine.py`의 `PointerEngine`은 Qt 검지 포인터 모드의 판정 로직 전체다. 카메라, MediaPipe, Qt, OpenCV, OS 입력 코드가 없다. 다른 셸(예: Tauri + TypeScript)로 옮길 때 이 문서의 입력·출력과 동작 규칙을 맞추고, 같은 사례로 시험한다.

## 계층

| 계층 | 지금 (Python) | 옮길 때 |
| --- | --- | --- |
| 카메라·손 인식 | `camera.py`, `hand_tracker.py`(MediaPipe Python, `hand_crop.py`) | MediaPipe Tasks JS(WebGL/WebGPU), 같은 21점 |
| **판정 (이 계약)** | `pointer_engine.py` + `pointer_workspace.py` + `gesture_engine.py`(핀치) | 그대로 이식 |
| 커서 이동·클릭 | `mouse_controller.py`(pyautogui/SetCursorPos, `screens.py`) | Rust `SendInput` 등 |
| 화면 | `qt_dashboard.py`, `dashboard.qml` | 웹 UI |
| 기록·측정 | `failure_log.py`, `tracking_test.py` | 그대로 이식 가능(순수 로직) |

## 입력 (프레임마다 `step`)

| 이름 | 형식 | 뜻 |
| --- | --- | --- |
| `t` | 초(단조 증가) | 프레임 시각 |
| `landmarks` | 21개 `{x, y, z}` 또는 없음 | 선택된 손 하나. x·y는 카메라 영상 0..1(미러 적용 후), z는 영상 폭 단위. MediaPipe 순서(0 손목, 4 엄지 끝, 8 검지 끝 …) |
| `aspect` | 실수 | 영상 높이 ÷ 폭 |
| `settings.click_style` | `"pinch"` / `"trigger"` / `"off"` | 클릭 방식 |
| `settings.anchor` | `"index"` / `"palm"` | 커서가 따라갈 점 |
| `settings.tracking_only` | 참/거짓 | 트래킹 측정 중(클릭 없음) |
| `settings.axes` | 4개 실수 또는 없음 | 방향 보정(`pointer_axes`) |
| `settings.config` | 키-값 | 기준값(`config/gestures.json`, `pointer_move_*`, `trigger_*` …) |
| `active` | 참/거짓 | 제어 중(일시정지·방향 보정 중이면 거짓) |
| `detecting` | 참/거짓 | 실제 카메라 영상을 분석 중 |

추가 호출: `record_cursor(t, 화면좌표)`(커서 적용 뒤, 방아쇠 되감기용), `frame_missing()`(카메라 프레임 없음), `reset()`(포인터 모드 아님).

## 출력 (`PointerFrame`, 손이 없으면 없음)

| 이름 | 뜻 |
| --- | --- |
| `action` | `move_cursor`, `pointer_wait`(커서 고정), `pointer_release`(손끝을 떼면 클릭), `left_click`, `right_click`, `drag_hold`. 비활성이면 없음 |
| `cursor_point` | 움직일 때만: 보정된 기준점(0..1). 화면 좌표 변환·가속은 커서 계층 몫 |
| `pointer_point` | 보정된 기준점(항상) |
| `pointer_source` | `"index"` / `"palm"`(커서 계층의 이득 선택) |
| `warp` | 있으면 먼저 커서를 이 화면 좌표로(방아쇠 누름 직전 위치) |
| `preview_point`, `contacts` | 그리기·진단용 |
| `latched`, `speed`, `click_armed`, `trigger_ratio` | 상태 표시용 |

## 동작 규칙 (우선순위 순)

1. **클릭 끔 / 트래킹 측정:** 손 모양과 무관하게 `move_cursor`, 클릭 판정 없음.
2. **이동 유지:** 기준점 속도(0.1초 창, 영상 폭/초) ≥ `pointer_move_speed`면 유지 시작, `pointer_still_speed` 미만으로 `pointer_move_latch`초 지나야 끝. 유지 중에는 `move_cursor`, 클릭 판정 없음, 끝나면 클릭 준비 해제. 방아쇠 누름·드래그 중에는 유지를 걸지 않는다. 짧은 손 소실은 유지를 끝내지 않는다.
3. **클릭 준비:** 유지가 끝났거나 손을 놓친 뒤에는, 엄지가 손가락에서 한 번 떨어져야 클릭을 받는다(이미 닿아 있던 접촉은 클릭이 아님).
4. **방아쇠(`trigger`):** `ThumbTrigger` — 접근 시 고정, 누름 시작 시 되감기(`warp`), 짧게 = 좌클릭, 가만히 = 우클릭 1회, 이동 = 드래그.
5. **핀치(`pinch`):** 제스처 엔진의 엄지+중지(좌)/엄지+약지(우) 핀치 → `pointer_release` → 떼면 클릭(`PointerPose`).
6. **튐 방지:** 검지 끝 기준일 때 검지 끝만 홀로 튀면 한 프레임 `pointer_wait`.

## 검증

- `tests/test_pointer_engine.py`: 위 규칙별 사례.
- 분리 전 코드와 비교(2026-11-04): 핀치·방아쇠·끔, 검지·손바닥, 일시정지, 손 소실, 급한 이동을 섞은 24,000프레임에서 출력(동작·커서·되감기·클릭 준비·유지) **불일치 0**. 좌클릭 98, 우클릭 6, 드래그 45, 되감기 127회 포함.
- 이식본도 같은 입력 열을 넣어 같은 출력이 나오는지 확인한다.
