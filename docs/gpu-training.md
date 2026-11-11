# GPU 학습 (2026-11-09) — AI agent용 메모

한 줄 요약: **GPU로 학습하면 인식이 급락하던 문제의 원인은 GPU가 아니라 `train_tcn.py`가 GPU일 때 배치 크기·학습률을 몰래 바꾸던 것이었다.** 지금은 GPU도 CPU와 같은 설정(배치 256, lr 1e-3)으로 학습한다. GPU torch는 프로젝트 `.venv`가 아니라 **별도 환경**에 있다.

## 1. 증상
같은 데이터, 같은 시드(`--mode fast --test-condition 2 --no-export`)로 평가 자세 하나의 인식(/105):

| 조건 | 인식 | 학습 시간 |
|---|---|---|
| CPU (시드 0 / 1) | 88 / 86 | 74~85초 |
| GPU 옛 기본값 (시드 0,1,2,3) | 53 / 55 / 58 / 41 | 25~32초 |
| GPU, 배치 256 + lr 1e-3 (시드 0 / 1) | 86 / 88 | 38~39초 |

GPU의 오작동 수가 적게 나온 것(13~26)은 좋아진 게 아니라 모델이 덜 반응한 결과다. 인식과 같이 읽을 것.

## 2. 원인 (`scripts/train_tcn.py`, 학습 함수 안 `use_large_batch`)
GPU이고 학습 창이 4096개 이상이면 배치 4096 / lr 2e-3으로 바꿨다. 에폭 수는 그대로라 업데이트 횟수가 약 1/16로 줄어 **덜 학습된 모델**이 나왔다. 확인한 근거:
- GPU 환경에서 `--device cpu`로 돌리면 CPU와 같은 결과(시드 0은 88/105, 오작동 42로 완전 일치) → torch 버전·환경 문제 아님.
- GPU에서 배치를 256으로 고정하면 86~88로 복구 → 배치/학습률이 원인.
- 시드 4개가 모두 낮았다 → 우연이 아님.

## 3. 지금 코드
- `use_large_batch`는 환경변수 `TCN_LARGE_BATCH=1`일 때만 켜진다(옛 동작을 실험할 때). 기본은 GPU도 배치 256, lr 1e-3.
- `import os`를 추가했다.
- 시험용으로 잠깐 넣었던 `TCN_SMALL_BATCH`는 없앴다.

## 4. 환경: 왜 별도 환경인가
- `uv run --locked` / `uv sync`(`run.bat`, `studio.bat`에 들어 있음)는 `.venv`의 torch를 잠금 파일 기준 `2.15.0+cpu`로 **되돌린다**. 확인했다: `.venv`에 CUDA torch를 깔아도 다음 `uv run`에서 CPU로 돌아온다.
- 그래서 GPU torch는 `..\hand-mouse-gpuenv`(프로젝트 폴더와 같은 위치의 형제 폴더, 저장소에 안 올라감)에 둔다. 환경변수 `HAND_MOUSE_GPU_ENV`로 위치를 바꿀 수 있다.
- 만든 방법(참고):
  ```
  uv venv ..\hand-mouse-gpuenv --python 3.12
  uv pip install --python ..\hand-mouse-gpuenv\Scripts\python.exe -r pyproject.toml --extra qt --extra train
  uv pip install --python ..\hand-mouse-gpuenv\Scripts\python.exe --reinstall --index-url https://download.pytorch.org/whl/cu130 torch
  ```
  설치된 torch는 `2.14.1+cu130`(프로젝트 요구 `>=2.15.0`보다 낮음. 학습은 정상 동작 확인).
- 이 PC: NVIDIA GeForce RTX 3050 (6 GB), 드라이버 610.88, CUDA 13.3.

## 5. 코드 연결 (`src/training_center.py`, `studio.py`)
- `gpu_training_python()` / `training_python()`: 별도 환경이 있으면 그 Python, 없으면 `sys.executable`.
- 학습 하위 프로세스(`scripts/train_tcn.py`)는 `training_python()`으로 실행. 앱 자체는 계속 `.venv`.
- `detect_gpu_training_capability()`는 별도 환경이 있으면 그 환경의 torch 버전(`version.py`)을 읽어 GPU 가능 여부를 정한다. 화면에 "GPU 가속 가능 (NVIDIA GeForce RTX 3050 · 6.0GB)"로 나온다.
- `studio.py` 4번(TCN 학습)도 같은 Python을 쓴다.
- **앱의 "GPU 설치" 버튼(`install_cuda_torch`)은 누르지 말 것.** `.venv`를 바꾸지만 다음 `uv sync`에 되돌려진다.

## 6. 속도
작은 모델이라 이득은 약 2배다(74~85초 → 38~39초). 배치를 키우면 더 빠르지만 위 문제가 생긴다. 큰 배치를 쓰려면 에폭 수나 학습률 스케줄을 함께 조정하고 같은 자세로 인식을 다시 확인할 것.

## 7. 조사할 때 먼저 볼 것
1. GPU 학습 결과가 CPU보다 나쁘면 로그의 `device=`와 `use_large_batch` 환경변수(`TCN_LARGE_BATCH`)부터 확인.
2. `uv run`/`uv sync` 뒤에 `.venv`의 torch가 CPU로 돌아오는 것은 정상 동작이다.
3. 별도 환경 Python으로 `-c "import torch; print(torch.cuda.is_available())"` 확인.

## 8. 한계
- 비교는 평가 자세 하나(2번), 시드 2~4개다. 자세 18개 전체(`--folds`)에서 GPU와 CPU를 비교하지는 않았다.
- GPU 소배치에서 "다른 동작으로 착각"이 CPU보다 높게 나왔다(6, 9 대 2~4). 시드 2개라 잡음일 수 있으나 확인하지 않았다.
- PC가 같은 날 PCIe 오류 기록 뒤 강제 종료된 적이 있다(`WHEA-Logger 17`, PCI Express Root Port). 원인은 확정되지 않았다. GPU 학습을 오래 돌릴 때 같은 증상이 나는지 지켜볼 것.
