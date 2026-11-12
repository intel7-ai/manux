"""Opt-in authenticated Android camera link over an injected RFCOMM stream.

Wire: uint32 big-endian size (including type), uint8 type, payload. A packet
never exceeds 1 MiB. Only camera frames and front-camera preview are accepted;
this endpoint cannot execute input commands. Nothing is persisted or logged.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

from manux.capture.remote import MAX_PACKET, RemoteCamera
from manux.link.security import Pairing, RateLimit
from manux.link.server import unpack_frame

SERVICE_UUID = "6f8c5d2a-4e10-4a61-9c23-58f0c17b2e90"
AUTH, READY, FRAME, ACK, SCREEN_REQUEST, SCREEN, ERROR = range(1, 8)


def encode_packet(kind, payload):
    if isinstance(payload, dict):
        payload = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if not isinstance(payload, bytes) or not 1 <= len(payload) + 1 <= MAX_PACKET:
        raise ValueError("invalid Bluetooth packet size")
    return (len(payload) + 1).to_bytes(4, "big") + bytes([kind]) + payload


def receive_packet(connection, stop, *, timeout=5.0):
    deadline = time.monotonic() + timeout

    def exact(length):
        result = bytearray()
        while len(result) < length:
            if stop.is_set():
                raise ConnectionError("Bluetooth stopped")
            if time.monotonic() >= deadline:
                raise TimeoutError("Bluetooth packet timed out")
            try:
                block = connection.recv(min(length - len(result), 65536))
            except TimeoutError:
                continue
            if not block:
                raise ConnectionError("Bluetooth peer closed")
            result.extend(block)
        return bytes(result)

    size = int.from_bytes(exact(4), "big")
    if not 1 <= size <= MAX_PACKET:
        raise ValueError("invalid Bluetooth packet size")
    packet = exact(size)
    return packet[0], packet[1:]


def _json(payload):
    if len(payload) > 1024:
        raise ValueError("Bluetooth metadata too large")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("Bluetooth metadata must be an object")
    return value


class BluetoothLink:
    """PhoneLink-compatible service; desktop injects the platform listener factory."""

    def __init__(self, data_dir=None, *, transport_factory, source=None, screen=None, **_):
        self.source = source if source is not None else RemoteCamera()
        self.screen = screen
        self.transport_factory = transport_factory
        self.pairing = Pairing()
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._listener = None
        self._thread = None
        self._connection = None
        self._device = None
        self._blocked = False
        self._epoch = 0
        self._front = False
        self._screen_epoch = 0
        self._screen_thread = None
        self.last_error = ""

    @property
    def url(self):
        return ""  # Android selects a bonded PC; there is no browser/QR URL.

    @property
    def local_name(self):
        """Display-only adapter name so the phone can select the correct PC."""
        with self._lock:
            return getattr(self._listener, "local_name", "")

    @property
    def device(self):
        with self._lock:
            return dict(self._device) if self._device else None

    @property
    def blocked(self):
        with self._lock:
            return self._blocked

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            listener = self.transport_factory()
            try:
                listener.open()  # Report radio/SDP errors synchronously to the UI.
            except BaseException:
                listener.close()
                raise
            self._listener = listener
            self._stop.clear()
            self.last_error = ""
            self._thread = threading.Thread(target=self._run, name="manux-bluetooth", daemon=True)
            self._thread.start()

    def issue_code(self):
        with self._lock:
            if self._blocked or self._device is not None:
                raise RuntimeError("blocked or a phone is already connected")
            return self.pairing.issue()

    def disconnect(self):
        self._disconnect(clear_pairing=True)

    def _disconnect(self, *, clear_pairing):
        with self._lock:
            self._epoch += 1
            self._screen_epoch += 1
            connection, self._connection = self._connection, None
            self._device = None
            self._front = False
            if clear_pairing:
                self.pairing.clear()
            self.source.disconnect()
            if self.screen:
                self.screen.clear()
        if connection:
            connection.close()

    def block(self):
        with self._lock:
            self._blocked = True
        self.disconnect()

    def unblock(self):
        with self._lock:
            self._blocked = False

    def stop(self):
        self._stop.set()
        self.disconnect()
        # accept() times out within 250ms. Leave Winsock alive until its user exits.
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=4)
        screen_thread = self._screen_thread
        if screen_thread and screen_thread is not threading.current_thread():
            screen_thread.join(timeout=4)
        # Screen sends use the same Winsock lifetime as the receiver. Close the
        # adapter only after both users have left (native sends are bounded at 3s).
        listener, self._listener = self._listener, None
        if listener:
            listener.close()
        self._thread = None

    def _send(self, connection, kind, payload, *, epoch=None):
        packet = encode_packet(kind, payload)
        with self._send_lock:
            with self._lock:
                if (connection is not self._connection or self._stop.is_set()
                        or (epoch is not None and epoch != self._epoch)):
                    return False
            connection.sendall(packet)
            return True

    def _error(self, connection, error, *, screen=False):
        message = {"error": error}
        if screen:
            message["request"] = "screen"
        return self._send(connection, ERROR, message)

    def _run(self):
        listener = self._listener
        while not self._stop.is_set():
            try:
                connection = listener.accept(timeout=.25)
            except TimeoutError:
                continue
            except OSError as error:
                # A peer that cannot authenticate/encrypt is rejected by Windows
                # accept with WSAEHOSTDOWN. It must not kill the advertised service.
                if error.errno in (10064, 10053, 10054):
                    self._stop.wait(.1)
                    continue
                if not self._stop.is_set():
                    self.last_error = "Bluetooth 연결이 중단됐습니다. PC Bluetooth를 확인하고 다시 켜 주세요."
                break
            with self._lock:
                if self._stop.is_set() or self._blocked:
                    connection.close()
                    continue
                self._connection = connection
                epoch = self._epoch
            try:
                self._serve(connection, epoch)
            except (OSError, ValueError, TypeError, KeyError, UnicodeError):
                # Payloads, credentials, peer identifiers and images are never logged.
                pass
            finally:
                with self._lock:
                    if self._connection is connection:
                        # Authentication failures never consume the displayed code.
                        # Successful auth already consumed it inside Pairing.
                        self._disconnect(clear_pairing=False)
                connection.close()

    def _serve(self, connection, epoch):
        kind, payload = receive_packet(connection, self._stop)
        if kind != AUTH:
            self._error(connection, "authentication_required")
            return
        auth = _json(payload)
        with self._lock:
            if self._blocked or self._epoch != epoch or self._connection is not connection:
                return
            try:
                # Pairing's IP restriction belongs to its HTTP entry point. Here the
                # native transport already authenticates/encrypts the Bluetooth peer;
                # exchange/consume the one-use code locally, without issuing a token
                # over the wire. No loopback listener or IP claim is accepted.
                token = self.pairing.pair(auth.get("code"), "127.0.0.1", auth.get("device"))
                name = self.pairing.authenticate(token, "127.0.0.1")
            except (ValueError, PermissionError):
                name = None
            if name:
                self._device = {"name": name, "peer": connection.peer}
                self.source.connect()
        if not name:
            self._error(connection, "authentication_failed")
            return
        self._send(connection, READY, {"server_time": time.time()}, epoch=epoch)
        frames, screens = RateLimit(36, 1), RateLimit(6, 1)
        while not self._stop.is_set():
            kind, payload = receive_packet(connection, self._stop)
            if kind == FRAME:
                if not frames.allow():
                    raise ValueError("Bluetooth frame rate exceeded")
                metadata, jpeg = unpack_frame(payload)
                with self._lock:
                    if self._epoch != epoch or self._connection is not connection:
                        return
                    accepted = self.source.submit(metadata, jpeg)
                    if accepted and self._front != (metadata.facing == "user"):
                        self._front = metadata.facing == "user"
                        self._screen_epoch += 1
                        if self.screen:
                            self.screen.clear()
                self._send(connection, ACK, {"seq": metadata.seq, "accepted": accepted,
                                            "server_time": time.time()}, epoch=epoch)
            elif kind == SCREEN_REQUEST:
                request = _json(payload)
                if request.get("profile", "compact") not in ("detail", "balanced", "compact"):
                    self._error(connection, "invalid_request", screen=True)
                elif not screens.allow():
                    self._error(connection, "screen_busy", screen=True)
                else:
                    self._request_screen(connection, epoch)
            else:
                self._error(connection, "invalid_request")
                return

    def _request_screen(self, connection, epoch):
        with self._lock:
            available = self.screen is not None and self._front and self.source.is_open
            busy = self._screen_thread is not None and self._screen_thread.is_alive()
            screen_epoch = self._screen_epoch
            if available and not busy:
                self._screen_thread = threading.Thread(
                    target=self._capture_screen, args=(connection, epoch, screen_epoch),
                    name="manux-bluetooth-screen", daemon=True)
                self._screen_thread.start()
                return
        self._error(connection, "screen_busy" if busy else "screen_unavailable", screen=True)

    def _capture_screen(self, connection, epoch, screen_epoch):
        try:
            jpeg = asyncio.run(self.screen.request(profile="compact"))
            with self._send_lock:
                with self._lock:
                    valid = (self._connection is connection and self._epoch == epoch
                             and self._screen_epoch == screen_epoch and self._front
                             and self.source.is_open and not self._stop.is_set())
                if not valid or not isinstance(jpeg, bytes) or not 0 < len(jpeg) <= 512 * 1024:
                    raise ValueError("screen unavailable")
                connection.sendall(encode_packet(SCREEN, jpeg))
        except (asyncio.CancelledError, OSError, ValueError, RuntimeError):
            try:
                self._error(connection, "screen_unavailable", screen=True)
            except OSError:
                connection.close()
