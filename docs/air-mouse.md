# 손 전체로 움직이는 마우스

2026-11-02 추가 개발. 기존 영상 네 동작과 별도의 마우스 모드이며, 기본 손바닥 자세에서 손가락만 바꿔 명령을 선택한다. 커서는 손끝이 아니라 손목과 손바닥 관절의 평균 위치를 따른다. 개인 등록 없이 사용할 수 있고 MediaPipe 모델을 재학습하지 않는다.

| 자세 | 동작 |
| --- | --- |
| 손바닥을 편 상태에서 손 전체 이동 | 상대 이동으로 커서 조작 |
| 같은 자세에서 엄지·검지 붙이기 | 잠깐 유지 후 좌버튼 누름, 떼면 놓음; 붙인 채 손을 움직이면 드래그 |
| 엄지·중지 붙이기 | 유지가 확인되면 우클릭 한 번; 다시 벌려야 재실행 |
| 엄지·약지를 붙인 채 위아래 이동 | 손의 이동량에 따라 스크롤; 멈추면 스크롤도 멈춤 |
| 손가락 접기·주먹·손 내리기 | 이동 중단, 눌린 버튼 해제 |

켜자마자 클릭되지 않도록 먼저 손바닥을 잠깐 펴야 한다. 클릭 자세는 기본 160ms와 최소 3회 관찰을 거쳐 확인한다. 이는 판정 설정이며 실제 전체 지연이나 인식률 보장값이 아니다. 두 손가락 핀치가 겹치면 실행을 보류한다. 커서 상대 이동은 모드 전환·손 소실·긴 입력 공백에서 기준 위치를 다시 잡는다.

## 실행

프로젝트 폴더에서 `npm ci --ignore-scripts`, `npm run desktop:setup`, `npm run setup`을 처음 한 번 실행한다. 이후 **MANUX 마우스 실행.cmd** 또는 `npm run desktop`으로 실행한다. Windows PowerShell과 Windows 사용자 입력 API를 사용하며 Python이나 별도 네이티브 npm 모듈은 요구하지 않는다.

1. 카메라를 시작한다.
2. `이 화면에서 연습`을 선택하고 `마우스 제어 켜기`를 체크한다.
3. 커서·클릭·드래그·우클릭·스크롤을 연습한다. 손가락만 움직일 때 커서가 크게 움직이지 않는지 확인한다.
4. 데스크톱 앱에서 제어 대상을 `Windows 마우스`로 바꾸고 제어를 다시 켠다.
5. 실제 앱 창을 조작한다. 다른 앱으로 포커스가 바뀌어도 동작하며 MANUX를 최소화하면 제어가 꺼진다.

`Esc`는 MANUX 창에서, **Ctrl + Alt + M**은 다른 앱에서도 마우스 제어를 끈다. 카메라 중단·오래된 입력·창 종료에서도 버튼을 해제한다. 제어는 실행할 때마다 기본 꺼짐이며, 모드/대상/감도 변경 후 다시 켜야 한다. 브라우저의 `npm start`는 연습만 가능하고 OS 커서를 움직이지 않는다.

## 연결 구조와 계약

`MediaPipe Worker → 손 관절 → AirMouseController → MouseWorkspace → 연습 영역 또는 preload → Electron main → Windows helper → SendInput`

- 기존 개인 등록·영상 제어와 동시에 명령을 보내지 않는다.
- `AirMouseController.update({landmarks,now,enabled,quality,handedness,width,height})`는 `mode`, `phase`, `progress`, `events`를 돌려준다. OS 호출이 없는 순수 판정기다.
- 이벤트는 `move(dx,dy)`, `button(left/right,down)`, `scroll(dy)`, `release`만 허용한다. 이동은 프레임당 축별 100px 이내, 스크롤은 240 이내로 제한한다.
- `window.manuxDesktop`은 상태 조회·활성화·마우스 이벤트·중지·상태 알림과, 인자를 받지 않는 앱 업데이트 상태 조회·확인·설치 승인(`docs/desktop-release.md`)만 제공한다. 임의 코드/셸 실행이나 키보드 입력은 노출하지 않는다.
- 실제 새 추론 결과마다 빈 배열을 포함해 전달한다. 타이머로 OS 입력 유효 기간을 연장하지 않는다. renderer, main, native helper가 각각 입력 중단을 감시한다. 자동으로 다시 활성화하지 않는다.
- 같거나 오래된 타임스탬프의 프레임(`phase: 'stale'`)은 새 추론이 아니다. 입력을 재생하지 않으며 정지 감시와 Windows 입력 유효 기간도 연장하지 않는다. 그래서 프레임이 멈추면 약 0.7초 안에 제어가 꺼지고 눌린 버튼이 해제된다(합성 시험으로 확인; 실제 카메라 끊김 시간은 별도 측정 필요).
- 카메라 영상은 서버로 전송·저장하지 않는다. 마우스 입력도 서버 HTTP API에 노출하지 않는다.

## 양손 모드 PoC (SCRUM-21)

보조손으로 제어를 허용하고 주손으로 커서를 움직이는 **실험용** 모드다. `AirMouseController` 수준에서만 구현했고 기본은 꺼짐이다. 실제 양손 추적 입력(추적 V2), `MouseWorkspace`, 설정 UI는 연결하지 않았다. 사용자에게 유용한지, 피로나 손목·팔 부담이 줄어드는지는 **측정하지 않았다**.

### 구현됨

- 생성자 옵션: `twoHand`(기본 `false`), `primaryHand`(기본 `'Right'`; `'Left'`도 가능). 다른 쪽 손이 보조손이다. 값이 틀리면 `RangeError`다.
- `twoHand:false`이면 기존 단일 손 입력·출력과 시험 결과는 변하지 않는다. `hands` 필드는 무시되고 결과에 새 필드도 없다.
- `twoHand:true`일 때 `update({hands, now, enabled, width, height})`를 쓴다. `hands`는 `[{landmarks, handedness:'Left'|'Right', quality?}]`이며 **두 손이 모두 보여야 한다.** `quality`가 없으면 `true`로 본다. 기존 `landmarks`/`handedness` 단일 필드는 이 모드에서 쓰지 않고, `hands`가 없으면 `invalid`로 이동 없이 중단한다.
- 반환값에 `auxActive`(불리언)가 추가된다. `twoHand:true`에서만 나타난다.
- 보조손 활성화: 보조손이 기본 손바닥 자세(손가락 3개 이상 펴짐)를 기존 `holdMs`와 최소 관찰 횟수만큼 유지하면 `auxActive`가 된다. 그동안 단계는 `holding-aux`이고 이벤트는 없다. 이후 주손은 기존 단일 손 규칙(기본 자세 대기 → 이동·클릭·스크롤)을 그대로 따른다. 주손 기준점은 활성화 때 새로 잡으므로 켜자마자 클릭이나 커서 점프가 없다.
- 보조손이 손바닥 자세가 아니면 단계는 `aux-required`이고 이동·클릭이 없다.
- 즉시 중단 후 보조손을 다시 유지해야 하는 경우(재활성화 대기): 활성화 중 보조손 자세 해제(`aux-released`), 손 하나라도 소실·품질 저하·잘못된 관절(`invalid`), 두 손 근접·교차(`hands-crossed`, 손 기준점 거리 0.2 미만, 영상 너비 단위), 라벨 문제나 좌우 순서 뒤바뀜(`role-confusion`), 긴 입력 공백(`tracking-reset`). 중단하면 이동이 멈추고 눌린 왼쪽 버튼이 해제되며 `{type:'release'}`가 나간다.
- 오래된 프레임(`stale`)은 기존처럼 아무 상태도 바꾸지 않는다. 사용 해제(`enabled:false`)는 기존처럼 초기화하며 자동으로 다시 켜지지 않는다.

### 제안 (다른 파트가 맞춰야 할 값)

- 추적(V2)이 한 프레임의 두 손을 `hands` 배열로 주고, 각 항목에 MediaPipe `categoryName`(`'Left'`/`'Right'`)을 `handedness`로 붙인다. 이 이름은 이 PoC가 임의로 정한 계약이며 추적 담당과 합의되지 않았다.
- 좌우 순서 뒤바뀜 감지는 두 손 기준점의 가로 순서가 바뀌었는지만 본다. 라벨 자체의 오류(같은 손에 반복해서 잘못된 라벨)는 감지하지 못한다. 카메라 좌우 반전과 라벨 의미는 추적 쪽에서 정해야 한다.
- 근접 임계값(0.2)과 보조손 자세는 합성 입력으로만 정했다. 실제 손 크기·거리에서 맞는 값은 측정하지 않았다.
- 채택 또는 보류를 정하려면 실제 사용자 시험이 필요하다. 제안 항목: 한손 대비 작업 시간, 오실행 횟수, 보조손 소실·손 교차 발생 횟수, 팔을 지지한 자세에서 손목·손가락·팔·어깨의 사용 전후 불편도. 통증·저림·중단 요청 시 시험을 멈춘다. 질환 예방이나 치료 효과는 주장하지 않는다.

## 검증과 한계

`npm test`는 기존 회귀와 판정/입력 계약을, `npm run test:mouse`는 합성 관절과 캔버스 카메라로 화면 연결을 검사한다. `node scripts/test-desktop-mouse.mjs`는 실제 PowerShell/C# helper를 사용하되 dry-run으로 운영체제 입력을 보내지 않는다. 자동 검사는 실제 웹캠 인식 정확도나 사용 편의성을 증명하지 않는다.

2026-11-02 검사 결과: 단위·서버 회귀 162개 통과, 합성 관절 브라우저 연결 검사 통과, 기존 실제 MediaPipe·합성 카메라 영상 회귀 통과. `node scripts/test-desktop-shell.mjs`에서 실제 Electron의 카메라/Worker 초기화, 마이크 거부, 입력 활성화·중지와 프레임 중단 시 해제를 확인했다. 네이티브 입력 검사는 모두 dry-run이며 사용자의 실제 커서에 클릭을 보내지 않았다. 창이 가려져도 추론을 이어가도록 데스크톱에서는 화면 그리기 주기와 독립된 타이머를 사용한다.

엄지·약지 핀치는 손가락 가림이나 손목 회전 때문에 특히 실물 확인이 필요하다. 작은 손 영상·조명·측면 자세에서는 인식이 끊길 수 있다. Windows의 권한 경계 때문에 관리자 권한 앱과 보안 화면까지 제어한다고 보장하지 않는다. 더블클릭 전용 동작, 수평 휠, 사용자별 마우스 동작 변경, 설치 파일 배포는 이번 범위에 포함하지 않았다.

참고: [Electron contextBridge](https://www.electronjs.org/docs/latest/api/context-bridge), [Electron 보안](https://www.electronjs.org/docs/latest/tutorial/security), [Windows SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput), [Windows MOUSEINPUT](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput).
