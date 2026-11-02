# MANUX 개발 참여 안내

팀원과 에이전트는 같은 작업 규칙과 검사 명령을 사용합니다. 먼저 [README](README.md), [공통 작업·결과 규칙](docs/harness.md), [AGENTS.md](AGENTS.md)를 읽으세요. 역할별 담당 범위는 [작업 문서](docs/workstreams/), 모듈 연결은 [계약](docs/contracts.md)을 따릅니다.

코드를 직접 수정하지 않는 팀원은 [Discord 참여 안내](README.md#discord로-참여하기)만으로 시작할 수 있습니다. 설치가 익숙하지 않으면 [첫날 온보딩](docs/onboarding.md)을 사용하세요.

## 1. 작업 위치와 기준 확인

Discord 작업은 협업 호스트의 별도 Git 작업 폴더에서 실행됩니다. 아래 로컬 개발 절차는 **본인 PC에서 직접 코드를 수정할 때** 사용합니다. 이미 작업 중인 호스트 폴더에 중복으로 복제하거나 다른 작업자의 파일을 정리하지 마세요.

처음 복제할 때:

```powershell
git clone https://github.com/intel7-ai/manux.git
cd manux
git branch -r
git status --short
```

팀이 공유한 기준 브랜치와 커밋을 확인한 뒤 그 브랜치에서 작업을 시작합니다. `main`이라는 이름만으로 최신 기능이 있다고 판단하지 않습니다. 공유되지 않은 로컬 변경은 복제에 포함되지 않습니다.

기존 폴더에서는 작업 전에 다음 상태를 기록합니다.

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git remote -v
```

미커밋 변경이 있으면 소유자와 내용을 먼저 확인합니다. 브랜치 전환·초기화·파일 삭제로 다른 작업을 덮어쓰지 않습니다. 확인한 기준에서 `codex/<이슈>-<짧은-설명>` 이름의 작업 브랜치를 만들고, PR에는 **기준 브랜치·기준 커밋·담당자**를 남깁니다. 기존 본인 브랜치가 있다면 그대로 이어갑니다.

## 2. 한 번 준비하고 같은 명령으로 검사

기준 환경은 Node.js **24**이며 Git도 필요합니다. Windows에서는 `MANUX 개발 준비.cmd`를 더블클릭하면 아래 준비와 공통 검사를 실행합니다. 다른 운영체제에서도 같은 npm 명령을 사용합니다.

```powershell
npm ci --ignore-scripts
npm run doctor
npm run verify
```

`npm ci`는 잠금 파일의 버전을 설치합니다. `--ignore-scripts`로 생략한 Electron 실행 파일은 데스크톱 실행이 필요할 때만 설치합니다. 웹캠 앱에 필요한 모델도 별도로 준비합니다.

```powershell
npm run setup
npm run desktop:setup
```

앱 실행은 `MANUX 마우스 실행.cmd` 또는 `npm run desktop`입니다. 브라우저는 `npm start` 후 <http://127.0.0.1:4173>에서 엽니다. 명령은 항상 저장소의 `package.json`이 있는 폴더에서 실행합니다. 패키지·모델·브라우저의 최초 다운로드에는 인터넷이 필요합니다.

## 3. 작은 작업과 일관된 결과

요청자가 **목표·현재 문제·완료 기준**을 적으면 에이전트가 [하네스 계약](docs/harness.md)의 작업 기록으로 정리합니다. 비전공자가 JSON을 직접 작성할 필요는 없습니다.

1. 현재 구현과 제안 기능을 구분하고, 재현 순서와 예상/실제 동작을 기록합니다.
2. 담당 영역에서 작은 변경을 구현합니다. `src/app.js`와 여러 모듈의 연결은 통합 담당자와 조정합니다.
3. 변경에 해당하는 검사를 실행하고, 통합 전에 `npm run verify`를 통과합니다.
4. 공통 형식 **상태 → 요약 → 변경 → 검증 → 다음**으로 결과를 공유합니다. 실제 실행 결과와 미실행 이유를 포함합니다.
5. 독립 작업의 결과를 리뷰하고 순서대로 통합합니다. 다른 작업자의 실행 중인 폴더나 결과를 덮어쓰지 않습니다.

에이전트의 내부 기록은 `.local/harness/<requestId>/task.json`과 `result.json`입니다. 보고서는 다음 명령으로 생성하며, 스키마·사용 예시는 하네스 문서가 원본입니다.

```text
npm run task:report -- TASK.json RESULT.json
```

작업 기록은 기본적으로 Git에 올리지 않습니다. 리뷰에 필요한 보고서·검사 결과를 PR에 첨부합니다. 형식 검사는 기록의 일관성과 증거 필드의 존재를 확인하며, 실제 동작의 정확성은 실행 결과와 리뷰로 확인합니다.

## 4. 검사 선택

| 변경 또는 목적 | 실행 | 확인 범위 |
| --- | --- | --- |
| 설치·환경 문제 | `npm run doctor` | Node·Git·의존성 등 준비 상태 |
| 코드·설정의 빠른 확인 | `npm run check` | 정적 검사와 설정·잠금 파일 일관성 |
| 모든 코드 변경의 공통 관문 | `npm run verify` 또는 `MANUX 검사.cmd` | 정적 검사와 전체 단위·서버 회귀 |
| 마우스 화면·브리지 연결 | `npm run test:browser` | 합성 관절 입력을 사용한 브라우저 통합 |
| Windows 입력 helper | `npm run test:native` | 실제 helper의 dry-run. 실제 커서 입력은 하지 않음 |
| 웹캠·등록·사용성 변화 | 실제 사용자 평가 | 실물 카메라·조명·거리·사용자별 성공/실패 |

브라우저 검사를 처음 실행하기 전에는 잠금 파일에 지정된 Playwright의 Chromium을 설치합니다.

```powershell
npm run browser:install
npm run test:browser
```

Windows 이외 환경의 native 검사는 명시적으로 건너뛰므로 Windows 검증으로 기록하지 않습니다. 실패한 검사를 통과로 바꾸기 위해 관련 없는 코드를 지우거나 검사를 비활성화하지 않습니다. 문제를 수정한 뒤 해당 검사와 영향을 받는 회귀만 다시 실행합니다.

합성 관절·카메라 검사 통과와 실제 사용자의 등록 실패는 동시에 존재할 수 있습니다. 자동 검사 수를 제품 인식률로 표현하지 않습니다. 실제 평가에서는 장치·해상도·거리·조명·사용자/세션 수, 등록/평가 분리, 오실행·지연·등록 실패를 기록합니다. 같은 자세의 인접 프레임은 독립적인 사용자 시도가 아닙니다.

### 영상·개인 등록의 추가 브라우저 검사

영상 또는 개인 등록을 변경한 담당자는 필요한 모델·사진 fixture를 준비하고 기존 추가 시나리오를 실행합니다. 사진 다운로드는 `tests/fixtures/sources.json`의 출처와 해시를 확인합니다. 일반 `verify`에는 다운로드나 웹캠이 필요하지 않습니다.

```powershell
npm run setup
node scripts/setup-test-fixtures.mjs
New-Item -ItemType Directory -Path public/media -Force | Out-Null
node --max-old-space-size=512 scripts/test-browser.mjs --make-demo
node --max-old-space-size=512 scripts/test-enrollment-browser.mjs
```

위 추가 브라우저 검사에도 `npm run browser:install`이 먼저 필요합니다. 첫 영상 검사는 90초 데모를 생성합니다. 데모가 있으면 `--make-demo`를 생략할 수 있습니다. 브라우저 검사를 동시에 여러 개 실행하지 않습니다.

`scripts/probe-personalization.mjs`는 숫자 fixture를 **덮어쓰는 조사 도구**입니다. 일반 설치·검사 과정에서 실행하지 않습니다. 데이터 변경이 필요한 담당자가 출처·모델 버전·변경 근거와 diff를 검토할 때만 사용합니다.

## 5. PR과 CI

한 PR에는 재현 가능한 문제 또는 합의된 작은 기능을 담습니다. [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md)에 따라 문제·변경·검증·미실행 이유·남은 한계를 기록합니다. PR 대상은 `preview`이며 리뷰어 승인 없이 MANUX required 통과 후 병합합니다. `production`에는 preview에서 시험한 뒤 `preview` PR로만 병합합니다([브랜치 흐름](docs/branch-flow.md)). 계약·프로필 형식·다른 담당 영역에 영향이 있으면 소비 모듈 담당자를 연결합니다.

GitHub Actions의 **MANUX CI**는 워크플로가 원격에 반영된 후 Windows와 Ubuntu의 공통 검사, 브라우저 검사, Windows native 검사를 실행합니다. 집계 검사 이름은 **MANUX required**입니다. **로컬 검사 통과와 원격 CI 통과를 각각 기록**합니다. 자동 검사는 실제 웹캠·사용성 검사를 대신하지 않습니다.

`preview`·`production`에는 직접 push하지 않고 PR로 통합합니다. 필수 검사를 강제하는 브랜치 보호는 GitHub 저장소 관리자가 별도로 설정해야 합니다. 워크플로 파일만으로 보호 규칙이 켜지지는 않습니다. 충돌은 본인 브랜치에서 해결하며 다른 사람의 브랜치를 강제 push하거나 재작성하지 않습니다.

사용자 프로필·원시 영상·토큰·계정 정보·로컬 산출물을 커밋하지 않습니다. 다른 사람의 Node·Chrome·Claude 프로세스를 일괄 종료하지 않습니다. GitHub/Jira 게시와 이슈 완료는 요청 범위와 실제 완료 기준에 따라 수행합니다.

## 6. 검증된 소스 전달

배포 담당자는 **커밋된 깨끗한 작업 폴더**에서 같은 코드로 검사를 통과한 뒤 소스 후보를 준비합니다.

```powershell
npm run verify
npm run release:prepare
```

`.local/releases`에 소스 폴더와 검증 정보를 담은 manifest가 생성됩니다. 미커밋 변경이나 오래된 검사 기록이 있으면 준비를 중단합니다. 미커밋 작업을 버려서 조건을 맞추지 말고 먼저 변경을 리뷰·커밋합니다.

GitHub의 수동 전달 워크플로 **MANUX source handoff**는 같은 기준의 소스 ZIP artifact를 만듭니다. 워크플로가 기본 브랜치에 등록되면 Actions 메뉴에서 수동 실행할 수 있습니다. 전달할 브랜치·커밋과 통과한 실행을 확인한 뒤 팀에 공유합니다. 이것은 설치 프로그램이나 자동 서비스 배포가 아니며, 받는 사람은 Node·Git·의존성·모델 등 실행 준비를 해야 합니다. 전달 이후 실제 사용자 평가 결과를 별도로 모읍니다.
