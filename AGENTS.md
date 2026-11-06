# MANUX 작업 규칙

- 현재 앱은 `apps/manux/`(Python + Qt/QML)다. 앱 작업 전 [앱 지침](apps/manux/AGENTS.md)을 읽고, 변경할 계층의 계약부터 확인한다.
- `hand-mouse/`는 정식 v0.3 릴리스 유지보수 전용이다. v0.4 배포 전환 전까지 보존한다. 기본 검색에서는 제외하며 필요할 때 `rg --files --no-ignore hand-mouse/`로 명시해 찾는다.
- 이전 웹앱·Electron은 현재 트리에서 퇴역했다. 요청받은 과거 조사만 [보관 안내](archive/README.md)를 따른다. 과거 코드를 기본 맥락으로 읽지 않는다.

## 작업과 전달

1. 시작할 때 `git status --short`, `git branch --show-current`, `git remote -v`를 확인한다. 다른 작업자의 변경·프로세스·worktree를 보존한다.
2. 새 작업은 최신 `origin/preview`에서 작업별 브랜치를 만든다. 이름은 `<이슈>-<설명>` 또는 `<설명>`이며 도구 접두사를 붙이지 않는다. 진행 중 브랜치는 그대로 이어간다.
3. 목표·완료 기준·범위·실제 기준 SHA를 `.local/harness/<requestId>/task.json`에 기록한다. Jira가 없어도 진행한다. 시작 전에 열린 PR·원격 브랜치를 확인하고 작업 브랜치를 push해 중복 작업을 알린다. 요청 없는 담당 변경·이슈 완료는 하지 않는다.
4. 작은 변경 후 앱 시험과 `npm ci --ignore-scripts`, `npm run verify`를 실행한다. Python 시험은 `apps/manux`에서 `uv run --locked --python 3.12 pytest -q`다. 실패를 숨기려고 검사를 지우거나 기대값만 바꾸지 않는다.
5. 같은 폴더의 `result.json`에 실제 증거·한계를 기록하고 `npm run task:report -- TASK.json RESULT.json`으로 보고한다. 형식은 [하네스](docs/harness.md)를 필요할 때 참고한다.
6. 기본 전달은 본인 브랜치 push와 `preview` 대상 PR까지다. 리뷰어 승인은 필요 없고 필수 검사를 확인한다. 실제 preview 시험 뒤 `preview → production` PR로만 정식 반영한다. 직접 push·강제 push·필수 검사 우회는 하지 않는다([브랜치 흐름](docs/branch-flow.md)).

## 안전과 사실

- 프레임을 쌓지 않는다. 중단된 세션의 결과를 무시하고 자원을 해제한다. 등록·녹화·연습·일시정지 중 실제 Windows 입력을 내보내지 않는다.
- 동작 ID와 액션 ID를 구분한다. 개인 모델의 거절을 규칙·기본 모델로 우회하지 않는다. 취소·실패·검증 전 저장은 기존 유효 등록·모델을 보존한다.
- 영상·실명·프로필·계정·키를 Git이나 시험 fixture에 넣지 않는다. 데이터 공유는 명시적 요청과 검증된 `tools/hub.py` 경로만 쓴다.
- 합성 시험·CI·실제 웹캠/Windows 시험을 구분한다. 인접 프레임은 독립 시도가 아니며 시험 수를 제품 정확도로 말하지 않는다. Jira/원격 조회 실패를 완료나 작업 없음으로 바꾸지 않는다.
- 결과 상태는 실제 완료 기준에 맞춘다. `delivery.integrated`는 production 반영 때만 true다. 응답은 `상태 → 요약 → 변경 → 검증 → 다음`과 필요한 `- ` 목록을 사용한다. Discord 종료 보고는 renderer 본문을 그대로 보내고 검사 훅을 우회하지 않는다. 별도 지시 없이 다른 사람에게 메시지를 보내지 않는다.
