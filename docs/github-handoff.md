# GitHub 게시 준비와 팀 인수인계

준비일: 2026-10-29. 사용자가 지정한 게시 대상은 [intel7-ai/manux](https://github.com/intel7-ai/manux)다. 이 문서는 첫 게시 기준 파일과 후속 팀 개발 절차를 정리한다. PR 생성·브랜치 보호 설정은 이 게시 작업에 포함하지 않는다.

## 현재 게시 상태

- 원본 앱 작업 폴더는 상위 작업공간 Git의 미추적 파일이며, 이 상위 저장소에는 원격을 추가하지 않는다.
- 확인한 게시 대상은 공개 저장소이고 첫 게시 직전에는 커밋이 없는 상태다. 전용 게시 폴더에서 `main`을 만들고 `origin`을 `https://github.com/intel7-ai/manux.git`로 설정한다.
- 게시용 폴더에서는 앱을 저장소 루트에 놓는다. `src/`, `public/`, `docs/`, `package.json`이 루트 기준 경로가 된다.
- 팀원 GitHub 계정은 별도 확인한다. Jira 표시 이름으로 GitHub 계정을 추측하지 않는다.
- 라이선스는 팀이 아직 선택하지 않았다. 임의의 오픈소스 라이선스를 추가하거나 공개 재배포 권한을 주장하지 않는다.

## 게시에 포함할 것

| 포함 | 이유 |
| --- | --- |
| `src/`, `public/index.html`, `public/styles.css`, `public/assets/manux.svg` | 현재 프로토타입 소스와 앱 자산 |
| `server.js`, 실행 스크립트, `package.json`, `package-lock.json` | 같은 의존성과 실행 환경 재현 |
| `tests/*.test.js`, 숫자 fixture·사진 출처 JSON | 모델 다운로드·카메라 없이 기본 회귀 실행 |
| `scripts/*.mjs`, `scripts/*.ps1` | 설치·선택적 브라우저 시험·인수인계 재생성 |
| `AGENTS.md`, `CONTRIBUTING.md`, `.github/` | 담당 에이전트 지침, 리뷰 양식, 단위 CI |
| `docs/`의 Markdown·JSON, 최종 발표자료·선별한 발표 이미지 | 마스터 플랜, API 계약, 역할 지침, Jira 근거, 착수보고와 출처 연결 |
| `.handoff-manifest.json` | 게시 기준 파일 목록·크기·SHA-256 확인 |

`node_modules`, `.npm-cache`, `.tools`, `logs`, `test-results`, `.git`, 사용자 프로필과 원시 카메라 영상은 제외한다. 모델 `.task`, 데모 `.webm`/`.mp4`, 공식 사진 `.jpg`도 포함하지 않는다. 모델은 `npm run setup`, 사진은 `node scripts/setup-test-fixtures.mjs`, 데모는 [CONTRIBUTING](../CONTRIBUTING.md)의 `--make-demo` 절차로 준비한다. 사진은 공식 출처·해시가 있는 시험 자료지만, 이번 패키지는 원본 사진을 재배포하지 않는다.

발표에서 실제 사용한 화면·로고 등은 `docs/presentation/assets/`의 선별된 이미지와 출처 README에 한해 포함한다. 편집 가능한 PPTX, 발표 내용 JSON·대본, `docs/presentation/build-deck.mjs`도 포함한다. 발표 빌더는 Codex의 Artifact Tool 런타임을 필요로 하며 앱의 `npm ci` 의존성에 포함되지 않는다. 자세한 환경 변수와 사용법은 [발표자료 안내](presentation/README.md)를 따른다. 임시 빌드·렌더·QA 파일은 제외한다.

LAN 인증서·개인키와 실행 기록이 있는 `.local/`도 게시하지 않는다. LAN 실행·종료 스크립트와 [LAN 데모·아키텍처 안내](lan-demo.md)는 포함한다. 첫 게시본은 기존 프로토타입과 LAN 변경을 함께 담으며, 보고된 개인 등록 실패가 해결됐다는 의미는 아니다.

## 깨끗한 게시 폴더 만들기

앱 루트에서 다음처럼 실행한다. 대상 경로는 존재하지 않는 새 폴더여야 한다. 스크립트는 기존 폴더를 삭제하거나 덮어쓰지 않는다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/prepare-handoff.ps1 -Destination '<새-게시용-폴더-절대경로>'
```

복사할 확장자와 경로를 명시한 허용 목록만 사용한다. `docs/master-plan.md`, `docs/contracts.md`, 역할 문서 5개가 준비되지 않았다면 중단한다. 계정 토큰·개인 키로 보이는 패턴을 발견하면 파일 이름만 출력하고 복사를 시작하지 않는다. 이는 제한적인 검사이므로 게시 직전 사람이 diff와 포함 파일을 검토한다.

결과 `.handoff-manifest.json`은 **게시 준비 시점 파일의 무결성 기록**이다. 이후 팀의 새 변경으로 해시가 달라지는 것은 정상이며, 파일을 고칠 때마다 이 기준선 manifest를 갱신할 필요는 없다. 실제 공동 출발점은 첫 게시 커밋 SHA로 기록한다.

## 새 전용 저장소에 첫 게시

다음 명령은 **새로 생성한 빈 MANUX 전용 원격 저장소**에만 적용한다. 기존 저장소가 이미 있으면 다음 절차를 사용한다. 꺾쇠 괄호 값은 실제 경로와 URL로 바꾼다.

```powershell
Set-Location '<게시용-폴더>'
git init -b main
git add --all
git diff --cached --stat
git diff --cached --check
git status --short
git commit -m 'chore: prepare MANUX team baseline and kickoff plan'
git remote add origin <확인한-MANUX-저장소-URL>
git push -u origin main
git rev-parse HEAD
```

`git add --all`은 위 스크립트가 만든 전용 게시 폴더에서만 사용한다. 이 작업공간 전체에서 실행하면 다른 산출물까지 포함될 수 있다. 처음 push하기 전에 저장소 공개 범위와 파일 내용을 확인한다. 이 문서 자체는 push 실행 승인을 뜻하지 않는다.

## 기존 팀 저장소가 있는 경우

1. 해당 저장소를 별도 폴더에 clone하고 `git remote show origin`으로 기본 브랜치를 확인한다.
2. 기본 브랜치 최신 상태에서 `codex/manux-team-baseline` 브랜치를 만든다.
3. 게시 폴더와 기존 파일을 비교하여 필요한 파일만 추가한다. 기존 코드·설정·히스토리를 통째로 덮어쓰지 않는다.
4. 단위 회귀와 변경 검토 후 baseline PR을 만든다. 승인된 PR을 통합한 커밋을 팀의 출발점으로 공유한다.

기존 `main`/`master` 이름이나 브랜치 보호 설정을 임의로 변경하지 않는다. 초기 공유가 끝나면 팀원들은 [CONTRIBUTING](../CONTRIBUTING.md)의 절차로 본인 브랜치를 만든다.

## 브랜치·리뷰 연결

| Jira | 담당 | 작업 브랜치 | 리뷰어 |
| --- | --- | --- | --- |
| [SCRUM-6](https://se4nchoi.atlassian.net/browse/SCRUM-6) | 김동혁 | `codex/scrum-6-camera` | 라즈 |
| [SCRUM-7](https://se4nchoi.atlassian.net/browse/SCRUM-7) | 박현규 | `codex/scrum-7-control` | Sean Choi |
| [SCRUM-8](https://se4nchoi.atlassian.net/browse/SCRUM-8) | 라즈 | `codex/scrum-8-personalization` | 김동혁 |
| [SCRUM-9](https://se4nchoi.atlassian.net/browse/SCRUM-9) | Paul Dan | `codex/scrum-9-onboarding` | Sean Choi |
| [SCRUM-10](https://se4nchoi.atlassian.net/browse/SCRUM-10) | Sean Choi | `codex/scrum-10-integration` | 박현규 |

별도 장기 `develop` 브랜치를 필수로 추가하지 않는다. 기본 브랜치와 짧은 작업 PR로 시작하고 통합 책임자가 공통 계약 변경을 조율한다. 저장소 관리자는 기본 브랜치의 직접 push 방지, 최소 1명 리뷰, 실제 성공한 CI를 요구하는 보호 규칙을 설정한다. 해당 설정은 아직 적용하지 않았다.

## 첫 인수인계 때 확인할 내용

- 기준 커밋 SHA와 Jira 작업 링크를 전원이 공유했는가.
- 각 PC에서 `npm ci --ignore-scripts`, `npm test`, 모델 준비·서버 실행을 재현했는가.
- 현재 등록 실패를 해결된 기능으로 오해하지 않았는가.
- 본인 에이전트가 담당 파일, 제안 계약, 합성 시험과 실물 평가의 차이를 읽었는가.
- 단일 동작 등록과 임의 `rock` 동작·바인딩을 현재 기능과 구분했는가.
- 각자가 코드 변경·리뷰·실물 검증 근거를 남길 작업을 선택했는가.

마스터 플랜의 새 요구는 [Jira 변경 제안](jira-change-proposal.md)에서 기존 이슈와 연결한다. 현재 Jira 상태가 자동으로 바뀌는 것은 아니다.
