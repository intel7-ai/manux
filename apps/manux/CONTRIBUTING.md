# MANUX 기여 안내

2026-11-07 10:00 KST부터 `manux-preview`를 개발 기준으로 사용한다. 아래 명령은 독립 브랜치의 저장소 루트에서 PowerShell 7로 실행한다. 변경 전에 [AGENTS.md](AGENTS.md), [구조](docs/architecture.md), 해당 [계약](docs/contracts.md)을 읽는다.

## 설치와 실행

Git·[uv](https://docs.astral.sh/uv/getting-started/installation/)를 설치한 뒤 저장소를 복제한다. uv가 Python 3.12와 잠금 파일의 의존성을 준비한다.

```powershell
git clone --branch manux-preview https://github.com/intel7-ai/manux.git
cd manux
uv sync --locked --python 3.12 --extra ui --extra vision --extra ml
uv run --locked --python 3.12 --extra ui --extra vision --extra ml python -m manux
```

기본 실행에는 torch가 필요하지 않다. 고급 TCN 학습·허브 worker를 개발할 때만 `--extra train --extra hub`를 추가한다. 원본 녹화와 개인 모델은 저장소 밖 사용자 폴더에 보관하며 공유는 [허브 안내](docs/dataset-hub.md)의 도구를 따른다. `.env`·키·실명·영상은 커밋하지 않는다.

## 시험

```powershell
# 최소 환경 전체 시험
uv run --locked --python 3.12 pytest -q
# 구조와 계약을 별도로 확인
uv run --locked --python 3.12 pytest -q tests/architecture tests/contract
# Qt·손 인식·ONNX 의존성을 포함한 전체 시험
uv run --locked --python 3.12 --extra ui --extra vision --extra ml pytest -q
# 웹캠 없이 번들 모델·라이브러리 자가 진단
uv run --locked --python 3.12 --extra ui --extra vision --extra ml python -m manux --self-check "$env:TEMP/manux-self-check.json"
```

최소 환경에서 선택 의존성 시험이 skip될 수 있으므로 개수·사유를 PR에 적는다. 합성·카메라 없는 시험과 실제 웹캠·Windows 마우스/키보드 확인을 구분한다. 실패를 숨기려고 시험을 삭제하거나 기대값만 맞추지 않는다. 인식률은 학습과 다른 시도·세션에서 오실행·지연과 함께 측정한다.

## 브랜치와 PR

시작할 때 Git 상태·브랜치·원격·열린 PR을 확인한다. 다른 작업을 지우거나 덮어쓰지 않는다.

```powershell
git fetch origin
git switch -c 이슈-설명 origin/manux-preview
```

이슈가 없으면 설명만 쓰며 도구 접두사는 붙이지 않는다. 작업 브랜치를 push하고 `manux-preview` 대상 PR을 연다. 목표·변경·영향받는 계층/계약·검증·미실행 항목은 [PR 양식](.github/PULL_REQUEST_TEMPLATE.md)을 따른다. 리뷰어 승인은 필수가 아니지만 `MANUX required` 통과와 Preview 설치본 확인 후 병합한다. 다른 브랜치 이력이 유입되지 않도록 squash 병합을 권장한다. 보호 브랜치 직접 push·강제 push·필수 검사 우회는 금지한다.

```text
작업 브랜치 → manux-preview PR → 필수 검사·설치본 확인 → squash 병합
manux-preview → manux-production PR → 필수 검사·설치본 확인 → 정식 릴리스
```

`manux-production` 대상 PR은 같은 저장소의 `manux-preview`만 허용한다. 정식 발행은 `manux-production` push의 [release.yml](.github/workflows/release.yml)이 `build.ps1 -Release`로 검증한 기본 설치본을 `v0.5.<run_number>` GitHub Release에 게시한다. 빌드는 읽기 권한만 사용하고 publish job에만 `contents: write`를 준다. 실제 발행과 수동 사용성 검증은 별개다.

독립 CI의 [standalone.yml](.github/workflows/standalone.yml)은 Windows pytest·구조/계약·기본 Preview 빌드·설치·자가 진단을 `MANUX required`로 집계한다. workflow는 원본 preview의 중첩 경로에 있을 때 실행되지 않는다. 전환 및 토큰 권한 문제의 수동 복구는 [브랜치 흐름](docs/branch-flow.md)을 따른다.
