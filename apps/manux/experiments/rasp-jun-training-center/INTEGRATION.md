# Rasp-Jun 학습센터 testing 통합

원본: `codex/rasp-jun-training-center@f1a7a23a4462c77f5bf3790a140e4b8bc72d7ca6`.
testing 기준: `5de8913055e0b520eaa1ed1c8cc4101140328e4c`.

원본 브랜치의 이력을 병합하고 실행 소스를 이 폴더에 보존했다. v0.4 본체와 Dan 실험 앱은
각각 기존 위치에 유지한다. `python -m manux`로 여는 v0.4 화면에 아래 기능을 이식한 것은
아니다. 이 폴더의 `tcn_app.py`가 해당 학습센터의 진입점이다.

## 포함 기능

- 18개 과제의 조건별 집중 녹화, 짧은 클릭 수집 계획과 준비/동작 구간 구분.
- 원하는 동작만 선택해서 학습하고 다른 동작의 모델을 보존하는 선택 학습.
- 학습센터에서 진입하는 진행 화면: 진행률, epoch/loss, 경과/남은 시간, 완료 예상 시각, 취소.
- 동작별 규칙/시험/적용 선택, 새 모델 시험 모드, 시계열 전처리 일치와 조건별 균형 학습.
- 기존 선택 모델 형식 호환, 성능이 무너진 후보의 자동 적용 방지, 실제 액션 재생 평가.
- 저장된 카메라 선택 및 Windows 카메라 backend 설정.

## 별도 실행

이 사본은 소스 통합이다. 개인 녹화·모델·실패 로그·녹화 기반 fixture·인증·로컬 상태는
포함하지 않았다. 원본 PC에서 사용하던 `models/`를 이 폴더의 로컬 `models/`에 준비하거나
검증된 모델을 별도로 제공받아야 한다. 선택 학습을 사용했다면 ONNX뿐 아니라 대응 JSON,
`gesture_tcn.selected.json`과 그 파일이 참조하는 `selected/` 하위 경로도 함께 유지한다.
기본 손/자세 추적용 `.task` 파일도 필요하다. 이 파일들은 Git에서 무시된다.

이 폴더에서 실행한다.

```powershell
uv sync --locked --extra qt --extra train --python 3.12
.venv/Scripts/python.exe tcn_app.py --check
.venv/Scripts/python.exe tcn_app.py
```

카메라 번호·backend는 원본 PC의 설정을 포함하므로 각 PC에서 `config/gestures.json`을
확인한다. v0.4 앱과 동시에 카메라나 Windows 입력을 사용하지 않는다. 개인 녹화 공유는
저장소의 [허브 안내](../../docs/dataset-hub.md)에 따른다.

## 검증과 한계

원본 소스에서 자동 시험 483개를 통과했다. 모델과 녹화 fixture가 없는 새 checkout에서는
전체 원본 시험을 그대로 재현할 수 없다. 필요한 로컬 자료를 준비한 뒤 이 폴더에서
`python -m pytest -q --basetemp=.local/pytest-check`로 실행한다. 검증된 파일의 해시는
`UPSTREAM-SOURCE.json`에 있으며 원본 blob과 대조했다. 기존 7개 파일의 행 끝 공백/파일 끝
빈 줄만 정리했고 해당 파일에는 원본 해시와 사본 해시를 모두 기록했다.

자동 시험은 실제 웹캠/Windows 입력의 인식률을 뜻하지 않는다. 좌클릭 후보의 기존 녹화
재생에서는 규칙 23/90, TCN 경로 23/90으로 실제 클릭 개선이 확인되지 않았고, 새 모델은
시험 모드에서 시작한다. 다양한 각도·사람·빠른 클릭의 실측은 별도 수집이 필요하다.
