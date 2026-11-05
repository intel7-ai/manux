# MANUX v0.4 모듈 계약

> 상태: **합의안 초안 (2026-11-05)**. 아직 이 계약을 구현한 기능 코드는 없다. 타입과 프로토콜은 [`src/manux/core/contracts.py`](../src/manux/core/contracts.py)에 코드로 있고 [`tests/contract/`](../tests/contract/)가 검사한다.
> **문서와 계약 코드가 다르면 둘 다 틀린 것이다.** 같은 PR에서 함께 고친다. 각 절의 "출처"는 v0.3(`hand-mouse/`)에서 옮겨 올 실제 코드다.

표기:
- **계약**: v0.4 모듈이 지켜야 하는 것. 계약 코드에 있다.
- **제안**: 아직 합의하지 않은 것. 구현 전에 이 문서에서 확정한다.
- **실패하면**: 예외를 던지는가 / `None`을 주는가 / `accepted=False`를 주는가 / 빈 목록을 주는가. 같은 타입이라도 "빈 결과"와 "실패"는 다른 계약이다.

## 1. 공통 규칙

| 항목 | 규칙 |
| --- | --- |
| 시간 `t` | `time.perf_counter()` 초. 카메라 프레임을 받은 순간에 한 번 정하고 그 프레임의 모든 계층이 같은 값을 쓴다. 단조 증가 |
| 영상 좌표 | 0..1 정규화. x 오른쪽, y 아래. 좌우 반전·회전을 **적용한 뒤**의 영상 기준. z는 MediaPipe 값(손목 기준, 영상 폭 단위) |
| 실세계 좌표 `world` | MediaPipe world landmarks, 미터, 손 중심 원점 |
| 관절 번호 | MediaPipe 21점 순서. 0 손목, 4 엄지 끝, 8 검지 끝, 12 중지 끝, 16 약지 끝, 20 새끼 끝 |
| 손 방향 | `"Left"` / `"Right"`는 **실제 손**. 반전 여부로 바꾸는 일은 `tracking`이 끝낸다 |
| 동작 ID | `<기능 ID>.<이름>`, 소문자·숫자·`_`. 예: `pinch.thumb_middle` |
| 액션 ID | `<영역>.<이름>`. 예: `mouse.left_click`, `nav.back` |
| 불변 | 계층 사이에 넘기는 값은 모두 `frozen` dataclass다. 받은 값을 고치지 않고 새 값을 만든다 (v0.3 TCN 확인기는 `result.action`을 직접 바꿨다) |

## 2. 프레임: `FrameInfo`

출처: `camera.py`(`CameraStream.read`, `actual_mode`), `qt_runtime.py`(`frame_aspect`).

| 필드 | 형식 | 뜻 |
| --- | --- | --- |
| `seq` | int ≥ 0 | 엔진이 처리한 프레임 번호 |
| `t` | float | §1의 시간 |
| `width`, `height` | int > 0 | 반전·회전 적용 후 크기 |
| `mirrored` | bool | 좌우 반전 적용 여부 |
| `rotation` | 0 / 90 / 180 / 270 | 적용한 회전 |
| `aspect` (계산) | float | `height / width` |

실패하면: 잘못된 값으로 만들면 `ValueError`. 카메라가 프레임을 못 주면 `FrameInfo`를 만들지 않고 엔진이 "프레임 없음"으로 처리한다(§6 `reset("frame_missing")`은 하지 않고, 손 소실 시간만 센다).

## 3. 손: `HandObservation`

출처: `hand_tracker.py`(`HandDetection`, `HandLandmark`), `hand_selection.py`, `hand_crop.py`. `world`는 Dan 브랜치에서 추가됨.

| 필드 | 형식 | 뜻 |
| --- | --- | --- |
| `frame` | `FrameInfo` | 이 손이 나온 프레임 |
| `landmarks` | 21 × `(x, y, z)` | §1 영상 좌표 |
| `world` | 21 × `(x, y, z)` 또는 `None` | §1 실세계 좌표. 모델이 주지 않으면 `None` |
| `handedness` | `"Left"` / `"Right"` | 실제 손 |
| `score` | 0..1 | MediaPipe 손 신뢰도 |
| `from_crop` | bool | 손 주변 확대 영상에서 찾았는가(좌표는 이미 전체 영상 기준으로 되돌림) |

규칙:
- 엔진에는 **조작 손 하나만** 들어간다. 손 선택(`HandSelection`)은 `tracking`이 끝낸다. 손이 없으면 `None`.
- 실패하면: 관절이 21개가 아니거나 숫자가 유한하지 않거나 `score`가 범위 밖이면 생성 시 `ValueError`. `tracking`은 이런 검출을 버리고 `None`을 넘긴다.

## 4. 특징 (`perception`)

출처: Dan `hand-mouse-gesture-overhaul@7448e4a`의 `src/tcn_features.py`, v0.3 `motion_mode.hand_features`. (Dan 브랜치 `palm_axes.py`는 커서 방향 보정이므로 특징이 아니라 `pointer` 기능 쪽이다.)

**계약:**
- 특징 함수는 순수 함수다. 같은 입력이면 같은 출력. 상태가 필요하면(속도 등) 이전 프레임 목록을 인자로 받는다.
- 특징 묶음마다 **버전 문자열**이 있다. 현재 하나: `local3d-v1`.
- `local3d-v1` (133개/프레임): 손목 원점, Y축 = 손목→중지 뿌리, X축 = 새끼 뿌리→검지 뿌리에서 Y 성분을 뺀 방향, Z = X × Y, 손목~중지 뿌리 길이로 나눔. 정규화 3D 좌표 63 + 직전 프레임 대비 변화 63 + 엄지 끝~네 손가락 끝 거리 4 + 영상 안 손바닥 이동 속도 2 + 손 있음 1.
- 공식을 조금이라도 바꾸면 버전을 올린다(`local3d-v2`). 같은 버전 이름으로 값이 바뀌면 학습된 모델이 조용히 틀린다.

실패하면: `world`가 없으면 그 프레임의 3D 특징은 0, "손 있음"은 0. 예외 없음.

## 5. 기능 모듈 (`Feature`)

출처: v0.3 `PointerEngine.step`, `GestureEngine.process`, `MotionMode.update`.

```python
class Feature(Protocol):
    id: str                           # "pointer", "pinch", "motion" ...
    modes: frozenset[str]             # 이 기능이 동작하는 중재 모드
    gestures: tuple[GestureId, ...]   # 낼 수 있는 동작 ID 전부
    def step(self, hand: HandObservation | None, ctx: FrameContext) -> Sequence[Intent]: ...
    def activity(self) -> Activity: ...
    def reset(self, reason: str) -> None: ...
```

### `Intent` (기능이 낸 의도)

| 필드 | 형식 | 뜻 |
| --- | --- | --- |
| `t` | float | 프레임 시간 |
| `source` | str | 낸 기능 ID |
| `gesture` | 동작 ID | `gestures`에 선언된 것만 |
| `phase` | `tap` / `begin` / `hold` / `end` | 한 번 / 시작 / 유지(매 프레임) / 끝 |
| `point` | `(x, y)` 0..1 또는 `None` | 보정된 기준점(포인터 이동·드래그·스크롤). 화면 픽셀 변환은 출력 계층 몫 |
| `confidence` | 0..1 또는 `None` | 있으면 |
| `info` | 읽기 전용 dict | 진단용. 출력 동작을 바꾸는 데 쓰지 않는다 |

### `Activity` (중재기에 공개하는 상태)

| 필드 | 뜻 |
| --- | --- |
| `holding` | 누름·드래그·스크롤이 진행 중 |
| `last_release_t` | 마지막으로 끝난 시각(없으면 `-inf`) |

### 규칙

1. `step`은 한 프레임에 한 번, 엔진 스레드에서만 불린다. 손이 없으면 `hand=None`으로 부른다(손 소실 유예 판정을 기능이 하도록).
2. `begin`을 낸 동작은 반드시 `end`를 낸다. `reset()`이 불리면 기능은 상태를 지우고, 열린 `begin`의 정리는 엔진이 출력 싱크의 `release_all()`로 대신한다.
3. 기능은 다른 기능을 import하지 않는다. 다른 기능의 상태는 `ctx.others`(기능 ID → `Activity`)로만 본다. 비공개 필드(`_` 시작)를 읽지 않는다.
4. 기능은 Windows 입력·Qt·카메라를 쓰지 않는다(구조 시험).
5. `reset(reason)`의 `reason`: `hand_lost` · `stage_change` · `mode_change` · `pause` · `settings` · `shutdown`.

실패하면: `step`이 예외를 던지면 엔진은 그 프레임의 그 기능 결과를 버리고, `release_all("error")`을 부르고, 기능을 `reset("error")`한 뒤 실패 기록을 남긴다. 앱은 멈추지 않는다. 같은 기능이 연속으로 실패할 때 끄는 기준은 **제안**(예: 1초에 3회).

### `FrameContext` (엔진이 기능에 주는 것)

| 필드 | 뜻 |
| --- | --- |
| `frame` | `FrameInfo` |
| `stage` | §8 단계 |
| `output` | §8 출력 대상 |
| `mode` | 중재 모드 (`pointer` / `motion` …) |
| `settings` | 이 기능의 설정 값(`<id>.` 접두사를 뗀 것) |
| `others` | 다른 기능들의 `Activity` |

### 중재기 (Arbiter)

출처: `qt_runtime._motion`, `_motion_blocked`, 포인터/제스처 분기.

- 한 순간의 모드는 하나다. 모드에 속한 기능만 `step`이 불린다(나머지는 `hand=None`도 받지 않는다).
- 모드 전환 순서: 출력 `release_all("mode_change")` → 이전 모드 기능 `reset("mode_change")` → 새 모드 시작.
- 모드 진입 요청은 기능이 `Intent(gesture="<id>.enter")`로 내거나 UI 명령으로 온다. 진입을 막는 조건(예: 어떤 기능이 `holding`이거나 `last_release_t`가 `motion.enter_quiet`초 안)은 중재기가 `others`로 판단한다.
- 구현(`engine/arbiter.py`): 기본 모드는 주 입력 방식(`pointer` 또는 `pinch`), 덧씌우는 모드는 그 모드를 가진 기능 ID와 같은 이름이다(예: `motion`). 기능은 `<id>.enter`로 들어가고 `<id>.exit`로 나온다. 이 두 의도는 `gestures`에 선언하지 않아도 되고, 중재기가 받아서 **라우터로 보내지 않는다**. 거절도 Snapshot `events`에 `refused:<이유>`로 남는다. UI 요청(`RequestMode`)은 조용함 조건 없이 바로 바꾼다.
- 진입 감시가 필요한 기능은 기본 모드도 `modes`에 넣는다(예: `motion.modes = {"pointer", "pinch", "motion"}`).

## 6. 의도 필터 (`IntentFilter`, 예: TCN 확인기)

출처: Dan 브랜치 `src/gesture_gate.py`.

```python
class IntentFilter(Protocol):
    id: str
    def observe(self, hand: HandObservation | None, ctx: FrameContext) -> None: ...
    def filter(self, intents: Sequence[Intent], ctx: FrameContext) -> Sequence[Intent]: ...
    def reset(self, reason: str) -> None: ...
```

**계약:**
- 필터는 의도를 **통과·보류·버림**만 한다. 새 동작을 만들지 않는다. 보류했다가 늦게 통과시키는 것은 허용(같은 `Intent`의 `t`만 현재 프레임으로).
- 보류에는 최대 시간이 있다(`tcn_gate.wait`, v0.3 값 0.3초). 시간이 지나면 버린다. 보류 중인 의도가 프레임마다 쌓이지 않는다.
- 필터가 버린 의도를 다른 경로(규칙 그대로 실행 등)로 우회하지 않는다.
- `begin`을 통과시켰으면 대응하는 `hold`/`end`도 통과시킨다. `begin`을 버렸으면 그 `hold`/`end`도 버린다.

**TCN 확인기 (`tcn_gate`)의 v0.3 동작 (출처 그대로):**

| 규칙이 낸 동작 | 모델 클래스 | 처리 |
| --- | --- | --- |
| 우클릭 | `thumb_ring` | 최근 0.5초 안에 확률 ≥ 0.7이 3프레임 연속이면 통과. 아니면 0.3초 보류 후 확인되면 통과, 안 되면 버림 |
| 스크롤 잡기 | `thumb_index` | 확인될 때까지 시작 보류(커서 멈춤). 한 번 시작하면 끝까지 유지 |
| 그 밖 | — | 그대로 통과 |

v0.4 확인기는 모델 정보 파일의 클래스 → 동작 ID 대응표를 읽는다(§10). 기존 3클래스 모델의 우클릭은 `pinch.thumb_ring`, 스크롤 잡기는 `pinch.scroll`에 연결한다.

실패하면: 모델이 없거나 정보가 맞지 않으면 필터를 **만들지 않는다**(§10). 실행 중 추론 오류는 그 프레임 확률을 "없음"으로 두고 보류 규칙을 그대로 적용한다(확인 안 됨 → 시간이 지나면 버림).

## 7. 액션과 바인딩 (`actions`)

출처: `gesture_engine.VALID_ACTIONS`, `motion_mode.ACTION_KEYS`, `mouse_controller.apply`.

### 액션 목록

| 액션 ID | phase | params | v0.3 이름 |
| --- | --- | --- | --- |
| `mouse.move` | hold | `gain` | `move_cursor` |
| `mouse.left_click` | tap | — | `left_click` |
| `mouse.right_click` | tap | — | `right_click` |
| `mouse.drag` | begin / hold / end | — | `drag_hold`, `drag_toggle` |
| `mouse.scroll` | begin / hold / end | `gain` | `scroll_grab` |
| `window.task_switcher` | tap | — | `task_switcher`, `alt_tab` |
| `window.show_desktop` | tap | — | `show_desktop` |
| `nav.back`, `nav.forward` | tap | — | 모션 `back`, `forward` |
| `tab.prev`, `tab.next` | tap | — | 모션 `tab_prev`, `tab_next` |
| `media.play_pause`, `media.volume_up`, `media.volume_down` | tap | — | **제안** (없음. 침대 사용 R6) |

이 표는 코드 [`actions/catalog.py`](../src/manux/actions/catalog.py)와 같아야 한다(`tests/unit/actions/test_actions.py`가 비교한다). **제안** 줄은 목록에 넣지 않는다.

- `params`(S3 추가, 계약 변경): 바인딩이 줄 수 있는 키. `gain`은 양수 배율(`mouse.move`: 커서 이동 배율, 예: 주먹 빠른 이동을 별도 동작 + `gain`으로 연결 / `mouse.scroll`: 스크롤 배율). 프레임마다 바뀌는 값은 `params`가 아니다.
- `window.task_switcher`는 S3에서 Alt+Tab 한 번(v0.3 `alt_tab`)이다. v0.3의 Alt를 누른 채 손으로 고르는 전환기(`task_switcher`)는 확인·취소 동작이 필요한 모드라 S4(중재기)에서 정한다.

v0.3 `grab`의 실제 용도는 이식 때 확인하고 이 표에 추가한다.

### 바인딩과 라우터

- 바인딩: `동작 ID → 액션 ID`. 기능의 `DEFAULT_BINDINGS` 위에 프로필 값이 덮어쓴다.
- 라우터는 `Intent`를 `ActionCommand`로 바꾼다. `phase`와 `point`는 그대로 옮긴다.
- **바인딩 없는 동작:** 아무것도 실행하지 않는다. Snapshot에 "연결 없음"으로 보이고 실패 기록에 남는다.
- **모르는 액션 ID:** 설정을 읽을 때 거부하고 그 바인딩만 기본값으로 되돌린다(앱은 계속).
- 한 액션의 `phase` 짝이 맞지 않는 바인딩(예: `tap` 동작 → `mouse.drag`)은 읽을 때 거부한다. 동작이 내는 phase를 읽을 때 알 수 없으면 라우터가 실행 때 거부한다(`phase_mismatch`, 명령 없음).

### `ActionCommand`

| 필드 | 뜻 |
| --- | --- |
| `t`, `action`, `phase`, `point` | 위와 같음 |
| `gesture` | 원인이 된 동작 ID (기록용) |
| `params` | 액션별 값(예: 스크롤 배율). 액션 목록에 정의된 키만 |

## 8. 단계와 출력 대상

출처: `qt_runtime.UiState.stage`(`splash | welcome | capture | tutorial | main`), `practice_output()`, `paused`, `calibrating`.

| 단계 / 상태 | 출력 대상 | 뜻 |
| --- | --- | --- |
| `splash`, `welcome` | `none` | 카메라 준비·안내 |
| `capture` (손 선택) | `none` | 모든 손을 보여 주고 조작 손을 고른다 |
| `tutorial` | `practice` | 연습 화면만 이벤트를 받는다. **Windows 커서도 움직이지 않는다**(v0.3 11/04 피드백) |
| `main` + 일시정지 | `none` | |
| `main` + 방향 보정·등록·녹화 중 | `none` | 등록 중 명령 실행 금지(기존 AGENTS.md 기술 경계) |
| `main` + 제어 켬 | `windows` | 유일하게 실제 입력이 나가는 상태 |

### `OutputSink`

```python
class OutputSink(Protocol):
    target: OutputTarget              # none / practice / windows
    def apply(self, cmd: ActionCommand) -> OutputResult: ...
    def release_all(self, reason: str) -> None: ...
    def close(self) -> None: ...
```

**계약:**
- `apply`는 예외를 던지지 않는다. 결과는 `OutputResult(accepted, reason)`. `reason`: `ok` · `unsupported`(이 싱크가 모르는 액션) · `blocked`(안전 규칙) · `error:<설명>`.
- `release_all`은 여러 번 불러도 안전하다. 눌린 버튼·드래그·스크롤 관성을 모두 끝낸다.
- 단계·모드 전환, 일시정지, 손 소실(`hand_lost_reset`초, v0.3 기본 0.3초), 설정 변경, 종료, 기능 오류 때 엔진이 `release_all`을 **먼저** 부른다.
- `begin`이 열려 있지 않은 `end`(예: `release_all` 뒤)는 `ok`이고 아무것도 하지 않는다. `begin` 없는 `hold`는 `blocked`이다. 버튼을 저절로 누르지 않는다(S3 추가).
- `close()` 뒤의 `apply`는 `blocked`이다(S3 추가).
- `windows` 싱크만 실제 입력을 보낸다. 실제 마우스가 움직이면 제어를 멈추는 감시(v0.3 Electron의 watchdog)는 **제안**.

## 9. 학습 녹화 형식

출처: Dan 브랜치 `src/recorder.py` (`schema: "hand-mouse-recording/1"`). v0.4는 이 형식을 **그대로 읽는다**. 쓰기 형식을 바꾸면 `manux-recording/2`로 올리고 읽기 변환을 둔다.

한 줄에 JSON 하나, gzip.

| `type` | 위치 | 내용 |
| --- | --- | --- |
| `meta` | 첫 줄 | `schema`, `mode`(`guided`/`free`), `created`, PC **별명**(`pc`), 카메라(`source`, 실제 해상도·fps, 반전, 메모), 조작 손, 프로필, 그때의 규칙 기준값 |
| `frame` | 프레임마다 | `t`(세션 시작 후 초), `lm`(21×3), `world`(21×3, 있으면), `hand`(`[방향, 점수]`), `pose`(상체 6점, 있으면), `label`(`target`·`phase`·`condition`…), `rule`(규칙 판정), `cursor` |
| `event` | 아무 때 | `kind`: `mark`(사용자가 F로 표시한 오작동), `set_done` 등 |
| `end` | 마지막 줄 | `frames`, `counts`(라벨별), `marks` |

**계약:**
- 영상은 저장하지 않는다. 실제 PC 이름·Windows 사용자 이름·계정 정보를 쓰지 않는다.
- 쓰는 중에는 `.part`로 저장하고 닫을 때 이름을 바꾼다. 학습·시험은 `end` 줄이 있는 파일만 읽는다.
- 녹화 중 출력 대상은 `none`이다(§8).
- 저장 위치는 [D-006](decisions.md#d-006-학습-데이터-위치): v0.4 동안은 팀 공유 허용, 릴리스(기본 모드)부터는 사용자 PC에만.

## 10. 모델 파일

출처: Dan 브랜치 `models/gesture_tcn.onnx` + `models/gesture_tcn.json`, `scripts/train_tcn.py`.

모델 정보 파일(`.json`)의 필수 키:

| 키 | 뜻 |
| --- | --- |
| `classes` | 출력 순서대로 클래스 이름 |
| `window` | 입력 프레임 수 (현재 15) |
| `features` | 프레임당 특징 수 (현재 133) |
| `feature_version` | §4 버전 (현재 `local3d-v1`) |
| `threshold`, `frames_in_a_row` | 확정 기준 (현재 0.7, 3) |
| `trained` | 학습 시각 |
| `data` | 학습에 쓴 녹화 목록(파일, PC 별명, 카메라, 프레임 수) |
| `held_out_results` | 학습에 쓰지 않은 자세(또는 사람)의 평가 결과 |

**계약:**
- `ml`은 읽기 전에 검사한다: 필수 키, `feature_version`이 앱의 특징 버전과 같음, ONNX 입력 모양이 `(1, window, features)`, 출력 크기가 `len(classes)`.
- 하나라도 틀리면 모델을 읽지 않고 그 모델을 쓰는 기능·필터를 끈다. Snapshot에 이유("모델 없음", "특징 버전 다름")를 보인다. 규칙만으로 동작하는 v0.3 방식으로 계속 간다.
- 모델 종류는 [D-005](decisions.md#d-005-기본-모델과-개인-모델)를 따른다: 릴리스에 들어가는 **기본 모델**과 사용자가 학습하는 **개인 모델**.
- 확인기가 읽는 선택 필드는 `kind`(`base` / `personal`)와 `gestures`(클래스 → 동작 ID 대응표)다. 기존 정보 파일은 `kind=base`로 읽는다. 정보 파일에 액션 ID를 넣지 않는다(액션은 바인딩, §7).
- **제안:** 개인 모델은 자기가 선언한 동작에 대해서만 기본 모델보다 먼저 쓰인다. 개인 모델이 거절한 입력은 기본 모델로 다시 판정하지 않는다.

## 11. 설정과 프로필

출처: `config/gestures.json`, `gesture_engine.load_config/save_config/set_profile`, `profiles.py`, `app_paths.py`, Qt `QSettings`(화면 선택 값).

**계약:**
- 설정 키는 `<기능 ID>.<이름>` 또는 공용 `app.<이름>`, `camera.<이름>`, `tracking.<이름>`. 기능의 기본값은 그 기능의 `SETTINGS`에 있다.
- 설정 파일은 `<설정 폴더>/settings.json` = `{"version": 1, "values": {키: 값}}`이다. 읽을 때 이전 버전을 변환한다.
- v0.3 변환(`settings/migrate_v03.py`): 주인이 있는 키는 표(`RENAMES`)대로 새 키로 바꾸고, 나머지는 `v03.<키>`로 그대로 보존한다. 기능을 옮길 때 그 기능의 v0.3 키를 표에 추가한다. v0.3 파일은 읽기만 하고 고치지 않는다.
- 잘못된 값(타입·범위)은 그 키만 기본값으로 되돌리고 경고를 남긴다. 앱은 멈추지 않는다. 모르는 키는 지우지 않고 보존한다.
- 저장은 임시 파일에 쓰고 이름을 바꾸는 방식으로 한다. 저장이 실패해도 이전 파일은 그대로다.
- 프로필은 사용자 값만 덮어쓴다. 프로필 이름 규칙은 v0.3 `profiles.NAME_RE`를 따른다.
- 사용자 폴더(`settings/paths.py`): 설치본 `%APPDATA%/MANUX/v04`, 소스 실행 `%APPDATA%/MANUX-dev/v04`, `MANUX_DATA_DIR`가 있으면 그 폴더. v0.3 설치본(`%APPDATA%/MANUX`)과 섞이지 않는다. 저장소 안에 사용자 설정을 쓰지 않는다.
- 공용 계층의 설정(`app.`, `camera.`, `tracking.`)은 `settings/builtin.py`에 선언한다. 계층 규칙상 `capture`·`tracking`은 `settings`를 import할 수 없으므로 조립 지점이 값을 넘긴다.

## 12. 화면과 엔진 사이 (`Snapshot`, `UiCommand`)

출처: `qt_runtime.Snapshot`, `UiState`, `qt_dashboard.py`.

- 엔진은 프레임마다 **불변** `Snapshot`을 하나 만든다. UI는 가장 최근 것만 읽는다. 지난 Snapshot을 쌓지 않는다.
- `Snapshot`에 들어가는 것(**제안**, S6에서 확정): 프레임 번호, 미리보기 영상(화면이 보일 때만), 손 관절(그리기용), 단계·모드·출력 대상, 최근 동작/액션과 결과, 기능별 상태 문구, 카메라 정보·밝기·어두움 경고, fps·단계별 처리 시간, 모델 상태.
- 현재 구현(`engine/snapshot.py`, 엔진 부분만): 프레임 번호·시각, 단계, 출력 대상, 모드, 제어 켬, 바쁨(녹화 등), 손 있음, 필터 뒤 의도, 라우팅 안 된 의도와 이유, 출력 결과, 기능별 `holding`, 이벤트(`hand_lost`, `mode:…`, `mark:…`), 오류. 영상·관절·카메라 정보·모델 상태는 조립 지점의 실행기(S6)가 덧붙인다.
- `UiCommand`(`engine/commands.py`): `SetStage`, `SetControl`, `SetBusy`(`calibrating`/`enrolling`/`recording`), `RequestMode`, `SetBaseMode`, `Mark`.
- UI → 엔진은 `UiCommand` 큐로만 보낸다. 예: 단계 바꾸기, 제어 켜기/끄기, 조작 손 바꾸기, 설정 값 바꾸기, 바인딩 바꾸기, 녹화 시작/끝, "방금 이상했어요" 표시. UI는 엔진 객체의 필드를 직접 바꾸지 않는다.
- UI는 판정 값을 계산하지 않는다. 화면에 보일 문구가 판정에서 나와야 하면 엔진이 Snapshot에 넣는다.

## 13. 계약을 바꾸는 절차

1. 이 문서와 `core/contracts.py`를 **먼저** 같은 PR에서 고친다. 구현은 같은 PR 또는 다음 PR.
2. PR 본문에 "계약 변경"과 영향받는 계층·기능 ID를 적는다.
3. `tests/contract/`와 `tests/architecture/`를 실행해 결과를 PR에 적는다.
4. **제안**을 **계약**으로 바꿀 때는 [결정 기록](decisions.md)에 한 줄 남긴다.
