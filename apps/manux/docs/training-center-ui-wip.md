# 학습 센터 UI WIP 인계 (2026-11-05)

사용자 요청으로 여기서 개발을 중단하고 별도 브랜치에 보존한다. **실행 가능한 완성본이 아니다.**

- 브랜치: `scrum-105-training-center-ui-wip`
- 출발 커밋: `42d7fa4` (`scrum-105-training-center`, preview 대상 [PR #72](https://github.com/intel7-ai/manux/pull/72))
- preview 병합·production 반영은 하지 않았다. PR #72는 이전 UI의 학습 흐름 구현이며 이번 UI WIP는 포함하지 않는다.

## 사용자의 최신 요청

- 학습 센터 UI를 MANUX 네이비·민트 색상으로 전면 정리한다.
- 기본 input/select의 검은 화면·읽기 어려운 모양을 없앤다.
- 동작 목록은 체크한 항목이 선택한 동작이다. 선택한 대상의 녹화·학습 버튼을 명확하게 둔다.
- 앱에서 HF 토큰을 받지 않는다. 기존 `.env`로 업로드한다.
- 집에서 이어서 작업할 수 있도록 현재 변경 전부를 WIP 커밋·원격 별도 브랜치로 보존한다.

## 지금 저장한 변경

- 계약 §12: 체크 선택 목록, 선택 녹화·통합 재학습, worker의 `.env` 인증으로 수정했다.
- `data/training_center.py`: 프로필 저장은 별명만, HF 요청은 환경 파일 경로만 전달한다. 다중 선택 녹화 계획 저장 키와 시간 계산을 추가했다.
- `desktop.py`: 선택한 과제 목록으로 녹화할 수 있고 설치 폴더 `.env`를 worker에 넘긴다.
- `ui/bridge.py`: 체크 선택 상태·필터 간 선택 유지·선택 녹화·선택 학습 API를 추가했다.
  녹화 계획 편집기의 다중 과제 제목·설명을 추가하고 토큰 입력 슬롯을 별명 저장 슬롯으로 바꿨다.
- `tools/app_worker.py`: source `.env`와 전달받은 환경 파일에서 HF 설정을 읽고 실제 repo를 결과에 돌려준다.
  선택한 여러 학습 클래스의 자료를 확인한다.
- 새 QML `theme/Studio.qml`, `components/StudioButton.qml`, `StudioField.qml`, `StudioCheck.qml`,
  `StudioSelect.qml`, `StudioStepper.qml`: 네이비·민트, 밝은 배경, 명시적인 글자색·체크·드롭다운 스타일.

## 이어서 할 일과 알려진 미완성

1. **새 학습 센터 화면은 아직 작성하지 않았다.** `dashboard.qml`에는 이전 팝업과 토큰 입력이 남아 있다.
   이미 제거한 `saveHubSettings`를 부르므로 지금 상태로 기존 설정 버튼을 쓰면 안 된다.
2. `LearningCenter.qml` 등으로 화면을 분리한다. 체크 목록 + 선택 상세/녹화·공유·학습 동선,
   train/eval 세그먼트, 별명만 받는 스타일 필드, `.env` 연결 상태를 구현한다.
   체크는 `setTrainingSelected` / `selectedTrainingRows`에 연결한다.
   선택 녹화는 `recordTrainingSelection()` 성공 후 기존 `RecordingPlanDialog`를 연다.
   선택 학습은 `trainTrainingSelection()`을 사용한다. 선택 0개는 실행을 막는다.
3. `RecordingPlanDialog.qml`의 기존 TextField/ComboBox/SpinBox/Button/CheckBox를 새 Studio 컨트롤로 연결한다.
4. `.env` UI 제거에 맞춰 테스트와 사용법을 갱신한다. 기존 테스트·로컬 진단 스크립트에는
   `configure(profile, repo, token)` / `saveHubSettings` 호출이 남아 있어 수정이 필요하다.
5. HF status/download는 별명 없이도 연결 확인이 되도록 `hub_action`의 별명 검증 범위를 정리한다.
   업로드만 현재 별명의 완료된 기록을 요구한다. 토큰을 앱 프로세스나 요청 JSON에 넣지 않는다.
6. QML 화면 렌더링(기본·작은 창·다중 선택·녹화 계획), 체크 대상의 정확한 녹화/학습,
   `.env` 연결, 전체 Python·npm 검사와 Windows 설치본 CI를 다시 실행한다.
7. 원래 PR #72와 WIP 브랜치를 어떻게 통합할지 정한 뒤 최종 변경으로 preview PR을 갱신한다.

## 검증 기준과 환경

- `42d7fa4` 기준 전체 Python 583개, npm 334개가 통과했다. source·Windows PyInstaller worker로
  합성 학습→독립 eval→설치→앱 적용·진행 이벤트·취소 보존을 확인했다.
- **그 뒤의 이 UI WIP는 시험하지 않았다.** 사용자가 중단을 요청해 추가 구현·검증하지 않는다.
- 기존 로컬 `.env` 인증으로 HF Dataset 읽기·쓰기와 실제 worker status 요청은 성공했다.
  원격 녹화는 확인 당시 0개였으며 실제 녹화는 업로드하지 않았다. 인증 파일은 Git에 포함하지 않는다.
- 이 PC는 RTX 3050 6GB와 Intel AI Boost NPU가 정상 인식된다. 현재 프로젝트 환경은
  PyTorch CPU 빌드, ONNX CPU 공급자이며 OpenVINO는 없다. GPU/NPU 가속 코드는 추가하지 않았다.

집에서는 먼저 루트·`apps/manux/AGENTS.md`와 구조·계약·결정 기록을 읽고 이 브랜치에서 이어간다.
