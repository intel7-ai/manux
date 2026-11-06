# 규칙·MLP·TCN 재생 비교

`tools/eval_judges.py`는 같은 독립 평가 녹화를 다섯 판정 후보에 재생한다. 모델을 설치하거나 Windows 입력을 실행하지 않는다. 합성 시험 통과는 실제 정확도 측정이 아니다. 이번 구현에서는 실제 사용자 녹화를 평가하지 않았다.

## 실행

`apps/manux`에서 실행한다. 녹화와 출력은 저장소 밖에 둔다.

```powershell
uv run --locked --python 3.12 --extra train python tools/eval_judges.py --train D:/manux-data/train-a.jsonl.gz D:/manux-data/train-b.jsonl.gz --eval D:/manux-data/eval-c.jsonl.gz --split session --features angles33 --train-seconds 10,30,60 --out D:/manux-results/angles33
uv run --locked --python 3.12 --extra train python tools/eval_judges.py --train D:/manux-data/train-a.jsonl.gz D:/manux-data/train-b.jsonl.gz --eval D:/manux-data/eval-c.jsonl.gz --split session --features spatial96 --train-seconds 10,30,60 --out D:/manux-results/spatial96
```

동일한 파일 목록·분할·학습량·임계값으로 두 번 실행해 특징을 비교한다. TCN은 두 실행 모두 기존 `local3d-v1`, 133차원, 15프레임을 쓴다. `--features`는 MLP만 바꾼다. MLP에는 torch가 필요 없지만 다섯 판정기를 모두 비교하므로 실행 도구에는 `train` 선택 의존성이 필요하다. TCN은 기존 비교 도구와 같은 CPU 학습·추론이다.

`--mode pinch|pointer`는 현재 규칙 엔진의 시작 모드다(기본 pinch). 하나의 preset으로 수집한 파일만 함께 쓴다. pointer 데이터는 `--mode pointer`로 실행한다. 녹화 당시의 임계값이나 개인 설정은 자동 재현하지 않으며 현재 preview 기본 설정을 쓴다. `--rules recorded`를 지정하면 현재 엔진 재생 대신 녹화에 저장된 대표 규칙 판정을 비교한다. 해당 선택을 현재 규칙의 성능이라고 부르지 않는다.

출력은 `results.json`, `results.md`다. 현재 checkout·공유 저장소·다른 worktree 내부 경로, 그 안을 가리키는 링크 경로도 거절한다. 보고서는 관절 원본·별명·원본 경로를 포함하지 않으며 파일 해시와 그룹 해시를 남긴다. 같은 출력 폴더로 다시 실행하면 기존 두 보고서를 덮어쓴다.

## 독립 분할과 학습량

- `--train`과 `--eval`은 별도 녹화 파일 목록이다. 지정된 `meta.split`이 역할과 다르면 오류다. 오래된 녹화의 split 누락은 허용한다.
- 동일 경로·바이트 해시·정규화한 프레임 내용이 학습과 평가에 겹치면 오류다. gzip 재압축이나 파일명·meta 변경만 한 복제도 거절한다. 각 목록 안의 중복도 거절한다.
- `session`: `meta.session_id` 또는 `meta.session`을 그룹으로 쓴다. 없으면 녹화 파일 하나를 세션 하나로 간주한다. 같은 세션을 여러 파일로 나눴다면 반드시 공통 session_id를 기록해야 한다.
- `condition`: 기존 `label_condition_key`의 물리 조건 해시를 쓴다. 이전 녹화는 `legacy-<condition_index>`다. `person`: 별명 해시를 쓴다. 해당 그룹 정보가 없으면 오류다.
- 평가 그룹을 하나씩 빼고, 그 그룹 및 같은 세션의 파일을 학습 목록에서 통째로 제외한다. 평가 파일이 여러 조건을 포함하면 그 파일의 **모든 조건**과 겹치는 학습 파일을 제외한다. 파일 안의 일부 조건만 잘라 양쪽에 넣지 않는다. 따라서 조건 분리가 안 된 수집에서는 학습 파일이 남지 않아 오류가 날 수 있다. 각 fold는 독립 표로 제공하며 겹칠 수 있는 fold의 점수를 합산하지 않는다.
- 프레임 무작위 분할은 없다. 가져온 `pose_data`의 구간 분할/중복 검사 중 파일 중복 검사를 재사용한다. 더 약한 구간 무작위 holdout은 CLI에서 제공하지 않는다. MLP 내부의 80/20 무작위 분할도 제거했다. epoch 안의 학습 순서 섞기는 데이터 분할이 아니다.
- 동작마다 최대 10/30/60초의 유효 학습 노출을 선택한다. `none`에도 같은 한도를 적용한다. 파일 내용 해시 순서·프레임 시간 순서의 앞부분을 쓰며 부족하면 실제 사용량을 함께 표시한다. 프레임에서 다음 프레임까지의 간격을 합산하고 마지막 프레임·0.3초 초과 공백은 0초다. 한도보다 긴 다음 프레임은 선택하지 않는다.
- 선택되지 않은 구간을 건너뛰어 시계열을 붙이지 않는다. 선택된 연속 조각마다 local3d/window를 다시 계산한다. TCN은 기존 `samples`의 왼쪽 zero padding 정책으로 학습한다. 스와이프 방향과 유효 세계 좌표 검사는 기존 학습 도구와 같다. 평가 때는 파일 전체를 연속 재생하며 정답 라벨·시도 경계에서 판정기를 초기화하지 않는다.
- 평가에만 있는 미학습 클래스·클래스별 학습 표본 부족·모드 진입/종료 클래스는 오류다. 제품 명령이 아직 없는 동작은 label.gesture를 명시해야 비교할 수 있으며 규칙에서는 미검출로 남는다. 실제 수집 지시를 어긴 라벨을 도구가 정답으로 보정해 주지는 않는다.

## 판정 후보와 측정 경계

| 후보 | 정의 |
|---|---|
| rules | 현재 preview의 `build_engine`으로 관절을 다시 재생, 필터 후 tap/begin 의도 |
| mlp | 선택한 단일 프레임 특징 → NumPy MLP → 공통 확정 정책 |
| tcn | `compare_tcn.train_multi`의 학습기·증강 → 직접 클래스 판정 → 공통 확정 정책 |
| rules_mlp_veto | 규칙의 시작 시점에 MLP가 다른 클래스를 veto 임계값 이상으로 확신하면 제거. 손/특징이 없으면 제거. 새 명령을 만들거나 뒤늦게 시작시키지 않음 |
| mlp_shape_tcn_motion | 동작 ID가 `motion.`인 클래스는 TCN 담당, 나머지는 MLP 담당. 확정 가능한 TCN 움직임을 우선하고 MLP의 움직임 판정으로 우회하지 않음 |

MLP와 TCN은 각 fold·학습량에서 한 번씩 학습하고 조합 후보가 같은 모델을 공유한다. MLP 구조·Adam은 testing 원본이며, TCN은 비교 도구 원본이다. 혼합 후보는 별도 두 모델을 재학습하는 비용이 아니라 두 모델의 학습 비용 합계를 표시한다. 모션 클래스가 없는 녹화에서는 혼합의 이점을 측정할 수 없으며 `motion_classes`가 빈 목록으로 남는다.

학습 판정 후보의 공통 정책 `offline-neutral-rearm-v1`: 기본 확률 0.7·연속 3프레임, `none` 연속 확정 뒤에만 새 시작, 시작 뒤에는 다시 중립 필요. `tcn_data.predicted_events`의 연속 검출을 재사용한다. 손/세계 좌표 소실이나 0.3초 초과 공백에서 상태를 끊는다. TCN은 유효 15프레임이 쌓인 뒤 확정한다. 이 후보 비교는 앱의 중재기·모드별 억제·바인딩·span 유지/해제까지 재현하는 직접 TCN 통합 시험과 구분한다. 현재 규칙은 실제 엔진을 쓰므로 규칙 고유의 재시작 정책을 유지한다.

## 지표

| 지표 | 계산 |
|---|---|
| 동작별 재현율 | target/task/set/rep/condition이 같은 연속 구간을 시도로 보고 그 안에 정답 시작이 한 번이라도 있으면 적중 1회. 공백 0.3초 초과는 다른 시도. 적중/시도와 비율 함께 제공 |
| 부정 구간 분당 오작동 | target=none의 모든 명령 시작 / 실제 none 노출 분. 유지 프레임은 제외. 노출이 0이면 null |
| 혼동표 | 행은 정답 클래스, 열은 발생한 명령 시작. 반복/잘못된 시작을 모두 남김. 정답 시작이 없는 시도는 miss 열에도 1을 남김. 따라서 행 합계는 시도 수와 다를 수 있음 |
| 지연 중앙값·p95 ms | 적중한 시도의 첫 프레임 → 첫 정답 명령 시작, 모든 시도의 표본을 합쳐 계산. 누락 시도는 지연에서 제외하므로 재현율과 함께 읽음. 적중이 없으면 null |
| 떨림 | 한 시도의 모든 명령 시작 수에서 첫 시작을 뺀 수. 다른 동작으로의 반복 시작도 포함 |
| 학습 시간 | 학습 호출의 실제 wall time. 첫 TCN에는 torch 지연 import 비용도 포함될 수 있음 |
| 프레임당 추론 ms | batch=1 순차 추론 호출의 평균과 p95. MLP·TCN은 캐시된 특징 이후의 모델 호출, 규칙은 engine.step 시간. 조합은 구성 호출 시간 합계. 특징 추출·확정/조합·파일 I/O·카메라·OS 지연은 이 값에 포함하지 않음 |

unknown/transition/idle 구간의 시작은 `ignored_starts`에 따로 남긴다. 부정 구간으로 임의 해석하지 않는다. 모델이 학습하지 않은 다른 규칙 명령도 버리지 않고 `unmodelled:<gesture>`로 혼동표·오작동에 포함한다. 단일 프레임 포인터 이동 hold는 명령 시작 지표에 포함하지 않는다. 추론 시간은 지연과 의미가 다르고, 현재 규칙의 engine.step에는 중재·라우팅 비용도 있어 모델 호출 시간과 직접적인 속도 우열을 주장할 수 없다.

## 출처와 검증

- 기준 preview: `d552c4678c83bd6af8ebc08fe4de1b86fd3e07de`.
- 33차원: `9b20d0de9344ba8fe1a2623aaa9e02d183768a2c`의 `perception/pose_angles.py`, 파일 그대로 보존. 구간 분할·중복 검사만 같은 커밋의 `tools/pose_data.py`에서 가져옴. 원본 브랜치: `pose-assist-v1-33dim`.
- 96차원: `ac505c9e3c34005e07c39c2d25023a79d27eec83`(PR #82)의 `perception/spatial_features.py`, 파일 그대로 보존. 33차원은 세계 좌표, 96차원은 원본이 정의한 영상 관절 좌표를 사용하므로 차원 수뿐 아니라 좌표계·정규화도 다르다.
- MLP: `431f9b0b8edbd3050590b2ddf4ceacf6e48e4e0b`(origin/testing)의 NumPy 학습·추론. 33차원·동적 클래스 허용, 내부 분할 제거, 입력 유효성 검사만 보강. studio·설치 기능은 가져오지 않음.
- 재사용: `data/rule_replay`의 녹화 규칙→시작 변환, `tools/replay`와 같은 read/summary, `tcn_data`의 local3d·학습 window·연속 확정, `compare_tcn`의 TCN 학습, `ml/card`의 모델 정보 검증.
- `tests/unit/data/test_eval_judges.py`는 합성 녹화만 쓴다. CI의 통합 시험은 TCN 예측기를 대역으로 교체해 torch 없이도 실행한다. 실제 torch 학습은 별도 합성 smoke 시험으로 구분한다. 실제 손짓 정확도·다인 일반화·Windows 사용성은 아직 측정하지 않았다.
