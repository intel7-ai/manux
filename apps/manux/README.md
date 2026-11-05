# MANUX v0.4 (apps/manux)

웹캠으로 Windows를 손짓으로 다루는 MANUX의 다음 버전. Qt/QML + Python 한 프로세스이고, MediaPipe 손 인식 위에 규칙 판정과 가벼운 TCN 모델을 같은 계약으로 얹는다.

**현재 상태 (2026-11-05):** 폴더 골격, 계약 코드(`src/manux/core/contracts.py`), 구조·계약 시험만 있다. 실행되는 앱은 아직 없다. 지금 쓰는 설치본은 `hand-mouse/`(v0.3)다.

## 먼저 읽을 것

1. [AGENTS.md](AGENTS.md): 작업 규칙과 기능 추가 절차 (사람과 AI 에이전트 모두)
2. [docs/architecture.md](docs/architecture.md): 폴더·의존 규칙·프레임 흐름·이식 순서
3. [docs/contracts.md](docs/contracts.md): 모듈 사이의 계약
4. [docs/decisions.md](docs/decisions.md): 확정·제안된 결정

## 시험

```powershell
cd apps/manux
uv run --python 3.12 pytest -q
```

카메라·모델 없이 실행된다. 이 시험이 통과해도 손 인식이나 실제 사용 성능을 뜻하지 않는다.
