# MANUX

웹캠의 손동작으로 Windows를 제어하는 **Python + Qt/QML 앱**입니다. 개발 코드는 [apps/manux](apps/manux/README.md)에 있습니다.

## 실행과 검사

Windows·Git·[uv](https://docs.astral.sh/uv/)·웹캠을 준비합니다.

```powershell
cd apps/manux
uv run --locked --python 3.12 --extra ui --extra vision python -m manux
uv run --locked --python 3.12 pytest -q
```

루트의 Node.js 24 도구는 공통 보고서·Discord 훅용입니다. 앱 실행기나 Electron 의존성을 포함하지 않습니다.

```powershell
npm ci --ignore-scripts
npm run verify
```

## 현재 상태

- preview `589cc9a`: 엔진·기본 기능·TCN 확인/직접 판정·개인 모델 검증/교체·Preview 설치본이 있습니다.
- 학습 센터는 [PR #72](https://github.com/intel7-ai/manux/pull/72), 후속 입력 수정은 [PR #75](https://github.com/intel7-ai/manux/pull/75)에서 별도 검증 중입니다.
- [MANUX Preview](apps/manux/README.md#시험용-설치파일-manux-preview)는 시험용이며 자동 업데이트하지 않습니다. [Releases](https://github.com/intel7-ai/manux/releases)의 정식 v0.3 설치본은 배포 전환 전까지 `hand-mouse/`에서 빌드합니다.
- 자동 시험은 실제 웹캠·Windows 입력·인식률을 보장하지 않습니다. 현재 구현의 기준은 [앱 지침](apps/manux/AGENTS.md#현재-상태)입니다.

## 작업 안내

[개발 참여](CONTRIBUTING.md) · [브랜치 흐름](docs/branch-flow.md) · [필요한 문서 찾기](docs/README.md)

팀 Discord의 `@1조봇(Manux)` 멘션 또는 `/ask`로 목표·문제·완료 조건을 보낼 수 있습니다. 공유 채널에 영상·계정·키를 올리지 않습니다. 팀 데이터 공유는 [허브 안내](apps/manux/docs/dataset-hub.md)를 따릅니다.

이전 웹앱·Electron·실행기는 현재 checkout에서 퇴역했습니다. 과거 코드가 필요할 때만 [고정 커밋 보관 안내](archive/README.md)를 참고합니다.
