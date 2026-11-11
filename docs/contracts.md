# MANUX v0.4 모듈 계약

## 학습센터 이식 계약 (testing)

- 2026-11-06 통합: 기본 앱과 학습센터는 같은 MANUX 테마의 공용 센터를 사용한다.
  동작별 녹화·공유와 포즈 표본 수집, 모델 보관함을 두 메인 화면에서 연다.
  원격 인증은 worker의 `.env`/환경에서만 읽는다. 토큰 입력·암호화 금고 UI는 이식하지 않는다.
- 모델 보관함은 내용 해시별 불변 파일과 변경 가능한 표시 이름을 구분한다. 업로드·다운로드는
  모델과 카드의 해시·특징·차원 검사를 거친다. 다운로드는 active를 바꾸지 않는다.
  TCN 시험 선택은 강제 shadow이며 기존 active가 있어도 가능하다. 실제 적용·복원은
  현재 별명으로 별도 녹화한 전체 eval과 기존 오실행 회귀 검사 후 원자적 설치한다.
  모델 이름·원격 경로를 로컬 파일 경로로 사용하지 않는다. 실패·취소는 기존 모델을 유지한다.
- Spatial MLP는 `spatial-mlp-v1` 96차원 영상 관절 특징의 별도 실험이다. `local3d-v1`
  TCN 특징·모델을 대체하지 않는다. 빠른 프레임 분할 점수는 독립 사용자 정확도가 아니다.
  학습은 UI를 막지 않고 수행하며 수집 중 Windows 입력을 차단한다. 보조 거부권은 기본 꺼짐,
  학습만으로 실제 제어에 자동 적용하지 않는다. 모델 변경은 엔진 프레임 경계에서 처리한다.

- `--ui studio`는 Rasp-Jun 원본 QML의 메인·학습센터·자세 설정·진행 페이지를 사용한다.
  선택은 로컬 `ui.ini`에 보존한다. `--ui standard`로 팀 공용 화면을 선택할 수 있다.
  두 화면 모두 같은 v0.4 런타임·녹화·학습 서비스를 사용한다.
- 원본 studio의 메인 카메라에는 손 관절·손바닥 기준 링·핀치 표시를 제어 시작 전에도 그린다.
  런타임의 독립된 표시용 PinchCore가 ViewFrame의 preview_guides/anchor/moving을 공급하며
  입력을 만드는 feature·TCN 정책·OS 출력 상태를 변경하지 않는다. UI는 같은 프레임의 복사본에만
  그린다. 제어 꺼짐과 녹화 중 Windows 입력 차단은 그대로 유지한다.
- `RuntimeRequest`의 `preview_model`은 미검증 후보를 강제 shadow로 로드한다.
  `tcn_toggle`의 `on`/`off`는 프레임 경계에서 출력·판정 상태를 해제하고 전역 TCN을
  켜거나 끈다. 동작별 저장 정책은 덮어쓰지 않으며 모델 교체 뒤에도 전역 선택을 유지한다.
  원본의 N 단축키는 `next_monitor` 요청으로 출력 계층의 모니터 전환을 호출한다.
  녹화·보정 중에는 실행하지 않는다.
- 손바닥 방향 보정은 원본 PCA 방식의 순수 perception 서비스다. 보정 중 입력을 차단하고,
  성공한 축만 손·카메라 반전별로 저장한다. 실패·취소는 이전 축을 유지한다.
  보정은 pinch 이동·드래그 좌표에만 적용하며 스크롤 좌표는 변경하지 않는다.
- 이전 스튜디오 가져오기는 로컬에서만 실행하며 원본 녹화·설정·모델을 변경하지 않는다.
  사용자 폴더의 `imports/<id>`에 원본을 보관하고 완료 녹화의 프레임/이벤트는 그대로 가져온다.
  용도 없는 이전 녹화에는 `train`만 부여한다. eval을 만들거나 학습 녹화를 평가용으로 복제하지 않는다.
  이미 저장된 v0.4 자세 계획·설정·카메라 선택은 보존한다. 선택 모델 목록의 override가 기본 모델보다 우선한다.
  변환된 개인 모델은 별도 검증 전에는 강제 shadow이며 OS 의도·Activity·소유권을 내지 않는다.
  저장된 apply 설정으로 이 제한을 우회할 수 없다. 검증은 기존 store 설치 계약을 그대로 따른다.
  가져온 모델이 아직 미검증이어도 선택 재학습에서 미선택 분류기를 보존한다.
- 학습센터의 각 동작에서 집중 녹화 설정과 단일 동작 학습을 제공하고 진행 페이지 진입은 상단에 둔다.
  자세별 설정은 동작 키마다 독립 저장한다. 종료된 worker 진행률·손실·수집량은 로컬에 보존하며
  재실행에서는 이전 결과임을 표시한다. 다시 시작한 학습으로 표시하거나 중단을 성공으로 바꾸지 않는다.
- 선택 학습은 선택한 클래스의 분류기만 최적화한다. 기존 분류기는 동결한 ONNX 구성 요소로
  보존하고, 하나의 자기완결 ONNX로 결합한다. 모든 기존 클래스의 별도 eval과 오실행 회귀
  검사를 통과해야 active 포인터를 바꾼다. 다른 클래스의 학습 자료가 없어도 해당 가중치를
  유지하지만, 기존 클래스의 평가 자료는 필요하다. 실패·취소는 이전 active를 보존한다.
- 클래스별 구성 요소 ID와 기본 정책을 모델 카드에 기록한다. 사용자 정책은 동작 ID별
  `rules`/`shadow`/`apply`와 구성 요소 ID를 함께 저장한다. 새 구성 요소는 기본 `shadow`다.
  `shadow`는 예측만 하며 의도·Activity·출처 소유권을 내지 않는다. `apply`만 출처를 소유하며
  개인 모델이 거절하면 해당 동작의 규칙으로 우회하지 않는다. 정책 변경은 엔진 스레드의
  프레임 경계에서 출력 해제·상태 초기화 후 적용한다.
- 결합 모델의 중립 점수는 각 구성 요소의 중립 확률 최솟값이다. 내보내기는
  `Concat → ReduceMin(axis=1, keepdims=1)`을 사용하여 NPU의 동적 두 입력 Minimum
  컴파일 제한을 피한다. 분류기 가중치·클래스 경쟁·정책 ID는 바꾸지 않는다.
- `continuous-v2` 학습은 준비/동작 라벨 때문에 시간을 초기화하지 않는다. 실제 손 소실,
  시간 역전 또는 250ms 초과 간격에서 특징 이력과 연속 확인을 초기화한다. 실행 시와 같은
  유효한 전체 윈도만 학습하며, 조건별 fold 경계를 넘는 윈도는 학습에서 제외한다.
  관절의 `local3d-v1` 수식 자체와 기존 모델 경로는 유지한다.
- worker 진행 상태는 작업 ID에 귀속된다. 완료 이벤트와 종료 코드 0이 모두 있어야 100%다.
  ETA는 관측된 반복 시간을 사용하며 검증·저장 단계는 별도로 표시한다. 화면을 닫아도
  작업은 계속되고, 취소 후 설치가 이미 끝난 경우에는 설치 결과를 숨기지 않는다.

> 상태: **합의안 초안 (2026-11-05)**. 구현 현황은 [AGENTS.md](../AGENTS.md), 타입과 프로토콜은 [`src/manux/core/contracts.py`](../src/manux/core/contracts.py), 검사는 [`tests/contract/`](../tests/contract/)를 따른다. 직접 판정의 구현 정책은 D-007의 팀 검토 전 제안으로 구분한다.
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

실패하면: 잘못된 값으로 만들면 ValueError. 카메라가 새 프레임을 못 주면 FrameInfo를 만들지 않는다. 실행기는 Engine.frame_missing(now)으로 대기 중 명령을 적용하고 손 소실 시간을 센다.

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
- 기본 검출 수 `tracking.num_hands`는 v0.3과 같은 1이다. 필요하면 2를 명시할 수 있지만,
  한 손만 보이는 상태에서 추가 손을 찾는 비용이 늘 수 있다. 선택한 조작 손이 검출되지 않으면
  다른 손으로 대체하지 않고 `None`을 넘긴다.
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
| `source` | str | 실제 판정 기능 ID (`pinch`, `tcn` 등). 동작 ID의 논리 영역과 다를 수 있다 |
| `gesture` | 동작 ID | `gestures`에 선언된 것만 |
| `phase` | `tap` / `begin` / `hold` / `end` | 한 번 / 시작 / 유지(매 프레임) / 끝 |
| `point` | `(x, y)` 0..1 또는 `None` | 보정된 기준점(포인터 이동·드래그·스크롤). 화면 픽셀 변환은 출력 계층 몫 |
| `confidence` | 0..1 또는 `None` | 있으면 |
| `info` | 읽기 전용 dict | 진단용. 출력 동작을 바꾸는 데 쓰지 않는다 |
| `scale` | 양수 또는 `None` | 프레임마다 바뀌는 속도 배율(v0.3 손 모양 가속·길게 누른 드래그). 라우터가 `gain`을 받는 액션의 바인딩 `gain`(기본 1)에 곱한다. 다른 액션에는 영향 없음. `None` = 1 (2026-11-05 계약 변경) |

### `Activity` (중재기에 공개하는 상태)

| 필드 | 뜻 |
| --- | --- |
| `holding` | 누름·드래그·스크롤이 진행 중 |
| `last_release_t` | 마지막으로 끝난 시각(없으면 `-inf`) |

### 규칙

**2026-11-05 직접 판정 계약 변경:** 동작 ID는 논리 영역을 보존한다(`pinch.thumb_ring` 등).
`Intent.source`는 실제 판정기다. 엔진은 선언된 `gestures`와 `source == Feature.id`를 검사하고,
`gesture_sources`(동작 ID → 판정 기능 ID)가 있으면 그 판정기의 의도만 채택한다.
규칙이 같은 동작을 냈거나 TCN이 거절·오류·중립을 낸 경우 규칙으로 우회하지 않는다.
직접 판정과 확인기(`tcn_gate`)는 같은 모델에 동시에 적용하지 않는다.
직접 판정기가 유효한 동작 자세를 확인·유지하는 동안 `Activity.holding`으로 해당 프레임을 점유한다.
그동안 다른 규칙의 새 명령·커서 이동·모드 진입은 실행하지 않고 `end`만 허용한다. 규칙 span에서
모델 명령으로 넘어갈 때 출력 해제와 경쟁 규칙 reset을 먼저 한다. 점유가 끝나면 모델이 맡지 않은
규칙 동작은 다시 허용한다. end는 같은 프레임의 새 begin/이동보다 먼저 전달한다.

`tcn` 기능은 모델의 `decisions`대로 `tap` 또는 `begin/hold/end`를 낸다. `none`이 전체 임계값을
`frames_in_a_row`번 연속 통과한 뒤에만 새 동작을 시작한다. 시작·reset·모드 변경·세계 좌표 소실·
추론 오류 뒤에도 이 중립 확인이 필요하다. tap은 중립 복귀 전 반복하지 않는다. span은 해당 클래스가
임계값 아래가 되거나 손/세계 좌표가 사라지면 즉시 end, 이후 중립 복귀를 요구한다. 단조 시간이
깨지거나 `tcn.max_gap`(기본 0.3초)을 넘으면 상태를 끊는다. 출력 `none` 동안에는 판정 상태를 쌓지 않는다.
모델은 클래스 중 가장 높은 확률 하나만 선택한다. 유효한 특징 window가 찬 뒤 예측한다.
모드 진입/종료는 이번 직접 판정의 대상이 아니며 기존 중재 경로를 사용한다.

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
- 중재기는 출력의 상태도 본다(2026-11-05 계약 변경): 현재 출력 싱크의 `holding()`이 참이면(드래그 버튼이 눌려 있거나 스크롤이 진행·관성 중) 진입을 거절한다(`refused:output holding`). v0.3도 이 경우 모션 진입을 막았다. 기능의 `others`에는 넣지 않는다.

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
| `mouse.drag` | begin / hold / end | `gain` | `drag_hold`, `drag_toggle` |
| `mouse.scroll` | begin / hold / end | `gain` | `scroll_grab` |
| `window.task_switcher` | tap | — | `task_switcher`, `alt_tab` |
| `window.show_desktop` | tap | — | `show_desktop` |
| `control.toggle` | tap | — | — |
| `window.close` | tap | — | 창 닫기(Alt+F4), `spatial_pose.rock_sign` 바인딩 |
| `nav.back`, `nav.forward` | tap | — | 모션 `back`, `forward` |
| `tab.prev`, `tab.next` | tap | — | 모션 `tab_prev`, `tab_next` |
| `media.play_pause`, `media.volume_up`, `media.volume_down` | tap | — | **제안** (없음. 침대 사용 R6) |

이 표는 코드 [`actions/catalog.py`](../src/manux/actions/catalog.py)와 같아야 한다(`tests/unit/actions/test_actions.py`가 비교한다). **제안** 줄은 목록에 넣지 않는다.

- `params`(S3 추가, 계약 변경): 바인딩이 줄 수 있는 키. `gain`은 양수 배율(`mouse.move`: 커서 이동 배율 / `mouse.drag`: 드래그 중 커서 이동 배율 / `mouse.scroll`: 스크롤 배율). 프레임마다 바뀌는 값은 `params`가 아니라 `Intent.scale`로 온다(§5).
- `window.task_switcher`는 Ctrl+Alt+Tab으로 Windows 창 선택기를 연다. 키는 즉시 모두 해제하고 열린 선택기에서 포인터로 고를 수 있다.

v0.3 `grab`의 실제 용도는 이식 때 확인하고 이 표에 추가한다.

### 바인딩과 라우터

- 바인딩: `동작 ID → 액션 ID`. 기능의 `DEFAULT_BINDINGS` 위에 프로필 값이 덮어쓴다.
- 라우터는 `Intent`를 `ActionCommand`로 바꾼다. `phase`와 `point`는 그대로 옮긴다. `Intent.scale`이 있고 액션이 `gain`을 받으면 명령의 `gain` = 바인딩 `gain`(기본 1) × `scale`. `info`는 옮기지 않는다.
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

### v0.5 V 제어 전환

- `control.v_sign`은 검지·중지 폄, 약지·새끼·엄지 접음을 규칙으로 확인해 기본 0.7초 연속 유지 후 tap을 낸다. 프레임 간격이 0.3초를 넘거나 손이 사라지면 유지 시간을 다시 센다. 기본 쿨다운은 1.5초이며 V를 풀기 전에는 반복하지 않는다.
- 유지 시간을 우회하지 않도록 `control.v_sign`은 TCN 직접 적용 대상이 아니다. 카탈로그에서 녹화·MLP 표본은 가능하며 TCN 직접 판정 메타데이터에서는 제외한다. 직접 적용 모델이 이를 소유하려 하면 명시 오류로 거부한다.
- MLP 보조 차단이 켜져 있고 선택한 MLP가 `v_sign` 클래스를 제공하면 같은 손의 확률이 기본 0.8 이상이어야 한다. 활성 모델의 거절·오류를 규칙으로 우회하지 않는다. 보조 차단이 꺼져 있거나 해당 클래스 또는 모델이 없거나 학습 메타데이터에 V 표본이 0개로 기록되면 규칙으로 확인한다. 표본 수가 기록되지 않은 이전 모델은 기존 동작을 유지한다.
- 기본 바인딩 `control.v_sign → control.toggle`을 라우터가 해석한다. 엔진이 `control.toggle`을 소비하며 Windows 출력 싱크로 보내지 않는다. main에서 제어 상태를 반전하고 출력 해제·다른 기능 reset을 먼저 한다. 전환 프레임에는 다른 명령을 내지 않는다. tutorial에서는 Snapshot에 성공 결과와 `control:practice` 이벤트만 남겨 연습을 완료할 수 있고 제어 상태나 실제 입력은 바꾸지 않는다.
- 제어가 꺼진 main에서는 V 전환 기능만 실행한다. calibrating/enrolling/recording 중에는 V도 실행하지 않으며 전환 상태를 쌓지 않는다. V 유지 중에도 경쟁 클릭·모드 진입·이동을 억제한다.
- 바탕화면 보기는 기본 0.6초 동안 손이 정지해야 하며 쿨다운은 3초다. 기존 `pinch.enable_thumb_tucked_open`으로 사용자가 끌 수 있다.
- 기본 손 모양 속도는 켜짐을 유지하며 편 손은 2배, 주먹은 0.35배로 정밀 이동한다. 기존 `pinch.shape_dpi_min`은 편 손 배율, `pinch.fist_dpi_multiplier`는 주먹 배율이며 중간 모양은 두 값 사이를 보간한다. 이는 11/07 실사용 피드백에 따른 조정이며 측정된 최적값은 아니다.

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
- 선택 메서드 `holding() -> bool`: 드래그·스크롤처럼 끝나지 않은 입력이 있으면 참. 예외를 던지지 않는다. 없거나 실패하면 엔진은 거짓으로 본다. `windows`(드래그·스크롤·관성), `practice`(끝나지 않은 `begin`), `none`(항상 거짓)이 구현한다.

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

테스터 수집기는 위 형식으로 완료한 세트마다 파일 하나를 쓴다. `meta.person`에는 테스터 별명,
`meta.preset`에는 손 조작 방식, `meta.split`에는 `train` 또는 `eval`을 기록한다. `meta.session_id`는
학습 센터에서 체크한 과제를 연속 녹화하는 **시작 요청 한 번의 묶음**을 식별한다.
묶음 시작 시 `str(uuid4())`로 생성한 하이픈 포함 UUID v4 문자열(36자)을 쓰며, 같은 묶음의
모든 파일에 같은 값을 넣는다. 일시정지·재개·다음 과제·파일 재열기는 값을 바꾸지 않는다.
같은 옵션으로 새 묶음을 시작하거나 앱을 재시작해 녹화를 시작하면 새 값을 만든다.
CLI 수집기(`tools/collect_mouse.py`)도 같은 키·형식을 쓴다. 이전 녹화의 필드 누락은 허용한다.

`label.task`는
실제 과제(예: 더블클릭), `label.target`은 학습 클래스(예: `thumb_middle`)다. 같은 제스처를
쓰는 클릭과 더블클릭은 같은 클래스를 쓴다. 자세·카메라 조건은 실행을 바꿔도 고정된 인덱스를 쓴다.
`rule.gesture`는 프레임의 대표 규칙 판정이며 학습 라벨은 가이드에서 따로 부여한다.
복귀 동작은 `transition`으로 기록한다. 유효한 세계 좌표가 부족한 세트는 완료 파일로 확정하지 않는다.

**계약 변경 (2026-11-05, 과제 목록·녹화 설정):** 영향받는 계층은 `data`, `tools/`(수집기·TCN 도구), 이후 `ui`(학습 센터 화면)다.
- **과제 목록은 하나**: `manux.data.catalog`. rasp-jun 브랜치의 18개 과제(키·학습 클래스 그대로)와 수집기 추가 과제(더블클릭, 가로 스크롤, Record & Fling, 포인터·트리거 클릭)다. 수집기·학습·화면은 이 목록만 읽는다. 과제에는 동작 ID(`gesture`, 클래스가 뜻하는 v0.4 동작)만 있고 액션 이름은 없다(D-004). `v_sign`·`rock_sign`·`swipe_down`은 v0.4 기능이 아직 없어 **녹화만** 한다(`gesture` 없음). 커서 이동 과제(`open_move`·`fist_move`·`pointer_move`)는 `none` 클래스로 기록한다.
- **녹화 설정(`manux-capture-plan/1`)**: `{"schema", "conditions": [행...]}`. 행 = `name`, `enabled`, `notes`, 물리 조건 6개(`orientation`, `position`(화면 9구역 + 자유 이동), `distance`, `posture`, `camera`, `lighting`, 각각 정해진 값 중 하나), 시간(`reps` 1~100, `prep`·`hold`·`rest` 0.5~10초, `free_seconds` 10~900초). 기본 설정 `basic`(중앙·왼쪽·오른쪽), `positions`(9구역), `diverse`(9개). 사용자 설정 폴더의 `config/capture-plans.json`에 과제별로 저장하고(`{"schema", "plans": {과제 키 또는 "all": 설정}}`), 저장소 안에는 쓰지 않는다. 잘못된 설정은 기존 파일을 바꾸지 않는다.
- **라벨 추가 필드**: 녹화 설정으로 녹화한 프레임의 `label`에 `condition_key`(물리 조건 6개 + 메모의 해시, `pose-` + 16자리)를 넣는다. 이름·순서·시간은 키에 들어가지 않으므로 같은 물리 조건은 녹화가 달라도 같은 키다. `condition_index`(켜진 행의 순서)는 이전 읽기 도구를 위해 계속 쓴다. `meta.guided.capture_plan`에 사용한 설정 전체를 남긴다.
- **읽기 규칙**: 학습·평가는 `condition_key`로 조건 그룹을 만들고, 키가 없는 이전 녹화는 `legacy-<condition_index>`로 읽는다. 사람·자세 그룹과 함께 조건 그룹 하나를 통째로 빼고 학습하는 평가를 지원한다. 스와이프 과제의 `do` 프레임 중 요청한 방향으로 움직이지 않은 프레임은 학습 대상에서 뺀다.
- 손의 3D 좌표가 없거나 일시정지한 동안에는 세트 시간이 멈추고, 그 프레임은 `target: idle`, `phase: paused`로 기록한다(학습 제외).

포즈 표본 JSON 저장(`data/pose_studio.py`)은 split·UUID가 포함된 고유 경로를 사용한다.
완성된 임시 파일만 원자적으로 신규 게시하며 기존 파일을 덮어쓰지 않는다. 같은 밀리초의
학습·평가·연속 저장도 서로 보존하고, 저장 실패는 수집 버퍼와 기존 파일을 유지한다.

## 10. 모델 파일

### 선택적 하드웨어 가속 (2026-11-06)

- 학습 worker·도구의 장치는 `auto|cpu|cuda|xpu`다. 명시한 장치의 실제 순전파·역전파·Adam
  검사가 실패하면 이유와 함께 실패한다. auto만 검증한 장치를 선택하거나 이유를 표시하고 CPU로
  전환할 수 있다. 요청·실제 장치·이름·런타임·전환 사유는 `ExecutionDevice`로 기록한다.
- 모델을 최적화기 생성 전에 옮긴다. 손실 가중치·증강 난수·미니배치·라벨은 같은 장치·자료형을
  사용한다. 입력 전체를 GPU에 올리지 않는다. 예측 반환·ONNX 내보내기는 CPU 배열/복사본을 쓴다.
  시드를 기록하되 장치 간 학습 가중치가 같다고 약속하지 않는다.
- 추론은 `cpu|cuda|openvino-cpu|openvino-gpu|openvino-npu|auto`를 `Predictor` 뒤에서 선택한다.
  기본은 cpu다. 명시적 가속 장치를 열거나 모델을 컴파일하지 못하면 모델 로드를 거부한다.
  공급자 이름만으로 실제 가속을 주장하지 않는다. OpenVINO는 EXECUTION_DEVICES를 검사하며
  NPU·GPU는 해당 장치만 명시한다. CUDA는 CPU EP 할당/실행 fallback을 금지한다.
- auto는 초기화 실패 이유와 실제 CPU 선택을 기록할 수 있다. 실행 중 오류·모델 거절은
  자동 재실행·다른 판정기로 우회하지 않고 기존 오류 해제·중립 복귀 계약을 따른다.
- 번들 기본 worker는 CPU 환경이다. CUDA/XPU/OpenVINO는 선택 환경에 설치하며 설치본 지원은
  별도 패키징 시험으로 구분한다. 추론 가속이 학습·MediaPipe 손 추적까지 가속한다는 뜻은 아니다.
- 비교는 같은 모델/입력의 출력 차이와 실제 명령 이벤트, 초기 로딩/컴파일, 준비 후 p50/p95를
  기록한다. 학습은 데이터 준비·전송·학습·내보내기를 분리하고 가속 장치를 동기화해 측정한다.

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
- 직접 판정용 선택 필드 `decisions`: 클래스 → `{kind, modes, point, threshold?, frames_in_a_row?}`.
  `kind`는 `tap` 또는 `span`, `modes`는 `pointer`·`pinch`·`motion` 중 비어 있지 않은 목록,
  `point`는 `none`·`palm`(0·5·9·13·17 평균)·`index`(8번)다. span은 point가 필요하다.
  임계값·연속 수는 생략하면 전체 값을 쓴다. `gestures`의 모든 클래스에 하나씩 필요하며,
  `none`과 `.enter`/`.exit`는 선언하지 않는다. 기존 모델은 확인기용으로 계속 읽는다.
  직접 모드를 명시했는데 모델/선언을 읽지 못하면 실행을 시작하지 않는다(규칙으로 되돌리지 않음).
- `model_sha256`가 있으면 ONNX 바이트 해시를 반드시 확인한다. 학습 도구는 내보낸 모델 해시를 쓴다.
- **설치·교체:** 검증된 개인 모델은 불변 버전 폴더에 모델·정보·평가 보고서를 먼저 쓰고 마지막에
  `active.json` 포인터를 원자적으로 교체한다. 실패·취소·검증 실패는 기존 active를 바꾸지 않는다.
  평가 녹화는 명시적 `split=eval`, 학습 녹화 해시와 겹치지 않아야 한다. 각 클래스에 평가 시도와 적중이
  있어야 하며 기존 클래스 적중 수·중립 오실행률이 현재 모델(최초에는 녹화의 규칙)보다 나빠지면 설치하지 않는다.
  중립 노출이 0이면 검증 불가다. 이는 개인 모델 교체 기준이며 기본 모델 릴리스 정확도 기준은 별도다.
- 데스크톱 실행기는 `active.json` 변경을 엔진 스레드의 프레임 경계에서 확인한다. 느린 파일 읽기 동안
  버튼이 남지 않도록 `release_all("settings")` → 기존 기능/필터 reset을 먼저 하고 새 모델·정보·해시를
  모두 검증한 뒤 새 파이프라인으로 바꾼다. 읽기 실패는 기존 메모리 모델을 유지하고 상태 문구에 남긴다.
  실패한 같은 포인터를 매 프레임 다시 읽지 않는다. 기존/새 모델 모두 중립 복귀 전 실행하지 않는다.
  엔진 `replace_pipeline`은 조립 지점의 실행기만 같은 스레드에서 호출하며 UI가 직접 호출하지 않는다.
- 개인 모델 파일을 직접 지정하는 개발 실행은 헤드리스 연습 또는 데스크톱 `--no-windows-input`으로
  확인한다. 개인 모델로 Windows 입력을 쓰려면 검증된 `--tcn-store`가 필요하다. active를 읽을 때
  모델·정보·평가 보고서 해시와 검증 기준을 다시 확인한다. 최초 설치는 평가 프레임의 규칙 기록이 필요하다.
- 수집한 클래스 중 `hold`만 내는 커서 이동과 중재기에서 소비되는 모드 진입은 학습·평가에 쓸 수 있다. 실행기는 `tap` 또는 `begin`으로 시작하고 바인딩이 있는 동작만 TCN 확인기에 연결한다.
- **제안:** 개인 모델은 자기가 선언한 동작에 대해서만 기본 모델보다 먼저 쓰인다. 개인 모델이 거절한 입력은 기본 모델로 다시 판정하지 않는다.

## 11. 설정과 프로필

출처: `config/gestures.json`, `gesture_engine.load_config/save_config/set_profile`, `profiles.py`, `app_paths.py`, Qt `QSettings`(화면 선택 값).

**계약:**
- 설정 키는 `<기능 ID>.<이름>` 또는 공용 `app.<이름>`, `camera.<이름>`, `tracking.<이름>`, `cursor.<이름>`. 기능의 기본값은 그 기능의 `SETTINGS`에 있다.
- 설정 파일은 `<설정 폴더>/settings.json` = `{"version": 1, "values": {키: 값}}`이다. 읽을 때 이전 버전을 변환한다.
- v0.3 변환(`settings/migrate_v03.py`): 주인이 있는 키는 표(`RENAMES`)대로 새 키로 바꾸고, 나머지는 `v03.<키>`로 그대로 보존한다. 기능을 옮길 때 그 기능의 v0.3 키를 표에 추가한다. v0.3 파일은 읽기만 하고 고치지 않는다.
- 잘못된 값(타입·범위)은 그 키만 기본값으로 되돌리고 경고를 남긴다. 앱은 멈추지 않는다. 모르는 키는 지우지 않고 보존한다.
- 저장은 임시 파일에 쓰고 이름을 바꾸는 방식으로 한다. 저장이 실패해도 이전 파일은 그대로다.
- 프로필은 사용자 값만 덮어쓴다. 프로필 이름 규칙은 v0.3 `profiles.NAME_RE`를 따른다.
- 사용자 폴더(`settings/paths.py`): 설치본 `%APPDATA%/MANUX/v04`, 소스 실행 `%APPDATA%/MANUX-dev/v04`, `MANUX_DATA_DIR`가 있으면 그 폴더. v0.3 설치본(`%APPDATA%/MANUX`)과 섞이지 않는다. 저장소 안에 사용자 설정을 쓰지 않는다.
- 공용 계층의 설정(`app.`, `camera.`, `tracking.`)은 `settings/builtin.py`에 선언한다. 계층 규칙상 `capture`·`tracking`은 `settings`를 import할 수 없으므로 조립 지점이 값을 넘긴다.

## 12. 화면과 엔진 사이 (`Snapshot`, `UiCommand`)

### 온보딩 커서 자동 설정

- 손바닥 커서의 가로 입력은 손바닥 기준점의 영상 X이며 세로 입력은 손목에서 손가락 뿌리로 향하는 3D 벡터의 기울기다. 기울기를 얻을 수 없는 프레임은 세로 위치를 유지한다. 클릭과 스크롤은 기존 손짓 입력을 사용한다.
- 온보딩은 손 확인, 좌우 편한 범위, 올린 손목, 내린 손목을 순서대로 측정한다. 영상 가로 범위를 주 모니터 너비에, 두 기울기 끝점을 높이에 대응시킨다. 조준과 Windows 출력에 같은 매핑을 사용한다.
- 조준 도중과 결과 화면에서 가로 감도·세로 감도·부드러움을 따로 조정할 수 있다. 조정하면 점수 측정을 멈추고 새 설정으로 자유 이동을 보여 준다. 다시 시험을 눌러 새 표적을 시작한다. 손을 놓친 구간은 유지 시간에 포함하지 않는다. 확인 화면의 적용 버튼에서만 설정을 확정한다.
- 보정값은 카메라 입력·영상 형식·조작 손·포인터 모드·모니터에 맞을 때 적용한다. 이동 감도를 직접 저장하면 해당 환경의 자동 보정값을 해제한다.
- 3D 기울기 보정은 이전 위치 기반 보정과 별도 버전으로 저장한다. 이전 보정은 다시 측정한다. 보정 전 손바닥 모드는 상대 이동에서도 세로 위치 대신 기울기 차이를 사용한다.
- 보정 프로필을 적용하면 위치 범위에 좌우·상하 감도를 적용한다. 손 모양에 따라 달라지는 기존 가속 배율은 보정 전 상대 이동에 사용한다.

- 스튜디오 민감도 편집은 Dan `hand-mouse-gesture-overhaul@a60241d`의 지원되는 핀치·이동 항목만 노출한다.
  `RuntimeRequest(kind="sensitivity", path=<config dir>, values=<편집한 키/값>)`를 런타임 큐로 보낸다.
  런타임은 범위·핀치 해제 기준(+0.02)·최소/최대 이동 감도를 검증하고 원자 저장한다. 실패 시 기존 값과 파일을 유지한다.
  성공하면 출력 해제·기능 reset·제어 일시정지 후 적용하며, 다시 제어를 시작해야 실제 입력을 낸다.
  결과는 `DesktopRuntime.sensitivity_result`(revision, success, text)로 UI가 읽는다.
  `cursor.relative_gain_min/max`, `cursor.smoothing`은 공유 설정으로 저장하며 조립 지점에서 Windows 출력에 전달한다.
  현재 프로필이 덮어쓴 값은 편집을 막고 표시한다. 창 열기·취소는 감도를 변경하지 않는다.

- `ViewFrame.frame_age_ms`는 앱이 카메라 프레임을 받은 시각부터 런타임 처리 후 화면 상태를
  발행하기 직전까지의 경과 시간이다. `None`은 측정값 없음이다. UI는 측정값만 표시한다.
  드라이버 내부 버퍼·모니터 표시 지연까지 포함하는 실제 손짓→화면 지연은 아니다.

### 학습 센터 사용 흐름 (2026-11-05)

- 별명 입력 초안은 주기적인 학습·허브 상태 갱신으로 덮어쓰지 않는다. 학습 센터·녹화 계획이
  열린 동안 메인 화면의 문자 단축키가 입력을 가로채지 않는다. 녹화 시작의 선행 조건이 부족하면
  실행 버튼 근처에서 이유를 표시한다.
- 허브 연결 확인은 읽기 연결과 업로드 권한을 구분해 반환한다. 읽기는 성공했지만 쓰기 권한이
  없거나 확인하지 못한 경우를 전체 연결 실패로 표시하지 않는다. 업로드 가능 여부는 별도 상태로
  표시하며, 권한 확인은 실제 업로드 시에도 수행한다. 토큰·원격 예외 본문은 노출하지 않는다.
  worker 완료 JSON의 `read_access`는 읽기 성공 여부이고, `upload_access`는 확인 성공 `true`,
  권한 거절 `false`, 미확인 `null`이다. 업로드 버튼은 `true`일 때만 활성화한다.

- 녹화 식별자는 `RecordingIdentity(profile, preset, split)`이다. 별명은 영문·숫자·`_`·`-` 1~32자,
  split은 `train` 또는 `eval`, preset은 `pinch`·`pointer`·`trigger`다. 사용자 폴더의
  `recordings/profile-<별명>/<preset>/<split>/`에 저장하며 동일 값을 meta에도 쓴다. UI에서 별명을
  정하기 전에는 녹화·업로드를 시작하지 않는다. 요약·학습은 하위 폴더도 읽고 eval은 학습에 넣지 않는다.
- 학습 센터는 사용 중 모델 파일을 덮어쓰지 않는다. 별도 작업 폴더에 후보와 `decisions`를 만들고,
  별도 eval 녹화로 §10 검증을 통과한 경우에만 개인 store를 바꾼다. 동작을 선택한 학습도 현재
  모델의 다른 분류기를 동결한 구성 요소로 유지하고 선택한 분류기만 학습한다. 자기완결 ONNX로
  결합한 뒤 전체 eval 검증을 통과해야 설치한다(위 testing 계약).
- 학습·HF 작업은 별도 worker 프로세스에서 실행한다. 소스는 Python + `tools/app_worker.py`,
  Trainer 선택 설치본은 `worker/MANUXWorker.exe`를 사용한다. 기본 설치본은 worker/torch를 포함하지 않는다.
  서비스는 각 학습 행에 `trainer_available`을 노출하며 worker가 없으면 `can_train=false`와
  "추가 구성요소 필요" 사유를 돌려준다. 화면은 기존 행 전달 경로로 고급 학습을 비활성화한다.
  worker 직접 요청도 시작 전에 같은 사유로 거절한다. 앱 프로세스는 torch를 import하지 않는다.
  요청·진행·성공·실패를 JSON으로 주고받으며 성공 코드만으로 모델 적용을 주장하지 않는다.
- 설치 성공은 `RuntimeRequest(kind="activate_model", path=<store>)`로 런타임 요청 큐에 전달한다.
  런타임이 프레임 경계에서 모델을 검사·교체한다. UI는 엔진 필드를 직접 바꾸지 않는다.
  다음 실행은 사용자 기본 store의 active를 자동으로 읽는다. 첫 설치 전에는 기존 규칙으로 동작한다.
- 학습 센터의 체크 상태는 선택한 과제 ID 목록과 같다. 필터를 바꿔도 선택은 유지하며 선택 0개일 때
  녹화·선택 학습을 시작하지 않는다. 선택 녹화는 해당 과제만 같은 계획으로 진행한다. 선택 학습은
  선택한 클래스만 최적화하고 기존 구성 요소·정책 ID를 보존한다.
- HF 인증은 `.env`/환경변수/HF 로그인에서 worker만 읽는다. 앱에서 토큰을 입력·저장하지 않는다.
  소스의 기존 `.env`, 설치 폴더의 `.env`, 사용자 config의 `.env`를 지원하며 사용자 config가 우선한다.
  `MANUX_HF_REPO`도 같은 환경에서 읽고, UI에는 저장소 주소와 연결 결과만 돌려준다.
  `training-center.json`에는 녹화 별명만 저장한다.
  설정 변경은 접근 권한을 부여하지 않으며 해당 HF Dataset에 읽기/쓰기 권한이 있어야 한다.
- 업로드는 사용자가 누른 경우에만 현재 별명의 완료된 녹화에 대해 기존 `hub.py` 검증을 거쳐 수행한다.
  팀에서 내려받은 다른 별명의 녹화를 다시 올리지 않는다. 화면에 대상 repo와 영상 미포함을 표시한다.
  다운로드는 별도 버튼으로 팀 녹화를 사용자 폴더에 가져온다. 실패는 완료/0건 성공으로 바꾸지 않는다.
  연결 확인·다운로드에는 녹화 별명이 필요하지 않다. 업로드에만 유효한 별명을 요구한다.
  토큰은 요청 JSON·명령행·진행 로그·내보내기 패키지에 넣지 않는다.

출처: `qt_runtime.Snapshot`, `UiState`, `qt_dashboard.py`.

- 엔진은 프레임마다 **불변** `Snapshot`을 하나 만든다. UI는 가장 최근 것만 읽는다. 지난 Snapshot을 쌓지 않는다.
- `Snapshot`에 들어가는 것(**제안**, S6에서 확정): 프레임 번호, 미리보기 영상(화면이 보일 때만), 손 관절(그리기용), 단계·모드·출력 대상, 최근 동작/액션과 결과, 기능별 상태 문구, 카메라 정보·밝기·어두움 경고, fps·단계별 처리 시간, 모델 상태.
- 현재 구현(`engine/snapshot.py`, 엔진 부분만): 프레임 번호·시각, 단계, 출력 대상, 모드, 제어 켬, 바쁨(녹화 등), 손 있음, 필터 뒤 의도, 라우팅 안 된 의도와 이유, 출력 결과, 기능별 `holding`, 이벤트(`hand_lost`, `mode:…`, `mark:…`), 오류. 영상·관절·카메라 정보·모델 상태는 조립 지점의 실행기(S6)가 덧붙인다.
- `ViewFrame.model_status`는 실행기가 읽은 모델 모드·갱신 결과 문구다. 브리지는 그대로 표시하고 판단하지 않는다. 교체 성공은 엔진 이벤트 `model:reloaded`에도 남긴다.
- `UiCommand`(`engine/commands.py`): `SetStage`, `SetControl`, `SetBusy`(`calibrating`/`enrolling`/`recording`), `RequestMode`, `SetBaseMode`, `ResetPractice`, `Mark`. `ResetPractice`는 tutorial에서만 출력 해제 → 모든 기능/필터 reset → 기본 모드 복귀 순서로 처리하며 `practice:reset` 이벤트를 남긴다. 다른 단계에서는 효과가 없다.
- UI → 엔진은 `UiCommand` 큐로만 보낸다. 예: 단계 바꾸기, 제어 켜기/끄기, 조작 손 바꾸기, 설정 값 바꾸기, 바인딩 바꾸기, 녹화 시작/끝, "방금 이상했어요" 표시. UI는 엔진 객체의 필드를 직접 바꾸지 않는다.
- UI는 판정 값을 계산하지 않는다. 화면에 보일 문구가 판정에서 나와야 하면 엔진이 Snapshot에 넣는다.

## 13. 학습 스튜디오 이식 (2026-11-06)

- 현재 v0.4 학습센터에서 동작 선택, 녹화 검수, 시뮬레이터, 모델 이력·진단을 제공한다.
  UI는 주입한 서비스만 사용하고 품질·변환·재생 판정은 별도 worker에서 수행한다.
- 녹화 선택은 루트 기준 상대 경로와 내용 해시에 묶는다. 다른 파일로 바뀌면 기존 선택을
  적용하지 않는다. 제외는 학습에서만 제외하며 평가 파일을 임의로 제외하지 않는다.
  삭제는 사용자 확인 후 휴지통 폴더로 이동하며 영구 삭제하지 않는다.
- 품질 경고는 관절 누락·급변·크기·시간·시도 길이의 기계적 검사다. 손짓 정답 검증이나
  모델 정확도 점수가 아니다. 경고만으로 녹화를 자동 삭제·제외하지 않는다.
- 보정·증강은 train 원본의 별도 파생본이다. meta.lineage에 원본 SHA256·변환 종류를 남기며
  2D에서 world를 만들지 않는다. 라벨/조건/반복 경계를 넘겨 보간하지 않는다. 원본과 보정본을
  동시에 중복 학습하지 않고 선택한 보정본이 원본을 대체한다. 증강은 추가 train 표본이다.
  eval 파생본은 학습·설치 평가에 사용할 수 없다. 후보/기존 모델의 원본 계보와 평가 계보가
  겹치면 설치하지 않는다. 학습 도구는 원본 해시도 모델 카드 data에 기록한다.
- 재생은 기록한 손 관절과 사용자가 켠 pose만 그리며 없는 팔을 추정해 만들지 않는다.
  녹화의 해상도 비율을 유지한다. 빈 손 프레임은 그대로 표시하고 다음 손 프레임 탐색을 제공한다.
  재생은 단조 시계의 경과 시간과 원본 타임스탬프로 프레임을 선택하며 UI 지연을 누적하지 않는다.
  모든 프레임의 수동 탐색과 타임라인의 품질 경고·기록 이벤트 이동은 계속 제공한다.
- `tracking.pose_enabled`는 기본 false, `tracking.pose_every_n_frames`는 기본 5다. 켜면 녹화 중에만
  공식 Pose Lite 모델로 상체 6점(왼/오 어깨·팔꿈치·손목)을 추가 추적한다. 같은 런타임 스레드에서
  처리하고 프레임을 쌓지 않는다. pose는 시각화용이며 TCN 특징에 넣지 않는다. 모델 부재·오류는
  손 추적·녹화를 막지 않고 상태로 보인다. 재생의 최근 pose 유지는 최대 0.3초이며 시각을 표시한다.
- 시뮬레이터는 현재 app 조립 함수·Engine·TCN·바인딩으로 녹화를 재생한다. 출력은
  practice/null만 주입하며 Windows sink를 만들지 않는다. 프레임 확률과 실제 tap/begin
  명령 이벤트를 구분한다. 누적 프레임 적중률을 실사용 정확도로 설명하지 않는다.
- 모델 이력은 store의 불변 버전이다. 복원은 저장된 모델을 현재의 독립 eval과 현재 모델에
  다시 비교하고 통과할 때만 새 버전을 원자적으로 설치한다. 검사 실패·취소는 active를 유지한다.
- 동작 선택 학습은 최신 testing의 분류기 구성 경로를 유지한다. 선택하지 않은 ONNX 구성 요소를
  보존하고 새 구성 요소는 shadow로 시작한다. 버전 복원은 현재 전체 eval을 다시 검사한다.
- 시뮬레이터는 현재 동작별 정책과 전체 TCN을 연습 적용한 비교 경로를 구분한다. 후자는 OS 입력
  없이 적용 전 예상 이벤트만 표시한다. 어느 경로도 사용자 적용 정책을 변경하지 않는다.
- studio worker의 완료 데이터는 해당 job 폴더 result.json에 기록한다. UI는 완료된 자기
  job의 결과만 읽고 종료 후 지연 결과는 무시한다. 학습·허브·변환·복원은 동시에 변경하지 않는다.

## 14. 계약을 바꾸는 절차

1. 이 문서와 `core/contracts.py`를 **먼저** 같은 PR에서 고친다. 구현은 같은 PR 또는 다음 PR.
2. PR 본문에 "계약 변경"과 영향받는 계층·기능 ID를 적는다.
3. `tests/contract/`와 `tests/architecture/`를 실행해 결과를 PR에 적는다.
4. **제안**을 **계약**으로 바꿀 때는 [결정 기록](decisions.md)에 한 줄 남긴다.


### v0.5 스튜디오 통합 (2026-11-06)

- MLP는 `ml/spatial_mlp.py` 하나다. 33차원 pose-angles-v1 / 96차원 spatial-mlp-v1과 동적 클래스를 허용하며 내부에서 학습/평가를 나누지 않는다. 선택적 validation_data는 호출자가 분리해 전달한다. 파일 저장·로드 형식은 manux-spatial-mlp/1이다.
- Pose Studio의 기존 무작위 80/20 프레임 내부 확인은 data의 명시적 분할 함수로 옮긴다. 10개 미만은 원래처럼 학습 표본 재확인이다. 화면 문구는 **학습 데이터 내부 확인(실제 정확도 아님)**이며 독립 세션/사람 평가가 아니다. eval_judges는 이 분할을 쓰지 않는다.
- `perception/spatial_features.py`는 #82의 96차원 원본 하나, pose_angles.py는 33차원 평가 비교용으로 유지한다.
- Pose/TCN/수집·공유 스튜디오는 분리된 서비스로 조립한다. Pose의 현재 모델 선택·저장·로드·재적용은 최신 #82를 따른다. Dan/Rasp-Jun의 과제·진행/공유·민감도 기능은 testing/#91을 보존하며 기존 계층 import 규칙을 완화하지 않는다.
- 분류 클래스 rock_sign은 `spatial_pose.rock_sign` 동작을 내고, `window.close` 액션은 바인딩으로만 연결한다. close는 tap 한 번에 Alt+F4를 누른 뒤 해제한다. 기본 assist 모드는 직접 명령을 내지 않는다.

### v0.5 사용성 화면·설정 연결 (2026-11-07)

- 실행기가 `ViewFrame.control_hand`(실제 조작 손), `preview_gesture`(엔진이 채택한 동작 ID), `practice_cursor`(연습 싱크의 정규화 가상 커서), `practice_precision`(채택된 이동의 gain < 1)을 전달한다. 관절은 기존 `landmarks`와 같은 영상 좌표다. Bridge는 값을 노출하고 QML은 이미지의 PreserveAspectFit 영역에 21점과 뼈대를 그린다. 영상 자체를 변경하거나 화면에서 손짓을 판정하지 않는다. 손/영상 소실 때 이전 관절을 남기지 않는다.
- 연습 싱크는 OS 호출 없는 `CursorMapper` 계산을 재사용한다. 감도·가속·손 모양 배율을 가상 1920×1080 화면에 적용하고 `cursor_position`으로 전달한다. 실제 Windows 커서·클릭을 보내지 않는다. 출력 해제는 가상 커서 위치를 유지하고 이동 앵커만 끊는다.
- `ViewFrame.actions`에는 출력 결과가 `ok`인 동작만 포함한다. 거절/오류는 온보딩 성공으로 세지 않는다. 제어 전환 뒤 Bridge의 일시정지 표시는 Snapshot.control_on을 따른다.
- `RuntimeRequest('advanced', config_dir, values)`는 bool/int/float/choice 설정 편집을 런타임 큐로 전달한다. 고급 설정은 알려진 실행 중 조절 가능한 키만 검증하며, 전체 검증·임시 저장 성공 후 메모리 설정과 연습/Windows 싱크를 갱신한다. 실패는 기존 설정 파일·실효값을 유지한다. 저장 중·실패·성공은 별도 revision 결과로 UI에 전달한다. 설정 변경 때 출력 해제·기능 reset·제어 끔을 먼저 적용한다. 프로필이 덮어쓴 값은 편집할 수 없다.

- §12 보충: 최신 영상은 한 장만 유지한다. 연습에서 받아들인 tap/begin·모드 전환은 불변 `PracticeEvent(seq,t,actions,cursor,precision,motion_on,has_hand)`로 별도 최대128개 링에 보존해 `ViewFrame.practice_events`로 전달한다. UI는 seq를 한 번만 소비하고 당시 가상 커서 위치로 성공을 확인한다. 단계 reset/연습 재진입 때 링을 비우고 지속 `practice_epoch`를 증가시켜 이전 단계 이벤트가 다음 단계에 재사용되지 않는다. 영상 프레임을 큐에 쌓지 않는다. UI의 표시 상태가 늦더라도 설정 변경은 항상 SetControl(False)를 큐에 보낸다.

## 14. 휴대폰 연결 (`link`, v0.8)

출처: Dan(한순중)의 `hand-mouse-gesture-overhaul@9da1f634e8c0afd9d3d6b32a56c83cddfb23d5f8`,
`src/phone_web_bridge.py`와 `src/camera.py`. 영향 계층: core, capture, 신규 link.

- 기본 꺼짐. 앱의 휴대폰 연결 켜기 또는 `--phone-web`/`--source phone`을 명시해야 listener를 연다. `RuntimeRequest("phone_camera", action)`은 start/local/pair/disconnect/block/unblock을 런타임 큐로 전달한다. Qt는 네트워크를 열지 않고 `ViewFrame.phone`의 일시적 상태·URL·코드만 표시한다. QR에는 URL만 넣는다.
- 연결 창은 `SetBusy("connecting")`으로 OS 입력을 막는다. 카메라 변경 전 입력을 해제하고 제어를 끈다. 녹화·등록·보정 중 소스 변경은 거절한다. 휴대폰 프레임도 기존 손 추적·엔진을 사용한다. 새 프레임이 없는 매 읽기는 `frame_missing`으로 기존 손 소실·입력 해제를 처리한다. 첫 유효 영상 이후 연결이 살아 있고 마지막 유효 영상에서 2초 미만인 짧은 수신 간격에는 미리보기만 유지한다. 연결 해제·새 세션의 첫 영상 대기·2초 이상 유효 영상 정체에는 이전 영상과 관절을 지운다. 유지한 영상을 추적·엔진·녹화에 다시 넣지 않는다. PC 카메라로 돌아가기는 서버를 닫고 기존 카메라 설정을 그대로 다시 연다. 인증 정보·휴대폰 선택은 설정에 저장하지 않으므로 재실행 기본은 기존 PC 카메라다.
- peer/bind 허용 주소는 RFC1918 IPv4(10/8, 172.16/12, 192.168/16), IPv6 ULA(fc00::/7), 로컬 시험용 loopback뿐이다. 공인·문서용·multicast·unspecified 주소 거절. 실제 TCP peer를 검사하며 Forwarded/X-Forwarded-For는 신뢰하지 않는다. Host와 HTTPS Origin은 listener 주소와 일치해야 한다.
- PC에서 6자리 난수 코드 발급, 단조시계 120초 만료·성공 한 번만 사용. 재발급은 이전 코드/미접속 토큰 폐기. 코드 교환은 전역 분당 5회, HTTP는 전역 분당 120회 제한.
- POST /pair의 {code, device} 검증 후 256bit 이상 접속 토큰 응답. 메모리에서만 관리하고 IP에 묶으며 120초 안에 한 번 WebSocket 인증에 쓴다. /ws 첫 JSON {kind:"auth", token} 없이는 프레임을 받지 않고 5초 내 인증하지 않으면 닫는다. 코드·토큰을 URL/QR/로그에 넣지 않는다.
- 한 기기만 접속. PC에 device(64자 이하 표시명, 권한 아님)·peer 표시. disconnect()는 연결·토큰·코드·프레임 폐기. block()은 전역 차단하고 끊는다. unblock() 후 새 코드 필요. 종료도 같은 정리.
- 첫 명시 실행 때 사용자 데이터 폴더 link/certs에 자체 서명 인증서/키 생성. Git checkout/worktree 내부와 그 안을 가리키는 링크 경로 거절. TLS 1.2 이상 HTTPS만 제공. 인증서 경고 후 iOS Safari getUserMedia 가능 여부는 사람이 검증한다.
- binary 프레임 하나 = 4byte big-endian JSON 길이 + UTF-8 JSON + JPEG. JSON ≤1024byte, 전체 ≤1MiB. JSON: {kind:"frame", seq, captured_at, width, height, mirrored, rotation, facing}. seq는 연결 내 증가 정수, captured_at은 PC epoch 초로 보정한 촬영 시각, JPEG 각 변 ≤1280·총 ≤1280×960, rotation은 적용할 시계방향 0/90/180/270, facing은 user|environment, mirrored는 적용할 좌우 반전이다. 캔버스는 회전 반영 픽셀이므로 기본 rotation=0, 전면만 mirrored=true. **480×360 상한·품질 0.55·목표 최대 30fps**는 원본 유지.
- sync JSON {kind:"sync", sent:epoch_ms} → {kind:"sync", sent, server:epoch_ms} 왕복 중간점으로 시계 보정. 촬영 시각은 JPEG 인코딩 직전 추정치이고 센서 노출 시각이 아니다. 데모 지연은 추정 촬영→PC 수신 시간이며 오차가 있다.
- 초당 프레임 36회(여유 포함) 제한. 서버 ack 전에 다음 프레임을 보내지 않고 건너뛴다. 잘못된 메시지는 닫고 이전 seq·750ms 초과/250ms 이상 미래 촬영 시각은 ack accepted=false로 버린다. audio 종류는 예약만 하고 현재 거절한다. 원격 명령·마이크·Bluetooth 없음.
- RemoteCamera는 압축 최신 하나만 보관, 덮어쓴 수를 센다. read(timeout)은 소비 스레드에서 JPEG 해독·반전·회전 후 FrameInfo와 BGR image 반환. FrameInfo.t는 수신 perf_counter, seq는 소스 전체에서 증가. 해독 중 연결이 바뀌면 결과 폐기. fps·actual_mode·추정 수신 지연 제공.
- 데스크톱 조립자는 휴대폰의 전면/후면 기본 반전과 `camera.mirror`가 다르면 추적 전에 영상과 `FrameInfo.mirrored`를 함께 보정한다. 반전 후 회전 순서와 기존 회전·크기를 유지한다. 전면 영상을 중복 반전하지 않으며 실제 손 선택·미리보기·관절·커서·녹화가 같은 보정된 좌표를 사용한다. PC 카메라는 이미 설정을 적용하므로 재보정하지 않는다.
- 새 프레임이 없으면 read는 None. 마지막 수신/접속 뒤 2초 정체면 is_open=false. 끊기/정지 때 즉시 슬롯을 비우고 대기자를 깨운다. QR 대기 화면·이전 프레임은 판정기에 넣지 않는다. 조립자가 Engine.frame_missing(now)으로 손 소실·입력 해제를 처리한다. link는 엔진·Windows 입력을 호출하지 않는다.
- 전면 카메라 연결에서는 현재 OS 커서가 있는 모니터 전체를 휴대폰에 표시한다. `platform.screen_preview`가 Qt GUI 스레드에서 QScreen을 캡처하고 커서 위치를 표시한다. 최대 1280×720에 비율 유지, JPEG 품질 65, 512KiB 상한이다. PC 화면은 좌우반전하지 않는다. `desktop`이 `ScreenRequests`의 take/complete 콜백을 주입하고 UI는 수명만 관리한다. 후면·미연결·요청 없음에는 캡처하지 않고 화면을 저장하지 않는다.
- 화면 전송은 손 영상 WebSocket과 별도의 `POST /screen` 요청이다. WS ready에서 메모리 전용 256bit 화면 토큰을 발급한다. 같은 Origin·IP·현재 인증된 WS·유효한 전면 카메라 프레임이 있어야 Authorization 헤더 토큰을 수락한다. URL·로그·저장소에 토큰을 넣지 않는다. 화면 HTTP는 초당 30회, 인증 후 요청은 초당 12회 제한하며 기존 일반 HTTP 분당 120회와 분리한다. 요청은 동시에 하나만 허용하고 캡처 대기는 0.8초로 제한한다. 전면/후면 전환·WS 종료·PC 차단은 진행 중 결과를 무효화한다. 응답도 인증과 세대를 재검사한다.
- 휴대폰은 이전 화면 응답·JPEG 해독을 마친 뒤 100ms 뒤에 다음 화면을 요청한다. 숨김·카메라 전환·연결 종료 때 HTTP를 취소하고 이미지 URL을 해제한다. 짧은 화면 수신 지연은 이전 이미지를 유지하고 2초 이상 갱신되지 않으면 비운다. 화면 실패는 손 영상 전송을 끊지 않는다. 전체화면은 사용자가 버튼을 눌러 진입·종료하며 모니터 비율을 유지한다. 전체화면 미지원/거절도 카메라 연결을 유지한다.

### v0.5 MLP·UI 감사 연결 계약

- 포즈 미리보기와 실행기 보조 판정은 모델의 feature_version에 맞는 96차원 또는 world 기반33차원 특징을 사용한다. 필요한 world가 없으면 해당 예측을 생략한다.
- 같은 클릭 클래스에 해당하는 손바닥/검지 동작은 보조 차단에서 같은 의미로 비교한다. V 제어 결과는 포즈 표본의 v_sign 클래스에 연결한다.
- 포즈 학습 요청은 선택한 모델 ID를 포함한다. worker는 해당 모델 표본만 읽어 같은 모델 슬롯에 후보를 저장하고 다른 슬롯을 보존한다. 기존 요청에서 ID가 없으면 저장된 선택값을 읽는다.
- 포즈 수집·학습·보조차단 버튼의 busy 상태는 worker/StudioBridge가 소유한다.
- 학습·수집·설정 창과 기존 포즈 페이지는 UI interactionBusy를 SetBusy(enrolling)로 큐에 전달한다. 해당 구간의 V는 제어 전환을 만들지 않는다. 등록·녹화 중 규칙 비교는 Windows 싱크가 없는 별도 practice 엔진의 Snapshot을 ViewFrame.rule_preview로 전달하고 녹화의 rule 필드에도 사용한다. 해당 구간이 끝나거나 종료하면 정리한다. 실제 제어 엔진은 출력none을 유지한다.

### v0.5 실사용 후속

- 스크롤 활성 중 엄지·검지 끝이 떨어지면 손가락 중간 관절이 가깝더라도 해제 유예를 시작한다. 스크롤 종료·관성과 드래그 판정은 각자의 기존 계약을 따른다.
- 상대 커서 이동은 확장 화면의 가로·세로 범위를 주 모니터 대비 축별로 반영한다. 카메라 종횡비 보정을 유지하고, 이동 위치는 확장 화면 경계 안에서 누적하되 실제 출력은 가장 가까운 모니터에 놓는다. 절대 모드는 기존처럼 선택한 모니터를 사용한다.
- 바탕화면 연습은 practice 싱크의 승인 동작을 표시한다. 선택 모션 연습은 엄지 척으로 모드에 진입한 뒤 승인된 스와이프까지 기다린다. 연습은 Windows 입력을 보내지 않는다.
- 포즈와 커서 기준 손끝 표시를 공유하는 창은 두 레이아웃에서 설정의 손가락 표시 스위치를 따른다. 제어 전환 알림은 실제 Snapshot의 control_on 변경을 표시한다.
