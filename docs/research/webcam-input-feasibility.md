# 웹캠 손 인터페이스 타당성 조사와 기반 인터페이스 설계안

- **작성:** 2026-11-04, MANUX Discord 협업 세션(AI 초안, 사람 검수 전)
- **요청:** sean.choi — 타사 해법 조사, 우리 방향 점검, 기반(foundation) 인터페이스 기준, 협업 진단
- **근거:** (A) 웹 조사 — 에이전트 2개와 직접 검색. 대부분 **검색 결과 요약 기반**이고 논문 본문은 읽지 못했습니다. (B) 저장소 — `origin/preview`(2026-11-04 10:47 KST 기준)의 파일과 Jira·GitHub 조회. 앱은 실행해 보지 않았습니다.
- **표기:** `확인`은 직접 연 공식 문서·페이지, `요약`은 검색 요약만 본 것, `미확인`은 근거를 못 찾은 것, `추론`은 제 해석입니다.

---

## 0. 한눈에 보는 결론

| 질문 | 답 | 확신도 |
| --- | --- | --- |
| 2D 웹캠으로 마우스를 대체할 수 있나? | **거친 포인팅과 클릭은 가능성이 높고, 정밀 포인팅은 불확실.** 출시된 제품은 대부분 시선·깊이·IR·근전도 같은 추가 센서를 쓴다 | 중간 (조사 요약 기반) |
| 지금 방향(손 관절 → 커서)이 맞나? | **큰 틀은 맞지만 두 가지는 바꿔야 한다.** ① 큰 손 모양(펼친 손 등) 대신 **작은 핀치류 하나로 클릭** ② 커서 이동과 클릭을 **서로 다른 신호로 분리** | 중간 |
| 터널증후군 예방이라고 말해도 되나? | **안 된다.** 입증한 연구를 찾지 못했다. "큰 동작을 줄이는 설계" 수준까지만 | 높음 |
| 협업은 잘 되고 있나? | **구현은 빠르지만 기준이 갈라져 있다.** 구현이 둘이고, 손가락 배정이 다르고, 측정 데이터가 없고, 리뷰가 0건이다 | 높음 (저장소 사실) |
| 가장 먼저 할 일 | **기반 인터페이스 계약 1개 + 손 좌표 기록을 재생하는 공용 시험대**를 만들어 두 구현이 같은 기준으로 비교되게 한다 | 설계 제안 |

---

## 1. 문제 정의 (요청 내용 정리)

1. 입력은 2D 웹캠 영상이다. 깊이가 없다.
2. MediaPipe가 손 관절을 추정하고 화면 좌표로 매핑한다.
3. "다른 동작"을 펼친 손 등으로 구분하는데, 직관적이지 않고 오히려 피로하다.
4. 관절이 겹치면 jitter가 생겨 부드러운 인터페이스가 안 된다.

기술적으로 이 넷은 **깊이 모호성(2, 3)**, **가림에 의한 추정 불안정(4)**, **제스처 어휘 설계(3)**로 묶입니다.

---

## 2. 타사는 어떻게 푸나

### 2.1 손 입력 제품 (센서 · 클릭 방식 · 포인팅)

| 제품 | 센서 | 클릭 | 포인팅 | 시사점 | 상태 |
| --- | --- | --- | --- | --- | --- |
| Apple Vision Pro | 시선+손 추적 | 엄지-검지 핀치. 손은 무릎 위 등 편한 자세에서도 인식 | **시선이 포인터**, 핀치가 인식될 때만 시선 정보 사용 | 손은 "확정"만 하고 위치는 다른 센서가 맡는다. 정밀 작업은 약하다는 평가 | 요약 [UploadVR](https://uploadvr.com/apple-vision-pro-gesture-controls), [Pfeuffer 등](https://research.google/pubs/design-principles-and-challenges-for-gaze-pinch-interaction-in-xr/) |
| Meta Quest 핸드트래킹 | 헤드셋 카메라 | 검지 핀치. 문서는 연속값이 아닌 **불리언 핀치 상태** 사용을 권장 | 손에서 레이캐스트(유효 플래그가 있을 때만) | 핀치는 불리언+유효성 플래그로 다룬다 | 요약 [Meta 문서](https://developers.meta.com/horizon/documentation/unity/unity-handtracking-interactions/) |
| HoloLens 2 | 헤드셋 센서 | air tap(엄지-검지 핀치), 핀치 유지로 grab·스크롤 | hand ray(손바닥 중심에서 투사) → **point and commit** | 포인팅과 확정의 분리를 공식 원칙으로 둔다 | 요약 [MS point and commit](https://learn.microsoft.com/en-us/windows/mixed-reality/design/point-and-commit) |
| Ultraleap (TouchFree) | 적외선 스테레오 | Air Push / Hover&Hold(dwell) / Touch Plane 중 선택 | 손 위치가 커서 구동 | 클릭 방식을 사용자가 고를 수 있게 둔다. 상업 이용은 라이선스 확인 필요 | 요약 [Ultraleap 문서](https://docs.ultraleap.com/TouchFree/touchfree-user-manual/technical-glossary.html) |
| Google Soli(Pixel 4) | 60GHz 레이더 | 클릭 개념 없음, 소수 에어 제스처 | 해당 없음 | 오작동·좁은 기능이 채택을 막았다는 후기 | 요약 [SlashGear](https://www.slashgear.com/pixel-4-motion-sense-gestures-face-another-hurdle-to-adoption-29597810) |
| Meta Neural Band | 손목 근전도 | 핀치, 커서 제어 지원(2025-09 출시) | 방식 상세 미확인 | 작은 제스처도 근육 신호로 감지 | 요약 [Meta 뉴스룸](https://about.fb.com/news/2025/09/meta-ray-ban-display-ai-glasses-emg-wristband/) |
| Apple Watch 더블탭·AssistiveTouch | 손목 센서 | 핀치/클렌치 | 포커스 링 이동 | 활성화에 더블 클렌치가 필요. 한 리뷰어는 핀치 약 75% 적중(일화) | 요약 [Apple 지원](https://support.apple.com/en-ie/guide/watch/apdec70bfd2d/9.0) |

### 2.2 웹캠·일반 카메라 제품과 오픈소스

| 사례 | 포인팅 | 클릭 | 떨림·오클릭 대책 | 상태 |
| --- | --- | --- | --- | --- |
| Camera Mouse | 얼굴 특징 추적, 머리 이동 | **dwell**(논문 기본 약 30px 안에서 0.5초) | gain·smoothing·dwell 영역/시간 조절. 오클릭 우려 보고. 연구에서 dwell이 처리량은 높았지만 사용자는 **의도적 클릭이 "내가 통제한다"는 느낌**이라 선호 | 요약 [Gips 논문](https://www.cs.bu.edu/fac/betke/cs591/papers/gips-betke-dimattia.pdf) |
| Windows Eye Control | 시선(전용 하드웨어) | dwell, 정밀 마우스·스크롤 모드 별도 | 사전 교정, dwell 시각 피드백 | 요약 [MS](https://support.microsoft.com/en-US/Accessibility/windows/eye-control/eye-control-basics-in-windows) |
| macOS Head Pointer | 카메라 머리 추적, 상대 이동 | 별도 기능(dwell 등)에 위임 | **일시정지·재교정**(커서를 화면 중앙으로) | 요약 [AbilityNet](https://mcmw.abilitynet.org.uk/how-to-control-your-computer-with-head-movement-in-macos-14-sonoma) |
| Project Gameface(Google, MediaPipe) | 머리 이동 | 표정 바인딩, 제스처별 민감도 슬라이더 | pointer_smooth 등 설정(포크 저장소 기준) | 요약 [GitHub](https://github.com/google/project-gameface) |
| 오픈소스 MediaPipe 가상 마우스 | 검지 끝 또는 상대 이동 | 엄지-검지 거리 | 진입·해제 임계값 분리(히스테리시스), EMA, One Euro, 카메라 fps와 분리한 60Hz 커서 루프 | 요약 [HandGesture-Mouse](https://github.com/murfidya/HandGesture-Mouse) (프로젝트 사례, 정확도 자료 아님) |

### 2.3 업계에서 반복되는 패턴 (추론)

1. **확정(클릭)은 작은 핀치류 하나**로 한다. Vision Pro, Quest, HoloLens, Watch 모두 같다.
2. **포인팅과 확정은 다른 신호**다. Vision Pro는 시선, HoloLens는 레이, 우리는 손 위치+핀치가 해당한다.
3. **시선·깊이·IR·EMG를 쓰지 않는 제품**(Camera Mouse, Head Pointer)은 거의 모두 **상대 이동 + 일시정지(클러치) + dwell 보조 + 감도 설정**을 둔다.
4. **정밀 작업은 별도 모드**(정밀 마우스, 감속)로 푼다. 1순위 해법으로 보이지 않는다.
5. 큰 손 모양(펼친 손, 주먹)으로 명령을 가르는 제품은 이번 조사에서 **찾지 못했다**. 우리의 "펼친 손으로 다른 동작" 방식이 피로하다는 사용자 보고와 방향이 같다.

---

## 3. 2D 웹캠의 기술적 한계와 대응

### 3.1 MediaPipe Hand Landmarker (확인: [공식 문서](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker))

- 구조: 손바닥 검출 + 21점 랜드마크의 2단. 모델 입력은 192~224px(`확인`). **손이 작게 찍히면 해상도가 부족**(추론).
- 출력 2종: **이미지 좌표**(x, y는 이미지 크기로 정규화, **z는 손목 기준 상대 깊이**, 작을수록 카메라에 가까움)와 **월드 좌표**(미터 단위, 원점은 손의 기하 중심). `요약`(검색 요약에서 확인, 공식 본문 대조는 못 함)
- VIDEO·LIVE_STREAM 모드는 **이전 프레임의 박스로 추적**하고, 추적 실패나 presence 점수 저하 시 손바닥 검출을 다시 돌린다(`확인`). 빠른 움직임·가림에서 박스가 어긋나 jitter와 손실이 생길 수 있다(`추론`).
- 임계값 기본값: detection·presence·tracking 모두 0.5(`확인`).
- z의 신뢰도, 가림·측면·저조도의 정량 정확도: **미확인**. 단안 RGB의 깊이는 추정값이라 거칠다는 것은 일반적 이해이나 근거 문서를 못 찾았습니다.
- 지연 수치는 모바일(Pixel 6 CPU 17ms) 기준이고 PC 웹캠 수치가 아니다.

### 3.2 Jitter 완화 (확인: [One Euro 저자 페이지](https://gery.casiez.net/1euro/))

| 기법 | 내용 | 우리 코드 현황 |
| --- | --- | --- |
| **One Euro Filter** | 느릴 때 강하게 평활(떨림 제거), 빠를 때 컷오프를 올려 지연 감소. 튜닝 순서: beta=0, min_cutoff≈1Hz로 시작 → 정지 상태에서 떨림이 사라질 때까지 min_cutoff 조정 → 빠르게 움직여 지연이 보이면 beta를 올림 | **Python 앱에는 있음**(`hand-mouse/src/filters.py`: One Euro + deadband). **Electron(JS) 컨트롤러는 지수 평활 하나**(`smoothing = 0.45`) |
| Deadband | 작은 움직임 무시 | Python에 있음. JS에서 찾지 못함 |
| 클릭 중 커서 고정 | 핀치 동작이 검지 끝을 흔드는 문제 대응 | Python의 "엄지 방아쇠" 후보 A(접근 시 고정, 누름 시 약 100ms 전 위치로 되감기)에 설계됨. 일반적 근거 자료는 못 찾음 → **가설로 두고 실측** |
| 손바닥 중심 앵커 | 관절 여러 개의 평균이라 핀치 동작에 덜 흔들림 | 설계 결정 노트가 비교 중. 일반 근거는 **미확인** |
| 핀치 히스테리시스·N프레임 확인·손 크기 정규화 | 임계값 2개, 연속 프레임 확인, 거리를 손바닥 폭으로 나눔 | Python 엔진에 히스테리시스·손바닥 폭 기준 비율이 있음 |
| 카메라 fps와 커서 루프 분리 | 보간으로 부드럽게 | Python에 144Hz 커서 보간, JS는 #36에서 보간 추가됨 |

### 3.3 깊이 없는 2D의 한계를 줄이는 방법 (구체 수치는 대부분 미확인)

| 방법 | 효과(추론) | 비용 |
| --- | --- | --- |
| 손 주변 크롭 후 재추론 | 작게 찍힌 손의 해상도 보완 | Python에 이미 시험 구현(`hand_crop.py`). 지연 증가 |
| 카메라 위치·조명·노출 가이드 | 가림·저조도 실패를 사전에 줄임 | 온보딩 필요. 이미 U1 범위 |
| 깊이·IR 센서(RealSense, Leap 계열 등) | 깊이 모호성 근본 해결 | **추가 하드웨어 → "집의 일반 웹캠" 목표와 충돌**. 비용·보급률 조사는 안 함 |
| 시선·머리 보조 | Vision Pro 방식. 포인팅을 손에서 덜어냄 | 시선은 일반 웹캠으로 정밀도 확보가 어렵다고 알려짐(근거 미확인). 머리 포인터는 선례 있음 |
| 큰 손 모양 대신 **손가락 접촉 조합** | 깊이 없이도 "닿았다/떨어졌다"는 비교적 판정 가능 | 가림 문제(엄지가 검지 뒤로 숨음)가 남는다는 위험을 설계 노트도 인정 |

---

## 4. 인체공학: 무엇이 근거 있고 무엇이 없나

| 주장 | 근거 | 상태 |
| --- | --- | --- |
| 공중 제스처는 어깨·팔 피로(gorilla arm)가 크다 | [Consumed Endurance, CHI 2014](https://blog.jhincapie.com/wp-content/uploads/2010/02/ga-final.pdf) — 팔을 굽히고 어깨와 허리 사이 높이에서 조작할 때 소모가 낮았다 | 요약 |
| **팔을 받치면** 장시간 제스처 부담이 줄어든다 | VAMR 2017 "Dispelling the Gorilla Arm Syndrome" ([Springer](https://link.springer.com/chapter/10.1007/978-3-319-57987-0_41)) | 요약 (저자·본문 미확인) |
| 클릭 제스처 중 pinch가 가장 편하다 | [Dube 등 2022](https://www.yorku.ca/mack/iss2022.html): Tap이 빠르고 정확, Dwell은 느리지만 부담이 낮음, **Pinch는 오류와 부담에서 불리한 결과가 있음** | 요약. **"핀치는 작으니 가장 편하다"를 전제로 두면 안 됨** |
| 마이크로 제스처가 큰 동작보다 피로가 적다 | 직접 비교 연구를 못 찾음 | **미확인** |
| 마우스 대체가 **터널증후군을 예방**한다 | 입증 연구를 못 찾음. 2024 소규모 파일럿(건강한 5명)과 시험 등록만 확인. 수직 마우스가 CTS 환자의 손목 압력을 줄이지 못했다는 연구가 인용됨(원문 미확인) | **근거 없음 → 제품 문구에서 주장 금지** |

저장소의 `docs/m1-webcam-mouse/`는 이미 이 점을 반영해 "피로를 줄이는지는 가설"로 적고 있습니다. 이 방향이 맞습니다.

---

## 5. 우리 설계안: 기반(foundation) 인터페이스

### 5.1 원칙 (조사 + 저장소 문서 종합)

1. **위치·의도·상태를 분리한다.** 손 위치를 잃은 것, 손 모양을 못 알아본 것, 사용자가 클릭을 요청한 것은 서로 다른 사건이다. 현재 문서(`interaction-contract.md`)가 같은 방향의 "세 채널"을 제안했다.
2. **이동은 상대 이동 + 클러치.** 손이 카메라 밖으로 나가거나 쉴 때 커서가 점프하지 않게 일시정지·재교정을 둔다(Head Pointer 사례).
3. **클릭은 작은 접촉 하나.** 후보는 엄지-검지(JS 앱) 또는 엄지-중지(Python 앱) 등 **손가락 하나 쌍**으로 통일한다. 펼친 손·주먹 등 큰 모양은 "명령"이 아니라 "쉬기/재개" 같은 상태 신호로만 쓴다.
4. **클릭 순간 커서는 고정한다.** 접근 시 고정, 누르기 시작 시 직전 위치로 되감기는 후보로 시험한다.
5. **안전 쪽 실패.** 불확실하면 명령 없음. 눌린 버튼은 손 소실·정지·긴급 중지에서 해제한다(이미 구현된 계약).
6. **측정 가능해야 한다.** 필터·임계값은 손 좌표 기록을 재생해 비교한다. 실시간 체감만으로 결정하지 않는다.

### 5.2 제안하는 계약 (구현이 아니라 제안)

```
HandState (매 프레임, 추적 계층 출력)
  t            단조 증가 시각(초)
  hands[]      { id, handedness, landmarks[21]{x,y,z}, quality, visible }
  tracking     'ok' | 'degraded' | 'lost'    # 위치를 믿을 수 있는가
  cameraAspect 영상 높이/폭

PointerSignal (포인터 계층)
  anchor       'palm' | 'index'              # 설정 가능, 기본값은 비교 시험으로 결정
  point        필터링된 0..1 좌표(One Euro + deadband)
  moving       bool                          # 이동 유지 상태
  frozen       bool                          # 클릭 접근 중 고정

IntentEvent (의도 계층, 사건)
  type         'press' | 'release' | 'click' | 'drag-start' | 'drag-end' | 'scroll' | 'pause' | 'resume'
  evidence     'contact' | 'dwell' | 'pose'   # 무엇을 근거로 판정했는가
  confidence   0..1 | null

OutputAction (Windows 입력 계층)
  move | button(down|up) | scroll | release   # 이미 desktop/mouse-bridge.mjs가 검증하는 4종
```

- 이 중 `OutputAction` 4종과 안전 해제 계약은 **이미 구현**되어 있습니다(`desktop/`).
- `PointerSignal`·`IntentEvent`는 Python `PointerEngine`의 입출력(`hand-mouse/docs/engine-contract.md`)과 거의 겹칩니다. **새로 만들기보다 이 문서를 기준 계약으로 승격하는 것이 가장 싸다**(추론).

### 5.3 공용 시험대 (이번 제안에서 가장 중요한 항목)

| 구성 | 내용 |
| --- | --- |
| 입력 | 손 좌표 기록(21점 × 시간). 저장소에 이미 `hand-mouse/src/failure_log.py`가 21점 기록을 남기는 구조가 있음 |
| 재생기 | 같은 기록을 JS와 Python 구현에 넣어 이벤트를 비교 |
| 지표 | 커서 떨림(정지 시 표준편차), 지연(입력→이벤트), 클릭 성공·오실행, 손 소실 시 버튼 해제 |
| 합격선 | 팀이 정함. 이 문서는 수치를 제안하지 않는다(근거 없음) |
| 주의 | 사용자 이름·원시 영상·계정 정보는 저장소에 넣지 않는다(`AGENTS.md`). 좌표 기록도 참가자 동의와 익명화 후에만 |

### 5.4 우선순위 있는 실험 (모두 합성이 아닌 실물 필요)

1. **클릭 방식 A/B**: 엄지 방아쇠(검지 유지) vs 손바닥 기준 + 핀치. 이미 `decision-pointer-click.md`가 같은 비교를 제안했고 "결정 전" 상태입니다.
2. **필터 비교**: 지수 평활(JS 현재) vs One Euro+deadband(Python 현재), 같은 기록으로.
3. **가림 실패 분류**: 엄지가 검지에 가려지는 각도, 카메라 높이별 손실률.
4. **피로**: 팔 지지 유무, 10분 조작 후 Borg 척도 등 자기 보고 + 쉬고 싶어진 시점. 효과 주장 금지.

---

## 6. 저장소 진행과 협업 진단 (사실 위주)

### 6.1 방향 점검

| 항목 | 관찰 | 판단 |
| --- | --- | --- |
| 구현이 둘 | `preview`에 **Electron(JS)** (`src/`, `desktop/`)와 **Python Qt**(`hand-mouse/`, 62개 파일)가 함께 병합됨. #33은 Python의 검지 포인터를 Electron 방식으로 이식, 계약 문서는 "Tauri+TypeScript 이식"까지 언급 | **수렴을 시도 중이나 어느 쪽이 기준인지 문서로 정해지지 않음** |
| 손가락 배정이 다름 | 설계 노트 표: 엄지+검지가 Electron에서는 **좌클릭**, Python에서는 **스크롤**. 엄지+중지는 각각 우클릭 / 좌클릭 | M1 제품에서 **하나로 맞춰야 한다**고 문서도 인정 |
| 필터 수준이 다름 | Python: One Euro+deadband+크롭. JS 컨트롤러: 지수 평활 0.45 | 사용자가 겪는 jitter는 어느 앱이냐에 따라 다를 수 있음(추론) |
| 설계 문서는 풍부, 결정은 비어 있음 | `docs/m1-webcam-mouse/`(13개 문서), `docs/design/`(7개 컴포넌트), 두 문서 모두 **"AI 초안/설계 제안/결정 전"** 표기 | 방향은 잘 정리되었으나 **사람이 확정한 기준이 없다** |
| 측정 데이터 없음 | 실제 사용자 측정 기록·재생 가능한 좌표 기록이 저장소에서 확인되지 않음. 문서도 "구현·실사용 검증 완료를 뜻하지 않는다"고 명시 | 기준 부족의 핵심 원인 |
| 등록(커스텀 동작) 중심 설계와 어긋날 수 있음 | M1 문서는 "기본 동작에 개인 손모양 등록을 필수로 두지 않는다"고 적음. 반면 Jira M1 태스크(SCRUM-23~25, 27 등)와 제가 만든 등록 엔진·`resolveBinding`은 **등록 중심 구조**를 전제로 함 | **제 산출물 일부가 새 방향과 맞지 않을 수 있다**(추론). 폐기가 아니라 "고급 기능"으로 위치를 다시 정해야 함 |

### 6.2 협업 상태

| 항목 | 관찰 | 해석 주의 |
| --- | --- | --- |
| PR 36건 모두 한 계정 | 병합된 PR 작성자가 전부 `se4nchoi`, 리뷰 0건(PR #1 제외). 정책상 `preview`는 **리뷰어 승인 불필요**(`docs/branch-flow.md`) | 호스트 PC 한 대에서 푸시한 결과일 수 있어, **PR 작성자로 팀원별 기여를 판단할 수 없다**. 제가 어제 한 푸시도 같은 계정 이름으로 올라감 |
| Jira 담당 | M1 20개 태스크의 담당자가 **전부 비어 있음**(고정 역할 삭제 후), 모두 "해야 할 일" | 실제 작업(PR #12~#36)과 Jira가 거의 연결되지 않음 |
| Jira 완료 | 완료는 한순중의 hand-mouse 항목(SCRUM-34~61, Python 앱)뿐 | 가장 성숙한 구현이 M1 계획 **밖**의 이슈로 관리됨 |
| 속도 | 11/03 20:52 ~ 11/04 10:47 사이 PR 약 20건 병합 | 빠르지만 **기준 없이 쌓이는 위험**. 리뷰 없이 병합되므로 계약 충돌이 CI(시험)에만 의존 |
| 기여 인원 | 커밋 작성자는 Sean Choi 111건, 다른 두 계정 합쳐 9건(11/02 이후) | 호스트 PC의 Git 사용자 설정이 Sean Choi라 **의미 있는 기여 분포가 아님** |

### 6.3 진단 요약

- **"기준 부족" 느낌은 맞다.** 원인은 능력이 아니라 구조다. ① 구현 둘 ② 계약은 문서로만 있고 공용 시험이 없음 ③ 의사결정 노트가 "결정 전"으로 남음 ④ 리뷰·Jira 연결이 없어 서로의 변경을 알기 어려움.
- 반대로 좋은 점: 설계 문서가 먼저 나왔고, 실패 기록(`failure_log`)과 계약 문서(`engine-contract.md`)가 있어 **시험대를 만들 재료가 이미 있다.**

---

## 7. 어떻게 바꿀 것인가

### 7.1 이번 주 안에 정할 것 (사람 결정 필요)

| # | 결정 | 이유 | 제안 |
| --- | --- | --- | --- |
| 1 | **기준 런타임 하나** (Electron JS / Python Qt / 이식 계획) | 두 구현이 같은 문제를 따로 푸는 비용 | 우선 **기준 계약(5.2)**만 확정하고, 런타임은 시험대 결과로 정한다 |
| 2 | **클릭 문법 하나** (손가락 쌍과 의미) | 현재 두 앱이 다름 | 결정 노트의 A/B 시험 결과로 정한다 |
| 3 | **기본값은 "등록 없는 프리셋"**, 등록은 고급 기능 | M1 문서가 이미 이 방향 | SCRUM-23~25·27은 범위를 다시 정의 |
| 4 | **건강 효과 문구 금지** | 근거 없음 | 제품·발표 자료에서 CTS 언급 제거 |

### 7.2 협업 방식

1. **계약 PR은 자기 외 1명이 본다.** `preview` 승인 면제는 유지하되, **`docs/design/03-interfaces.md`·`hand-mouse/docs/engine-contract.md` 같은 계약 파일 변경만** 다른 사람의 확인을 요구한다(승인 설정이 아니라 팀 합의로).
2. **Jira를 실제 작업과 연결한다.** PR 제목에 `SCRUM-번호`를 넣고, 담당을 다시 채운다. 연결 없는 PR이 많으면 진행률을 읽을 수 없다.
3. **"결정 전" 노트에 기한을 둔다.** `decision-pointer-click.md`에 시험 날짜와 결정자를 적는다.
4. **PR 작성자 표기.** 한 PC에서 푸시하더라도 PR 본문에 실제 작업자를 적는다(공동 작성 표기).
5. **주 1회 통합 시험.** `preview` 병합본으로 실제 웹캠 10분 조작을 하고 결과를 PR이나 Jira에 남긴다(`docs/branch-flow.md` 3번 규칙을 실제로 수행).

### 7.3 단계별 로드맵 (제안)

| 단계 | 내용 | 산출물 | 판단 기준 |
| --- | --- | --- | --- |
| 0 (1~2일) | 5.2 계약 확정, 손 좌표 기록 포맷 확정 | 계약 문서 1개 | 두 구현 담당이 합의 |
| 1 (3~5일) | 공용 재생 시험대, 필터 A/B | 시험대 + 비교 표 | 같은 기록에서 떨림·지연 수치 |
| 2 (1주) | 클릭 방식 A/B 실물 시험(3인 이상) | 결정 노트의 "결정" 절 | 오실행·성공·피로(자기 보고) |
| 3 | 기준 런타임 선택·나머지 이식 | 단일 앱 | 시험대 통과 |
| 4 | 정밀 모드·dwell 보조·시선/머리 보조 검토 | 접근성 옵션 | 사용자 피드백 |

---

## 8. 이 문서의 한계

- 웹 조사는 **검색 요약 위주**이고 논문 본문을 읽지 못했습니다. `미확인` 항목(MediaPipe의 z 신뢰도·가림 정확도, 클릭 중 커서 고정의 근거 자료, 마이크로 제스처의 피로 비교, 깊이 센서의 비용 비교)은 추측으로 채우지 않았습니다.
- 저장소 분석은 `origin/preview` 파일 읽기와 Jira·GitHub 조회입니다. **앱을 실행하지 않았고, 두 앱의 실제 jitter나 지연을 측정하지 않았습니다.**
- "구현 둘", "손가락 배정이 다름"은 저장소의 설계 노트에 적힌 내용을 인용한 것이며 코드를 줄 단위로 대조하지는 않았습니다.
- 협업 진단은 PR·커밋·Jira의 **메타데이터**에서 나온 것이고, 팀원들이 실제로 어떻게 소통하는지는 알지 못합니다.
- 제 산출물(등록 엔진, `resolveBinding`)에 대한 평가는 제 해석입니다. 사람 검수가 필요합니다.

## 출처 (직접 확인 / 요약)

- 확인: [MediaPipe Hand Landmarker](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker), [One Euro Filter](https://gery.casiez.net/1euro/)
- 요약: [MediaPipe Hands 논문(초록)](https://arxiv.org/abs/2006.10214), [Consumed Endurance 2014](https://blog.jhincapie.com/wp-content/uploads/2010/02/ga-final.pdf), [Dube 2022](https://www.yorku.ca/mack/iss2022.html), [Gaze+Pinch 설계 원칙](https://research.google/pubs/design-principles-and-challenges-for-gaze-pinch-interaction-in-xr/), [HoloLens point and commit](https://learn.microsoft.com/en-us/windows/mixed-reality/design/point-and-commit), [Meta 핸드트래킹](https://developers.meta.com/horizon/documentation/unity/unity-handtracking-interactions/), [Ultraleap TouchFree](https://docs.ultraleap.com/TouchFree/touchfree-user-manual/technical-glossary.html), [Camera Mouse 논문](https://www.cs.bu.edu/fac/betke/cs591/papers/gips-betke-dimattia.pdf), [Windows Eye Control](https://support.microsoft.com/en-US/Accessibility/windows/eye-control/eye-control-basics-in-windows), [macOS Head Pointer](https://mcmw.abilitynet.org.uk/how-to-control-your-computer-with-head-movement-in-macos-14-sonoma), [Project Gameface](https://github.com/google/project-gameface), [Meta Neural Band](https://about.fb.com/news/2025/09/meta-ray-ban-display-ai-glasses-emg-wristband/), [Apple Watch 더블탭](https://support.apple.com/en-ie/guide/watch/apdec70bfd2d/9.0)
- 저장소: `docs/design/decision-pointer-click.md`, `docs/m1-webcam-mouse/*`, `hand-mouse/docs/engine-contract.md`, `hand-mouse/src/filters.py`, `src/air-mouse-controller.js`, `docs/branch-flow.md` (모두 `origin/preview`, 2026-11-04)
