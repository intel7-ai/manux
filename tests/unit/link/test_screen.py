"""Synthetic screenshots through real local TLS; no desktop pixels or phone required."""
import asyncio

import pytest

from manux.link.screen import ScreenRequests
from manux.link.server import PhoneLink
from manux.link.security import RateLimit
from test_link import authenticate, client_for, packet


async def take_request(screen):
    for _ in range(100):
        ticket = screen.take()
        if ticket is not None:
            return ticket
        await asyncio.sleep(.005)
    raise AssertionError("capture was not requested")


def test_screen_profile_validation_does_not_leave_a_pending_request():
    async def run():
        screen = ScreenRequests()
        with pytest.raises(ValueError):
            await screen.request(profile="unbounded")
        assert screen.take() is None
        pending = asyncio.create_task(screen.request(profile="compact"))
        ticket = await take_request(screen)
        assert ticket.profile == "compact"
        screen.complete(ticket, b"synthetic JPEG")
        assert await pending == b"synthetic JPEG"
    asyncio.run(run())


def test_screen_accepts_twenty_fps_and_only_bounded_profiles(tmp_path):
    async def run():
        screen = ScreenRequests()
        link = PhoneLink(tmp_path, host="127.0.0.1", screen=screen)
        client, origin = await client_for(link)
        try:
            ws, _ = await authenticate(client, origin, link.issue_code())
            headers = {"Origin": origin, "Authorization": "Bearer " + link._screen_key}
            await ws.send_bytes(packet(0, facing="user", mirrored=True))
            assert (await ws.receive_json())["accepted"]
            assert (await client.post("/screen", headers={**headers, "X-Manux-Screen-Profile": "unbounded"})).status == 400
            assert screen.take() is None
            started = asyncio.get_running_loop().time()
            for index in range(20):
                profile = ("balanced", "compact", "detail")[index % 3]
                request = asyncio.create_task(client.post("/screen", headers={**headers, "X-Manux-Screen-Profile": profile}))
                ticket = await take_request(screen)
                assert ticket.profile == profile
                screen.complete(ticket, b"synthetic JPEG")
                response = await request
                assert response.status == 200
                await response.read()
                await asyncio.sleep(max(0, started + (index+1)*.05 - asyncio.get_running_loop().time()))
            await ws.close()
        finally:
            await client.close()
    asyncio.run(run())


def test_single_screen_request_timeout_invalidation_and_late_results():
    async def run():
        screen = ScreenRequests()
        assert screen.take() is None
        old = asyncio.create_task(screen.request())
        ticket = await take_request(screen)
        assert screen.take() is None
        with pytest.raises(BlockingIOError):
            await screen.request()
        screen.clear()
        with pytest.raises(asyncio.CancelledError):
            await old
        new = asyncio.create_task(screen.request())
        next_ticket = await take_request(screen)
        screen.complete(ticket, b"old screenshot")
        await asyncio.sleep(0)
        assert not new.done()
        screen.complete(next_ticket, b"new screenshot")
        assert await new == b"new screenshot"
        with pytest.raises(asyncio.TimeoutError):
            await screen.request(timeout=.01)
        assert screen.take() is None
    asyncio.run(run())


def test_paired_front_only_screen_without_blocking_camera_and_no_replay(tmp_path):
    async def run():
        screen = ScreenRequests()
        link = PhoneLink(tmp_path, host="127.0.0.1", screen=screen)
        client, origin = await client_for(link)
        try:
            assert (await client.post("/screen", headers={"Origin": origin})).status == 403
            ws, _ = await authenticate(client, origin, link.issue_code())
            key = link._screen_key
            headers = {"Origin": origin, "Authorization": "Bearer " + key}
            assert (await client.post("/screen", headers=headers)).status == 409  # rear/default
            assert (await client.post("/screen", headers={**headers, "Origin": "https://evil.example"})).status == 403
            assert (await client.post("/screen", headers={**headers, "Authorization": "Bearer wrong"})).status == 403
            await ws.send_bytes(packet(0, facing="user", mirrored=True))
            assert (await ws.receive_json())["accepted"]
            # A screen request waits for Qt; camera ACK and sync continue independently.
            pending = asyncio.create_task(client.post("/screen", headers=headers))
            ticket = await take_request(screen)
            assert (await client.post("/screen", headers=headers)).status == 429
            await ws.send_bytes(packet(1, facing="user", mirrored=True))
            assert (await asyncio.wait_for(ws.receive_json(), .5))["accepted"]
            await ws.send_json({"kind": "sync", "sent": 123})
            assert (await asyncio.wait_for(ws.receive_json(), .5))["kind"] == "sync"
            data = packet(2)
            jpeg = data[4 + int.from_bytes(data[:4], "big"):]
            screen.complete(ticket, jpeg)
            response = await pending
            assert response.status == 200 and await response.read() == jpeg
            assert response.headers["Cache-Control"] == "no-store"
            assert response.content_type == "image/jpeg"
            # Screen traffic has a separate budget from page/pair HTTP requests.
            link._http_limit = RateLimit(1, 60)
            assert (await client.get("/")).status == 200
            assert (await client.get("/")).status == 429
            pending = asyncio.create_task(client.post("/screen", headers=headers))
            ticket = await take_request(screen)
            await ws.send_bytes(packet(2, facing="environment", mirrored=False))
            assert (await ws.receive_json())["accepted"]
            assert (await pending).status == 503
            screen.complete(ticket, jpeg)  # stale capture cannot be delivered after the switch
            assert (await client.post("/screen", headers=headers)).status == 409
            link._http_limit = RateLimit(120, 60)
            await link.disconnect_async()
            await ws.close()
            assert (await client.post("/screen", headers=headers)).status == 403
            new_ws, _ = await authenticate(client, origin, link.issue_code())
            assert link._screen_key != key
            assert (await client.post("/screen", headers=headers)).status == 403
            await new_ws.close()
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["capture_error", "too_large", "disconnect", "rear_front", "wrong_peer"])
def test_screen_failure_and_session_changes_never_publish_old_pixels(tmp_path, failure):
    async def run():
        screen = ScreenRequests()
        link = PhoneLink(tmp_path, host="127.0.0.1", screen=screen)
        client, origin = await client_for(link)
        try:
            ws, _ = await authenticate(client, origin, link.issue_code())
            headers = {"Origin": origin, "Authorization": "Bearer " + link._screen_key}
            await ws.send_bytes(packet(0, facing="user", mirrored=True))
            assert (await ws.receive_json())["accepted"]
            if failure == "wrong_peer":
                link._device["peer"] = "127.0.0.2"
                assert (await client.post("/screen", headers=headers)).status == 403
                assert screen.take() is None
                return
            pending = asyncio.create_task(client.post("/screen", headers=headers))
            ticket = await take_request(screen)
            if failure == "disconnect":
                await link.disconnect_async()
            elif failure == "rear_front":
                for seq, facing in ((1, "environment"), (2, "user")):
                    await ws.send_bytes(packet(seq, facing=facing, mirrored=facing == "user"))
                    assert (await ws.receive_json())["accepted"]
                screen.complete(ticket, b"old pixels")
            else:
                screen.complete(ticket, None if failure == "capture_error" else b"x" * (512 * 1024 + 1))
            assert (await pending).status != 200
            if failure != "disconnect":
                await ws.send_json({"kind": "sync", "sent": 1})
                assert (await ws.receive_json())["kind"] == "sync"
            await ws.close()
        finally:
            await client.close()
    asyncio.run(run())
