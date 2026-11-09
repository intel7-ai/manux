# MANUX v0.4 구조 (apps/manux)

> 상태: **합의안 초안 (2026-11-05, Sean Choi)**. 결정 D-001~D-004·D-006과 D-005의 방향은 확정이다([결정 기록](decisions.md)). "제안"으로 표시된 부분은 팀 검토 전 초안이다.
> 진행 상황은 [§8 이식 순서](#8-이식-순서)의 상태 열이 기준이다. 직접 TCN 판정·개인 모델 검증/설치·교체는 [사용법](direct-tcn.md)을 참고한다. Releases 설치파일은 S6의 배포 채널 전환 전까지 `hand-mouse/`(v0.3)에서 빌드한다.

## 1. 목표

1. **구조 먼저.** 기능을 늘리기 전에 폴더·의존 방향·모듈 계약을 고정한다. 이후 기능은 정해진 자리에 모듈로 추가한다.
2. **UI 플랫폼 고정.** Qt/QML + Python 한 프로세스. 화면은 판정을 하지 않고 엔진이 만든 결과를 보여 주기만 한다.
3. **판정 방식 교체 가능.** 규칙 엔진, TCN 확인기, TCN 직접 판정을 같은 계약 뒤에 두고 설정으로 바꾼다.
4. **기록으로 검증.** 카메라 없이 녹화된 관절 기록을 엔진에 다시 넣어(재생) 동작을 비교한다. 이식은 이 재생 비교로 확인한다.

## 2. 왜 다시 짜는가 (v0.3 `hand-mouse/`에서 확인한 사실)

| 사실 (preview `36c3a01`) | 문제 | v0.4에서 |
| --- | --- | --- |
| `qt_runtime.py`의 `_step()` 한 함수가 카메라·손 인식·확대·손 선택·모드 전환·판정·마우스 출력·그리기·실패 기록·성능 기록을 모두 한다 | 기능 하나를 고치려면 전체 흐름을 알아야 한다. 여러 사람이 같은 함수를 고친다 | 단계별 모듈 + 조립 지점(`app.py`) 하나 |
| `motion_mode`의 진입 차단이 두 엔진의 **비공개 필드**(`_index_released_at`, `_scroll_released_at` 등)를 직접 읽는다 | 엔진 내부를 바꾸면 모션 모드가 조용히 깨진다 | 기능은 `activity()`로 상태를 공개하고, 중재기(Arbiter)만 읽는다 |
| `gesture_engine.py` 1,164줄, `mouse_controller.py` 1,247줄 | 판정·가속·스크롤 관성·창 전환·Windows 입력이 한 클래스에 섞여 있다 | 판정은 `features/`, 액션 이름은 `actions/`, 실제 입력은 `output/` |
| 연습 화면은 `practice_output()`이 빈 결과를 돌려주는 방식으로 Windows 입력을 막는다 | 연습 화면이 실제 이벤트를 받지 못한다 | 출력 싱크를 `연습`/`Windows`/`없음`으로 바꿔 끼운다 |
| 동작 이름이 곧 실행 명령이다(`"left_click"`) | 사용자 맞춤 연결을 넣을 자리가 없다 | 동작 ID와 액션 ID를 나누고 바인딩으로 연결 |
| 포인터 엔진(`pointer_engine.py`)은 이미 카메라·Qt·OS 없이 분리돼 있고, 분리 전후 24,000프레임 출력 불일치 0으로 확인됐다([engine-contract](../../../hand-mouse/docs/engine-contract.md)) | — | **이 방식을 전체에 적용한다** |

## 3. 폴더 구조

```text
apps/manux/
  AGENTS.md              에이전트 진입점: 읽는 순서, 규칙, 기능 추가 절차
  README.md
  pyproject.toml
  docs/
    architecture.md      이 문서: 구조·의존 규칙·프레임 흐름·이식 순서
    contracts.md         모듈 계약: 타입·실패 의미·불변식
    decisions.md         결정 기록
  src/manux/
    core/                계약 타입과 프로토콜(contracts.py). 표준 라이브러리만
    settings/            설정 스키마·기본값·이전 버전 변환, 프로필, 저장 경로
    capture/             카메라. 최신 프레임 하나만 유지
    link/                선택적 휴대폰 HTTPS·짝짓기·브라우저 페이지 (엔진·입력 없음)
    tracking/            MediaPipe 손 인식, 손 주변 확대, 조작 손 선택
    perception/          관절 → 특징(손 기준 좌표 local3d, 손 품질)
    features/            기능 모듈. 기능마다 하위 패키지 하나 (§5)
    ml/                  ONNX 모델 읽기, 모델 정보 검증
    actions/             액션 목록, 바인딩(동작 ID → 액션 ID), 라우터
    output/              출력 싱크: windows / practice / null, 안전 해제
    engine/              프레임 파이프라인, 모드 중재기, 단계 상태기계, Snapshot
    data/                학습 녹화, 실패 기록, 성능 기록, 로그 묶음
    platform/            Windows 전용: 전역 단축키, 모니터, 자동 업데이트
    ui/                  Qt 브리지(뷰모델)와 qml/(디자인 토큰, 컴포넌트, 페이지)
    app.py               조립 지점: 모든 계층을 import하는 유일한 파일
  tools/                 학습·재생·모델 내보내기. 앱 worker에 필요한 도구는 별도 MANUXWorker에 묶음
  tests/
    architecture/        의존 규칙 시험 (§4)
    contract/            계약 타입·프로토콜 시험
    unit/                모듈별 시험
    replay/              녹화 재생 비교 시험
```

`models/`(번들 손 모델, [models/README.md](../models/README.md))와 `packaging/`(PyInstaller + Velopack, `build.ps1`·`manux.spec`·`entry.py`)은 S6에서 추가했다. 자동 업데이트는 `platform/updater.py`다. 개인 녹화 원본은 이 폴더에 두지 않는다([계약 §9](contracts.md#9-학습-녹화-형식)).

## 4. 의존 규칙

화살표는 "import해도 된다"이다. 거꾸로는 금지다. `tests/architecture/test_layers.py`가 이 표를 검사한다. 표를 바꾸면 시험도 같은 PR에서 바꾼다.

| 계층 | import 가능한 MANUX 계층 | 허용 외부 라이브러리 |
| --- | --- | --- |
| `core` | 없음 | 표준 라이브러리만 |
| `settings`, `capture`, `tracking`, `perception`, `ml`, `data`, `platform` | `core` | 각자 필요한 것 (아래 금지 목록 제외) |
| `link` | `core`, `capture` | 지연 import하는 `aiohttp`, `cryptography` |
| `actions` | `core`, `settings` | |
| `features` | `core`, `perception`, `ml` | **다른 기능 모듈 import 금지** |
| `output` | `core`, `actions`, `platform` | |
| `engine` | `core`, `perception`, `actions`, `settings` | 기능·출력·데이터는 `app.py`가 주입 |
| `ui` | `core`, `engine`, `settings` | |
| `app.py`(헤드리스), `desktop.py`(데스크톱 앱), `__main__.py` | 전부 | |

외부 라이브러리 경계:

| 라이브러리 | 쓸 수 있는 곳 |
| --- | --- |
| `PySide6` (Qt) | `ui`, `platform`, `app.py` |
| `cv2`, `mediapipe` | `capture`, `tracking` |
| `pyautogui`, `ctypes`, `uiautomation`, `win32*` | `output`, `platform` |
| `onnxruntime` | `ml` |
| `openvino` (선택 의존성) | `ml` (실제 GPU/NPU 실행 장치 확인) |

| `aiohttp`, `cryptography` | `link` (명시적 서버/인증서 시작 때 지연 import) |
| `torch` | `tools/`만 (앱 프로세스에는 없음. 설치본의 별도 MANUXWorker에 포함) |

link는 기본 꺼짐이며 엔진·판정·Windows 입력·설정을 import하지 않는다. 서버/인증서는 명시적으로 켤 때만 aiohttp/cryptography를 import한다. 끊김 때 remote.read는 None을 반환하고 조립자는 기존 frame_missing 경로로 손 소실을 처리한다. 앱 조립은 #96 이후 별도 PR이다.

## 5. 기능 모듈

testing의 `spatial_pose`는 별도 96차원 영상 관절 특징·NumPy MLP와 기본 꺼짐의 거부권이다.
포즈 수집은 data, 특징은 perception, 모델은 ml, 학습·공유는 tools/worker에 둔다.
같은 패키지의 증거 생성과 guard만 상태를 공유하며 TCN의 가중치·특징·의도 출처를 바꾸지 않는다.
공통 모델 보관함·HF/GitHub 네트워크도 worker가 담당하고 UI는 결과를 표시한다.

기능은 `src/manux/features/<id>/`에 하나씩 둔다. 각 기능은 [계약 §5](contracts.md#5-기능-모듈-feature)의 `Feature`를 구현하고 다음을 함께 가진다.

| 들어가는 것 | 위치 |
| --- | --- |
| 판정 코드 (`Feature` 구현) | `features/<id>/__init__.py`와 하위 파일 |
| 내보내는 동작 ID 목록과 기본 바인딩 | 같은 패키지의 `GESTURES`, `DEFAULT_BINDINGS` |
| 설정 키와 기본값 | 같은 패키지의 `SETTINGS` (`<id>.` 접두사) |
| 설정 화면 (필요할 때만) | `ui/qml/pages/features/<id>/` + 뷰모델 |
| 시험 | `tests/unit/features/test_<id>.py`, 필요하면 재생 시험 |

기능은 서로 import하지 않는다. 다른 기능의 상태가 필요하면 중재기가 `activity()`를 모아서 `FrameContext`로 넘긴다. 기능 등록은 `app.py`의 목록 한 줄이다.

**기능을 나누는 기준 ([D-003](decisions.md#d-003-기능-모듈과-조립-지점-하나)):** 상태를 공유하며 겹치는 동작은 한 기능 안에 둔다. 독립적으로 켜고 끌 수 있는 것만 별도 기능으로 나눈다.

v0.3에서 옮길 첫 기능 후보 (이름은 제안):

| 기능 ID | 출처 (v0.3) | 비고 |
| --- | --- | --- |
| `pointer` | `pointer_engine.py`, `pointer_workspace.py` | 이미 분리됨. 먼저 옮긴다 |
| `pinch` | `gesture_engine.py`의 핀치 판정과 스크롤 잡기·드래그 | 좌·우클릭, 스크롤 잡기, 드래그가 누름 상태와 다시 누름 히스테리시스를 공유하므로 한 기능. 스크롤 관성(`mouse_controller.ScrollPump`)은 출력 쪽 |
| `motion` | `motion_mode.py` | 스와이프 → 뒤로/앞으로/탭 |
| `tcn_gate` | Dan `hand-mouse-gesture-overhaul@7448e4a`의 `gesture_gate.py`, `tcn_features.py` | `IntentFilter`로 구현 (§6) |
| `tcn` | v0.4 직접 판정 | `Feature`; 모델 정보의 동작·phase·모드로 의도를 냄. 규칙과의 출처 선택은 엔진이 담당 |

## 6. 프레임 흐름

```text
phone JPEG ─► link (인증·HTTPS) ─► capture.remote (최신 하나·해독)
                                      │
capture (USB/URL 또는 remote) ─► tracking ─► HandObservation | None
                              │
                       perception (특징)
                              │
              engine: Arbiter(모드 중재) ─► 활성 기능들 .step() ─► Intent[]
                              │
                     IntentFilter[] (예: tcn_gate)
                              │
                 actions: Router(바인딩) ─► ActionCommand[]
                              │
         output: 단계별 싱크 (Windows | 연습 | 없음) ─► OutputResult
                              │
                     Snapshot ─► ui (읽기만)
```

- **스레드:** 카메라 스레드는 최신 프레임 하나만 유지한다. 엔진 스레드는 한 번에 한 프레임만 처리한다. UI 스레드는 Snapshot을 읽고, 명령(`UiCommand`)은 큐로만 보낸다. 엔진은 Qt 객체를 만지지 않는다.
- **안전:** 단계 전환·모드 전환·일시정지·손 소실·설정 변경·종료 때 출력 싱크의 `release_all()`이 먼저 불린다. 누르고 있던 버튼이 남지 않는다.
- **Windows 입력은 한 곳에서만:** `output/windows`. 다른 계층은 Windows 입력 함수를 부를 수 없다(§4 시험).

## 7. UI 플랫폼 (Qt/QML)

- **화면은 결정하지 않는다.** QML은 Snapshot을 그리고 사용자의 선택을 `UiCommand`로 보낸다. 손짓 판정·임계값 계산·액션 실행은 QML과 뷰모델에 두지 않는다.
- **디자인 시스템:** `ui/qml/theme/`(색·글꼴·간격 토큰 싱글턴), `ui/qml/components/`(공통 부품), `ui/qml/pages/`(단계별 페이지). 페이지는 토큰만 쓰고 색·크기 값을 직접 적지 않는다.
- **단계:** v0.3의 `splash → welcome → capture → tutorial → main`을 그대로 쓰고 [계약 §8](contracts.md#8-단계와-출력-대상)의 표로 단계마다 출력 대상을 고정한다.
- **오버레이·트레이:** v0.3의 손가락 HUD(`finger_overlay.py`)와 트레이를 `ui/`로 옮긴다. 프레임 위 그리기(현재 엔진 안의 `cv2.circle`)는 QML 오버레이로 옮긴다.

## 8. 이식 순서

D-009 승인으로 v0.5 정식 설치본은 `apps/manux/`에서 빌드한다. `hand-mouse/`의 정식 발행은 비활성화한다. S2·S4의 v0.3 동등성 확인은 이식 당시 결과로 갈음한다. D-009(2026-11-06, Sean 승인)에 따라 v0.5부터 의도적으로 동작을 바꾸며 동등성을 유지하지 않는다. 현재 기능·계약 시험으로 검증한다. 측정하지 않은 단계는 완료로 표시하지 않는다.

| 단계 | 내용 | 완료 기준 | 상태 (2026-11-05 preview) |
| --- | --- | --- | --- |
| S0 | 폴더 골격, 계약 문서·코드, 의존 규칙 시험, 에이전트 안내 | 구조 시험 통과, 팀 검토 | 완료 (#53, CI 강제 #55) |
| S1 | `core` 보강, `settings`(v0.3 `gestures.json` 변환), `capture`, `tracking`, 재생 도구 | v0.3 녹화를 읽어 `HandObservation` 열로 재생 | 완료 (#56). Dan 녹화 28,557/28,557 손 재생 |
| S2 | `pointer` 기능 + `output/practice`·`output/null` | 이식 당시 비교(#58)로 갈음; v0.5 동등성 제약 없음(D-009) | 완료 (#58), 합성 비교 |
| S3 | `output/windows` (커서 매핑·클릭·스크롤 관성·창 전환을 `mouse_controller.py`에서 분리) | 실제 Windows에서 이동·클릭·드래그·스크롤 수동 확인 기록 | 코드 병합 (#57). **실제 Windows 수동 확인 미실행** |
| S4 | `pinch`(클릭·스크롤 잡기·드래그), `motion` 기능, 중재기(비공개 필드 읽기 제거) | 이식 당시 비교(#59~#61)로 갈음; v0.5는 모션 진입·현재 계약 시험(D-009) | 완료 (#59 엔진, #60·#61 기능), 합성 비교 |
| S5 | `tcn_gate`, `ml`, `data/recorder`, `tools/train_tcn.py` (Dan 브랜치에서) | 모델 정보 검증, 확인기 켬/끔 재생 비교 | 코드 병합 (#60·#62). 모델 정보 검증 완료. **실제 녹화로 확인기 켬/끔 비교 미실행** |
| S6 | 데스크톱·Preview/정식 설치본·선택 Trainer | 합성/설치 검사와 실제 웹캠·Windows 수동 확인을 구분 | v0.5 채널 전환 승인(D-009, 2026-11-06). 기본 설치본은 torch worker 제외, -WithTrainer는 선택 구성. production의 manux-release.yml이 apps/manux를 발행. **실제 발행·웹캠·Windows 수동 확인은 미실행** |

S2와 S3, S4와 S5는 서로 다른 폴더를 고치므로 동시에 진행할 수 있다.

## 9. 범위 밖 (v0.4)

- 기존 웹앱(`src/`, `public/`, `server.js`)과 Electron(`desktop/`)의 새 기능. 기존 문서는 그 앱의 기록으로 둔다.
- 5~10m 원거리 다중 TCN, 시선 추적 연동. 실험 결과가 나오면 v0.5 범위에서 다시 정한다.
- 책상 위 포인터 정밀도만을 위한 조정([제품 방향](https://github.com/intel7-ai/manux/blob/product-direction-in-bed/docs/product-direction.md)).


## v0.5 통합 보충

스튜디오 통합은 [통합 기록](v05-integration.md)을 따른다. §4 의존 방향은 변경하지 않는다. MLP 저장/추론은 ml, 순수 특징은 perception, 표본/내부 확인 분할은 data, 학습 worker는 tools, 조립·런타임 교체는 desktop.py에 둔다. 수집 화면의 비동기 읽기 완료는 queued Qt signal로 알린다.
