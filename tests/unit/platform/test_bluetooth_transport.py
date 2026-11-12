"""Exercise native Winsock stream calls without pairing or using a Bluetooth radio."""
import socket
import sys

import pytest

from manux.platform.bluetooth_transport import _Connection, _Winsock
from manux.platform import bluetooth_transport


def test_local_name_selects_bound_adapter_without_guessing(monkeypatch):
    monkeypatch.setattr(bluetooth_transport, "_local_radio_names",
                        lambda: [(1, "Synthetic PC A"), (2, "Synthetic PC B")])
    assert bluetooth_transport._local_radio_name(2) == "Synthetic PC B"
    assert bluetooth_transport._local_radio_name(0) == ""
    assert bluetooth_transport._local_radio_name(3) == ""
    monkeypatch.setattr(bluetooth_transport, "_local_radio_names", lambda: [(1, "Synthetic PC")])
    assert bluetooth_transport._local_radio_name(0) == "Synthetic PC"
    assert bluetooth_transport._local_radio_name(2) == ""
    monkeypatch.setattr(bluetooth_transport, "_local_radio_names", lambda: [(1, "Synthetic PC"), (0, "")])
    assert bluetooth_transport._local_radio_name(0) == ""


def test_local_name_failure_is_optional_and_close_clears_display(monkeypatch):
    def unavailable():
        raise OSError("synthetic radio unavailable")

    monkeypatch.setattr(bluetooth_transport, "_local_radio_names", unavailable)
    assert bluetooth_transport._local_radio_name(0) == ""
    listener = bluetooth_transport.WindowsRfcommListener()
    assert listener.local_name == ""
    listener._local_name = "Synthetic PC"
    listener.close()
    assert listener.local_name == ""


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows Winsock adapter")
def test_native_stream_send_receive_timeout_and_idempotent_close():
    # RFCOMM and TCP use the same Winsock stream ABI. The native socket, buffer,
    # select and close code runs here; SDP/radio activation is a separate smoke.
    api = _Winsock()
    connection = None
    try:
        with socket.socket() as listener:
            listener.settimeout(2)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            with socket.create_connection(listener.getsockname(), timeout=2) as client:
                accepted, _ = listener.accept()
                handle = accepted.detach()
                connection = _Connection(api, handle, 1)
                api.nonblocking(handle)
                client.sendall(b"synthetic-camera-packet")
                assert connection.recv(1024) == b"synthetic-camera-packet"
                connection.sendall(b"synthetic-ack-packet")
                assert client.recv(1024) == b"synthetic-ack-packet"
                with pytest.raises(TimeoutError):
                    connection.recv(1)
                connection.close()
                connection.close()
                assert connection.recv(1) == b""
                assert client.recv(1) == b""
    finally:
        if connection:
            connection.close()
        api.dll.WSACleanup()
