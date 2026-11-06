"""Authentication added to Dan's @9da1f63 LAN camera bridge (contracts section 14)."""
from __future__ import annotations

import hmac
import ipaddress
import secrets
import time
from collections import deque

NETWORKS = tuple(map(ipaddress.ip_network, (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "fc00::/7", "::1/128")))


def private_address(address):
    try:
        ip = ipaddress.ip_address(address)
        ip = getattr(ip, "ipv4_mapped", None) or ip
        return any(ip.version == net.version and ip in net for net in NETWORKS)
    except ValueError:
        return False


class RateLimit:
    def __init__(self, count, seconds, clock=time.monotonic):
        self.count, self.seconds, self.clock = count, seconds, clock
        self._hits = deque()

    def allow(self):
        now = self.clock()
        while self._hits and self._hits[0] <= now - self.seconds:
            self._hits.popleft()
        if len(self._hits) >= self.count:
            return False
        self._hits.append(now)
        return True


class Pairing:
    """Single pending code/token, never persisted. Called only on the server loop."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.attempts = RateLimit(5, 60, clock)
        self.clear()

    def clear(self):
        self.code, self.token, self.peer, self.device = None, None, None, None
        self.expires = 0.0

    def issue(self):
        self.clear()
        self.code = f"{secrets.randbelow(1_000_000):06d}"
        self.expires = self.clock() + 120
        return self.code

    def pair(self, code, peer, device):
        if not self.attempts.allow():
            raise PermissionError("pairing rate limited")
        if (not private_address(peer) or not isinstance(code, str) or not code.isascii()
                or len(code) != 6 or self.code is None or self.clock() >= self.expires
                or not hmac.compare_digest(code, self.code)):
            raise ValueError("invalid or expired pairing code")
        if not isinstance(device, str) or not 1 <= len(device) <= 64 or any(ord(c) < 32 for c in device):
            raise ValueError("invalid device name")
        self.code = None
        self.token, self.peer, self.device = secrets.token_urlsafe(32), peer, device
        self.expires = self.clock() + 120
        return self.token

    def authenticate(self, token, peer):
        if (not isinstance(token, str) or not token.isascii() or self.token is None
                or self.clock() >= self.expires or peer != self.peer
                or not hmac.compare_digest(token, self.token)):
            return None
        device = self.device
        self.clear()
        return device
