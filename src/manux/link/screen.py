"""One pending desktop preview request. No screenshots are retained or queued."""
from __future__ import annotations

import asyncio
import threading

from manux.core.contracts import ScreenCaptureRequest


class ScreenRequests:
    def __init__(self):
        self._lock = threading.Lock()
        self._pending = None
        self._claimed = False

    async def request(self, timeout=.8, *, profile="detail"):
        loop = asyncio.get_running_loop()
        ticket, future = ScreenCaptureRequest(profile), loop.create_future()
        with self._lock:
            if self._pending is not None:
                raise BlockingIOError("screen capture already pending")
            self._pending = (ticket, loop, future)
            self._claimed = False
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            with self._lock:
                if self._pending is not None and self._pending[0] is ticket:
                    self._pending = None

    def take(self):
        with self._lock:
            if self._pending is None or self._claimed:
                return None
            self._claimed = True
            return self._pending[0]

    @staticmethod
    def _resolve(future, jpeg):
        if not future.done():
            future.set_result(jpeg)

    def complete(self, ticket, jpeg):
        with self._lock:
            pending = self._pending
            if pending is None or pending[0] is not ticket:
                return
            # Invalidation can still cancel this future before delivery.
            _, loop, future = pending
            if not loop.is_closed():
                loop.call_soon_threadsafe(self._resolve, future, jpeg)

    def clear(self):
        with self._lock:
            pending, self._pending = self._pending, None
            if pending is not None and not pending[1].is_closed():
                pending[1].call_soon_threadsafe(pending[2].cancel)
