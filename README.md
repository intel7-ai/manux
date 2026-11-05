# MANUX

웹캠으로 손동작을 읽어 **Windows 마우스를 제어하는 팀 프로젝트**입니다. 영상과 손 추론은 사용하는 PC 안에서만 처리합니다. Discord에서 같은 프로젝트를 함께 개발할 수 있습니다.

## MANUX는 `apps/manux/`입니다 (2026-11-05)

앱 이름은 **MANUX**, 코드는 `apps/manux/`(v0.4, Python + Qt/QML) 한 곳입니다. 새 기능·수정·문서는 여기에 합니다. 나머지 폴더는 이전 버전입니다.

| 폴더 | 지위 | 시작 문서 |
| --- | --- | --- |
| `apps/manux/` | **MANUX.** 이식 단계 S0~S5 병합, S6(데스크톱 화면) 진행 중 | [apps/manux/README.md](apps/manux/README.md) · [AGENTS.md](apps/manux/AGENTS.md) |
| `hand-mouse/` | 이전 버전 v0.3. **`apps/manux`에 설치본이 생길 때까지만** Releases `v0.3.x` 설치파일을 이 폴더에서 빌드합니다. 설치본을 깨는 버그 수정만 받고 기능은 `apps/manux`로 옮깁니다 | [hand-mouse/README.md](hand-mouse/README.md) |
| `src/`, `public/`, `desktop/`, `server.js` | 이전 웹앱·Electron(v0.2). 회귀 시험(`npm run verify`)만 CI에서 돕니다 | 아래 [이전 버전](#이전-버전) |

문서 전체의 현재/기록 구분은 [docs/README.md](docs/README.md)에 있습니다.

## 무엇부터 하면 되나요?

| 하고 싶은 일 | 시작할 곳 |
| --- | --- |
| 코드를 몰라도 기능 제안·오류 보고·개발 요청하기 | 아래 **Discord로 참여하기** |
| 앱을 설치해서 써 보기 | 아래 **설치해서 사용하기** |
| 소스에서 실행하기 | 아래 **소스에서 실행하기** |
| 코드를 바꾸고 PR 올리기 | [브랜치 흐름](docs/branch-flow.md) · [개발 참여 안내](CONTRIBUTING.md) |
| MANUX 기능을 만들거나 고치기 | [apps/manux/AGENTS.md](apps/manux/AGENTS.md)부터 |

새 작업은 최신 `origin/preview`에서 작업별 브랜치를 만들고 `preview`로 PR을 엽니다. 이미 작업 중인 브랜치는 그대로 보존하고 그 브랜치에서 PR을 만듭니다.

## Discord로 참여하기

`1조-수화-pc제어-시스템` 채널에서 `@1조봇(Manux)`을 멘션하거나, 등록된 사용자는 `/ask`로 요청합니다. 둘 다 **같은 Claude 협업 대화**로 들어갑니다.

```text
@1조봇(Manux) [목표] 마우스 사용 중 손이 화면 밖으로 나가면 즉시 멈추게 해줘.
[현재 문제] 손을 내린 뒤에도 가끔 커서가 움직이는 것 같아.
[완료 확인] 손을 내렸을 때 이동과 눌린 버튼이 해제되는지 검사하고 결과를 알려줘.
```

전문 용어나 코드를 쓸 필요는 없습니다. **하려는 일·현재 문제·완료를 확인할 방법**을 알려주세요. 오류는 발생 순서와 오류 문구를 함께 보내면 재현하기 쉽습니다. 에이전트는 [공통 작업·결과 규칙](docs/harness.md)에 따라 요청을 정리하고 검증 결과를 보고합니다.

- Discord의 독립 작업은 최대 **5개**까지 별도 Git 작업 폴더에서 진행합니다. 각자 로컬 실행·검사를 마친 브랜치로 PR을 만듭니다. 개인 PC에서의 작업은 이 대기열을 사용하지 않습니다.
- “작업 완료”, “공유 코드에 통합”, “GitHub 게시”는 서로 다른 상태입니다. 결과 보고에서 확인하세요.
- 채널 대화는 팀의 공유 맥락입니다. 토큰·계정 비밀번호·개인 영상을 올리지 않습니다.
- Discord 요청은 **협업 호스트 PC**에서 실행됩니다. 팀원 PC의 앱을 원격 설치하거나 팀원 PC의 마우스를 자동 제어하는 연결은 아닙니다.

봇 운영자는 별도 봇 프로젝트의 `discord_bot.bat`를 실행하고 창을 유지해야 합니다. 팀원 모두가 봇을 설치할 필요는 없습니다. 응답이 없으면 호스트 운영자에게 실행 상태를 확인해 달라고 알려주세요.

## 설치해서 사용하기

[GitHub Releases](https://github.com/intel7-ai/manux/releases)의 최신 버전에서 `MANUX-win-Setup.exe`를 받아 설치합니다. Python·Node·Git은 필요 없습니다. 앱은 시작할 때 새 버전을 확인해 내려받고, 앱을 닫으면 적용됩니다. 사용자 설정은 `%APPDATA%\MANUX`에 있어 업데이트해도 유지됩니다.

지금 게시된 `v0.3.x`는 이전 버전(`hand-mouse/`) 코드입니다. `apps/manux`의 설치본·자동 업데이트(S6 남은 항목)가 준비되면 같은 Releases·같은 이름으로 교체합니다. 상세: [설치본과 자동 업데이트](hand-mouse/docs/packaging.md).

## 소스에서 실행하기

준비물: Windows, [uv](https://docs.astral.sh/uv/), 웹캠.

```powershell
cd apps/manux
uv run --python 3.12 --extra ui --extra vision python -m manux --hand-model ..\..\hand-mouse\models\hand_landmarker.task
```

웹캠 없이 녹화 재생, 헤드리스 실행기, 테스터 데이터 수집 도구는 [apps/manux/README.md](apps/manux/README.md)에 있습니다. 시험은 `uv run --python 3.12 pytest -q`입니다.

이전 버전 v0.3을 소스로 실행하려면 `cd hand-mouse` 후 `uv run --locked --extra qt python main.py --ui qt --source 0` ([hand-mouse/README.md](hand-mouse/README.md)).

## 현재 구현과 확인하지 않은 것

| 범위 | 상태 |
| --- | --- |
| MANUX 설정·카메라·손 인식·기능(포인터·핀치·모션)·엔진·Windows 출력·TCN 확인기 | `apps/manux/`에 병합. 실제 웹캠·실제 Windows 입력·Qt 화면·인식률은 확인하지 않음 |
| MANUX 데스크톱 화면 (S6) | 진행 중. 남은 것: 트래킹 측정, 학습 센터 화면, 설치본·자동 업데이트 |
| 사용자 정의 손동작 등록과 원하는 액션 연결 | 후속 과제 |
| 이전 버전 v0.3 (현재 설치파일) | 커서·클릭·드래그·스크롤, 트래킹 측정, 실패 기록, 여러 모니터, 트레이. 실제 손 확인은 개발자 몇 명의 PC·웹캠에 한정 |

MediaPipe 손 추적 모델을 재학습하지 않습니다. 자동 시험 통과는 실사용 인식률이나 사용성을 뜻하지 않습니다. 실제 사용자 평가는 [M1 평가 자료](docs/m1-webcam-mouse/README.md)의 절차를 따릅니다.

## 검사와 CI

| 대상 | 로컬 명령 | CI |
| --- | --- | --- |
| MANUX (`apps/manux/`) | `cd apps/manux` 후 `uv run --python 3.12 pytest -q` | MANUX CI의 구조·계약 시험. 모든 PR |
| 이전 v0.3 (`hand-mouse/`) | `cd hand-mouse` 후 `uv run --locked --extra qt python -m pytest -q` | `hand-mouse (Windows)` 시험과 설치파일 빌드. 이 폴더 변경 시 |
| 이전 웹앱 | `npm ci --ignore-scripts` 후 `npm run verify` | MANUX CI `verify`. preview는 Ubuntu만, production은 Windows·브라우저·Electron 시험 설치본까지 |

필수 검사 이름은 **MANUX required**입니다. `preview` PR은 이 검사만 통과하면 작성자가 병합합니다(리뷰어 승인 없음). preview에서 시험한 뒤 `preview → production` PR로 병합하면 설치본이 GitHub Release로 게시됩니다(지금은 `hand-mouse-release.yml`, `apps/manux` 설치본이 생기면 교체). 상세: [브랜치 흐름](docs/branch-flow.md).

## 이전 버전

- **v0.3 (`hand-mouse/`)**: Dan의 Python 손 마우스와 Qt 화면. 기능은 `apps/manux`로 옮겼거나 옮기는 중이며([이식 기록](https://se4nchoi.atlassian.net/browse/SCRUM-118)), 문서는 [docs/README.md](docs/README.md)의 v0.3 절에 있습니다.
- **v0.2 웹앱·Electron (`src/`·`desktop/`)**: 2026-11-04 전까지의 브라우저 영상 제어·에어 마우스·Electron 앱입니다. Electron 정식 Release는 게시된 적이 없고, 게시 workflow는 2026-11-05에 삭제했습니다.

v0.2 웹앱:

- 실행: Node.js 24에서 `npm ci --ignore-scripts`, `npm run setup`, `npm run desktop:setup` 후 `npm run desktop` (루트의 `MANUX 개발 준비.cmd`, `MANUX 마우스 실행.cmd`, `MANUX 검사.cmd`도 이 앱용입니다)
- 브라우저만: `npm start` 후 <http://127.0.0.1:4173>
- 문서: [온보딩](docs/onboarding.md), [에어 마우스](docs/air-mouse.md), [영상 사용법](docs/usage-video.md), [Windows 입력 연결](desktop/README.md), [모듈 계약](docs/contracts.md), [설계 문서](docs/design/README.md)

## 저장소 규칙

| 문서 | 용도 |
| --- | --- |
| [AGENTS.md](AGENTS.md) | 사람·에이전트 공통 작업 규칙 (`CLAUDE.md`에서 참조) |
| [docs/branch-flow.md](docs/branch-flow.md) | 작업 브랜치 → preview → production |
| [docs/harness.md](docs/harness.md) | 작업·결과 보고 형식 |
| [docs/README.md](docs/README.md) | 문서 목록과 현재/기록 구분 |

소스 저장소: [intel7-ai/manux](https://github.com/intel7-ai/manux). 손 모델 파일은 지금 `hand-mouse/models/`에 있고(`apps/manux`도 이 경로를 씀), 웹앱 모델 출처는 `public/models/model-source.json`에 기록합니다.
