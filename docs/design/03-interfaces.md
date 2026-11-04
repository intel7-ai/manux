# 03. 인터페이스 정의서 (M1)

> 상태: **AI 초안, 사람 검수 전** (2026-11-03). 입력: [02 컴포넌트](02-components.md)와 실제 export. **구현됨**은 2026-11-03 `preview`(PR #10·#11 포함)와 열린 PR #12·#13(마우스 정지 감시·양손 PoC)의 코드에 있는 시그니처이고, **제안**은 아직 코드에 없다. 세부 수치·이유 코드는 [contracts](../contracts.md) §9, [air-mouse](../air-mouse.md), [settings-store](../settings-store.md)가 원본이다. 이 문서와 다르면 코드가 사실이고, 이 문서를 고친다.
>
> 함수마다 **실패하면** 줄을 둔다: 예외를 던지는가 / `null`을 주는가 / `false`·`accepted:false`를 주는가 / 빈 값인가. 같은 반환 타입이라도 "빈 결과"와 "실패"는 다른 계약이다.

## 추적 → 앱 조립

```js
cameraConstraints(preset = '1080', deviceId = '') -> MediaStreamConstraints
// 해상도 프리셋을 getUserMedia 제약으로 바꾼다. 실패: 없음(알 수 없는 프리셋은 기본값).

new VisionClient({workerFactory, createBitmap, initTimeoutMs = 45000, frameTimeoutMs = 10000})
client.init() -> Promise<{delegate: 'GPU' | 'CPU'}>
// Worker를 만들고 모델을 한 번만 준비한다. 실패: reject + close(). 같은 client는 다시 쓰지 않는다.
client.recognize(video, timestamp) -> Promise<{result, inferenceMs} | null>
// 프레임 한 장을 추론한다. null = 처리 중(busy)이거나 닫힘 — 실패가 아니라 "이번 프레임 건너뜀".
// 실패: 시간 초과·Worker 오류는 reject하고 세션 전체를 close()한다(밀린 프레임 없음).
// inferenceMs: Worker 안 recognizeForVideo 소요 ms(왕복 아님).
client.close(error?) -> void   // 대기 중 요청을 모두 reject. 여러 번 불러도 안전.

extractOrientation(landmarks, {width, height, mirrored = true}) -> {valid, yaw, pitch, ...}
// 손 방향(도). 실패: valid:false (예외 없음).
```

`result`는 MediaPipe GestureRecognizer 결과 그대로다(`landmarks[0]`, `handedness[0][0].categoryName`, `gestures[0][0]`). **한 손만** 들어온다(`numHands: 1`).

## 판정 (순수 함수, 입력을 바꾸지 않음)

```js
getHandQuality(landmarks, {width, height}) -> {ok, reason, spanPx, shortSidePx, boundaryCutoff}
// 입력 사용 가능 여부. reason: ok | near-frame-edge | hand-too-small | invalid-landmarks.
// 실패: 예외 없음, ok:false. spanPx = 손 상자 긴 변 픽셀 수.
extractFeatures(landmarks, {width, height}) -> number[37] | null
// 이동·크기·좌우 반사에 무관한 특징. 실패: null (잘못된 관절·0 크기).

trainGesture({gestureId, viewSamples, existingRegistrations = {}, rejectionExamples = []})
  -> {candidate | null, usable, conflicts, reason, diagnostics}
// 동작 하나의 후보 학습. 실패 두 종류:
//  - 입력 형식 오류(잘못된 ID, 각도별 12개 미만, 잘못된 벡터) → TypeError
//  - 학습은 됐지만 쓸 수 없음 → usable:false, candidate:null, reason ∈
//    unstable-samples | conflicts-with-registered-gesture | rejection-example-too-close |
//    acceptance-radius-too-small | too-many-gestures | invalid-existing-registration
// 어느 경우에도 existingRegistrations는 바뀌지 않는다.
registerGesture(registrations, candidate) -> registrations'
// candidate.gestureId 하나만 추가·교체한 새 레코드. 실패: 잘못된 후보·기존 레코드 → TypeError,
// 다른 동작과 충돌·12개 초과 → RangeError. 입력은 그대로.
removeGesture(registrations, gestureId) -> registrations'
// 실패: 잘못된 ID → TypeError. 등록돼 있지 않은 ID → 같은 내용의 복사본(멱등, 오류 아님).
validateRegistrations(registrations) -> {valid, errors: string[]}
// 저장·불러오기 검사. 실패: 예외 없음, valid:false. actionId 필드가 있으면 거절.
classifyRegisteredGesture({features, registrations})
  -> {gestureId | null, accepted, kind: 'match'|'unknown'|'invalid', reason, distance, margin | null}
// 등록 동작만 대상으로 판정. 실패: 예외 없음, accepted:false, gestureId:null.
// reason: matched | outside-enrollment | ambiguous-gesture | no-registrations | invalid-features | invalid-model.
// distance: 가장 가까운 템플릿까지 특징 거리. margin: 2등과 거리 차, 등록이 하나면 null.
```

영상 모드(UC-08) 경로 `trainEnvironment`·`classifyPersonal`·`validateProfile`은 기존 그대로다. 결과 키가 `gesture`(새 경로는 `gestureId`)인 점이 다르다.

## 등록 흐름

```js
new EnrollmentController({prepareMs, captureMs, sampleIntervalMs, minSamplesPerView = 12, ...})
// 실패: 시간·표본 설정이 서로 맞지 않으면 TypeError.
ctrl.start({now, environment: 'desk'|'sofa', gestureId?}) -> snapshot
// gestureId가 있으면 그 동작 하나를 5개 각도로 수집. 없으면 기존 5자세 흐름. 실패: 잘못된 환경·ID → TypeError.
ctrl.update({now, features, quality, orientation, handedness, match?}) -> snapshot
// 새 관찰만 표본이 된다. 타이머 호출(update({now}))은 표본을 만들지 않는다.
// 실패: now가 거꾸로 감 → TypeError. 나쁜 관찰은 예외 없이 거절 이유(snapshot.reason)만 바뀐다.
ctrl.startVerification({now}) -> snapshot   // 실패: phase가 captured가 아니면 Error.
ctrl.retry({now}) -> snapshot               // 실패한 그 각도만 다시. 실패: phase가 failed가 아니면 Error.
ctrl.cancel() -> snapshot                   // 표본 폐기, phase = cancelled. 실패 없음.
commandsSuppressed(snapshot) -> boolean
// idle·cancelled일 때만 false. 스냅샷이 없거나 알 수 없는 단계면 true(안전 쪽).
```

`snapshot.phase` 값: `idle`, `prepare`, `calibrate`, `capture`, `captured`, `verify-prepare`, `verify`, `verified`, `failed`, `cancelled`. `snapshot.sampleGroupsByPose[gestureId]`가 `trainGesture`의 `viewSamples` 입력이다.

## 제어

```js
new AirMouseController({sensitivity = 1800, smoothing = 0.45, holdMs = 160, twoHand = false, primaryHand = 'Right'})
// 실패: 범위 밖 값 → RangeError.
ctrl.update({landmarks, now, enabled, quality = true, handedness, width, height, hands?})
  -> {mode, phase, progress, events, auxActive?}
// 관절 → 마우스 이벤트. OS 호출 없음. 실패: 예외 없음 — 나쁜 입력은 phase(invalid 등)와
// 해제 이벤트로 표현한다. phase 'stale' = 같거나 오래된 타임스탬프, 상태 불변·이벤트 없음.
// events: move{dx,dy} | button{button:'left'|'right', down} | scroll{dy} | release.
// progress: 0~1, 클릭 자세 유지 진행률.
ctrl.reset() -> {phase:'disabled', events:[{type:'release'}]}

new MouseWorkspace({root, bridge = window.manuxDesktop, sensitivity, onSensitivityChange})
ws.update({landmarks, now, quality, handedness, width, height, latency}) -> state | null
// 활성 상태면 판정 결과를 연습 영역 또는 bridge.send로 보낸다. null = 마우스 모드 아님.
// 실패: latency > 450 또는 700ms 동안 새 프레임 없음 → stop(이유). 예외 없음.
ws.toggle() / ws.stop(text?) / ws.setReady(bool) / ws.setActive(bool) / ws.status(text, isError?)
// stop은 항상 버튼 해제·bridge.stop()을 포함한다. 다시 켜기는 사용자 조작으로만.

new GestureController({holdMs = 300, minConfidence = 0.7, repeatEnabled = false, ...})   // 영상 모드
gate.update({gesture, score, now, handPresent, neutral, quality, enabled}) -> {phase, action | null, progress}
// 실패: 설정 모순 → RangeError. 시간 역행은 phase invalid-time/clock-reset, action:null.
applyMediaAction(video, 'pause'|'play'|'forward'|'backward') -> Promise<{action, before, after, paused}>
// 실패: 지원하지 않는 명령·영상 없음 → Error, 재생 차단 → NotAllowedError(reject).
```

**구현됨 (C3, `src/binding-resolver.js`, 순수 함수 · 앱에는 아직 연결되지 않음):**

```js
resolveBinding({decision, bindings, allowedActions}) -> {actionId | null, reason}
// decision = classifyRegisteredGesture 결과. 실패: 예외 없음, actionId:null.
// bindings = 설정 레코드의 environments.<환경>.bindings (gestureId -> {gestureId, actionId, enabled}).
// allowedActions = 배열 또는 Set. 형식이 틀린 입력은 모두 명령 없음으로 끝난다.
// reason: bound | not-accepted | no-binding | disabled | unknown-action.
//   not-accepted: accepted:true·kind:'match'·올바른 gestureId가 모두 맞지 않으면 (미수락·모호·오류·위조). 공통 모델 결과로 대체하지 않는다.
//   no-binding: 연결이 없거나 행의 gestureId가 키와 다르거나 형식이 틀림.   disabled: enabled:false (알 수 없는 액션보다 먼저 판정).
//   unknown-action: MOUSE_ACTIONS에 없거나 allowedActions에 없는 액션.
MOUSE_ACTIONS / ALLOWED_ACTION_IDS   // 허용 액션의 유일한 정의: mouse.left-click, mouse.right-click, mouse.pause
eventsForAction(actionId) -> 이벤트 배열 | null   // 클릭은 button down+up, pause는 [] (호출자가 제어를 끈다), 모르는 id는 null
findSharedActions(bindings) -> [{actionId, gestureIds}]   // 같은 액션을 쓰는 활성 연결 보고(정보용)
```

정책(구현됨): 한 동작에는 연결이 하나다(레코드가 gestureId 키이므로 두 번째 `setBinding`은 교체). 여러 동작이 같은 액션을 가리키는 것은 **허용하고 거절하지 않는다**: 한 프레임은 하나의 판정만 받으므로 같은 프레임에서 두 번 실행되지 않는다. 화면(U3)은 `findSharedActions`로 경고할 수 있다. 스크롤·드래그는 연속 손 이동이 필요해 정적 손 모양에 연결할 수 없으므로 허용 목록에서 뺐다.

**제안 (미구현, 앱 조립/R5):** 판정 → `resolveBinding` → `eventsForAction` → `bridge.send`/연습 영역 연결, 액션 실행 결과 기록, 연결 변경 시 반복·잠금 초기화. `mouse.pause` 처리(제어 끄기)도 앱 조립이 맡는다.

**조작 방식 프리셋 (구현됨, 2026-11-04):** 모든 엔진은 `update({landmarks, now, enabled, quality, width, height}) -> {mode, phase, progress, events}`와 `reset()`을 같은 뜻으로 구현한다. `MouseWorkspace.makeController()`가 `#mouse-engine` 값으로 고른다.

```js
new IndexPointerController({sensitivity = 1800, clickStyle: 'trigger'|'off', anchor = 'index', screenWidth})
// 실패: 범위 밖 값 → RangeError. update는 예외 없음.
// phase: stale(같거나 오래된 시각, 이벤트 없음) | disabled | invalid(손 없음·품질 미달, 눌린 버튼 해제) | tracking | latched | holding
// events: AirMouseController와 같은 move/button/release, 한 번에 8개 이하, 이동은 축별 100px 이하.
new PointerEngine().step(t, points, aspect, {clickStyle, anchor, axes, config}, {active})
  -> {action, preview, pointer, cursor, source, warp, latched, speed, armed, ratio, contacts} | null
// hand-mouse/docs/engine-contract.md의 PointerFrame. 손이 없으면 null.
buildOverlayFrame(landmarks, {mode, width, height}) -> {tips, contacts, mode} | null
// 손 없음·퇴화한 손 → null(오버레이가 흐려짐). 알 수 없는 mode → 'other'.
```

**Input Studio 제어 세션 (구현됨, 2026-11-04):**

```js
new MouseSession({bridge, controller, clock, onChange, timers})
session.start() / stop(text, error) / toggle() / setController(controller) / setOverlay(boolean)
session.update({landmarks, now, quality, handedness, width, height, latency}) -> controller state
// MouseWorkspace와 같은 안전 규칙: stale은 입력 유지·전송 없음, latency > 450ms·0.7초 정지면 stop(버튼 해제).
// bridge가 없거나 available이 아니면 미리보기(전송 없음).
new HandSelection(holdSeconds = 5).observe(detections, nowSeconds) -> 'right' | 'left' | ''
selectedDetection(detections, side) -> 그 손 하나 | null   // 둘 이상이거나 없으면 null
```

## 설정 저장소

```js
new SettingsStore({storage = localStorage, validateRegistrations, allowedActions = [], now})
store.settings  // 마지막으로 커밋된 값의 복사본
store.status    // empty | loaded | corrupt | newer | unavailable
store.error     // 불러오기 문제의 사용자 안내 문구 | null
store.setMouse({sensitivity?, mainHand?, area?}) -> settings
store.resetMouse() -> settings
store.putGesture({registration, verifiedAt, sessionId, environment = 'desk'}) -> settings
store.removeGesture(gestureId, environment = 'desk') -> settings     // 등록·확인 기록·연결 함께 삭제
store.setBinding({gestureId, actionId, enabled = true}, environment = 'desk') -> settings
store.removeBinding(gestureId, environment = 'desk') -> settings
// 모든 변경: 복사본 적용 → 전체 검사 → 한 번 쓰기 → 다시 읽어 확인 → 메모리 반영.
// 실패: 항상 예외(Error/RangeError/TypeError), 메모리·저장소는 이전 값.
//  - 읽기 전용(newer·unavailable), 다른 창 저장, 검사 실패, 용량 초과(2,000,000자), 쓰기 실패, 재확인 불일치
//  - setBinding: allowedActions에 없는 액션 → RangeError(쓰기 전)
// revision: 커밋마다 1씩 증가하는 저장 횟수(다른 창 충돌 감지용이 아님 — 감지는 원문 비교).
```

## 데스크톱 셸 (preload가 renderer에 노출하는 전부)

```js
window.manuxDesktop.getStatus() -> Promise<{available, enabled, platform, generation, dryRun, error}>
window.manuxDesktop.setEnabled(boolean) -> Promise<status>
// 실패: boolean이 아니면 TypeError. 켜기 실패는 reject가 아니라 status.enabled:false + error.
window.manuxDesktop.send(events) -> void
// 켜져 있을 때만 보낸다. 반환값 없음 — 결과는 status 알림으로 온다.
// main 쪽 실패: 배치 8개 초과·허용 밖 이벤트·이동 100px/스크롤 240 초과·300ms 넘은 입력 → 제어 끄기(부분 실행 없음).
window.manuxDesktop.stop() -> void
window.manuxDesktop.onStatus(callback) -> unsubscribe
window.manuxDesktop.setOverlay(boolean) -> Promise<boolean>   // 화면 손가락 표시 창 켜기/끄기. boolean이 아니면 TypeError
window.manuxDesktop.sendOverlayFrame(frame) -> void           // main이 validateOverlayFrame으로 다시 검사, 형식이 다르면 버림
// generation: 켤 때마다 증가. 이전 generation의 늦은 응답은 무시한다.
```

main은 1초 동안 입력이 없으면(`watchdogMs`) 스스로 끄고, helper는 독립 watchdog과 stdin EOF로 버튼을 해제한다.

## 유스케이스 예외 흐름 대조표

| UC | 예외 흐름 | 표현하는 계약 | 상태 |
| --- | --- | --- | --- |
| UC-01 | 권한 거부·카메라 없음 | `getUserMedia` reject → 앱 조립 문구 | 표현됨 |
| UC-01 | 모델·프레임 시간 초과 | `VisionClient` reject + `close()` | 표현됨 |
| UC-01 | 카메라 끊김 → 등록 취소·마우스 끄기 | 앱 조립 `stopCamera()` → `cancel()`, `ws.setReady(false)` | 표현됨(앱 조립 내부) |
| UC-01 | 손 작음·잘림 → 보류 | `getHandQuality().ok:false` | 표현됨 |
| UC-02 | 손 소실·품질 저하 → 해제 | `AirMouseController` phase + `release` 이벤트 | 표현됨 |
| UC-02 | 0.7초 정지 | `MouseWorkspace` watchdog, `stale`은 lease 연장 안 함 | 표현됨 |
| UC-02 | 왕복 450ms 초과 | `ws.update({latency})` → `stop` | 표현됨 |
| UC-02 | 허용 밖·오래된 입력 | `validateMouseEvents`, `MouseBridge.send` 나이 검사 | 표현됨 |
| UC-02 | helper 종료 → 재실행 안내 | `MouseBridge` exit → status.error | 표현됨 |
| UC-02 | 두 핀치 겹침 → 보류 | `AirMouseController` 내부(`ambiguous`) | 일부 — phase로 구분되지 않음 |
| UC-03 | 브라우저에서 Windows 대상 비활성 | `status.available` | 표현됨 |
| UC-04 | 등록 중 명령 차단 | `commandsSuppressed` | 일부 — 앱 조립이 호출하지 않고, 등록 중 `return`으로 같은 효과 |
| UC-04 | 표본 불안정·충돌·반경 작음 | `trainGesture` usable:false + reason | 표현됨 |
| UC-04 | 각도 실패 → 그 각도만 | `ctrl.retry` | 표현됨 |
| UC-04 | 취소·이탈 → 기존 등록 보존 | 컨트롤러가 저장 등록을 갖지 않음, `cancel()` | 표현됨 |
| UC-04 | 저장 실패 → 기존 유지·재시도 | `SettingsStore` 예외 계약 | 표현됨(화면 경로 미구현) |
| UC-04 | 오래된 후보 거절 | `registerGesture` RangeError | 표현됨 |
| UC-05 | 미수락 → 명령 없음, 우회 금지 | `classifyRegisteredGesture` + `resolveBinding` | 일부 — 판정기는 구현·시험됨, 앱에 연결되지 않아 실행 경로 없음 |
| UC-05 | 연결 없음·비활성·알 수 없는 액션 | `resolveBinding` reason | 일부 — 판정기는 구현·시험됨, 앱 미연결 |
| UC-05 | 연결 변경 시 초기화 | 없음 | **미표현** |
| UC-05 | 실행 실패를 성공으로 기록 안 함 | 없음 | **미표현** |
| UC-06 | 범위 밖·미확인·미등록 연결 | `validateSettings` | 표현됨 |
| UC-06 | 손상·새 버전·다른 창 | `readSettings` status, `update` 원문 비교 | 표현됨 |
| UC-07 | 해시 불일치 → 설치 안 함 | `desktop/updater.mjs` | 표현됨([desktop-release](../desktop-release.md)) |
| UC-07 | 재시작 후 제어 꺼짐 | `MouseBridge.enabled` 초기값 false | 표현됨 |

미표현 2건과 일부 2건은 모두 UC-05(C3)다. 허용 액션 목록(`MOUSE_ACTIONS`)과 같은 액션 중복 연결 정책(허용하고 보고)은 이번에 정했으며 사람 검수가 필요하다. 남은 것은 앱 조립 연결, 연결 변경 시 초기화, 실행 결과 기록이다.
