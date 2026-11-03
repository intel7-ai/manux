# Windows 앱 배포와 자동 업데이트

개인 PC의 AI와 Discord worker가 각각 작업 브랜치에서 개발하고 PR로 제출한다. `production`은 검사와 리뷰를 통과한 코드만 받으며, 병합 뒤 Windows 설치본과 업데이트를 자동 게시한다.

## 팀원이 사용하는 흐름

1. 최신 `origin/preview`에서 `codex/scrum-23-gesture-enrollment` 같은 작업 브랜치를 만든다. 이미 진행 중인 브랜치는 그 자리에서 계속 작업한다.
2. `npm run desktop`으로 직접 확인하고 관련 검사를 실행한다. 다른 사람의 checkout이나 공유 Claude의 코드 통합을 기다리지 않는다.
3. 브랜치를 푸시하고 `preview` 대상 PR을 만든다. PR에는 목적·변경·검증·남은 한계를 적는다.
4. PR의 `MANUX CI`가 Windows/Linux, 합성 브라우저, Windows helper, 실제 패키지 실행·업데이트 다운로드·설치본 교체를 검사한다. `MANUX-preview-...` artifact에는 시험용 설치파일이 있다.
5. `MANUX required`가 통과하면 작성자가 preview에 병합한다(리뷰어 승인 없음). preview push의 artifact 설치파일로 실제 앱을 시험한다.
6. preview에서 시험한 뒤 `preview → production` PR을 병합한다([브랜치 흐름](branch-flow.md)). `production`에 병합되면 `MANUX production release`가 검증·빌드하고 [GitHub Releases](https://github.com/intel7-ai/manux/releases)에 정식 버전을 게시한다.

검사 실패, 승인 대기, 머지 완료, 정식 Release 게시는 다른 상태다. 첫 Release가 게시되기 전에는 설치된 사용자 앱으로 전달할 정식 업데이트가 없다.

## 정식 앱과 PR 시험용 앱

| 항목 | 정식 앱 | PR 시험용 앱 |
| --- | --- | --- |
| 표시 이름 | MANUX | MANUX Preview |
| 설치 ID | `ai.intel7.manux` | `ai.intel7.manux.preview` |
| 기본 포트 | 4175 | 4176 |
| 설정 저장 | 정식 앱의 기존 userData/localStorage | 별도의 MANUX Preview userData/localStorage |
| 자동 업데이트 | 게시된 정식 GitHub Release | 꺼짐 |
| 받는 곳 | GitHub Releases | 해당 PR의 Actions artifact |

두 앱의 설치 ID와 설정 폴더를 분리해서 PR 설치본이 정식 앱을 덮어쓰지 않게 한다. 모델·WASM·Electron·Windows 입력 도우미를 패키지에 포함하므로 사용하는 PC에 Node/npm을 설치할 필요가 없다. 시험용 앱끼리는 같은 Preview 설치를 교체한다. 두 앱이 동시에 실행되면 전역 정지 단축키를 먼저 등록한 앱만 실제 마우스 제어를 사용할 수 있다.

## 빌드·검증 명령

```powershell
npm ci --ignore-scripts
npm run desktop:setup
npm run verify
npm run desktop:dist
npm run test:packaged
npm run test:update-download
```

로컬 시험용 앱은 빌드 전에 `$env:MANUX_BUILD_CHANNEL = 'preview'`를 설정한다. 기본은 정식 앱 형태이며 위 명령은 파일을 업로드하거나 설치하지 않는다. `dist/`는 Git 제외 대상이다. CI가 버전을 부여하며 로컬 빌드는 package.json의 버전을 사용한다.

- `desktop:dist`: Windows x64 사용자 단위 NSIS 설치파일과 업데이트 메타데이터 생성.
- `test:packaged`: 실제 unpacked 앱을 별도 프로필로 열어 모델·WASM·helper(dry-run), IPC 범위, 메뉴, 재실행 후 설정 보존을 확인한다.
- `test:update-download`: 실제 electron-updater가 루프백 서버의 설치파일을 다운로드하고 해시를 검증하며, 잘못된 해시를 거부하는지 확인한다. 이전 버전은 시험 adapter로 모사하며 실제 설치·교체는 하지 않는다.
- `test:installed-update`: 일회용 GitHub-hosted Windows VM에서만 실행한다. Preview를 이전 버전 번호로 재패키징해 실제 설치한 뒤, 기본 ElectronAppAdapter로 업데이트를 다운로드하고 NSIS로 교체한다. 같은 프로필의 설정 보존과 재시작 후 마우스 제어 꺼짐을 확인한다. 개인 PC나 self-hosted runner에서는 실행을 거부한다.
- 실제 웹캠 인식·OS 클릭, 과거 출시 버전에서의 데이터 이전, 공개 Release를 통한 정식 앱 업데이트는 별도 Windows 인수 시험이다. CI의 Preview 설치 시험과 구분해서 PR에 기록한다.

`asar: false`로 패키징하여 PowerShell helper와 로컬 파일 서버가 필요한 파일을 실제 경로로 읽는다. 모델은 저장된 SHA-256·크기와 일치해야 빌드된다. 빌드 도구는 고정 버전 electron-builder 26.18.0, 런타임 업데이트는 electron-updater 6.8.11이다.

## 자동 배포의 경계

PR job은 읽기 권한만 가진다. 설치파일을 만들지만 정식 Release를 게시하지 않는다. 태그를 푸시하는 것만으로 배포되지 않는다.

정식 workflow는 `production` push에서 동작한다. 읽기 권한의 build job이 테스트와 패키징을 완료한 다음, 별도의 publish job이 그 **동일 artifact**의 SHA-256·업데이트 SHA-512·원본 커밋을 확인해 게시한다. publish job만 `contents: write`를 가지며 GitHub Actions의 기본 `GITHUB_TOKEN`을 사용한다. 사용자 앱에 토큰을 넣지 않는다.

정식 빌드 버전은 package.json의 major/minor를 유지하고 patch에 workflow run number를 더한다. 소스 버전을 매번 커밋하지 않아도 각 배포의 버전이 증가한다. PR은 `-preview.<PR>.<run>` 버전이다. 각 정식 태그는 해당 production 커밋을 가리키고 설치파일·blockmap·latest.yml·release-manifest.json을 Release에 함께 게시한다.

부분 업로드는 draft 상태로 남아 사용자 앱에 노출되지 않는다. 기존 태그나 게시된 파일을 덮어쓰지 않는다. 실패 원인을 해결한 PR을 production에 병합하여 새 workflow 실행·새 버전을 만든다. 이미 게시된 버전보다 낮은 버전은 거부한다. 이전 정상 코드로 복구할 때도 새 버전으로 배포한다.

## 앱에서 업데이트 적용

패키지된 정식 Windows 앱은 시작 20초 뒤, 이후 6시간마다 업데이트를 확인한다. 메뉴의 **MANUX → 업데이트 확인**으로도 확인할 수 있다. 새 버전은 자동 다운로드하고, 준비되면 **지금 설치 / 나중에**를 선택한다. 나중에 설치하려면 메뉴의 **준비된 업데이트 설치**를 사용한다.

마우스 제어 중에는 설치·재시작하지 않는다. 설치 후 재실행해도 제어는 기본 꺼짐이다. 같은 앱 ID·userData·포트를 유지하여 커스텀 설정을 보존한다. 오프라인이나 업데이트 서버 오류는 앱 사용을 막지 않으며 이미 다운로드한 설치본도 유지한다. 개발 실행과 Preview는 정식 자동 업데이트를 사용하지 않는다.

현재 빌드는 코드 서명 인증서가 없어 Windows에서 알 수 없는 게시자 경고가 나올 수 있다. 공개 `intel7-ai/manux`의 Release를 토큰 없이 받는다. 저장소를 비공개로 바꾸려면 별도의 공개 배포 위치를 마련해야 한다. 코드 서명은 인증서를 저장소에 넣지 않고 CI 비밀 설정으로 추가할 수 있다.
