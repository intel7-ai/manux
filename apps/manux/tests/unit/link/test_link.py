"""Real local TLS/WebSocket with synthetic JPEGs; no phone, webcam, or Windows input."""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from manux.link.certificates import ensure_certificate, server_context
from manux.link.security import Pairing, RateLimit, private_address
from manux.link.server import PhoneLink, unpack_frame

aiohttp = pytest.importorskip("aiohttp")
pytest.importorskip("cryptography")
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


def packet(seq=0, **changes):
    cv2 = pytest.importorskip("cv2")
    meta = dict(kind="frame", seq=seq, captured_at=time.time(), width=16, height=12,
                mirrored=False, rotation=0, facing="environment")
    meta.update(changes)
    data = json.dumps(meta).encode()
    jpeg = cv2.imencode(".jpg", np.zeros((12, 16, 3), np.uint8))[1].tobytes()
    return len(data).to_bytes(4, "big") + data + jpeg


@pytest.mark.parametrize("ip", ["10.1.2.3", "192.168.0.1", "172.16.0.1", "172.31.255.255", "fd00::1", "127.0.0.1", "::1", "::ffff:192.168.0.2"])
def test_private_addresses(ip):
    assert private_address(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "172.32.0.1", "192.0.2.1", "169.254.1.1", "0.0.0.0", "::", "fe80::1", "ff02::1", "example.com"])
def test_non_private_addresses(ip, tmp_path):
    assert not private_address(ip)
    with pytest.raises(ValueError):
        PhoneLink(tmp_path, host=ip)


def test_pairing_expiration_reuse_ip_binding_and_reissue():
    now = [0.0]
    pairing = Pairing(clock=lambda: now[0])
    code = pairing.issue()
    assert len(code) == 6 and code.isdigit()
    now[0] = 120
    with pytest.raises(ValueError):
        pairing.pair(code, "192.168.0.2", "phone")
    code = pairing.issue()
    token = pairing.pair(code, "192.168.0.2", "phone")
    assert len(token) >= 43
    with pytest.raises(ValueError):
        pairing.pair(code, "192.168.0.2", "phone")
    assert pairing.authenticate(token, "192.168.0.3") is None
    assert pairing.authenticate(token, "192.168.0.2") == "phone"
    assert pairing.authenticate(token, "192.168.0.2") is None
    now[0] += 61
    token = pairing.pair(pairing.issue(), "192.168.0.2", "phone")
    now[0] += 120
    assert pairing.authenticate(token, "192.168.0.2") is None
    token = pairing.pair(pairing.issue(), "192.168.0.2", "phone")
    pairing.issue()
    assert pairing.authenticate(token, "192.168.0.2") is None


def test_pairing_and_http_rate_limits():
    pairing = Pairing()
    pairing.issue()
    for _ in range(5):
        with pytest.raises(ValueError):
            pairing.pair("wrong!", "127.0.0.1", "phone")
    with pytest.raises(PermissionError):
        pairing.pair("wrong!", "127.0.0.1", "phone")
    now = [0.0]
    rate = RateLimit(2, 1, lambda: now[0])
    assert rate.allow() and rate.allow() and not rate.allow()
    now[0] = 1.0
    assert rate.allow()


def test_certificates_outside_repo_only_and_reuse(tmp_path):
    repo = Path(__file__).resolve().parents[5]
    with pytest.raises(ValueError, match="outside"):
        ensure_certificate(repo / ".local/forbidden-phone-certs", "127.0.0.1")
    assert not (repo / ".local/forbidden-phone-certs").exists()
    cert, key = ensure_certificate(tmp_path, "127.0.0.1")
    original = cert.read_bytes(), key.read_bytes()
    assert cert.is_relative_to(tmp_path) and key.is_relative_to(tmp_path)
    assert ensure_certificate(tmp_path, "127.0.0.1") == (cert, key)
    assert (cert.read_bytes(), key.read_bytes()) == original
    ensure_certificate(tmp_path, "192.168.0.2")  # address change renews SAN
    assert cert.read_bytes() != original[0]


def test_certificate_symlink_escape(tmp_path):
    repo = Path(__file__).resolve().parents[5]
    alias = tmp_path / "checkout"
    try:
        alias.symlink_to(repo, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable on this machine")
    with pytest.raises(ValueError):
        ensure_certificate(alias / ".local/cert-escape", "127.0.0.1")


@pytest.mark.parametrize("data", [b"", b"\x00\x00\xff\xff{}", b"\x00\x00\x00\x02[]bad", b"x" * (1024 * 1024 + 1)])
def test_bad_envelope(data):
    with pytest.raises(ValueError):
        unpack_frame(data)


async def client_for(link):
    server = TestServer(link.create_app(), scheme="https")
    await server.start_server(ssl=server_context(link.data_dir, link.host))
    client = TestClient(server, connector=aiohttp.TCPConnector(ssl=False))
    await client.start_server()
    origin = str(server.make_url("/")).rstrip("/")
    return client, origin


async def authenticate(client, origin, code):
    response = await client.post("/pair", json={"code": code, "device": "Synthetic phone"}, headers={"Origin": origin})
    assert response.status == 200
    token = (await response.json())["token"]
    ws = await client.ws_connect("/ws", origin=origin)
    await ws.send_json({"kind": "auth", "token": token})
    assert (await ws.receive_json())["kind"] == "ready"
    return ws, token


def test_tls_client_jpeg_to_remote_and_disconnect_block(tmp_path):
    async def run():
        link = PhoneLink(tmp_path, host="127.0.0.1")
        client, origin = await client_for(link)
        try:
            response = await client.get("/")
            assert response.status == 200 and "TARGET_W = 480" in await response.text()
            assert response.headers["Permissions-Policy"] == "camera=(self), microphone=()"
            ws, token = await authenticate(client, origin, link.issue_code())
            assert link.device == {"name": "Synthetic phone", "peer": "127.0.0.1"}
            await ws.send_json({"kind": "sync", "sent": 123})
            assert (await ws.receive_json())["sent"] == 123
            await ws.send_bytes(packet())
            assert (await ws.receive_json())["accepted"]
            info, image = await asyncio.to_thread(link.source.read, .5)
            assert image.shape == (12, 16, 3) and info.rotation == 0
            assert link.source.last_latency_ms is not None
            await ws.send_bytes(packet(0))
            assert not (await ws.receive_json())["accepted"]
            await ws.send_bytes(packet(1, captured_at=time.time() - 2))
            assert not (await ws.receive_json())["accepted"]
            assert link.source.read(0) is None
            await link.disconnect_async()
            assert link.device is None and not link.source.is_open and link.source.read(0) is None
            await ws.close()
            reused = await client.ws_connect("/ws", origin=origin)
            await reused.send_json({"kind": "auth", "token": token})
            assert (await reused.receive()).type == aiohttp.WSMsgType.CLOSE
            await reused.close()
            ws, _ = await authenticate(client, origin, link.issue_code())
            await link.disconnect_async(block=True)
            assert (await client.get("/")).status == 403
            assert link.pairing.token is None and link.pairing.code is None
            assert not link.source.is_open
            await ws.close()
        finally:
            await client.close()
    asyncio.run(run())


def test_missing_auth_bad_origin_http_and_external_peer_rejected(tmp_path, monkeypatch):
    async def run():
        import manux.link.server as module
        monkeypatch.setattr(module, "AUTH_TIMEOUT", .02)
        link = PhoneLink(tmp_path, host="127.0.0.1")
        client, origin = await client_for(link)
        try:
            for headers in ({}, {"Origin": "https://evil.example"}):
                assert (await client.post("/pair", json={}, headers=headers)).status == 403
            assert (await client.get("/", headers={"Host": "evil.example"})).status == 403
            with pytest.raises(aiohttp.WSServerHandshakeError):
                await client.ws_connect("/ws", origin="https://evil.example")
            for payload in (None, {"kind": "auth", "token": "invalid"}, {"kind": "frame"}):
                ws = await client.ws_connect("/ws", origin=origin)
                if payload is not None:
                    await ws.send_json(payload)
                assert (await ws.receive()).type == aiohttp.WSMsgType.CLOSE
                await ws.close()
                assert not link.source.is_open
            guard = link.create_app().middlewares[0]
            for peer, secure in (("8.8.8.8", True), ("127.0.0.1", False)):
                request = SimpleNamespace(transport=SimpleNamespace(get_extra_info=lambda _: (peer, 1)),
                                          secure=secure, host="127.0.0.1", headers={"X-Forwarded-For": "192.168.0.1"})
                with pytest.raises(web.HTTPForbidden):
                    await guard(request, lambda _: None)
            link._http_limit = RateLimit(1, 60)
            assert (await client.get("/")).status == 200
            assert (await client.get("/")).status == 429
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize("bad", [b"bad-jpeg", "audio", "flood"])
def test_invalid_frame_audio_and_rate_abuse_close_socket(tmp_path, bad):
    async def run():
        link = PhoneLink(tmp_path, host="127.0.0.1")
        client, origin = await client_for(link)
        try:
            ws, _ = await authenticate(client, origin, link.issue_code())
            if bad == "audio":
                await ws.send_json({"kind": "audio"})
            elif bad == "flood":
                for i in range(36):
                    await ws.send_bytes(packet(i))
                    await ws.receive_json()
                await ws.send_bytes(packet(37))
            else:
                await ws.send_bytes(bad)
            assert (await ws.receive()).type == aiohttp.WSMsgType.CLOSE
            await ws.close()
            assert not link.source.is_open
        finally:
            await client.close()
    asyncio.run(run())


def test_default_off_lazy_imports_and_server_lifecycle(tmp_path):
    script = ('import sys; from manux.link.server import PhoneLink; '
              'assert not ({"aiohttp", "cryptography", "cv2", "mediapipe"} & sys.modules.keys())')
    assert subprocess.run([sys.executable, "-c", script], capture_output=True).returncode == 0
    link = PhoneLink(tmp_path, host="127.0.0.1", port=0)
    assert not tmp_path.joinpath("link").exists() and not link.source.is_open
    assert link.start()
    try:
        assert link.issue_code() and link.url.startswith("https://127.0.0.1:")
        link.block()
        assert link.blocked
        with pytest.raises(RuntimeError):
            link.issue_code()
        link.unblock()
        assert link.issue_code()
    finally:
        link.stop()
    assert not link._running and link._thread is None and not link.source.is_open
