# 문서 목록 (2026-11-05)

저장소에는 세 세대의 앱이 함께 있어 문서도 섞여 있다. 아래에서 **현재** 문서만 지금의 코드와 규칙을 설명한다. **기록** 문서는 당시의 결정·계획·실행법으로 보존하며, 현재 구현이나 규칙으로 인용하지 않는다. 기록 문서를 지우거나 옮기지 않은 이유는 Jira·PR·발표자료가 이 경로로 링크하기 때문이다.

## 현재: 저장소 규칙

| 문서 | 내용 |
| --- | --- |
| [../AGENTS.md](../AGENTS.md) | 사람·에이전트 공통 작업 규칙 |
| [branch-flow.md](branch-flow.md) | 작업 브랜치 → preview → production, 검사 깊이 |
| [harness.md](harness.md) | 작업 접수·결과 보고 형식 (`harness/`) |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | PR·검사 절차. 검사 명령 부분은 이전 웹앱 기준이다 |

## 현재: MANUX (`apps/manux/`, v0.4)

| 문서 | 내용 |
| --- | --- |
| [../apps/manux/README.md](../apps/manux/README.md) | 실행·시험·데이터 수집 |
| [../apps/manux/AGENTS.md](../apps/manux/AGENTS.md) | v0.4 작업 규칙과 현재 상태 |
| [../apps/manux/docs/architecture.md](../apps/manux/docs/architecture.md) | 계층·의존 규칙·이식 순서 |
| [../apps/manux/docs/contracts.md](../apps/manux/docs/contracts.md) | 모듈 계약 |
| [../apps/manux/docs/decisions.md](../apps/manux/docs/decisions.md) | 확정·제안 결정 (D-001~) |
| [unified-rebuild/selection.md](unified-rebuild/selection.md) | v0.4 착수 때 팀 자료에서 고른 것과 뺀 것 (11/05) |

## 이전 버전 v0.3 (`hand-mouse/`)

`apps/manux`에 설치본이 생길 때까지 Releases `v0.3.x` 설치파일은 이 폴더에서 빌드한다. 기능 설명은 `apps/manux`로 옮긴 기능의 출처로 참고한다.

| 문서 | 내용 |
| --- | --- |
| [../hand-mouse/README.md](../hand-mouse/README.md) | 실행 옵션·설정 키·트래킹 측정·실패 기록 |
| [../hand-mouse/docs/packaging.md](../hand-mouse/docs/packaging.md) | 현재 설치본·자동 업데이트·릴리스 (`apps/manux` 설치본이 생기면 교체) |
| [../hand-mouse/docs/engine-contract.md](../hand-mouse/docs/engine-contract.md) | 검지 포인터 엔진의 입력·출력 |
| [../hand-mouse/docs/motion-mode.md](../hand-mouse/docs/motion-mode.md) | 모션 모드 |
| [../hand-mouse/docs/hand-shape-speed.md](../hand-mouse/docs/hand-shape-speed.md) | 손 모양으로 커서 속도 정하기 |
| [../hand-mouse/docs/browse-cursor.md](../hand-mouse/docs/browse-cursor.md) | 스크롤 중 커서 고정 |
| [../hand-mouse/docs/jira-20261104.md](../hand-mouse/docs/jira-20261104.md) | 11/04 개선 기록 (Jira 반영됨) |

## 현재: 제품 설계와 사용자 평가

앱 세대와 관계없이 쓰는 설계·평가 자료다. 일부는 웹앱 시절에 썼으므로 "현재 구현" 줄은 각 문서의 날짜 기준이다.

| 문서 | 내용 |
| --- | --- |
| [m1-webcam-mouse/](m1-webcam-mouse/README.md) | M1 웹캠 마우스 설계 검토, 평가 키트·진행 안내·기록지 |
| [design/decision-pointer-click.md](design/decision-pointer-click.md) | 커서 기준점·클릭 방식 (결정 전) |
| [design/decision-customization.md](design/decision-customization.md) | 개발자 없이 하는 동작 맞춤 설정 (제안) |

## 기록: 이전 웹앱·Electron (v0.2, `src/`·`desktop/`)

코드는 남아 있고 회귀 시험도 돌지만 새 기능은 넣지 않는다.

| 문서 | 내용 |
| --- | --- |
| [onboarding.md](onboarding.md) | 웹앱 첫 실행·문제 해결 |
| [air-mouse.md](air-mouse.md) | 웹앱 손 전체 마우스 (11/02) |
| [usage-video.md](usage-video.md) | 영상 제어·개인 등록 |
| [desktop-release.md](desktop-release.md) | Electron 설치본·자동 업데이트. 정식 게시 workflow는 11/05 삭제 |
| [settings-store.md](settings-store.md) | 웹앱 설정 저장소 계약 |
| [contracts.md](contracts.md) | 웹앱 모듈 계약 |
| [design/](design/README.md) 01~05 | 웹앱 기준 설계 문서 5종 |
| [verification.md](verification.md) | 10/29 각도별 등록 검증 기록 |
| [lan-demo.md](lan-demo.md) | 10/29 LAN 브라우저 데모 (호스트 주소는 당시 값) |
| [workstreams/](workstreams/) | R1~R5 작업 영역 지침 (11/03 고정 담당 폐지) |

## 기록: 계획·인수인계·발표 (10/29~11/03)

| 문서 | 내용 |
| --- | --- |
| [master-plan.md](master-plan.md) · [team-plan.md](team-plan.md) | 10/29 계획과 역할 배분 (11/03 담당 배정 폐지) |
| [jira-change-proposal.md](jira-change-proposal.md) | 10/29 Jira 변경 제안 |
| [jira-snapshot-20261029.json](jira-snapshot-20261029.json) · [jira-sprint-20261029.json](jira-sprint-20261029.json) | 10/29 Jira 사본 |
| [github-handoff.md](github-handoff.md) | 10/29 첫 GitHub 게시 준비 |
| [desktop-hmi.md](desktop-hmi.md) | 10/29 데스크톱 HMI 방향 |
| [career-skills.md](career-skills.md) | 10/29 프로젝트에서 설명할 역량 정리 |
| [presentation/](presentation/README.md) | 10/29 착수보고 발표 패키지 |

Jira의 작업 기록(PR 링크·병합일)은 에픽 [SCRUM-80](https://se4nchoi.atlassian.net/browse/SCRUM-80)(11/04까지)과 [SCRUM-118](https://se4nchoi.atlassian.net/browse/SCRUM-118)(v0.4 이식, 11/05)에 있다.
