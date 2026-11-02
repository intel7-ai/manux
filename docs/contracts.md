# 다음 개발을 위한 모듈 계약

> 2026-11-02: 사용자 요청으로 별도의 [마우스 모드와 Windows 실행 계약](air-mouse.md)을 추가했다. 아래 내용은 기존 단일 등록 기능의 계획이며, 마우스 모드는 기존 프로필이나 영상 명령을 변경하지 않는다.

기준일: 2026-10-29. **이 문서의 `Proposed` API와 스키마는 설계안이며 현재 앱에 구현돼 있지 않다.** D1에 R2·R3·R4·R5가 합의한 뒤 계약 테스트와 함께 도입한다. 기존 API를 변경할 때 소비 모듈도 같은 통합 PR에서 갱신한다.

## 1. 현재 결합 지점

| 현재 코드 | 현재 동작 | 단일 등록에 필요한 변경 |
| --- | --- | --- |
| `personalization.js`의 `POSES`, `trainEnvironment`, `classifyPersonal` | 다섯 클래스와 모든 각도 요구, 고정 목록으로 비교 | 선택 클래스 후보 학습과 부분 클래스 집합·거부 검증 |
| `enrollment.js`의 `start({now, environment})` | 다섯 자세 전체 수집·확인 | 시작할 때 대상 동작을 고정하고 해당 동작만 완료 |
| `app.js`의 `saveEnrollment` | 환경 모델 전체 교체 | 검증된 대상 동작만 병합하고 다른 등록 보존 |
| `validateProfile`, `profile-store.js` | 프로필 v1, 완전한 환경 표본 검사 | 새 버전의 부분 등록 구조, 구형 데이터 변환·오류 보존 |
| `gesture-controller.js`의 `GESTURE_ACTIONS` | 인식 클래스와 명령 고정 연결 | P0 호환 유지, P1에서 모양·바인딩·액션 분리 |

UI의 동작 선택만 바꾸면 위의 완전성 검사·저장·거부 판정이 깨진다. R3·R4·R5 계약 변경을 함께 검증한다.

## 2. ID와 저장 범위

동작 ID는 표시 이름과 분리한다. 이름을 바꿔도 등록·바인딩 참조가 유지돼야 한다. 기본 ID 예시는 `builtin.thumb-up`, 사용자 ID 예시는 `custom:<uuid>`다. 액션 ID 예시는 `media.play`, `media.pause`, `media.seek-forward-10`, `media.seek-backward-10`이다. 실제 ID 목록과 변환표는 계약 PR에서 확정한다.

```ts
// Proposed structure. Opaque model/evidence fields need validators in the contract PR.
type GestureDefinition = {
  id: string; name: string; source: 'builtin' | 'custom';
  kind: 'static-hand'; orientationPolicy: 'invariant';
};
type GestureRegistration = {
  gestureId: string; modelVersion: number; featuresVersion: number;
  model: unknown; viewCoverage: unknown; verification: unknown; verifiedAt: string;
};
type Binding = { gestureId: string; actionId: string; enabled: boolean };
type Environment = {
  registrations: Record<string, GestureRegistration>;
  bindings: Record<string, Binding>;
};
type ProfileV2 = {
  version: 2; id: string; name: string; createdAt: string;
  gestures: Record<string, GestureDefinition>;
  environments: { desk?: Environment; sofa?: Environment };
};
```

P0에서는 기본 ID만 사용한다. P1에서 사용자 정의 ID를 허용한다. `rock`이라는 이름 자체는 손 모양 규격이 아니므로 사용자의 실제 예시를 해당 ID에 저장한다. 표본이 없는 정의는 `미등록`, 바인딩이 없는 등록은 `액션 미연결`로 표시한다. 책상/소파별 등록과 바인딩을 명시적으로 저장하고 환경을 넘어 묵시적으로 복사하지 않는다.

방향을 명령으로 구분하는 정책은 후속 확장이다. 현재 제안은 기본 동작과 rock 모두 허용된 여러 기울기를 같은 모양으로 보는 `invariant`다. 손을 흔드는 시간적 모션은 포함하지 않는다.

## 3. 단일 동작 수집·확인

```js
// Proposed, owned by R4.
controller.start({ now, environment, gestureId, capturePolicy })
controller.update({ now, features, quality, orientation, handedness, match })
controller.startVerification({ now, candidateModel })
controller.retry({ now })
controller.cancel()
// snapshot: sessionId, gestureId, environment, phase, targetView,
// coverage, reason, capturedExamples, verificationSummary
```

- 한 세션은 하나의 `gestureId`와 하나의 환경을 고정한다. 중간 변경 시 취소하고 새 세션을 시작한다.
- `captured`는 후보 학습 가능, `verified`는 저장 가능을 의미한다. 수집만 끝난 데이터를 활성 모델로 사용하지 않는다.
- 타이머 호출은 유효 표본·각도·관찰 시간을 생성하지 않는다. 실제 새 추론 입력만 수집한다.
- 수집·검증 표본을 분리한다. 확인 수행을 다시 학습에 넣어 성공률을 높여 보이지 않는다.
- 실패·취소 시 기존 저장 모델은 유지한다. 필요한 각도만 다시 수집할 수 있게 하되 수집 조건·완료 상태를 일관되게 초기화한다.
- 각도 정책은 `capturePolicy`로 모듈 계약에 둔다. D2 실물 기준선으로 정하고 UI·모델·저장이 같은 정책 ID/버전을 사용한다.
- 중립/다른 모양에 대한 거부 확인은 필요하지만, 다른 네 동작 전체의 강제 등록으로 되돌아가지 않는다. 짧은 별도 거부 확인 흐름과 예시 규격을 R3·R4가 함께 정한다.

## 4. 학습·판정 계약

```js
// Proposed, owned by R3. Names are provisional until the contract PR.
trainGesture({ gestureId, viewSamples, capturePolicy, existingRegistrations, rejectionExamples })
// -> { candidate, usable, conflicts, reason, diagnostics }
classifyRegisteredGesture({ features, registrations, rejectionPolicy })
// -> { gestureId: string | null, accepted: boolean,
//      kind: 'match' | 'neutral' | 'unknown' | 'invalid', reason,
//      distance, margin: number | null }
```

한 클래스만 있을 때 두 번째 후보와의 차이는 정의되지 않는다. `margin`을 무한대나 임의 고득점으로 만들지 않는다. 허용 거리·품질·별도 거부 예시 등으로 수락을 제한하고, 검증할 증거가 부족하면 `insufficient-rejection-evidence`처럼 이유를 반환한다. 새 클래스를 추가할 때 기존 모든 활성 클래스와 충돌을 다시 점검한다. 충돌로 기존 클래스를 조용히 비활성화하지 않고 후보 저장을 보류한다.

| 입력/상태 | 결과 |
| --- | --- |
| 유효 등록과 충분히 가까우며 거부 기준 통과 | 해당 `gestureId`, `accepted: true` |
| 미등록 모양·모델 범위 밖 | `accepted: false`, 명령 없음 |
| 둘 이상의 등록과 구분 어려움 | `ambiguous-gesture`, 명령 없음 |
| 중립·명시적 거부 예시 | 중립/거부로 표시, 명령 없음 |
| 유효 관절 없음·손 너무 작음·잘림 | 품질 보류, 명령 없음 |
| 모델/특징 버전 불일치 | 모델 오류, 명령 없음 |

개인 모드에서는 개인 판정이 거부한 입력을 공통 모델 명령으로 우회하지 않는다. 공통 모델 사용은 사용자가 명시적으로 선택한 별도 모드다. 기본 동작 한 개만 등록한 개인 모드에서는 나머지 미등록 세 동작을 임의로 실행하지 않는다. `neutral`은 명시적으로 확인된 쉬는 자세이고 `unknown`은 불확실·미등록 입력이다. 둘을 구분해 R2에 전달하며 단순 거부를 중립 해제 신호로 바꾸지 않는다.

## 5. 저장 트랜잭션과 마이그레이션

R5가 저장 흐름, R3가 모델 검증을 맡는다. 저장 함수는 사용자 ID·환경·대상 동작 ID·등록 세션/리비전을 확인한다.

1. 검증 완료한 후보가 현재 세션·사용자·환경·동작과 일치하는지 검사한다.
2. 현재 프로필을 복제하고 해당 `registrations[gestureId]`만 교체한다.
3. 전체 스키마, ID 참조, 크기 제한, 모델 버전을 검사한다.
4. 한 번의 저장 작업이 성공한 뒤 메모리 상태와 UI를 바꾼다. 실패하면 기존 메모리·저장 상태를 유지한다.
5. 삭제는 선택 동작과 해당 바인딩에만 적용한다. 사용자 삭제는 별도 명시적 작업이다.

구형 프로필 v1과 모델 v1/v2의 기본 클래스를 새 ID로 옮긴다. 마이그레이션은 원본 검증과 복구 가능한 사본을 먼저 확보하며 실패 시 원본을 덮어쓰지 않는다. 기존 localStorage 키를 즉시 삭제하지 않는다. 구형 데이터가 없거나 손상된 경우를 따로 시험한다. 저장 용량 상한은 기존 4,000,000자 제한을 고려하고 원시 영상을 추가 저장하지 않는다.

## 6. 판정·바인딩·실행 경계

P0는 기존 네 명령 동작을 보존한다. P1에서는 수락된 동작 ID를 활성 바인딩으로 해석하여 허용 액션만 실행한다. 액션 카탈로그는 앱 내부 영상 네 동작으로 시작한다.

- 바인딩 없음/비활성/알 수 없는 액션 ID는 명령 없음이다.
- 반복 가능 여부는 액션 속성이다. 현재 ±10초만 반복 가능하며 재생·정지는 한 번만 실행한다.
- 바인딩·사용자·환경·모드 변경, 등록 시작, 카메라 종료, 탭 이탈, Esc에서 판정 후보·잠금·반복 시간을 초기화하고 제어를 해제한다.
- 실행 실패를 성공 로그로 기록하지 않는다. 실제 영상 범위를 벗어난 seek는 경계에서 제한한다.
- 임의 문자열을 코드·셸·키 입력으로 실행하는 바인딩은 이번 범위에 없다.

## 7. 진단 사건과 재현 정보

원시 영상 없이 다음 항목을 기록하는 최소 스키마를 R5가 정리하고 각 모듈이 채운다.

```js
{ sessionId, eventSeq, timeMs, eventType, mode, environment,
  gestureId, phase, targetView, reason, quality,
  capturedCount, validDurationMs, candidateDistance,
  modelVersion, actionId, actionResult, actualResolution }
```

`timeMs`는 해당 실행의 메인 스레드 단조 시각 기준이다. Worker의 다른 시간 원점 값을 그대로 빼지 않는다. 추론 소요 시간은 별도 duration, 사람 동작부터 화면 반응까지는 별도 관찰 지표로 측정한다. `eventSeq`로 중복·순서를 추적하고 세션이 바뀐 늦은 응답을 버린다. 저장하는 진단은 사용자가 내보낼 때 범위를 확인하고 실험 참가자 이름 대신 세션/참가자 코드를 사용한다.

## 8. 병합 전 계약 시험

| 시나리오 | 주 담당 / 리뷰 |
| --- | --- |
| 한 동작만 존재하는 유효 프로필과 미등록 거부 | R3·R5 / R2 |
| 선택 동작만 수집·검증 후 저장, 나머지 동작 불필요 | R4·R5 / R3 |
| 한 동작 재등록·취소·저장 실패 후 다른 등록 보존 | R5 / R4 |
| 새 사용자 정의 모양이 기존 등록과 충돌 | R3 / R1 |
| 미연결 모양·잘못된 액션·바인딩 변경 중 반복 | R2·R5 / R4 |
| 구형 프로필 읽기·변환 실패·용량 실패 | R5·R3 / R2 |
| 손 소실·손 바뀜·늦은 결과·관찰 공백 | R1·R4 / R3 |

새 테스트는 이 계약의 경계를 검증한다. 구현의 내부 반복문을 그대로 복제하는 테스트로 완료를 주장하지 않는다.
