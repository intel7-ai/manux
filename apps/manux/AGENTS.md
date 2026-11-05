# apps/manux 에이전트 작업 지침 (v0.4)

`apps/manux/` 안의 파일을 만들거나 고치는 모든 작업, 그리고 `hand-mouse/`의 기능을 v0.4로 옮기는 작업은 이 문서를 따른다. 저장소 루트의 [AGENTS.md](../../AGENTS.md)(브랜치·PR·보고 규칙)도 그대로 적용된다. 사람의 현재 요청이 이 문서보다 우선한다.

## 작업 전에 반드시 읽기 (이 순서로)

1. [docs/architecture.md](docs/architecture.md): 폴더, 의존 규칙(§4), 기능 모듈(§5), 프레임 흐름(§6), 이식 순서(§8)
2. [docs/contracts.md](docs/contracts.md): 고칠 계층의 절 전체와 §1 공통 규칙
3. [docs/decisions.md](docs/decisions.md): **확정**과 **제안**을 구분한다. 제안을 확정된 규칙처럼 설명하지 않는다
4. [`src/manux/core/contracts.py`](src/manux/core/contracts.py): 계약 타입의 실제 코드
5. [docs/next-steps.md](docs/next-steps.md): 지금 상태, TCN 구조 결정(비교 실험), 다음 할 일 순서
6. [docs/dataset-hub.md](docs/dataset-hub.md): 팀 녹화 데이터(`.jsonl.gz`)·모델(`.onnx`) 수집 및 Hugging Face 허브(`tools/hub.py`) 동기화 절차

읽은 뒤 작업 계획에 "어느 계층·어느 기능 ID·어느 계약 절"을 고치는지 적는다. 그 범위 밖의 파일은 고치지 않는다.

## 현재 상태

- 2026-11-05 preview `589cc9a`: 이식 단계 **S0~S5 코드 병합**. S3 실제 Windows 입력·S5 실제 녹화 비교 등 남은 수동 검증은 [구조 §8](docs/architecture.md#8-이식-순서)을 따른다. `settings`·`capture`·`tracking`·`data`(녹화 읽기·쓰기·안내 녹화)(S1·S5), `features/pointer`·`output/practice`·`null`(S2), `actions`·`output/windows`(S3), `features/pinch`·`features/motion`(S4), `perception/local3d`·`ml`·`features/tcn_gate`·`tools/train_tcn.py`·`evaluate_tcn.py`(S5), `engine`(파이프라인·중재기), 헤드리스 실행기 `python -m manux.app --replay <녹화> | --camera`(연습 출력만).
- **S6 진행 중:** 데스크톱 앱 `python -m manux`(`desktop.py` + `ui/bridge.py` + v0.3 `ui/qml/dashboard.qml`). 화면 QML은 v0.3의 최신 수정 그대로 두고, 브리지가 같은 속성·슬롯 이름으로 v0.4 엔진(Snapshot·UiCommand)에 연결한다. 화면을 바꿀 때는 QML을, 동작을 바꿀 때는 기능·엔진을 고치고 브리지에 판정을 넣지 않는다. 설치본·자동 업데이트: `packaging/`(PyInstaller + Velopack) + `platform/updater.py`. PR마다 `MANUX Preview` 설치파일을 만든다(`.github/workflows/manux-app.yml`, 게시·자동 업데이트 없음, v0.3과 나란히 설치, 설정은 `%APPDATA%\MANUXPreview\v04`). 트래킹 측정: `data/tracking_test.py`(v0.3과 같은 표적·수치·CSV), 런타임 스레드가 Windows 출력의 커서 위치로 갱신하고 화면은 `ViewFrame.tracking`만 그린다. 남은 것: 학습 센터 화면, 배포 채널 전환(`build.ps1 -Release`를 릴리스 workflow에 연결). 전환 전까지 Releases 설치파일은 `hand-mouse/`(v0.3)에서 빌드한다.
- **검증 구분:** CI는 Preview 설치·자가 진단·Qt 창 열기를 확인한다. 실제 웹캠 사용, 실제 Windows 입력, 사용자 화면 동선, 인식률은 별도 수동 검증이 필요하다. 실제 데이터 확인은 Dan의 녹화 1개(한 사람)를 엔진에 재생한 것뿐이다.
- **#69 병합:** `features/tcn` 직접 판정(명시적 켬), 별도 바인딩 JSON, 사용자 폴더 특징 캐시, `tools/promote_tcn.py`의 별도 eval 검증·불변 버전 설치, 데스크톱 `--tcn-store` 교체가 있다. [직접 TCN 안내](docs/direct-tcn.md)를 먼저 읽는다. Preview 설치본은 #70에 있으며 정식 배포 채널 전환은 남아 있다. 합성 시험과 실제 사용자 확인을 구분한다.
- **preview 밖의 작업:** 학습 센터·별명별 녹화·HF 앱 연결·별도 worker는 [PR #72](https://github.com/intel7-ai/manux/pull/72)에서 별도 검증 중이다. 사용자 정의 카탈로그·바인딩 편집 화면은 후속이다. 열린 PR의 구현·CI 결과를 preview 병합이나 정식 릴리스로 설명하지 않는다.
- OpenCV·MediaPipe는 실제 카메라·모델을 열 때만 import한다(`vision` extra). 시험과 CI는 이 둘 없이 돈다. 새 코드도 이 방식을 따른다.
- 확정: Qt/QML + Python 한 프로세스(D-001), 새 앱은 `apps/manux/`, `hand-mouse/`는 수정만(D-002), 겹치는 동작은 한 기능에 묶는 기능 모듈(D-003), 동작 ID ≠ 액션 ID(D-004), 릴리스용 기본 모델 + 사용자 개인 모델(D-005 방향), 학습 데이터는 v0.4 동안 팀 공유 허용·릴리스부터 사용자 PC에만(D-006).
- 제안(아직 규칙 아님): D-005 세부 기준, 계약의 **제안** 표시 절.
- TCN: v0.4 도구는 녹화 라벨에서 클래스를 읽어 3개로 고정되지 않는다(합성 5클래스로 실행 경로만 확인). 실제 모델은 아직 Dan의 3클래스(`none / thumb_index / thumb_ring`), 한 사람·한 세션·한 카메라 기록의 성적뿐이다. 다른 동작이나 실사용 정확도 향상을 보장하는 것으로 설명하지 않는다. `solution.md`의 원거리·적외선 100% 주장은 검증 자료가 아니다.

## 규칙

1. **계약 먼저.** 계층 사이에 오가는 값이나 함수 모양을 바꾸면 `docs/contracts.md`와 `core/contracts.py`를 같은 PR에서 먼저 고친다(계약 §13). PR 본문에 "계약 변경"과 영향받는 계층을 적는다.
2. **의존 규칙.** `architecture.md` §4 표를 지킨다. `tests/architecture/test_layers.py`가 검사한다. 시험을 통과시키려고 표를 고치지 않는다. 표를 바꿔야 하면 이유를 [결정 기록](docs/decisions.md)에 남긴다.
3. **기능끼리 import 금지.** 다른 기능의 상태는 `FrameContext.others`의 `Activity`로만 본다. 비공개 필드(`_`로 시작)를 다른 모듈에서 읽지 않는다.
4. **Windows 입력은 `output/windows`에서만.** 연습(`tutorial`)·일시정지·등록·녹화 중에는 실제 입력이 나가지 않는다(계약 §8).
5. **화면은 판정하지 않는다.** QML·뷰모델에 손짓 판정, 임계값 계산, 액션 실행을 넣지 않는다.
6. **동작 ID ≠ 액션 ID.** 기능은 `<기능>.<이름>` 동작만 내고 실행은 바인딩이 정한다.
7. **이식은 비교로 끝낸다.** `hand-mouse/`에서 옮긴 기능은 같은 입력 열에서 v0.3과 출력이 같은지 재생 시험으로 확인한다. 의도적으로 바꾼 동작은 PR에 차이와 이유를 적는다.
8. **개인 데이터 금지.** 사용자 이름, 개인 프로필, 웹캠 영상, 계정·키를 저장소와 시험 fixture에 넣지 않는다. 녹화 원본은 `apps/manux/`와 시험 fixture에 복사하지 않는다(팀 실험 공유는 [D-006](docs/decisions.md#d-006-학습-데이터-위치)과 [docs/dataset-hub.md](docs/dataset-hub.md)의 `tools/hub.py`를 따른다).

## 기능 추가 절차

1. 기능 ID를 정한다(소문자, 예: `media_swipe`). 같은 ID가 없는지 `src/manux/features/`를 확인한다.
2. `contracts.md` §7 액션 목록에 필요한 액션이 없으면 계약 변경부터 한다.
3. `src/manux/features/<id>/`를 만들고 `Feature`를 구현한다. 같은 패키지에 `GESTURES`, `DEFAULT_BINDINGS`, `SETTINGS`를 둔다.
4. 시험: `tests/unit/features/test_<id>.py`. `begin`에는 반드시 `end`가 오는지, `reset()` 뒤 상태가 비는지, 손 없음(`None`) 입력을 시험한다.
5. 설정 화면이 필요하면 `ui/qml/pages/features/<id>/`와 뷰모델. 색·크기는 theme 토큰만 쓴다.
6. `app.py`의 기능 목록에 한 줄 추가.

## 검증

```powershell
cd apps/manux
uv run --python 3.12 pytest -q
```

구조·계약·단위·재생 시험과 실제 웹캠 사용 시험을 구분해서 보고한다. 시험 수나 프레임 점수를 제품 정확도로 설명하지 않는다. 실행하지 않은 검사를 완료로 표시하지 않는다.
