# MANUX (v0.5)

<p align="center">
  <img src="https://img.shields.io/badge/Runtime-Python_3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Runtime" />
  <img src="https://img.shields.io/badge/Desktop-Qt6_QML_(PySide6)-41CD52?style=flat-square&logo=qt&logoColor=white" alt="Desktop" />
  <img src="https://img.shields.io/badge/AI_Vision-MediaPipe_Tasks-0078D4?style=flat-square&logo=google&logoColor=white" alt="MediaPipe" />
  <img src="https://img.shields.io/badge/Custom_AI_Model-SpatialPoseMLP_(Zero--GC)-8A2BE2?style=flat-square" alt="Custom AI Model" />
  <img src="https://img.shields.io/badge/Trained_Samples-17%2C689+_Gold_Dataset-success?style=flat-square" alt="Trained Samples" />
  <img src="https://img.shields.io/badge/Inference_Speed-0.003ms_(3.0μs)-yellow?style=flat-square" alt="Inference Speed" />
  <img src="https://img.shields.io/badge/Unit_Tests-890_Passed-brightgreen?style=flat-square" alt="Unit Tests" />
  <img src="https://img.shields.io/badge/Platform-Windows_x64-0078D6?style=flat-square&logo=windows&logoColor=white" alt="Platform" />
</p>

웹캠을 통해 Windows 마우스와 제스처를 손짓만으로 정밀하게 제어하는 AI 휴먼 인터페이스 앱입니다.  
단일 프로세스 내에서 **PySide6(Qt/QML)** UI, **Google MediaPipe** 3D 손 랜드마크 추적, 그리고 초경량 **96차원 Spatial MLP(3D 공간 신경망)** 모델이 센서 퓨전(Sensor Fusion)되어 극저지연(3.0µs)의 안정적인 제스처 마우스 경험을 제공합니다.

---

## ⚡ v0.5 핵심 기능 & 최신 개선 사항 (Key Features)

### 1. 물리 마우스 1:1 조작 패러다임 (Pure 1:1 Physical Mouse Emulation)
- **인위적 딜레이/버퍼링 완전 폐기**: 지연 보류(`pending click`) 로직을 걷어내고, 실제 하드웨어 마우스와 정확히 동일한 1:1 즉각 반응 구조로 전환했습니다.
- **자세 접촉/유지 (Contact & Hold)**: 손가락이 닿거나 제스처를 취하는 순간 즉시 마우스 버튼 Down (`buttons.press`).
- **자세 유지 중 이동 (Drag)**: 버튼이 눌린 상태 그대로 커서가 이동하여 좌클릭 드래그 및 우클릭 드래그가 자연스럽게 동작.
- **자세 해제 (Release on Separate)**: 손가락을 떼는 순간 즉시 마우스 버튼 Up (`buttons.release` on `mouse.move`).
- **지뢰찾기, 웹 서핑, 창 드래그 완벽 지원**: 불필요한 더블클릭 오발화나 버튼 릴리즈 딜레이 없이 마우스와 똑같이 반응합니다.

### 2. 우클릭 드래그 & 양쪽 동시 클릭 (`thumb_middle_ring`)
- **우클릭 드래그 (`pinch.right_drag`)**: 엄지+약지(`thumb_ring`)를 맞댄 채 손을 움직이면 우클릭이 눌린 채 자유롭게 드래그됩니다.
- **양쪽 동시 클릭 (Chording)**: 엄지+중지+약지를 동시에 맞대면 마우스 좌클릭과 우클릭이 동시에 눌린 상태로 유지됩니다. 지뢰찾기(Minesweeper)에서 주변 숫자 타일 동시 열기, CAD 및 3D 모델링 툴의 뷰포트 양쪽 버튼 조작을 손짓만으로 수행할 수 있습니다.

### 3. 학습 센터 (포즈 스튜디오 / PoseSnapshotStudio) 고도화
- **"누르고 있는 중인 자세(Continuous Hold)" 실시간 수집**: 모델 학습에 사용하는 데이터는 클릭 순간의 찰나가 아닌 '누르고 있는 중인 자세'입니다. 우클릭(`thumb_ring`)이나 양쪽 클릭(`thumb_middle_ring`)을 취했을 때 2D 규칙 간섭으로 좌클릭으로 오분류되던 현상을 완전히 해결했습니다.
- **21개 전 관절 인식 검증 (`require_full_joints`)**: 화면 테두리 밖으로 손가락이 잘려 불완전한 상태로 수집되는 왜곡 표본을 원천 차단합니다.
- **4초 즉시 학습 & 무중단 핫리로드 (Zero-Restart)**: 앱 재부팅 없이 수집한 표본을 4초 만에 즉시 학습하여 실시간 엔진에 바로 적용합니다.
- **멀티 모델 관리**: 작업 용도별(고속 작업용, 정밀 작업용 등) 모델을 생성·전환·삭제할 수 있습니다.

### 4. 3D Spatial MLP & Sensor Fusion 레이어 (`SpatialPoseGuard`)
- **2D 한계 극복**: 카메라 각도 왜곡과 원근감에 취약했던 기존 2D 유클리드 좌표 판정에서 벗어나, 3D 월드 랜드마크 기반 **96차원 공간 기하학 특징(Spatial Features)** 추출.
- **초경량 Zero-GC 인퍼런스**: 순수 NumPy 행렬 연산으로 **0.003ms (3.0µs)** 추론 속도를 달성하여 프레임 드랍 없는 실시간 보정 수행.
- **센서 퓨전 & 오판 교정**: 손이 기울어지거나 가로로 누워도 MLP가 자세를 확인하면 2D 규칙의 오판을 실시간으로 올바른 제스처로 변환/보호합니다.

### 5. 모션 모드와 기본 마우스 조작의 완전 분리 (Decoupled Motion Mode)
- 엄지척(`thumbs_up`)으로 모션 모드를 켜도 기존 마우스 이동, 클릭, 스크롤 등의 기본 동작이 멈추지 않고 그대로 동작하며, 모션 제스처만 추가 레이어로 함께 작동합니다.

### 6. 바탕화면 보기(`palm_side`) 수평 손 검증 & 고급 설정
- 일상적인 손 이동 중 바탕화면이 열리는 오발화를 방지하기 위해, 손바닥을 가로로 눕힌 수평 손자세($dx \ge 0.65 dy$)와 일정 시간 유지를 엄격히 검증합니다.
- 설정 UI에서 바탕화면 보기 기능을 켜고 끌 수 있는 토글(`enable_thumb_tucked_open`)을 제공합니다.

---

## ✋ 지원 제스처 및 동작 목록 (10 Gestures)

| 번호 | 클래스 ID | 제스처 이름 | 매핑 동작 | 조작 설명 |
|:---:|---|---|---|---|
| **0** | `none` | 이동 / 대기 | 커서 이동 (`mouse.move`) | 손을 편 채 또는 주먹을 쥐고 자유롭게 커서 이동 |
| **1** | `thumb_middle` | 좌클릭 / 좌클릭 드래그 | 좌클릭 및 드래그 (`mouse.left_click`, `mouse.drag`) | 엄지와 중지를 맞대어 클릭, 맞댄 채 움직이면 드래그 |
| **2** | `thumb_ring` | 우클릭 / 우클릭 드래그 | 우클릭 및 드래그 (`mouse.right_click`, `pinch.right_drag`) | 엄지와 약지를 맞대어 우클릭, 맞댄 채 움직이면 우클릭 드래그 |
| **3** | `thumb_middle_ring` | 양쪽 동시 클릭 | 좌우 버튼 동시 누름 유지 (`mouse.both_click`) | 엄지+중지+약지 3점을 맞대어 양쪽 버튼 동시 홀드 (지뢰찾기 등) |
| **4** | `thumb_index` | 스크롤 | 부드러운 스크롤 (`mouse.scroll`) | 엄지와 검지를 맞댄 채 손을 상하좌우로 움직여 스크롤 |
| **5** | `thumb_pinky` | 작업 전환기 | 윈도우 작업 전환 창 열기 (`window.task_switcher`) | 엄지와 새끼손가락을 맞대기 (Ctrl+Alt+Tab) |
| **6** | `palm_side` | 바탕화면 보기 | 모든 창 최소화 / 복원 (`window.show_desktop`) | 손바닥을 수평으로 눕히고 엄지를 접어 0.6초 유지 (Win+D) |
| **7** | `thumbs_up` | 모션 모드 | 모션 모드 전환 (`motion.enter` / `motion.exit`) | 주먹을 쥐고 엄지만 위로 세워 모션 제어 모드 토글 |
| **8** | `v_sign` | 제어 일시정지 | 마우스 조작 일시정지 / 재개 (`control.v_sign`) | 검지와 중지를 펴서 V자 모양을 1.5초간 유지 |
| **9** | `rock_sign` | 창 닫기 | 현재 활성 창 닫기 (`window.close`) | 검지와 새끼손가락을 세운 락 사인 (Alt+F4) |

---

## 🚀 빠른 시작 (Quickstart)

### 1. 데스크톱 앱 실행
```powershell
uv run --locked --python 3.12 --extra ui --extra vision --extra ml python -m manux
```
- **화면 및 웹캠 확인**: MediaPipe Hand Landmarker와 Qt/QML 대시보드가 구동됩니다.
- **웹캠 없이 녹화 파일 재생**:
  ```powershell
  uv run --python 3.12 --extra ui python -m manux --replay <녹화.jsonl.gz> --no-windows-input
  ```
- **자가 진단 (Self Check)**:
  ```powershell
  python -m manux --self-check out.json
  ```

### 2. Spatial MLP 모델 원클릭 학습 (1.5초)
내장된 17,000+ 골드 스냅샷 샘플을 기반으로 96차원 Spatial MLP를 즉시 재학습합니다:
```powershell
uv run --python 3.12 python tools/train_spatial_mlp.py
```
학습 결과는 `models/spatial_mlp/active_model.json`에 저장되어 앱 실행 시 기본 탑재됩니다.

### 3. 단위 테스트 실행 (890개 전체 검증)
```powershell
uv run --locked --python 3.12 pytest -q
```
*클릭 1:1 에뮬레이션, 드래그 유지, 우클릭 드래그, 양쪽 클릭, Spatial Guard, 스튜디오 전 기능 890개 테스트가 100% 통과하도록 검증됩니다.*

---

## 📁 주요 문서 링크

1. [AGENTS.md](AGENTS.md): 작업 규칙과 기능 추가 절차
2. [docs/architecture.md](docs/architecture.md): 폴더 구조, 프레임 파이프라인, 모듈 의존성
3. [docs/contracts.md](docs/contracts.md): Intent, Observation, Output Sink 간 인터페이스 계약
4. [docs/advanced-settings.md](docs/advanced-settings.md): 감도 및 고급 설정 제어
5. [docs/eval-judges.md](docs/eval-judges.md): 규칙 vs MLP vs TCN 판정기 벤치마크
6. [docs/dataset-hub.md](docs/dataset-hub.md): 제스처 데이터 수집 및 허브 동기화 가이드

---

## 🛠️ 패키징 및 빌드

```powershell
# 기본 프리뷰 로컬 빌드
./packaging/build.ps1 -Version 0.5.0-local

# 고급 학습 컴포넌트 포함 빌드
./packaging/build.ps1 -Version 0.5.0-local -WithTrainer

# 릴리즈 채널 빌드
./packaging/build.ps1 -Version 0.5.0 -Release
```
정식 릴리즈는 `manux-production` 브랜치 push 시 GitHub Actions 워크플로우를 통해 자동으로 배포됩니다.
