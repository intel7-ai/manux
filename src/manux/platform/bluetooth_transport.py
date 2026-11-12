"""Windows RFCOMM sockets and SDP, loaded only on explicit Bluetooth start.

No Python Bluetooth package is required. The listener requires OS authentication
and encryption; application pairing is an additional gate in ``link.bluetooth``.
"""
from __future__ import annotations

import ctypes as c
import sys
import threading
import time
import uuid

SERVICE_UUID = "6f8c5d2a-4e10-4a61-9c23-58f0c17b2e90"
SOCKET = c.c_size_t
INVALID_SOCKET = SOCKET(-1).value


class _Guid(c.Structure):
    _fields_ = [("data1", c.c_uint32), ("data2", c.c_uint16),
                ("data3", c.c_uint16), ("data4", c.c_ubyte * 8)]


class _Address(c.Structure):
    # ws2bth.h explicitly uses pshpack1.h, including on 64-bit Windows.
    _pack_ = 1
    _fields_ = [("family", c.c_uint16), ("address", c.c_uint64),
                ("service", _Guid), ("port", c.c_uint32)]


class _SocketAddress(c.Structure):
    _fields_ = [("address", c.c_void_p), ("length", c.c_int)]


class _CsAddress(c.Structure):
    _fields_ = [("local", _SocketAddress), ("remote", _SocketAddress),
                ("type", c.c_int), ("protocol", c.c_int)]


class _Query(c.Structure):
    _fields_ = [("size", c.c_uint32), ("name", c.c_wchar_p),
                ("service", c.POINTER(_Guid)), ("version", c.c_void_p),
                ("comment", c.c_wchar_p), ("namespace", c.c_uint32),
                ("provider", c.c_void_p), ("context", c.c_wchar_p),
                ("protocol_count", c.c_uint32), ("protocols", c.c_void_p),
                ("query", c.c_wchar_p), ("address_count", c.c_uint32),
                ("addresses", c.POINTER(_CsAddress)), ("flags", c.c_uint32),
                ("blob", c.c_void_p)]


class _FdSet(c.Structure):
    _fields_ = [("count", c.c_uint32), ("sockets", SOCKET * 64)]


class _Timeval(c.Structure):
    _fields_ = [("seconds", c.c_int32), ("microseconds", c.c_int32)]


class _RadioInfo(c.Structure):
    _fields_ = [("size", c.c_uint32), ("address", c.c_uint64),
                ("name", c.c_wchar * 248), ("device_class", c.c_uint32),
                ("subversion", c.c_uint16), ("manufacturer", c.c_uint16)]


def _local_radio_names():
    """Read radio names for transient UI only; never log or persist identifiers."""
    if sys.platform != "win32":
        return []
    bluetooth = c.WinDLL("Bthprops.cpl")
    kernel = c.WinDLL("Kernel32.dll")
    definitions = {
        "BluetoothFindFirstRadio": ([c.POINTER(c.c_uint32), c.POINTER(c.c_void_p)], c.c_void_p),
        "BluetoothFindNextRadio": ([c.c_void_p, c.POINTER(c.c_void_p)], c.c_int),
        "BluetoothFindRadioClose": ([c.c_void_p], c.c_int),
        "BluetoothGetRadioInfo": ([c.c_void_p, c.POINTER(_RadioInfo)], c.c_uint32),
    }
    for name, (args, result) in definitions.items():
        fn = getattr(bluetooth, name)
        fn.argtypes, fn.restype = args, result
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [c.c_void_p], c.c_int
    size, handle = c.c_uint32(4), c.c_void_p()
    finder = bluetooth.BluetoothFindFirstRadio(c.byref(size), c.byref(handle))
    if not finder:
        return []
    radios = []
    try:
        while handle:
            try:
                info = _RadioInfo(size=c.sizeof(_RadioInfo))
                if bluetooth.BluetoothGetRadioInfo(handle, c.byref(info)) == 0:
                    radios.append((int(info.address), info.name.strip()))
                else:
                    # Still count unreadable adapters, so wildcard selection does
                    # not mistake the only readable name for the only radio.
                    radios.append((0, ""))
            finally:
                kernel.CloseHandle(handle)
            if not bluetooth.BluetoothFindNextRadio(finder, c.byref(handle)):
                break
    finally:
        bluetooth.BluetoothFindRadioClose(finder)
    return radios


def _local_radio_name(bound_address):
    try:
        radios = _local_radio_names()
    except (OSError, AttributeError):
        return ""  # The display hint must not prevent an otherwise valid connection.
    for address, name in radios:
        if bound_address and address == bound_address:
            return name
    # Windows can report the wildcard address after bind. Do not guess between
    # several adapters, or display an unrelated adapter after an exact mismatch.
    return radios[0][1] if not bound_address and len(radios) == 1 else ""


class _Winsock:
    def __init__(self):
        if sys.platform != "win32":
            raise OSError("Bluetooth 연결은 Windows PC에서 지원됩니다.")
        self.dll = c.WinDLL("Ws2_32.dll")
        definitions = {
            "WSAStartup": ([c.c_uint16, c.c_void_p], c.c_int),
            "WSACleanup": ([], c.c_int), "WSAGetLastError": ([], c.c_int),
            "socket": ([c.c_int, c.c_int, c.c_int], SOCKET),
            "bind": ([SOCKET, c.c_void_p, c.c_int], c.c_int),
            "getsockname": ([SOCKET, c.c_void_p, c.POINTER(c.c_int)], c.c_int),
            "listen": ([SOCKET, c.c_int], c.c_int),
            "accept": ([SOCKET, c.c_void_p, c.POINTER(c.c_int)], SOCKET),
            "closesocket": ([SOCKET], c.c_int),
            "shutdown": ([SOCKET, c.c_int], c.c_int),
            "ioctlsocket": ([SOCKET, c.c_int32, c.POINTER(c.c_uint32)], c.c_int),
            "setsockopt": ([SOCKET, c.c_int, c.c_int32, c.c_void_p, c.c_int], c.c_int),
            "recv": ([SOCKET, c.c_void_p, c.c_int, c.c_int], c.c_int),
            "send": ([SOCKET, c.c_void_p, c.c_int, c.c_int], c.c_int),
            "select": ([c.c_int, c.POINTER(_FdSet), c.POINTER(_FdSet),
                        c.POINTER(_FdSet), c.POINTER(_Timeval)], c.c_int),
            "WSASetServiceW": ([c.POINTER(_Query), c.c_int, c.c_uint32], c.c_int),
        }
        for name, (args, result) in definitions.items():
            fn = getattr(self.dll, name)
            fn.argtypes, fn.restype = args, result
        # WSADATA is output-only. 512 aligned bytes cover both Windows ABIs.
        data = (c.c_uint64 * 64)()
        result = self.dll.WSAStartup(0x202, c.byref(data))
        if result:
            raise OSError(result, "Windows 소켓을 시작할 수 없습니다.")

    def error(self, operation):
        code = self.dll.WSAGetLastError()
        return OSError(code, f"Bluetooth {operation} 실패 ({code}). PC Bluetooth 전원·어댑터를 확인해 주세요.")

    def check(self, result, operation):
        if result == -1:
            raise self.error(operation)
        return result

    def nonblocking(self, sock):
        enabled = c.c_uint32(1)
        self.check(self.dll.ioctlsocket(sock, c.c_int32(0x8004667E), c.byref(enabled)), "nonblocking")

    def wait(self, sock, seconds, *, write=False):
        fdset = _FdSet()
        fdset.count, fdset.sockets[0] = 1, sock
        timeout = _Timeval(int(seconds), int((seconds % 1) * 1_000_000))
        pointer = c.byref(fdset)
        result = self.dll.select(0, None if write else pointer,
                                 pointer if write else None, None, c.byref(timeout))
        self.check(result, "wait")
        if not result:
            raise TimeoutError("Bluetooth socket timed out")


class _Connection:
    def __init__(self, api, sock, address):
        self._api, self._socket = api, sock
        self._close_lock = threading.Lock()
        digits = f"{address:012X}"
        self.peer = ":".join(digits[i:i + 2] for i in range(0, 12, 2))

    def recv(self, size):
        sock = self._socket
        if sock == INVALID_SOCKET:
            return b""
        self._api.wait(sock, .25)
        buffer = c.create_string_buffer(size)
        length = self._api.dll.recv(sock, buffer, size, 0)
        if length == -1 and self._api.dll.WSAGetLastError() == 10035:
            raise TimeoutError("Bluetooth socket would block")
        self._api.check(length, "receive")
        return buffer.raw[:length]

    def sendall(self, payload):
        deadline = time.monotonic() + 3.0
        offset = 0
        while offset < len(payload):
            sock = self._socket
            if sock == INVALID_SOCKET:
                raise ConnectionError("Bluetooth connection closed")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Bluetooth send timed out")
            self._api.wait(sock, remaining, write=True)
            block = payload[offset:offset + 16384]
            sent = self._api.dll.send(sock, block, len(block), 0)
            if sent == -1 and self._api.dll.WSAGetLastError() == 10035:
                continue
            self._api.check(sent, "send")
            if not sent:
                raise ConnectionError("Bluetooth connection closed")
            offset += sent

    def close(self):
        with self._close_lock:
            sock, self._socket = self._socket, INVALID_SOCKET
            if sock != INVALID_SOCKET:
                self._api.dll.shutdown(sock, 2)
                self._api.dll.closesocket(sock)


class WindowsRfcommListener:
    """One explicitly opened authenticated RFCOMM service, advertised through SDP."""

    def __init__(self, service_uuid=SERVICE_UUID):
        self.service_uuid = service_uuid
        self.channel = 0
        self._local_name = ""
        self._api = None
        self._socket = INVALID_SOCKET
        self._registration = None
        self._close_lock = threading.RLock()

    @property
    def local_name(self):
        """Actual local Bluetooth name, for in-memory PC selection guidance only."""
        return self._local_name

    def open(self):
        with self._close_lock:
            if self._api is not None:
                return self.channel
            self._api = _Winsock()
            api = self._api
            try:
                self._socket = api.dll.socket(32, 1, 3)
                if self._socket == INVALID_SOCKET:
                    raise api.error("socket")
                enabled = c.c_uint32(1)
                for option in (0x80000001, 2):  # authentication, encryption
                    api.check(api.dll.setsockopt(self._socket, 3, c.c_int32(option),
                                                c.byref(enabled), c.sizeof(enabled)), "security")
                address = _Address(family=32, port=0xFFFFFFFF)
                api.check(api.dll.bind(self._socket, c.byref(address), c.sizeof(address)), "bind")
                length = c.c_int(c.sizeof(address))
                api.check(api.dll.getsockname(self._socket, c.byref(address), c.byref(length)), "address")
                api.check(api.dll.listen(self._socket, 1), "listen")
                api.nonblocking(self._socket)
                guid = _Guid.from_buffer_copy(uuid.UUID(self.service_uuid).bytes_le)
                endpoint = _SocketAddress(c.addressof(address), c.sizeof(address))
                endpoints = _CsAddress(endpoint, endpoint, 1, 3)
                query = _Query(size=c.sizeof(_Query), name="MANUX Phone", service=c.pointer(guid),
                               namespace=16, address_count=1, addresses=c.pointer(endpoints))
                api.check(api.dll.WSASetServiceW(c.byref(query), 0, 0), "SDP registration")
                # Keep every nested ctypes pointer alive until unregister.
                self._registration = (query, endpoints, address, guid)
                self.channel = int(address.port)
                self._local_name = _local_radio_name(address.address)
                return self.channel
            except BaseException:
                self.close()
                raise

    def accept(self, timeout=.25):
        api, sock = self._api, self._socket
        if api is None or sock == INVALID_SOCKET:
            raise ConnectionError("Bluetooth listener closed")
        api.wait(sock, timeout)
        address = _Address()
        length = c.c_int(c.sizeof(address))
        accepted = api.dll.accept(sock, c.byref(address), c.byref(length))
        if accepted == INVALID_SOCKET:
            if api.dll.WSAGetLastError() == 10035:
                raise TimeoutError("Bluetooth accept would block")
            raise api.error("accept")
        try:
            api.nonblocking(accepted)
            return _Connection(api, accepted, address.address)
        except BaseException:
            api.dll.closesocket(accepted)
            raise

    def close(self):
        with self._close_lock:
            self._local_name = ""
            api, self._api = self._api, None
            if api is None:
                return
            registration, self._registration = self._registration, None
            if registration is not None:
                api.dll.WSASetServiceW(c.byref(registration[0]), 2, 0)
            sock, self._socket = self._socket, INVALID_SOCKET
            if sock != INVALID_SOCKET:
                api.dll.closesocket(sock)
            self.channel = 0
            api.dll.WSACleanup()
