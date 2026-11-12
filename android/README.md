# MANUX Phone Android companion

Android 8.0(API 26) 이상에서 PC MANUX의 휴대폰 카메라 입력으로 연결하는 네이티브 앱입니다. 손 인식·모델·학습·Windows 제어는 기존 PC 프로그램에서 실행합니다. APK에는 개인 데이터·모델·연결 코드가 포함되지 않습니다.

## 인터넷 연결 테스트 · net.1

1. PC MANUX에서 **인터넷 연결 (테스트)**를 켜고 표시된 임시 HTTPS 주소와 6자리 코드를 확인합니다. 별도 서버 계정 없이 실행되는 임시 터널이며 PC 앱을 종료하면 주소도 사용할 수 없게 됩니다.
2. APK 상단의 **인터넷 연결 (테스트)**를 누르고 PC의 HTTPS 주소를 입력합니다. 브라우저로 이동하지 않고 앱 안에서 인터넷 연결 화면을 엽니다.
3. 카메라 권한을 허용하고 PC의 새 코드를 입력한 뒤 연결합니다. 서로 다른 Wi-Fi나 LTE에서도 시도할 수 있습니다.
4. PC에서 제어를 시작합니다. 전면 카메라에서는 PC 모니터 전체가 나오며 전체보기 버튼을 지원합니다.

직접 WebRTC 연결을 먼저 시도합니다. NAT/방화벽 때문에 직접 연결되지 않으면 인증된 HTTPS/WSS JPEG 중계로 전환하고 화면에 **인터넷 중계**로 표시합니다. TURN 서버가 설정된 것처럼 표시하지 않습니다. WebRTC 카메라는 기존 4:3 비율을 유지한 최대 1280×960(세로960×1280)·30fps를 요청하며, 해상도 유지와 최대4Mbps 송신을 설정합니다. PC 모니터는 최대1280×720·15fps·고품질JPEG85와 H264 우선 협상을 사용합니다. 중계 모드는 카메라 최대960×720/JPEG72(고지연640×480/JPEG62)·15fps, PC 화면 최대1920×1080/JPEG85·8fps를 요청합니다. 화면 상단에는 요청값이 아닌 실제 송수신 해상도와FPS를 표시합니다. 실제 성능은 연결 상태·WebView·PC 처리 속도에 따라 달라집니다.

WebView는 사용자가 입력한 HTTPS origin만 사용하며 다른 origin과 페이지 이동, HTTP, 파일·콘텐츠 접근, 인증서 오류 우회를 차단합니다. 카메라만 허용하고 마이크·JavaScript 네이티브 브리지는 사용하지 않습니다. 코드·토큰·영상은 저장하지 않고 주소만 기억합니다. 앱을 잠시라도 백그라운드로 보내면 문서를 제거해 카메라·연결을 종료합니다. 돌아온 뒤 **다시 열기**와 새 코드로 연결하세요. Android System WebView가 오래됐으면 업데이트가 필요합니다.

## Bluetooth 연결

기존 Bluetooth 연결도 그대로 사용할 수 있습니다.

앱 첫 화면 상단의 **연결 방법** 버튼을 누르면 페어링부터 PC 제어 시작까지 4단계 안내를 볼 수 있습니다. 긴 안내는 스크롤해서 읽고 **닫기**로 돌아옵니다. PC 전체보기·재연결·Wi-Fi 연결 방법도 같은 안내에 포함합니다.

1. Windows와 휴대폰의 Bluetooth를 켜고 운영체제 설정에서 서로 페어링합니다.
2. PC MANUX의 **휴대폰 연결 → Bluetooth**에서 연결을 시작하고 6자리 코드를 확인합니다.
3. APK 상단에 **MANUX Phone · net.1**가 표시되는지 확인하고 **카메라**와 **근처 기기** 권한을 허용합니다. **연결할 PC를 선택하세요**를 눌러 MANUX가 실행 중인 PC를 직접 고르고 코드를 입력한 뒤 **연결**을 누릅니다. 권한 허용 후에도 기기를 자동 선택하거나 연결하지 않습니다. PC가 목록에 없으면 **블루투스 설정**에서 페어링 후 **새로고침**을 누릅니다.
4. PC에서 **제어 시작**을 누르면 기존 손 동작 기능을 사용할 수 있습니다.
5. **전면 → 후면** 버튼으로 카메라를 바꿉니다. 전면 연결에서는 휴대폰에 PC 모니터가 표시됩니다. **PC 화면 전체보기**는 비율을 유지해 바탕화면 전체를 보여줍니다. 전체보기 닫기 또는 뒤로 가기로 돌아옵니다.

앱을 백그라운드로 보내거나 화면을 끄면 카메라와 연결을 종료합니다. 다시 사용할 때 PC의 새 코드로 연결하세요. 연결 코드는 저장하지 않습니다. Bluetooth는 영상 대역폭이 제한되므로 Wi-Fi와 같은 영상 품질·프레임 속도를 보장하지 않습니다. 카메라는 약 320×240, JPEG 품질 45, 최대 10fps이며 PC 화면은 compact 프로필로 최대 약 1.7fps를 요청합니다. 실제 속도는 두 기기의 무선 상태에 따라 낮아집니다. 이동·클릭 인식 판정과 설정은 변경하지 않습니다.

## PC 선택과 연결 진단

bt.4부터 스피커·이어폰 등 Bluetooth Audio/Video 클래스는 목록에서 제외하고, Computer 클래스는 `[PC]`로 먼저 표시합니다. 분류가 없는 Windows PC도 있을 수 있으므로 `[기타 · PC인지 확인]` 기기는 직접 선택할 수 있습니다. 이름이 PC와 일치하는지 확인하세요. 목록 첫 항목은 항상 선택 안내이며 다른 기기를 자동 선택하지 않습니다. 새로고침은 이미 고른 PC가 목록에 남아 있을 때에만 같은 기기를 유지합니다. 연결 상태에는 선택한 PC 이름을 화면에만 표시합니다.

Android 12 이상에서는 근처 기기 권한에 `BLUETOOTH_CONNECT`와 `BLUETOOTH_SCAN`이 포함됩니다. SCAN은 연결 전 다른 화면에서 실행 중인 검색을 취소하는 Android API에만 사용합니다. 이 앱이 주변 기기 검색을 시작하거나 위치를 수집하지는 않습니다(`neverForLocation`). 이전 APK에서 업데이트하면 근처 기기 권한을 추가로 허용해야 할 수 있습니다.

상태에 `BT-DISCOVERY`(기존 검색 정리), `BT-CONNECT`(PC 서비스 검색·연결), `BT-AUTH`(6자리 코드 인증), `BT-STREAM`(영상 전송)을 구분해 표시합니다. 연결 제한시간과 인증 제한시간을 별도로 둡니다. 오류에는 단계와 안전한 오류 분류만 표시하고 실제 예외 메시지·기기 식별자·코드는 남기지 않습니다.

시스템의 일시적인 페어링 창이 떠서 Activity가 `onPause`되는 동안에는 인증 전 연결 시도를 유지합니다. 카메라는 즉시 멈추며 앱이 완전히 가려지는 `onStop`에서는 연결도 종료합니다. 화면에 `APP_BACKGROUND`가 표시되면 앱으로 돌아와 다시 연결하세요.

## 기존 Wi-Fi 연결

PC 연결 창의 `https://…` 주소를 입력하고 **Wi-Fi 연결**을 누르면 기존 휴대폰 웹 페이지를 외부 브라우저로 엽니다. 이 APK는 TLS 인증서 검증을 우회하지 않습니다. Wi-Fi 연결 코드는 브라우저에서 별도로 입력합니다.

## 빌드

JDK 17 이상(이 작업에서는 JDK 21), Android SDK platform 35, build-tools 35.0.0, Gradle 8.9를 사용합니다. Android Gradle Plugin은 8.7.3이며 추가 AndroidX 런타임 의존성은 없습니다. `gradlew`, `gradlew.bat`, `gradle/wrapper`를 포함하므로 전역 Gradle 설치는 필요하지 않습니다. `JAVA_HOME`과 `ANDROID_HOME`을 자신의 설치 경로로 지정하고 `android` 폴더에서 `./gradlew :app:assembleDebug :app:lintDebug`(Windows: `.\gradlew.bat`)를 실행할 수 있습니다.

저장소 루트에서 PowerShell로 실행합니다(로컬 도구 설치 경로 기준).

```powershell
$env:JAVA_HOME = 'C:\Program Files\Java\jdk-21.0.12.1'
$env:ANDROID_HOME = Join-Path (Get-Location) '.local\android-sdk'
$env:GRADLE_USER_HOME = Join-Path (Get-Location) '.local\gradle-home'
& '.\.local\android-tools\gradle-8.9\bin\gradle.bat' -p android --no-daemon :app:assembleDebug :app:lintDebug
```

결과: `android/app/build/outputs/apk/debug/app-debug.apk`. 개인 설치·검증용 debug 서명 APK이며 Play Store 배포용 릴리스 서명본은 아닙니다. 빌드 결과, 서명키, SDK, Gradle 캐시는 Git에 넣지 않습니다. USB 디버깅을 허용한 Android가 연결되어 있으면 SDK의 `adb install -r`로 설치할 수 있습니다.

## 합성 검사와 실기기 확인

Android 없이 실행할 수 있는 검사입니다. 패킷 길이 제한, 잘린 입력, 연속 패킷, NV21 회전과 색상 평면 순서를 확인합니다.

```powershell
New-Item -ItemType Directory -Path android/build/protocol-tests -Force | Out-Null
& "$env:JAVA_HOME/bin/javac.exe" -encoding UTF-8 -d android/build/protocol-tests android/app/src/main/java/org/manux/phone/Protocol.java android/app/src/main/java/org/manux/phone/CameraGeometry.java android/app/src/main/java/org/manux/phone/ConnectionState.java android/app/src/main/java/org/manux/phone/DeviceSelection.java android/app/src/main/java/org/manux/phone/InternetAddress.java android/tests/ProtocolSelfTest.java
& "$env:JAVA_HOME/bin/java.exe" -cp android/build/protocol-tests org.manux.phone.ProtocolSelfTest
```

APK 빌드·lint·합성 검사는 실제 휴대폰 연결 시험을 대신하지 않습니다. 실기기에서 확인할 항목: 최초 권한 허용/거부, 만료 코드, 전후면 전환, 세로/가로 방향, 좌우반전, PC 전체보기, 백그라운드 전환 후 카메라 종료, PC 앱 종료/거리 이탈 후 재연결, 카메라/PC 화면 실제 FPS입니다.

## 전송 계약

Secure Bluetooth Classic RFCOMM 서비스 UUID: `6f8c5d2a-4e10-4a61-9c23-58f0c17b2e90`. 이미 페어링한 기기만 선택하며 위치 권한은 사용하지 않습니다. SCAN 권한은 연결 전 검색 취소에만 사용합니다. 모든 패킷은 big-endian u32 길이(type 포함) + u8 type + payload, 최대 1MiB입니다.

| Type | Payload |
|---|---|
| 1 AUTH | JSON `code`, `device` |
| 2 READY | JSON `server_time` (Unix 초) |
| 3 FRAME | u32 metadata 길이 + JSON metadata + JPEG |
| 4 ACK | JSON `seq`, `accepted`, `server_time` |
| 5 SCREEN_REQUEST | JSON `profile: compact` |
| 6 SCREEN_JPEG | JPEG bytes |
| 7 ERROR | JSON `error`, 화면 오류이면 `request: screen` |

프레임 metadata는 기존 PC 입력 계약(`kind`, `seq`, `captured_at`, `width`, `height`, `mirrored`, `rotation`, `facing`)을 그대로 사용합니다. JPEG는 NV21 픽셀을 먼저 정방향으로 돌린 후 인코딩하여 `rotation=0`으로 보내며 PC의 기존 좌우반전 동작과 호환됩니다. 시간은 READY 왕복 중간값으로 PC 시각에 맞춥니다. ACK 대기 중인 카메라 프레임은 하나만 허용하고 나머지는 버립니다. 화면 요청도 하나만 진행합니다. 코드나 영상은 로그·파일에 저장하지 않습니다.
