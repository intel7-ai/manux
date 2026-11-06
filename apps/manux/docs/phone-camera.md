# 휴대폰을 원격 카메라로 (v0.8 개발 도구)

PC 화면에서 5~7m 떨어져 있어도 휴대폰은 손 가까이 **약 1m**에 거치한다. 휴대폰과 PC를 같은
사설 Wi-Fi/LAN에 연결하고 손이 휴대폰 화면 중앙에 충분히 크게 들어오게 둔다. 앱 설치와
Bluetooth는 필요 없다. 게스트 Wi-Fi의 기기 간 통신 차단(AP isolation), VPN 인터페이스,
다른 서브넷 라우팅 때문에 접속이 안 될 수 있다.

이 코드는 Dan(한순중)의 [원본 9da1f63](https://github.com/intel7-ai/manux/blob/9da1f634e8c0afd9d3d6b32a56c83cddfb23d5f8/src/phone_web_bridge.py)을 이식했다.
원본 README의 `--phone-web` / `--source phone`과 QR 대기 화면·전면/후면 전환·**480×360 상한,
JPEG 품질 0.55, 목표 30fps**를 기준으로 한다. 원본 브랜치에 11/04 실제 휴대폰 사용 기록
`field-logs/2026-11-04-home-pc-phone`이 있지만, **이번 이식본의 실물 시험 증거는 아니다**.
추가한 인증과 새 프레임 메타를 사용하므로 원본 페이지와 프로토콜 호환은 없다.

## PC 실행

`apps/manux` 폴더에서:

```powershell
uv run --locked --python 3.12 --extra link --extra vision --extra qr python tools/phone_camera_demo.py
# LAN 주소 자동 선택이 틀린 경우 (PC의 실제 사설 IP 사용)
uv run --locked --python 3.12 --extra link --extra vision --extra qr python tools/phone_camera_demo.py --host 192.168.0.10 --port 8443
```

`qr`는 선택 사항이다. 빼면 URL만 출력하고 QR 없이 대기 화면을 표시한다. QR에는 URL만 담으며
코드·토큰은 담지 않는다. 기본 서버는 꺼져 있고 이 명령을 실행해야 열린다. `--no-tracking`은
MediaPipe 없이 영상만 확인한다. 추적 모델이 없으면 사용자 데이터 폴더에 내려받는다.

1. Windows 방화벽 알림이 나오면 **개인 네트워크**에서 해당 Python 프로세스의 수신을 허용한다.
   관리형 PC에서는 관리자가 해당 LAN/8443 TCP 인바운드를 허용해야 할 수 있다. 공용 네트워크
   허용이나 공유기 포트 포워딩은 필요 없다.
2. 휴대폰 Safari/Chrome으로 터미널 URL 또는 QR의 `https://PC사설IP:8443`을 연다.
3. 아래 인증서 경고를 확인한 뒤 PC 터미널의 **6자리 코드**를 입력한다. 발급 후 2분, 한 번만
   유효하다. 후면/전면을 고르고 연결을 누르면 카메라 권한을 요청한다. 기본은 후면이다.
4. 폰에는 PC 연결 상태·수신 확인 FPS·전면/후면 전환·끊기가 보인다. Wake Lock 지원 시 화면
   꺼짐 방지를 요청하며 지원/배터리 정책에 따라 해제될 수 있다. 다른 탭으로 가면 전송을 쉰다.
5. PC 창은 손 검출 여부·최대 손 경계 크기 px·처리 FPS·추정 촬영→수신 지연을 표시한다.
   실제 센서 시각은 없으며 브라우저/PC 시계 보정과 왕복 지연의 오차가 포함된다.

PC 단축키: `D` 연결 끊고 새 코드, `B` 전역 차단, `U` 차단 해제, `P` 코드 재발급, `ESC` 종료.
끊김 때 이전 프레임 대신 대기 화면을 표시한다. 이 데모는 엔진과 Windows 입력을 만들지 않는다.
휴대폰의 끊기·페이지 종료도 카메라 트랙과 소켓을 닫는다. 자동 재접속은 하지 않으며 PC에서 새
코드를 받아야 한다. 접속 중에는 PC에 기기 표시명·IP를 출력한다. 토큰은 메모리에서만 사용한다.

## 자체 서명 인증서와 브라우저

인증서는 첫 **명시적 실행** 때 사용자 데이터 폴더의 `link/certs/phone-cert.pem`과
`phone-key.pem`에 생성한다. 소스 실행 기본 경로는 `%APPDATA%/MANUX-dev/v04`, 설치 실행은
`%APPDATA%/MANUX/v04`이며 `MANUX_DATA_DIR`로 바꿀 수 있다. 저장소 안 경로는 거절한다.
키를 휴대폰에 복사하거나 Git에 넣지 않는다. IP 변경/인증서 만료 때 인증서를 갱신한다.

- **Android Chrome:** 해당 PC URL의 인증서 경고에서 `고급` → 해당 IP로 계속 이동(안전하지 않음)이
  제공되면 선택하고 카메라 권한을 허용한다. 관리 정책/Chrome 버전에 따라 예외가 막힐 수 있다.
- **iOS Safari:** `이 연결은 비공개로 설정되어 있지 않습니다`에서 세부 정보/고급 → 이 웹 사이트
  방문이 제공되면 해당 PC 주소임을 확인하고 진행한다. 기기 암호를 요청할 수 있다. 경고를 넘겨도
  카메라가 안 열리면 연결 상태의 오류와 `isSecureContext`/카메라 권한을 확인한다. **iOS 자체 서명
  HTTPS에서 getUserMedia가 실제로 열리는지는 사람이 확인해야 한다.** 예외 메뉴가 없거나 권한이
  거절되면 테스트를 통과했다고 처리하지 않는다. 신뢰 인증서 구성이 필요한 기기는 별도 설정 대상이다.

경고 예외는 이 PC 주소에 대해서만 확인한다. 브라우저의 전역 보안 기능을 끄는 절차는 사용하지 않는다.
공식 문서: [getUserMedia의 secure context 조건](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia),
[Wake Lock 가시성·해제 조건](https://www.w3.org/TR/screen-wake-lock/),
[Apple의 자체 서명 인증서 신뢰 설명](https://support.apple.com/en-ie/guide/deployment/depb5eff8914/1/web/1.0).

## 기존 IP 카메라 앱 대안

IP Webcam 같은 휴대폰 앱이 제공하는 HTTP MJPEG/RTSP 영상 URL은 기존 OpenCV 카메라 경로로
열 수 있다. 예: `python -m manux --source http://192.168.0.15:8080/video` (앱 실행에 필요한
기존 extras 사용). `--source 192.168.0.15:8080`도 기존 파서가 `/video` 주소로 바꾼다.
이 경로는 휴대폰 앱이 필요하고 이 문서의 짝짓기/HTTPS 서버와 별도다. 해당 앱의 인증 설정을 따른다.
v0.4/v0.5 데스크톱에서 `--source phone`/`--phone-web`은 아직 조립하지 않았으므로 새 데모를 사용한다.

## 구현·검증 경계

- 계약 §14, `link`는 core/capture만 import. 영상은 최신 한 장만 보관하며 저장·업로드하지 않는다.
- 사설 TCP peer/Host/Origin 검사, 2분 일회용 코드·IP에 묶인 일회용 토큰, 단일 연결·속도 제한.
- JPEG envelope·해상도·시각·순서 검증, 느린 전송 건너뜀, 끊김/2초 정체 시 새 프레임 없음 처리.
- `audio` 메시지 종류는 v1.1 확장 자리만 예약했다. 현재 수신 거절, 마이크 권한/음성 인식 없음.
- 합성 JPEG와 실제 로컬 TLS aiohttp 클라이언트로 시험한다. Android Chrome/iOS Safari 실물,
  서로 다른 LAN 환경·처리 성능·Windows 입력은 시험하지 않았다.
- PR #96 병합 후 별도 앱 조립 PR에서 카메라 선택·QR·기기 표시·끊기·전역 차단 화면과
  `Engine.frame_missing` 연결이 필요하다. 이 PR만으로 데스크톱 앱에서 선택할 수 있지는 않다.
