# 팀 데이터·모델 중앙 허브 가이드 (`tools/hub.py`)

> **대상:** MANUX 팀원 및 각 팀원이 사용하는 AI 에이전트(Claude Code, Codex, Antigravity, Cursor 등)
> **목적:** 로컬에서 수집한 손짓 녹화(`.jsonl.gz`)와 학습된 TCN 모델(`.onnx` + `.json`)을 Git 저장소에 넣지 않고, Hugging Face 비공개 Dataset 저장소(`se4nchoi/manux-datasets`)를 통해 검증·동기화한다.

---

## 1. AI 에이전트 필수 준수 규칙 (Guardrails)

팀원의 요청으로 데이터 수집·업로드·다운로드·학습을 실행하는 AI 에이전트는 반드시 아래 규칙을 지킨다.

1. **Git 커밋 금지:** `.jsonl.gz`, `.jsonl.gz.part`, 개인 `.onnx` 모델, `.env` 파일을 절대 Git 브랜치에 `git add`하거나 커밋하지 않는다.
2. **토큰 보안:** `.env` 안의 `HF_TOKEN` 값을 터미널에 출력(`cat`, `echo`, `Get-Content`)하거나 로그·PR 본문에 남기지 않는다.
3. **무결성·익명성 검증 우회 금지:** 수동으로 Hugging Face에 파일을 올리지 않고 반드시 `tools/hub.py push`를 사용한다. `hub.py`는 다음 계약을 자동으로 강제한다:
   - 미완성 녹화(`.part`), `end` 줄이 없는 파일, 파생 폴더(`recordings/cleaned/`, `recordings/augmented/`) 제외
   - 실제 PC 호스트명·Windows 계정명(`FORBIDDEN_META`) 또는 해시되지 않은 구버전 PC 이름 차단
   - `.onnx` 모델 업로드 시 짝이 되는 `.json` 모델 카드(`local3d-v1`, 133차원 특징) 유효성 검사
4. **별명(`--profile`) 규칙:** 녹화 시 `--profile`에는 실명 대신 영문·숫자 별명(예: `tester01`, `jun`)만 사용한다.

---

## 2. 최초 1회 설정 (팀원 PC)

프로젝트 루트에서 `.env.example`을 `.env`로 복사한 뒤, 팀장에게 전달받은 Hugging Face 토큰(`hf_...`)을 붙여넣는다.

```powershell
Copy-Item .env.example .env
notepad .env
```

`.env` 파일 예시:
```ini
MANUX_HF_REPO=se4nchoi/manux-datasets
HF_TOKEN=hf_여기에_전달받은_팀_토큰_붙여넣기
```

> `tools/hub.py`는 실행 시 자동으로 `.env`를 읽으므로 매번 `--repo`나 `--token`을 입력할 필요가 없다.

---

## 3. 작업별 표준 명령어

### 3.1 현황 확인 (`status`)

로컬 녹화 폴더(`recordings/`)와 원격 허브(`se4nchoi/manux-datasets`)의 파일 수·프레임 수·참여자별 통계를 확인한다.

```powershell
# 오프라인 로컬 검증만 실행
.\.venv\Scripts\python.exe tools/hub.py status --local

# 로컬 + Hugging Face 원격 허브 현황 함께 확인
.\.venv\Scripts\python.exe tools/hub.py status
```

### 3.2 내 녹화 데이터 업로드 (`push`)

검증을 통과한 새 `.jsonl.gz` 녹화만 SHA-256 기준으로 중복 없이 원격 허브의 `recordings/<person>/` 아래에 업로드하고 `manifest.jsonl`을 갱신한다.

```powershell
# 업로드 전 사전 검증 (서버 전송 없음)
.\.venv\Scripts\python.exe tools/hub.py push --dry-run

# 실제 업로드
.\.venv\Scripts\python.exe tools/hub.py push
```

### 3.3 팀원 데이터 내려받기 (`pull`)

원격 허브에서 내 PC에 아직 없는 녹화만 골라 내려받는다.

```powershell
# 전체 학습용(train) 녹화 증분 다운로드
.\.venv\Scripts\python.exe tools/hub.py pull --split train

# 특정 참여자 또는 특정 동작만 골라 받기
.\.venv\Scripts\python.exe tools/hub.py pull --person tester01 --preset thumb_middle

# 공유된 최신 TCN 모델(.onnx + .json)까지 함께 받기
.\.venv\Scripts\python.exe tools/hub.py pull --split train --models
```

### 3.4 학습된 TCN 모델 공유 (`push --model`)

학습 스크립트(`scripts/train_tcn.py` 또는 `scripts/train_selected.py`)로 만든 `.onnx`와 `.json` 모델 카드 쌍을 검증한 뒤 허브의 `models/` 폴더에 업로드한다.

```powershell
.\.venv\Scripts\python.exe tools/hub.py push --model models/gesture_tcn.onnx
```
