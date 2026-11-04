# 설치본과 자동 업데이트 (2026-11-04)

설치되는 MANUX 앱은 이 Python 손 마우스 앱이다. Electron 앱(`desktop/`)은 배포 대상이 아니다.

## 구성

| 부분 | 파일 | 하는 일 |
| --- | --- | --- |
| 진입점 | `manux_app.py` | Velopack 설치·업데이트 훅을 먼저 처리하고, `--ui`가 없으면 Qt 화면으로 `main.py`를 실행한다 |
| 경로 | `src/app_paths.py` | 소스 실행은 지금처럼 `hand-mouse/`를 쓴다. 설치본은 사용자 파일을 `%APPDATA%\MANUX`에 둔다 |
| 업데이트 | `src/updater.py` | 시작할 때 한 번 GitHub 릴리스를 확인하고 백그라운드로 내려받는다. 앱을 닫으면 적용되고 다음 실행이 새 버전이다. 실행 중에는 끊지 않는다 |
| 빌드 | `packaging/manux.spec`, `packaging/build.ps1` | PyInstaller 한 폴더 빌드 → `vpk pack`으로 `MANUX-win-Setup.exe`와 업데이트 패키지 |
| 배포 | `.github/workflows/hand-mouse-release.yml` | PR: 설치파일을 artifact로만 올린다. production 병합: 같은 빌드를 GitHub 릴리스 `v<버전>`으로 게시한다 |

## 사용자 파일 (`%APPDATA%\MANUX`)

설치 폴더(`%LocalAppData%\MANUX\current`)는 업데이트마다 바뀐다. 그래서 아래는 설치 폴더 밖에 둔다.

- `config/gestures.json`: 첫 실행에 설치본의 기본값을 복사한다. 업데이트가 새 기본값을 가져오면, 사용자가 그 사이 고치지 않은 경우에만 새 것으로 바꾼다. 고쳤으면 사용자 것을 그대로 둔다.
- `config/profiles/`: 보정 프로필
- `logs/`: 실패 기록, 트래킹 측정, 성능 로그
- `certs/`: 스마트폰 카메라용 자체 서명 인증서

## 버전

`pyproject.toml`의 `major.minor`에 workflow 실행 번호를 붙인다(예: `0.3.57`). PR 빌드는 `0.3.57-pr40`처럼 붙여 정식 업데이트와 섞이지 않는다.

## 로컬 빌드

```powershell
cd hand-mouse
uv sync --locked --extra qt --extra package
./packaging/build.ps1 -Version 0.3.0 -SkipPack   # build/dist/MANUX/MANUX.exe
```

설치파일까지 만들려면 .NET SDK와 `dotnet tool install -g vpk --version 1.2.161`이 필요하다. CI(windows-latest)에는 .NET이 있다.

## 한계

- 코드 서명이 없다. 처음 설치할 때 Windows SmartScreen 경고가 뜰 수 있다.
- 설치본 크기는 약 440MB(MediaPipe·Qt·OpenCV 포함)다. 두 번째 업데이트부터는 바뀐 부분(delta)만 받는다.
- 업데이트 확인은 GitHub API의 비로그인 한도(시간당 60회/IP)를 쓴다. 실패하면 다음 실행에 다시 확인한다.
