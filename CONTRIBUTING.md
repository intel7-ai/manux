# MANUX 개발 참여

현재 앱은 `apps/manux/`입니다. [앱 작업 지침](apps/manux/AGENTS.md)에서 변경할 계층의 구조·계약을 확인하세요.

## 시작

Git·Windows·uv를 준비하고 저장소 루트에서 실행합니다.

```powershell
cd apps/manux
uv sync --locked --python 3.12 --extra ui --extra vision
uv run --locked --python 3.12 pytest -q
uv run --locked --python 3.12 --extra ui --extra vision python -m manux
```

추가 학습 의존성·재생·설정 위치·시험 설치본은 [앱 README](apps/manux/README.md)를 따릅니다. 카메라 없이 가능한 자동 시험과 실제 사용자 시험을 구분합니다.

## 변경과 PR

1. Git 상태·브랜치·원격을 확인합니다. 다른 작업자의 변경과 worktree를 보존합니다.
2. 새 작업은 최신 `origin/preview`에서 `<이슈>-<설명>` 브랜치를 만듭니다. 기존 작업은 해당 브랜치에서 계속합니다. 열린 PR을 먼저 확인하고 작업 브랜치를 push합니다.
3. `.local/harness/<requestId>/task.json`에 목표·완료 기준·허용 파일·실제 기준 SHA를 기록합니다. 결과·검사 증거는 같은 폴더의 `result.json`에 남깁니다([하네스](docs/harness.md)).
4. 해당 Python 시험과 루트의 공통 검사를 실행합니다. Node.js 24에서 아래 명령을 사용합니다. 루트 npm 패키지는 외부 의존성이 없는 보고·검증 도구입니다.

```powershell
npm ci --ignore-scripts
npm run verify
npm run task:report -- TASK.json RESULT.json
```

5. 본인 브랜치를 push하고 `preview` 대상 PR을 엽니다. 필수 검사를 확인하고 미실행·실패·한계를 그대로 적습니다. 리뷰어 승인은 필요 없습니다.
6. preview에서 실제 앱을 시험한 뒤 `preview → production` PR로만 정식 반영합니다. 직접 push·강제 push·필수 검사 우회는 하지 않습니다([브랜치 흐름](docs/branch-flow.md)).

## 검사와 배포

- `MANUX CI`: 공통 하네스와 v0.4 Python 시험. `MANUX required`가 집계합니다.
- `MANUX app`: v0.4 변경 PR의 Windows 시험·Preview 빌드·자가 진단·창 열기·설치 검사. 실제 웹캠·입력 시험은 별도입니다.
- v0.3 정식 릴리스: `hand-mouse-release.yml`을 v0.4 배포 전환 전까지 유지합니다. Preview 빌드 성공만으로 정식 채널을 전환하지 않습니다.

영상·개인 프로필·토큰·원본 녹화·개인 모델을 Git이나 fixture에 넣지 않습니다. 사용자의 기존 데이터와 다른 작업자의 프로세스를 정리 대상으로 취급하지 않습니다.

## 이전 앱

웹앱·Electron·Node 소스 전달·LAN 실행 절차는 퇴역했습니다. 코드·전용 시험·실행 환경은 [보관 커밋](archive/README.md)에서 함께 복구합니다. 실패한 현재 검사를 숨기려고 삭제한 것이 아니라 지원 대상인 앱 세대를 분리한 것입니다.
