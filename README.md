# MANUX

> `testing` 통합 브랜치: 현재 preview 앱과 Dan 변경의 소스를 함께 보존합니다.
> Dan의 새 기능은 별도 실험 폴더에 있으며 v0.4 화면 연결은 후속입니다. [통합 범위](apps/manux/docs/testing-integration.md).

웹캠의 손동작으로 Windows를 제어하는 **Python + Qt/QML 앱**입니다. 개발 코드는 [apps/manux](apps/manux/README.md)에 있습니다.

## 실행과 검사

Windows·Git·[uv](https://docs.astral.sh/uv/)·웹캠을 준비합니다.

```powershell
cd apps/manux
uv run --locked --python 3.12 --extra ui --extra vision --extra ml --extra train --extra hub python -m manux
uv run --locked --python 3.12 pytest -q
```

루트의 Node.js 24 도구는 공통 보고서·Discord 훅용입니다. 앱 실행기나 Electron 의존성을 포함하지 않습니다.

```powershell
npm ci --ignore-scripts
npm run verify
```

## 현재 상태

- preview의 기본 구현: 엔진·기본 기능·TCN 확인/직접 판정·개인 모델 검증/교체·Preview 설치본이 있습니다.
- 학습 센터·선택 녹화·`.env` 공유는 [#72](https://github.com/intel7-ai/manux/pull/72), 별명 입력·허브 권한 표시는 [#75](https://github.com/intel7-ai/manux/pull/75), 한 손 추적 기본값·앱 내 지연 표시는 [#76](https://github.com/intel7-ai/manux/pull/76)으로 통합합니다. [사용 순서](apps/manux/docs/learning-center.md)와 [커서 지연 확인](apps/manux/docs/cursor-performance.md)을 참고하세요.
- [MANUX Preview](apps/manux/README.md#시험용-설치파일-manux-preview)는 시험용이며 자동 업데이트하지 않습니다. [Releases](https://github.com/intel7-ai/manux/releases)의 정식 v0.3 설치본은 배포 전환 전까지 `hand-mouse/`에서 빌드합니다.
- 자동 시험은 실제 웹캠·Windows 입력·인식률을 보장하지 않습니다. 현재 구현의 기준은 [앱 지침](apps/manux/AGENTS.md#현재-상태)입니다.

## 작업 안내

CI 설치본을 기다리지 않는 공동 소스 시험은 [testing 브랜치 안내](docs/testing.md)를 따릅니다. `testing` 대상 PR과 push는 자동 CI를 실행하지 않습니다.

[개발 참여](CONTRIBUTING.md) · [브랜치 흐름](docs/branch-flow.md) · [필요한 문서 찾기](docs/README.md)

팀 Discord의 `@1조봇(Manux)` 멘션 또는 `/ask`로 목표·문제·완료 조건을 보낼 수 있습니다. 공유 채널에 영상·계정·키를 올리지 않습니다. 팀 데이터 공유는 [허브 안내](apps/manux/docs/dataset-hub.md)를 따릅니다.

이전 웹앱·Electron·실행기는 현재 checkout에서 퇴역했습니다. 과거 코드가 필요할 때만 [고정 커밋 보관 안내](archive/README.md)를 참고합니다.
