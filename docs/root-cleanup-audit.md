# 루트 폴더 정리 조사

2026-11-05 기준. preview `589cc9a8d338e40df8edc005825caf0c9a6329fc`.
[모델·가속 조사](../apps/manux/docs/acceleration-and-model-investigation.md)에 함께 제시하는 정리 제안이다.
파일을 삭제하거나 옮기지 않았다. 실행 중인 worker의 작업 폴더는 읽기만 했다.

## 조사 결과와 처리 방향

어수선함의 주원인은 참조되지 않는 문서가 아니라 **세 세대의 앱이 실행·안내 진입점을 공유하는 것**이다.
`docs/README.md`는 이미 기록 문서를 구분하며 Jira·PR·발표자료의 링크 때문에 기존 경로를 보존한다고 명시한다.
안내 페이지 없이 삭제·이동하면 변경 배경을 잃거나 해당 링크가 끊긴다.

| 루트 항목 | 확인한 근거 | 권장 처리 |
| --- | --- | --- |
| `README.md`, `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md` | 저장소 진입 문서. CLAUDE는 AGENTS를 참조 | 유지하되 현재 v0.4 안내를 명확히 함 |
| `apps/manux/` | 현재 v0.4 앱 | 유지. 기본 개발 위치 |
| `hand-mouse/` | `.github/workflows/hand-mouse-release.yml`이 아직 v0.3 릴리스를 담당 | 검증된 v0.4 릴리스 workflow가 대체할 때까지 유지 |
| `package.json`, 잠금 파일, `scripts/`, `tests/`, `harness/` | `ci.yml`에서 npm verify 실행. 보고서는 task-report.mjs 사용 | 공통 도구 유지. 이전 앱 의존성을 분리한 뒤 npm 앱 코드를 정리 |
| `.node-version`, `.nvmrc` | setup-node는 `.node-version` 사용. prepare-release.mjs는 둘 다 포함 | 호출부와 지원할 로컬 도구를 전환할 때까지 유지 |
| `src/`, `public/`, `desktop/`, `server.js` | npm 명령·서버 시험·브라우저 검사·Electron 패키징에서 참조 | 별도 PR에서 v0.2를 한 단위로 정리. server.js만 옮기면 import가 깨짐 |
| `electron-builder.yml` | desktop-build.mjs·test-installed-update.mjs·desktop-package-config.test.js에서 사용 | 단독 삭제하지 않고 Electron 도구와 함께 정리 |
| `MANUX *.cmd` 실행·검사 파일 4개 | README·온보딩에서 참조. npm이나 이전 start-manux.ps1을 실행 | 가장 혼동하기 쉬운 항목. 참조를 고친 뒤 이전 앱 실행기를 함께 이동. 공통 검사는 명확한 이름의 명령으로 유지 |
| `start-manux.ps1`, `start-manux-lan.ps1`, `stop-manux-lan.ps1` | MANUX 실행.cmd·LAN 안내·prepare-handoff.ps1이 해당 경로 참조 | v0.2 실행기와 함께 보관하고 경로 가정도 함께 수정 |
| `docs/` | 현재 브랜치·하네스·평가 자료와 과거 v0.2·계획이 섞임 | 현재 문서 색인 유지. 이동 시 기록 표시나 새 위치 안내 페이지 추가 |
| 공유 작업 폴더의 `.local/`, `dist/`, `test-results/`, `node_modules/` | 로컬 보고서·빌드 결과·의존성. 기존 무시 규칙 적용 | 생성물이라고 worker 1 실행 중 지워도 되는 것은 아님. 그대로 보존 |
| 공유 작업 폴더의 `.worktrees/` | 이번 조사의 별도 Git worktree | 불필요한 파일로 취급해 삭제하지 않음. 작업 종료 후 git worktree 명령으로 관리 |

새 worktree의 루트에는 `solution.md` 같은 추가 조사 파일이 없다.
AGENTS에서 해당 이름을 언급한다고 현재 추적 중인 루트 파일이 존재한다는 뜻은 아니다.
추측 대신 실제 파일 목록으로 판단한다.

## 구체적인 정리 순서

1. **안내 경로부터 정리한다.** CONTRIBUTING의 첫 문단은 v0.4 안내보다 앞에서
   이전 `docs/design`·`workstreams`·`docs/contracts`로 독자를 보낸다.
   이전 npm 절보다 앞에 현재 uv 실행·pytest 안내를 둔다.
   루트 AGENTS에도 v0.4 안내 뒤 여러 세대의 읽기 목록이 섞여 있다.
   브랜치·보고 규칙은 보존하고 과거 계약은 과거 코드에만 연결한다.
2. **서로 모순되는 상태 설명을 정리한다.** 기준 커밋의 `apps/manux/AGENTS.md`는
   앞에서는 S6 패키징을 설명하고 뒤에서는 패키징이 없다고 한다.
   `next-steps.md`는 #69로 들어온 기능·캐시·핫리로드를 미구현으로 적고 있다.
   해당 부분에 과거 시점을 표시하고 현재 상태의 기준을 하나로 둔다.
   worker 1의 #72가 같은 상태 문서를 수정하므로 그 작업이 들어온 뒤 기준을 갱신한다.
3. **v0.2를 한 단위로 보관한다.** 실행 가능한 소스를 `archive/web-v02/`에 둘지,
   접근 가능한 마지막 Git 커밋·태그를 기록으로 삼을지 정한다.
   workflow·스크립트·시험·문서에서
   `rg -n 'server\.js|electron-builder|start-manux|stop-manux'`로 참조를 조사한다.
   하네스·보고 검사를 이전 앱 검사에서 먼저 분리한다.
   실행기뿐 아니라 prepare-release.mjs·prepare-handoff.ps1도 고친다.
   새로 복제한 환경에서 시험한다. 이동을 통과시키려고 검사를 일괄 삭제하지 않는다.
4. **링크를 보존한다.** 오래된 계획을 `docs/archive/2026-10/`로 옮길 때는
   안내 페이지를 유지할 비용보다 정리 이익이 큰지 판단한다.
   이전 경로에는 날짜와 새 위치를 알리는 작은 문서를 남기고 상대 링크를 수정한다.
   현재 사용하는 M1 평가 자료는 계속 찾을 수 있게 둔다.
5. **v0.3는 나중에 정리한다.** 실제 v0.4 설치본·업데이트·Windows 제어·복구 경로를
   확인한 뒤에만 릴리스 workflow를 전환하고 v0.3를 보관한다.
6. **로컬 생성물은 따로 정리한다.** 정확한 경로·실행 중 프로세스·worktree 소유 관계를 확인하고
   보고서·데이터·모델을 보존한다. 다른 worker가 실행 중일 때 `git clean -fdx`를 실행하거나
   공유 빌드 폴더를 재귀 삭제하지 않는다.

## 정리 후 루트 구성 제안

`apps/`, `docs/`, `.github/`, 에이전트·기여·README 진입 문서와 최소 공통 하네스·도구 설정을 둔다.
이전 실행 코드와 실행기는 현재 참조가 모두 전환된 뒤 루트에서 옮길 수 있다.
숨김 도구 설정은 보기 좋게 만들기 위해 옮기지 않고 해당 도구가 요구하는 위치에 둔다.

## 후속 정리 PR에서 확인할 것

`git ls-files`, 숨김 `.github`·`.claude`를 포함한 참조 검색, Markdown 상대 링크 검사,
새로 복제한 환경의 npm 설치·verify, v0.4 pytest, 패키징 workflow 확인,
실행기 기본 동작 확인, 보고서 생성을 수행한다. 남겨 둔 이전 빌드 경로도 시험한다.
외부 링크의 사용 여부를 모르면 이전 경로에 새 위치 안내를 남긴다.

이번 조사는 정리 후보와 의존성을 확인한 것이다.
후속 이동 검사나 실제 파일 정리가 이미 수행됐다는 뜻은 아니다.
