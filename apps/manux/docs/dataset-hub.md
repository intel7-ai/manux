# 팀 데이터·모델 중앙 허브 가이드 (`tools/hub.py`)

> **대상:** MANUX 팀원 및 각 팀원이 사용하는 AI 에이전트(Claude Code, Codex, Antigravity, Cursor 등)
> **목적:** 로컬에서 수집한 손짓 녹화(`.jsonl.gz`)와 학습된 TCN 모델(`.onnx` + `.json`)을 Git 저장소에 넣지 않고([D-006](decisions.md#d-006-학습-데이터-위치)), Hugging Face 비공개 Dataset 저장소(`se4nchoi/manux-datasets`)를 통해 검증·동기화한다.

---

## 앱에서 수집·업로드하기

v0.4 MANUX Preview는 [학습 센터](learning-center.md)에서 별명·HF 주소·토큰 설정, 읽기/쓰기 권한
확인, 본인 완료 기록 업로드, 팀 데이터 다운로드를 지원한다. 아래 CLI 절차도 유지한다.
사용자마다 저장소 쓰기 권한이 필요하며 공개 저장소라는 이유만으로 업로드가 허용되지는 않는다.
앱은 기존 Dataset에 연결하며 새 저장소를 만들지 않는다.


## 1. AI 에이전트 필수 준수 규칙 (Guardrails)

팀원의 요청으로 데이터 수집·업로드·다운로드·학습을 실행하는 AI 에이전트는 반드시 아래 규칙을 지킨다.

1. **Git 커밋 금지:** `.jsonl.gz`, `.jsonl.gz.part`, 개인 `.onnx` 모델, `.env` 파일을 절대 Git 브랜치에 `git add`하거나 커밋하지 않는다.
2. **토큰 보안:** `apps/manux/.env`(또는 루트 `.env`) 안의 `HF_TOKEN` 값을 터미널에 출력(`cat`, `echo`, `Get-Content`)하거나 로그·PR 본문에 남기지 않는다.
3. **무결성·익명성 검증 우회 금지:** 수동으로 Hugging Face에 파일을 올리지 않고 반드시 `tools/hub.py push`를 사용한다. `hub.py`는 다음 계약([contracts.md §9·§10](contracts.md#9-학습-녹화-형식))을 자동으로 강제한다:
   - 미완성 녹화(`.part`) 및 `end` 줄이 없는 파일 제외
   - 실제 PC 호스트명·Windows 계정명(`FORBIDDEN_META`) 또는 해시되지 않은 구버전 PC 이름 차단
   - `.onnx` 모델 업로드 시 짝이 되는 `.json` 모델 카드(`local3d-v1`, 133차원 특징) 유효성 검사
4. **별명(`--profile`) 규칙:** 녹화 시 `--profile`에는 실명 대신 영문·숫자 별명(예: `tester01`, `dan`, `sean` 등 1~32자)만 쓰고, 학습(`--split train`)과 평가(`--split eval`)는 별도 시도로 나누어 녹화한다.

---

## 2. 최초 1회 세팅 (`apps/manux/.env`)

에이전트는 명령을 실행하기 전에 `apps/manux/.env` 파일이 있는지 먼저 확인한다(`Test-Path apps/manux/.env`).

- 파일이 없으면 `apps/manux/.env.example`을 복사하고 사용자에게 팀 디스코드에 공유된 `HF_TOKEN`을 붙여 넣어 달라고 안내한다:
  ```powershell
  cd apps/manux
  Copy-Item .env.example .env
  ```
- `apps/manux/.env` 형식:
  ```ini
  MANUX_HF_REPO=se4nchoi/manux-datasets
  HF_TOKEN=hf_팀에서_공유받은_토큰
  ```
- `tools/hub.py`는 실행 시 `apps/manux/.env`와 저장소 루트 `.env`를 자동으로 읽으므로 별도 Windows 시스템 환경변수 등록이 필요 없다.

---

## 3. 작업별 실행 명령어 (`apps/manux` 디렉터리 기준)

모든 명령은 `apps/manux` 폴더에서 실행한다.

### 3.1 현황 확인 (`status`)
로컬 녹화 폴더(`%APPDATA%\MANUX-dev\v04\recordings`)와 원격 허브(`se4nchoi/manux-datasets`)에 쌓인 테스터별·모드별·클래스별 프레임 수를 확인한다.
```powershell
cd apps/manux
# 로컬 + 원격 허브 동시 확인:
uv run --python 3.12 --with huggingface-hub python tools/hub.py status

# 오프라인(로컬 녹화본만) 확인:
uv run --python 3.12 python tools/hub.py status --local
```

### 3.2 내 PC에서 녹화 수집 (`collect_mouse.py`)
Windows 마우스 실제 출력은 자동으로 꺼진 상태에서 안전하게 녹화된다.
```powershell
cd apps/manux
# 1) 녹화할 과제와 조건 목록 미리 보기:
uv run --python 3.12 --extra vision python tools/collect_mouse.py --list --plan basic

# 2) 학습용(train) 녹화 수집:
uv run --python 3.12 --extra vision python tools/collect_mouse.py --profile <테스터별명> --preset pinch --plan basic --split train --hand-model ..\..\hand-mouse\models\hand_landmarker.task

# 3) 평가용(eval) 녹화 따로 수집 (학습과 다른 시도로 최소 1회):
uv run --python 3.12 --extra vision python tools/collect_mouse.py --profile <테스터별명> --preset pinch --plan basic --split eval --hand-model ..\..\hand-mouse\models\hand_landmarker.task
```

### 3.3 수집한 녹화본(및 모델) 허브에 업로드 (`push`)
내 PC의 `%APPDATA%\MANUX-dev\v04\recordings`에서 완료된 `.jsonl.gz`만 골라 계약 검사를 통과한 새 파일만 증분 업로드한다(이미 올라간 SHA-256 파일은 자동 건너뜀).
```powershell
cd apps/manux
# 업로드 전 검증만 미리 해보기 (Dry-run):
uv run --python 3.12 --with huggingface-hub python tools/hub.py push --dry-run

# 실제 업로드:
uv run --python 3.12 --with huggingface-hub python tools/hub.py push

# 학습된 .onnx 모델(+ 같은 이름의 .json 카드 필수)도 함께 올릴 때:
uv run --python 3.12 --with huggingface-hub python tools/hub.py push --model "$env:APPDATA\MANUX-dev\v04\models\gesture_tcn.onnx"
```

### 3.4 팀원들이 올린 데이터·모델 내려받기 (`pull`)
원격 허브에서 내 로컬 폴더에 없는 파일만 증분 다운로드한다. 내려받은 파일은 `%APPDATA%\MANUX-dev\v04\recordings/profile-<별명>/<preset>/<split>/` 아래에 정리된다.
```powershell
cd apps/manux
# 전체 녹화본 + 공유 모델 모두 받기:
uv run --python 3.12 --with huggingface-hub python tools/hub.py pull --models

# 특정 조건만 골라 받기 (예: train split만, 또는 특정 테스터/클래스만):
uv run --python 3.12 --with huggingface-hub python tools/hub.py pull --split train
uv run --python 3.12 --with huggingface-hub python tools/hub.py pull --person tester01 --class thumb_ring
```

### 3.5 모인 데이터로 TCN 비교 실험 및 학습 (`compare_tcn.py` / `train_tcn.py`)
`pull`을 실행한 뒤 로컬에 모인 전체 팀원의 녹화본으로 바로 학습·평가를 돌린다.
```powershell
cd apps/manux
$all = (Get-ChildItem "$env:APPDATA\MANUX-dev\v04\recordings\*\pinch\*\*.jsonl.gz").FullName

# 사람별(Leave-One-Person-Out) 다중 분류 vs 이진 TCN 비교:
uv run --python 3.12 --extra train python tools/compare_tcn.py $all --axis person

# 전체 train 녹화로 모델 학습 후 eval 녹화로 평가:
$train = (Get-ChildItem "$env:APPDATA\MANUX-dev\v04\recordings\*\pinch\train\*.jsonl.gz").FullName
$eval  = (Get-ChildItem "$env:APPDATA\MANUX-dev\v04\recordings\*\pinch\eval\*.jsonl.gz").FullName
$out   = "$env:APPDATA\MANUX-dev\v04\models\gesture_tcn.onnx"
uv run --python 3.12 --extra train python tools/train_tcn.py $train --output $out
uv run --python 3.12 --extra ml python tools/evaluate_tcn.py $out $eval --group-by person
```

---

## 4. 팀원에게 전달할 AI 프롬프트 (복사해서 본인의 AI 에이전트에 붙여넣기)

팀원이 Claude Code, Codex, Cursor, Antigravity 등 어떤 AI를 쓰든 아래 문장만 복사해서 주면 에이전트가 문서를 읽고 알아서 실행합니다:

```text
apps/manux/docs/dataset-hub.md 를 읽고 다음 순서대로 진행해 줘:
1. apps/manux/.env 파일이 있는지 확인하고, 없으면 .env.example을 복사해서 내가 HF_TOKEN을 넣을 수 있게 알려줘.
2. 토큰이 준비되면 `tools/hub.py status`로 현재 허브 상태를 확인해 줘.
3. 내가 녹화한 데이터를 올릴 때는 `tools/hub.py push`를 쓰고, 다른 팀원 데이터를 받아서 학습/비교할 때는 `tools/hub.py pull`로 받은 뒤 실행해 줘.
```
