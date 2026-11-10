# MANUX (v0.5)

<p align="center">
  <img src="https://img.shields.io/badge/Runtime-Python_3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Runtime" />
  <img src="https://img.shields.io/badge/Desktop-Qt6_QML_(PySide6)-41CD52?style=flat-square&logo=qt&logoColor=white" alt="Desktop" />
  <img src="https://img.shields.io/badge/AI_Vision-MediaPipe_Tasks-0078D4?style=flat-square&logo=google&logoColor=white" alt="MediaPipe" />
  <img src="https://img.shields.io/badge/Custom_AI_Model-SpatialPoseMLP_(Zero--GC)-8A2BE2?style=flat-square" alt="Custom AI Model" />
  <img src="https://img.shields.io/badge/Trained_Samples-17%2C689_Gold_Dataset-success?style=flat-square" alt="Trained Samples" />
  <img src="https://img.shields.io/badge/Inference_Speed-0.003ms_(3.0μs)-yellow?style=flat-square" alt="Inference Speed" />
  <img src="https://img.shields.io/badge/Unit_Tests-745_Passed-brightgreen?style=flat-square" alt="Unit Tests" />
  <img src="https://img.shields.io/badge/Platform-Windows_x64-0078D6?style=flat-square&logo=windows&logoColor=white" alt="Platform" />
</p>

웹캠을 통해 Windows 마우스와 제스처를 손짓만으로 정밀하게 제어하는 AI 휴먼 인터페이스 앱입니다.
단일 프로세스 내에서 **PySide6(Qt/QML)** UI, **Google MediaPipe** 3D 손 랜드마크 추적, 그리고 초경량 **96차원 Spatial MLP(3D 공간 신경망)** 모델이 센서 퓨전(Sensor Fusion)되어 극저지연(3.0µs)의 안정적인 제스처 마우스 경험을 제공합니다.

---

## ⚡ v0.5 핵심 혁신 & 병목 해결 (Key Innovations)

### 1. 3D Spatial MLP & Sensor Fusion 레이어 (`SpatialPoseGuard`)
- **2D 한계 극복**: 카메라 각도 왜곡과 원근감에 취약했던 기존 2D 유클리드 좌표 판정에서 벗어나, 3D 월드 랜드마크 기반 **96차원 공간 기하학 특징(Spatial Features)** 추출.
- **초경량 Zero-GC 인퍼런스**: 순수 NumPy 행렬 연산으로 **0.003ms (3.0µs)** 추론 속도를 달성하여 프레임 드랍 없는 실시간 보정 수행.
- **센서 퓨전(Sensor Fusion)**: 2D 물리 규칙과 Spatial MLP 판별을 융합하여, 손 각도가 비틀리거나 가로로 누워도 99% 확신도로 `thumb_middle`(좌클릭/드래그), `thumb_index`(스크롤), `thumb_ring`(우클릭)을 판정.

### 2. 마우스 조작 안정화 및 실전 병목 해결
- **의도치 않은 더블클릭 오발화 차단**: 중지를 맞대고 유지할 때 윈도우 클릭이 연타되지 않도록 마우스 다운 디바운스 및 안정적 홀드 상태 전환 (`defer down until motion/hold`).
- **바탕화면 / 파일 드래그 안정화**: 드래그 도중 손 떨림으로 2D 좌표가 1~2프레임 튀더라도 MLP가 자세 유지를 확인하면 드래그를 조기 종료하지 않고 유지(Sustain).
- **스크롤 휠 감도 및 관성 최적화**: 기본 휠 게인을 1/4(8,000)로 최적화하고 손 정지 후 릴리즈 시 튕김 방지. GUI 설정 슬라이더 제공.
- **가로 손 자세(Horizontal Posture) 스크롤 차단 해제**: 손을 눕혀 쥐었을 때 2D 투영 왜곡으로 인한 억제 조건을 완화하고 Spatial Guard 스크롤 어시스트 연결.
- **손 모양 적응형 속도 스케일링 (Adaptive Shape DPI)**: 편 손은 정밀/저속(0.6x), 쥔 손/주먹은 고속(2.0x)으로 쾌적한 화면 이동, 드래그 중에는 1.0x 락으로 안정성 유지.

### 3. 데이터셋 및 원클릭 초고속 학습 파이프라인
- **내장 골드 데이터셋**: `data/pose_snapshots` (26개 세션, 17,689개 실전 제스처 스냅샷) + `data/legacy`
- **1.5초 초고속 재학습**: `python tools/train_spatial_mlp.py` 단 한 줄로 전체 데이터셋을 1.5초 만에 학습하여 `models/spatial_mlp/active_model.json` 생성.

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
내장된 17,000+ 스냅샷 샘플을 기반으로 96차원 Spatial MLP를 즉시 재학습합니다:
```powershell
uv run --python 3.12 python tools/train_spatial_mlp.py
```
학습 결과는 `models/spatial_mlp/active_model.json`에 저장되어 앱 실행 시 기본 탑재됩니다.

### 3. 단위 테스트 실행 (745개 전체 검증)
```powershell
uv run --locked --python 3.12 pytest tests/unit/ -q
```
*모든 핵심 제스처(클릭, 드래그, 스크롤, 보간기, 설정 스토어)가 100% 통과하도록 유지됩니다.*

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
