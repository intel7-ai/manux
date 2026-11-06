# 자세 보조 (pose assist)

프레임 하나의 손 모양(관절 각도·비율 33개)으로 클래스 확률을 내는 작은 MLP가 **TCN을 보조**한다. TCN을 대체하지 않는다.
기본은 꺼짐이다. 이 문서는 구현된 것, 제안인 것, 구현하지 않은 것을 나눠 적는다.

## 1. 구현됨 (이 브랜치, 합성 데이터 시험까지)

| 부분 | 위치 | 내용 |
| --- | --- | --- |
| 특징 `pose-angles-v1` | `perception/pose_angles.py` | `local3d-v1` 손 좌표계 위 33개 비좌표 특징. 거리·코사인·x/y 성분만 쓰므로 이동·크기·회전·좌우반전에 변하지 않는다. 퇴화 입력(관절 없음·NaN·길이 0)은 `None`. 공식을 바꾸면 버전 문자열을 올린다. 시험 벡터 고정(`tests/unit/perception/test_pose_angles.py`) |
| 증거 `PoseFeature` | `features/pose` | `Feature`지만 `PoseEvidence`만 만든다. `Intent`를 내지 않는다. 손·월드 관절이 없거나 모델 오류이거나 확률이 문턱 미만이면 **침묵** |
| 보호기 `PoseGuard` | `features/pose` | `IntentFilter`(계약 §6). 의도를 버리기만 한다. 필터 체인 맨 뒤 |
| 엔진 훅 | `engine/pipeline.py` | 필터의 선택적 `drain_events()` 메모를 Snapshot `events`에 넣는다. 다른 필터에는 영향 없음 |
| 조립 | `app.py` | `--pose-model` 또는 `pose_assist.enabled`+`pose_assist.model`. 직접 TCN 모드는 `tcn_gate`만 거부하고 보호기는 허용 |
| 학습 도구 | `tools/pose_data.py`, `tools/train_pose.py` | 녹화(jsonl.gz) → 구간(segment) → 구간 단위 분할 → window=1 MLP → ONNX + 모델 정보 |

### 동작

- **거부권(veto):** `veto_gestures`(기본 `pinch.thumb_ring,pointer.right_click`)의 `tap`/`begin`은, 모델의 1순위가 **다른 클래스**이고 확률 ≥ `tau_veto`인 상태가 `k_frames` 연속이면 버린다. 증거가 모자라거나 약하거나 모델이 모르는 동작이면 그대로 통과.
- **휴식(idle) 게이트:** 1순위가 `none`이고 확률 ≥ `tau_idle`인 상태가 `k_frames` 연속이면 `idle_sources`(기본 `tcn`) 또는 `idle_gestures`의 새 `begin`을 버린다(`idle_taps`를 켜면 `tap`도).
- 버린 `begin`의 `hold`/`end`는 함께 버리고, 통과한 `begin`의 것은 통과한다. 모드 진입/종료 의도와 다른 동작은 항상 통과한다.
- **시작 힌트 없음(v1).** 이 모듈은 동작을 시작·유지·종료할 수 없다.
- 동작 ID ≠ 액션 ID(D-004): 클래스→동작 ID 대응은 모델 정보의 `gestures`에 있고 액션 이름은 어디에도 없다.

### 설정 (모두 기본 꺼짐)

| 키 | 기본 | 범위 |
| --- | --- | --- |
| `pose_assist.enabled` | `false` | 켜기 |
| `pose_assist.model` | `""` | `.onnx` 경로(옆에 같은 이름 `.json`) |
| `pose_assist.tau_veto` | `0.9` | 0.5–0.999 |
| `pose_assist.tau_idle` | `0.9` | 0.5–0.999 |
| `pose_assist.k_frames` | `3` | 1–30 |
| `pose_assist.veto_gestures`, `idle_class`, `idle_sources`, `idle_gestures`, `idle_taps` | 위 동작 절 | 쉼표 구분 문자열 / bool |

켜는 법: `python -m manux.app --pose-model <경로>.onnx` 또는 설정에서 `pose_assist.enabled=true`와 `pose_assist.model`. 모델이 없거나 모델 정보가 맞지 않으면 보조 없이 실행하고 이유를 남긴다.

### 학습

```
cd apps/manux
uv run --python 3.12 --extra train python tools/train_pose.py <train.jsonl.gz ...> [--eval <eval.jsonl.gz ...>] --output <저장소 밖>/pose.onnx
```

- 분할은 **구간 단위**다. 이웃 프레임은 독립 시도가 아니므로 프레임을 무작위로 나누지 않는다. `--eval` 파일이 학습 파일의 복사본이면 거부한다.
- 구간이 적은 클래스(기본 8 미만), 평가 구간이 2 미만인 클래스, 한 사람 데이터는 경고하고 모델 정보 `pose.warnings`에 남긴다. `--strict`는 경고를 오류로 바꾼다.
- 모델 정보는 `ml.card` 검증을 통과한다(`window=1`, `features=33`, `feature_version=pose-angles-v1`, `role=pose_evidence`).
- 저장소 안에 모델을 쓰지 않는다(D-006).

### 시험으로 확인한 것

- 보조가 꺼져 있거나 침묵/오류일 때 엔진 출력(Snapshot·명령·해제)이 보조 없는 엔진과 동일함(무작위 입력 비교). 말하는 보조는 다르게 동작함을 대조 시험으로 확인(비어 있는 시험 방지).
- 거부권·휴식·`begin` 연쇄 처리·모드 의도 통과. 변이 확인 결과는 PR 보고에 기록한다.
- 합성 손으로 학습→ONNX→`create_pose_assist` 로드→증거·거부권 경로.

## 2. 제안 (확정 아님, D-009)

- 박현규 분석(한 사람·하루, 구간 묶음 5-fold, **이미지 좌표·park 어휘** 기준)의 수치: 33개 비좌표 특징 정확도 약 82.5%, 문턱 ≥ 0.9 거부권으로 우클릭 오인식 약 1.5~1.7%, idle 재현율 약 93.8%. **preview의 `local3d-v1`·어휘로 다시 측정하지 않았다.** 이 값을 제품 효과로 말하지 않는다.
- 기본 문턱 0.9, `k_frames=3`은 그 분석에서 온 시작값이다. preview 데이터로 조정해야 한다.
- 시작 힌트, 학습 센터의 불일치(mismatch) 수집은 후속이다.

## 3. 구현하지 않음 / 미측정

- 실제 모델·실제 웹캠에서의 효과, TCN과의 비교, 오실행 감소·지연 증가: **미측정**.
- 데스크톱 앱(`desktop.py`) 연결: `reload_model`이 파이프라인을 새로 만들며 보호기를 버리므로 미연결(후속).
- 학습 센터의 구간 단위 불일치 수집 모드·라벨 분포 경고 UI.
- 여러 사람·클래스당 수십 개 독립 구간의 데이터. 지금 모델 품질은 말할 수 없다.
- 부분 등록, 모델 교체 시 보호기 재조립.
