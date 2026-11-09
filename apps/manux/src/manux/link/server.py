"""Opt-in HTTPS camera bridge, port of Dan (한순중) phone_web_bridge.py @9da1f634e8c0afd9d3d6b32a56c83cddfb23d5f8.

Retains latest-only JPEG reception. Adds pairing/private-network controls; remote commands removed.
Optional aiohttp/cryptography imports happen only at create_app/start.
"""
from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from manux.capture.remote import MAX_PACKET, RemoteCamera
from manux.core.contracts import RemoteFrameMetadata
from manux.link.certificates import server_context
from manux.link.security import Pairing, RateLimit, private_address

AUTH_TIMEOUT = 5.0


def unpack_frame(packet):
    if not 4 < len(packet) <= MAX_PACKET:
        raise ValueError("invalid packet size")
    length = int.from_bytes(packet[:4], "big")
    if not 0 < length <= 1024 or 4 + length >= len(packet):
        raise ValueError("invalid metadata size")
    meta = json.loads(packet[4:4 + length])
    if not isinstance(meta, dict) or meta.pop("kind", None) != "frame":
        raise ValueError("unsupported message kind (audio reserved)")
    return RemoteFrameMetadata(**meta), packet[4 + length:]


class PhoneLink:
    def __init__(self, data_dir, *, host, port=8443, source=None):
        if not private_address(host):
            raise ValueError("bind a private IP, not a public or wildcard address")
        self.data_dir, self.host, self.port = Path(data_dir), host, port
        self.source = source if source is not None else RemoteCamera()
        self.pairing = Pairing()
        self._http_limit = RateLimit(120, 60)
        self._blocked = False
        self._ws = None
        self._pending = set()
        self._device = None
        self._loop = None
        self._thread = None
        self._ready = threading.Event()
        self._error = None
        self._running = False

    @property
    def url(self):
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"https://{host}:{self.port}"

    @property
    def device(self):
        device = self._device
        return dict(device) if device else None

    @property
    def blocked(self):
        return self._blocked

    def issue_code(self):
        if self._loop and self._loop.is_running():
            async def issue():
                return self._issue_code()
            return asyncio.run_coroutine_threadsafe(issue(), self._loop).result(timeout=3)
        return self._issue_code()

    def _issue_code(self):
        if self._blocked or self._ws is not None:
            raise RuntimeError("blocked or a phone is already connected")
        return self.pairing.issue()

    async def disconnect_async(self, *, block=False):
        if block:
            self._blocked = True
        self.pairing.clear()
        self.source.disconnect()  # invalidate consumer results before awaiting network close
        self._device = None
        sockets = set(self._pending)
        if self._ws is not None:
            sockets.add(self._ws)
        self._ws = None
        for ws in sockets:
            await ws.close(code=1001, message=b"PC disconnected")

    def disconnect(self):
        self._control(False)

    def block(self):
        self._control(True)

    def _control(self, block):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.disconnect_async(block=block), self._loop).result(timeout=5)
        else:
            self._blocked = self._blocked or block
            self.pairing.clear()
            self.source.disconnect()
            self._device = None

    def unblock(self):
        if self._loop and self._loop.is_running():
            async def clear():
                self._blocked = False
            asyncio.run_coroutine_threadsafe(clear(), self._loop).result(timeout=3)
        else:
            self._blocked = False

    def create_app(self):
        from aiohttp import web

        @web.middleware
        async def guard(request, handler):
            peer = request.transport.get_extra_info("peername") if request.transport else None
            if self._blocked or not peer or not private_address(peer[0]):
                raise web.HTTPForbidden()
            try:
                host = urlsplit("https://" + request.host).hostname
            except ValueError:
                raise web.HTTPForbidden() from None
            if not request.secure or host != self.host:
                raise web.HTTPForbidden()
            if not self._http_limit.allow():
                raise web.HTTPTooManyRequests()
            origin = request.headers.get("Origin")
            if (origin is not None or request.path in ("/pair", "/ws")) and origin != f"https://{request.host}":
                raise web.HTTPForbidden()
            return await handler(request)

        async def headers(request, response):
            response.headers.update({
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer", "Cross-Origin-Resource-Policy": "same-origin",
                "Permissions-Policy": "camera=(self), microphone=()",
                "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                           "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                                           "media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'",
            })

        async def index(request):
            return web.Response(text=Path(__file__).with_name("static").joinpath("phone.html").read_text(encoding="utf-8"),
                                content_type="text/html")

        async def pair(request):
            if self._ws is not None:
                raise web.HTTPConflict()
            try:
                data = await request.json()
                token = self.pairing.pair(data["code"], request.remote, data["device"])
                return web.json_response({"token": token})
            except PermissionError:
                raise web.HTTPTooManyRequests() from None
            except (ValueError, KeyError, TypeError):
                raise web.HTTPForbidden() from None

        async def shutdown(app):
            await self.disconnect_async(block=True)

        app = web.Application(middlewares=[guard], client_max_size=2048)
        app.router.add_get("/", index)
        app.router.add_post("/pair", pair)
        app.router.add_get("/ws", self._handle_ws)
        app.on_response_prepare.append(headers)
        app.on_shutdown.append(shutdown)
        return app

    async def _handle_ws(self, request):
        from aiohttp import WSMsgType, web

        if self._ws is not None or self._pending:
            raise web.HTTPConflict()
        ws = web.WebSocketResponse(max_msg_size=MAX_PACKET, heartbeat=2, compress=False, receive_timeout=5)
        await ws.prepare(request)
        self._pending.add(ws)
        try:
            first = await asyncio.wait_for(ws.receive(), AUTH_TIMEOUT)
            if first.type != WSMsgType.TEXT:
                raise ValueError("authentication required")
            auth = json.loads(first.data)
            if not isinstance(auth, dict) or auth.get("kind") != "auth":
                raise ValueError("authentication required")
            device = self.pairing.authenticate(auth.get("token"), request.remote)
            if device is None or self._blocked:
                raise ValueError("invalid access token")
            self._pending.discard(ws)
            self._ws = ws
            self._device = {"name": device, "peer": request.remote}
            self.source.connect()
            await ws.send_json({"kind": "ready"})
            rate = RateLimit(36, 1)
            async for msg in ws:
                if self._blocked or self._ws is not ws:
                    break
                if not rate.allow():
                    raise ValueError("message rate exceeded")
                if msg.type == WSMsgType.BINARY:
                    meta, jpeg = unpack_frame(msg.data)
                    accepted = self.source.submit(meta, jpeg)
                    await ws.send_json({"kind": "ack", "seq": meta.seq, "accepted": accepted})
                elif msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    sent = data.get("sent") if isinstance(data, dict) else None
                    if (not isinstance(data, dict) or data.get("kind") != "sync"
                            or type(sent) not in (int, float) or not math.isfinite(sent)):
                        raise ValueError("unsupported message (audio reserved)")
                    await ws.send_json({"kind": "sync", "sent": sent, "server": time.time() * 1000})
                elif msg.type == WSMsgType.ERROR:
                    break
        except (ValueError, TypeError, KeyError, UnicodeError, asyncio.TimeoutError):
            if self._ws is ws:
                self._ws = None
                self._device = None
                self.source.disconnect()
            await ws.close(code=1008, message=b"invalid phone link message")
        finally:
            self._pending.discard(ws)
            if self._ws is ws:
                self._ws = None
                self._device = None
                self.source.disconnect()
        return ws

    def start(self):
        """Explicitly open HTTPS; constructor/imports never listen or create certificates."""
        if self._running:
            return True
        self._ready.clear()
        self._error = None
        context = server_context(self.data_dir, self.host)

        def run():
            from aiohttp import web

            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            runner = web.AppRunner(self.create_app(), access_log=None)
            try:
                loop.run_until_complete(runner.setup())
                site = web.TCPSite(runner, self.host, self.port, ssl_context=context)
                loop.run_until_complete(site.start())
                self.port = site._server.sockets[0].getsockname()[1]
                self._running = True
                self._ready.set()
                loop.run_forever()
            except Exception as error:
                self._error = error
                self._ready.set()
            finally:
                loop.run_until_complete(runner.cleanup())
                loop.close()
                self._running = False

        self._thread = threading.Thread(target=run, name="phone-link", daemon=True)
        self._thread.start()
        if not self._ready.wait(5) or self._error is not None:
            self.stop()
            raise OSError("phone HTTPS listener failed") from self._error
        return True

    def stop(self):
        self.block()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise OSError("phone listener did not stop")
            self._thread = None
