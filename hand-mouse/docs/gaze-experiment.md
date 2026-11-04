# 시선 추적 실험 1단계 (hand-mouse)

> 상태: **실험, 사람 검수 전**. 이 단계는 시선이 쓸 만한지 **재기 위한 도구**를 만든다. 시선으로 커서를 움직이는 기능은 없고, 손 포인터 동작은 바뀌지 않았다.
>
> 배경: 팀 조사 문서(`reports/webcam-input-feasibility.md`, `reports/webcam-gaze-hand-extension.md`, 조정자 PC의 로컬 파일)의 결론 — 일반 웹캠 시선 오차는 보고치로 약 2~4°(60cm·24인치 FHD에서 1° ≈ 38px)라 시선만으로 커서(구조 B)는 어렵고, **구조 C(화면을 보고 있나)** 가 가장 안전하며, **구조 A(시선 접근 + 손 미세 조정)** 는 실측 뒤 결정한다.

## 한눈에

| 항목 | 상태 |
| --- | --- |
| 구조 C `AttentionGate` (머리 각도로 "화면을 보고 있나") | **구현됨, 단위 시험됨**. 기본 꺼짐. 손 명령 경로에는 **연결하지 않음** |
| 시선 추정기 (얼굴 랜드마크 → 홍채 오프셋·머리 자세 → 릿지 회귀 → One Euro 평활) | **구현됨, 합성 시험됨**. 아무 곳에도 연결하지 않음 |
| 정확도 측정 도구 `scripts/gaze_validate.py` (9점 교정, 별도 검증점, 10분 드리프트, `--synthetic`) | **구현됨**. `--synthetic`과 시험은 실행함. **실제 카메라 경로는 이 작업에서 실행하지 않음** |
| 모델 내려받기 `scripts/fetch_face_model.py` (sha256 고정) | **구현됨, 실제로 내려받아 해시 계산함** |
| **실제 웹캠 정확도 (도·px, 드리프트)** | **미측정** |
| 얼굴 랜드마크 인덱스의 실제 모델 검증 | **미확인** (아래) |
| 구조 A 워프(시선 접근 + 손 미세 조정) | **미구현** (다음 단계) |
| OpenVINO | **미구현**. 먼저 추론 지연 기준선을 잰다(다음 단계) |

## 구성

| 파일 | 역할 |
| --- | --- |
| `src/gaze/features.py` | 얼굴 랜드마크 → 눈별 홍채 오프셋(눈 너비 단위), 눈 감김 정도, 머리 yaw/pitch/roll **근사** |
| `src/gaze/calibration.py` | 릿지 회귀(1차, 2차 선택). 상수 열 제거. 실패는 예외 대신 `FitReport` |
| `src/gaze/estimator.py` | 교정 표본 수집 → 적합 → 추정. 눈 감김·머리 이탈·교정 전은 `valid:false`와 이유 |
| `src/gaze/attention.py` | `AttentionGate` — 진입/해제 임계값 분리, 최소 프레임, 얼굴 없음 처리 |
| `src/gaze/metrics.py` | 화면 크기·거리로 px↔cm↔도 환산, 오차 통계(중앙값, 평균, p90, 최대, 유효율) |
| `src/gaze/protocol.py` | 9점 교정 → 별도 검증점 5개 → (선택) 드리프트 반복 측정 |
| `src/gaze/synthetic.py` | 합성 얼굴과 가상 사용자(노이즈·깜빡임·프레임 누락·드리프트) |
| `scripts/gaze_validate.py` | 위를 묶은 측정 도구 |
| `scripts/fetch_face_model.py` | `face_landmarker.task` 내려받기와 sha256 확인 |

`src/gaze/`의 순수 로직은 numpy와 기존 `src/filters.py`의 `OneEuroFilter`만 쓴다. MediaPipe·OpenCV·Qt를 이 패키지가 직접 import하지 않는다. (`src.gaze`를 import하면 기존 `src/__init__.py`가 카메라 모듈을 불러오는 점은 기존 구조 그대로다.)

## 실행법

hand-mouse/ 에서:

```
# 1. 카메라·모델 없이 계산 경로 확인 (합성 사용자)
uv run --locked python scripts/gaze_validate.py --synthetic
uv run --locked python scripts/gaze_validate.py --synthetic --drift-minutes 5 --synthetic-drift-per-min 0.01

# 2. 실제 측정 (사람이 한다)
uv run --locked python scripts/fetch_face_model.py          # 처음 한 번, sha256 확인
uv run --locked python scripts/gaze_validate.py --consent \
    --screen-px 1920x1080 --screen-cm 53.1x29.9 --distance-cm 60 [--drift-minutes 10]

# 시험
uv run --locked pytest tests/test_gaze_*.py
```

- 화면 크기(cm)는 **실제 패널 가로·세로**를 자로 재서 넣는다(대각선 인치만으로 추정하지 않는다). 시청 거리는 눈에서 화면까지다.
- Esc로 중단하면 아무것도 저장하지 않는다.
- 실제 모드는 `--consent`(측정받는 사람의 동의)가 없으면 시작하지 않는다. 모델 파일이 없어도 시작하지 않는다.
- 결과는 `hand-mouse/logs/gaze/gaze-<mode>-<시각>.json`에 저장된다(`logs/`는 Git 무시 폴더).

## 사람이 직접 해야 하는 실측 절차

합성 시험은 **코드가 일관되다**는 것만 보여 준다. 시선이 쓸 만한지는 아래를 사람이 해야 안다.

1. `fetch_face_model.py`로 모델을 받는다.
2. 측정받는 사람의 **동의**를 받고, 화면 cm와 시청 거리를 잰다. 자세는 평소 일할 때처럼 한다(턱받침 없이).
3. `gaze_validate.py --consent`를 실행하고 점을 눈으로 따라간다(9점 교정 → 5점 검증). 한 번에 약 1분.
4. 조건을 바꿔 반복한다: 안경 유무, 머리를 고정/자유롭게 움직임, 조명 2종, 다른 웹캠.
5. 드리프트: `--drift-minutes 10`으로 10분간 평소처럼 앉아 있다가 주기적으로 검증점을 본다.
6. 3명 이상. 결과 JSON의 `validation.error_deg.median`, `error_px.median`, `valid_rate`, `drift_slope_deg_per_min`을 모아 표로 만든다.
7. 판단 기준(합격선)은 팀이 정한다. 이 문서는 수치를 제안하지 않는다. 계산식만 있다: 구조 A는 `시선 오차(px) < 손의 미세 이동이 감당하는 반경`일 때만 의미가 있다.

## 지표

| 지표 | 뜻 | 위치 |
| --- | --- | --- |
| 도(°) 오차 | `atan(오차 cm / 시청 거리)` (평면 근사, 가장자리에서는 조금 과대) | `validation.error_deg` |
| px 오차 | 화면 픽셀 단위 거리 | `validation.error_px` |
| 유효율 | 유효 추정 프레임 / 전체 측정 프레임 (깜빡임·얼굴 없음·머리 이탈은 무효) | `valid_rate` |
| 점별 오차 | 검증점 5개 각각의 중앙값 | `validation.per_point` |
| 드리프트 | 시간에 따른 중앙값 변화와 기울기(°/분) | `drift`, `drift_slope_deg_per_min` |
| 교정 RMSE | **정확도가 아님**. 같은 표본으로 맞춘 오차라 낙관적 | `calibration.train_rmse` |

환산식: `1° = 시청거리 × tan(1°)` cm. 기본값(60cm, 53.1×29.9cm, 1920×1080)에서 약 1.05cm ≈ 38px. 이 값은 연구 노트의 계산 가정이다.

검증점은 교정점과 **다른 위치**여야 교정 오차가 정확도로 둔갑하지 않는다. 같은 시선의 인접 프레임은 독립 시도가 아니므로 점·사람 단위로 묶어 보고한다.

## 개인정보 규칙

- 얼굴·눈 영상과 시선 좌표는 민감한 생체 정보다. **로컬에서만 처리**한다.
- 이 도구는 **영상과 이미지를 저장하지 않는다.** 결과 JSON에는 목표점과 통계만 있고 프레임별 시선 좌표나 랜드마크는 없다(시험이 키 이름으로 확인한다).
- 저장소에 원시 영상, 프레임별 좌표, 이름·계정 같은 식별 정보를 넣지 않는다(`AGENTS.md`). 결과 JSON도 커밋하지 않는다(`logs/`는 무시됨).
- 측정은 본인 동의가 있어야 한다(`--consent`). 외부에 공유할 때는 참가자 번호로 익명화하고, 안경·조명 같은 조건만 남긴다.
- 모델 파일(`models/face_landmarker.task`)은 커밋하지 않는다. `.gitignore`에 넣었고, 내려받을 때 sha256(`64184e22…c9ff`, 3,758,596 bytes)을 확인한다.

## 알려진 한계 (정직하게)

- **랜드마크 인덱스 미확인:** `features.py`의 인덱스(홍채 468~477, 눈 모서리 33/133/362/263, 눈꺼풀 159/145/386/374, 볼 234/454, 이마 10, 턱 152)는 MediaPipe Face Mesh 규약에서 가져왔고 **이 작업에서 실제 얼굴로 검증하지 않았다**. 시험은 다른 인덱스를 주입해도 통과하도록 짰고, 기본 인덱스가 맞는지는 실제 얼굴로 `gaze_validate.py`를 돌려 오차가 합리적인지로 간접 확인해야 한다. 첫 실측 때 눈 점이 엉뚱하면 인덱스부터 의심한다.
- **머리 자세는 근사:** 얼굴 변환 행렬이 아니라 랜드마크 기하(볼·이마·턱)에서 계산한다. 부호는 검증하지 않았고(거울 영상 영향), 절댓값만 쓴다. z 값은 단안 추정이라 거칠다.
- **임계값은 튜닝하지 않음:** `max_head_angle_deg`, `min_openness`, AttentionGate 각도(진입 20°/15°, 해제 28°/24°)와 프레임 수, One Euro 파라미터(`min_cutoff=1.0`, `beta=2.0`)는 **시작값**이다.
- **머리 움직임 보정이 약함:** 교정 중 머리를 가만히 두면 머리 특징 열이 상수로 제거되어, 이후 머리를 움직여도 보정되지 않는다. 필요하면 교정 중에 머리를 조금 움직이게 한다(실측으로 효과 확인 필요).
- **합성 결과는 정확도가 아니다.** `--synthetic`의 수치(예: 중앙 0.5°)는 가상 사용자에 대한 것이다.
- **카메라 경로 미실행:** `CameraSource`(전체 화면 점, 프레임 읽기, Face Landmarker)는 이 작업에서 실행하지 않았다. 모델이 한글 경로에서 파일 경로로는 열리지 않는 환경 문제가 있어(이 저장소의 기존 손 모델 시험도 같은 이유로 실패함) **메모리에서 읽어 로드**하도록 했고, 빈 프레임으로 로드까지는 확인했다(얼굴 검출은 0, 정상).
- 화면 위치는 평면·정면 근사다. 멀티 모니터, 비스듬한 화면은 다루지 않는다.

## 구조 C 연결 제안 (구현하지 않음)

`AttentionGate`는 손 명령 경로에 연결하지 않았다. 연결한다면 `src/qt_runtime.py`의 포인터 호출 한 곳이 후보다:

```diff
-            frame_out = pointer.step(
-                t0, detections[0].landmarks if detections else None, mouse.frame_aspect,
-                PointerSettings(...),
-                active=not self.paused and not ui.calibrating, detecting=should_detect,
-            )
+            # gate: AttentionGate(enabled=ui.attention_gate), pose: 별도 얼굴 추론의 머리 자세
+            attending = gate.update(head_pose)          # 비활성이면 항상 True
+            frame_out = pointer.step(
+                t0, detections[0].landmarks if detections else None, mouse.frame_aspect,
+                PointerSettings(...),
+                active=not self.paused and not ui.calibrating and attending, detecting=should_detect,
+            )
```

- 얼굴 모델을 손 모델과 **같은 프레임에서** 돌려야 하므로 추론 지연이 늘어난다. 이 비용은 **측정하지 않았다**.
- "보고 있지 않을 때 손 명령을 막는" 효과(오실행 감소)와 "읽거나 타이핑하다 돌아볼 때의 불편"은 실사용 시험으로만 알 수 있다.
- 한 카메라로 얼굴(정면)과 손(시야 안)을 동시에 잘 보는 **배치**가 가능한지가 먼저다.

## 다음 단계 (이번 범위 밖)

1. **실측** (위 절차) — 오차(px)가 손의 미세 이동 반경 안에 들어오는지 확인해 구조 A의 go/no-go를 정한다.
2. **구조 A 보수형 워프:** 손이 움직이기 시작할 때만 커서를 시선 쪽으로 옮기고, 워프 거리 상한을 두고, 시선이 `valid:false`이면 워프하지 않고 손 기준선으로 복귀한다.
3. **OpenVINO 후속 조사:** 지금 파이프라인(MediaPipe 손 추론 + 얼굴 추론)의 **추론 지연 기준선을 먼저 측정**한다. OpenVINO가 어느 모델에 어떤 이득이 있는지는 그 숫자와 변환 가능 여부를 보고 정한다. 이번에는 구현하지 않았고 효과도 알지 못한다.
4. 필요하면 얼굴 변환 행렬로 머리 자세를 바꾸고(`output_facial_transformation_matrixes`), 교정 표본에 머리 움직임을 넣는다.
