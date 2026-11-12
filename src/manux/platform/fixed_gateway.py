"""An opt-in fixed HTTPS address leased to one owned Quick Tunnel.

Only the stable origin and a current-user DPAPI blob are persisted. Authentication
tokens, leases, temporary endpoints and HTTP response bodies are never logged.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from manux.platform.quick_tunnel import QuickTunnel, _loopback_origin

CONFIG_NAME = "internet-gateway.json"
HTTP_TIMEOUT = 5.0
LEASE_TTL = 90
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43,128}\Z", re.ASCII)
_LEASE = re.compile(r"[A-Za-z0-9_-]{16,128}\Z", re.ASCII)


def _origin(value):
    try:
        if not isinstance(value, str) or value != value.strip() or any(c.isspace() for c in value):
            raise ValueError
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or len(host) > 253 or "." not in host
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                       for label in host.split("."))):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return "https://" + host
    except (TypeError, ValueError):
        pass
    raise ValueError("고정 연결 주소는 공개 HTTPS 사이트 주소여야 합니다.")


def _token(value):
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise ValueError("고정 연결 인증 설정이 올바르지 않습니다.")
    return value


def _dpapi(data, *, decrypt):
    if sys.platform != "win32":
        raise OSError("고정 연결 인증 키 보호는 Windows에서만 지원합니다.")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                          ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    memory = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(memory, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    try:
        if not operation(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
            raise OSError("Windows가 고정 연결 인증 키를 보호하거나 읽지 못했습니다.")
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        ctypes.memset(memory, 0, len(memory))
        if outgoing.pbData:
            ctypes.memset(outgoing.pbData, 0, outgoing.cbData)
            kernel.LocalFree(outgoing.pbData)


def protect_token(data):
    return _dpapi(data, decrypt=False)


def unprotect_token(data):
    return _dpapi(data, decrypt=True)


@dataclass(frozen=True)
class GatewayConfig:
    url: str
    token: str = field(repr=False)


def load_gateway_config(data_dir, *, unprotect=None):
    """Return None if unconfigured; malformed/unreadable credentials fail closed."""
    path = Path(data_dir) / CONFIG_NAME
    try:
        with path.open("rb") as stream:
            raw = stream.read(16385)
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError("고정 연결 인증 설정 파일을 읽지 못했습니다.") from None
    try:
        if len(raw) > 16384:
            raise ValueError
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("version") != 1 or type(data["version"]) is not int:
            raise ValueError
        url = _origin(data["url"])
        blob = base64.b64decode(data["protected_token"], validate=True)
        if not 1 <= len(blob) <= 8192:
            raise ValueError
        value = (unprotect or unprotect_token)(blob).decode("ascii")
        return GatewayConfig(url, _token(value))
    except Exception:
        raise ValueError("고정 연결 인증 설정을 읽지 못했습니다. 이 Windows 계정에서 설정을 확인해 주세요.") from None


def bootstrap_gateway(data_dir, gateway_url, *, protect=None, unprotect=None):
    """Create one 256-bit PC key; return only its SHA256 for server provisioning.

    Repeating setup for the same origin preserves the existing key. Changing a
    configured origin is refused so a setup mistake cannot replace a live key.
    """
    url = _origin(gateway_url)
    current = load_gateway_config(data_dir, unprotect=unprotect)
    if current is not None:
        if current.url != url:
            raise ValueError("다른 고정 연결 주소가 이미 설정돼 있습니다.")
        return hashlib.sha256(current.token.encode("ascii")).hexdigest()
    token = secrets.token_urlsafe(32)
    protected = (protect or protect_token)(token.encode("ascii"))
    if not isinstance(protected, bytes) or not 1 <= len(protected) <= 8192:
        raise ValueError("Windows 인증 키 보호 결과가 올바르지 않습니다.")
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"version": 1, "url": url,
                          "protected_token": base64.b64encode(protected).decode("ascii")}).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".internet-gateway-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # An atomic no-overwrite publication also handles concurrent setup calls.
        os.link(temporary, directory / CONFIG_NAME)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def gateway_request(method, url, token, payload, *, timeout=HTTP_TIMEOUT):
    """Small HTTPS JSON exchange; callers may inject this boundary for private QA."""
    parsed = urlsplit(url)
    _origin(f"{parsed.scheme}://{parsed.netloc}")
    if (parsed.path != "/_manux/endpoint" or parsed.query or parsed.fragment
            or method not in ("POST", "PUT", "DELETE") or not 0 < timeout <= HTTP_TIMEOUT):
        raise ValueError("invalid gateway request")
    request = Request(url, data=json.dumps(payload, separators=(",", ":")).encode("utf-8"), method=method,
                      headers={"Authorization": "Bearer " + _token(token), "Content-Type": "application/json",
                               "User-Agent": "MANUX/0.5 (Windows; FixedGateway)",
                               "Accept": "application/json", "Cache-Control": "no-store"})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=min(HTTP_TIMEOUT, timeout)) as response:
            body = response.read(4097)
            if len(body) > 4096:
                raise ValueError("고정 연결 서버의 응답이 너무 큽니다.")
            return response.status, json.loads(body) if body else None
    except HTTPError as error:
        status = error.code
        error.close()
        return status, None


class _Run:
    def __init__(self, quick, origin):
        self.quick, self.origin = quick, origin
        self.stopped = threading.Event()
        self.cleanup_lock = threading.Lock()
        self.lease = None
        self.deadline = 0.
        self.endpoint = ""
        self.worker = None


class FixedGatewayTunnel:
    def __init__(self, data_dir, gateway_url=None, *, token=None, quick_factory=QuickTunnel,
                 request=None, heartbeat_interval=25., clock=time.monotonic):
        if token is None:
            config = load_gateway_config(data_dir)
            if config is None:
                raise ValueError("고정 연결 주소가 아직 설정되지 않았습니다.")
            if gateway_url is not None and _origin(gateway_url) != config.url:
                raise ValueError("고정 연결 주소와 저장된 인증 설정이 일치하지 않습니다.")
            gateway_url, token = config.url, config.token
        self.gateway_url, self._token = _origin(gateway_url), _token(token)
        self._quick_factory, self._request = quick_factory, request or gateway_request
        self._interval, self._clock = float(heartbeat_interval), clock
        if not 0 < self._interval <= 25:
            raise ValueError("invalid gateway heartbeat interval")
        self._lock = threading.RLock()
        self._open_lock, self._close_lock = threading.Lock(), threading.Lock()
        self._run = None
        self._last_error = ""

    def _fail(self, run, message):
        with self._lock:
            if self._run is run and not run.stopped.is_set():
                self._last_error = message
                run.stopped.set()

    def _alive(self, run):
        if run is None or run.stopped.is_set() or run.lease is None:
            return False
        if not run.quick.process_alive:
            self._fail(run, "인터넷 연결 도우미가 종료됐습니다. 인터넷 연결을 다시 켜 주세요.")
        elif self._clock() >= run.deadline:
            self._fail(run, "고정 주소 연결이 만료됐습니다. 인터넷 연결을 다시 켜 주세요.")
        return not run.stopped.is_set()

    @property
    def process_alive(self):
        with self._lock:
            return self._alive(self._run)

    @property
    def public_url(self):
        return self.gateway_url if self.process_alive else ""

    @property
    def url(self):
        with self._lock:
            return self._run.endpoint if self._alive(self._run) else ""

    @property
    def last_error(self):
        with self._lock:
            self._alive(self._run)
            return self._last_error

    def _exchange(self, method, payload, *, timeout=HTTP_TIMEOUT):
        return self._request(method, self.gateway_url + "/_manux/endpoint", self._token, payload, timeout=timeout)

    def open(self, local_url):
        origin = _loopback_origin(local_url)
        with self._open_lock:
            with self._lock:
                if self._alive(self._run):
                    if self._run.origin == origin:
                        return self._run.endpoint
                    raise RuntimeError("고정 연결이 이미 다른 서버에 연결돼 있습니다.")
            self.close()
            run = _Run(self._quick_factory(), origin)
            with self._lock:
                self._run, self._last_error = run, ""
            try:
                endpoint = run.quick.open(origin)
                if not re.fullmatch(r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com", endpoint):
                    raise ValueError
                if run.stopped.is_set():
                    raise RuntimeError
                started = self._clock()
                status, data = self._exchange("POST", {"endpoint": endpoint})
                if (status not in (200, 201) or not isinstance(data, dict) or data.get("ttl") != LEASE_TTL
                        or type(data.get("ttl")) is not int or not isinstance(data.get("lease"), str)
                        or _LEASE.fullmatch(data["lease"]) is None):
                    raise ValueError
                with self._lock:
                    run.lease, run.deadline = data["lease"], started + LEASE_TTL
                    if self._run is not run or run.stopped.is_set() or self._clock() >= run.deadline:
                        raise RuntimeError
                    run.endpoint = endpoint
                    run.worker = threading.Thread(target=self._heartbeat, args=(run,),
                                                  name="manux-fixed-gateway", daemon=True)
                    run.worker.start()
                return endpoint
            except Exception:
                self._fail(run, "고정 주소에 PC를 연결하지 못했습니다. 인터넷 연결을 다시 켜 주세요.")
                self._cleanup(run)
                raise RuntimeError(self._last_error or "고정 주소 연결 시작이 취소됐습니다.") from None

    def _heartbeat(self, run):
        try:
            while True:
                delay = max(0., min(self._interval, run.deadline - self._clock()))
                if run.stopped.wait(delay):
                    break
                with self._lock:
                    if self._run is not run or not self._alive(run):
                        break
                    lease, remaining = run.lease, run.deadline - self._clock()
                started = self._clock()
                try:
                    status, _ = self._exchange("PUT", {"lease": lease}, timeout=min(HTTP_TIMEOUT, remaining))
                except Exception:
                    status = 0
                with self._lock:
                    if self._run is not run or run.stopped.is_set():
                        break
                    if status == 204 and self._clock() < run.deadline:
                        run.deadline = started + LEASE_TTL
                    elif status in (401, 403, 409):
                        self._fail(run, "고정 주소 연결이 교체됐거나 인증이 거절됐습니다. 인터넷 연결을 다시 켜 주세요.")
                    else:
                        self._alive(run)  # Retry transient errors only within the last confirmed lease.
        finally:
            self._cleanup(run)

    def _cleanup(self, run):
        with run.cleanup_lock:
            with self._lock:
                lease, run.lease = run.lease, None
            if lease is not None:
                try:
                    self._exchange("DELETE", {"lease": lease})
                except Exception:
                    pass  # The bounded server lease also expires without this best-effort revoke.
            run.quick.close()

    def close(self):
        with self._close_lock:
            with self._lock:
                run, self._run = self._run, None
                if run is None:
                    return
                run.stopped.set()
            if run.worker is not None and run.worker is not threading.current_thread():
                run.worker.join(timeout=2 * HTTP_TIMEOUT + 10)
            self._cleanup(run)

    stop = close
