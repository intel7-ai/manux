"""RFCOMM application protocol over socketpair; synthetic pixels, no radio/input."""
import json
import queue
import socket
import threading
import time

import numpy as np
import pytest

from manux.capture.remote import MAX_PACKET
from manux.link.bluetooth import (ACK, AUTH, ERROR, FRAME, READY, SCREEN, SCREEN_REQUEST,
                                  BluetoothLink, encode_packet, receive_packet)
from manux.link.screen import ScreenRequests


class Connection:
    peer = "00:00:00:00:00:01"  # synthetic identifier only

    def __init__(self, sock):
        self.sock = sock
        sock.settimeout(.05)

    def recv(self, size):
        return self.sock.recv(size)

    def sendall(self, data):
        self.sock.sendall(data)

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class Listener:
    def __init__(self):
        self.connections = queue.Queue()
        self.opened = False

    def open(self):
        self.opened = True

    def accept(self, timeout=.25):
        try:
            return self.connections.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError from None

    def close(self):
        self.opened = False

    def connect(self):
        # The Windows socketpair fallback can wait forever if a sandbox blocks
        # loopback. Keep the failure bounded and explicit for local network tests.
        with socket.socket() as acceptor:
            acceptor.settimeout(2)
            acceptor.bind(("127.0.0.1", 0))
            acceptor.listen(1)
            client = socket.create_connection(acceptor.getsockname(), timeout=2)
            try:
                server, _ = acceptor.accept()
            except BaseException:
                client.close()
                raise
        self.connections.put(Connection(server))
        return Connection(client)


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.005)
    raise AssertionError("condition timed out")


@pytest.fixture
def service():
    listener = Listener()
    link = BluetoothLink(transport_factory=lambda: listener, screen=ScreenRequests())
    link.start()
    yield link, listener
    link.stop()
    assert not listener.opened
    assert not link.source.is_open


def read(client):
    kind, payload = receive_packet(client, threading.Event(), timeout=2)
    return kind, payload if kind == SCREEN else json.loads(payload)


def connect(link, listener, *, code=None):
    code = link.issue_code() if code is None else code
    client = listener.connect()
    client.sendall(encode_packet(AUTH, {"code": code, "device": "synthetic phone"}))
    return client


def frame(seq=0, **changes):
    cv2 = pytest.importorskip("cv2")
    metadata = dict(kind="frame", seq=seq, captured_at=time.time(), width=16, height=12,
                    mirrored=False, rotation=0, facing="user")
    metadata.update(changes)
    encoded = json.dumps(metadata).encode()
    jpeg = cv2.imencode(".jpg", np.zeros((12, 16, 3), np.uint8))[1].tobytes()
    return encode_packet(FRAME, len(encoded).to_bytes(4, "big") + encoded + jpeg)


def test_packet_reader_handles_fragmentation_timeouts_and_coalesced_packets():
    packets = [TimeoutError(), *[bytes([b]) for b in encode_packet(AUTH, {"device": "test"})]]

    class Fragments:
        def recv(self, _):
            chunk = packets.pop(0)
            if isinstance(chunk, Exception):
                raise chunk
            return chunk

    kind, payload = receive_packet(Fragments(), threading.Event())
    assert kind == AUTH and json.loads(payload) == {"device": "test"}
    assert not packets


@pytest.mark.parametrize("size", [0, MAX_PACKET + 1, 0xFFFFFFFF])
def test_oversized_length_rejected_before_payload_read(size):
    class HeaderOnly:
        def recv(self, length):
            assert length == 4
            return size.to_bytes(4, "big")

    with pytest.raises(ValueError):
        receive_packet(HeaderOnly(), threading.Event())


def test_auth_frame_ack_stale_rejection_and_disconnect_clear(service):
    link, listener = service
    client = connect(link, listener)
    try:
        kind, ready = read(client)
        assert kind == READY and abs(ready["server_time"] - time.time()) < 1
        assert link.device["name"] == "synthetic phone"
        client.sendall(frame(1))
        kind, ack = read(client)
        assert kind == ACK and ack["seq"] == 1 and ack["accepted"]
        client.sendall(frame(1))
        assert read(client)[1]["accepted"] is False
        assert link.source.read(0) is not None
        client.sendall(frame(2, captured_at=time.time() - 2))
        assert read(client)[1]["accepted"] is False
        link.disconnect()
        assert link.device is None and link.source.read(0) is None
        assert not link.source.is_open and link.pairing.code is None
    finally:
        client.close()


def test_wrong_code_and_pre_auth_frame_never_open_source(service):
    link, listener = service
    link.issue_code()
    client = connect(link, listener, code="not-a-code")
    assert read(client) == (ERROR, {"error": "authentication_failed"})
    client.close()
    wait_for(lambda: link._connection is None)
    assert link.device is None and not link.source.is_open
    client = listener.connect()
    client.sendall(frame())
    assert read(client) == (ERROR, {"error": "authentication_required"})
    client.close()
    assert not link.source.is_open


def test_wrong_code_followed_by_correct_code_reuses_displayed_code(service):
    link, listener = service
    code = link.issue_code()
    client = connect(link, listener, code="invalid")
    assert read(client)[1]["error"] == "authentication_failed"
    client.close()
    wait_for(lambda: link._connection is None)
    assert link.pairing.code == code
    retry = connect(link, listener, code=code)
    assert read(retry)[0] == READY
    retry.close()


def test_rejected_bluetooth_peer_does_not_stop_listener():
    class PeerRejected(Listener):
        rejected = False

        def accept(self, timeout=.25):
            if not self.rejected:
                self.rejected = True
                raise OSError(10064, "synthetic peer authentication failure")
            return super().accept(timeout)

    listener = PeerRejected()
    link = BluetoothLink(transport_factory=lambda: listener)
    link.start()
    try:
        client = connect(link, listener)
        assert read(client)[0] == READY
        assert link.last_error == ""
        client.close()
    finally:
        link.stop()


def test_code_is_single_use_and_disconnect_reissue_survives_old_handler(service):
    link, listener = service
    code = link.issue_code()
    client = connect(link, listener, code=code)
    assert read(client)[0] == READY
    link.disconnect()
    new_code = link.issue_code()
    client.close()
    # Old handler's finally must not clear the newly issued pairing code.
    second = connect(link, listener, code=new_code)
    assert read(second)[0] == READY
    second.close()
    wait_for(lambda: link.device is None)
    third = connect(link, listener, code=code)
    assert read(third)[0] == ERROR
    third.close()


def test_screen_requires_live_front_frame_and_unavailable_errors_do_not_kill_camera(service):
    link, listener = service
    client = connect(link, listener)
    assert read(client)[0] == READY
    request = encode_packet(SCREEN_REQUEST, {"profile": "compact"})
    client.sendall(request)
    assert read(client) == (ERROR, {"error": "screen_unavailable", "request": "screen"})
    client.sendall(frame(1, facing="environment"))
    assert read(client)[1]["accepted"]
    client.sendall(request)
    assert read(client)[1]["error"] == "screen_unavailable"
    client.sendall(frame(2))
    assert read(client)[1]["accepted"]
    client.sendall(request)
    ticket = wait_for(link.screen.take)
    assert ticket.profile == "compact"
    # Camera frames and ACKs continue while a screen capture is pending.
    client.sendall(frame(3))
    assert read(client)[0] == ACK
    link.screen.complete(ticket, b"synthetic-screen-jpeg")
    assert read(client) == (SCREEN, b"synthetic-screen-jpeg")
    client.close()


def test_rear_switch_cancels_pending_screen_without_exposing_old_pixels(service):
    link, listener = service
    client = connect(link, listener)
    assert read(client)[0] == READY
    client.sendall(frame(1))
    assert read(client)[0] == ACK
    client.sendall(encode_packet(SCREEN_REQUEST, {}))
    ticket = wait_for(link.screen.take)
    client.sendall(frame(2, facing="environment"))
    replies = [read(client), read(client)]
    assert {kind for kind, _ in replies} == {ACK, ERROR}
    link.screen.complete(ticket, b"old-screen-pixels")
    assert not link._front
    client.close()


def test_block_stop_and_restart_release_threads_and_pending_auth(service):
    link, listener = service
    client = connect(link, listener)
    assert read(client)[0] == READY
    link.block()
    assert link.blocked and not link.source.is_open
    with pytest.raises(RuntimeError):
        link.issue_code()
    link.unblock()
    client.close()
    second = connect(link, listener)
    assert read(second)[0] == READY
    thread = link._thread
    link.stop()
    assert not thread.is_alive() and not listener.opened
    second.close()
    link.start()
    pending = listener.connect()  # no auth packet; stop must still be prompt
    wait_for(lambda: link._connection is not None)
    thread = link._thread
    started = time.monotonic()
    link.stop()
    assert time.monotonic() - started < 1
    assert not thread.is_alive()
    pending.close()


def test_start_error_closes_transport_and_preserves_underlying_reason():
    class MissingRadio(Listener):
        def open(self):
            super().open()
            raise OSError("missing radio")

    listener = MissingRadio()
    link = BluetoothLink(transport_factory=lambda: listener)
    with pytest.raises(OSError, match="missing radio"):
        link.start()
    assert not listener.opened and link._thread is None


def test_stop_joins_pending_screen_before_closing_native_adapter(service):
    link, listener = service
    client = connect(link, listener)
    assert read(client)[0] == READY
    client.sendall(frame(1))
    assert read(client)[0] == ACK
    client.sendall(encode_packet(SCREEN_REQUEST, {}))
    ticket = wait_for(link.screen.take)
    screen_thread = link._screen_thread
    close = listener.close

    def checked_close():
        assert not screen_thread.is_alive()
        close()

    listener.close = checked_close
    link.stop()
    assert not screen_thread.is_alive()
    link.screen.complete(ticket, b"invalidated-screen")
    assert not link.source.is_open
    client.close()
