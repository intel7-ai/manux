# MANUX

웹캠으로 손동작을 읽어 **앱 안의 영상과 Windows 마우스를 제어하는 팀 프로젝트**입니다. 영상과 손 추론은 사용하는 PC에서 처리합니다. Discord에서는 같은 프로젝트를 함께 개발할 수 있습니다.

M1 제품 목표는 **Windows 데스크톱 앱, 비전 화면의 기본 마우스 프리셋, 사용자 정의 손동작과 컴퓨터 조작의 연결**입니다. 한 손·두 손 조작의 최종 선택은 평가 후 결정합니다. [확인된 목표와 현재 구현](docs/design/01-use-cases.md)을 함께 확인하세요.

## 무엇부터 하면 되나요?

| 하고 싶은 일 | 시작할 곳 |
| --- | --- |
| 코드를 몰라도 기능 제안·오류 보고·개발 요청하기 | 아래 **Discord로 참여하기** |
| 내 PC에서 앱을 실행하고 사용해 보기 | 아래 **Windows에서 처음 실행하기** |
| 코드를 바꾸고 검사·리뷰받기 | [개발 참여 안내](CONTRIBUTING.md) |
| 설치 오류나 빨간 검사 결과 해결하기 | [첫날 온보딩과 문제 해결](docs/onboarding.md) |

새 작업은 최신 `origin/preview`에서 작업별 브랜치를 만듭니다. 개인 PC의 원하는 AI와 Discord worker 모두 같은 PR 검증·리뷰·배포 흐름을 사용합니다. 이미 작업 중인 브랜치는 그대로 보존하고 해당 브랜치에서 PR을 만듭니다.

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

## 설치파일로 사용하기

[GitHub Releases](https://github.com/intel7-ai/manux/releases)의 정식 `MANUX-Setup-<버전>.exe`를 설치합니다. 모델과 실행 환경을 포함하므로 Node·Git은 필요 없습니다. 첫 정식 Release가 게시되기 전에는 아래 개발 실행을 사용합니다. 이후 앱이 새 정식 버전을 내려받고 사용자가 설치를 선택하면 재시작합니다.

PR 검토용 `MANUX Preview` 설치파일은 해당 PR의 Actions artifact에서 받습니다. 정식 앱과 설치 ID·설정 폴더·포트가 다르며 자동 업데이트는 꺼져 있습니다. [설치·PR 시험·자동 배포 상세](docs/desktop-release.md)

## Windows 개발 환경에서 실행하기

준비물은 **Windows, Node.js 24, Git for Windows, 웹캠**, 그리고 팀이 공유한 이 프로젝트 폴더입니다. 명령은 `package.json`이 있는 `manux` 폴더에서 실행합니다.

1. **`MANUX 개발 준비.cmd`**를 더블클릭합니다. 패키지를 설치하고 환경·공통 검사를 실행합니다.
2. 같은 폴더의 PowerShell에서 최초 한 번 다음 두 명령을 실행합니다. 다운로드 때문에 인터넷이 필요합니다.

   ```powershell
   npm run setup
   npm run desktop:setup
   ```

3. **`MANUX 마우스 실행.cmd`**를 더블클릭합니다.
4. 카메라를 켜고 **이 화면에서 연습 → 마우스 제어 켜기**부터 사용합니다. 현재 브랜치의 [마우스 사용법](docs/air-mouse.md)에 있는 기본 자세와 클릭 동작을 확인한 뒤 손 전체를 움직입니다.
5. 실제 커서를 제어하려면 데스크톱 앱에서 **Windows 마우스**를 직접 선택하고 제어를 다시 켭니다. **Ctrl + Alt + M**으로 다른 앱에서도 제어를 멈출 수 있습니다.

다음부터는 `MANUX 마우스 실행.cmd`로 시작합니다. 코드나 의존성이 업데이트되면 다시 개발 준비·검사를 실행합니다. [자세한 마우스 사용법](docs/air-mouse.md)에서 클릭·드래그·스크롤 동작과 제한을 확인하세요.

영상 모드나 브라우저 연습만 사용하려면 모델 준비 후 `npm start`를 실행하고 <http://127.0.0.1:4173>을 엽니다. 브라우저에서는 실제 Windows 커서에 명령을 보내지 않습니다. [영상·개인 등록 상세 사용법](docs/usage-video.md)

### Python 손 마우스 앱 (`hand-mouse/`)

Dan의 Python 가상 마우스(SCRUM-32·33·43~61)와 Qt 대시보드·검지 포인터 모드가 `hand-mouse/`에 있습니다. Electron 앱과 코드를 공유하지 않으며 설치파일·`npm run verify`·소스 패키지에 포함되지 않습니다. 실행 방법은 [hand-mouse/README.md](hand-mouse/README.md)를 따릅니다(예: `hand-mouse` 폴더에서 `uv run --locked --extra qt python main.py --ui qt --source 0`).

원본 브랜치는 `manux-python-ui`(Qt 작업)와 `hand-mouse-gesture-overhaul`(Dan)입니다. 원본의 새 커밋은 `git merge -X subtree=hand-mouse/ origin/manux-python-ui`로 가져옵니다.

## 현재 구현과 남은 일

| 기능 | 현재 상태 |
| --- | --- |
| 영상 재생·정지·앞/뒤 이동 | 브라우저와 앱 안의 로컬 영상 제어 구현 |
| 손 전체 이동·클릭·드래그·우클릭·스크롤 | 연습 화면과 Electron/Windows 입력 경로 구현. 실제 제어는 기본 꺼짐 |
| 사용자·책상/소파별 개인 등록 | 코드가 있으나 일괄 등록 부담과 실제 등록 실패가 보고됨 |
| 원하는 동작 하나만 등록, 임의 손 모양·액션 연결 | 후속 개발 과제 |
| 실물 웹캠 사용 편의성·거리별 성능 | 별도 사용자 평가 필요. 자동 검사 통과로 보장하지 않음 |

MediaPipe 손 추적 모델을 재학습하는 방식은 아닙니다. 개인 등록은 관절 특징 예시와 판정 기준을 저장합니다. 현재 동작의 자세한 계약은 [에어 마우스](docs/air-mouse.md)와 [Windows 입력 연결](desktop/README.md)을 먼저 확인하세요. 10월 29일 계획 문서는 당시의 기준선과 제안을 담고 있습니다.

## 검사와 팀 전달

**`MANUX 검사.cmd`**를 더블클릭하거나 아래 명령을 실행합니다. 같은 공통 검사를 팀원 PC와 CI에서 사용합니다.

```powershell
npm run verify
```

| 검사 | 확인하는 것 |
| --- | --- |
| `npm run doctor` | Node·Git·의존성 등 실행 준비 상태 |
| `npm run verify` | 코드·설정 검사와 전체 단위·서버 회귀 |
| `npm run test:browser` | 합성 입력으로 마우스 화면과 연결 흐름 확인. 최초 `npm run browser:install` 필요 |
| `npm run test:native` | Windows 입력 helper의 dry-run. 실제 커서에 명령을 보내지 않음 |
| 실물 사용자 평가 | 실제 웹캠·조명·거리·사용성 확인 |

빨간 결과가 나오면 실패한 단계와 오류 문구를 그대로 공유하세요. 검사 통과와 실제 사용성, GitHub 반영 여부를 구분합니다. [검사 선택·PR·소스 전달 절차](CONTRIBUTING.md)를 따라 변경을 검토합니다.

PR은 Windows/Linux 검사, 브라우저 검사와 Windows 시험용 설치파일 빌드를 통과해야 합니다. PR은 `preview`로 열고 필수 검사 통과 후 병합합니다(리뷰어 승인 없음). preview에서 시험한 뒤 `preview → production` PR로 `production`에 병합하며, 병합되면 GitHub Actions가 정식 버전의 Windows 설치파일·업데이트 메타데이터를 검증하고 GitHub Release로 게시합니다. `release:prepare`는 별도의 소스 전달 도구이며 앱 배포와 구분합니다.

## 구조와 다음 문서

```text
웹캠 → MediaPipe Worker → 손동작 판정 → 영상 / 마우스 연습
                                      └→ Electron → Windows 입력
개인 AI / Discord worker → 작업 브랜치 → PR 검사 → preview 시험 → production → Windows Release
```

| 문서 | 용도 |
| --- | --- |
| [온보딩](docs/onboarding.md) | 첫 실행과 오류 해결 |
| [공통 작업·결과 규칙](docs/harness.md) | 팀원과 에이전트가 같은 기준으로 작업·보고 |
| [개발 참여](CONTRIBUTING.md) | 브랜치·검사·PR·소스 전달 |
| [에이전트 규칙](AGENTS.md) | 저장소 작업 규칙의 원본; `CLAUDE.md`에서 참조 |
| [모듈 계약](docs/contracts.md) · [작업 문서](docs/workstreams/) | 담당 영역과 연결 변경 |
| [영상 사용법](docs/usage-video.md) · [마우스 사용법](docs/air-mouse.md) | 기능별 상세 사용 |
| [데스크톱 설치본·자동 업데이트](docs/desktop-release.md) | Windows 설치파일, PR 시험용 앱, production 자동 배포·업데이트 |
| [LAN 데모](docs/lan-demo.md) | 같은 네트워크에서 브라우저 데모. 주소는 현재 호스트에 확인 |
| [마스터 플랜](docs/master-plan.md) | 기존 요구사항·후속 개발 계획 |

소스 저장소: [intel7-ai/manux](https://github.com/intel7-ai/manux). 모델 출처·해시는 `public/models/model-source.json`에 기록합니다.
