# 손짓 학습 스튜디오 (2026-11-05)

이 앱은 이제 **TCN 학습 앱**이기도 하다. 틈날 때 녹화하고, 학습하고, 동작별 성적을 보고, 부족한 동작만 더 녹화하고, 다른 앱에 넣을 묶음으로 내보낸다.

```
녹화 → 학습 → 동작별 성적 확인 → 부족한 동작 집중 녹화 → 다시 학습 → 내보내기
```

## 1. 시작: `studio.bat` (긴 명령을 외우지 않아도 됨)

`studio.bat`을 두 번 클릭하면 번호 메뉴가 뜬다. 처음이면 uv와 라이브러리(`--extra qt --extra train`)를 자동으로 설치한다.

| 번호 | 하는 일 | 실제로 실행되는 명령 (`uv run --locked --extra qt --extra train` 뒤) |
|---|---|---|
| 1 | 앱 실행 (연결된 카메라, 번호를 물어봄) | `python main.py --ui qt --source N` |
| 2 | 앱 실행 (휴대폰 브라우저 카메라) | `python main.py --ui qt --phone-web` |
| 3 | 학습 센터 리포트 (콘솔) | `python scripts/training_report.py` |
| 4 | TCN 학습 | `python scripts/train_tcn.py --folds` |
| 5 | 모델 내보내기 | `python scripts/export_model.py` |
| 6 | 시험 돌리기 | `python -m pytest -q` |

메뉴는 고를 때마다 실제 명령과 그 뜻을 보여 준다. 옵션의 뜻:

| 옵션 | 효과 |
|---|---|
| `--extra qt` | Qt 화면(PySide6)을 설치·사용. 빼면 uv가 지운다 |
| `--extra train` | 학습·모델 실행(torch CPU, onnx, onnxruntime). 빼면 uv가 지운다 |
| `--locked` | `uv.lock`에 적힌 버전 그대로 (PC마다 같은 환경) |
| `--ui qt` | MANUX Qt 화면 (녹화·학습 센터·손짓 확인이 있는 화면) |
| `--source N` | 카메라 번호. 0 = 첫 카메라 (이 집 PC는 폰 카메라가 0) |
| `--phone-web` | 휴대폰 브라우저를 카메라로 (QR 접속) |
| `--headless` | 창 없이 알림 영역(트레이)에서만 |
| `--profile 이름` | 보정 프로필 고르기 |

`run.bat`은 예전처럼 바로 앱을 켠다(같은 라이브러리 묶음).

## 2. 앱 안의 "학습 센터" (위쪽 버튼)

동작마다 한 줄씩:

- **데이터**: 녹화한 반복 횟수 / 목표 30회, 녹화한 자세 수 / 3 (반주먹 이동은 분 / 목표 9분). 막대가 노란색이면 부족.
- **인식**: 학습에 **쓰지 않은** 자세에서 맞힌 비율 = 처음 보는 상황에서의 실력. 괄호는 규칙만 썼을 때.
  반주먹 이동은 **오작동 회/분**(낮을수록 좋음).
- **학습**: 학습한 데이터를 맞힌 비율. 학습은 높은데 인식이 낮으면 "외우기만 했다" → 다른 자세·다른 날 데이터가 필요.
- **→ 조언**과 상태 칩: 좋아요 / 더 녹화 / 다시 학습.
  - 반복 30회 미만, 자세 3개 미만, 인식 80% 미만, 오작동 0.5회/분 초과 → **더 녹화**
  - 모델보다 새 녹화가 있음 → **다시 학습**
- **[집중 녹화]**: 그 동작만 안내 녹화(자세 3개 × 15회, 반주먹 이동은 자세마다 3분). 손바닥(Dan) 포인터에서 된다.
  녹화 파일 meta에 `focus`가 남는다.

아래쪽:

- **[학습 시작]**: 별도 프로세스로 `train_tcn.py --folds --progress`를 돌린다(앱은 계속 쓸 수 있음). 지금 단계(평가 학습 n/3 → 최종), 반복·손실, 경과 초, 손실 곡선이 보인다.
  끝나면 성적이 새로 고쳐지고 **손짓 확인(TCN)이 새 모델을 다시 읽는다**. 녹화 중에는 누를 수 없다.
- **[내보내기]**: `exports/gesture-tcn-<시각>/`과 같은 이름의 `.zip`. **[폴더 열기]**로 탐색기에서 연다.

창은 Esc나 바깥 클릭으로 닫힌다.

## 3. 내보낸 묶음 (다른 앱에 넣기)

| 파일 | 내용 |
|---|---|
| `gesture_tcn.onnx` | 모델 (입력 `x` = (1, 15, 133), 출력 `logits` = 클래스 3개) |
| `gesture_tcn.json` | 클래스 순서, 창 길이, 판정 기준(확률 0.7 이상 3프레임 연속), 데이터 출처, 동작별 성적 |
| `tcn_features.py` | MediaPipe 관절 → 특징 133개 (모델과 반드시 같은 코드) |
| `example_use.py` | numpy + onnxruntime만으로 쓰는 예시 (`GestureModel.step(t, world, image, aspect)`) |
| `README.md` | 위 내용과 성적 요약 |
| `dataset.npz` | 녹화 전체의 프레임별 특징 `x`, 정답 `y`(-1 = 제외), 자세 `posture`, 녹화 번호 `recording` (다른 프레임워크에서 다시 학습할 때) |

입력은 MediaPipe Hand Landmarker의 `hand_world_landmarks`(3D, 미터)와 `hand_landmarks`(화면 정규화), 화면 비율(높이/너비)이다.
확인: 내보낸 폴더에서 `example_use.py`로 녹화 6,000프레임을 돌려 프레임 단위로 93% 맞음(2026-11-05).

## 4. 학습 진행 이벤트 (`train_tcn.py --progress`)

한 줄에 JSON 하나. 앱(`src/training_center.py`의 `TrainingJob`)이 읽는다.
`loaded`(구간 수, 자세, folds) → `fold_start` → `epoch`(stage, epoch, loss) → `fold_result` → `final_start` → `epoch`… → `done`(per_gesture).
`models/gesture_tcn.json`에 `per_gesture`, `train_accuracy`, `loss_curve`가 함께 저장된다.

## 5. 지금 성적 (2026-11-05 04:15, 녹화 1개 17분, 폰 카메라)

| 동작 | 인식 (규칙만) | 학습 데이터 정확도 |
|---|---|---|
| 엄지+검지 (스크롤) | 84% (56%) | 91% |
| 엄지+약지 (우클릭) | 80% (56%) | 100% |
| 반주먹 이동 | 오작동 0.67회/분 (5.69) | 83% |

→ 학습 센터 조언: 반주먹 이동을 더 녹화(9분 목표, 오작동 0.5회/분 이하 목표). 우클릭은 학습 100%·인식 80%라 다른 자세·다른 날 데이터가 도움이 된다.

## 관련 파일
`studio.py`, `studio.bat`, `src/training_center.py`, `scripts/training_report.py`, `scripts/export_model.py`, `scripts/train_tcn.py`,
`src/guided_recording.py`(`tasks_for`), `src/qt_dashboard.py`·`src/dashboard.qml`(학습 센터 창), `tests/test_training_center.py`.

## 6. 여러 PC의 학습 데이터 모으기 (2026-11-05 결정)

- **녹화는 코드와 같은 브랜치(`feat/tcn-training-app-transition`)에 함께 올린다.** 다른 PC·다른 각도에서 녹화한 파일도 같은 `recordings/`에 쌓고, 학습은 그 폴더 전체로 한다.
- 녹화 파일 이름에 **PC 별명**이 들어간다: `20261105-030947-home-pc-guided.jsonl.gz`. 두 PC가 같은 초에 녹화해도 겹치지 않는다.
  - 별명은 `config/gestures.json`의 `"pc_alias"`(영문·숫자·`-`·`_`). PC마다 다르게 정한다(예: `home-pc`, `office-laptop`).
  - 비워 두면 `pc-` + PC 이름으로 만든 4자리 코드(같은 PC면 늘 같은 코드, 이름은 알아낼 수 없음).
  - **이 저장소는 공개라 실제 PC 이름과 Windows 사용자 이름은 녹화·모델 정보 어디에도 저장하지 않는다**(2026-11-05). 그 전에 만든 녹화 1개는 파일 안에 실제 PC 이름이 남아 있지만, 학습 센터·모델 정보·내보내기에는 코드(`pc-ded5`)로만 나온다.
- 녹화 중에는 `….jsonl.gz.part`로 저장되고, 끝나면 이름이 바뀐다. `.part`는 git에 올라가지 않고 학습에도 쓰이지 않는다.
- **받으면서 학습해도 된다**: 학습은 시작할 때 완성된 녹화 목록을 고정한다. 반쯤 받은 파일이나 끝 줄이 없는 파일(녹화 중 앱이 꺼짐)은 건너뛴다. 진행 줄과 학습 센터에 "다음 학습에 포함 n개"로 표시되고, 다음 학습 때 들어간다.
- **용량**: 학습 센터 위쪽에 "녹화 총 n MB / 분리 권장 500 MB"가 보인다. 500 MB를 넘으면 노란 안내가 뜬다. 그때는 학습 데이터를 전용 비공개 저장소로 옮기는 걸 검토한다. git은 지운 파일도 기록에 남기므로, 옮기기 전에 이미 올린 양은 줄지 않는다. 녹화는 17분에 약 10 MB다.
