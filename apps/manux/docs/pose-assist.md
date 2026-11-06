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

### 휴식 억제와 게이트 모드 주의

`idle_sources`의 기본값은 `tcn`이라 휴식 게이트는 **직접 TCN이 낸 의도**에만 걸린다. TCN 확인기(`tcn_gate`) 경로에서는 의도의 출처가 규칙 기능 ID(`pinch` 등)이므로 기본 설정에서는 휴식 억제가 **동작하지 않는다**(거부권은 출처와 무관하게 동작). 확인기 경로에서 휴식 억제를 쓰려면 `pose_assist.idle_gestures`에 억제할 동작 ID(예: `pinch.scroll`)를 쉼표로 지정하거나 `idle_sources`에 해당 출처를 추가한다. 코드 기본값은 바꾸지 않았다.

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
- `--eval`을 주지 않으면 같은 녹화의 인접 구간이 학습과 보류 양쪽에 들어가(같은 사람·같은 세션) 보류 결과가 낙관적일 수 있다. 이때 경고를 출력하고 모델 정보 `pose.warnings`에 남긴다(`--strict`와는 무관). 공정한 수치에는 별도 `--eval` 녹화가 필요하다.
- 모델 정보 `data` 출처의 `person`은 해시 별칭이다. 기존 `train_tcn.py`는 녹화의 `person` 원문을 그대로 카드에 쓴다(관행이 다름).
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
- 데스크톱 앱 화면(UI)에서의 표시·조작: 보조 상태(`pose_status`)를 ViewFrame에 싣지 않았고, 모델 교체 요청(`activate_pose_model`)을 보내는 화면도 없다. 런타임 연결은 4절.
- 학습 센터의 구간 단위 불일치 수집 모드·라벨 분포 경고 UI: 이 브랜치에 넣지 않는다(별도 PR).
- 여러 사람·클래스당 수십 개 독립 구간의 데이터. 지금 모델 품질은 말할 수 없다.
- 부분 등록, 모델 교체 시 보호기 재조립.

## 4. 데스크톱 런타임: 모델 교체와 보조 유지

`desktop.build(..., pose_model=...)`(`--pose-model` 또는 설정 `pose_assist.enabled`+`pose_assist.model`)가 보조를 만들어 엔진에 붙이고 `DesktopRuntime.pose_assist`에 둔다. 꺼져 있으면 `None`이고 아래 모든 경로는 보조가 없던 때와 같다.

| 상황 | 동작 |
| --- | --- |
| TCN 모델 교체(`reload_model`, 활성 store 포인터 변경) | 같은 보조 **인스턴스**를 새 파이프라인에 다시 붙인다(특징은 TCN 뒤, 보호기는 필터 맨 뒤). `replace_pipeline`이 눌린 출력을 풀고 모든 부분을 reset하므로 `k_frames` 연속 횟수·열린 구간·증거가 0에서 다시 시작한다. 모델 교체가 pose 모델을 바꾸지 않는다 |
| TCN 교체 실패 | 기존 파이프라인 그대로(보조 포함) 유지, 기존 `model_status` 메시지 |
| pose 모델 교체(`RuntimeRequest("activate_pose_model", 경로)` → `reload_pose`) | 새 모델을 먼저 읽는다. 성공하면 이전 특징·보호기를 빼고 새 쌍으로 교체(출력 해제·횟수 초기화), TCN 특징·라우터·출처는 그대로. 보조가 없던 앱에 새로 붙일 수도 있다 |
| pose 모델 교체 실패(없음·카드 불일치 등) | 이전 보조를 **그대로 유지**하고 `pose_status`에 이유를 남긴다(이전 보조가 없었으면 꺼진 채 이유만 남김). 보조를 끄지는 않는다 |
| 보조 꺼짐 | `reload_model`은 이전과 같은 인자로 `replace_pipeline(parts[0], (), ...)`를 부른다(시험으로 고정) |

pose 모델에는 TCN 같은 활성 store 포인터 규약이 아직 없다. 그래서 교체는 파일 경로를 직접 받는 요청이며, 개인 모델 설치·검증 흐름(D-005, 계약 §10)은 후속이다.
시험: `tests/unit/test_desktop_pose.py`. 변이 확인으로 (보호기 누락, 특징만 유지, 이전 쌍 중복, 실패해도 교체) 4가지가 모두 시험에 걸렸다는 것은 **작성자 보고이며 리뷰에서 재현하지 않았다**(로그 없음).

## 5. 측정 절차 (아직 실측하지 않음)

**실측은 하지 않았다.** `tools/pose_compare.py`는 합성 녹화와 가짜 모델로 동작만 확인했다. 아래 절차로 실제 데이터를 돌리기 전에는 보조의 효과를 주장하지 않는다.

필요한 것
- eval 녹화(`split=eval`, 월드 관절 포함, 학습에 쓰지 않은 사람·세션이 바람직). 라벨: `label.target`(클래스), `label.gesture`(동작 ID), 휴식 구간은 `idle` 라벨. 구간 수는 클래스당 수십 개를 목표로 한다.
- 후보/활성 TCN 모델(`.onnx`+카드)과 pose 모델. 카드 `data[].sha256`에 학습 녹화가 모두 있어야 하고 eval 녹화와 겹치면 도구가 거부한다. **겹침 검사는 파일 바이트의 SHA-256이 같은 경우만 잡는다.** 같은 세션을 다시 내보낸 파일이나 같은 사람의 인접 세션은 통과하므로, eval은 학습과 다른 날·다른 세션(가능하면 다른 사람)으로 사람이 확인해야 한다.

명령
```
cd apps/manux
uv run --python 3.12 python tools/pose_compare.py <eval...> --tcn-model <tcn.onnx> --pose-model <pose.onnx>     [--tcn-mode direct|gate] [--idle-labels idle none] [--tau-veto 0.9 --tau-idle 0.9 --k-frames 3] --json out.json --markdown out.md
```
조건: A 규칙 단독, B 규칙+TCN, C B+자세 보조. 출력에는 이름·경로가 없다(녹화·모델은 해시 앞 12자).

결과표 양식(도구가 그대로 만든다): 오인식 우클릭 횟수와 분당 비율, 휴식 프레임 수와 그 중 커서 이동 프레임·클릭 발생, 클릭 성공률(구간 단위)과 지연(프레임), 거부권·휴식 게이트가 취소한 횟수(정당/부당/미라벨), 자세 보조 침묵 프레임 비율. 판정은 프레임 단위이며 취소의 "정당"은 그 프레임의 라벨이 해당 동작이 아니라는 뜻이다. 분당 비율의 분모 시간은 프레임 간격을 **0.5초로 상한**해 합산하고 마지막 프레임은 제외한다. 침묵 비율은 손이 보이는 프레임만 분모로 한다.

D-009 판단 기준 초안 (확정 아님): **C가 B보다 오인식 우클릭과 휴식 중 커서 이동을 줄이고, 클릭 성공률이 줄지 않을 때만 채택한다.** 그 밖에 부당한 취소가 정당한 취소보다 많거나 침묵 비율이 매우 높으면 보류한다. 임계값(`tau_*`, `k_frames`)은 eval이 아니라 별도 검증 녹화로 정하고 eval에서는 한 번만 확인한다. 한 사람·한 세션 결과는 일반화로 말하지 않는다.
