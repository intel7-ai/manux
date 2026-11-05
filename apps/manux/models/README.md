# 번들 모델

| 파일 | 출처 | SHA-256 |
| --- | --- | --- |
| `hand_landmarker.task` | MediaPipe Hand Landmarker (float16), `manux.tracking.landmarker.MODEL_URL` | `fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1` |

v0.3(`hand-mouse/models/`)과 같은 파일이다(같은 Git blob). 설치본(`packaging/manux.spec`)에 들어가고, 소스 실행(`python -m manux`)도 `--hand-model` 없이 이 파일을 쓴다.
학습한 TCN 모델은 여기에 두지 않는다. 개인 녹화로 만든 모델은 [D-006](../docs/decisions.md)에 따른다.
