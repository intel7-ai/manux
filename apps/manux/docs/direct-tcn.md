# TCN 직접 판정과 개인 모델 교체

2026-11-05 구현. 이 기능은 **명시적으로 켜는 개발 경로**다. 기본 실행은 기존 규칙이며,
`--tcn-model`만 주면 기존 확인기다. 실제 사용자 인식률·Windows 사용성은 아직 확인하지 않았다.

## 달라진 경로

`모델 클래스 → 동작 ID → 별도 바인딩 → 액션`으로 실행한다. 모델 정보의 `gestures`와
`decisions`가 판정 의미를 정하고, 액션 이름은 모델 안에 넣지 않는다. 모델이 맡은 동작을
거절하면 규칙으로 대신 실행하지 않는다. 확인·유지 중에는 다른 규칙의 새 명령과 커서 이동을
막아 같은 손 자세에서 두 명령이 나가지 않게 한다. 중립 복귀 전 tap을 반복하지 않는다.

새 사용자 동작은 `custom.rock`처럼 선언할 수 있다. 바인딩이 없으면 실행하지 않고 Snapshot에
`unbound`를 남긴다. 바인딩 편집 화면·학습 센터는 아직 없으므로 사용자 폴더의 JSON을 사용한다.

## 1. 학습

팀 녹화 수집은 진행 중이다. HF 공용 데이터 저장소와 Gemini 업로드 스크립트는 별도 준비 중이며,
새 녹화를 이 Git 저장소에 추가하지 않는다. 내려받은 완료 녹화는 사용자 폴더에 두고 기존 학습
도구에 경로로 전달한다. 이 앱은 HF로 자동 업로드하거나 다운로드하지 않는다.

`decisions.json` 예시(학습 클래스가 `none / thumb_index / thumb_ring`인 경우):

```json
{
  "thumb_index": {"kind": "span", "modes": ["pinch"], "point": "palm"},
  "thumb_ring": {"kind": "tap", "modes": ["pinch"], "point": "none", "threshold": 0.8, "frames_in_a_row": 3}
}
```

`span`은 begin/hold/end, `tap`은 중립 복귀 사이에 한 번이다. 기준점은 `none`, `palm`, `index`이며
span에는 기준점이 필요하다. 클래스별 임계값·연속 수는 생략하면 전체 모델 값을 쓴다.
`modes`는 `pinch`, `pointer`, `motion` 중 선택한다. 모션 진입/종료는 기존 규칙·중재기가 담당한다.

```powershell
cd apps/manux
$data = "$env:APPDATA/MANUX-dev/v04"
$train = (Get-ChildItem "$data/recordings/train/*.jsonl.gz").FullName
uv run --python 3.12 --extra train python tools/train_tcn.py $train --decisions "$data/decisions.json" --output "$data/candidate.onnx"
```

녹화 라벨의 `gesture`에 클래스별 동작 ID를 넣는다(기존 핀치 클래스는 이전 대응표도 지원).
새 동작 클래스에는 명시적 ID가 필요하다. `decisions`는 `gestures`의 모든 클래스에 하나씩 선언한다.
학습 도구는 특징을 사용자 폴더 `features/`에 `.npz`로 캐시한다. 녹화 SHA256·특징 버전·구간·
화면 비율이 바뀌면 다시 계산하며 손실/손상된 캐시는 다시 만든다. 원시 녹화를 캐시에 복사하지 않는다.

## 2. 별도 시도로 확인하고 설치

```powershell
$eval = (Get-ChildItem "$data/recordings/eval/*.jsonl.gz").FullName
uv run --python 3.12 --extra ml python tools/promote_tcn.py "$data/candidate.onnx" $eval --store "$data/models/personal"
```

평가는 실제 `TcnFeature`를 재생하여 중립 재무장·클래스별 기준을 적용한 tap/begin을 센다.
평가 파일은 명시적 `split=eval`이어야 하며 현재/새 모델의 학습 파일 해시와 겹치면 거부한다.
모든 동작에 별도 평가 시도와 적중이 있어야 하고, 기존 동작 적중 수·중립 오실행률·다른 동작을
잘못 낸 횟수가 현재 모델보다 나빠지면 교체하지 않는다. 최초에는 녹화의 규칙 기록과 비교한다.
중립 노출이 없거나 규칙 기록이 필요한데 없으면 설치하지 않는다. 기존 동작 삭제·클래스 ID 변경도 거부한다.

통과 시 `versions/<버전>/`에 모델·정보·검증 보고서를 먼저 저장한 뒤 `active.json`만 원자적으로
교체한다. 실패·취소·디스크 오류·다른 설치와의 충돌은 이전 active를 보존한다. 중단된 설치의
미참조 버전은 자동으로 활성화되지 않는다. 새 모델이 모든 사람에게 일반화된다는 의미는 아니다.
평가 데이터나 기준을 임의로 바꿔 통과시키지 않고, 수집이 부족하면 모델을 활성화하지 않는다.

## 3. 연습·데스크톱 연결

헤드리스 개발 확인(연습 출력만):

```powershell
uv run --python 3.12 --extra ml python -m manux.app --replay <녹화> --mode pinch --tcn-mode direct --tcn-model "$data/candidate.onnx"
```

개인 모델 파일을 데스크톱에서 직접 확인하려면 `--no-windows-input`이 필요하다. 실제 Windows
입력은 검증된 store만 사용한다. 시작 후에도 튜토리얼·일시정지·등록·녹화 중에는 Windows 입력이 꺼진다.

```powershell
uv run --python 3.12 --extra ui --extra vision --extra ml python -m manux --tcn-mode direct --tcn-store "$data/models/personal" --hand-model <hand_landmarker.task>
```

사용자 동작 바인딩 JSON 예시: `{"custom.rock": "nav.back"}`. `--bindings "$data/bindings.json"`으로
전달한다. 알려진 동작은 기존 기본 바인딩을 유지한다. 액션·phase가 맞지 않으면 시작을 거부한다.

데스크톱은 store 포인터 변경을 프레임 경계에서 확인하고 파일 읽기 전에 출력 해제·판정 상태 reset을
한다. 새 파일·해시 검사를 통과하면 새 판정기로 바꾼다. 갱신 실패 시 기존 메모리 모델을 유지하며
화면 상태 문구에 알린다. 두 경우 모두 중립부터 다시 확인한다. 앱을 다시 시작할 필요 없이 다음 설치를 읽는다.

## 남은 단계

- 학습 센터와 바인딩 편집 화면, 사용자 카탈로그 편집.
- 팀 데이터의 사람 분리 평가, 실제 웹캠·Windows 입력 확인.
- 기본 모델 릴리스 기준 확정, v0.4 설치본·자동 업데이트 전환.

자동 시험은 합성 손·가짜 예측기 및 선택적 작은 ONNX 그래프로 실행 경로와 안전 조건을 확인한다.
시험 수나 합성 평가 적중 수를 제품 정확도로 사용하지 않는다.
