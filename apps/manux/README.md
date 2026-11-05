# MANUX v0.4 (apps/manux)

웹캠으로 Windows를 손짓으로 다루는 MANUX의 다음 버전. Qt/QML + Python 한 프로세스이고, MediaPipe 손 인식 위에 규칙 판정과 가벼운 TCN 모델을 같은 계약으로 얹는다.

**현재 상태 (2026-11-05):** 포인터·핀치·모션, TCN 확인기와 헤드리스 실행기를 연결했습니다. 현재 설치본은 `hand-mouse/`(v0.3)입니다.

## 먼저 읽을 것

1. [AGENTS.md](AGENTS.md): 작업 규칙과 기능 추가 절차 (사람과 AI 에이전트 모두)
2. [docs/architecture.md](docs/architecture.md): 폴더·의존 규칙·프레임 흐름·이식 순서
3. [docs/contracts.md](docs/contracts.md): 모듈 사이의 계약
4. [docs/decisions.md](docs/decisions.md): 확정·제안된 결정

## 데스크톱 앱 실행 (S6, 진행 중)

v0.3과 같은 화면(`ui/qml/dashboard.qml`)이 v0.4 엔진 위에서 돕니다.

```powershell
cd apps/manux
uv run --python 3.12 --extra ui --extra vision python -m manux --hand-model ..\..\hand-mouse\models\hand_landmarker.task
```

- 웹캠 없이 화면 확인: `uv run --python 3.12 --extra ui python -m manux --replay <녹화.jsonl.gz> --no-windows-input`
  (녹화를 재생합니다. `--no-windows-input`이면 실제 마우스 입력을 보내지 않습니다.)
- 사용자 설정·화면 설정(`settings.json`, `ui.ini`)은 `%APPDATA%\MANUX-dev\v04\config`에 저장됩니다. v0.3 설정은 건드리지 않습니다.
- 아직 없는 것: 트래킹 측정, 미리보기 위 손가락 번호 그리기, 학습 센터 화면, 자동 업데이트·설치본.

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

TCN 확인기를 연결하려면 `uv run --extra ml`로 실행하고
`--tcn-model <모델>.onnx`를 지정합니다. Qt 화면과 Windows 조작은 S6에서 연결합니다.

직접 판정은 `--tcn-mode direct`로 명시적으로 켭니다. 클래스별 선언·별도 액션 바인딩,
특징 캐시·별도 평가 후 개인 모델 설치·데스크톱 자동 교체는 [직접 TCN 안내](docs/direct-tcn.md)를 따릅니다.
개인 모델 파일은 연습으로 확인하고 실제 Windows 입력에는 검증된 `--tcn-store`를 사용합니다.

## 테스터 프로필 데이터 수집

저장소 루트에서 `apps/manux`로 이동한 뒤 실행합니다. `tester01`은 실명 대신 쓸
테스터 별명입니다. 같은 별명으로 다시 실행하면 파일이 계속 추가됩니다.

```powershell
cd apps/manux
uv run --locked --extra vision python tools/collect_mouse.py --profile tester01 --source 0
```

- 과제는 하나의 과제 목록(`src/manux/data/catalog.py`)에서 가져옵니다. rasp-jun의 18개 과제
  (클릭·드래그, 창·모드, 방향 모션, 이동·중립)와 더블클릭·가로 스크롤·Record & Fling입니다.
  `--preset pinch`(기본)는 손 모양 조작, `--preset pointer`는 검지 포인터와 핀치 클릭,
  `--preset trigger`는 검지 옆을 엄지로 눌러 클릭·드래그하는 방식의 과제를 고릅니다.
  더블클릭처럼 같은 손 모양을 쓰는 과제는 같은 학습 클래스를 씁니다. 커서 이동 과제는 `none`
  클래스입니다. `v_sign`·`rock_sign`·`swipe_down`은 v0.4 기능이 아직 없어 녹화만 합니다.
- 자세·위치·거리·카메라·조명 조건과 반복 시간은 녹화 설정(`--plan`)이 정합니다.
  `basic`(기본: 정면 중앙·왼쪽·오른쪽), `positions`(화면 9구역), `diverse`(9가지 조건),
  `saved`(사용자 설정 폴더의 `config/capture-plans.json`에 저장한 설정) 또는 설정 JSON 파일
  경로를 쓸 수 있습니다. 설정의 켜진 조건마다 모든 과제를 한 번씩 녹화합니다.
  `--condition 2` 또는 `--condition "화면 위"`로 일부 조건만, `--reps 5`·`--free-seconds 30`으로
  반복 횟수와 자유 동작 시간만 바꿀 수 있습니다.
- 카메라 창에서 `SPACE`로 세트를 시작하고 `GO`가 보일 때 동작합니다. `REST`에서 손을
  편하게 되돌리세요. `R`은 현재 세트 재시도, 대기 중 `N`은 건너뛰기, `Esc`는 종료입니다.
  손의 3D 관절을 잃으면 세트 시간이 멈춥니다(`PAUSED`). 과제 설명(한국어)은 콘솔에 나옵니다.
  완료한 세트는 즉시 저장되고 손 추적이 부족하거나 취소한 세트는 학습 입력에서 제외됩니다.
- `--list`로 과제 키·학습 클래스·동작 ID, 조건 목록과 예상 녹화 시간을 확인합니다.
  예: `--list --plan positions --focus thumb_ring`. `--focus none thumb_middle thumb_index_middle`은
  세 과제만 수집합니다. `--control-hand Left`로 왼손을 고릅니다. 평가 시도는 `--split eval`로
  따로 수집합니다.
- 원본 영상은 화면에만 표시합니다. 손 관절 좌표·과제 라벨·규칙 판정은
  `%APPDATA%/MANUX-dev/v04/recordings/profile-tester01/pinch/train/` 아래에 세트별
  `.jsonl.gz`로 저장합니다. 실제 Windows 마우스·키보드 입력은 꺼져 있습니다.
  카메라 모델은 로컬 v0.4 모델 또는 체크아웃의 `hand-mouse/models/hand_landmarker.task`를 사용합니다.

## TCN 학습과 평가 (S5)

녹화에는 `label.target`과 `label.gesture`를 사용합니다. 이전 3클래스 녹화는 기존
이름을 읽습니다. 더블클릭처럼 같은 입력을 반복하는 과제는 기존 입력 클래스에 포함됩니다.
중립 `none`과 학습할 동작이 하나 이상 필요합니다. 손의 세계 좌표가 있는 프레임이
학습에 사용됩니다.

```powershell
$data = "$env:APPDATA/MANUX-dev/v04/recordings/profile-tester01/pinch"
$train = (Get-ChildItem "$data/train/*.jsonl.gz").FullName
$eval = (Get-ChildItem "$data/eval/*.jsonl.gz").FullName
uv run --locked --python 3.12 --extra train python tools/train_tcn.py $train --output "$data/personal.onnx"
uv run --locked --python 3.12 --extra ml python tools/evaluate_tcn.py "$data/personal.onnx" $eval --group-by condition
```

의존성: 앱에서 모델을 실행하려면 `ml` extra(onnxruntime), 학습하려면 `train` extra(torch·onnx·onnxruntime)가
필요합니다. torch는 `tools/`에서만 쓰고 `src/`는 import하지 않습니다. CI(`.[test]`)는 둘 다 없이 돕니다.
학습 도구는 저장소 안에 모델을 쓰지 않습니다(D-006).

학습 도구는 클래스 목록을 녹화에서 읽습니다. `--classes`로 클래스와 순서를
지정할 수 있습니다. 모델마다 한 프리셋을 사용하세요. 기본 평가(`--hold-out all`)는 사람, 자세, 물리 조건을 각각 하나씩 제외해
학습을 반복합니다(`person`·`posture`·`condition`·`both`(사람+자세)로 고를 수 있습니다).
사람 그룹은 `meta.person` 별명을 우선하고, 없으면 `meta.pc` 별명을 씁니다.
PC를 공유하면 사람마다 다른 별명이 필요합니다. 자세 그룹은 `condition_index`를
우선하고, 없으면 `condition`을 씁니다. 물리 조건 그룹은 녹화 설정의 `condition_key`이며,
이름이나 순서가 달라도 같은 조건이면 같은 그룹입니다. 키가 없는 이전 녹화는
`legacy-<condition_index>`로 묶습니다. 그룹 또는 학습 클래스가 부족한 분할은 그 사유를
모델 정보에 남깁니다. 스와이프 과제에서 요청한 방향으로 움직이지 않은 프레임은 학습에서 뺍니다.

평가는 동작 시도별 TCN·규칙 적중 횟수와 중립 구간의 분당 오실행을 기록합니다.
규칙 적중은 녹화에 저장된 `rule`을 사용합니다. 비교하려는 동작의 규칙 출력을
녹화에 포함해야 합니다. 더블클릭·스크롤 방향별 사용성은 실제 출력 평가가 필요합니다.
커서 이동과 엄지 척도 학습 데이터에 포함할 수 있으며 실행기의 TCN 확인은 클릭처럼
`tap` 또는 `begin`으로 시작하는 바인딩에 적용됩니다. 원본과 모델은 작업 폴더 밖에 보관합니다.
