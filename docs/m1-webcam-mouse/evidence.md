# 웹캠 마우스 설계의 연구 근거

> 게시 참고: 코드 결합에 대한 아래 관찰은 2026-11-03 로컬 미커밋 작업트리의 읽기 검토다. 원격 main 또는 릴리스 구현의 재현 근거가 아니다. 경로와 행 번호는 그 로컬 사본의 참고 위치이며, 게시 범위와 한계는 [publication.md](publication.md)를 따른다.

연구는 특정 제스처를 정답으로 고르는 근거보다 **무엇을 분리해서 설계하고 비교해야 하는지**를 보여준다. 아래 결과는 각 연구의 장비·작업 안에서 관찰된 결과이며, MANUX의 가정용 웹캠 성능 수치가 아니다.

## 모드 유지와 기억 부담

Sellen·Kurtenbach·Buxton은 텍스트 편집에서 운동감각과 시각 피드백을 비교했다. 두 피드백 모두 모드 오류를 줄였고, 계속 누르는 발 페달은 토글 페달보다 모드 상태를 더 잘 전달했다. 사용자가 상태를 몸으로 유지하면 현재 모드를 기억하기 쉬울 수 있다는 근거다. 손가락 자세를 계속 유지하는 것이 편하다는 증거는 아니다.

**설계 판단:** 단계형은 지속 자세의 부담을 줄일 가능성이 있지만, 모드 망각·종료 누락이 증가하는지를 반드시 비교한다. 커서 주변 상태 표시가 모든 모드 오류를 해결한다고 가정하지 않는다.

출처: [Sellen et al. 1992 The Prevention of Mode Errors Through Sensory Feedback](https://www.billbuxton.com/ModeErrorsHCI.pdf).

## 팔 자세와 지속 사용의 부담

Consumed Endurance 연구는 어깨의 생체역학을 이용해 공중 조작의 피로를 분석했다. 해당 실험에서 팔을 굽히고 어깨와 허리 사이 높이에서 조작하는 조건의 소모가 낮았다. 비교한 한 손 선택 방식에서는 dwell의 소모가 낮았다. 이는 dwell이 모든 작업에서 가장 빠르거나 정확하다는 결론이 아니다.

**설계 판단:** 낮고 작은 작업 공간과 팔 지지가 가능한 조건을 비교한다. 손가락의 편안함뿐 아니라 어깨·팔·키보드 복귀·쉬고 싶어진 시점을 관찰한다. 손 관절만 기록해 CE로 정밀한 어깨 피로를 계산했다고 해석하지 않는다.

출처: [Hincapié-Ramos et al. 2014 Consumed Endurance](https://blog.jhincapie.com/wp-content/uploads/2010/02/ga-final.pdf).

## 한 손과 두 손의 모드 전환

Surale·Matulic·Vogel은 VR에서 11종 손 기반 모드 전환을 비교했다. 주손의 큰 자세 변경은 불리했고, 주손의 미세한 pinch 변형은 빠른 비주손 방식과 비슷한 전환 시간을 보였다. 이 결과는 두 손의 무조건적인 우월성을 지지하지 않는다.

이 실험은 LEAP과 HMD를 사용했으며, 빠른 자세 전환과 pinch 판정 문제를 줄이기 위해 사용자 보정 분류기와 접촉 압력 센서를 추가했다. 따라서 보고된 전환 성능을 RGB 웹캠만으로 달성할 수 있다는 근거로 사용할 수 없다.

**설계 판단:** 이동 손에 큰 모양 변화를 강요하지 않는 한 손 방식과, 다른 손에 명령을 맡기는 방식을 비교한다. 명령 인식과 연속 좌표 추적은 별도 실패 지표로 기록한다.

출처: [Surale et al. 2019 Experimental Analysis of Bare Hand Mid-air Mode-Switching Techniques in Virtual Reality](https://fmatulic.github.io/pubs/CHI2019.pdf).

## 클릭 제스처의 속도와 부담

Dube 등의 연구는 Push·Tap·Dwell·Pinch를 선택 과제에서 비교했다. Tap은 빠르고 정확한 후보였고, Dwell은 느리지만 정확하고 부담이 낮았다. Pinch는 비교적 빨랐지만 오류와 부담에서 불리했다. 실험에는 특수 손 추적 및 초음파 촉각 피드백 조건이 포함되어 있다.

**설계 판단:** “pinch는 작으니 가장 편하다”는 전제를 두지 않는다. 짧은 접촉·손가락 굽힘·dwell 후보를 피로·인지 부담·오실행·웹캠 가시성으로 평가한다. 포인터를 머무르게 하는 탐색과 선택 의도를 dwell이 혼동할 수 있다는 가설을 검증하기 위해 읽기·탐색 행동을 별도로 포함한다. 인용 연구의 dwell은 시선 입력이 아니라 손가락 위치 유지다.

출처: [Dube et al. 2022 Push Tap Dwell and Pinch](https://www.yorku.ca/mack/iss2022.html).

## 두 손의 속도와 정확도 상충

LeapPointer 연구에서 두 손 방식은 비교한 TouchFree보다 빨랐고 자기 보고 피로가 낮았지만 정확도도 낮았다. 비교 장비는 Leap Motion이며 작업 맥락은 공공 화면이었다. 마우스보다 두 손 방식이 우수하다는 결과로 해석하면 안 된다.

**설계 판단:** 두 손 선택에서 포인터와 확정을 나누는 이득이 있어도 오클릭이 늘면 기본값으로 자동 채택하지 않는다. 주손·보조손의 가림과 키보드 병행을 추가로 검증한다.

출처: [Lafuente et al. Comparing a Mid-air Two-hand Pinching Point-and-click Technique](https://academica-e.unavarra.es/entities/publication/7b67035f-9493-4cf9-b26c-cbbaf1a53d81).

## 포인팅 성능의 비교 방법

Soukoreff·MacKenzie는 포인팅 장치 비교에서 이동 거리·목표 크기·좌표 분산·오류를 함께 수집하는 방법을 정리했다. 속도만 기록하거나 성공한 시행만 남기면 장치 비교가 왜곡될 수 있다.

**설계 판단:** 작은 목표와 먼 목표를 포함하고 성공·실패·복구를 보존한다. Fitts 과제의 시간 정의를 고정하고, 별도로 모드 진입과 재배치까지 포함한 전체 작업 시간을 기록한다. 이 방법을 참고하는 것이 ISO 인증이나 현행 표준 전체 준수를 뜻하지는 않는다.

출처: [Soukoreff and MacKenzie 2004 Towards a Standard for Pointing Device Evaluation](https://www.yorku.ca/mack/ijhcs2004.html).

## 편한 이동 범위와 재배치

Nancel·Vogel·Lank의 CHI 2015 트랙패드 연구는 재배치의 영향을 전달 함수 효과와 분리해 비교했다. 모든 과제에서 재배치 없이 목표에 닿을 수 있었어도, 허용하지 않은 조작이 더 어렵고 오류·준비 시간이 늘었으며 더 빠르지 않았다고 저자들은 요약한다. 이는 트랙패드 결과이며 RGB 웹캠 드래그의 검증이 아니다.

**설계 판단:** 재배치 0회를 우선 목표로 두기보다 편한 범위·작은 목표·재배치·전체 시간을 함께 평가한다. 일반 마우스 대체를 주장하려면 긴 드래그가 작업 범위를 넘을 때의 경로도 필요하다.

출처: [저자의 Clutching Is Not (Necessarily) the Enemy 연구 요약](https://mathieu.nancel.net/). 연결된 HAL 전문은 이 회차에서 접근 차단되어 연구 요약을 넘는 세부 조건·수치는 인용하지 않았다.

RubberEdge는 위치 제어와 속도 제어를 결합하고, 전환 시 궤적·속도의 불연속과 탄성 피드백을 다뤘다. 특정 장치·과제의 성능을 가정용 웹캠 성능으로 옮기지 않는다.

**설계 판단:** 속도 제어·감도 증가는 재배치의 대안일 수 있지만 멈추기·정밀도·전환 피드백의 검증이 필요하다. 촉각 경계가 없는 손 조작에서 같은 전환을 사용한다고 가정하지 않는다.

출처: [Casiez et al. RubberEdge, UIST 2007 원문](https://arxiv.org/pdf/0804.0556).

## 추적과 자세 분류의 기술적 구분

MediaPipe Hand Landmarker는 손의 관절 위치와 handedness를 출력하며, 연속 영상에서 손 추적과 필요할 때의 재검출을 구분한다. 손 좌표 추적은 특정 명령 손모양 클래스를 계속 맞혀야 한다는 제품 규칙과 동일하지 않다.

**설계 판단:** 좌표 추적 품질과 명령 인식 품질의 문턱을 같은 값으로 취급하지 않는다. 다만 관절이 실제로 가려지거나 신원이 바뀐 경우는 명령 모드를 기억하는 것만으로 좌표가 복구되지 않는다.

출처: [Google MediaPipe Hand Landmarker 공식 가이드](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker).

Python 가이드의 min_tracking_confidence는 손 경계 상자의 프레임 간 IoU 문턱으로 설명된다. 이 값이나 손의 검출/존재 설정을 버튼용 집기의 유효성·실제 접촉 신뢰도로 읽지 않는다. G0/R2의 정상 전이와 필수 손가락 근거 관찰불가를 구별할 수 있는지는 별도 검증이 필요하다. [Python 구성·결과 가이드](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/python)

1€ Filter의 저자 설명은 잡음 억제와 지연 사이의 조정을 다룬다. **설계 판단:** 커서가 덜 떨리는지만 보고 필터를 고르지 않고 정지·느린 조준·빠른 이동·버튼 전이의 지연을 함께 본다. 위치 평활화와 버튼 확정/해제 규칙은 별도 요인으로 두며, 한 후보만 더 강하게 평활화한 차이를 제어 문법의 효과로 해석하지 않는다. 필터가 의도·가림·손 신원 문제까지 해결하는 것으로 설명하지 않는다. [1€ Filter 저자 가이드](https://gery.casiez.net/1euro/)

## Windows에서 마우스 대체를 말할 수 있는 범위

Win32의 마우스 입력 예시는 down에서 시작 위치와 입력 캡처를 정하고, 눌림 중 이동을 처리한 뒤 up으로 마치는 흐름을 보여 준다. WPF ClickMode도 누름·해제·hover에 반응하는 시점을 구분한다. 완성된 클릭 한 쌍만 보내는 것이 가능한 입력 설계의 전부는 아니며, down이 아직 앱에 영향을 주지 않았다고 가정할 수도 없다.

**설계 판단:** 닫힘→OS down·열림→up을 직접 대응하는 G0를 별도 선행 후보로 검토한다. 기존 B1의 클릭/드래그 사전 판별과 구분하고, 정지 누름·작은 클릭·짧은 드래그에서 앱 결과를 확인한다. 이 문서/API의 예시는 RGB 인식 성능이나 G0의 인간공학적 우월성을 입증하지 않는다.

출처: [Microsoft Using Mouse Input](https://learn.microsoft.com/en-us/windows/win32/inputdev/using-mouse-input), [WPF ClickMode](https://learn.microsoft.com/en-us/dotnet/api/system.windows.controls.clickmode?view=windowsdesktop-10.0).

Microsoft의 SendInput 문서는 입력 주입이 UIPI의 적용을 받으며 같은 수준 이하의 무결성 수준을 가진 앱에만 입력할 수 있다고 설명한다. 따라서 일반 앱에서의 시연을 관리자 권한 앱이나 모든 시스템 화면에서도 동작한다는 약속으로 확대하면 안 된다.

**설계 판단:** 일반 앱 사이 전환, 지원하지 않는 대상에서의 중지·안내, 실제 입력 전달 성공과 대상 앱 작업 완료의 차이를 검증 항목으로 둔다. 이 제한을 없애기 위해 앱을 무조건 관리자 권한으로 실행하는 것을 제품 기본값으로 정하지 않는다.

출처: [Microsoft SendInput 공식 문서](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput).

## 현재 코드에서 확인된 사실

아래는 문헌이 아니라 2026년 11월 3일 로컬 소스의 읽기 검토 결과다. 설계 검토 후 구현으로 바뀔 수 있다.

| 현재 결합 | 근거 파일 | 영향 |
| --- | --- | --- |
| 매 프레임 사용자 모양을 action으로 전달 | `src/app.js:398` (로컬 작업트리 검토) | 모양 분류 실패가 지속 의도의 종료처럼 전달됨 |
| action이 없으면 interrupt하고 모션 기준을 지움 | `src/registered-mouse-controller.js:70` (로컬 작업트리 검토) | 좌표를 추적해도 커서가 끊길 수 있음 |
| move·left·scroll에서만 연속 이동 사용 | `src/registered-mouse-controller.js:105` (로컬 작업트리 검토) | 모양 유지와 연속 제어가 결합됨 |
| 기본 방식도 관절 상태로 모드를 다시 판정 | `src/air-mouse-controller.js:208` (로컬 작업트리 검토) | 사용자 등록 기능만 고쳐 해결되지 않음 |
| 한 손 추론과 첫 번째 손 소비 | `src/vision-worker.js:8` (로컬 작업트리 검토) | 두 손에는 지속 신원·역할·교차·소실 규칙이 추가로 필요 |

따라서 현재 자동 시험 수와 수집 데이터 용량은 새 조작 방식의 편안함이나 마우스 대체 성능을 입증하지 않는다. 기존 프레임 관리·세션 분리·입력 중지·저장 보존은 재사용을 검토할 기반이며, 기존 모양 유지 규칙은 비교 기준이다.

## 데이터 확보의 다음 단위

사용자·카메라·세션을 분리한 **완전한 행동 시도**를 평가 단위로 삼는다. 시작부터 완료·실패·재시도·종료까지 남겨 전환 중 어떤 문제가 생겼는지 볼 수 있어야 한다. 인접 프레임을 독립 성공으로 늘리지 않는다.

우선 수집할 사례는 기본 동작의 자연스러운 시작과 종료, 클릭 중 손 위치 변화, 타이핑과 포인팅의 왕복, 얼굴 만지기와 물 마시기, 보조손 진입·교차, 손 가림과 재진입이다. 이 사례들이 설계 결함과 센서 결함을 구분하게 한다. 특정 포즈 사진을 대량 확보하기 전에 실제로 실패하는 사건과 조건을 정한다.
