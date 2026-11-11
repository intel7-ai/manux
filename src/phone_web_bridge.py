"""
App-less Smartphone Browser Camera Bridge (`PhoneWebBridge`).
Allows any smartphone (iOS Safari / Android Chrome) to stream its camera directly
to Hand-Mouse over HTTPS + WebSocket without installing any mobile app.

Features:
- Auto-generates a local self-signed TLS certificate (`cryptography`) so mobile browsers
  enable `navigator.mediaDevices.getUserMedia` over HTTPS.
- Hosts a mobile-friendly HTML5 camera page with Front/Rear camera toggle, resolution
  and FPS controls, and live connection status.
- Renders a waiting frame with a scannable QR code and connection instructions on the PC
  until the smartphone connects and starts streaming JPEG frames over WebSocket.
"""

from __future__ import annotations

import asyncio
import datetime
import hmac
import ipaddress
import secrets
import socket
import ssl
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

import aiohttp
from aiohttp import web
import cv2
import numpy as np
import qrcode
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


MOBILE_CAMERA_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
  <title>Hand-Mouse 스마트폰 카메라</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0f172a;
      color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      display: flex;
      flex-direction: column;
      align-items: center;
      min-height: 100vh;
      padding: 12px;
    }
    h1 { font-size: 1.2rem; margin-bottom: 6px; color: #38bdf8; }
    .status {
      font-size: 0.9rem;
      padding: 6px 14px;
      border-radius: 999px;
      background: #1e293b;
      margin-bottom: 10px;
      color: #94a3b8;
    }
    .status.connected { background: #064e3b; color: #34d399; font-weight: 600; }
    .video-wrap {
      position: relative;
      width: 100%;
      max-width: 540px;
      border-radius: 14px;
      overflow: hidden;
      background: #000;
      border: 2px solid #334155;
      aspect-ratio: 4 / 3;
    }
    video {
      width: 100%;
      height: 100%;
      object-fit: cover;
      transform: scaleX(-1);
    }
    video.rear { transform: none; }
    .controls {
      display: flex;
      gap: 10px;
      margin-top: 12px;
      flex-wrap: wrap;
      justify-content: center;
      width: 100%;
      max-width: 540px;
    }
    button {
      flex: 1;
      min-width: 130px;
      padding: 12px 16px;
      border: none;
      border-radius: 10px;
      background: #0284c7;
      color: white;
      font-size: 0.95rem;
      font-weight: 600;
      cursor: pointer;
    }
    button:active { opacity: 0.85; }
    button.secondary { background: #334155; }
    .hint {
      margin-top: 12px;
      font-size: 0.82rem;
      color: #94a3b8;
      text-align: center;
      line-height: 1.45;
    }
  </style>
</head>
<body>
  <h1>🖐️ Hand-Mouse 스마트폰 카메라</h1>
  <div id="status" class="status">카메라 권한 요청 중...</div>

  <div class="video-wrap">
    <video id="video" autoplay playsinline muted></video>
  </div>

  <div class="controls">
    <button id="switchBtn" type="button">🔄 전면/후면 전환</button>
    <button id="modeBtn" type="button" class="secondary">🎯 모드 전환 (범위/펜/절대)</button>
  </div>
  <div class="controls">
    <button id="dpiDownBtn" type="button" class="secondary">🐢 속도 -</button>
    <button id="dpiUpBtn" type="button">⚡ 속도 +</button>
    <button id="pauseBtn" type="button" class="secondary">⏸️ 일시정지</button>
  </div>
  <div class="controls">
    <button id="calibrateBtn" type="button">🧭 영점 재설정</button>
  </div>
  <div class="controls">
    <button id="calibXBtn" type="button" class="secondary">↔ 좌우만</button>
    <button id="calibCBtn" type="button" class="secondary">▢ 모서리만</button>

  </div>

  <p class="hint">
    • 휴대폰은 책상 위 손을 위에서(또는 비스듬히) 내려다보게 두세요.<br/>
    • <b>엄지+검지</b> 붙임 = 커서 이동 &nbsp;|&nbsp; <b>엄지+중지</b> = 좌클릭 &nbsp;|&nbsp; <b>엄지+약지</b> = 우클릭<br/>
    • <b>🧭 영점 재설정</b>: ① 엄지+검지를 붙이고 오른쪽으로 먼저, 좌우로 여러 번 왔다 갔다 ② 모서리 4곳 손모양을 잡기.<br/>
    • 손을 많이 움직여야 하면 <b>속도 +</b>를 누르세요.
  </p>

  <canvas id="canvas" style="display:none;"></canvas>

  <script>
    const video = document.getElementById('video');
    const canvas = document.getElementById('canvas');
    const ctx = canvas.getContext('2d', { alpha: false, desynchronized: true });
    const statusEl = document.getElementById('status');
    const switchBtn = document.getElementById('switchBtn');
    const modeBtn = document.getElementById('modeBtn');
    const dpiDownBtn = document.getElementById('dpiDownBtn');
    const dpiUpBtn = document.getElementById('dpiUpBtn');
    const pauseBtn = document.getElementById('pauseBtn');
    const calibrateBtn = document.getElementById('calibrateBtn');
    const calibXBtn = document.getElementById('calibXBtn');
    const calibCBtn = document.getElementById('calibCBtn');

    let facingMode = 'user'; // 'user' (전면) or 'environment' (후면)
    let stream = null;
    let ws = null;
    let paused = false;
    let sending = false;
    let lastVideoTime = -1;

    // Target 480x360 resolution & 0.55 JPEG quality for ultra-low Wi-Fi latency
    // (MediaPipe HandLandmarker internally resizes to 224x224 anyway)
    const TARGET_W = 480;
    const TARGET_H = 360;
    const JPEG_QUALITY = 0.55;

    async function startCamera() {
      if (stream) {
        stream.getTracks().forEach(t => t.stop());
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: { ideal: facingMode },
            width: { ideal: TARGET_W },
            height: { ideal: TARGET_H },
            frameRate: { ideal: 30, max: 30 }
          },
          audio: false
        });
        video.srcObject = stream;
        video.className = (facingMode === 'environment') ? 'rear' : '';
        statusEl.textContent = '카메라 준비 완료 - PC 연결 중...';
        connectWebSocket();
        scheduleNextFrame();
      } catch (err) {
        statusEl.textContent = '카메라 접근 실패: ' + err.message;
      }
    }

    function connectWebSocket() {
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
        return;
      }
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(proto + '//' + location.host + '/ws' + location.search);  // carries the pairing key (?k=)
      ws.binaryType = 'arraybuffer';

      ws.onopen = () => {
        statusEl.textContent = '✅ PC와 실시간 연결됨 (초저지연 스트리밍 중)';
        statusEl.classList.add('connected');
      };

      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.status) {
            statusEl.textContent = '✅ ' + data.status;
          }
        } catch (_) {}
      };

      ws.onclose = () => {
        statusEl.textContent = '연결 끊김 - 재연결 시도 중...';
        statusEl.classList.remove('connected');
        setTimeout(connectWebSocket, 1000);
      };
    }

    function sendCurrentVideoFrame() {
      if (
        !paused &&
        ws &&
        ws.readyState === WebSocket.OPEN &&
        ws.bufferedAmount === 0 &&
        !sending &&
        video.readyState >= 2 &&
        video.currentTime !== lastVideoTime
      ) {
        lastVideoTime = video.currentTime;
        const vw = video.videoWidth || TARGET_W;
        const vh = video.videoHeight || TARGET_H;
        const scale = Math.min(1.0, TARGET_W / vw, TARGET_H / vh);
        const cw = Math.max(160, Math.round(vw * scale));
        const ch = Math.max(120, Math.round(vh * scale));

        if (canvas.width !== cw || canvas.height !== ch) {
          canvas.width = cw;
          canvas.height = ch;
        }
        ctx.drawImage(video, 0, 0, cw, ch);
        sending = true;
        canvas.toBlob(blob => {
          if (blob && ws && ws.readyState === WebSocket.OPEN && ws.bufferedAmount === 0) {
            blob.arrayBuffer().then(buf => {
              ws.send(buf);
              sending = false;
            }).catch(() => { sending = false; });
          } else {
            sending = false;
          }
        }, 'image/jpeg', JPEG_QUALITY);
      }
      scheduleNextFrame();
    }

    function scheduleNextFrame() {
      if ('requestVideoFrameCallback' in HTMLVideoElement.prototype) {
        video.requestVideoFrameCallback(sendCurrentVideoFrame);
      } else {
        requestAnimationFrame(sendCurrentVideoFrame);
      }
    }

    function sendCommand(cmd) {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ cmd }));
      }
    }

    switchBtn.addEventListener('click', () => {
      facingMode = (facingMode === 'user') ? 'environment' : 'user';
      startCamera();
    });

    modeBtn.addEventListener('click', () => sendCommand('toggle_mode'));
    dpiUpBtn.addEventListener('click', () => sendCommand('dpi_up'));
    dpiDownBtn.addEventListener('click', () => sendCommand('dpi_down'));
    calibrateBtn.addEventListener('click', () => sendCommand('calibrate'));
    calibXBtn.addEventListener('click', () => sendCommand('calibrate_x'));
    calibCBtn.addEventListener('click', () => sendCommand('calibrate_corners'));

    pauseBtn.addEventListener('click', () => {
      paused = !paused;
      pauseBtn.textContent = paused ? '▶️ 전송 재개' : '⏸️ 일시정지';
    });

    startCamera();
  </script>
</body>
</html>
"""


def get_local_ip() -> str:
    """Detect the primary LAN IPv4 address of the PC."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def ensure_self_signed_cert(cert_dir: Path, ip_addr: str) -> Tuple[Path, Path]:
    """Generate a self-signed certificate and private key for HTTPS mobile camera access."""
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "phone_bridge_cert.pem"
    key_path = cert_dir / "phone_bridge_key.pem"

    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "KR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "HandMouse Local Bridge"),
            x509.NameAttribute(NameOID.COMMON_NAME, ip_addr),
        ]
    )

    san_entries = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    try:
        san_entries.append(x509.IPAddress(ipaddress.IPv4Address(ip_addr)))
    except ValueError:
        pass

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def decode_jpeg_bytes(data: bytes) -> Optional[np.ndarray]:
    """Decode raw JPEG bytes sent from smartphone browser into a BGR numpy frame."""
    if not data:
        return None
    arr = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return frame


def build_waiting_qr_frame(url: str, width: int = 960, height: int = 540) -> np.ndarray:
    """Create a visual instruction screen with a scannable QR code while waiting for phone connection."""
    canvas = np.full((height, width, 3), (24, 28, 38), dtype=np.uint8)

    # Generate QR code image
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr_pil = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_np = np.array(qr_pil, dtype=np.uint8)
    qr_bgr = cv2.cvtColor(qr_np, cv2.COLOR_RGB2BGR)

    qr_size = min(240, height - 120)
    qr_bgr = cv2.resize(qr_bgr, (qr_size, qr_size), interpolation=cv2.INTER_NEAREST)

    qx = 50
    qy = (height - qr_size) // 2
    canvas[qy : qy + qr_size, qx : qx + qr_size] = qr_bgr

    tx = qx + qr_size + 40
    cv2.putText(
        canvas,
        "Smartphone Web Camera Mode (No App Required)",
        (tx, 120),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (80, 220, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "1. Connect phone to the same network (or USB tethering)",
        (tx, 175),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.56,
        (230, 230, 230),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "2. Scan the QR code or open this URL in Chrome / Safari:",
        (tx, 215),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.56,
        (230, 230, 230),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        url,
        (tx, 265),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.78,
        (0, 255, 140),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "3. If browser shows 'Security Warning', tap 'Advanced -> Proceed'",
        (tx, 320),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52,
        (180, 200, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "   (Because it uses a local self-signed HTTPS certificate)",
        (tx, 350),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (150, 165, 190),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "Waiting for smartphone browser connection...",
        (tx, 415),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (0, 200, 255),
        2,
        cv2.LINE_AA,
    )
    return canvas


MAX_PENDING_COMMANDS = 32


class PhoneWebCameraServer:
    """Background HTTPS + WebSocket server receiving live camera frames from a smartphone browser."""

    def __init__(self, port: int = 8443, cert_dir: Optional[Path] = None) -> None:
        self.port = port
        self.host_ip = get_local_ip()
        base_dir = cert_dir or (Path(__file__).resolve().parent.parent / "models" / "certs")
        self.cert_path, self.key_path = ensure_self_signed_cert(base_dir, self.host_ip)
        # A pairing key in the address (and so in the QR code): without it any device on the same network could open
        # /ws and send its own pictures, which the hand tracker would turn into cursor moves and clicks.
        self.token = secrets.token_urlsafe(6)
        self.url = f"https://{self.host_ip}:{self.port}/?k={self.token}"
        self._sockets = 0

        self._latest_frame: Optional[np.ndarray] = None
        self._latest_raw_jpeg: Optional[bytes] = None
        self._frame_seq: int = 0
        self._consumed_seq: int = -1
        self._frame_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._pending_commands: list[str] = []
        self._client_connected = False
        self._waiting_frame = build_waiting_qr_frame(self.url)

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._started = threading.Event()
        self._running = False

    def create_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/ws", self._handle_ws)
        return app

    def _paired(self, request: web.Request) -> bool:
        return hmac.compare_digest(str(request.query.get("k", "")), self.token)

    async def _handle_index(self, request: web.Request) -> web.Response:
        if not self._paired(request):
            return web.Response(status=403, text="Scan the QR code shown on the PC (the address needs its pairing key).")
        return web.Response(text=MOBILE_CAMERA_HTML, content_type="text/html")

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        if not self._paired(request):
            return web.Response(status=403, text="pairing key missing")
        ws = web.WebSocketResponse(max_msg_size=4 * 1024 * 1024)
        await ws.prepare(request)
        self._sockets += 1                 # counted: one phone leaving must not show "no phone" while another is on
        self._client_connected = True
        print(f"[PhoneWebBridge] Smartphone connected from {request.remote}")

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY and msg.data:
                    # Store only the newest raw JPEG bytes in the receive loop;
                    # decoding happens on-demand on the consumer thread so queued stale
                    # packets are immediately dropped (zero bufferbloat).
                    with self._frame_lock:
                        self._latest_raw_jpeg = msg.data
                        self._frame_seq += 1
                    self._frame_event.set()
                elif msg.type == aiohttp.WSMsgType.TEXT and msg.data:
                    try:
                        import json as _json
                        payload = _json.loads(msg.data)
                        cmd = str(payload.get("cmd", "")).strip()
                        if cmd:
                            with self._frame_lock:
                                self._pending_commands.append(cmd[:64])
                                del self._pending_commands[:-MAX_PENDING_COMMANDS]   # nothing may grow without end
                    except Exception:
                        pass
        finally:
            self._sockets = max(0, self._sockets - 1)
            self._client_connected = self._sockets > 0
            self._frame_event.set()
            print("[PhoneWebBridge] Smartphone disconnected")

        return ws

    def start(self) -> bool:
        if self._running:
            return True

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_ctx.load_cert_chain(str(self.cert_path), str(self.key_path))

            app = self.create_app()
            runner = web.AppRunner(app, access_log=None)
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, host="0.0.0.0", port=self.port, ssl_context=ssl_ctx)
            loop.run_until_complete(site.start())

            self._running = True
            self._started.set()
            try:
                loop.run_forever()
            finally:
                loop.run_until_complete(runner.cleanup())
                loop.close()

        self._thread = threading.Thread(target=_runner, daemon=True)
        self._thread.start()
        self._started.wait(timeout=5.0)
        print(f"[PhoneWebBridge] Server listening at {self.url}")
        return self._running

    @property
    def has_client(self) -> bool:
        return self._client_connected and (
            self._latest_frame is not None or self._latest_raw_jpeg is not None
        )

    @property
    def has_new_frame(self) -> bool:
        """Whether a fresh frame has arrived from the smartphone since the last read."""
        with self._frame_lock:
            return self._client_connected and self._frame_seq != self._consumed_seq

    def wait_for_new_frame(self, timeout: float = 0.015) -> bool:
        """Wait briefly for a fresh WebSocket frame to avoid busy-spinning on stale frames."""
        if not self._client_connected:
            return False
        if self.has_new_frame:
            return True
        self._frame_event.wait(timeout=timeout)
        self._frame_event.clear()
        return self.has_new_frame

    def read_frame(self) -> Tuple[bool, np.ndarray]:
        """Return latest smartphone camera frame (decoding only the newest packet), or QR waiting screen."""
        raw_to_decode: Optional[bytes] = None
        seq_snapshot: int = -1
        with self._frame_lock:
            if not self._client_connected:
                return True, self._waiting_frame.copy()
            if self._latest_raw_jpeg is not None and self._frame_seq != self._consumed_seq:
                raw_to_decode = self._latest_raw_jpeg
                seq_snapshot = self._frame_seq

        if raw_to_decode is not None:
            decoded = decode_jpeg_bytes(raw_to_decode)
            if decoded is not None:
                with self._frame_lock:
                    self._latest_frame = decoded
                    self._consumed_seq = seq_snapshot
                return True, decoded.copy()

        with self._frame_lock:
            if self._latest_frame is not None and self._client_connected:
                return True, self._latest_frame.copy()
        return True, self._waiting_frame.copy()

    def pop_commands(self) -> list[str]:
        """Retrieve and clear any remote control commands sent from the smartphone UI."""
        with self._frame_lock:
            if not self._pending_commands:
                return []
            cmds = list(self._pending_commands)
            self._pending_commands.clear()
            return cmds

    def stop(self) -> None:
        self._running = False
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
