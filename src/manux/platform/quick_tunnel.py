"""Explicit, temporary Cloudflare tunnel for an already-authenticated loopback app.

Only the child launched here is owned/stopped. Raw child output, URLs and any
credentials are never logged or persisted. The public origin lives in memory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading


_PUBLIC_URL = re.compile(
    r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com(?=$|[\s|\"'<>])",
    re.ASCII,
)
_CHILD_ENV = frozenset((
    "SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "COMSPEC",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "LANG", "LC_ALL",
))


def _loopback_origin(value):
    if not isinstance(value, str):
        raise ValueError("인터넷 연결 대상은 로컬 HTTP 서버여야 합니다.")
    match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})/?", value, re.ASCII)
    if match is None or not 1 <= int(match[1]) <= 65535:
        raise ValueError("인터넷 연결 대상은 포트가 지정된 127.0.0.1 HTTP 서버여야 합니다.")
    return f"http://127.0.0.1:{int(match[1])}"


class _Run:
    def __init__(self, process, temporary, origin):
        self.process, self.temporary, self.origin = process, temporary, origin
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.reader = None
        self.candidate = ""
        self.connected = False


class CloudflareQuickTunnel:
    def __init__(self, executable=None, *, ready_timeout=30.0, popen=None):
        self.executable = executable
        self.ready_timeout = float(ready_timeout)
        if not 0 < self.ready_timeout <= 60:
            raise ValueError("invalid tunnel startup timeout")
        self._popen = popen if popen is not None else subprocess.Popen
        self._lock = threading.RLock()
        self._open_lock = threading.Lock()
        self._close_lock = threading.RLock()
        self._run = None
        self._url = ""
        self._last_error = ""

    def _check_exit(self):
        run = self._run
        if run is not None and not run.stopped.is_set() and run.process.poll() is not None:
            self._url = ""
            self._last_error = "인터넷 연결 도우미가 종료됐습니다. 인터넷 연결을 다시 켜 주세요."

    @property
    def url(self):
        with self._lock:
            self._check_exit()
            return self._url

    @property
    def process_alive(self):
        with self._lock:
            self._check_exit()
            return bool(self._run is not None and not self._run.stopped.is_set()
                        and self._run.process.poll() is None)

    @property
    def last_error(self):
        with self._lock:
            self._check_exit()
            return self._last_error

    def open(self, local_url):
        origin = _loopback_origin(local_url)
        with self._open_lock:
            with self._lock:
                run = self._run
                if run is not None and self.process_alive:
                    if run.origin == origin and self._url:
                        return self._url
                    raise RuntimeError("인터넷 연결이 이미 시작 중이거나 다른 서버에 연결돼 있습니다.")
            self.close()
            executable = self.executable or os.environ.get("MANUX_CLOUDFLARED", "")
            if not executable and getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
                executable = Path(sys._MEIPASS) / "tools" / "cloudflared.exe"
            if not executable or not Path(executable).is_file():
                self._last_error = "인터넷 연결 도우미 파일을 찾을 수 없습니다. MANUX_CLOUDFLARED 설정을 확인해 주세요."
                raise FileNotFoundError(self._last_error)
            # An explicit empty config and restricted environment prevent a user's
            # unrelated named tunnel/ingress/token configuration from taking over.
            temporary = tempfile.TemporaryDirectory(prefix="manux-quick-tunnel-")
            config = Path(temporary.name) / "config.yml"
            config.write_text("{}\n", encoding="utf-8")
            arguments = [str(Path(executable).resolve()), "tunnel", "--config", str(config),
                         "--no-autoupdate", "--url", origin, "--protocol", "http2"]
            options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, shell=False,
                           env={key: value for key, value in os.environ.items() if key.upper() in _CHILD_ENV})
            if sys.platform == "win32":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            try:
                process = self._popen(arguments, **options)
            except OSError:
                temporary.cleanup()
                self._last_error = "인터넷 연결 도우미를 실행할 수 없습니다."
                raise OSError(self._last_error) from None
            run = _Run(process, temporary, origin)
            with self._lock:
                self._run = run
                self._url, self._last_error = "", ""
                run.reader = threading.Thread(target=self._drain, args=(run,),
                                              name="manux-quick-tunnel", daemon=True)
                run.reader.start()
            completed = run.ready.wait(self.ready_timeout)
            with self._lock:
                success = (completed and self._run is run and not run.stopped.is_set()
                           and run.connected and bool(self._url) and process.poll() is None)
                if success:
                    return self._url
                if not self._last_error:
                    self._last_error = ("인터넷 연결 시작이 취소됐습니다." if run.stopped.is_set()
                                        else "임시 인터넷 연결을 준비하지 못했습니다. 네트워크 상태를 확인해 주세요.")
                error = self._last_error
            self.close()
            raise RuntimeError(error)

    def _drain(self, run):
        tail = ""
        try:
            while True:
                # readline's size bound also limits malformed/no-newline output.
                chunk = run.process.stderr.readline(4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                message = tail + text
                tail = message[-256:] if not text.endswith("\n") else ""
                # Accept normal CLI output and JSON logging without retaining it.
                if message.startswith("{"):
                    try:
                        record = json.loads(message)
                        message = str(record.get("message", "")) if isinstance(record, dict) else ""
                    except (ValueError, TypeError):
                        pass
                candidate = _PUBLIC_URL.search(message)
                registered = "Registered tunnel connection" in message
                with self._lock:
                    if self._run is not run or run.stopped.is_set():
                        continue
                    if candidate and not run.candidate:
                        run.candidate = candidate[0]
                    if registered:
                        run.connected = True
                    if run.candidate and run.connected:
                        self._url = run.candidate
                        run.ready.set()
        except (OSError, ValueError):
            pass
        finally:
            with self._lock:
                if self._run is run and not run.stopped.is_set():
                    self._url = ""
                    self._last_error = "인터넷 연결 도우미가 중단됐습니다. 인터넷 연결을 다시 켜 주세요."
                run.ready.set()

    def close(self):
        with self._close_lock:
            with self._lock:
                run = self._run
                self._url = ""
                if run is None:
                    return
                run.stopped.set()
                run.ready.set()
            process = run.process
            # No shell and no updater: cloudflared is our direct, single child.
            # Popen uses its owned handle/PID; never kill by executable name.
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            if run.reader is not None and run.reader is not threading.current_thread():
                run.reader.join(timeout=2)
            if process.stderr is not None:
                process.stderr.close()
            run.temporary.cleanup()
            run.candidate = ""
            with self._lock:
                if self._run is run:
                    self._run = None

    stop = close


QuickTunnel = CloudflareQuickTunnel
