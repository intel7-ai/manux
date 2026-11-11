# 온라인 학습 사용·설정·데이터 관리

설계와 근거는 `online-learning-design.md`, 단계별 결과는 `online-learning-phase1~5.md`.

## 켜기
- `config/gestures.json`의 `"online_learning_enabled": true` (이 PC는 켜져 있음). 끄면 수집·등록·학습 화면이 모두 사라진다.
- 처음 한 번: 기존 안내 녹화를 표본으로 바꾸고 기본 모델을 만든다.
  ```
  .venv\Scripts\python.exe scripts/online_import.py
  ..\hand-mouse-gpuenv\Scripts\python.exe scripts/online_train_base.py
  .venv\Scripts\python.exe scripts/online_build_pose.py
  ```
  GPU 환경이 없으면 셋 다 `.venv`의 Python으로 돌려도 된다(느림).
- 앱 실행: `.venv\Scripts\python.exe main.py --ui qt` (손바닥 모드 화면에 기능이 있음).

## 앱에서 할 수 있는 것
| 기능 | 어디서 | 하는 일 |
|---|---|---|
| 정정 | 오작동 직후 **F** → "방금 동작은 무엇이었나요?" | 직전 3초를 고른 동작으로 저장(`user_correction`/`user_confirmed`). "저장 안 함"은 지운다. |
| 새 동작 | 왼쪽 **새 동작 등록** | 5번 시연 → 결과 확인 → 등록. 관절 자세 비교라 바로 인식된다(`class_added`, 신경망 변화 없음). |
| 명령 연결 | 등록 창 → **명령 바꾸기** | 0.5초 유지하면 한 번 실행. 위험한 키 거부, 앱 창이 앞이면 보내지 않음. |
| 학습 | 왼쪽 **학습하기** | 별도 프로세스에서 후보 학습 → 검사 → 통과할 때만 교체. 결과를 문장으로 보여 줌. |
| 되돌리기 | 왼쪽 **되돌리기** | 마지막 교체 전 모델로. |
| 마우스 판정에 쓰기 | 손짓 확인 **켬** → **기존 모델 / 내 모델** | "내 모델"이면 학습한 모델이 클릭·스크롤 시작을 확인한다. 기본은 기존 모델. |

## 안전장치
- 학습 중인 후보는 마우스에 닿지 않는다. 교체는 검사 통과 후, 교체 직후 1초는 확인을 보류한다.
- 내 모델로 확인할 때: 기존 확인기의 연속 프레임·문턱·유지 시간·입력 품질 보류 + 1·2위 차이 0.2 이상.
- 등록·안내 녹화 중에는 OS 입력이 나가지 않는다. **Esc**는 언제든 일시정지(등록 중이면 취소).
- 포인터 이동은 확인하지 않는다(명세대로 클릭·드래그에만 높은 기준).

## 데이터
| 경로 | 내용 | 비고 |
|---|---|---|
| `data/online/index.jsonl`, `samples/*.npz` | 표본(관절·메타데이터). 카메라 영상 없음 | 기본 2GB 상한, 넘으면 라벨 없는 오래된 것부터 삭제 |
| `data/online/encoder.pt`, `encoder.json` | 기본 모델과 검증 세션 목록 | |
| `data/online/versions/` | `production.pt`, `previous.pt`, `candidate.pt`, `versions.json`(이력) | |
| `data/online/pose_memory.json` | 등록 동작의 자세 | |
| `config/gesture_bindings.json`의 `user:<이름>` | 등록 동작의 명령 | |

`data/`는 `.gitignore`에 있어 올라가지 않는다. 손 그림 저장(`rec_save_crops`)은 안내 녹화에만 해당하고 따로 끌 수 있다.

### 지우기
- 등록 동작 하나: 등록 창 → 명령 바꾸기 → **동작 삭제**(자세·시연·명령을 함께 지움).
- 전부 초기화: 앱을 끄고 `data/online/` 폴더를 지운 뒤 위 "처음 한 번"을 다시 한다. 코드로는 `SampleStore().reset()`.
- 학습 모델만 처음으로: `data/online/versions/` 폴더를 지운다(다음 학습 때 기본 모델로 다시 시작).
