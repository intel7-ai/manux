# apps/manux 에이전트 작업 지침 (v0.4)

`apps/manux/` 안의 파일을 만들거나 고치는 모든 작업, 그리고 `hand-mouse/`의 기능을 v0.4로 옮기는 작업은 이 문서를 따른다. 저장소 루트의 [AGENTS.md](../../AGENTS.md)(브랜치·PR·보고 규칙)도 그대로 적용된다. 사람의 현재 요청이 이 문서보다 우선한다.

## 작업 전에 반드시 읽기 (이 순서로)

1. [docs/architecture.md](docs/architecture.md): 폴더, 의존 규칙(§4), 기능 모듈(§5), 프레임 흐름(§6), 이식 순서(§8)
2. [docs/contracts.md](docs/contracts.md): 고칠 계층의 절 전체와 §1 공통 규칙
3. [docs/decisions.md](docs/decisions.md): **확정**과 **제안**을 구분한다. 제안을 확정된 규칙처럼 설명하지 않는다
4. [`src/manux/core/contracts.py`](src/manux/core/contracts.py): 계약 타입의 실제 코드

읽은 뒤 작업 계획에 "어느 계층·어느 기능 ID·어느 계약 절"을 고치는지 적는다. 그 범위 밖의 파일은 고치지 않는다.

## 현재 상태

- 2026-11-05: 이식 단계 **S0**(골격·계약·구조 시험). 기능 코드는 없다. `hand-mouse/`(v0.3)가 설치본이다.
- 확정: Qt/QML + Python 한 프로세스(D-001), 새 앱은 `apps/manux/`, `hand-mouse/`는 수정만(D-002).
- TCN은 Dan 브랜치(`hand-mouse-gesture-overhaul@7448e4a`)에 있고 `none / thumb_index / thumb_ring` 세 클래스만 학습했다. 한 사람·한 세션·한 카메라 기록의 성적이다. 다른 동작이나 실사용 정확도 향상을 보장하는 것으로 설명하지 않는다. `solution.md`의 원거리·적외선 100% 주장은 검증 자료가 아니다.

## 규칙

1. **계약 먼저.** 계층 사이에 오가는 값이나 함수 모양을 바꾸면 `docs/contracts.md`와 `core/contracts.py`를 같은 PR에서 먼저 고친다(계약 §13). PR 본문에 "계약 변경"과 영향받는 계층을 적는다.
2. **의존 규칙.** `architecture.md` §4 표를 지킨다. `tests/architecture/test_layers.py`가 검사한다. 시험을 통과시키려고 표를 고치지 않는다. 표를 바꿔야 하면 이유를 [결정 기록](docs/decisions.md)에 남긴다.
3. **기능끼리 import 금지.** 다른 기능의 상태는 `FrameContext.others`의 `Activity`로만 본다. 비공개 필드(`_`로 시작)를 다른 모듈에서 읽지 않는다.
4. **Windows 입력은 `output/windows`에서만.** 연습(`tutorial`)·일시정지·등록·녹화 중에는 실제 입력이 나가지 않는다(계약 §8).
5. **화면은 판정하지 않는다.** QML·뷰모델에 손짓 판정, 임계값 계산, 액션 실행을 넣지 않는다.
6. **동작 ID ≠ 액션 ID.** 기능은 `<기능>.<이름>` 동작만 내고 실행은 바인딩이 정한다.
7. **이식은 비교로 끝낸다.** `hand-mouse/`에서 옮긴 기능은 같은 입력 열에서 v0.3과 출력이 같은지 재생 시험으로 확인한다. 의도적으로 바꾼 동작은 PR에 차이와 이유를 적는다.
8. **개인 데이터 금지.** 사용자 이름, 개인 프로필, 웹캠 영상, 개인 녹화 원본, 계정·키를 저장소와 시험 fixture에 넣지 않는다.

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
