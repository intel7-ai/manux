"""Desktop-owned phone link lifecycle; no Qt, gesture decisions or Windows input."""
from __future__ import annotations

import ipaddress
import socket
import time

from manux.link.security import private_address
from manux.link.server import PhoneLink


def lan_address():
    """Ask the routing table for a private LAN interface without sending a packet."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("192.168.255.254", 9))
        address = sock.getsockname()[0]
    if not private_address(address) or ipaddress.ip_address(address).is_loopback:
        raise OSError("PC의 Wi-Fi/LAN 주소를 찾지 못했습니다. 연결 주소를 직접 입력하세요.")
    return address


class PhoneSession:
    def __init__(self, data_dir, *, factory=PhoneLink, bluetooth_factory=None, internet_factory=None,
                 clock=time.monotonic, screen=None):
        self.data_dir, self.factory, self.clock = data_dir, factory, clock
        self.bluetooth_factory = bluetooth_factory
        self.internet_factory = internet_factory
        self.link = None
        self.transport = ""
        self.screen = screen
        self.code, self.expires, self.note = "", 0., ""

    def _issue(self):
        self.code = self.link.issue_code()
        self.expires = self.clock() + 120

    def command(self, action, host=""):
        """Only the runtime thread calls this; construction leaves networking off."""
        transport = action if action in ("bluetooth", "internet") else "wifi"
        if action in ("start", "bluetooth", "internet") and (self.link is None or self.transport != transport):
            if transport in ("bluetooth", "internet"):
                factory = self.bluetooth_factory if transport == "bluetooth" else self.internet_factory
                if factory is None:
                    raise OSError("이 환경에서는 요청한 휴대폰 연결을 사용할 수 없습니다.")
                candidate = factory(self.data_dir)
            else:
                candidate = self.factory(self.data_dir, host=host.strip() or lan_address())
            if self.screen is not None:
                candidate.screen = self.screen
            try:
                candidate.start()
                code = candidate.issue_code()
            except Exception:
                candidate.stop()
                raise
            previous = self.link
            self.link, self.code, self.expires = candidate, code, self.clock() + 120
            self.transport = transport
            if previous is not None:
                previous.stop()
        elif action == "local":
            self.close()
        elif self.link is None:
            raise ValueError("먼저 휴대폰 연결을 켜세요.")
        elif action in ("start", "bluetooth", "internet", "pair"):
            self._issue()
        elif action == "disconnect":
            self.link.disconnect()
            self._issue()
        elif action == "block":
            self.link.block()
            self.code, self.expires = "", 0.
        elif action == "unblock":
            self.link.unblock()
            self._issue()
        else:
            raise ValueError("지원하지 않는 휴대폰 연결 요청입니다.")
        self.note = ""

    def view(self):
        link = self.link
        device = link.device if link else None
        seconds = max(0, int(self.expires - self.clock()))
        link_error = getattr(link, "last_error", "") if link else ""
        pending_code = getattr(getattr(link, "pairing", None), "code", self.code)
        if link_error or pending_code != self.code:
            seconds = 0
        connected = bool(device and link.source.is_open)
        if link is None:
            status = "PC 카메라 사용 중 · 휴대폰 연결 꺼짐"
        elif link.blocked:
            status = "휴대폰 연결 차단됨"
        elif connected:
            status = "휴대폰 연결됨 · 영상을 기다리는 중" if not link.source.actual_mode() else "휴대폰 영상 수신 중"
        elif device:
            status = "휴대폰 영상이 멈췄습니다. 폰의 " + ("MANUX 앱" if self.transport in ("bluetooth", "internet") else "브라우저") + " 화면을 확인하세요."
        else:
            status = "휴대폰 연결 대기" if seconds else "새 연결 코드가 필요합니다. 새 코드를 받으세요."
        return dict(available=True, bluetooth_available=self.bluetooth_factory is not None,
                    internet_available=self.internet_factory is not None,
                    fixed_address=bool(self.transport == "internet" and getattr(link, "fixed_address", False)),
                    transport=self.transport, enabled=link is not None, connected=connected,
                    media_mode=getattr(link, "media_mode", ""),
                    local_name=getattr(link, "local_name", "") if self.transport == "bluetooth" else "",
                    blocked=bool(link and link.blocked), url=link.url if link else "",
                    code=self.code if link and not device and not link.blocked and seconds else "",
                    seconds=seconds if not device else 0, device=device.get("name", "") if device else "",
                    status=self.note or link_error or status)

    def close(self):
        if self.link is not None:
            self.link.stop()
            self.link = None
        self.code, self.expires = "", 0.
        self.transport = ""
