# 이 브랜치를 이어서 작업하는 Claude에게

## 이 브랜치(`feat/dan-tuning-20261105`)의 출처
- **집 PC에서 폰 카메라로 쓰면서 맞춘 값이다.** 원래 개발하던 PC·웹캠에서 한 작업이 아니다.
  - PC: 집 PC (녹화·모델 정보에는 별명 `home-pc`, 이전 녹화는 코드 `pc-ded5`로 표시. 이 저장소는 공개라 실제 PC 이름·사용자 이름은 적지 않는다), 작업 폴더 `Documents\claude code\hand-mouse-main` (GitHub `main` 4124a0f를 내려받은 것)
  - 카메라: 휴대폰 카메라를 **카메라 번호 0**으로 연결, 1280×720 @ 30fps, 거울 모드, 조작 손 = 오른손 (`--ui qt`, 손바닥(Dan) 모드)
  - 기간: 2026-11-04 저녁 ~ 11-05 새벽
  - 이 카메라에서는 손이 작게 찍혔다: 손 크기(손목→손가락 뿌리 중 가장 긴 것)가 화면 폭의 약 0.09~0.16.
- 판단 근거는 그 PC의 `logs/failures/*.json`(손을 놓치거나 클릭할 때 앞뒤 3초 관절 좌표)을 같은 코드로 다시 돌린 결과다.
- **그 기록의 사본이 `field-logs/2026-11-04-home-pc-phone/failures/`에 있다**(JSON 200개 + `index.csv`, 약 36 MB). 앱은 최근 200개만 남기므로 11-04 22:24 ~ 11-05 00:30 것뿐이고, 그 앞(19시~22시)의 기록은 이미 지워졌다. 영상은 없고 관절 좌표·판정 값만 있다(창 제목·페이지 이름 없음 확인).
  - 다시 돌리는 법: 파일마다 `trigger_t + frames[i].t`가 시각, `frames[i].lm`이 21개 관절(x, y, z, 1280×720 기준 정규화). `GestureEngine.process(lm, timestamp=시각)`에 시간 순서대로 넣고, `engine.frame_aspect = 720/1280`. 파일끼리 시간이 겹치므로 시각으로 중복을 없앨 것.
  - 앱의 `logs/`(`.gitignore`)와 섞이지 않게 일부러 다른 폴더에 두었다. 새 PC에서 쌓이는 기록과 합치지 말 것.
- 한 사람, 한 카메라 배치의 기록이다. 수치는 정확도가 아니라 "같은 기록에서 전후 비교"다. 자세한 근거는 `docs/` 각 문서의 날짜별 절에 있다.

## 이 환경에 맞춘 값 (`config/gestures.json`)
- 핀치가 손 크기에 비례해 시작: `pinch_size_ratio` 0.2, `click_size_ratio` 0.22, `ring_click_size_ratio` / `pinky_click_size_ratio` 0.25 (손이 작게 찍혀 고정 거리 기준이 너무 넓었음)
- 반주먹: `curled_moving_speed` 0.3, `curled_pinch_size_ratio` 0.13, `curled_click_size_ratio` 0.15, `side_pinch_after_curl` 0.4, `side_rearm_ratio` 0.35, 배율 곡선 `shape_dpi_curve` 0.5, 주먹 배율 `fist_dpi_multiplier` 35
- 핀치 브라우징: `drag_hold_size_ratio` 0.15, `scroll_return_open` 0.012, `scroll_return_distance` 0.015
- 주먹 커서 궤적: `path_smoothing` true, `path_follow` 0.5, `path_reverse_deg` 30
- 방향 보정: `palm_axes`는 이 자세에서 잰 값(위아래 약 50° 기울어짐)인데 **꺼 둠**(`palm_axes_strength` 0). 팔꿈치 보정도 꺼 둠(`elbow_pivot` false). 둘 다 커서를 엉뚱하게 휘게 했다(`docs/path-smoothing.md`).
- Pose 팔 표시: `pose_enabled` true, `pose_smooth` 0.25 (`models/pose_landmarker_lite.task`)
- 바탕화면 보기·V 사인·모션 모드 기준도 이 기록으로 조정했다(`docs/motion-mode.md`, `docs/toggle-sign.md`).

## 다른 PC·카메라에서 이어서 할 때
- 손 크기에 비례하는 기준은 카메라가 바뀌어도 어느 정도 따라가지만, 배율(`fist_dpi_multiplier`, `shape_dpi_curve`)과 화면 단위 고정 기준은 카메라·거리에 따라 다르게 느껴질 수 있다. 사용자가 불편을 말하면 **그 PC에서 새로 쌓인 `logs/failures`로** 다시 판단하고, 이 PC의 결과와 섞지 말 것.
- `palm_axes`는 이 PC의 자세에서 잰 값이라 다른 자리에서는 의미가 없다. 켜려면 그 자리에서 Qt 화면의 "커서 방향 보정"을 다시 한다.
- `tests/fixtures/*.json`은 이 PC 기록에서 잘라 낸 관절 좌표다(영상 없음, 1280×720). 회귀 시험용이다.
- 알려진 실패(2026-11-10 갱신): `test_the_corner_badge_text_follows_the_mode`는 이제 통과한다. Windows가 아닌 곳(리눅스 컨테이너)에서는 3개가 실패한다: `test_sequence`의 chrome App Paths, `test_ui_magnet`의 스레드 시험, `test_virtual_hand`의 창 표시. Windows에서 다시 확인할 것.
- Qt 화면 실행: `uv run --locked --extra qt python main.py --ui qt` (`--extra qt` 없이 실행하면 PySide6가 없다).

## 지금 상태와 다음 할 일 (2026-11-05 새벽, 브랜치 `feat/tcn-training-app-transition`)
- 된 것: 학습 데이터 녹화(`docs/recording.md`, Qt "학습 데이터 녹화"), 첫 안내 녹화 1개(`recordings/20261105-030947-guided.jsonl.gz`, 폰 카메라 17분, 엄지+약지·엄지+검지·반주먹 이동 × 자세 3), TCN 학습 스크립트(`scripts/train_tcn.py`), 모델(`models/gesture_tcn.onnx` + `.json`), 앱의 손짓 확인기(`src/gesture_gate.py`, Qt "손짓 확인 (TCN)"). 결과·수치는 `docs/recording.md`.
- **2026-11-05 새벽 4시대: "TCN 학습 앱" 1차 완료** (`docs/training-studio.md`): `studio.bat`/`studio.py` 번호 메뉴(명령·옵션 설명), 앱 위쪽 [학습 센터](동작별 데이터·인식률·학습 정확도·오작동·조언, [집중 녹화], [학습 시작] 진행·손실 곡선, [내보내기]), `scripts/training_report.py`, `scripts/export_model.py`, `train_tcn.py --progress`, `src/training_center.py`. 아래 1~4는 이 요청의 원문 요약이다. 아직 GitHub에 올리지 않았다(사용자가 "올려"라고 할 때만).
- **학습 데이터 공유 방식(사용자 결정, 2026-11-05)**: 녹화(`recordings/*.jsonl.gz`)는 코드와 같은 브랜치에 계속 함께 올린다. 학습 센터가 총량을 보여 주고 500 MB를 넘으면 전용 저장소 분리를 권한다. 파일 이름에 PC 이름, 녹화 중엔 `.part`, 학습은 완성된 파일만(`src/recorder.finished_recordings`). `docs/training-studio.md` 6절.
- 사용자가 요청했던 작업: 이 앱을 "TCN 학습 앱"으로 바꾸기.
  1. 긴 실행 명령(`uv run --locked --extra qt --extra train python main.py --ui qt`)을 쉽게: 무엇을 하는지, 어떤 옵션이 어떤 효과인지 바로 알 수 있게(예: 실행 메뉴·바로가기 배치 파일, 옵션 설명).
  2. 동작별 **학습 정도(데이터 양·학습 손실)**와 **인식률(학습에 안 쓴 자세에서의 인식·오작동)**을 앱 화면에서 바로 보이게. 사용자가 "이 동작은 데이터가 더 필요하다"를 알아보고 그 동작만 골라 집중 녹화할 수 있게.
  3. 녹화 → 데이터셋 → TCN 학습 → 평가 → **다른 앱에 옮길 수 있는 형태로 내보내기(export)**를 앱 안에서 한 흐름으로. 틈날 때마다 녹화해서 학습시키는 앱.
  4. 학습 중인 TCN의 동작별 학습률·인식률을 사용자가 볼 수 있게.
- 함께 하기로 한 인식률 개선(아직 안 함): 실제로 닿은 구간만 정답으로 다시 표시, 동작별 판정 기준 조정, 두 동작은 모델이 직접 판정하는 선택지, 평소 녹화 + F 표시 데이터로 주기적 재학습, 다른 날 추가 녹화. 확인기만으로는 인식률이 규칙 수준(약 55%)에 묶인다(모델 단독은 처음 보는 자세에서 82%).

## 오판 줄이기 (2026-11-09, `docs/training-studio.md` 7절)
- "닿기 직전" 녹화 항목 4개(`near_thumb_*`, 정답 `none`)를 추가했다. 아직 녹화 전이라 효과는 재지 못했다.
- `train_tcn.py --hard-negatives`는 18개 자세 비교에서 효과가 없어(대조군과 차이 없음) 기본으로 꺼 두었다.
- `none`은 첫 녹화의 자세 3개에만 있다(`drop_all_rest`). 오작동 수치도 그 3개 자세로만 재므로, 다른 자세에서의 오작동은 아직 모른다.
- `train_tcn.py --keep-rest`(쉬기 구간을 `none`으로 학습, 기본 꺼짐): 18개 자세 비교에서 오작동 29→14, 쉬기 오작동 182→118로 줄었지만 인식도 678→660/771로 줄었다. 아직 최종 모델에 적용하지 않았다.

## 다른 PC에서 같은 환경 만들기 (TCN 학습 준비 포함, 2026-11-05)
- 손짓 판정을 학습 모델(TCN)로 보완하는 작업을 시작한다. 학습용 라이브러리는 선택 묶음 `train`에 있다: `torch`(CPU 판, 약 200 MB), `onnx`(학습한 모델 내보내기), `onnxruntime`(앱에서 모델 실행).
- 설치: 저장소 폴더에서
  ```
  uv sync --locked --extra qt --extra train
  ```
  uv가 없으면 `run.bat`이 먼저 설치한다. GPU는 필요 없다(이 PC도 CPU로 학습).
- **주의:** `uv run`/`uv sync`에 `--extra`를 빼면 uv가 환경을 기본 묶음에 맞추면서 PySide6·torch를 지운다. 이 작업에서는 늘 `--extra qt --extra train`을 붙인다.
- 이 PC에서 확인한 버전: torch 2.15.0+cpu, onnx 1.23.2, onnxruntime 1.31.1, mediapipe 1.2.0, PySide6 6.12.0, Python 3.12. 시험 359개 통과(위의 알려진 실패 1개 제외).
- 계획(사용자와 합의): 앱을 TCN 학습 데이터 제조기로 확장 → 오류가 많은 동작부터(엄지+약지 우클릭, 엄지+검지 스크롤, 반주먹 이동="아무것도 아님") 3가지 각도 × 15회 + 반주먹 이동 15분 녹화 → 규칙 엔진 위에 "의도한 동작인지" 확인기로 붙여 같은 기록에서 규칙과 비교 → 좋아지면 다른 동작으로 넓힌다. 녹화에는 화면 좌표뿐 아니라 3D 관절(world landmarks), Pose 어깨, PC·카메라·사람·날짜를 처음부터 함께 저장한다.

## 화면 가상 손과 명세 마무리 (2026-11-10 밤, 이 PC `pc-rtx3060`)
- 가상 손 조작·측정·남은 것: `docs/virtual-hand.md`. 이 모드에서는 규칙 엔진의 핀치가 모두 꺼지고 손 모양 판정(`src/virtual_hand.py`)만 쓴다.
- 실사용 클릭 기록은 `logs/virtual_clicks/`(git 제외), 클릭 뒤 5초 안 F = "클릭 아님". 결과는 `scripts/virtual_click_report.py`.
- 명세 남은 항목(FAR/FRR 곡선, 표본 정리, 자동 되돌리기, 학습 우선순위, Pause 긴급 정지): `docs/online-learning-phase5.md` "마무리 추가".

## hand-mouse-1109 흡수 (2026-11-10)
- `hand-mouse-1109` 폴더는 이 저장소의 이전 버전이다. 거기에만 있던 것(`src/crop_shadow.py`, home-pc 실패 기록 `field-logs/2026-11-09-home-pc/failures/`, `config/recording-plans.json`)을 옮겼다. 무엇을 왜 안 가져왔는지는 `docs/far-hand-loss.md` 25절. 이후 1109는 보관만 한다.

## 말·글로 동작 연결 (AI, 2026-11-10)
- 동작 설정 탭 "5-4. 말·글로 연결 (AI)": 적거나(또는 Windows 음성 입력 Win+H로) 말한 요청 → AI가 연결을 제안 → 목록과 대조 검사 → 사용자가 체크한 것만 저장. `src/llm_binding.py`(프롬프트·검사·적용), `src/llm_providers.py`(Claude=공식 `anthropic` SDK, OpenAI·Gemini·OpenRouter·Ollama=OpenAI 호환 HTTP), `src/secret_store.py`(키를 Windows DPAPI로 암호화해 QSettings에 저장).
- **`anthropic`은 선택 묶음 `llm`에 있다. 이제 동기화는 늘 `uv sync --locked --extra qt --extra train --extra llm`** (`--extra llm`을 빼면 anthropic이 지워짐; studio.bat·setup-tcn.cmd는 고쳐 둠). 다른 회사 연결은 추가 패키지 없이 동작.
- 보내는 것: 요청 문장, 동작 이름·현재 명령, 명령 목록. 영상·관절·창 제목은 안 보냄. `launch_app`(프로그램 실행) 제안은 기본 체크 해제.
- 실제 API로는 아직 시험하지 않았다(시험은 모두 가짜 응답).
- 같은 탭의 [손 모양 보여주기](`src/pose_probe.py`, 3초 뒤 2초 관찰)·[키 기억하기](`src/key_capture.py`, 누른 조합 하나만): "이 손모양이면 저렇게"의 이/저를 앱이 확인해 AI 요청에 덧붙인다. 목록에 없는 손 모양은 '1초 유지' 복합 동작으로 새로 등록(평소 손 모양은 기본 체크 해제).

## 그리기 모드·설정 창 (2026-11-10)
- 그리기 모드: `docs/draw-mode.md`. 손바닥 모드의 [설정]은 이제 Qt 설정 창(`palmSettings`)을 연다: 모션 모드, 커서 방향 보정·초기화(메인 화면 왼쪽에서 옮김), 그리기 모드, 엔진 세부 값(기존 Tk 슬라이더 창).
- AI 연결 확장(2026-11-10): 매크로(`src/macro.py`, 최대 10단계: 단축키·글자·기다리기·열기·마우스·유튜브 키, 백그라운드 실행, Pause로 중단), AI가 새 복합 동작(손 모양+쓸기/유지/두 번 집기)과 새 시계열 모션(손 모양+8방향 한 번 쓸기, 모션 모드에서) 생성, 그리기 모양도 연결 대상. AI로 바꾼 내역은 `config/ai_connections.json`(요청·이전 연결 포함)에 쌓이고 화면 목록에서 되돌리기. 이 창은 메인 화면 상단 [말·글로 연결 (AI)] 버튼(동작 설정 5-4 탭에서 옮김).
- 동작 이어 하기(2026-11-10): `src/sequence.py`. 손 모양 2~3개를 차례로(각 0.25초 유지, 손 모양을 놓은 뒤 1초 안에 다음) → 명령. 손 모양은 `pose_probe.hand_posture`(관절만, 모든 모드). 저장 `config/sequences.json`, 명령은 바인딩 `seq_*`. AI로 만들고 [설정]에서 목록·끄기·삭제. 같거나 앞부분이 겹치는 이어 하기는 막음.
- AI 연결 목록(`config/ai_connections.json`)은 그 기능을 넣은 뒤부터 쌓인다. 그 전에 AI로 만든 `comp_open_hand_hold`(손 펴고 1초 유지 → youtube)는 손으로 한 줄 넣어 두었다.
- 거꾸로 V(2026-11-10): `v_sign_down`(검지·중지 끝이 손목보다 손 크기 1/3 이상 아래, `pose_probe.fingers_down`). 제어 켜기/끄기 V(`toggle_sign`)는 이제 바로 선 V만. 녹화: 바로 선 V 5100프레임 중 거꾸로 판정 0, 쉬는 중 0.1%. 사용자 연결: `comp_v_sign_down_hold`(거꾸로 V 1초) → `chrome.exe --incognito https://www.youtube.com`, V는 제어 켜기/끄기로 되돌림.
- 자체 감지기가 있는 신호(V=제어, 엄지 위로=모션 모드)도 바인딩을 바꾸면 그 명령을 실행(`QtRuntime._sign_override`). 단 제어가 꺼져 있을 때 V는 늘 다시 켜기. `launch_app`은 PATH에 없는 chrome.exe 등을 App Paths로 찾음(`_shell_open`).
- 손 모양으로 그리기(2026-11-10): `src/path_gesture.py`. 손 모양(예: 주먹)을 유지한 채 손바닥으로 그리기 모드 모양(원 등)을 그리면 명령. 손 모양이 끝나거나 0.35초 멈추면 판정, 등록한 그 손 모양+그림만 실행, 그리기 모드 없이 모든 손 모드에서. 저장 `config/path_gestures.json`, 바인딩 `path_*`. AI로 만들고(`new_trigger` = `draw_*`) [설정]의 "이어 하기 · 손 모양으로 그리기"에서 관리. [손 모양·움직임 보여주기]로 원을 보여 주면 "그 손 모양 + 원"을 기본 제안. 가상 손 모드에서 주먹은 스크롤이라 주먹 그리기 중 화면이 스크롤될 수 있음.
- 유지(hold) 복합 동작: 새로 만들 때 0.5초(평소 손 모양은 1초), 사용자의 거꾸로 V는 0.4초. 유지 중 0.15초 이하 "모양 없음"은 무시(두 번 집기 손 모양은 제외).
- 움직임 판정(2026-11-10): [손 모양·움직임 보여주기]는 손 크기를 2초 동안의 중간값(`hand_px`) 하나로 쓰고 경로를 살짝 매끄럽게 함(프레임마다 나누면 손이 돌 때 경로가 폭발). 결과: 가만히=손 모양, 곧게=방향(8), 휘게=그리기 모양, 손바닥은 제자리+60° 이상 회전=돌리기(엄지 위로는 엄지 방향). 매 시도의 관절 좌표를 `logs/pose_probe/`(최근 50개)에 저장. 손 모양 그리기(`path_gesture`)에 `rotate_cw/ccw`(70°, 1.5초 안) 추가.
- 주먹 vs "손가락 앞으로 굽히기(ㄱ자)" `fingers_bent`: 2D로는 같아 보여 3D 비율(z 포함)로 구분(주먹 ≤1.2, ㄱ자 검지·중지 ≥1.3). 녹화의 기존 동작 판정은 변화 없음.
- 보여주기 판정 규칙(사용자 결정, 2026-11-10): "말로 정의하기 어려운 자세는 무시하거나 다른 자세로 이야기한다" → 손이 보였으면 실패 대신 가장 가까운 손 모양·움직임으로 답하고 `approx` 표시(AI에도 알림). 손날 `knife_hand`(3D 손바닥이 옆을 향함 ≥0.6), 갔다 오는 긋기는 나가는 구간 직선성(≥0.7)으로 방향 움직임. 모션 모드도 새 손 모양(손날·ㄱ자·거꾸로 V)을 받음. 기록 `logs/pose_probe/` 8건으로 확인: 손날 긋기 2건 → 손날 + 대각선, 엄지 손목 돌리기 → 시계 100°.

## 코드 점검에서 고친 것과 남긴 것 (2026-11-10, 리눅스 컨테이너에서 점검)
- 고친 것(재현 스크립트로 확인): 카메라가 끊기면 드래그 버튼이 눌린 채 남던 것(Qt·Tk·기본 화면 모두), 일시정지 뒤 스크롤 관성이 되살아나던 것, `main.py`의 `ScrollTargetFinder` 인자 순서, `gestures.json`을 BOM·다른 인코딩으로 저장하면 기본값으로 덮이거나 시작에서 멈추던 것(이제 `.bak-unreadable-*`로 보관, 저장은 임시 파일 뒤 교체), 프로필의 `calibrated_hand_size` 누락, 궤적 매끈하게 설정이 처음 값에 고정되던 것, 폰 브리지 페어링 키(QR 주소의 `?k=`), 온라인 표본 색인 덮어쓰기, 클릭 확인이 거부한 집기가 뗄 때 클릭되던 것, 학습 전 백업 순서, 재개 캐시 지문, `training_selection.json` 원자적 저장, AI 제안 부분 저장 시 기록 유실, 매크로 한글 붙여넣기 핸들, `run.bat`의 `uv sync --inexact`.
- **Windows에서 직접 확인이 필요한 것**: 매크로 한글 붙여넣기(`src/macro.py::_paste`), 폰으로 QR을 찍어 연결(주소에 키가 붙음; 예전에 즐겨찾기한 주소는 403), `run.bat`.
- 남긴 것(동작·수치가 바뀌어 사용자 결정이 필요): 모델 카드의 `rate`가 보강 학습 뒤 수치로 바뀌는 것(처음 보는 자세 수치는 `zero_shot_rate`), 선택 학습 모델의 평가 기준(0.58/2)과 앱 기준(0.7/3) 불일치, 학습(양방향 손가락 복원)과 앱(인과 추적)의 특징 차이, `augment()`가 0으로 채운 창 앞부분에 가짜 속도를 만드는 것, `--split-mode`가 기록만 되고 쓰이지 않는 것, 설정 다시 읽기(R)·프로필 전환이 닫기/V 사인·모션 모드·팔꿈치 보정·손 자르기에 닿지 않는 것, `CursorInterpolator`의 드문 경합, 온라인 학습 자동 되돌리기가 승격 때와 같은 검증 자료만 다시 보는 것.
