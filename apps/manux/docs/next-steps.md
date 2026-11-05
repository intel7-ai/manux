# v0.4 다음 작업 계획 (2026-11-05)

> 작성: Sean Choi (AI: Claude Code). 다른 세션(Codex 등)이 이 문서만 보고 이어서 작업할 수 있게 쓴다.
> 먼저 [AGENTS.md](../AGENTS.md) → [architecture.md](architecture.md) → [contracts.md](contracts.md) → [decisions.md](decisions.md)를 읽는다.
> 이 브랜치(`scrum-105-next-steps`)에는 이 문서와 함께 **아직 preview에 없는 작업 1번**이 들어 있다.

## 1. 지금 상태 (preview `8bbff09`)

- S0~S5 병합: settings·capture·tracking·features(pointer/pinch/motion)·actions·output(windows/practice/null)·engine·perception(local3d)·ml·data(녹화 읽기/쓰기, 안내 녹화)·tcn_gate·도구(train/evaluate/collect).
- S6 진행 중: 데스크톱 앱 `python -m manux`(#65). v0.3 `dashboard.qml`을 그대로 쓰고 `ui/bridge.py`가 v0.4 엔진에 연결. 트레이·전역 단축키·튜토리얼·손가락 HUD·방향 보정 포함.
- S6a 병합(#66, Codex #63 포함): 18개 녹화 과제 카탈로그(`data/catalog.py`), 녹화 계획(`data/capture_plan.py`, basic=중앙/왼쪽/오른쪽, positions=9구역, diverse), `tools/collect_mouse.py --plan --focus`.
- 속도 회귀 수정(#64): `Intent.scale`로 손 모양 1~25배·길게 누른 드래그 15배가 출력까지 전달.
- **확인 안 한 것:** 실제 웹캠, 실제 Windows 입력(이 작업 PC에는 카메라 없음), 인식률(데이터가 한 사람뿐).

## 2. TCN 구조 결정 (비교 실험 결과)

Dan의 `per-gesture-tcn-briefing.md`(동작별 이진 TCN)를 분석하고 Dan 녹화 1개(한 사람, 자세 3개를 하나씩 빼고 평가, 20 에포크)로 비교했다. 도구: `tools/compare_tcn.py`(이 브랜치).

| 구조 | 엄지+검지 | 엄지+약지 | none 오작동/분 (자세별) | 학습 시간 |
|---|---|---|---|---|
| 규칙(v0.3) | 20/45 | 19/45 | 3.4 · 9.5 · 1.6 | — |
| **A 다중 분류 TCN 1개** | **33/45** | **26/45** | **2.9 · 7.7 · 0.8** | 15~20초 |
| B 동작별 이진 TCN | 25/45 | 25/45 | 6.6 · 8.7 · 1.3 | 30초 |

새 동작 추가(엄지+약지를 나중에 추가):
- B-add(기존 이진 모델 유지 + 새 모델 추가): 기존 검지 모델이 약지 시도의 **5/15에서 오작동**(자세 1), 오작동/분 최대 11.9. "무간섭"은 파일만 그렇고 시스템은 아니다.
- C(고정 몸통 + 새 머리): 새 동작 **0/45**. 클래스 2개로 배운 몸통은 약지를 구별하지 못했다.
- A(전부 다시 학습): 캐시된 특징으로 15~20초.

**결정(제안, 데이터가 늘면 다시 확인):**
1. 기본 모델 = 다중 분류 TCN 1개, 데이터가 바뀌면 캐시된 전체 데이터로 다시 학습(잊어버림 없음, 동작끼리 배타적). 클래스별 임계값·연속 프레임 수.
2. 사용자 동작 = 같은 모델에 클래스를 추가해 기본 데이터 + 사용자 데이터로 다시 학습(개인 모델, D-005).
3. 동작별 이진 TCN·고정 몸통 머리는 지금은 쓰지 않는다. 3~5명 × 18과제 데이터가 생기면 `compare_tcn.py --axis person`으로 다시 비교한다.
4. 여러 TCN은 **시간 규모로** 나눈다(짧은 핀치 0.5초 / 스와이프 1~1.5초, 엔진 중재기의 모드와 같은 경계). 아직 시험 안 함.

결과가 바뀔 수 있는 것: 사람·카메라·조건 수(가장 큼), 클래스 수(18개), 데이터 크기(전체 재학습 시간 증가), TCN을 게이트가 아니라 직접 판정에 쓰는 것, 추적 설정(확대·해상도·어두움).

## 3. 새 동작을 넣는 절차와 빈 곳

```
정의 → 녹화 → 학습 → 확인 → 설치 → 연결 → 사용
```

| 단계 | 지금 | 빈 곳 |
|---|---|---|
| 정의 | 기본 동작: `data/catalog.py`에 과제 추가 | 사용자 정의 카탈로그 파일(코드 수정 없이) |
| 녹화 | `tools/collect_mouse.py --focus <과제> --plan basic` | 앱 안 학습 센터 화면 |
| 학습 | `tools/train_tcn.py`(클래스는 녹화 라벨에서) | 특징 캐시(`.npz`) |
| 확인 | `tools/evaluate_tcn.py`(사람·자세·조건 분리) | 저장 전 확인(새 동작이 되고 기존 동작이 나빠지지 않을 때만 교체) |
| 설치 | 모델 정보 파일(`kind`, `gestures`) 검사 | 데스크톱 앱 핫리로드 |
| 연결 | 바인딩·라우터 | 바인딩 편집 화면, 미디어 액션(`media.*`) |
| 사용 | `tcn_gate`는 규칙 판정을 확인·차단만 함 | **TCN이 직접 의도를 내는 기능이 없다 → 새 동작은 학습해도 아무것도 실행하지 못한다** |

## 4. 할 일 (순서대로)

### 4.1 [이 브랜치에 포함] 비교 도구 + 규칙 기준 점수 수정 → PR
- `tools/compare_tcn.py`: 위 비교를 다시 실행하는 도구.
- 비교 전 조건 검사: 동작 2개 이상과 `none`, 선택한 축의 그룹 2개 이상, 모든 구간의 그룹 정보, 각 fold의 학습 클래스별 유효 프레임이 필요하다. 조건을 못 채우면 결과 없이 성공하지 않고 오류로 끝난다. `epochs`·`count`는 양수, `threshold`는 (0, 1]이다.
- 출력에는 특징 버전·학습 설정·fold별 구간 수·중립 노출 시간을 남긴다. 중립 노출이 없으면 오작동/분은 0이 아니라 `null`(측정 불가)이다. `B_double_fires`는 확인된 이진 예측이 실제로 겹친 연속 구간 수다(시작 시각이 가까운 횟수가 아님).
- `tools/tcn_data.py`: v0.3 녹화는 `rule.gesture`에 v0.3 이름(`thumb_ring_pinch`)이 있어 규칙 기준이 항상 0이었다. v0.4 ID(점 포함)일 때만 쓰고 아니면 녹화된 action으로 바꾼다. 회귀 시험 `tests/unit/data/test_tcn_data.py::test_rule_baseline_counts_v03_recordings`.
- 할 일: `cd apps/manux; uv run --python 3.12 pytest -q` 확인 후 preview 대상 PR.

### 4.2 [팀] 녹화 모으기 (가장 큰 병목)
3~5명이 각자 PC·카메라로:
```powershell
cd apps/manux
uv run --python 3.12 --extra vision python tools/collect_mouse.py --list
uv run --python 3.12 --extra vision python tools/collect_mouse.py --profile <익명별명> --preset pinch --plan basic
```
- 평가용은 따로 `--split eval`로 한 번 더(학습과 같은 시도를 섞지 않는다).
- 녹화는 사용자 폴더에 저장된다. 저장소에 넣지 않는다(D-006). 팀 공유는 `tools/hub.py`(`se4nchoi/manux-datasets`)로 올리고 받는다([dataset-hub.md](dataset-hub.md) 참고: `uv run --python 3.12 --with huggingface-hub python tools/hub.py push` / `pull`).
- 모이면: `uv run --python 3.12 --extra train python tools/compare_tcn.py <녹화들> --axis person`.

### 4.3 [실제 PC] 하드웨어 확인 (웹캠 있는 Windows PC)
- `uv run --python 3.12 --extra ui --extra vision python -m manux`
- 확인: 손 고르기 → 튜토리얼 → 메인. 제어 켬 후 이동·클릭·우클릭·드래그·스크롤·모션 스와이프가 실제 Windows에서 동작하는지, 일시정지·손 사라짐 때 버튼이 남지 않는지. S3 완료 기준(실제 Windows 수동 확인)을 이것으로 닫는다. 결과를 architecture.md §8 표에 기록.

### 4.4 [개발] TCN 직접 판정 기능 — 다음 핵심 작업
- `features/tcn/`: 모델 정보의 `gestures`(클래스 → 동작 ID)대로 의도를 내는 `Feature`. 클래스별 임계값·연속 프레임, 모드(`modes`)는 모델이 다루는 동작에 맞춘다(핀치 모델 = `pinch`, 모션 모델 = 기본 모드들 + `motion`).
- 규칙 기능과의 관계: 같은 동작 ID를 규칙과 TCN이 동시에 내지 않게 한다(설정으로 동작별 출처 선택 또는 TCN 우선 + 규칙 끔). 결정을 decisions.md에 남긴다.
- 특징 캐시: 녹화별 `local3d-v1` 특징을 `.npz`로(사용자 폴더). 특징 버전·녹화 해시가 다르면 다시 계산.
- 저장 전 확인: 새 모델이 따로 한 시도(eval split)에서 새 동작을 인식하고 기존 동작 인식·오작동이 나빠지지 않을 때만 교체(D-005 세부 기준을 계약으로).
- 핫리로드: `desktop.py`가 모델 파일 변경을 감지해 엔진의 기능을 교체(교체 전 `release_all`).
- 시험: 가짜 예측기로 의도·phase·임계값, 재생 시험, 구조 시험. 계약 변경은 contracts.md §5·§6·§10 먼저.

### 4.5 [개발] 앱 화면: 학습 센터 + 바인딩 편집
- **학습 센터·녹화 계획 화면 이식 완료:** `data/training_center.py`(녹화 요약, v0.4 `held_out_results`·v0.3 모델 성적 통합, 동작별 안내 행, `tools/train_tcn.py` 백그라운드 전체/선택 학습, 모델 내보내기), `ui/qml/RecordingPlanDialog.qml`, `ui/qml/dashboard.qml`("학습 센터" 팝업, 안내/평소 녹화 제어, 9구역 위치 안내 격자), `desktop.py`(녹화 중 `SetBusy("recording", True)`로 출력 `none` 보장).
- 남은 것: 바인딩 편집 화면(동작 → 액션 연결), 사용자 정의 카탈로그 파일(사용자 폴더 JSON)로 코드 수정 없이 새 동작 정의.

### 4.6 [개발] 설치본·자동 업데이트, 배포 전환
- **설치본·자동 업데이트 완료(#70):** `packaging/`(PyInstaller + Velopack) + `platform/updater.py`. PR마다 `MANUX Preview` 시험 설치파일(`.github/workflows/manux-app.yml`, v0.3과 나란히 설치, 업데이트 없음). 릴리스 설치본의 사용자 폴더는 `%APPDATA%\MANUX\v04`(v0.3과 분리).
- 남은 것: Preview 설치파일로 v0.3 기능 목록 수동 확인 후 배포 채널 전환(`build.ps1 -Release`를 릴리스 workflow에 연결). **`hand-mouse/` 아래를 바꾸면 production 반영 때 설치본이 배포되므로 건드리지 않는다.**

### 남은 작은 일
- v0.3 중첩 설정(`gestures.<id>`, `motion_actions`)을 v0.4 바인딩으로 변환(`settings/migrate_v03.py`).
- 미리보기 위 손가락 번호 그리기. (트래킹 측정은 #70에서 `data/tracking_test.py`로 이식)
- 모션 진입 진행률(%)을 Snapshot에 넣어 배지에 표시.

## 5. 작업 규칙 요약
- 브랜치는 최신 `origin/preview`에서 `scrum-105-<설명>`(도구 접두사 없이), PR 대상 preview.
- 계층 규칙(architecture §4)·계약 먼저(contracts §13). 브리지·QML에 판정을 넣지 않는다.
- 녹화·모델·개인 데이터를 저장소에 넣지 않는다. 시험은 합성 데이터만.
- 실행하지 않은 검사·한 사람 데이터의 수치를 제품 정확도로 쓰지 않는다.
