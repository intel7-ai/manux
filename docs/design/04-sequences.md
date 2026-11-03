# 04. 시퀀스 다이어그램 (M1)

> 상태: **AI 초안, 사람 검수 전** (2026-11-03). 입력: [03 인터페이스](03-interfaces.md). participant 이름은 [02 컴포넌트](02-components.md)와 같고, 화살표 라벨은 03에 있는 함수만 쓴다. 새 정보를 만들지 않는 문서다 — 순서가 03과 다르면 이 문서가 틀린 것이다.
>
> GitHub와 VS Code 미리보기(Mermaid 확장)에서 렌더해 화살표 방향을 눈으로 확인한다.

## S-1. 프레임 하나가 Windows 커서가 되기까지 (UC-01·UC-02)

```mermaid
sequenceDiagram
  participant App as 앱 조립
  participant Tracking as 추적
  participant Recognition as 판정
  participant Control as 제어
  participant Desktop as 데스크톱 셸

  loop 16ms 타이머 (데스크톱) / requestAnimationFrame (브라우저)
    App->>Tracking: recognize(video, timestamp)
    alt 처리 중(busy) 또는 닫힘
      Tracking-->>App: null (이번 프레임 건너뜀)
    else 정상
      Tracking-->>App: {result, inferenceMs}
      App->>Recognition: getHandQuality(landmarks, {width, height})
      Recognition-->>App: {ok, reason}
      App->>Control: ws.update({landmarks, now, quality, handedness, width, height, latency})
      Control->>Control: ctrl.update(...) → {mode, phase, events}
      alt phase = stale (같은 타임스탬프)
        Note over Control: 이벤트 없음, watchdog·lease 연장 안 함
      else 새 추론
        Control->>Desktop: manuxDesktop.send(events)
        Desktop->>Desktop: validateMouseEvents → helper(SendInput)
      end
    end
  end

  alt 왕복 > 450ms 또는 700ms 새 프레임 없음
    Control->>Control: ws.stop(이유)
    Control->>Desktop: manuxDesktop.stop()
  else 1초 입력 없음 (main watchdog)
    Desktop->>Desktop: disable(이유)
    Desktop-->>Control: onStatus({enabled:false, error})
  end
```

- 마우스 모드에서는 판정의 특징 벡터·등록 판정을 쓰지 않는다. 핀치·손바닥 판정은 제어(`AirMouseController`) 안에 있다.
- 시간 초과 시 `recognize`는 reject하고 앱 조립은 `stopCamera()`로 세션 전체를 끝낸다(그림 생략).

## S-2. 손 모양 하나 등록 (UC-04)

앱 조립 쪽 단계(★)는 **아직 코드에 없는 제안 경로**다(U2). 다른 화살표의 함수는 구현돼 있다.

```mermaid
sequenceDiagram
  participant App as 앱 조립
  participant Enrollment as 등록 흐름
  participant Recognition as 판정
  participant Settings as 설정 저장소

  App->>Enrollment: ★ ctrl.start({now, environment:'desk', gestureId})
  loop 새 인식 결과마다
    App->>Enrollment: ★ commandsSuppressed(snapshot)
    Enrollment-->>App: true → 영상·마우스 명령 만들지 않음
    App->>Recognition: extractFeatures(landmarks, {width, height})
    App->>Enrollment: ctrl.update({now, features, quality, orientation, handedness})
  end
  Enrollment-->>App: snapshot.phase = captured
  App->>Recognition: ★ trainGesture({gestureId, viewSamples, existingRegistrations})
  alt usable:false
    Recognition-->>App: {candidate:null, reason, conflicts}
    Note over App: 이유 안내, 기존 등록 불변, 다시 등록
  else usable:true
    Recognition-->>App: {candidate}
    App->>Enrollment: ctrl.startVerification({now})
    loop 각도별 새 수행
      App->>Recognition: classifyRegisteredGesture({features, registrations:{[id]: candidate}})
      App->>Enrollment: ctrl.update({..., match})
    end
    alt phase = failed
      App->>Enrollment: ctrl.retry({now})
    else phase = verified
      App->>Recognition: ★ registerGesture(registrations, candidate)
      alt RangeError (다른 동작과 충돌)
        Note over App: 오래된 후보 거절, 기존 유지
      else 새 레코드
        App->>Settings: ★ store.putGesture({registration, verifiedAt, sessionId})
        alt 예외
          Note over App: 저장 실패 안내 + 저장 다시 시도, 메모리·저장소 이전 값
        else 저장됨
          App->>Enrollment: ctrl.cancel()
        end
      end
    end
  end
```

- 취소·Esc·창 이탈·카메라 종료는 어느 단계에서든 `ctrl.cancel()` 하나로 끝난다(그림 생략).
- `store.putGesture` 전에 `registerGesture`를 부르는 이유: 저장소는 충돌을 모른다. 충돌 재검사는 판정의 책임이다.

## S-3. 등록 동작 → 액션 실행 (UC-05, 제안)

전부 **제안**이다(C3). `resolveBinding`은 03의 제안 시그니처다.

```mermaid
sequenceDiagram
  participant App as 앱 조립
  participant Recognition as 판정
  participant Control as 제어
  participant Settings as 설정 저장소
  participant Desktop as 데스크톱 셸

  App->>Recognition: classifyRegisteredGesture({features, registrations})
  Recognition-->>App: decision
  App->>Control: resolveBinding({decision, bindings, allowedActions})
  alt actionId = null (not-accepted / no-binding / disabled / unknown-action)
    Note over Control: 명령 없음, 공통 모델로 우회하지 않음
  else actionId 있음
    Control->>Desktop: manuxDesktop.send(events)
  end
```

`bindings`는 앱 조립이 `store.settings`에서 읽어 넘긴다. 제어는 설정 저장소를 직접 부르지 않는다(02의 의존 방향).

## S-4. 시작할 때 설정 복원과 변경 저장 (UC-06)

```mermaid
sequenceDiagram
  participant App as 앱 조립
  participant Settings as 설정 저장소
  participant Control as 제어

  App->>Settings: new SettingsStore({validateRegistrations})
  alt status = loaded / empty
    Settings-->>App: settings
  else status = corrupt
    Settings-->>App: 기본값 + error (원문 보존)
  else status = newer / unavailable
    Settings-->>App: 기본값 + error (읽기 전용)
  end
  App->>Control: new MouseWorkspace({sensitivity, onSensitivityChange})
  Control->>App: onSensitivityChange(sensitivity)
  App->>Settings: store.setMouse({sensitivity})
  alt 예외 (읽기 전용 / 다른 창 저장 / 검사·쓰기 실패)
    Settings-->>App: Error
    App->>Control: ws.status(문구, true)
  else 커밋됨
    Settings-->>App: settings
  end
```
