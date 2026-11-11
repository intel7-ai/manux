"""The virtual hand's clicks from everyday use, and the person's "that was not a click" marks (F key), 2026-11-10.

Every hand-shape click (left / double / drag / right) is written with the joints of the last PRE_SECONDS: screen landmarks and 3D
world landmarks, no image. F within MARK_SECONDS of a click adds a mark record for it. scripts/virtual_click_report.py reads them:
how often a click was wrong, and what the TCN click check (src/click_check.py) would have kept.

One JSON line per record in logs/virtual_clicks/<date>-<time>-<pc>.jsonl (logs/ is not in git). The PC is the nickname from
config "pc_alias" (src/recorder.py), never the real PC or user name. Clicks from different PCs stay in their own files.
"""
from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

PRE_SECONDS = 1.5       # joints kept before each click (the TCN needs 15 frames, the report looks 0.3 s before contact)
MARK_SECONDS = 5.0      # F this long after a click marks it
KINDS = ("left", "double", "drag", "right")


class ClickLog:
    def __init__(self, folder: Path, pc: str = "", camera: str = "") -> None:
        self.folder = Path(folder)
        self.pc = pc or "pc"
        self.camera = camera
        self._frames: Deque[Dict[str, Any]] = deque()
        self._path: Optional[Path] = None
        self._next_id = 1
        self.last: Optional[Tuple[int, float, str]] = None     # (id, time, kind) of the latest click
        self._marked: set = set()
        self.failed = ""

    # ------------------------------------------------------------------ every frame
    def frame(self, now: float, hand: Any) -> None:
        if hand is None:
            self._frames.append({"t": round(now, 4)})
        else:
            rec = {"t": round(now, 4), "lm": [[round(p.x, 5), round(p.y, 5), round(p.z, 5)] for p in hand.landmarks]}
            world = getattr(hand, "world", None)
            if world:
                rec["world"] = [[round(float(v), 5) for v in (p if isinstance(p, (list, tuple)) else (p.x, p.y, p.z))]
                                for p in world]
            self._frames.append(rec)
        while self._frames and now - self._frames[0]["t"] > PRE_SECONDS:
            self._frames.popleft()

    # ------------------------------------------------------------------ events
    def click(self, kind: str, now: float, pressed: Optional[float] = None, tcn_peak: Optional[float] = None) -> Optional[int]:
        """Write one click with the joints before it. `pressed` = when the fingers met. Returns its id (None if writing failed)."""
        cid = self._next_id
        self._next_id += 1
        rec = {"type": "click", "id": cid, "kind": kind, "t": round(now, 4),
               "pressed": None if pressed is None else round(pressed, 4), "wall": time.strftime("%Y-%m-%d %H:%M:%S"),
               "tcn_peak": None if tcn_peak is None else round(float(tcn_peak), 4), "frames": list(self._frames)}
        if not self._write(rec):
            return None
        self.last = (cid, now, kind)
        return cid

    def mark_last(self, now: float) -> Optional[str]:
        """F: the latest click, if it was within MARK_SECONDS and not marked yet, was not meant. Returns its kind."""
        if self.last is None:
            return None
        cid, t, kind = self.last
        if now - t > MARK_SECONDS or cid in self._marked:
            return None
        if not self._write({"type": "mark", "id": cid, "t": round(now, 4), "wall": time.strftime("%Y-%m-%d %H:%M:%S")}):
            return None
        self._marked.add(cid)
        return kind

    # ------------------------------------------------------------------ file
    def _write(self, rec: Dict[str, Any]) -> bool:
        try:
            if self._path is None:
                self.folder.mkdir(parents=True, exist_ok=True)
                self._path = self.folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{self.pc}.jsonl"
                head = {"type": "session", "pc": self.pc, "camera": self.camera, "wall": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "pre_seconds": PRE_SECONDS}
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(head, ensure_ascii=False) + "\n")
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return True
        except Exception as exc:          # a full disk must not stop the hand
            self.failed = str(exc)
            return False


def read_clicks(folder: Path) -> List[Dict[str, Any]]:
    """All clicks under `folder`, each with "file", "pc" and "marked" (True = F said it was not a click)."""
    out: List[Dict[str, Any]] = []
    for path in sorted(Path(folder).glob("*.jsonl")):
        clicks: Dict[int, Dict[str, Any]] = {}
        pc = ""
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue                  # a line cut off by a crash
            if rec.get("type") == "session":
                pc = rec.get("pc", "")
            elif rec.get("type") == "click":
                clicks[rec["id"]] = {**rec, "file": path.name, "pc": pc, "marked": False}
            elif rec.get("type") == "mark" and rec.get("id") in clicks:
                clicks[rec["id"]]["marked"] = True
        out.extend(clicks.values())
    return out
