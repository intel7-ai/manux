"""The app's side of background learning: start scripts/online_update.py in its own process, read its progress, and say what happened.
Also the production model as a read-only predictor, so the "what was that really?" question can start from its guess."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.online.samples import DEFAULT_ROOT, SUPERVISED_SOURCES, SampleStore

ROOT = Path(__file__).resolve().parents[2]


def _python() -> str:
    try:
        from src.training_center import training_python
        return training_python()
    except Exception:
        return sys.executable


class OnlineJob:
    """One learning round or rollback at a time. Thread-safe reads of `state`."""

    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self.state: Dict[str, Any] = {"phase": "idle"}
        self.version = 0          # +1 whenever state changes, so a poller can tell

    @property
    def running(self) -> bool:
        return self.state.get("phase") in ("starting", "loading", "update", "checking")

    def _set(self, **state: Any) -> None:
        with self._lock:
            self.state = state
            self.version += 1

    def start(self, rollback: bool = False, command: Optional[List[str]] = None) -> bool:
        if self.running:
            return False
        args = command or [_python(), "-u", str(ROOT / "scripts" / "online_update.py"), "--progress", "--root", str(self.root)]
        if rollback and command is None:
            args.append("--rollback")
        self._set(phase="starting", rollback=rollback)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            self._proc = subprocess.Popen(args, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                          errors="replace", env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            self._set(phase="error", message=str(exc))
            return False
        threading.Thread(target=self._read, name="online-job", daemon=True).start()
        self.set_low_priority(False)          # below normal from the start: the camera comes first
        return True

    def _read(self) -> None:
        proc = self._proc
        last: Dict[str, Any] = {}
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            last = event
            name = event.pop("event", "")
            if name == "done":
                self._set(phase="done", **event)
            elif name == "error":
                self._set(phase="error", message=event.get("message", ""))
            else:
                self._set(phase=name, **event)
        proc.wait()
        if self.state.get("phase") not in ("done", "error"):
            self._set(phase="error", message=f"학습 프로세스가 멈췄어요 (종료 코드 {proc.returncode})")
        del last

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()

    def set_low_priority(self, low: bool) -> bool:
        """Spec 13: while the camera loop falls behind, the learning process only gets the CPU nobody else wants (Windows idle
        priority); otherwise below normal. True when the priority was set."""
        proc = self._proc
        if proc is None or proc.poll() is not None or os.name != "nt":
            return False
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0200 | 0x0400, False, proc.pid)      # set / query information
            if not handle:
                return False
            try:
                return bool(kernel32.SetPriorityClass(handle, 0x40 if low else 0x4000))   # IDLE / BELOW_NORMAL
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False


def summary(root: Path | str = DEFAULT_ROOT) -> Dict[str, Any]:
    """How many confirmed/corrected samples there are, and what the last round did."""
    root = Path(root)
    count = 0
    try:
        count = len([s for s in SampleStore(root).samples(sources=list(SUPERVISED_SOURCES)) if s.label != "unknown"])
    except OSError:
        pass
    history: List[Dict[str, Any]] = []
    path = root / "versions" / "versions.json"
    if path.is_file():
        try:
            history = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            history = []
    rounds = [h for h in history if h.get("event") in ("promoted", "rejected", "rolled_back")]
    return {"samples": count, "last": rounds[-1] if rounds else None, "can_rollback": (root / "versions" / "previous.pt").is_file(),
            "has_production": (root / "versions" / "production.pt").is_file()}


class ProductionPredictor:
    """The production model, read-only, on the CPU: the most likely gesture of a short sequence, or "" when unsure."""

    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        import torch

        from src.online.models import make_model
        from src.online.versions import ModelManager
        saved = ModelManager(root).load()
        info = saved["info"]
        self.classes: List[str] = list(info["classes"])
        self.window = int(info["window"])
        self.model = make_model(info["kind"], len(self.classes), self.window, info["embedding"])
        self.model.load_state_dict(saved["state"])
        self.model.eval()
        self._torch = torch
        self.loaded = time.time()

    def guess(self, frames: List[Dict[str, Any]], aspect: float, confidence: float = .6) -> str:
        import numpy as np

        from src.tcn_features import frame_features, windows
        if not frames:
            return ""
        x = windows(frame_features(frames, aspect), self.window)
        keep = x[:, -1, 132] > 0
        if not keep.any():
            return ""
        with self._torch.no_grad():
            p = self._torch.softmax(self.model(self._torch.from_numpy(np.ascontiguousarray(x[keep], dtype=np.float32))), 1).numpy()
        mean = p.mean(axis=0)
        best = int(mean.argmax())
        return self.classes[best] if mean[best] >= confidence else ""
