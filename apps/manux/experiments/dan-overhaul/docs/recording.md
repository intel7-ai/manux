# 학습 데이터 녹화 (2026-11-05)

손짓 판정을 학습 모델(TCN)로 보완하기 위한 데이터 녹화. 코드: `src/recorder.py`(파일 쓰기·읽기), `src/guided_recording.py`(안내 순서·정답 표시), 연결 `src/qt_runtime.py`(`_recording_mode`, `_record_frame`), 화면 `src/dashboard.qml`("학습 데이터 녹화").

## 쓰는 법 (Qt 화면, 손바닥(Dan) 모드)
- 왼쪽 패널 **학습 데이터 녹화**: 끔 / 안내 녹화 / 평소 녹화. 고르는 순간 녹화가 시작되고, **끔**이나 **녹화 끝**을 누르면 파일이 저장된다(앱을 꺼도 닫힌다).
- **안내 녹화**: 자세 3가지(평소 / 기대거나 옆으로 돌린 / 폰 위치를 바꾼) × 동작 3가지(엄지+약지, 엄지+검지 스크롤, 반주먹 이동) = 9묶음. 묶음마다 자세를 잡고 **시작**. 동작은 "준비(1초) → 하세요(1.6초) → 쉬기(1.6초)" × 15회, 반주먹 이동은 3분. 녹화 중에는 마우스가 움직이지 않는다. 전부 약 15분.
- **평소 녹화**: 평소처럼 쓰면서 녹화. 정답은 "아무 동작 아님"이고, 잘못 판정된 순간 **F**(또는 "방금 이상했어요")를 누르면 표시가 남는다.

## 파일
`recordings/<날짜-시각>-<guided|free>.jsonl.gz` (git에 올라가는 폴더. 영상 없음).
- 첫 줄 `meta`: PC 이름·사용자, 카메라(번호, 실제 해상도·fps, 거울 모드, `camera_note` 설정값 = "폰 카메라 …"), 조작 손, 프로필, 그때 규칙 기준값.
- `frame`: 시각, 손 화면 관절 `lm`(x, y, z), 3D 관절 `world`(미터, 카메라 각도에 덜 민감), 손 방향·점수, Pose 어깨·팔꿈치·손목 `pose`(최근 0.25초 안), 정답 `label`(`target`: thumb_ring / thumb_index / none / transition / idle, 자세·회차), 규칙 판정 `rule`(action, gesture, 배율, 굽힘, 손 크기), 커서.
- `event`: `mark`(F), `set_done`. 마지막 줄 `end`: 정답별 프레임 수.
- 정답 쓰는 법: `thumb_ring` / `thumb_index` = 그 동작, `none` = 아무 동작 아님(쉬기, 반주먹 이동), `transition`(준비 중)과 `idle`(시작 대기)은 학습에서 뺀다.

## 확인
- 시험 `tests/test_recording.py`: 순서·정답 표시, 파일 형식, 실행부(가짜 카메라)에서 안내 녹화 중 커서가 그대로이고 정답이 붙은 프레임과 F 표시가 저장됨.
- 실제 폰 카메라로 녹화해 본 것은 아직 없다.

## 첫 TCN 학습 결과 (2026-11-05 03:3x)
- 데이터: `recordings/20261105-030947-guided.jsonl.gz` (집 PC, 폰 카메라, 17분, 정답 붙은 0.5초 구간 23,587개).
- 학습: `uv run --locked --extra qt --extra train python scripts/train_tcn.py --folds` (자세 하나씩 빼고 세 번 학습 + 전체로 최종 학습, CPU로 한 번에 약 40초).
- 특징: `src/tcn_features.py` (3D 관절을 손 기준 좌표계로, 손 크기로 나눔 → 카메라 각도·거리와 무관).
- 모델: 원인 지연 합성곱 3층(채널 64, 15프레임), 출력 none / thumb_index / thumb_ring. 판정: 확률 0.7 이상이 3프레임 연속.
- 학습에 안 쓴 자세에서 (규칙 → TCN):

| 빼 둔 자세 | 엄지+검지 인식 | 엄지+약지 인식 | 반주먹 이동 중 오작동(3분) |
|---|---|---|---|
| 평소 자세 | 10 → 13 /15 | 8 → 14 /15 | 15 → 2 |
| 기대거나 돌린 자세 | 10 → 13 /15 | 11 → 11 /15 | 21 → 1 |
| 폰 위치를 바꾼 자세 | 5 → 12 /15 | 6 → 11 /15 | 15 → 3 |

- 결과 파일: `models/gesture_tcn.onnx`(209 KB, onnxruntime으로 한 번 판정 0.06 ms), `models/gesture_tcn.json`(정답 종류, 구간 길이, 특징 버전, 기준값, 데이터 출처, 위 결과).
- 한계: 한 사람·한 카메라·한 날의 녹화.

## 앱에 붙임: 손짓 확인 (2026-11-05)
- 코드: `src/gesture_gate.py`, 연결 `src/qt_runtime.py`(`_gate_for`, 손바닥 모드). Qt 왼쪽 패널 **손짓 확인 (TCN: 우클릭·스크롤)** 끔/켬(모델 파일이 있으면 기본 켬).
- 규칙이 그대로 판정하고, 학습 모델이 확인한다. 매 프레임 최근 0.5초로 확률을 구하고:
  - 우클릭: 최근 0.5초 안에 thumb_ring이 0.7 이상 3프레임 연속이면 보낸다. 아니면 0.3초까지 기다렸다가 확인되면 보내고, 아니면 버린다.
  - 스크롤 잡기: thumb_index가 확인된 뒤에만 시작한다(그 전엔 커서가 멈춰 있음). 시작된 잡기는 끝까지 간다.
  - 좌클릭·커서·다른 손짓은 그대로(모델이 아직 이 둘만 배웠다).
- 녹화를 앱과 같은 방식으로 재생(최종 모델은 이 녹화로 학습했으므로 학습 데이터 안의 수치): 반주먹 이동 중 우클릭 12 → 0, 스크롤 8 → 0, 엄지+약지를 하는데 스크롤 10 → 0. 동작을 알아본 횟수는 규칙과 거의 같다(우클릭 27 → 27 /45, 스크롤 24 → 22 /45): 확인기는 규칙이 놓친 것을 살리지는 못하고 잘못 나간 것만 막는다. 인식률까지 올리려면 다음 단계로 모델이 직접 판정해야 한다.
- 시험: `tests/test_gesture_gate.py`. 실제 손으로는 아직 확인하지 않았다.

## 녹화 직후 자동 품질 검사·정제·오프라인 증강·재학습 파이프라인
- 녹화 중에는 원본 데이터를 그대로 저장하며, 녹화 종료 시점(`QtRuntime._stop_recording` → `training_center.run_post_recording_pipeline`)에 순서대로 후처리를 수행한다.
  1. **품질 검사 (`src/data_quality.py` - `check_recording`)**: 손 미검출 비율, 비어 있거나 너무 짧은 `do` 구간, 급격한 관절 점프/이상치, 비정상 손 크기, 클래스/자세별 데이터 부족, `transition`/`idle` 과다 비율을 평가해 `QualityReport`(`score` 0~1, `status`: `"pass"` / `"warning"` / `"fail"`, 문제 구간 목록, 요약 메시지)를 생성한다.
  2. **기준 미달 시 중단**: `status == "fail"`이면 학습 센터에 경고 로그를 남기고 정제·증강·재학습을 중단한다.
  3. **자동 정제 (`src/data_quality.py` - `clean_recording`)**: 원본 파일(`recordings/*.jsonl.gz`)은 절대 덮어쓰지 않고 `recordings/cleaned/` 아래에 별도 저장한다. 짧은 손 미검출 구간(1~2프레임) 선형 보간, 단일 프레임 관절 점프 스무딩/제외, 기준 미달의 짧은 `do` 구간 마스킹(`transition`/`idle`), 미검출 프레임 제거를 수행한다.
  4. **오프라인 시퀀스 증강 (`src/offline_augment.py` - `augment_recording_file`)**: 활성화 시 정제된(또는 원본) 시퀀스에 3D 회전(±30°~45°), 스케일(±10%~15%), 소량 가우시안 관절 노이즈, 시간축 늘리기/줄이기, 랜덤 프레임 드롭을 적용해 `recordings/augmented/`에 N배(기본 4배) 생성하거나 학습 시 메모리 내(`--offline-augment`)로 공급한다. `scripts/train_tcn.py`의 기존 배치 단위 온라인 `augment()`는 학습 중 그대로 유지된다.
  5. **선택적 자동 재학습**: `auto_retrain_after_recording: true`이고 품질 점수가 `min_quality_score` 이상이면 `scripts/train_tcn.py` 학습을 자동으로 시작한다.
- 설정 키 (`config/gestures.json` 및 `src/gesture_engine.py` `DEFAULT_CONFIG`):
  - `auto_quality_check`: `true`
  - `auto_clean`: `true`
  - `auto_offline_augment`: `false`
  - `auto_retrain_after_recording`: `false`
  - `min_quality_score`: `0.7`
  - `offline_augment_factor`: `4`
  - `offline_augment_rotation_deg`: `40`
  - `offline_augment_scale`: `0.15`
