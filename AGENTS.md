# MANUX 팀 에이전트 작업 지침

## 가장 먼저: v0.4 구조 (`apps/manux/`)

2026-11-05부터 새 개발은 `apps/manux/`(v0.4, Qt/QML + Python)에서 한다. **`apps/manux/` 안의 작업, `hand-mouse/` 기능을 옮기는 작업, 손짓·TCN·출력·화면 구조에 관한 작업은 시작 전에 [apps/manux/AGENTS.md](apps/manux/AGENTS.md)와 거기서 지정한 [구조](apps/manux/docs/architecture.md)·[계약](apps/manux/docs/contracts.md)·[결정 기록](apps/manux/docs/decisions.md)을 먼저 읽는다.** 계약과 다른 구현을 하지 않는다. 계약을 바꿔야 하면 계약 문서부터 고친다.

| 폴더 | 지위 (2026-11-05) |
| --- | --- |
| `apps/manux/` | v0.4 개발 위치. 이식 S0~S5 병합, S6(데스크톱 화면) 진행 중. 아직 설치본이 아니다. 상태는 [apps/manux/AGENTS.md](apps/manux/AGENTS.md) "현재 상태"가 기준이다 |
| `hand-mouse/` | v0.3 설치본(Releases `v0.3.x`, `hand-mouse-release.yml`이 production 병합 때 게시). 버그 수정과 데이터 수집만. PR 본문에 v0.4 반영 여부를 적는다 |
| `src/`, `public/`, `server.js`, `desktop/` | 이전 웹앱·Electron(v0.2). 새 기능을 넣지 않는다. 정식 게시 workflow는 2026-11-05 삭제했다. 아래의 웹앱 문서(`docs/contracts.md`, `docs/design/`)는 이 코드의 기록이다 |

문서별 현재/기록 구분은 [docs/README.md](docs/README.md)를 본다.

---

이 저장소는 로컬 웹캠으로 영상과 Windows 마우스를 제어하는 MANUX의 공동 작업 공간이다. 먼저 [README](README.md)와 [하네스](docs/harness.md)를 읽고, 작업에 필요한 [설계 문서 5종](docs/design/README.md)·[모듈 계약](docs/contracts.md)과 관련 영역의 [작업 문서](docs/workstreams/)만 추가로 읽는다. 설계 문서의 컴포넌트 **하지 않는 것** 열이 파일 경계이고, 코드에 문서에 없던 판단이 생기면 같은 PR에서 문서를 고친다. [마스터 플랜](docs/master-plan.md)은 과거 계획을 포함하므로 현재 구현과 구분한다. 사람의 현재 요청이 이 문서보다 우선한다.

## 현재 상태

- 현재 마일스톤 범위의 기준은 최신 [SCRUM-5](https://se4nchoi.atlassian.net/browse/SCRUM-5)와 [SCRUM-11](https://se4nchoi.atlassian.net/browse/SCRUM-11)의 세부 태스크다. 2026-11-02 승인된 M1은 11/04 PoC·11/06 Windows 책상 마우스 제품 후보다. 2026-11-03부터 고정 역할·담당 배정은 없다. 세부 태스크의 담당이 비어 있으면 아래 "작업 가져가기"를 따른다. 10/29의 팀 계획·마스터 플랜은 과거 계획이므로 현재 Jira 범위와 충돌할 때 따르지 않는다.
- 개발 경로는 개인 PC의 원하는 AI 또는 Discord worker → 작업별 브랜치 → preview 대상 PR → 자동 검사 → preview 머지·시험 → `preview → production` PR → production 머지 → Windows 설치본 릴리스다. 리뷰어 승인은 요구하지 않는다([브랜치 흐름](docs/branch-flow.md)). Jira 번호가 없는 개선도 목표·범위를 기록하여 시작할 수 있다.
- 팀원 개인 PC의 AI 에이전트 개발/브랜치 푸시와 Discord Claude의 독립 worktree 개발을 모두 지원한다. 공유된 기준 커밋을 확인하고 `scrum-23-gesture-enrollment`처럼 세부 태스크 번호를 브랜치·PR에 명시한다. 외부 푸시는 로컬 작업 폴더에 자동 반영되지 않으며 최신 원격 조회와 PR 리뷰·통합은 별개다.
- Discord의 할 일 질문은 Jira를, 원격 개발 현황은 GitHub를 새로 조회한다. OAuth 인증 계정이나 Git 작성자를 Discord 질문자로 간주하지 않는다. Jira/원격 조회 실패를 작업 없음이나 완료로 바꾸지 않는다.

아래 2026-11-02~11-03 항목은 **이전 웹앱(`src/`·`desktop/`)의 상태 기록**이다. v0.3·v0.4의 상태는 위 표와 각 폴더 README를 따른다.

- 2026-11-02 손 전체 이동 기반 마우스 모드와 Electron/Windows 입력 경로를 추가했다. 현재 계약·실행법은 `docs/air-mouse.md`와 `desktop/README.md`를 확인한다. 실제 웹캠 사용성과 실제 OS 클릭 검증은 별도다.

- 기본 네 명령, 선택적 ±10초 반복, 카메라·Worker, 사용자·환경 프로필, 여러 각도 등록 코드가 있다.
- 2026-10-29 사용자는 여러 동작을 한꺼번에 등록하는 부담과 실제 등록 실패를 보고했다. 원인은 아직 확인하지 않았다. 자동 시험 통과를 실사용 문제 해결이나 인식률 보장으로 표현하지 않는다.
- 2026-11-03 기준 동작 하나씩 등록하는 엔진(`trainGesture`·`registerGesture`·`classifyRegisteredGesture`)과 설정 저장소(`SettingsStore`)는 preview에 있다. 화면에서 사용자 정의 동작을 등록하는 경로, 등록 동작을 액션으로 실행하는 경로, 허용 액션 목록은 아직 없다. 현재 구현과 남은 일은 [설계 문서 01](docs/design/01-use-cases.md)의 "현재 구현" 줄이 기준이다. 문서의 제안 API를 현재 구현으로 설명하지 않는다.
- 현재 개인화는 관절 특징 예시 기반 분류다. 사전학습 MediaPipe 가중치를 파인튜닝하지 않는다.

## 브랜치와 담당 경계

작업을 시작할 때 `git status --short`, `git branch --show-current`, `git remote -v`로 실제 상태를 확인한다. 다른 사람의 변경을 되돌리거나 깨끗하게 만들기 위해 삭제하지 않는다. 신규 작업은 최신 `origin/preview`에서 작업 단위 `<이슈>-<설명>` 브랜치를 만든다. 한 사람의 모든 작업을 한 브랜치에 모으지 않는다. 기존 진행 중 브랜치·미커밋 파일·worker는 그대로 보존하고 재생성하거나 강제로 production에 맞추지 않는다.

통합 흐름은 [브랜치 흐름](docs/branch-flow.md)을 따른다. 작업 브랜치는 `origin/preview`에서 만들고 PR 대상은 `preview`다. 리뷰어 승인은 필요 없다. `production`에는 preview에서 시험한 뒤 `preview` 브랜치 PR로만 병합한다.

브랜치 이름에 도구 접두사(`codex/`, `claude/` 등)를 붙이지 않는다. `scrum-23-gesture-enrollment`처럼 `<이슈>-<설명>`만 쓰고, Jira 번호가 없으면 `<설명>`만 쓴다. 열린 PR의 head 브랜치 이름을 바꾸면 GitHub가 그 PR을 닫으므로, 이미 PR이 있는 브랜치는 이름을 바꾸지 않는다.

**작업 가져가기.** 담당이 없거나 막혀 있는 일(설계 문서의 "미구현"·"제안"·교차 검수 항목, Jira의 미배정 태스크)은 누구나 가져갈 수 있다. 가져가는 사람의 에이전트는 시작할 때 Jira 해당 이슈의 담당자·상태를 바꾸거나 코멘트를 남기고, 또는 GitHub에 브랜치·draft PR을 올려 작업 중임을 표시한다. 둘 다 할 수 없으면 사람에게 알린다. 표시 없이 같은 파일을 두 사람이 고치지 않도록, 시작 전에 Jira와 열린 PR·원격 브랜치를 새로 조회한다. 사람이 시키지 않은 담당 변경·이슈 완료 처리는 하지 않는다.

**파일 경계.** 어떤 파일이 어느 컴포넌트에 속하고 무엇을 하지 않는지는 [02 컴포넌트](docs/design/02-components.md) 표가 기준이다. 사람별 담당 파일은 두지 않는다. 고친 파일의 대응 테스트는 고친 사람이 같은 PR에서 맞춘다. `src/app.js`처럼 여러 컴포넌트를 묶는 파일은 [03 인터페이스](docs/design/03-interfaces.md) 계약을 먼저 고치고 최소 연결 diff만 넣는다. 한 PR에서 컴포넌트 경계 밖의 대규모 리팩터링을 하지 않는다. 계약을 바꾸면 PR 본문에 영향받는 컴포넌트를 적는다.

## 작업 순서

1. 현재 Git 상태를 확인하고 요청을 `harness/task.schema.json` 형식으로 정리한다. 비전공자에게 JSON 작성을 요구하지 않는다. 목표·완료 기준·허용 파일·기준 커밋을 기록하고, Jira가 없다는 이유만으로 진행을 막지 않는다.
2. 실패를 재현한다. 환경, 입력 순서, 예상/실제 결과를 기록하고 원인 가설을 사실과 분리한다.
3. 본인 브랜치에서 작은 변경을 구현하고 앱을 실행하여 확인한다. Discord worker는 최대 5개 독립 worktree에서 병렬 진행한다. 신규 worktree는 원격 preview의 정확한 SHA에서 시작하며 공유 checkout의 미커밋 변경을 복제하지 않는다. worker는 다른 worker나 공유 checkout을 수정하지 않는다.
4. `npm run verify`와 변경에 필요한 브라우저/Windows 검사를 실행한다. 실패를 숨기기 위해 테스트를 삭제하거나 기대값만 맞추지 않는다. 수정 뒤에는 관련 검증을 다시 실행한다.
5. `harness/result.schema.json` 형식으로 실제 증거·한계를 기록하고 `npm run task:report -- TASK.json RESULT.json`으로 보고서를 만든다. 본인 브랜치를 push하여 `preview` 대상 PR을 연다. PR의 필수 검사·시험용 Windows 설치파일을 확인하고 머지한 뒤 preview에서 시험한다. production에는 `preview → production` PR로만 반영한다. 공유 checkout에 코드를 먼저 통합하는 절차는 두지 않는다. 로컬 리뷰가 필요한 복잡한 변경만 별도 reviewer를 사용한다.

작업 기록은 `.local/harness/<requestId>/task.json`, `result.json`에 둔다. 폴더와 ID는 일치시킨다. 신규 Discord worker는 조정자가 절대 경로로 전달한 task.json을 자기 작업 폴더에 복사하고, 구현 전에 `baselineCommit`을 `.local/discord-baseline.json`의 `sourceHead`로 확정한다. 생성 훅이 접수 때보다 최신 preview를 조회할 수 있기 때문이다. 기존 진행 중 작업의 기준은 변경하지 않는다. 완료 후 조정자는 확정된 task.json과 result.json 둘 다 공유 checkout의 같은 위치에 복사하고 worker의 원본 기록을 보존한다. 코드는 작업 브랜치에 둔다. Git에 올릴 PR에는 민감정보를 제거한 보고서와 검사 증거만 첨부한다.

## 모든 사용자에게 같은 응답

- 최종 보고서는 renderer가 만든 `상태 → 요약 → 변경 → 검증 → 다음` 순서와 각 항목 아래 `- ` 목록 줄을 그대로 사용한다. 같은 기록의 브라우저용 보고서는 `npm run task:report -- TASK.json RESULT.json --html .local/보고서.html`로 만든다(`.local/` 또는 `reports/` 아래만 허용). `complete`는 해당 요청의 검증 완료이며 PR 게시·production 머지·릴리스와 별개다. `delivery.integrated`는 production에 반영됐을 때만 true다. PR·배포 링크와 상태를 사실대로 표시한다.
- 완료 기준이 충족되지 않았거나 검사를 실행하지 못했으면 `needs_review` 또는 `blocked`로 기록한다. 결과 계약의 검사는 형식과 증거 유무를 확인하며 내용의 진실성을 대신 보증하지 않는다.
- 질문·인사·접수·진행도 같은 다섯 항목을 쓴다. 예: `상태: 정보`, `요약: ...`, `변경: 없음`, `검증: 미실행(안내 답변)`, `다음: ...`. 간단한 안내에는 작업 JSON을 만들지 않는다.
- Discord의 `reply`와 `edit_message`는 `.claude/settings.json`의 전송 전 검사 훅을 거친다. 종료 상태는 기록과 renderer 출력이 일치해야 전송된다. 막히면 원인을 수정하고 재시도하며 HTTP·다른 도구로 우회하지 않는다. Discord 전송은 원래 사용자의 요청에 대한 답변만 한다.
- 기술 용어는 필요한 만큼만 설명하고 실행할 다음 행동을 적는다. 미실행·실패·가설을 완료·성공·사실로 바꾸지 않는다.

## 기술 경계

- 오래된 프레임을 쌓지 않고 동시에 한 프레임을 처리하는 계약, 세션 종료 후 결과 무시, 자원 정리를 보존한다.
- 등록 중 영상 명령을 실행하지 않는다. 취소·실패·검증 전 저장은 기존 유효 등록을 손상시키지 않아야 한다.
- 개인 모델이 거절한 입력을 공통 모델 명령으로 우회하지 않는다.
- 향후 부분 등록은 선택한 동작·사용자·환경만 교체해야 한다. 누락된 클래스, 중립, 미등록 입력, 바인딩 없는 동작의 실행 정책을 계약과 시험으로 명시한다.
- 동작 ID와 액션 ID를 분리한다. `rock`이라는 이름이 분류 결과나 OS 명령 그 자체라고 가정하지 않는다.
- 사용자 이름·개인 프로필·원시 웹캠 영상·계정 정보·키를 시험 fixture나 저장소에 넣지 않는다.

## 검증과 주장

`npm ci --ignore-scripts` 후 `npm run verify`가 로컬·CI 공통 검사다. 모델·웹캠 없이 실행되는 단위 시험과 합성 브라우저 시험, 실제 사용자 시험을 구분한다. 브라우저 검사 준비와 CI 실패 해결은 [CONTRIBUTING](CONTRIBUTING.md)을 따른다.

같은 손 자세의 인접 프레임은 독립적인 사용자 시도가 아니다. 등록과 평가 수행을 분리하고 성공률과 함께 오실행·지연·등록 실패를 기록한다. 테스트 수나 프레임 점수를 제품 정확도로 설명하지 않는다. 실행하지 않은 검사와 아직 측정하지 않은 수치를 완료로 표시하지 않는다.

개발 요청의 기본 전달 범위는 본인 작업 브랜치의 push와 preview 대상 PR 생성까지다. 사용자가 로컬 작업만 지정하면 그 범위를 따른다. preview·production 직접 push·강제 push·필수 검사 우회는 하지 않는다. Jira 담당자 변경·이슈 완료는 별도 요청과 실제 완료 기준에 따른다. production 머지 뒤 배포는 검증된 릴리스 workflow가 수행한다.
