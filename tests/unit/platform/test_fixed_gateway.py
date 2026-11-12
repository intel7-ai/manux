"""Synthetic gateway credentials/endpoints; no public network or application data."""
import base64
import hashlib
import json
import sys
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

import manux.platform.fixed_gateway as module

PUBLIC = "https://synthetic-manux.example.com"
ENDPOINT = "https://synthetic-fixed-test.trycloudflare.com"
ORIGIN = "http://127.0.0.1:49152"
TOKEN = "S" * 43
LEASE = "synthetic-current-lease"


class Quick:
    def __init__(self, events):
        self.events = events
        self.process_alive = False
        self.closed = threading.Event()

    def open(self, origin):
        assert origin == ORIGIN
        self.events.append("quick-open")
        self.process_alive = True
        return ENDPOINT

    def close(self):
        self.events.append("quick-close")
        self.process_alive = False
        self.closed.set()


def make(tmp_path, *, request=None, **kwargs):
    events = []
    quick = Quick(events)
    def exchange(method, url, token, payload, *, timeout):
        assert url == PUBLIC + "/_manux/endpoint" and token == TOKEN
        assert 0 < timeout <= 5
        events.append(method)
        if method == "POST":
            assert payload == {"endpoint": ENDPOINT}
        else:
            assert payload == {"lease": LEASE}
        if request is not None:
            return request(method, payload)
        return (200, {"lease": LEASE, "ttl": 90}) if method == "POST" else (204, None)
    tunnel = module.FixedGatewayTunnel(tmp_path, PUBLIC, token=TOKEN, quick_factory=lambda: quick,
                                       request=exchange, **kwargs)
    return tunnel, quick, events


def eventually(predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("gateway worker did not reach the expected synthetic state")


def test_open_keeps_pinned_endpoint_and_displays_fixed_url_then_revokes_before_close(tmp_path):
    tunnel, quick, events = make(tmp_path)
    assert tunnel.public_url == "" and not tunnel.process_alive
    assert tunnel.open(ORIGIN) == ENDPOINT
    assert tunnel.url == ENDPOINT and tunnel.public_url == PUBLIC and tunnel.last_error == ""
    assert tunnel.open(ORIGIN) == ENDPOINT and events.count("POST") == 1
    tunnel.close()
    assert events == ["quick-open", "POST", "DELETE", "quick-close", "quick-close"]
    assert not tunnel.process_alive and tunnel.public_url == "" and tunnel.url == ""
    tunnel.close()
    assert events.count("DELETE") == 1


def test_heartbeat_renewal_and_worker_shutdown(tmp_path):
    renewed = threading.Event()
    def request(method, payload):
        if method == "PUT":
            renewed.set()
        return (200, {"lease": LEASE, "ttl": 90}) if method == "POST" else (204, None)
    tunnel, quick, events = make(tmp_path, request=request, heartbeat_interval=.01)
    tunnel.open(ORIGIN)
    worker = tunnel._run.worker
    assert renewed.wait(1) and tunnel.process_alive
    tunnel.close()
    assert not worker.is_alive() and quick.closed.is_set()
    assert events.index("DELETE") < events.index("quick-close")


def test_conflicting_lease_fails_immediately_without_registering_again(tmp_path):
    def request(method, payload):
        return (200, {"lease": LEASE, "ttl": 90}) if method == "POST" else (409, None)
    tunnel, quick, events = make(tmp_path, request=request, heartbeat_interval=.01)
    tunnel.open(ORIGIN)
    eventually(lambda: quick.closed.is_set())
    assert not tunnel.process_alive and tunnel.last_error and tunnel.public_url == ""
    assert events.count("POST") == 1
    tunnel.close()


def test_transient_heartbeat_timeout_retains_last_confirmed_lease(tmp_path):
    failed, recovered = threading.Event(), threading.Event()
    puts = []
    def request(method, payload):
        if method == "POST":
            return 200, {"lease": LEASE, "ttl": 90}
        if method == "PUT":
            puts.append(True)
            if len(puts) == 1:
                failed.set()
                raise TimeoutError("synthetic failure")
            recovered.set()
        return 204, None
    tunnel, _, _ = make(tmp_path, request=request, heartbeat_interval=.05)
    try:
        tunnel.open(ORIGIN)
        assert failed.wait(1)
        assert tunnel.process_alive and tunnel.last_error == "" and tunnel.public_url == PUBLIC
        assert recovered.wait(1) and tunnel.process_alive
    finally:
        tunnel.close()


def test_expired_lease_is_not_revived_by_late_success(tmp_path):
    clock = [0.]
    def request(method, payload):
        if method == "POST":
            return 200, {"lease": LEASE, "ttl": 90}
        if method == "PUT":
            clock[0] = 91.
        return 204, None
    tunnel, quick, _ = make(tmp_path, request=request, heartbeat_interval=.01, clock=lambda: clock[0])
    tunnel.open(ORIGIN)
    eventually(lambda: quick.closed.is_set())
    assert not tunnel.process_alive and "만료" in tunnel.last_error
    tunnel.close()


def test_tunnel_death_invalidates_lease_and_stops_worker(tmp_path):
    tunnel, quick, _ = make(tmp_path)
    tunnel.open(ORIGIN)
    worker = tunnel._run.worker
    quick.process_alive = False
    assert not tunnel.process_alive and tunnel.last_error and tunnel.url == ""
    eventually(lambda: not worker.is_alive())
    tunnel.close()


@pytest.mark.parametrize("response", [(403, None), (200, []), (200, {"lease": LEASE, "ttl": 900}),
                                      (200, {"lease": "bad\r\nlease", "ttl": 90})])
def test_failed_registration_closes_only_owned_tunnel_and_redacts_errors(tmp_path, response, capsys):
    tunnel, quick, _ = make(tmp_path, request=lambda *_: response)
    with pytest.raises(RuntimeError) as caught:
        tunnel.open(ORIGIN)
    assert quick.closed.is_set() and not tunnel.process_alive
    assert TOKEN not in str(caught.value) and ENDPOINT not in str(caught.value)
    assert capsys.readouterr() == ("", "")


def test_stop_during_registration_revokes_late_lease_before_final_child_cleanup(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def request(method, payload):
        if method == "POST":
            entered.set()
            assert release.wait(2)
            return 200, {"lease": LEASE, "ttl": 90}
        return 204, None
    tunnel, quick, events = make(tmp_path, request=request)
    failures = []
    def opening():
        try:
            tunnel.open(ORIGIN)
        except RuntimeError:
            failures.append(True)
    opener = threading.Thread(target=opening)
    opener.start()
    assert entered.wait(1)
    tunnel.close()
    release.set()
    opener.join(2)
    assert failures == [True] and not opener.is_alive() and not tunnel.process_alive
    assert events[-2:] == ["DELETE", "quick-close"]


@pytest.mark.parametrize("url", ["http://synthetic.example.com", "https://127.0.0.1",
                                 "https://localhost", "https://a.example/path", "https://a.example?token=x",
                                 "https://user:pass@a.example", "https://a.example:4433", "https://a.example\n"])
def test_invalid_public_origin_never_stores_or_launches(tmp_path, url):
    with pytest.raises(ValueError):
        module.FixedGatewayTunnel(tmp_path, url, token=TOKEN)
    assert list(tmp_path.iterdir()) == []


def test_bootstrap_encrypts_once_returns_only_hash_and_refuses_origin_replacement(tmp_path, monkeypatch):
    monkeypatch.setattr(module.secrets, "token_urlsafe", lambda count: TOKEN if count == 32 else None)
    protects = []
    def protect(value):
        protects.append(value)
        return b"synthetic-protected-blob"
    def unprotect(value):
        assert value == b"synthetic-protected-blob"
        return TOKEN.encode()
    digest = module.bootstrap_gateway(tmp_path, PUBLIC, protect=protect, unprotect=unprotect)
    assert digest == hashlib.sha256(TOKEN.encode()).hexdigest()
    stored = (tmp_path / module.CONFIG_NAME).read_text()
    assert TOKEN not in stored and ENDPOINT not in stored
    assert json.loads(stored) == {"version": 1, "url": PUBLIC,
        "protected_token": base64.b64encode(b"synthetic-protected-blob").decode()}
    config = module.load_gateway_config(tmp_path, unprotect=unprotect)
    assert config.url == PUBLIC and config.token == TOKEN and TOKEN not in repr(config)
    assert module.bootstrap_gateway(tmp_path, PUBLIC, protect=protect, unprotect=unprotect) == digest
    assert len(protects) == 1
    with pytest.raises(ValueError):
        module.bootstrap_gateway(tmp_path, "https://different.example.com", protect=protect, unprotect=unprotect)


def test_invalid_or_undecryptable_config_is_not_silently_replaced(tmp_path):
    path = tmp_path / module.CONFIG_NAME
    path.write_text('{"version":1,"url":"https://a.example","protected_token":"bad base64"}')
    original = path.read_bytes()
    with pytest.raises(ValueError):
        module.load_gateway_config(tmp_path, unprotect=lambda _: TOKEN.encode())
    with pytest.raises(ValueError):
        module.bootstrap_gateway(tmp_path, PUBLIC, protect=lambda _: b"new")
    assert path.read_bytes() == original


@pytest.mark.skipif(sys.platform != "win32", reason="Windows current-user DPAPI")
def test_real_dpapi_roundtrip_uses_only_synthetic_bytes():
    encrypted = module.protect_token(TOKEN.encode())
    assert encrypted != TOKEN.encode() and TOKEN.encode() not in encrypted
    assert module.unprotect_token(encrypted) == TOKEN.encode()


def test_http_boundary_has_bounded_body_timeout_and_no_redirect(monkeypatch):
    observed = []
    class Response:
        status = 200
        def read(self, maximum):
            assert maximum == 4097
            return b'{"lease":"synthetic-current-lease","ttl":90}'
        def __enter__(self): return self
        def __exit__(self, *_): pass
    def build(handler):
        assert handler.redirect_request(None, None, 302, "", {}, "https://evil.example") is None
        def opening(request, *, timeout):
            observed.append((request.get_method(), request.full_url, timeout))
            assert request.get_header("Authorization") == "Bearer " + TOKEN
            assert request.get_header("User-agent") == "MANUX/0.5 (Windows; FixedGateway)"
            return Response()
        return SimpleNamespace(open=opening)
    monkeypatch.setattr(module, "build_opener", build)
    status, body = module.gateway_request("POST", PUBLIC + "/_manux/endpoint", TOKEN, {"endpoint": ENDPOINT})
    assert status == 200 and body["ttl"] == 90
    assert observed == [("POST", PUBLIC + "/_manux/endpoint", 5.)]


def test_redirect_http_error_is_returned_without_reading_or_forwarding_credentials(monkeypatch):
    error = HTTPError(PUBLIC, 302, "synthetic redirect", {}, None)
    def opening(*args, **kwargs):
        raise error
    monkeypatch.setattr(module, "build_opener", lambda *_: SimpleNamespace(open=opening))
    assert module.gateway_request("PUT", PUBLIC + "/_manux/endpoint", TOKEN, {"lease": LEASE}) == (302, None)
