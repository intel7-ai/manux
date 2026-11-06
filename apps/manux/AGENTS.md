# MANUX 앱 작업 지침

현재 앱은 Python + Qt/QML 한 프로세스다. 저장소 공통 규칙은 [루트 지침](../../AGENTS.md)을 따른다.

## 필요한 문서만 읽기

- 처음 앱 구조를 다루면 [구조](docs/architecture.md)의 계층·프레임 흐름을 읽는다.
- 변경 전 [계약](docs/contracts.md)의 공통 규칙과 **해당 계층 절**, [타입](src/manux/core/contracts.py), 관련 [결정](docs/decisions.md)을 확인한다. 제안을 확정된 정책으로 설명하지 않는다.
- 직접 TCN/모델 교체는 [직접 TCN](docs/direct-tcn.md), 데이터 수집·공유는 [허브](docs/dataset-hub.md)를 추가로 읽는다. 요청과 무관한 과거 계획·모든 문서를 일괄 읽지 않는다.

## 현재 상태

기준: 2026-11-06 preview 통합 대상 #72·#75·#76의 구현. 실제 원격 병합 상태는 PR에서 확인한다.

- S0~S5 코드, 직접 TCN·특징 캐시·검증 후 개인 모델 설치/교체(#69), Preview 설치본·트래킹 측정(#70), 허브 CLI(#68)가 병합됐다.
- [PR #72](https://github.com/intel7-ai/manux/pull/72)는 체크한 과제의 녹화·통합 재학습, 별명별 train/eval, HF 공유와 별도 worker를 연결한다([사용법](docs/learning-center.md)). 인증은 worker가 `.env`에서 읽고 앱에는 별명만 저장한다. [#75](https://github.com/intel7-ai/manux/pull/75)는 별명 입력 초안·선택 녹화 안내·HF 읽기/쓰기 권한 표시를 보강한다. [#76](https://github.com/intel7-ai/manux/pull/76)은 기본 한 손 추적과 앱 내 프레임 지연 표시를 추가한다([측정 범위](docs/cursor-performance.md)). 합성/설치 시험과 실제 사용 검증·정식 릴리스를 구분한다.
- Releases는 정식 배포 전환 전까지 v0.3에서 빌드한다. 실제 웹캠·Windows 입력·다인 인식률은 별도 시험이 필요하다. CI 창 열기·설치 성공이 실사용 검증은 아니다.
- 사용자 정의 카탈로그·바인딩 편집·정식 배포 전환은 후속이다. 과거 작업 계획은 현재 미구현 목록으로 읽지 않는다.

## 구현 경계

- 계약을 바꾸면 문서·타입을 먼저 함께 고치고 영향받는 계층을 PR에 적는다. [구조 §4](docs/architecture.md#4-의존-규칙)의 import 방향과 기능 간 import 금지를 지킨다.
- 기능은 `features/<id>/`에 둔다. `Feature`·동작 목록·기본 바인딩·설정을 함께 두고 조립 지점에서 연결한다. 겹치는 상태를 공유하는 동작을 억지로 분리하지 않는다.
- QML·브리지는 판정·임계값·OS 실행을 하지 않는다. UI는 Snapshot을 그리고 명령 큐를 쓴다. Windows 입력은 `output/windows`만 담당한다.
- 기능 간 상태는 `FrameContext.others`의 `Activity`로만 본다. 비공개 필드를 읽지 않는다. 시작·해제 짝, reset, 손 소실과 오류 안전성을 시험한다.
- MediaPipe/OpenCV/ONNX는 실제로 사용할 때 지연 import한다. torch는 학습 도구/별도 worker에만 둔다. 사용자 데이터·특징 캐시·개인 모델은 저장소 밖에 둔다.
- 기존 기능 이식은 같은 입력 재생 비교로 확인한다. 자동 시험을 실물 인식률로 표현하지 않는다.

## 검증

`apps/manux`에서 `uv run --locked --python 3.12 pytest -q`. 선택 의존성·skip·실물 미검증 항목을 보고한다. 실행과 설치는 [README](README.md)를 따른다.
