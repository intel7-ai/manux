"""Synthetic child-process streams; no Cloudflare account, network or public port."""
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

import pytest

from manux.platform.quick_tunnel import CloudflareQuickTunnel, QuickTunnel, _loopback_origin


PUBLIC = "https://synthetic-test-only.trycloudflare.com"
ORIGIN = "http://127.0.0.1:49152"


class Stream:
    def __init__(self):
        self.chunks = queue.Queue()
        self.remainder = b""
        self.closed = False
        self.read_limits = []

    def feed(self, value):
        self.chunks.put(value)

    def readline(self, size):
        self.read_limits.append(size)
        data = self.remainder or self.chunks.get(timeout=3)
        self.remainder = b""
        if data is None:
            return b""
        newline = data.find(b"\n")
        length = min(size, newline + 1 if newline >= 0 else len(data))
        self.remainder = data[length:]
        return data[:length]

    def close(self):
        self.closed = True


class Process:
    def __init__(self, *, ready=True, terminate_works=True):
        self.stderr = Stream()
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.terminate_works = terminate_works
        if ready:
            self.stderr.feed(f"INF | {PUBLIC} |\n".encode())
            self.stderr.feed(b"INF Registered tunnel connection connIndex=0 protocol=http2\n")

    def poll(self):
        return self.returncode

    def finish(self, code):
        self.returncode = code
        self.stderr.feed(None)

    def terminate(self):
        self.terminate_calls += 1
        if self.terminate_works:
            self.finish(0)

    def kill(self):
        self.kill_calls += 1
        self.finish(-9)

    def wait(self, timeout):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("synthetic-child", timeout)
        return self.returncode


def tunnel(tmp_path, process=None, **kwargs):
    path = tmp_path / "synthetic-cloudflared.exe"
    path.write_bytes(b"synthetic executable placeholder")
    process = process or Process()
    launch = []

    def popen(arguments, **options):
        launch.append((arguments, options))
        return process

    return CloudflareQuickTunnel(path, popen=popen, **kwargs), process, launch


def eventually(predicate):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("synthetic worker did not complete")


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:49152", "http://localhost:49152", "http://0.0.0.0:49152",
    "http://192.168.1.1:49152", "http://[::1]:49152", "http://127.0.0.1",
    "http://127.0.0.1:0", "http://127.0.0.1:65536", "http://127.0.0.1:49152/path",
    "http://127.0.0.1:49152?token=synthetic", "http://127.0.0.1:49152#fragment",
    "http://user@127.0.0.1:49152", " http://127.0.0.1:49152", "http://127.0.0.1:49152\n", None,
])
def test_invalid_origin_never_launches_a_child(tmp_path, url):
    bridge, process, launch = tunnel(tmp_path)
    with pytest.raises(ValueError):
        bridge.open(url)
    assert launch == [] and process.terminate_calls == 0


def test_launch_is_hidden_direct_and_isolated_from_unrelated_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("TUNNEL_TOKEN", "synthetic-token-not-for-child")
    monkeypatch.setenv("HF_TOKEN", "synthetic-unrelated-token")
    bridge, process, launch = tunnel(tmp_path)
    assert QuickTunnel is CloudflareQuickTunnel
    try:
        assert bridge.open(ORIGIN + "/") == PUBLIC
        assert bridge.url == PUBLIC and bridge.process_alive and bridge.last_error == ""
        args, options = launch[0]
        assert args[1] == "tunnel" and args[args.index("--url") + 1] == ORIGIN
        assert "--no-autoupdate" in args and args[args.index("--protocol") + 1] == "http2"
        config = Path(args[args.index("--config") + 1])
        assert config.read_text() == "{}\n"
        assert options["shell"] is False and options["stdout"] == subprocess.DEVNULL
        assert options["stderr"] == subprocess.PIPE
        assert "TUNNEL_TOKEN" not in options["env"] and "HF_TOKEN" not in options["env"]
        if sys.platform == "win32":
            assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert bridge.open(ORIGIN) == PUBLIC and len(launch) == 1
        with pytest.raises(RuntimeError):
            bridge.open("http://127.0.0.1:49153")
    finally:
        reader = bridge._run.reader
        bridge.stop()
        bridge.close()
    assert not bridge.process_alive and bridge.url == ""
    assert process.terminate_calls == 1 and process.stderr.closed and not reader.is_alive()
    assert not config.exists()


def test_url_alone_waits_for_registered_edge_and_drain_keeps_running(tmp_path):
    process = Process(ready=False)
    bridge, _, _ = tunnel(tmp_path, process, ready_timeout=1)
    results = []
    starter = threading.Thread(target=lambda: results.append(bridge.open(ORIGIN)))
    starter.start()
    eventually(lambda: bridge._run is not None)
    process.stderr.feed(f"| {PUBLIC} |\n".encode())
    eventually(lambda: bridge._run.candidate == PUBLIC)
    assert results == [] and starter.is_alive()
    process.stderr.feed(b"INF Registered tunnel connection protocol=http2\n")
    starter.join(1)
    assert results == [PUBLIC]
    process.stderr.feed(b"synthetic secret should never be retained " * 500 + b"\n")
    eventually(lambda: len(process.stderr.read_limits) > 6)
    assert max(process.stderr.read_limits) == 4096 and bridge.last_error == ""
    bridge.close()


@pytest.mark.parametrize("bad", [
    "http://synthetic-test-only.trycloudflare.com", "https://trycloudflare.com",
    "https://a.b.trycloudflare.com", "https://a.trycloudflare.com.evil.example",
    "https://a.trycloudflare.com@evil.example", "https://a.trycloudflare.com:443",
    "https://a.trycloudflare.com/path", "https://a.trycloudflare.com?token=synthetic",
])
def test_untrusted_public_url_is_not_accepted(tmp_path, bad):
    process = Process(ready=False)
    process.stderr.feed((bad + "\nRegistered tunnel connection\n").encode())
    bridge, _, _ = tunnel(tmp_path, process, ready_timeout=.03)
    with pytest.raises(RuntimeError):
        bridge.open(ORIGIN)
    assert bridge.url == "" and not bridge.process_alive and process.terminate_calls == 1


def test_json_stderr_and_url_split_across_bounded_reads(tmp_path):
    process = Process(ready=False)
    process.stderr.feed(json.dumps({"message": "Registered tunnel connection"}).encode() + b"\n")
    process.stderr.feed(b"x" * 4080 + (" | " + PUBLIC + " |\n").encode())
    bridge, _, _ = tunnel(tmp_path, process)
    try:
        assert bridge.open(ORIGIN) == PUBLIC
    finally:
        bridge.close()


def test_child_exit_invalidates_url_and_only_owned_process_is_stopped(tmp_path):
    bridge, process, _ = tunnel(tmp_path)
    bridge.open(ORIGIN)
    process.finish(7)
    eventually(lambda: bridge.url == "")
    assert not bridge.process_alive and bridge.last_error
    bridge.close()
    assert process.terminate_calls == 0


def test_timeout_kills_unresponsive_child_without_retaining_output(tmp_path):
    process = Process(ready=False, terminate_works=False)
    process.stderr.feed(b"ERR synthetic-secret-must-not-escape\n")
    bridge, _, _ = tunnel(tmp_path, process, ready_timeout=.03)
    with pytest.raises(RuntimeError) as error:
        bridge.open(ORIGIN)
    assert "synthetic-secret" not in str(error.value) + bridge.last_error
    assert process.terminate_calls == 1 and process.kill_calls == 1 and process.stderr.closed
    assert bridge._run is None


def test_close_cancels_pending_start_and_joins_reader(tmp_path):
    bridge, process, _ = tunnel(tmp_path, Process(ready=False), ready_timeout=1)
    failures = []

    def start():
        try:
            bridge.open(ORIGIN)
        except RuntimeError as error:
            failures.append(str(error))

    starter = threading.Thread(target=start)
    starter.start()
    eventually(lambda: bridge._run is not None)
    reader = bridge._run.reader
    bridge.close()
    starter.join(1)
    assert failures and not starter.is_alive() and not reader.is_alive()
    assert process.terminate_calls == 1 and bridge.url == ""


def test_missing_executable_and_spawn_failure_leave_no_process(tmp_path, monkeypatch):
    monkeypatch.delenv("MANUX_CLOUDFLARED", raising=False)
    bridge = CloudflareQuickTunnel()
    with pytest.raises(FileNotFoundError):
        bridge.open(ORIGIN)
    assert not bridge.process_alive
    executable = tmp_path / "synthetic-cloudflared.exe"
    executable.write_bytes(b"placeholder")
    monkeypatch.setenv("MANUX_CLOUDFLARED", str(executable))
    configs = []

    def fail(arguments, **_):
        configs.append(Path(arguments[arguments.index("--config") + 1]))
        raise OSError("synthetic internal detail")

    bridge = CloudflareQuickTunnel(popen=fail)
    with pytest.raises(OSError, match="도우미"):
        bridge.open(ORIGIN)
    assert not bridge.process_alive and configs and not configs[0].exists()


@pytest.mark.parametrize("selection", ["explicit", "environment", "bundle", "source", "missing-explicit", "missing-environment"])
def test_packaged_helper_lookup_preserves_explicit_choices_and_source_behavior(tmp_path, monkeypatch, selection):
    bundle = tmp_path / "bundle"
    (bundle / "tools").mkdir(parents=True)
    files = {"explicit": tmp_path / "explicit.exe", "environment": tmp_path / "environment.exe",
             "bundle": bundle / "tools" / "cloudflared.exe"}
    for path in files.values():
        path.write_bytes(b"synthetic executable placeholder")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "frozen", selection != "source", raising=False)
    monkeypatch.delenv("MANUX_CLOUDFLARED", raising=False)
    explicit = None
    if selection in ("explicit", "environment", "missing-explicit", "missing-environment"):
        monkeypatch.setenv("MANUX_CLOUDFLARED", str(files["environment"]))
    if selection in ("explicit", "missing-explicit"):
        explicit = files["explicit"]
    if selection.startswith("missing-"):
        files[selection.removeprefix("missing-")].unlink()
    process, calls = Process(), []
    def popen(arguments, **options):
        calls.append(arguments)
        return process
    bridge = CloudflareQuickTunnel(explicit, popen=popen)
    try:
        if selection == "source" or selection.startswith("missing-"):
            with pytest.raises(FileNotFoundError):
                bridge.open(ORIGIN)
            assert not calls and not bridge.process_alive
        else:
            assert bridge.open(ORIGIN) == PUBLIC
            assert Path(calls[0][0]) == files[selection].resolve()
    finally:
        bridge.close()
