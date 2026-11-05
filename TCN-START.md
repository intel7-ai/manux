# MANUX TCN 실행 안내

Dan님의 `intel7-ai/manux` / `hand-mouse-gesture-overhaul` 최신 커밋
`7448e4a` (2026-11-05 08:57 KST)를 기반으로 만든 별도 프로그램입니다.
작업 브랜치는 `codex/manux-latest-tcn-20261105`입니다.

## 실행

이 PC에서는 **MANUX TCN 실행.cmd**를 두 번 클릭합니다.
다른 PC에서는 먼저 **setup-tcn.cmd**를 실행합니다(uv 필요).

1. 처음에는 사용할 손을 선택합니다.
2. **손바닥 · Dan 기본** 모드와 **손짓 확인(TCN) 켬**으로 시작합니다.
3. 준비되면 **제어 시작**을 누릅니다. 시작 전에는 OS 입력이 일시정지됩니다.
4. 엄지+중지: 좌클릭. 엄지+약지: TCN 확인 후 우클릭.
   엄지+검지: TCN 확인 후 페이지를 잡고 스크롤.
5. **Space**로 시작/일시정지, **Esc**로 일시정지, **Q** 또는 창의 X로 종료합니다.

카메라를 바꾸려면 앱에서 **C**를 누르거나 다음과 같이 실행합니다.

```powershell
.venv\Scripts\python.exe tcn_app.py --source 1
.venv\Scripts\python.exe tcn_app.py --phone-web
.venv\Scripts\python.exe tcn_app.py --dry-run
.venv\Scripts\python.exe tcn_app.py --check
```

`--dry-run`은 카메라·추론 화면만 확인하고 OS 입력을 보내지 않습니다.
`--check`는 카메라를 열지 않고 실제 ONNX 모델 추론을 검사합니다.

## 적용한 TCN

- 모델: `models/gesture_tcn.onnx`, 학습 시각 2026-11-05 04:18:48.
- 입력: 최근 15프레임 × 133개 특징, CPU ONNX Runtime 추론.
- 분류: 중립, 엄지+검지, 엄지+약지.
- 원본의 규칙 판정을 TCN이 확인하여 **우클릭·스크롤 시작**에 적용합니다.
  좌클릭·커서 이동 등은 원본 규칙을 사용합니다.
- 검지 포인터 모드로 바꾸면 이 TCN 확인 경로를 사용하지 않습니다.
- 실행 전에 모델 호환성·추론을 검사하며 실패 시 오류를 알리고 종료합니다.
- **학습 센터**에서 기존 녹화, 동작별 결과, 집중 녹화, 재학습, 내보내기를 사용합니다.

이 복사본의 설정은 `config/tcn-ui.ini`, 프로필은 `config/profiles/`에 저장합니다.
모델과 원본 녹화는 overhaul에서 받은 그대로이며 새로 학습하지 않았습니다.
동봉된 성적은 원본의 한 사람·한 카메라 녹화에서 얻은 결과입니다.
이 PC에서의 실제 손동작 정확도는 별도 확인이 필요합니다.

## 개발 검사

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe tcn_app.py --check
```

설치형 EXE가 아닌 Python/Qt 실행본이며, 이 폴더의 `.venv`가 필요합니다.

## 이 PC에서 확인한 결과

- 전체 자동 시험: **384개 통과**.
- 동봉된 ONNX 모델의 CPU 추론 및 원본 모델 파일 일치 확인.
- 한글 경로에서 MediaPipe 손/자세 모델 로딩 및 합성 프레임 처리 확인.
- 실제 카메라 1280×720 @ 30fps, 손 추적, TCN 켬 상태, 학습 센터 화면 확인.
- 화면 확인은 `--dry-run`으로 진행했습니다. 실제 OS 클릭과 사용자별 인식률은 미측정입니다.
- 원본에 있던 모션 배지 테스트의 누락된 초기 상태를 보완했습니다.
