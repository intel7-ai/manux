# 팀 개발 시작 안내

[마스터 플랜](docs/master-plan.md)과 본인 [작업 문서](docs/workstreams/)를 먼저 읽는다. 현재 소스는 공동 프로토타입이다. 여러 동작을 한꺼번에 등록하는 부담과 실제 등록 실패가 보고됐으며, 개인별 개선·검증이 남아 있다.

## 1. 설치와 실행

기준 개발 환경은 Windows, Chrome, Node.js 24 계열이다. 준비 시점 개발 PC의 Node 버전은 `v24.22.1`이다. 다른 버전의 동작을 보장하지 않으므로 `node --version`을 결과에 기록한다. 아래 명령은 `package.json`이 있는 저장소 루트에서 실행한다.

```powershell
node --version
npm ci --ignore-scripts
npm test
npm run setup
npm start
```

`http://127.0.0.1:4173`에서 본인 영상 파일을 열어 사용한다. `npm run setup`은 공식 모델을 내려받으므로 최초에는 네트워크가 필요하다. 패키지와 모델 준비 후 앱 추론은 로컬에서 실행된다. `npm start`로 띄운 서버는 해당 터미널에서 Ctrl+C로 종료한다. 다른 사람의 Node/Chrome 프로세스를 일괄 종료하지 않는다.

소스 패키지에는 모델 바이너리, 사진 fixture, 90초 데모 영상이 없다. 앱은 본인 로컬 영상으로 시험할 수 있다. 90초 데모 버튼을 쓰려면 아래 브라우저 시험 준비와 생성 절차를 수행한다.

## 2. 자기 브랜치에서 시작

첫 게시 후 팀이 공유하는 저장소 URL을 사용한다. 원격 기본 브랜치는 `main`이라고 추측하지 말고 확인한다.

```powershell
git clone <팀-저장소-URL>
cd <복제한-폴더>
git remote show origin
git status --short
git switch <확인한-기본-브랜치>
git pull --ff-only
git switch -c codex/scrum-6-camera
```

마지막 줄은 본인 역할의 브랜치 이름으로 바꾼다. 이미 존재하는 본인 브랜치라면 `git switch <브랜치>`를 사용한다. 미커밋 작업이 있으면 먼저 내용을 확인하고 브랜치 전환으로 덮어쓰지 않는다. 복제 직후 기준 커밋 `git rev-parse HEAD`를 작업 기록에 남긴다.

에이전트는 **본인 복제 폴더**에서 열고 아래 지시와 본인 작업 문서를 전달한다.

> 이 저장소의 AGENTS.md, docs/master-plan.md, docs/contracts.md와 내 역할 작업 문서를 읽어라. 나는 [이름 / R번호 / Jira 키]이고 현재 [브랜치]에서 작업한다. 먼저 현재 구현과 미구현 제안, 등록 실패의 확인된 사실과 가설을 구분해라. 담당 파일의 실패 사례 하나를 재현하고 작은 변경으로 해결한 뒤 대응 시험과 증거를 남겨라. 다른 역할의 파일과 app.js 연결 변경은 계약과 최소 diff로 조정해라. 실제 측정하지 않은 성능과 Jira 완료를 주장하지 마라.

## 3. 테스트 수준

| 수준 | 명령·방법 | 증명하는 범위 |
| --- | --- | --- |
| 단위·서버 회귀 | `npm test` | 입력 시퀀스·모델/프로필 경계·HTTP 서버 동작 |
| 실제 모델 + 합성 카메라 | `node scripts/test-browser.mjs` | 모델 연결, 미디어 제어, 기본 카메라 생명주기 |
| 합성 관절 전체 등록 | `node scripts/test-enrollment-browser.mjs` | 각도·등록·저장·명령 연결. 실제 추론의 정확도는 대체 입력으로 평가하지 않음 |
| 실제 사용자 | 별도 평가 세션과 실제 웹캠 | 등록 성공/실패, 거리·조명·사용자별 성공률·오실행·전체 반응 시간 |

PR에서는 변경에 해당하는 검사를 먼저 실행하고, 통합 전에 전체 단위 회귀를 확인한다. 브라우저 시험과 실물 시험은 해당 변경이 영향을 주는 경우 실행한다. 합성 입력의 통과와 사용자의 등록 실패 보고가 동시에 존재할 수 있다.

## 4. 선택적 브라우저 시험 준비

기존 스크립트는 설치된 Chrome의 `channel: 'chrome'`을 사용한다. Chrome 설치가 필요하다. Playwright 기본 경로는 최초 개발 환경 전용이므로 각 PC에서는 명시적으로 `PLAYWRIGHT_MODULE`을 지정한다. 준비 시점 번들 버전은 `1.62.1`이었다.

```powershell
npm install --prefix .tools --no-save --package-lock=false playwright@1.62.1
$env:PLAYWRIGHT_MODULE = (Resolve-Path '.tools/node_modules/playwright/index.mjs').Path
node scripts/setup-test-fixtures.mjs
New-Item -ItemType Directory -Path public/media -Force | Out-Null
node --max-old-space-size=512 scripts/test-browser.mjs --make-demo
node --max-old-space-size=512 scripts/test-enrollment-browser.mjs
```

사진은 `tests/fixtures/sources.json`에 기록한 공식 URL에서 받아 SHA-256을 비교한다. 파일이 이미 있으면 다시 다운로드하지 않고 먼저 해시를 확인한다. 위 첫 브라우저 시험은 90초 데모를 생성한 뒤 시험한다. 후속 실행에는 `--make-demo`를 생략할 수 있다. 브라우저 검사를 동시에 여러 개 실행하지 않는다.

`scripts/probe-personalization.mjs`는 숫자 fixture를 **재생성하여 덮어쓰는** 조사 도구다. 평소 테스트 준비에서 실행하지 않는다. R3이 데이터 변경 근거·모델 버전·출처와 변경 diff를 검토할 때만 사용한다. 사진과 모델은 저장소의 공개 배포 대상으로 자동 추가하지 않는다.

## 5. PR과 리뷰

- 한 PR은 재현 가능한 문제 또는 합의한 작은 기능 단위로 만든다.
- 제목에 Jira 키를 넣고, 본문에 문제·변경·검증·한계를 기록한다.
- 작성자 외 지정 리뷰어가 실제 diff와 재현 결과를 확인한다.
- 기본 브랜치에 직접 push하지 않는다. 보호 규칙은 저장소 관리자 설정이 필요하며 현재 준비 파일만으로 활성화되지 않는다.
- 충돌은 본인 브랜치에서 해결하고 필요한 회귀를 다시 확인한다. 다른 사람의 브랜치를 강제 push하거나 재작성하지 않는다.
- 코드·시험·실물 결과·리뷰 링크를 Jira에 모으고 완료 기준을 실제로 충족했을 때 상태를 바꾼다.

CI 준비 파일은 Windows와 Node 24에서 `npm ci --ignore-scripts`, `npm test`만 실행한다. 아직 원격에서 실행된 결과는 없으며 카메라·모델 정확도·사용성 시험을 대신하지 않는다.
