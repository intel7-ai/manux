"""Real local HTTP/WS and synthetic WebRTC. No public tunnel, webcam or input."""
import asyncio
import json
import time

import numpy as np
import pytest

from manux.link.internet import InternetLink

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer

ORIGIN = "https://synthetic-manux.trycloudflare.com"
HEADERS = {"Host": "synthetic-manux.trycloudflare.com", "Origin": ORIGIN}


def packet(seq=0, **changes):
    cv2 = pytest.importorskip("cv2")
    metadata = dict(kind="frame", seq=seq, captured_at=time.time(), width=16, height=12,
                    mirrored=False, rotation=0, facing="user")
    metadata.update(changes)
    header = json.dumps(metadata).encode()
    jpeg = cv2.imencode(".jpg", np.full((12, 16, 3), 90, np.uint8))[1].tobytes()
    return len(header).to_bytes(4, "big") + header + jpeg


async def client_for(link, *, pinned=True):
    if pinned:
        link._pin_public_url(ORIGIN)
    client = TestClient(TestServer(link.create_app()), headers=HEADERS)
    await client.start_server()
    return client


async def authenticate(link, client):
    result = await client.post("/pair", json={"code": link.issue_code(), "device": "Synthetic phone"})
    assert result.status == 200
    data = await result.json()
    assert data["iceServers"] == []
    ws = await client.ws_connect("/ws")
    await ws.send_json({"kind": "auth", "token": data["token"]})
    ready = await ws.receive_json()
    assert ready["kind"] == "ready"
    return ws, ready


def test_public_origin_is_pinned_not_forwarded_and_auth_required():
    async def run():
        link = InternetLink(tunnel_factory=lambda: None, ice_servers=[])
        client = await client_for(link, pinned=False)
        try:
            assert (await client.get("/")).status == 403
            link._pin_public_url(ORIGIN)
            for headers in ({"Host": "evil.test"}, {"Origin": "https://evil.test"},
                            {"Origin": "null", "X-Forwarded-For": "127.0.0.1"}):
                assert (await client.post("/pair", json={}, headers=headers)).status == 403
            assert (await client.post("/screen")).status == 403
            assert (await client.post("/pair", json={"code": "000000", "device": "test"})).status == 403
            ws, _ = await authenticate(link, client)
            assert link.device["name"] == "Synthetic phone"
            assert (await client.post("/pair", json={})).status == 409
            await ws.send_str("[]")
            assert (await ws.receive()).type == aiohttp.WSMsgType.CLOSE
            await asyncio.sleep(.02)
            assert link.device is None and not link.source.is_open
        finally:
            await client.close()
    asyncio.run(run())


def test_relay_frames_screen_auth_generation_and_stop():
    class Screen:
        def __init__(self):
            self.called = 0
            self.cleared = 0
            self.profiles = []
        async def request(self, *, profile):
            self.called += 1
            self.profiles.append(profile)
            return b"\xff\xd8demo\xff\xd9"
        def clear(self):
            self.cleared += 1
    async def run():
        screen = Screen()
        link = InternetLink(tunnel_factory=lambda: None, ice_servers=[], screen=screen)
        client = await client_for(link)
        try:
            ws, ready = await authenticate(link, client)
            await ws.send_json({"kind": "fallback"})
            assert await ws.receive_json() == {"kind": "mode", "mode": "relay"}
            await ws.send_bytes(packet())
            assert (await ws.receive_json())["accepted"]
            info, image = await asyncio.to_thread(link.source.read, .2)
            assert image.shape == (12, 16, 3)
            await ws.send_bytes(packet())
            assert not (await ws.receive_json())["accepted"]
            await ws.send_bytes(packet(1, captured_at=time.time() - 2))
            assert not (await ws.receive_json())["accepted"]
            authorized = {"Authorization": "Bearer " + ready["screen_token"]}
            assert (await client.post("/screen", headers=authorized)).status == 200
            assert screen.called == 1
            detailed = {**authorized, "X-Manux-Screen-Profile": "internet_detail"}
            assert (await client.post("/screen", headers=detailed)).status == 200
            assert screen.profiles == ["balanced", "internet_detail"]
            await ws.send_json({"kind": "camera", "mirrored": False, "rotation": 0, "facing": "environment"})
            await ws.send_json({"kind": "sync", "sent": 5})
            assert (await ws.receive_json())["kind"] == "sync"
            assert (await client.post("/screen", headers=authorized)).status == 409
            await link.disconnect_async()
            assert link.source.read(0) is None and link.device is None
            assert (await client.post("/screen", headers=authorized)).status == 403
            assert link._pc is None and not link._rtc_tasks
        finally:
            await client.close()
    asyncio.run(run())


def test_internet_pair_rate_limit_and_pending_session_invalidation():
    async def run():
        link = InternetLink(tunnel_factory=lambda: None, ice_servers=[])
        client = await client_for(link)
        try:
            code = link.issue_code()
            for _ in range(5):
                assert (await client.post("/pair", json={"code": "invalid", "device": "test"})).status == 403
            assert (await client.post("/pair", json={"code": code, "device": "test"})).status == 429
            ws = await client.ws_connect("/ws")
            await link.disconnect_async()
            assert (await ws.receive()).type == aiohttp.WSMsgType.CLOSE
            assert link.device is None
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize("camera_size,monitor_codec", [((1280, 960), "H264"), ((960, 1280), "VP8")])
def test_real_rtc_camera_and_monitor_then_relay_cleanup(camera_size, monitor_codec):
    aiortc = pytest.importorskip("aiortc")
    av = pytest.importorskip("av")
    cv2 = pytest.importorskip("cv2")

    class Screen:
        calls = 0
        async def request(self, *, profile):
            self.calls += 1
            assert profile == "internet_hd"
            # Even a capture provider that ignores the profile cannot emit an
            # oversized level-3.1 H264 frame.
            return cv2.imencode(".jpg", np.full((1080, 1920, 3), 170, np.uint8))[1].tobytes()
        def clear(self):
            pass

    class Camera(aiortc.VideoStreamTrack):
        async def recv(self):
            pts, time_base = await self.next_timestamp()
            frame = av.VideoFrame.from_ndarray(np.full((camera_size[1], camera_size[0], 3), 95, np.uint8),
                                               format="bgr24")
            frame.pts, frame.time_base = pts, time_base
            return frame

    async def run():
        screen = Screen()
        link = InternetLink(tunnel_factory=lambda: None, ice_servers=[], screen=screen)
        client = await client_for(link)
        peer = aiortc.RTCPeerConnection(aiortc.RTCConfiguration(iceServers=[]))
        monitor = asyncio.get_running_loop().create_future()
        tasks = []

        @peer.on("track")
        def received(track):
            async def consume():
                while not monitor.done():
                    frame = await track.recv()
                    if frame.width == 1280:
                        monitor.set_result(frame)
            tasks.append(asyncio.create_task(consume()))

        try:
            ws, _ = await authenticate(link, client)
            peer.addTransceiver(Camera(), direction="sendonly")
            monitor_receiver = peer.addTransceiver("video", direction="recvonly")
            if monitor_codec == "VP8":
                monitor_receiver.setCodecPreferences([
                    codec for codec in aiortc.RTCRtpReceiver.getCapabilities("video").codecs
                    if codec.mimeType in ("video/VP8", "video/rtx")])
            await peer.setLocalDescription(await peer.createOffer())
            await ws.send_json({"kind": "camera", "mirrored": True, "rotation": 0, "facing": "user"})
            await ws.send_json({"kind": "offer", "sdp": peer.localDescription.sdp})
            answer = await asyncio.wait_for(ws.receive_json(), 10)
            assert answer["kind"] == "answer", answer
            from aiortc.sdp import SessionDescription
            answer_media = SessionDescription.parse(answer["sdp"]).media
            assert answer_media[0].rtp.codecs[0].mimeType == "video/VP8"
            assert answer_media[1].rtp.codecs[0].mimeType == "video/" + monitor_codec
            await peer.setRemoteDescription(aiortc.RTCSessionDescription(sdp=answer["sdp"], type="answer"))
            assert (await asyncio.wait_for(ws.receive_json(), 10))["mode"] == "webrtc"
            item = await asyncio.to_thread(link.source.read, 5)
            assert item is not None and item[1].shape == (camera_size[1], camera_size[0], 3) and item[0].mirrored
            monitor_frame = await asyncio.wait_for(monitor, 5)
            assert (monitor_frame.width, monitor_frame.height) == (1280, 720) and screen.calls > 0
            assert link.source.last_latency_ms is None
            await ws.send_json({"kind": "fallback"})
            mode = await asyncio.wait_for(ws.receive_json(), 5)
            assert mode == {"kind": "mode", "mode": "relay"}
            assert link._pc is None and not link._rtc_tasks and link.source.read(0) is None
            await ws.send_bytes(packet())
            assert (await ws.receive_json())["accepted"]
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await peer.close()
            await client.close()
    asyncio.run(run())


def test_start_pins_only_after_tunnel_ready_and_closes_resources():
    class Tunnel:
        closed = False
        def open(self, origin):
            assert origin.startswith("http://127.0.0.1:")
            return ORIGIN
        def close(self):
            self.closed = True
    tunnel = Tunnel()
    link = InternetLink(tunnel_factory=lambda: tunnel, ice_servers=[])
    try:
        assert link.start()
        assert link.url == ORIGIN and len(link.issue_code()) == 6
    finally:
        link.stop()
    assert tunnel.closed and not link._running and link.url == "" and not link.source.is_open


def test_bad_rtc_offer_preserves_authenticated_relay_option():
    async def run():
        link = InternetLink(tunnel_factory=lambda: None, ice_servers=[])
        client = await client_for(link)
        try:
            ws, _ = await authenticate(link, client)
            await ws.send_json({"kind": "offer", "sdp": "not an SDP document"})
            assert await ws.receive_json() == {"kind": "error", "reason": "rtc_failed"}
            assert link._pc is None
            await ws.send_json({"kind": "fallback"})
            assert (await ws.receive_json())["mode"] == "relay"
            await ws.send_bytes(packet())
            assert (await ws.receive_json())["accepted"]
        finally:
            await client.close()
    asyncio.run(run())


def test_fixed_gateway_display_does_not_replace_origin_guard_and_expires_closed():
    class Tunnel:
        process_alive = True
        public_url = "https://stable.example.test"
        last_error = ""
        def open(self, origin):
            return ORIGIN
        def close(self):
            self.process_alive = False
            self.public_url = ""
    tunnel = Tunnel()
    link = InternetLink(tunnel_factory=lambda: tunnel, ice_servers=[])
    try:
        link.start()
        assert link.fixed_address and link.url == "https://stable.example.test"
        assert link._public_url == ORIGIN and link._public_host == ORIGIN.removeprefix("https://")
        tunnel.public_url = ""
        assert link.url == ""  # never reveal/fall back to a temporary route after a lease disappears
        tunnel.process_alive = False
        tunnel.last_error = "고정 주소 연결이 만료됐습니다."
        assert link.last_error == tunnel.last_error and link.blocked
    finally:
        link.stop()
    assert not link.fixed_address and link.url == ""


def test_tunnel_death_invalidates_display_and_camera():
    class Tunnel:
        process_alive = True
        def open(self, origin):
            return ORIGIN
        def close(self):
            self.process_alive = False
    tunnel = Tunnel()
    link = InternetLink(tunnel_factory=lambda: tunnel, ice_servers=[])
    try:
        link.start()
        link.source.connect()
        tunnel.process_alive = False
        assert link.last_error and link.url == "" and not link.source.is_open and link.blocked
    finally:
        link.stop()


def test_screen_result_after_camera_change_is_not_delivered():
    async def run():
        started, finish = asyncio.Event(), asyncio.Event()
        class Screen:
            async def request(self, *, profile):
                started.set()
                await finish.wait()
                return b"\xff\xd8private screen\xff\xd9"
            def clear(self):
                pass  # Deliberately emulate a capture that cannot be cancelled.
        link = InternetLink(tunnel_factory=lambda: None, ice_servers=[], screen=Screen())
        client = await client_for(link)
        try:
            ws, ready = await authenticate(link, client)
            await ws.send_json({"kind": "fallback"})
            await ws.receive_json()
            await ws.send_bytes(packet())
            await ws.receive_json()
            assert link.source.read(0) is not None
            response_task = asyncio.create_task(client.post("/screen", headers={
                "Authorization": "Bearer " + ready["screen_token"]}))
            await asyncio.wait_for(started.wait(), 2)
            await ws.send_json({"kind": "camera", "mirrored": False, "rotation": 0, "facing": "environment"})
            await ws.send_json({"kind": "sync", "sent": 1})
            await ws.receive_json()
            finish.set()
            response = await response_task
            assert response.status == 403 and "private screen" not in await response.text()
        finally:
            finish.set()
            await client.close()
    asyncio.run(run())
