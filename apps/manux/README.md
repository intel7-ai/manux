# MANUX v0.4 (apps/manux)

웹캠으로 Windows를 손짓으로 다루는 MANUX의 다음 버전. Qt/QML + Python 한 프로세스이고, MediaPipe 손 인식 위에 규칙 판정과 가벼운 TCN 모델을 같은 계약으로 얹는다.

**현재 상태 (2026-11-05):** 포인터·핀치·모션, TCN 확인기와 헤드리스 실행기를 연결했습니다. 현재 설치본은 `hand-mouse/`(v0.3)입니다.

## 먼저 읽을 것

1. [AGENTS.md](AGENTS.md): 작업 규칙과 기능 추가 절차 (사람과 AI 에이전트 모두)
2. [docs/architecture.md](docs/architecture.md): 폴더·의존 규칙·프레임 흐름·이식 순서
3. [docs/contracts.md](docs/contracts.md): 모듈 사이의 계약
4. [docs/decisions.md](docs/decisions.md): 확정·제안된 결정

## 시험

```powershell
cd apps/manux
uv run --python 3.12 pytest -q
```

카메라·모델 없이 실행된다. 이 시험이 통과해도 손 인식이나 실제 사용 성능을 뜻하지 않는다.

## 헤드리스 실행

완료된 녹화를 재생하거나 실제 카메라에서 프레임별 JSON Snapshot을 출력할 수 있습니다.
두 경로 모두 연습 출력을 사용합니다. 조작 손은 설정에서 읽고, 설정이 비어 있으면
오른손을 사용합니다. `--control-hand Left`로 선택할 수 있습니다.

```powershell
uv run python -m manux.app --replay <녹화> --mode pinch --max-frames 100
uv run --extra vision python -m manux.app --camera --source 0
```

TCN 확인기를 연결하려면 `uv run --with onnxruntime`으로 실행하고
`--tcn-model <모델>.onnx`를 지정합니다. Qt 화면과 Windows 조작은 S6에서 연결합니다.

## TCN 학습과 평가 (S5)

녹화에는 `label.target`과 `label.gesture`를 사용합니다. 이전 3클래스 녹화는 기존
이름을 읽습니다. 새 녹화는 `GuidedSession`에 원하는 `GestureSpec` 목록을 넘깁니다.
중립 `none`과 학습할 동작이 하나 이상 필요합니다. 손의 세계 좌표가 있는 프레임이
학습에 사용됩니다.

```powershell
uv run --python 3.12 --extra train python tools/train_tcn.py <녹화1> <녹화2> --output <저장소 밖>/<모델>.onnx
uv run --python 3.12 --extra ml python tools/evaluate_tcn.py <모델>.onnx <별도-평가-녹화> --group-by person
```

의존성: 앱에서 모델을 실행하려면 `ml` extra(onnxruntime), 학습하려면 `train` extra(torch·onnx·onnxruntime)가
필요합니다. torch는 `tools/`에서만 쓰고 `src/`는 import하지 않습니다. CI(`.[test]`)는 둘 다 없이 돕니다.
학습 도구는 저장소 안에 모델을 쓰지 않습니다(D-006).

학습 도구는 클래스 목록을 녹화에서 읽습니다. `--classes`로 클래스와 순서를
지정할 수 있습니다. 기본 평가는 사람과 자세를 각각 하나씩 제외해 학습을 반복합니다.
사람 그룹은 `meta.person` 별명을 우선하고, 없으면 `meta.pc` 별명을 씁니다.
PC를 공유하면 사람마다 다른 별명이 필요합니다. 자세 그룹은 `condition_index`를
우선하고, 없으면 `condition`을 씁니다. 같은 인덱스는 여러 녹화에서 같은 자세를
뜻해야 합니다. 그룹 또는 학습 클래스가 부족한 분할은 그 사유를 모델 정보에 남깁니다.

평가는 동작 시도별 TCN·규칙 적중 횟수와 중립 구간의 분당 오실행을 기록합니다.
규칙 적중은 녹화에 저장된 `rule`을 사용합니다. 비교하려는 동작의 규칙 출력을
녹화에 포함해야 합니다. 학습에서 제외한 녹화로 평가하고 원본과 모델은 작업 폴더
밖에 보관합니다.
