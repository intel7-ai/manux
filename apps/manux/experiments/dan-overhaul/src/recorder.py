"""
Dataset recorder (2026-11-05): every frame of a session to `recordings/<time>-<mode>.jsonl.gz`, for training a
gesture model (TCN) that checks / replaces the hand-written rules.

One JSON object per line:
- `{"type": "meta", ...}` first: schema, PC nickname (never the real PC or user name: the repository is public), camera (source, real mode, mirror, a free note such as "phone
  camera"), control hand, profile, mode ("guided" | "free"), the rule thresholds in use. Recordings from different PCs
  and cameras must never be mixed up (2026-11-04: tuning from the home PC's phone camera looked like the old webcam's).
- `{"type": "frame", ...}` per camera frame: time, the hand's image joints (x, y, z), its 3D joints in metres
  (`world`, nearly the same from any camera angle), handedness and score, the latest Pose shoulders / elbows / wrists,
  the LABEL (what the user was asked to do, see src/guided_recording.py, or "none" while just using the mouse) and
  what the RULES decided (action, gesture, multiplier, curl), the cursor.
- `{"type": "event", ...}`: the user's "that was wrong" mark (F key / button) and the like.
- `{"type": "end", ...}` last: frame counts per label.

No camera images are stored. About 2-3 kB per frame before compression.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import socket
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA = "hand-mouse-recording/1"
FORBIDDEN_META = frozenset({"pc_host", "hostname", "computername", "user", "username", "ip", "mac", "cwd", "home"})
LEGACY_RAW_PC_RE = re.compile(r"^pc-[0-9a-f]{4}$")


def _r(v: float, n: int = 4) -> float:
    return round(float(v), n)


def pc_label(alias: str = "") -> str:
    """How this PC appears in recordings and file names. The recordings are uploaded to a public repository, so never
    the real PC name or user name: the nickname from config "pc_alias" (e.g. "home-pc"), else "pc-" + a short code
    made from the PC name (same PC -> same code, but the name cannot be read back)."""
    nick = re.sub(r"[^A-Za-z0-9_-]+", "", alias or "")
    if nick:
        return nick[:32]
    try:
        host = socket.gethostname()
    except Exception:
        host = ""
    return "pc-" + hashlib.sha1(host.encode("utf-8")).hexdigest()[:4]


def machine_info(alias: str = "") -> Dict[str, str]:
    return {"pc": pc_label(alias), "pc_named": "alias" if re.sub(r"[^A-Za-z0-9_-]+", "", alias or "") else "code"}


def public_source(meta: Dict[str, Any]) -> Tuple[str, str]:
    """(pc, camera note) of a recording, safe to publish. Recordings made before 2026-11-05 05:00 kept the real PC name
    (no "pc_named"): shown as a code instead, and the name is taken out of the camera note."""
    pc = str(meta.get("pc") or "")
    note = str((meta.get("camera") or {}).get("note", ""))
    if pc and not meta.get("pc_named"):
        code = "pc-" + hashlib.sha1(pc.encode("utf-8")).hexdigest()[:4]
        note, pc = note.replace(pc, code), code
    return pc, note


class SessionRecorder:
    def __init__(self, folder: Path, mode: str, meta: Dict[str, Any], now: Optional[float] = None) -> None:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        # The PC name keeps files from several PCs apart in one shared folder (same second on two PCs).
        meta = {k: v for k, v in dict(meta).items() if k.lower() not in FORBIDDEN_META}
        who = machine_info(meta.pop("pc_alias", ""))
        pc = who["pc"]
        self.path = folder / f"{stamp}-{pc}-{mode}.jsonl.gz"
        # Written as .part and renamed on close: training and git never pick up a recording still being written.
        self._part = self.path.with_name(self.path.name + ".part")
        self._f = gzip.open(self._part, "wt", encoding="utf-8")
        self.mode = mode
        self.counts: Counter = Counter()
        self.frames = 0
        self.marks = 0
        self._t0 = time.perf_counter() if now is None else now
        self._write({"type": "meta", "schema": SCHEMA, "mode": mode, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                     **who, **meta})

    def _write(self, obj: Dict[str, Any]) -> None:
        self._f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")

    def frame(self, now: float, hand: Any = None, pose: Optional[Sequence[Sequence[float]]] = None,
              label: Optional[Dict[str, Any]] = None, rule: Optional[Dict[str, Any]] = None,
              cursor: Optional[Tuple[int, int]] = None) -> None:
        rec: Dict[str, Any] = {"type": "frame", "t": _r(now - self._t0, 3)}
        if hand is not None:
            rec["lm"] = [[_r(p.x), _r(p.y), _r(p.z)] for p in hand.landmarks]
            if getattr(hand, "world", None):
                rec["world"] = [[_r(x, 5), _r(y, 5), _r(z, 5)] for x, y, z in hand.world]
            rec["hand"] = [hand.handedness, _r(hand.score, 3)]
        if pose is not None:
            rec["pose"] = [[_r(v, 4) for v in p] for p in pose]
        lab = dict(label or {"target": "none"})
        rec["label"] = lab
        if rule:
            rec["rule"] = rule
        if cursor is not None:
            rec["cursor"] = [int(cursor[0]), int(cursor[1])]
        self._write(rec)
        self.frames += 1
        self.counts[str(lab.get("target", "none")) if hand is not None else "no_hand"] += 1

    def event(self, now: float, kind: str, info: Optional[Dict[str, Any]] = None) -> None:
        self._write({"type": "event", "t": _r(now - self._t0, 3), "kind": kind, **(info or {})})
        if kind == "mark":
            self.marks += 1

    def close(self) -> Path:
        if self._f is not None:
            self._write({"type": "end", "frames": self.frames, "counts": dict(self.counts), "marks": self.marks})
            self._f.close()
            self._f = None
            os.replace(self._part, self.path)
        return self.path

    @property
    def part_path(self) -> Path:
        return self._part

    def discard(self) -> None:
        if self._f is not None:
            try:
                self._f.close()
            except Exception:
                pass
            self._f = None
        for p in (self._part, self.path):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass


def read_session(path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(meta, frames, events) of a recording. meta["complete"] is True when the file has its closing "end" line."""
    meta: Dict[str, Any] = {}
    frames: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    complete = False
    raw_bytes = gzip.decompress(Path(path).read_bytes())
    for line in raw_bytes.decode("utf-8").splitlines():
        if not line:
            continue
        obj = json.loads(line)
        kind = obj.get("type")
        if kind == "end":
            complete = True
        elif kind == "meta":
            meta = obj
        elif kind == "frame":
            frames.append(obj)
        elif kind == "event":
            events.append(obj)
    meta["complete"] = complete
    return meta, frames, events


_FINISHED_CACHE: Dict[Tuple[str, int, int], Optional[str]] = {}


def finished_recordings(folder: Path) -> Tuple[List[Path], List[Tuple[Path, str]]]:
    """(usable, skipped) recordings in a folder, fixed at this moment. Skipped: unreadable (half-downloaded) or no
    closing line (app closed mid-recording) - with the reason. Files still being recorded end in .part and are not listed."""
    usable: List[Path] = []
    skipped: List[Tuple[Path, str]] = []
    for p in sorted(Path(folder).glob("*.jsonl.gz")):
        try:
            st = p.stat()
            cache_key = (str(p.resolve()), st.st_mtime_ns, st.st_size)
        except OSError as exc:
            skipped.append((p, f"읽지 못함 ({type(exc).__name__})"))
            continue
        if cache_key in _FINISHED_CACHE:
            reason = _FINISHED_CACHE[cache_key]
            if reason is None:
                usable.append(p)
            else:
                skipped.append((p, reason))
            continue
        try:
            raw = gzip.decompress(p.read_bytes()).rstrip()
            last = raw.rsplit(b"\n", 1)[-1].decode("utf-8") if raw else ""
            if json.loads(last).get("type") != "end":
                reason = "끝나지 않은 녹화"
                _FINISHED_CACHE[cache_key] = reason
                skipped.append((p, reason))
                continue
        except Exception as exc:  # truncated gzip, broken JSON, file in use
            reason = f"읽지 못함 ({type(exc).__name__})"
            _FINISHED_CACHE[cache_key] = reason
            skipped.append((p, reason))
            continue
        _FINISHED_CACHE[cache_key] = None
        usable.append(p)
    return usable, skipped
