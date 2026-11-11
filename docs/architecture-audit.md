# 구조 조사 (Phase 0, 2026-11-09)

명세: `hand-mouse-online-continual-learning-spec-v2.md` 2절. 대상은 `feat/crop-cnn-distance-course-20261109`를 푼 사본(`.git` 없음, 원래 PC와 다른 RTX 3060 PC)이다. 아래는 코드를 읽어 확인한 사실이다. 전체 줄을 다 읽지 않은 큰 파일(`gesture_engine.py` 1727줄, `mouse_controller.py` 2171줄, `qt_dashboard.py`, `training_center.py`)은 해당 경로만 확인했다.

## 한눈에
- 동작 판정의 주체는 **규칙 엔진**(`GestureEngine`)이다. 학습 모델은 규칙이 낸 동작을 승인하거나 막는 확인기(`GestureGate`)일 뿐이고, 화면에서 켜야 동작한다(`UiState.tcn_gate` 기본 `False`).
- 학습은 모두 **녹화 후 일괄 학습**이다. 사용 중에 가중치를 바꾸는 경로는 없다.
- 즉시 등록과 실시간 적응 코드는 있지만 **앱에 연결되어 있지 않다**.

## 항목별

| # | 항목 | 코드 | 현재 동작 | 문제 | 재사용 |
|---|---|---|---|---|---|
| 1 | 카메라·프레임 | `src/camera.py` `_grab_loop`, `_read_latest`, `read` | 전용 스레드로 읽고 최신 프레임만 처리. 거울 처리, 폰 연결 | 없음 | 그대로 |
| 2 | MediaPipe | `src/hand_tracker.py` `HandTracker.detect`, `_detect_crop`, `_crop_pass`. 팔은 `src/pose_tracker.py` | VIDEO 모드. 확대 영역 전용 landmarker, TTA | 먼 손을 못 찾음(`far-hand-loss.md` 22~24절). **앱에서 학습 불가** | 그대로 |
| 3 | 관절·좌우 | `HandDetection`, `src/hand_selection.py`, `src/tcn_features.py` `hand_frame` | 3D 관절을 손 자체 좌표계로 정규화 | 없음 | 특징 그대로 |
| 4 | 손 그림·CNN | `src/hand_crop.py`(검출 영역), `src/crop_recorder.py`, `src/crop_cnn.py`, `scripts/train_crop_cnn.py` | CNN은 오프라인 평가만, 앱에 없음 | 11-09까지의 그림은 표시로 오염(24절). 기존 CNN 수치 무효 | 다시 재서 결정 |
| 5 | 거리 판정 | `GestureEngine.process`, `config/gestures.json`의 `pinch_size_ratio` 등 | 손 크기에 비례한 고정 기준 | 학습되지 않음, 사람·카메라별 조정 필요 | 안전 규칙·대조군으로 유지 |
| 6 | 평활·상태 | `GestureEngine`(hysteresis), `filters.py`, `path_smoother.py`, `cursor_interp.py`, `GestureGate.confirmed`(연속 k프레임) | 포인터와 동작 판정이 엔진에 함께 있음 | 동작 판정만 교체하기 어려움 | 포인터 경로는 유지 |
| 7 | 정적·동적 | `src/gesture_catalog.py` `Task`(분류), `src/motion_mode.py`(규칙), TCN 15프레임 | 쓸기는 규칙, 나머지는 손 모양+짧은 창 | 한 모델로 통합되어 있지 않음 | 분류표 재사용 |
| 8 | 시퀀스 | `tcn_features.windows`(15 고정), `GestureGate._frames`, 안내 녹화 단계(`prep`/`do`/`rest`) | 정답 구간은 안내 일정으로 정함 | **일반 사용 중 동작 구간 분리 없음**. 일정 기반 정답이라 배경 단서가 섞임 | 창 생성만 재사용 |
| 9 | 마우스 경로 | `qt_runtime._step` → `engine.process` → `gate.filter` → `mouse.apply`. `action_bindings.py` | 안내 녹화 중에는 OS 입력 없음 | 안전 게이트가 확인기와 한 몸 | 경로 유지, 게이트 확장 |
| 10 | 오류·정정 | `src/failure_log.py` `FailureRecorder`, `record_negative_candidate`. F 키(`failure_mark`), 동작 직후 일시정지 | "오작동 후보" 기록만(`auto_retrain=False`) | **정답 라벨을 입력하는 흐름이 없음** | 후보 수집에 재사용 |
| 11 | 설정·실행 | `config/gestures.json`, `pyproject.toml`(`qt`, `train` 선택), `uv.lock`, `studio.py`, `main.py --ui qt` | 추가 묶음 없이 실행하면 torch 없음 | 온라인 학습은 torch가 필수. GPU torch는 `..\hand-mouse-gpuenv` | 실행 방식 조정 |
| 12 | 성능 | `src/perf_log.py`, 아래 측정 | 앱 추론은 ONNX CPU + numpy MLP | 먼 손에서 전체 34~45ms로 30fps 초과 | 측정 도구 재사용 |

## 학습에 재사용할 수 있는 코드
| 코드 | 내용 | 연결 상태 |
|---|---|---|
| `scripts/train_tcn.py` `TCN`, `train`, `augment`, `load_training_sessions` | 인과 TCN(3층, 팽창 1·2·4, 15프레임, 133특징, 64채널, 50,824 매개변수). `init_model`로 이어 학습 가능 | 일괄 학습으로 사용 중. `head`가 분리되어 있어 임베딩을 꺼내기 쉬움 |
| `src/capture_dataset.py` `load_sessions`, `refine_guided_transition_labels` | 녹화 → 창·정답·가중치 | 사용 중 |
| `src/data_quality.py` | 프레임 품질 점수 | 사용 중 |
| `src/spatial_mlp.py` `SpatialMlpModel` | 프로토타입, Mahalanobis OOD, `register_custom_gesture`, `adapt_prototype_ema`, `rollback_prototypes`, SupCon | 학습·평가에만 사용. 등록과 적응은 **앱에서 호출하지 않음** |
| `GestureGate.register_custom_gesture`, `record_runtime_adaptation` | 위 기능의 앱 쪽 입구 | **호출하는 곳 없음**(`src`, `main.py` 검색) |
| `src/gesture_models.py` `publish_bank`, `training_lock`, `load_single` | 모델 원자적 게시, 해시 검증, ONNX 불러오기 | 동작별 학습에서 사용 |
| `UiState.model_version`, `QtRuntime._gate_for` | 버전이 바뀌면 실행 중 확인기를 새 모델로 교체 | 사용 중. 후보 모델 교체의 기반 |
| `training_center.backup_current_model`, `src/fold_cache.py` | 백업, 끊긴 학습 이어하기 | 사용 중 |
| `scripts/gate_replay.py` | 진짜 확인기로 녹화 재생 | 11-09 추가 |

프로토타입 코드는 `SpatialMlpModel`(한 프레임 96차원, 시간 정보 없음)에 묶여 있다. 동적 동작까지 다루려면 TCN 임베딩 쪽에도 같은 기능이 필요하다.

## 현재 기준값 (실제 측정)

### 실시간 학습 비용 (RTX 3060 12GB, 지금 TCN, 무작위 입력, 앱 실행 중, 40회 중앙값)
| 장치 | 배치 32 | 배치 64 | 배치 256 | 추론 1회 |
|---|---|---|---|---|
| GPU | 2.4ms | 2.6ms | 2.5ms | 0.41ms |
| CPU | 1.8ms | 2.7ms | 5.6ms | 0.20ms |

GPU 메모리 최대 24MB(예약 44MB). 매개변수 변화는 측정으로 확인(0 아님). **이 크기의 모델은 계산 비용이 병목이 아니다.** 지금 학습이 오래 걸리는 이유는 약 12만 창을 25에폭 돌리고 자세 23개를 하나씩 빼며 평가하기 때문이다(판당 TCN 48초, MLP 47초). 카메라 루프와 동시에 돌릴 때의 영향은 아직 재지 않았다.

### 지금 앱의 판정 (`gate_replay.py deployed`, 원래 PC 거리 코스 녹화 2개, 엄지+검지, 손이 보일 때만)
| 거리 | 규칙만 | 확인기 포함 |
|---|---|---|
| 1m | 14/20, 오반응 15 | 14/20, 4 |
| 2m | 14/20, 33 | 13/20, 19 |
| 3m | 18/20, 14 | 18/20, 4 |

### 모델 카드 (`models/gesture_tcn.json`, 11-08 학습, 녹화 26개)
처음 보는 자세의 인식률: 엄지+검지 93.8%, 약지 86.5%, 중지 74.2%, 새끼 72.9%, 옆 손 87.8%, V 100%, 엄지 위 100%. 이동 중 오작동 분당 2.27회(규칙 8.84). 한 사람·한 카메라.

### 이 PC의 손 놓침
| 녹화 | 1m | 2m | 3m |
|---|---|---|---|
| `204210` 수정 전 | 7% | 16% | 60%(3회에서 멈춤) |
| `205537` 이어받기 | 2% | 44% | 25% |
| `211228` 검색 켬(지금 꺼짐) | 5% | 70%(2회에서 멈춤) | - |

녹화마다 조건이 달라 직접 비교는 어렵다.

### 시험
전체 656개 통과. 화면 단추·실제 카메라 동작은 자동 시험 범위 밖이다.

## 재현한 실패
- 먼 거리 손 놓침: 위 표. **온라인 학습으로 고칠 수 없다.**
- 21:28 이후 약 1시간 40분(프레임 약 18만 개) 동안 앱 기록에 손 검출 0건. 그 시간에 사용자가 카메라 앞에 있었는지 확인하지 못했다. 앱 문제인지 아직 모른다.

## 기록해 둘 불일치
- TCN은 8분류, 공간 MLP는 `swipe_tab_left` 포함 9분류(`models/`). 함께 쓰면 공통 분류만 섞인다.
- 모델 분류 수가 고정이라, 지금 구조로는 새 동작 추가에 재학습이 필요하다.
- `pinch_report`의 "TCN 통과" 열은 안내 녹화에서 정답이 그대로 찍힌 값이다(21절).
