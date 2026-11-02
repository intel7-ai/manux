# 데스크톱 앱 패키징·배포·자동 업데이트

각자 PC에서 개발 환경 없이 최신 MANUX를 설치해 확인하기 위한 Windows 설치본 경로다. **서명되지 않은 빌드**이며, 실제 웹캠·실제 OS 클릭·실제 업데이트 설치는 이 문서의 자동 검사로 확인하지 않았다.

## 구성

| 구분 | 내용 |
| --- | --- |
| 패키징 | `electron-builder` 26.18.0, 설정은 `electron-builder.yml` (x64 NSIS, 사용자 단위 설치 `oneClick`) |
| 업데이트 | `electron-updater` 6.8.11, GitHub Releases(`intel7-ai/manux`), 정책은 `desktop/updater.mjs` |
| 릴리스 | `.github/workflows/release.yml`: `v*` 태그 푸시 → 검증 → 빌드 → **초안(draft) 릴리스** 업로드 |
| 대상 | Windows x64만. 다른 OS는 지원하지 않는다. |

`asar`는 껐다(`asar: false`). 마우스 PowerShell 도우미(`desktop/windows-mouse.ps1`)는 외부 프로세스가 읽는 실제 파일이어야 하고, 서버(`server.js`)가 `src/`, `public/`, `node_modules`를 파일 경로로 서비스하기 때문이다. 대가로 설치 폴더의 앱 파일을 사용자가 수정할 수 있고 코드 위변조 방지 효과는 없다.

## 로컬 빌드와 확인

```powershell
npm ci --ignore-scripts
npm run desktop:pack      # dist/win-unpacked/MANUX.exe (설치 없이 실행 가능)
npm run test:packaged     # 패키지된 앱 시작 검사 (dry-run, 실제 마우스 입력 없음)
npm run desktop:dist      # dist/MANUX-Setup-<버전>.exe + latest.yml (업로드 없음)
```

- 첫 빌드는 Electron(약 130 MB), NSIS 도구, 제스처 모델(8.4 MB, `npm run setup`)을 내려받는다. 모델은 `public/models/model-source.json`의 SHA-256과 다르면 빌드를 중단한다.
- `dist/`는 Git에 올리지 않는다.
- `npm run test:packaged`는 창 생성, 로컬 서버(`/health`, 화면, MediaPipe wasm, 모델 해시), 패키지 안의 도우미 시작(dry-run), 마우스 제어 기본 꺼짐, 렌더러의 Node 접근 차단, 업데이트 API 범위를 확인한다. 설치 파일을 설치하거나 업데이트를 내려받지는 않는다.

## 자동 업데이트 동작

- **패키지된 Windows 실행에서만** 켜진다. `npm run desktop`, 시험, 다른 OS에서는 꺼진다. `MANUX_AUTO_UPDATE=0`으로 끌 수 있다.
- 시작 20초 뒤 한 번, 이후 6시간마다 GitHub의 **게시된(draft 아님)** 최신 릴리스를 확인한다. 새 버전은 내려받기만 하고 `autoInstallOnAppQuit`을 꺼서 종료 시 몰래 설치하지 않는다. 다운그레이드·사전 릴리스는 허용하지 않는다.
- 내려받기가 끝나면 설치 여부를 묻는 창이 뜬다("지금 설치"/"나중에"). **승인 없이 설치하거나 재시작하지 않는다.** 창 제목에도 "업데이트 x.y.z 준비됨"이 표시된다.
- **마우스 제어가 켜져 있으면** 설치를 거절하고, 확인 창도 제어가 꺼진 뒤에 띄운다. 재시작한 앱은 항상 마우스 제어가 꺼진 상태로 시작한다(기존 계약).
- 확인 실패(오프라인, 404, 서명·해시 불일치)는 앱 사용을 막지 않고 `getUpdateStatus()`의 `error`로만 나타난다.
- 렌더러에는 `getUpdateStatus`, `checkForUpdates`, `installUpdate`, `onUpdateStatus`만 추가했다. URL·경로·채널을 받지 않으며 main에서 기존과 같은 송신자·origin 검사를 한다.

## 릴리스하는 방법 (사람이 수행)

1. `package.json`의 `version`을 올려 PR로 병합한다(예: `0.3.0`).
2. 병합된 커밋에 태그를 만들어 푸시한다: `git tag v0.3.0` → `git push origin v0.3.0`. 태그는 `v` + `package.json` 버전과 같아야 한다.
3. Actions의 "MANUX desktop release"가 `npm run verify`, 패키지 시작 검사, 빌드를 거쳐 **초안 릴리스**에 `MANUX-Setup-0.3.0.exe`, `.blockmap`, `latest.yml`을 올린다.
4. 사람이 초안을 확인하고 **Publish release**를 누르면 그때부터 설치된 앱이 업데이트를 발견한다. 즉시 공개하려면 `electron-builder.yml`의 `releaseType`을 `release`로 바꾼다.

이 workflow는 아직 한 번도 실행하지 않았다. 태그를 푸시하는 첫 릴리스에서 처음 동작을 확인하게 된다.

## 알아둘 한계

- **코드 서명이 없다.** 처음 설치할 때 Windows SmartScreen이 "알 수 없는 게시자" 경고를 보일 수 있고, 업데이트도 서명 대신 `latest.yml`의 SHA-512로만 무결성을 확인한다. 릴리스 계정이나 저장소가 침해되면 악성 설치본이 배포될 수 있다. 신뢰할 수 있는 배포가 필요하면 서명 인증서가 필요하다(이 저장소에는 인증서를 넣지 않는다).
- 저장소는 확인 시점(2026-11-02, `gh repo view`)에 **공개(PUBLIC)** 였다. 공개 저장소의 게시된 릴리스는 토큰 없이 업데이트할 수 있다. 비공개로 바꾸면 업데이트 확인이 실패하며, 팀원 PC에 토큰을 넣는 방식은 쓰지 않는다.
- 업데이트 롤백 기능은 없다. 문제가 생기면 이전 버전 설치 파일을 직접 설치해야 하고, 다운그레이드는 자동으로 허용되지 않는다.
- 프로필은 앱 origin(`http://127.0.0.1:4175`)별 localStorage에 있다. 설치본을 새 포트나 다른 사용자 계정에서 실행하면 개발 실행과 프로필이 공유되지 않는다.
- 아이콘은 기본 Electron 아이콘이다. 서명을 끄면서 실행 파일의 아이콘·버전 메타데이터 편집도 건너뛰었다.
- 설치 프로그램 실행, 설치본에서의 실제 웹캠·마우스 동작, 실제 업데이트 다운로드·설치는 사람이 별도로 확인해야 한다.
- `npm audit`는 개발 의존성에서 moderate 15건을 보고한다(배포 앱에 들어가는 의존성은 0건). 이번 작업에서 처리하지 않았다.
