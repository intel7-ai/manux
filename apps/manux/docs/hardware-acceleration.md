# 선택적 GPU 학습·GPU/NPU 추론

2026-11-06: 기존 [조사 계획 PR #73](https://github.com/intel7-ai/manux/pull/73)의 순서로 구현했다.
기본 설치본 환경은 CPU다. 소스의 별도 CUDA/OpenVINO 환경에서 실제 장치 실행을 확인했다.
TCN 가속과 MediaPipe 카메라·손 추적 가속은 별개다. torch는 계속 도구·별도 worker에만 있다.

## 장치 선택과 사용

- 학습: `--train-device auto|cpu|cuda|xpu` 또는 환경 `MANUX_TRAIN_DEVICE`.
  학습 CLI·비교 도구는 `--device`다. auto는 장치의 순전파·역전파·Adam 검사를 통과한
  CUDA → XPU → CPU 순서로 선택하고 CPU 선택 이유를 남긴다. 명시 장치 실패는 작업 실패다.
- 추론: `--inference-device cpu|cuda|openvino-cpu|openvino-gpu|openvino-npu|auto` 또는
  `MANUX_INFERENCE_DEVICE`. 기본 cpu, auto도 보수적으로 CPU를 유지한다.
- 앱 설정 키는 `app.training_device`, `app.inference_device`다. 우선순위는 CLI → 환경 → 설정이다.
  worker 진행과 학습 카드에 요청·실제 장치·이름·빌드·전환 사유가 남는다. 실제 추론 장치는
  메인 화면의 모델 상태에서 확인한다. 환경을 설치하는 것만으로 CPU torch가 CUDA torch로 바뀌지 않는다.

학습 환경은 분리한다. 기존 `.venv`·사용자 모델·설정을 변경하지 않는다.

```powershell
cd apps/manux
uv venv --python 3.12 .venv-cuda
uv pip install --python .venv-cuda/Scripts/python.exe --index-url https://download.pytorch.org/whl/cu132 torch==2.15.0
uv pip install --python .venv-cuda/Scripts/python.exe -e ".[ui,vision,ml,train,hub,test]"
.venv-cuda/Scripts/python.exe -m manux --train-device cuda --inference-device cpu
```

해당 CUDA 13.2 wheel·드라이버 조합은 이 PC에서 확인했다. 다른 드라이버는 [공식 PyTorch 설치표](https://pytorch.org/get-started/locally/)를 확인한다.
이 환경을 다시 `uv sync`하면 프로젝트의 CPU 기본 배포 패키지로 바뀔 수 있으므로 명시한 Python으로 실행한다.
현재 testing의 원본 UI를 쓰려면 위 명령에 `--ui studio`를 추가할 수 있다.

OpenVINO는 선택 extra다. CPU torch와 독립적인 추론 런타임을 사용한다.

```powershell
uv run --locked --python 3.12 --extra ui --extra vision --extra ml --extra train --extra hub --extra openvino python -m manux --inference-device openvino-npu
```

명시한 NPU가 없거나 컴파일을 못 하면 CPU로 조용히 바꾸지 않는다. 실제 NPU/GPU만 지정해 컴파일하고
`EXECUTION_DEVICES`를 검사한다([OpenVINO Core](https://docs.openvino.ai/nightly/api/ie_python_api/_autosummary/openvino.Core.html)).
ORT CUDA는 별도의 호환 GPU 배포 패키지가 필요하며 CPU EP 할당·실행 fallback을 막는다
([CUDA EP](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html), [설정 키](https://github.com/microsoft/onnxruntime/blob/main/include/onnxruntime/core/session/onnxruntime_session_options_config_keys.h)).
모델이 입력을 거절하거나 실행 중 오류가 나면 기존 안전 해제·중립 복귀를 따르며 다른 판정기로 재실행하지 않는다.

## 구현과 측정

- 모델을 최적화기보다 먼저 옮긴다. 가중치·라벨·증강 난수·배치는 같은 장치/자료형을 쓴다.
  입력 구간은 CPU에 두고 미니배치만 옮긴다. 비교 도구도 같은 장치 처리·증강을 공유한다.
- ONNX는 CPU 복사본으로 내보내고 학습 장치 예측과 ORT CPU 출력 차이를 검사한다.
  기존 별도 평가·모델 승격·취소 보존 검증은 그대로 수행한다. 장치 간 학습 가중치 동일성을 약속하지 않는다.
- `tools/benchmark_acceleration.py`는 합성 녹화로 장치별 반복 측정을 한다. `--worker`는
  별도 평가·설치를 포함하고, 기본은 학습 CLI·내보내기만 측정한다. `--model`로 같은 모델의 추론을 비교한다.
  준비·전송·동기화한 학습·내보내기, 초기 컴파일, 준비 후 p50/p95를 나눠 기록한다.
- 벤치마크 반복본은 추가 작업량일 뿐 독립 사용자 시도나 인식 정확도 증거가 아니다.
  확률 허용 차이는 0.001이며 같은 녹화의 TCN tap/begin/end 이벤트도 비교한다.

검증 PC: Core Ultra 7 265K, RTX 3050 6GB(드라이버 610.88), Intel Graphics, Intel AI Boost.
CUDA torch 2.15.0+cu132와 OpenVINO 2026.4.1에서 실제 실행했다. 같은 합성 2,880프레임·30 epoch의
학습 CLI+내보내기 중앙값(각 3회)은 CPU 9.03초, CUDA 5.54초였다. 이 수치는 실제 팀 데이터나 설치본 성능이 아니다.
실제 명령 4개가 발생한 동일 모델의 준비 후 p95는 ORT CPU 약 0.040ms, Intel GPU 약 0.186ms,
NPU 약 0.435ms였다. 확률 차이 최대 0.000013 이내, 이벤트는 같았다. 초기 로딩/컴파일은
CPU 약 5ms, GPU/NPU 약 1초로 구분했다. 작은 TCN의 추론 기본은 CPU로 유지한다.


preview 기준 전체 worker(별도 eval·원자적 설치 포함)도 다른 시험과 겹치지 않게 각 3회 측정했다.
중앙값은 CPU 9.50초, CUDA 6.11초로 이 합성 작업에서는 약 36% 줄었다. 이 결과를
다른 데이터량·최신 선택 학습 구성이나 설치본의 보장 성능으로 사용하지 않는다.

최신 testing의 균형 샘플링·선택 학습 worker에서도 같은 합성 작업을 각 3회 측정했다.
별도 eval·설치를 포함한 중앙값은 CPU 10.10초, CUDA 6.73초로 약 33% 줄었다.
이는 아래 NPU 결합 연산 수정 전 측정이며 실제 팀 데이터의 성능 보장은 아니다.
CUDA로 두 분류기를 학습하고 하나만 다시 학습한 뒤, 미선택 분류기 바이트·정책 보존과
독립 eval 부족 시 이전 모델 보존을 확인했다.

두 구성 요소의 동적 `Min`은 이 PC의 NPU 컴파일러에서 실패했다. 새 결합 내보내기는
같은 최솟값을 `Concat → ReduceMin`으로 계산한다. 구성 요소의 가중치와 정책 ID는
그대로이며 새 전체 모델은 기존처럼 전체 별도 eval 후에만 설치한다. 수정 후 두 동작의
같은 합성 평가 녹화에서 Intel GPU와 NPU 모두 CPU와 같은 명령 이벤트(각 4개)를 냈고,
확률 차이는 각각 최대 0.000179·0.000139로 허용 차이 0.001 이내였다.
기존 파일을 로드할 때 몰래 바꾸지는 않는다. 이전 결합 모델은 NPU에서 지원되지 않을 수
있으며, 새 내보내기·별도 평가를 거치거나 명시적으로 CPU를 사용한다.

XPU API 선택은 구현했으나 별도 XPU torch 환경에서 실측하지 않았다. ORT CUDA 추론과
가속 런타임을 넣은 Windows 설치본도 별도 검증 대상이다. NPU는 이 구현에서 추론에 사용하며
PyTorch 역전파 학습 장치로 제공하지 않는다. 실제 웹캠/OS 지연·전력·다인 정확도는 아직 측정하지 않았다.
