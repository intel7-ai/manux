"""Explicit internet camera transport behind an injected HTTPS tunnel.

Only a loopback HTTP server is opened here. The public origin is pinned after
tunnel startup. WebRTC is preferred; authenticated JPEG relay remains usable
when direct ICE connectivity is unavailable. No input commands are accepted.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from manux.capture.internet import InternetCamera, MAX_VIDEO_DIMENSION, MAX_VIDEO_PIXELS
from manux.capture.remote import MAX_PACKET
from manux.link.security import RateLimit
from manux.link.server import AUTH_TIMEOUT, PhoneLink, unpack_frame

DEFAULT_ICE = ({"urls": "stun:stun.l.google.com:19302"},)


class InternetLink(PhoneLink):
    def __init__(self, data_dir=None, *, tunnel_factory, source=None, screen=None, ice_servers=None):
        super().__init__(data_dir or ".", host="127.0.0.1", port=0,
                         source=source if source is not None else InternetCamera(), screen=screen)
        self.tunnel_factory = tunnel_factory
        self.ice_servers = list(DEFAULT_ICE if ice_servers is None else ice_servers)
        self._tunnel = None
        self._public_url = ""
        self._public_host = ""
        self.fixed_address = False
        self._pc = None
        self._rtc_tasks = set()
        self._rtc_epoch = 0
        self._source_generation = None
        self._camera_metadata = (True, 0, "user")
        self._session_epoch = 0
        self._disconnecting = False
        self.media_mode = ""
        self.last_error = ""

    @property
    def url(self):
        if self.last_error:
            return ""
        # Display the gateway origin without changing the private listener's
        # pinned Quick Tunnel Host/Origin validation boundary.
        public = getattr(self._tunnel, "public_url", "")
        return public if self.fixed_address else public or self._public_url

    @property
    def last_error(self):
        tunnel = self._tunnel
        if self._public_url and tunnel is not None and not getattr(tunnel, "process_alive", True):
            if not self._last_error:
                self._last_error = getattr(tunnel, "last_error", "") or "인터넷 연결 서버가 종료됐습니다. PC 카메라로 돌아간 뒤 인터넷 연결을 다시 켜세요."
                self.source.disconnect()
                self._blocked = True
                if self._loop is not None and self._loop.is_running():
                    self._loop.call_soon_threadsafe(lambda: asyncio.create_task(self.disconnect_async(block=True)))
        return self._last_error

    @last_error.setter
    def last_error(self, value):
        self._last_error = value

    def _pin_public_url(self, value):
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            raise ValueError("a single public HTTPS origin is required")
        self._public_url = f"https://{parsed.netloc}".rstrip("/")
        self._public_host = parsed.netloc

    def create_app(self):
        from aiohttp import web

        @web.middleware
        async def guard(request, handler):
            peer = request.transport.get_extra_info("peername") if request.transport else None
            try:
                local = bool(peer and ipaddress.ip_address(peer[0]).is_loopback)
            except ValueError:
                local = False
            # Forwarded headers never establish identity or override this origin.
            if (not local or self._blocked or not self._public_url
                    or request.host != self._public_host):
                raise web.HTTPForbidden()
            origin = request.headers.get("Origin")
            if ((origin is not None or request.path in ("/pair", "/ws", "/screen"))
                    and origin != self._public_url):
                raise web.HTTPForbidden()
            limit = self._screen_http_limit if request.path == "/screen" else self._http_limit
            if not limit.allow():
                raise web.HTTPTooManyRequests()
            return await handler(request)

        async def headers(request, response):
            response.headers.update({
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer", "Cross-Origin-Resource-Policy": "same-origin",
                "Permissions-Policy": "camera=(self), microphone=()",
                "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self' blob:; "
                    "img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'",
            })

        async def index(request):
            page = Path(__file__).with_name("static") / "internet.html"
            return web.Response(text=page.read_text(encoding="utf-8"), content_type="text/html")

        async def pair(request):
            if self._ws is not None or self._disconnecting:
                raise web.HTTPConflict()
            try:
                data = await request.json()
                # The tunnel is the network peer. Authentication is the one-use
                # code and token, never a claimed public IP / forwarded header.
                token = self.pairing.pair(data["code"], "127.0.0.1", data["device"])
                return web.json_response({"token": token, "iceServers": self.ice_servers})
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
        app.router.add_post("/screen", self._handle_screen)
        app.on_response_prepare.append(headers)
        app.on_shutdown.append(shutdown)
        return app

    async def disconnect_async(self, *, block=False):
        # No await until every old session result has been invalidated.
        self._disconnecting = True
        self._session_epoch += 1
        if block:
            self._blocked = True
        self.pairing.clear()
        self.source.disconnect()
        self._clear_screen()
        self._device = None
        sockets = set(self._pending)
        if self._ws is not None:
            sockets.add(self._ws)
        self._ws = None
        self.media_mode = ""
        try:
            await self._close_peer()
            await asyncio.gather(*(ws.close(code=1001, message=b"PC disconnected") for ws in sockets),
                                 return_exceptions=True)
        finally:
            self._disconnecting = False

    async def _close_peer(self):
        self._rtc_epoch += 1
        peer, self._pc = self._pc, None
        tasks, self._rtc_tasks = self._rtc_tasks, set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if peer is not None:
            await peer.close()

    async def _handle_ws(self, request):
        from aiohttp import WSMsgType, web

        if self._ws is not None or self._pending or self._disconnecting:
            raise web.HTTPConflict()
        session_epoch = self._session_epoch
        ws = web.WebSocketResponse(max_msg_size=MAX_PACKET, heartbeat=15, compress=False)
        await ws.prepare(request)
        self._pending.add(ws)
        try:
            first = await asyncio.wait_for(ws.receive(), AUTH_TIMEOUT)
            if first.type != WSMsgType.TEXT or len(first.data) > 2048:
                raise ValueError("authentication required")
            auth = json.loads(first.data)
            name = (self.pairing.authenticate(auth.get("token"), "127.0.0.1")
                    if isinstance(auth, dict) and auth.get("kind") == "auth" else None)
            if name is None or self._blocked or session_epoch != self._session_epoch:
                raise ValueError("authentication failed")
            self._pending.discard(ws)
            self._ws, self._device = ws, {"name": name, "peer": "internet"}
            self._source_generation = self.source.connect()
            self._clear_screen()
            self._screen_key = secrets.token_urlsafe(32)
            self.media_mode = ""
            await ws.send_json({"kind": "ready", "server_time": time.time(), "screen_token": self._screen_key})
            control_rate, frames = RateLimit(30, 1), RateLimit(36, 1)
            async for message in ws:
                if self._blocked or self._ws is not ws:
                    break
                if message.type == WSMsgType.BINARY:
                    if self.media_mode != "relay" or not frames.allow():
                        raise ValueError("unexpected camera frame")
                    metadata, jpeg = unpack_frame(message.data)
                    accepted = self.source.submit(metadata, jpeg, generation=self._source_generation)
                    if accepted:
                        self._set_front(metadata.facing == "user")
                    await ws.send_json({"kind": "ack", "seq": metadata.seq,
                                        "accepted": accepted, "server_time": time.time()})
                elif message.type == WSMsgType.TEXT:
                    if not control_rate.allow() or len(message.data) > 128 * 1024:
                        raise ValueError("invalid signaling rate or size")
                    data = json.loads(message.data)
                    if not isinstance(data, dict):
                        raise ValueError("invalid signaling")
                    kind = data.get("kind")
                    if kind == "sync":
                        sent = data.get("sent")
                        if type(sent) not in (float, int) or not math.isfinite(sent):
                            raise ValueError("invalid sync")
                        await ws.send_json({"kind": "sync", "sent": sent, "server": time.time() * 1000})
                    elif kind == "camera":
                        mirror, rotation, facing = data.get("mirrored"), data.get("rotation", 0), data.get("facing")
                        if (type(mirror) is not bool or type(rotation) is not int or rotation != 0
                                or facing not in ("user", "environment")):
                            raise ValueError("invalid camera metadata")
                        self._camera_metadata = mirror, rotation, facing
                        self._set_front(facing == "user")
                    elif kind == "offer":
                        try:
                            await asyncio.wait_for(self._offer(ws, data.get("sdp")), 18)
                        except Exception:
                            # SDP parser / native codec errors are untrusted input
                            # failures, never request logs containing SDP or tokens.
                            if self._ws is ws:
                                await self._close_peer()
                                await ws.send_json({"kind": "error", "reason": "rtc_failed"})
                    elif kind == "fallback":
                        await self._close_peer()
                        if self._ws is not ws:
                            break
                        self._source_generation = self.source.connect()
                        self._set_front(False)
                        self.media_mode = "relay"
                        await ws.send_json({"kind": "mode", "mode": "relay"})
                    else:
                        raise ValueError("unsupported message")
                elif message.type == WSMsgType.ERROR:
                    break
        except (ValueError, TypeError, KeyError, UnicodeError, OSError, asyncio.TimeoutError):
            await ws.close(code=1008, message=b"invalid internet camera message")
        finally:
            self._pending.discard(ws)
            if self._ws is ws:
                self._ws, self._device = None, None
                self.source.disconnect()
                self._clear_screen()
                self.media_mode = ""
                await self._close_peer()
        return ws

    def _set_front(self, value):
        if self._screen_front != value:
            self._screen_front = value
            self._screen_epoch += 1
            if self.screen is not None:
                self.screen.clear()

    async def _notify(self, ws, payload):
        if self._ws is not ws or ws.closed:
            return
        try:
            await ws.send_json(payload)
        except (OSError, RuntimeError):
            pass  # A closing socket cannot receive a late media-state callback.

    async def _handle_screen(self, request):
        from aiohttp import web

        ws, key, epoch = self._ws, self._screen_key, self._screen_epoch
        authorization = request.headers.get("Authorization", "")
        if (ws is None or key is None or not authorization.isascii()
                or not secrets.compare_digest(authorization, "Bearer " + key)):
            raise web.HTTPForbidden()
        if (self.screen is None or not self._screen_front or not self.source.is_open
                or self.source.actual_mode() is None or self.media_mode != "relay"):
            raise web.HTTPConflict()
        profile = request.headers.get("X-Manux-Screen-Profile", "balanced")
        if profile not in ("compact", "balanced", "internet_detail"):
            raise web.HTTPBadRequest()
        if not self._screen_limit.allow():
            raise web.HTTPTooManyRequests()
        try:
            jpeg = await self.screen.request(profile=profile)
        except (BlockingIOError, asyncio.TimeoutError, asyncio.CancelledError):
            raise web.HTTPServiceUnavailable() from None
        if (self._ws is not ws or self._screen_key != key or self._screen_epoch != epoch
                or not self._screen_front or not self.source.is_open or self._blocked):
            raise web.HTTPForbidden()
        if not isinstance(jpeg, bytes) or not 4 <= len(jpeg) <= 512 * 1024:
            raise web.HTTPServiceUnavailable()
        return web.Response(body=jpeg, content_type="image/jpeg")

    async def _offer(self, ws, sdp):
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCRtpSender, RTCSessionDescription

        if (not isinstance(sdp, str) or not 1 <= len(sdp) <= 128 * 1024
                or self._pc is not None or self._ws is not ws):
            raise ValueError("invalid or repeated offer")
        peer = RTCPeerConnection(RTCConfiguration(iceServers=[RTCIceServer(**s) for s in self.ice_servers]))
        self._pc = peer
        self._rtc_epoch += 1
        epoch = self._rtc_epoch
        self._source_generation = self.source.connect()
        generation = self._source_generation
        video_count = 0

        @peer.on("track")
        def track_received(track):
            nonlocal video_count
            if track.kind != "video" or video_count:
                track.stop()
                return
            video_count += 1
            task = asyncio.create_task(self._receive_video(track, peer, epoch, generation))
            self._rtc_tasks.add(task)
            task.add_done_callback(self._rtc_tasks.discard)

        @peer.on("connectionstatechange")
        async def state_changed():
            if self._pc is peer and self._ws is ws:
                if peer.connectionState == "failed":
                    await self._notify(ws, {"kind": "error", "reason": "rtc_failed"})
                elif peer.connectionState == "connected":
                    self.media_mode = "webrtc"
                    await self._notify(ws, {"kind": "mode", "mode": "webrtc"})

        # The phone offers camera first, monitor second. Preferences must be set
        # before the offer is applied; aiortc chooses codecs during that step.
        peer.addTransceiver("video", direction="recvonly")
        monitor = peer.addTransceiver("video", direction="sendonly")
        codecs = RTCRtpSender.getCapabilities("video").codecs
        monitor.setCodecPreferences(sorted(codecs, key=lambda codec: codec.mimeType.lower() != "video/h264"))
        await peer.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
        transceivers = peer.getTransceivers()
        outgoing = [t for t in transceivers if t.kind == "video" and t.receiver.track is None]
        if (video_count != 1 or len(transceivers) != 2 or len(outgoing) != 1
                or any(t.kind != "video" for t in transceivers) or peer.sctp is not None):
            raise ValueError("one outgoing camera and one incoming monitor video track required")
        # addTrack would choose the first unused sender, including the camera's
        # recvonly slot. Bind the monitor to the phone's separate recvonly slot.
        outgoing[0].sender.replaceTrack(self._screen_track(peer, epoch))
        outgoing[0].direction = "sendonly"
        await peer.setLocalDescription(await peer.createAnswer())
        if self._pc is not peer or self._ws is not ws:
            return
        await ws.send_json({"kind": "answer", "sdp": peer.localDescription.sdp})

    async def _receive_video(self, track, peer, epoch, generation):
        from aiortc.mediastreams import MediaStreamError
        last_accepted = time.monotonic()
        try:
            while self._pc is peer and self._rtc_epoch == epoch:
                frame = await track.recv()
                if frame.pts is None or frame.time_base is None:
                    continue
                if not (0 < frame.width <= MAX_VIDEO_DIMENSION and 0 < frame.height <= MAX_VIDEO_DIMENSION
                        and frame.width * frame.height <= MAX_VIDEO_PIXELS):
                    raise ValueError("camera frame too large")
                mirror, rotation, facing = self._camera_metadata
                accepted = self.source.submit_video(frame.to_ndarray(format="bgr24"),
                    media_time=float(frame.pts * frame.time_base), generation=generation,
                    mirrored=mirror, rotation=rotation, facing=facing)
                if accepted:
                    last_accepted = time.monotonic()
                elif time.monotonic() - last_accepted > 2:
                    raise ValueError("camera stream fell behind")
        except (MediaStreamError, ValueError, OSError):
            if self._pc is peer and self._ws is not None:
                await self._notify(self._ws, {"kind": "error", "reason": "rtc_camera_stopped"})

    def _screen_track(self, peer, epoch):
        from fractions import Fraction
        import io
        from aiortc import VideoStreamTrack
        from aiortc.mediastreams import MediaStreamError
        import av

        owner = self

        class MonitorTrack(VideoStreamTrack):
            def __init__(self):
                super().__init__()
                self.started = None
                self.sent_at = 0.

            async def recv(self):
                if owner._pc is not peer or owner._rtc_epoch != epoch:
                    raise MediaStreamError
                now = time.monotonic()
                await asyncio.sleep(max(0., self.sent_at + 1 / 15 - now))
                self.sent_at = time.monotonic()
                capture_epoch = owner._screen_epoch
                frame = None
                if (owner.screen is not None and owner._screen_front and owner.source.is_open
                        and owner.source.actual_mode() is not None):
                    try:
                        # aiortc's H264 encoder advertises level 3.1: keep its
                        # frame within 3600 macroblocks (720p, either orientation).
                        jpeg = await owner.screen.request(profile="internet_hd")
                        if (isinstance(jpeg, bytes) and 4 <= len(jpeg) <= 512 * 1024
                                and owner._screen_epoch == capture_epoch and owner._screen_front
                                and owner._pc is peer and owner.source.is_open):
                            with av.open(io.BytesIO(jpeg), format="mjpeg") as container:
                                frame = next(container.decode(video=0))
                            bound_w, bound_h = (720, 1280) if frame.height > frame.width else (1280, 720)
                            scale = min(1., bound_w / frame.width, bound_h / frame.height)
                            if scale < 1.:
                                frame = frame.reformat(width=max(2, int(frame.width * scale) // 2 * 2),
                                                       height=max(2, int(frame.height * scale) // 2 * 2))
                    except (BlockingIOError, asyncio.TimeoutError, ValueError, OSError, StopIteration,
                            av.error.FFmpegError):
                        pass
                    except asyncio.CancelledError:
                        if owner._pc is not peer or owner._rtc_epoch != epoch:
                            raise
                if owner._pc is not peer or owner._rtc_epoch != epoch:
                    raise MediaStreamError
                if frame is None:
                    frame = av.VideoFrame(width=160, height=90, format="yuv420p")
                    for i, plane in enumerate(frame.planes):
                        plane.update(bytes([16 if i == 0 else 128]) * plane.buffer_size)
                now = time.monotonic()
                if self.started is None:
                    self.started = now
                frame.pts = round((now - self.started) * 90000)
                frame.time_base = Fraction(1, 90000)
                return frame

        return MonitorTrack()

    def start(self):
        if self._running:
            return True
        self._ready.clear()
        self._error = None
        self.last_error = ""
        self._blocked = False

        def run():
            from aiohttp import web
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            runner = web.AppRunner(self.create_app(), access_log=None)
            try:
                loop.run_until_complete(runner.setup())
                site = web.TCPSite(runner, "127.0.0.1", 0)
                loop.run_until_complete(site.start())
                self.port = site._server.sockets[0].getsockname()[1]
                self._running = True
                self._ready.set()
                loop.run_forever()
            except Exception:
                self._error = "internet listener failed"
                self._ready.set()
            finally:
                loop.run_until_complete(runner.cleanup())
                loop.close()
                self._running = False

        self._thread = threading.Thread(target=run, name="internet-link", daemon=True)
        self._thread.start()
        try:
            if not self._ready.wait(5) or self._error:
                raise OSError("internet loopback listener failed")
            self._tunnel = self.tunnel_factory()
            self._pin_public_url(self._tunnel.open(f"http://127.0.0.1:{self.port}"))
            self.fixed_address = bool(getattr(self._tunnel, "public_url", ""))
        except BaseException:
            self.stop()
            raise
        return True

    def stop(self):
        tunnel, self._tunnel = self._tunnel, None
        try:
            if tunnel is not None:
                tunnel.close()
        finally:
            self._public_url = self._public_host = ""
            self.fixed_address = False
            super().stop()
