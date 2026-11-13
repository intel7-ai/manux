"""Explicitly saved PowerShell commands, kept off the camera thread."""
from __future__ import annotations

import base64
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time

from manux.compat.hand_mouse.pc_commands import script_problem

TIMEOUT = 600.0
OUTPUT_LIMIT = 4000


def powershell_path():
    candidates = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "PowerShell/7/pwsh.exe",
                  Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"]
    return next((str(p) for p in candidates if p.is_file()), None) or shutil.which("pwsh")


def command_line(executable, script):
    problem = script_problem(script)
    if problem:
        raise ValueError(problem)
    # EncodedCommand preserves quotes, Korean, multiline text and pipelines without shell interpolation.
    wrapped = ("$ErrorActionPreference='Stop'; $global:LASTEXITCODE=0; "
               "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
               "$OutputEncoding=[Console]::OutputEncoding; try {\n" + script +
               "\nif ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n} "
               "catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }")
    encoded = base64.b64encode(wrapped.encode("utf-16-le")).decode("ascii")
    return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


class CommandRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread = None
        self._state = dict(running=False, message="", output="", exit_code=None)

    def status(self):
        with self._lock:
            return dict(self._state)

    def cancel(self):
        self._cancel.set()

    def _set(self, **values):
        with self._lock:
            self._state.update(values)

    def start(self, script, *, label="PowerShell", dry_run=False, wait=False, cancel_event=None):
        problem = script_problem(script)
        if problem:
            return False, problem
        if dry_run:
            return True, label + " · 실행하지 않고 형식 확인"
        executable = powershell_path()
        if not executable:
            return False, "PowerShell 실행 파일이 없어요"
        args = command_line(executable, script)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False, "앞의 PowerShell 명령이 실행 중이에요"
            self._cancel.clear()
            self._state = dict(running=True, message=label + " 실행 중", output="", exit_code=None)
            self._thread = threading.Thread(target=self._run, args=(args, label, cancel_event), daemon=True)
            job = self._thread
            job.start()
        if wait:
            job.join()
            result = self.status()
            return result["exit_code"] == 0, result["message"]
        return True, label + " 실행 중"

    def _run(self, args, label, cancel_event):
        process = None
        try:
            # A temporary spool prevents unbounded in-memory stdout; only the tail is presented in the UI.
            with tempfile.TemporaryFile() as output:
                process = subprocess.Popen(args, shell=False, stdin=subprocess.DEVNULL,
                                           stdout=output, stderr=subprocess.STDOUT,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                deadline = time.monotonic() + TIMEOUT
                stopped = ""
                while process.poll() is None:
                    if self._cancel.is_set() or (cancel_event is not None and cancel_event.is_set()):
                        stopped = "중지됨 · 이미 완료된 작업은 되돌리지 않습니다"
                    elif time.monotonic() >= deadline:
                        stopped = "시간 초과 (10분)"
                    if stopped:
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=3)
                        break
                    self._cancel.wait(.05)
                code = process.poll()
                size = output.seek(0, os.SEEK_END)
                output.seek(max(0, size - OUTPUT_LIMIT * 4))
                text = output.read(OUTPUT_LIMIT * 4).decode("utf-8", errors="replace")[-OUTPUT_LIMIT:]
                if size > OUTPUT_LIMIT * 4 or len(text) == OUTPUT_LIMIT:
                    text = "… (최근 출력)\n" + text
                message = stopped or ("완료" if code == 0 else f"실패 (종료 코드 {code})")
                self._set(running=False, message=label + " · " + message, output=text,
                          exit_code=None if stopped else code)
        except Exception as exc:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            self._set(running=False, message=label + " 실행 실패", output=str(exc)[:OUTPUT_LIMIT], exit_code=None)


runner = CommandRunner()
