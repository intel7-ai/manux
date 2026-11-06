"""One external job at a time. The app never imports torch or transmits credentials in argv."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4


COMPONENT_REQUIRED = "추가 구성요소 필요: TCN 고급 학습·팀 공유는 Trainer 포함 설치본이 필요합니다."


def worker_available(*, frozen=None, executable=None, tools_dir=None):
    """현재 설치본의 실행 파일 존재 여부만 확인한다. torch를 앱에서 import하지 않는다."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    executable = Path(executable or sys.executable)
    if frozen:
        return (executable.parent / "worker" / "MANUXWorker.exe").is_file()
    tools = Path(tools_dir) if tools_dir else Path(__file__).resolve().parents[3] / "tools"
    return (tools / "app_worker.py").is_file()


def worker_command(request: Path, *, frozen=None, executable=None, tools_dir=None):
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    executable = Path(executable or sys.executable)
    if frozen:
        worker = executable.parent / "worker" / "MANUXWorker.exe"
        if not worker.is_file():
            raise ValueError(COMPONENT_REQUIRED)
        return [str(worker), "--request", str(request)]
    tools = Path(tools_dir) if tools_dir else Path(__file__).resolve().parents[3] / "tools"
    return [str(executable), str(tools / "app_worker.py"), "--request", str(request)]


class WorkerJob:
    def __init__(self, jobs_dir: Path, *, on_done=None, tools_dir=None):
        self.jobs_dir, self.on_done, self.tools_dir = Path(jobs_dir), on_done, tools_dir
        self.proc = None
        self.state = ({"running": False} if worker_available(tools_dir=tools_dir) else
                      {"running": False, "stage": "추가 구성요소 필요", "error": COMPONENT_REQUIRED})
        try:
            saved = json.loads((Path(jobs_dir) / "last-job.json").read_text(encoding="utf-8"))
            if isinstance(saved, dict) and saved.get("finished") and not saved.get("running"):
                self.state = {**saved, "restored": True}
        except (OSError, ValueError):
            pass
        self._lock = threading.Lock()
        self._cancel = None

    @property
    def running(self):
        with self._lock:
            return bool(self.state.get("running"))

    def fail(self, text):
        with self._lock:
            if not self.state.get("running"):
                self.state = {"running": False, "error": str(text), "stage": "진행할 수 없습니다"}
        return False

    def start_request(self, request):
        with self._lock:
            if self.state.get("running"):
                return False
            if not worker_available(tools_dir=self.tools_dir):
                self.state = {"running": False, "error": COMPONENT_REQUIRED, "stage": "추가 구성요소 필요"}
                return False
            job_id = uuid4().hex
            folder = self.jobs_dir / job_id
            folder.mkdir(parents=True)
            payload = {**request, "schema": "manux-worker-job/1", "job_id": job_id,
                       "job_dir": str(folder), "cancel_path": str(folder / "cancel")}
            path = folder / "request.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            try:
                argv = worker_command(path, tools_dir=self.tools_dir)
                self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             text=True, encoding="utf-8", errors="replace",
                                             env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, ValueError):
                self.state = {"running": False, "error": "작업 구성요소를 시작하지 못했습니다. 설치 상태를 확인하세요."}
                return False
            self._cancel = folder / "cancel"
            self.state = {"running": True, "stage": "준비 중", "progress": 0., "started": time.time(),
                          "job_id": job_id, "kind": request["kind"], "log": [], "events": [],
                          "phase": "loading", "task_names": request.get("task_names", [])}
            proc = self.proc
        threading.Thread(target=self._read, args=(proc, payload), daemon=True).start()
        return True

    def _read(self, proc, request):
        completed = None
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue  # do not expose subprocess stack traces, local paths or HTTP credentials
            if not isinstance(event, dict) or "event" not in event:
                continue
            with self._lock:
                if self.state.get("job_id") != request["job_id"]:
                    continue
                if event["event"] == "error":
                    self.state["error"] = str(event.get("message", "작업 실패"))
                elif event["event"] == "done":
                    completed = event
                else:
                    now = time.time()
                    if "step" in event and event["step"] != self.state.get("step"):
                        self.state.pop("last_epoch_at", None)
                        self.state["epoch_seconds"] = []
                        self.state["losses"] = []
                    for key in ("stage", "phase", "epoch", "epochs", "loss", "fold", "folds", "step", "steps",
                                "execution", "train_files", "eval_files", "conditions", "frames"):
                        if key in event:
                            self.state[key] = event[key]
                    progress = event.get("progress")
                    if type(progress) in (int, float) and math.isfinite(progress):
                        self.state["progress"] = max(self.state.get("progress", 0.), min(.99, max(0., progress)))
                    if "epoch" in event and "loss" in event:
                        self.state["losses"] = (self.state.get("losses", []) + [float(event["loss"])])[-120:]
                        previous = self.state.get("last_epoch_at")
                        if previous is not None and now > previous:
                            self.state["epoch_seconds"] = (self.state.get("epoch_seconds", []) + [now - previous])[-8:]
                        self.state["last_epoch_at"] = now
                    if event.get("stage"):
                        line = str(event["stage"])
                        if "epoch" in event:
                            line += f" · {event['epoch']}/{event['epochs']} · loss {float(event['loss']):.4f}"
                        self.state["events"] = (self.state.get("events", []) + [line])[-120:]
        code = proc.wait()
        with self._lock:
            if self.state.get("job_id") != request["job_id"]:
                return
            self.state["running"] = False
            self.state["finished"] = time.time()
            if code == 0 and completed is not None:
                self.state.update(stage=str(completed.get("stage", "완료")), progress=1., result=completed, success=True)
                self.state.pop("error", None)
            elif not self.state.get("error"):
                self.state["error"] = ("작업을 중단했습니다. 마지막 적용·업로드 상태를 확인하세요." if self._cancel.exists()
                                       else "작업을 완료하지 못했습니다. 데이터·연결·설치 상태를 확인하세요.")
            try:
                from manux.data.legacy_import import atomic_json
                atomic_json(self.jobs_dir / "last-job.json", self.state)
            except OSError:
                pass  # Result display persistence must not change a completed installation.
        if code == 0 and completed is not None and self.on_done is not None and self.state.get("job_id") == request["job_id"]:
            self.on_done(completed)

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self.state)

    def stop(self):
        with self._lock:
            if not self.state.get("running") or self.proc is None:
                return
            self._cancel.touch()
            self.state["stage"] = "중단 요청 중"
            self.state["cancelled"] = True
            proc = self.proc
            kind = self.state.get("kind")
        if kind in ("train", "studio", "import", "validate_import"):
            return  # checked at epoch/validation/install boundaries; never kill inside atomic installation
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
        else:
            proc.terminate()
