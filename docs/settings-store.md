# 설정 저장소 계약 (A2 / SCRUM-30)

2026-11-02 추가. `src/settings-store.js`는 마우스 설정, 등록 동작, 액션 연결을 하나의 버전 있는 레코드로 저장한다. 기존 영상 모드의 `manux.profiles.v1`은 읽거나 바꾸지 않는다. [모듈 계약](contracts.md) §2·§5의 제안을 M1 범위(책상 프리셋 1개, 사용자 1명)로 줄여 구현했다.

## 저장 형식

localStorage 키 `manux.settings.v1`, 직렬화 상한 2,000,000자.

```js
{
  version: 2, revision: 3, savedAt: '2026-11-03T01:00:00.000Z',
  mouse: {sensitivity: 1800, mainHand: 'right', area: {left: 0, top: 0, right: 1, bottom: 1},
         pointer: {hands: 'two', engine: 'index', click: 'trigger', anchor: 'index'}},
  environments: {
    desk: {
      registrations: {'custom:ok-sign': /* G1 registerGesture 결과 그대로 */},
      verifications: {'custom:ok-sign': {verifiedAt: '2026-11-03T01:00:00.000Z', sessionId: 'enroll-1'}},
      bindings: {'custom:ok-sign': {gestureId: 'custom:ok-sign', actionId: 'mouse.left-click', enabled: true}},
    },
  },
}
```

| 필드 | 규칙 |
| --- | --- |
| `mouse.sensitivity` | 600~3600, 100 단위 (현재 슬라이더와 같음) |
| `mouse.mainHand` | `right` \| `left` \| `null`(아직 고르지 않음, 데스크톱 첫 실행에서 물어봄) |
| `mouse.pointer` | 데스크톱 스튜디오의 포인터 설정. `hands`: `two`(기본, 한 손 이동·다른 손 동작)\|`one`, `engine`: `index`\|`palm`, `click`: `trigger`\|`off`, `anchor`: `index`\|`palm`. 기본 `index`·`trigger`·`index` |
| `mouse.area` | 0~1 비율, 가로·세로 0.2 이상. 아직 판정기가 사용하지 않는 저장 전용 값 |
| `registrations` | G1의 `validateRegistrations`로 검사. 레코드는 G1이 만든 그대로 저장하고 A2가 필드를 덧붙이지 않는다 |
| `verifications` | 모든 등록에 하나씩 있어야 한다. 확인을 거치지 않은 후보는 저장할 수 없다 |
| `bindings` | 등록된 동작만 참조. 동작당 하나. 저장된 값은 형식(`영문.영문`)만 검사하고, 새로 연결할 때는 허용 액션 목록에 있어야 한다 |

## API

```js
const store = new SettingsStore({validateRegistrations, allowedActions});
store.settings            // 마지막으로 커밋된 값의 복사본
store.status              // empty | loaded | corrupt | newer | unavailable
store.error               // 불러오기 문제가 있으면 사용자 안내 문구
store.setMouse({sensitivity, mainHand, area})
store.resetMouse()        // 마우스 설정만 기본값. 등록 동작은 그대로
store.putGesture({registration, verifiedAt, sessionId})   // 한 동작만 추가·교체, 연결 유지
store.removeGesture(gestureId)                            // 등록·확인 기록·연결을 함께 삭제
store.setBinding({gestureId, actionId, enabled})
store.removeBinding(gestureId)
```

모든 변경 함수는 같은 트랜잭션을 거친다: 복사본에 적용 → 전체 검사 → 한 번 쓰기 → 다시 읽어 일치 확인 → 그 뒤에 메모리 갱신. 실패하면 예외를 던지고 메모리와 저장소는 그대로다. 호출자는 예외 메시지를 사용자에게 보여 주고 이전 화면 상태를 유지한다.

## 실패·버전 처리

| 상황 | 동작 |
| --- | --- |
| 저장 공간 부족·쓰기 예외 | 예외, 기존 값 유지 |
| 쓴 값이 다시 읽히지 않음 | 이전 원문으로 되돌리고 예외 |
| 검사 실패(범위·형식·미확인 등록·미등록 동작 연결·허용 밖 액션) | 쓰기 전에 예외 |
| 저장 원문이 깨짐 | 기본값으로 실행, 원문은 지우지 않음. 첫 저장 전에 `manux.settings.v1.backup`에 한 번 복사 |
| 더 새 버전(`version` > 2)이 저장한 값 | 읽기 전용, 기본값으로 실행, 저장 거부 (자동 업데이트 후 이전 버전 실행 대비) |
| 다른 창이 먼저 저장 | 덮어쓰지 않고 예외 ("앱을 다시 열어 주세요") |
| 저장소 접근 자체가 실패 | 읽기 전용, 기본값 |

## 다른 파트가 연결할 부분 (미구현)

- **G1(SCRUM-23):** 연결됨. `app.js`가 `new SettingsStore({validateRegistrations})`로 G1 검증기를 넘긴다. 저장 경로는 `trainGesture` → `registerGesture(현재 등록, 후보)`로 충돌 재검사 → 해당 동작 하나만 `putGesture`. `tests/settings-store-gestures.test.js`가 실제 G1 함수로 재실행 복원·판정을 확인한다(합성 특징).
- **U2(SCRUM-27):** 확인 단계가 `verified`일 때만 `putGesture`를 호출하고, `sessionId`는 등록 세션 ID를 쓴다. 예외가 나면 저장 실패 안내를 보여 주고 다른 동작 목록은 바꾸지 않는다.
- **C3(SCRUM-22):** 허용 액션 ID 목록을 정해 `allowedActions`로 넘긴다. 저장소는 액션 이름을 해석하지 않는다. 알 수 없는 액션이 연결된 동작은 실행 시 명령 없음으로 처리한다.
- **U3(SCRUM-28):** 주손·영역 설정 화면은 `setMouse`, 기본값 복원은 `resetMouse`를 쓴다.

## 시험과 한계

`tests/settings-store.test.js`가 위 표의 경우를 메모리 저장소로 검사한다. 등록 레코드는 G1 검증기의 대역(stub)으로 시험했다. 실제 G1 검증기와의 연결, 실제 Electron 재실행 복원은 별도로 기록한다. 영상 모드 프로필(v1)의 변환은 이번 범위가 아니다. 프로필 키와 같은 localStorage 할당량(브라우저 기본 약 5MB)을 나눠 쓰므로, 용량 부족은 저장 예외로 처리될 뿐 미리 막지 않는다.

## 버전 기록

- v2 (2026-11-04): `mouse.pointer` 추가(기본값으로 채움), `mouse.mainHand`에 `null` 추가, 기본값 `null`. v1 기록은 읽을 때 v2로 옮긴다. v1은 늘 기본값 `right`를 저장했으므로 `left`만 유지하고 나머지는 `null`(다시 물어봄). 첫 저장 때 v2로 쓴다. v1 앱이 v2 기록을 만나면 "더 새 버전" 규칙대로 읽기 전용이 된다. 저장 키 이름(`manux.settings.v1`)은 그대로다.
