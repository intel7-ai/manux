# testing: CI 대기 없는 공동 소스 시험

현재 v0.4 통합본은 루트 `run_manux.bat`로 시작한다. 동작별 데이터 공유·모델 이름/선택/복원·
포즈 표본·가속과 실제 카메라 확인 범위는 [통합 사용 흐름](../apps/manux/docs/testing-all-v04.md)을 따른다.

`testing`은 최신 preview를 바탕으로 여러 작업을 PR 병합해 실제 PC에서 빠르게 시험하는 브랜치다. `testing` 대상 PR과 push에는 자동 CI·설치본 빌드·릴리스가 없다. 기존 `workflow_dispatch`가 있는 workflow는 필요할 때 수동 실행할 수 있다.

## 앱 받기와 실행

기존 작업 폴더·설치본을 보존하려면 새 폴더에 받는다. Windows·Git·uv가 필요하다.

```powershell
git clone --branch testing https://github.com/intel7-ai/manux.git manux-testing
cd manux-testing/apps/manux
uv run --locked --python 3.12 --extra ui --extra vision --extra ml --extra train --extra hub python -m manux
```

Qt 화면이 열리며 설치파일은 필요 없다. 최초에는 Python과 큰 학습 의존성을 받을 수 있다. 의존성을 준비한 뒤에는 코드 변경 후 앱을 다시 실행해 확인한다.

개발 변경이 없는 시험 전용 폴더에서 새 버전을 받을 때는 앱을 닫고 저장소 루트에서 실행한다.

```powershell
git status --short
git pull --ff-only origin testing
git rev-parse HEAD
```

미커밋 변경이 있으면 덮어쓰거나 삭제하지 말고 별도 작업 브랜치에 보존한다. 소스 앱의 데이터는 `%APPDATA%/MANUX-dev/v04`에 저장된다. Preview 설치본의 `%APPDATA%/MANUXPreview/v04`와 경로가 다르며 자동 이전하지 않는다. 여러 소스 checkout은 같은 MANUX-dev 데이터를 사용하므로 동시에 실행하지 않는다.

## 통합과 정식 전달

1. 기능·수정은 기존 규칙대로 최신 `origin/preview`에서 만든 본인 작업 브랜치에서 진행한다. 변경에 맞는 로컬 시험과 `npm ci --ignore-scripts`, `npm run verify`는 유지한다.
2. 빠른 공동 시험이 필요하면 그 작업 브랜치에서 **base = testing**인 PR을 열어 병합한다. CI 완료나 설치본을 기다리지 않고 위 명령으로 실행한다. 이 브랜치에는 필수 상태 검사를 설정하지 않는다.
3. testing과 충돌하면 `origin/testing`에서 별도 통합 브랜치를 만들어 원본 작업 브랜치를 병합하고 충돌을 해결한다. 그 통합 PR만 testing에 반영해 원본 작업에 다른 사람의 미검증 변경이 섞이지 않게 한다.
4. 시험 기록에는 testing의 커밋 SHA, 모델 버전, 조건, 재현 절차, 실제/합성 여부를 적는다. 웹캠 시험을 자동 시험 통과로 대체하지 않는다.
5. 정식 반영은 **원본 작업 브랜치 → preview PR**로 전달한다. 통합 과정에서 필요한 수정도 원본에 반영하고 기존 필수 검사를 통과한다. testing 전체를 preview로 병합하지 않는다. production은 계속 preview에서만 PR을 받는다.

preview의 새 변경이 필요하면 preview → testing PR로 받아 시험 기준을 갱신한다. testing을 강제 push로 초기화하거나 다른 사람의 통합 결과를 지우지 않는다.

## 공동 데이터 수집

- 수집을 시작할 때 팀이 사용할 testing 커밋 SHA·동작 목록·수집 계획·모델 버전을 정하고 기록한다. 한 수집 차수 중간에 버전을 바꾸지 않는다.
- 먼저 두 PC에서 소량 녹화 → 허브 업로드 → 다른 PC 다운로드·확인까지 수행한 뒤 수집량을 늘린다.
- 파일 완료·손상·중복 검사와 실제 손짓/라벨의 정확성 확인을 구분한다. 모델이 거절한 올바른 시도를 임의로 삭제하지 않는다.
- train/eval은 별도 시도로 수집한다. 영상·개인 녹화·모델·인증 정보는 Git에 올리지 않는다. 공유는 [검증된 허브 경로](../apps/manux/docs/dataset-hub.md)를 사용한다.

testing 브랜치는 앱 기능이나 모델 구성을 자동으로 바꾸지 않는다. 동작별 독립 이진 TCN 이식은 별도 변경이며, testing 생성만으로 적용된 것으로 보고하지 않는다.

## Rasp-Jun 학습센터 실험 앱

`codex/rasp-jun-training-center`의 선택 학습·진행률/ETA·TCN 개선은
`apps/manux/experiments/rasp-jun-training-center/`에 별도 실행 소스로 포함한다.
v0.4 기본 실행 경로와 기존 Dan 실험 소스는 유지한다. 모델 준비, 실행 명령과 검증 한계는
[통합 안내](../apps/manux/experiments/rasp-jun-training-center/INTEGRATION.md)를 따른다.
