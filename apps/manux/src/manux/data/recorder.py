"""Writing training recordings in `hand-mouse-recording/1` (docs/contracts.md section 9).

Ported from Dan's `hand-mouse-gesture-overhaul@7448e4a` `src/recorder.py`. The lines are the same,
so the S1 reader (`data/recording.py`), Dan's tools and `tools/train_tcn.py` all read them:

- `meta` first: schema, mode, created, `pc` (an alias, never the real PC or user name), `pc_named`,
  then whatever the caller passes (camera, control hand, profile, guided tasks, rule thresholds).
- `frame` per camera frame: `t` (s since start), `lm`, `world`, `hand` ([MediaPipe label, score]),
  `pose`, `label`, `rule`, `cursor`.
- `event`: marks (the user's "that was wrong") and the like.
- `end` last: frames, counts per label, marks.

No camera images. The file is `<name>.part` while recording and renamed on `close()`: training,
the reader and git never pick up a recording still being written.

`hand` stores the MediaPipe label, as v0.3 did: on a mirrored camera that is the opposite of the
physical hand (`HandObservation.handedness`). The reader flips it back (`tracking.selection` rule),
so a write -> read round trip gives the same `HandObservation` (tested).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import socket
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from manux.core.contracts import HandObservation

SCHEMA = "hand-mouse-recording/1"
MODES = ("guided", "free")
# meta keys that would put a real name into a shared file; refused outright
FORBIDDEN_META = frozenset({"hostname", "host", "computer", "computer_name", "user", "username", "user_name", "account"})
_ALIAS_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _r(v: float, n: int = 4) -> float:
    return round(float(v), n)


def pc_label(alias: str = "", hostname: Optional[str] = None) -> str:
    """How this PC appears in recordings and file names: the alias (letters, digits, `-`, `_`; max 32),
    else `pc-` + 4 hex of a hash of the PC name (same PC -> same code; the name cannot be read back)."""
    nick = _ALIAS_RE.sub("", alias or "")
    if nick:
        return nick[:32]
    if hostname is None:
        try:
            hostname = socket.gethostname()
        except OSError:
            hostname = ""
    return "pc-" + hashlib.sha1(hostname.encode("utf-8")).hexdigest()[:4]


def machine_info(alias: str = "", hostname: Optional[str] = None) -> Dict[str, str]:
    return {"pc": pc_label(alias, hostname), "pc_named": "alias" if _ALIAS_RE.sub("", alias or "") else "code"}


def public_source(meta: Mapping[str, Any]) -> Tuple[str, str]:
    """(pc, camera note) of a recording, safe to publish. Recordings made before 2026-11-05 05:00 kept
    the real PC name (no `pc_named`): shown as a code instead, and the name is taken out of the note."""
    pc = str(meta.get("pc") or "")
    note = str((meta.get("camera") or {}).get("note", ""))
    if pc and not meta.get("pc_named"):
        code = "pc-" + hashlib.sha1(pc.encode("utf-8")).hexdigest()[:4]
        note, pc = note.replace(pc, code), code
    return pc, note


def _flip(side: str) -> str:
    return {"Left": "Right", "Right": "Left"}[side]


class SessionRecorder:
    """One recording. `frame()` per camera frame, `event()` any time, `close()` once (idempotent).

    `now` values are the caller's clock (`time.perf_counter()` in the app); `t` in the file is
    `now - start`. As a context manager it closes on the way out, or aborts (keeps `.part`) on an error.
    """

    def __init__(self, folder: Path, mode: str, meta: Optional[Mapping[str, Any]] = None,
                 now: Optional[float] = None, alias: str = "", hostname: Optional[str] = None,
                 stamp: Optional[str] = None) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        meta = dict(meta or {})
        bad = FORBIDDEN_META & {k.lower() for k in meta}
        if bad:
            raise ValueError(f"recordings never store real names: remove {sorted(bad)} from meta")
        alias = alias or str(meta.pop("pc_alias", "") or "")
        meta.pop("pc", None)
        meta.pop("pc_named", None)
        who = machine_info(alias, hostname)
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
        self.path = folder / f"{stamp}-{who['pc']}-{mode}.jsonl.gz"
        self.part = self.path.with_name(self.path.name + ".part")
        self.mode = mode
        self.mirrored = bool((meta.get("camera") or {}).get("mirror", True))   # same default as the reader
        self.counts: Counter = Counter()
        self.frames = 0
        self.marks = 0
        self._t0 = time.perf_counter() if now is None else float(now)
        self._f = gzip.open(self.part, "wt", encoding="utf-8")
        self._write({"type": "meta", "schema": SCHEMA, "mode": mode, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                     **who, **meta})

    @property
    def closed(self) -> bool:
        return self._f is None

    def _write(self, obj: Mapping[str, Any]) -> None:
        if self._f is None:
            raise ValueError("recording is closed")
        self._f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")

    def frame(self, now: float, hand: Optional[HandObservation] = None,
              pose: Optional[Sequence[Sequence[float]]] = None, label: Optional[Mapping[str, Any]] = None,
              rule: Optional[Mapping[str, Any]] = None, cursor: Optional[Tuple[int, int]] = None) -> None:
        rec: Dict[str, Any] = {"type": "frame", "t": _r(now - self._t0, 3)}
        if hand is not None:
            rec["lm"] = [[_r(x), _r(y), _r(z)] for x, y, z in hand.landmarks]
            if hand.world is not None:
                rec["world"] = [[_r(x, 5), _r(y, 5), _r(z, 5)] for x, y, z in hand.world]
            side = _flip(hand.handedness) if self.mirrored else hand.handedness
            rec["hand"] = [side, _r(hand.score, 3)]
        if pose is not None:
            rec["pose"] = [[_r(v, 4) for v in p] for p in pose]
        lab = dict(label or {"target": "none"})
        rec["label"] = lab
        if rule:
            rec["rule"] = dict(rule)
        if cursor is not None:
            rec["cursor"] = [int(cursor[0]), int(cursor[1])]
        self._write(rec)
        self.frames += 1
        self.counts[str(lab.get("target", "none")) if hand is not None else "no_hand"] += 1

    def event(self, now: float, kind: str, info: Optional[Mapping[str, Any]] = None) -> None:
        self._write({"type": "event", "t": _r(now - self._t0, 3), "kind": kind, **(info or {})})
        if kind == "mark":
            self.marks += 1

    def close(self) -> Path:
        """Write the `end` line and rename `.part` to the final name. Safe to call twice."""
        if self._f is not None:
            self._write({"type": "end", "frames": self.frames, "counts": dict(self.counts), "marks": self.marks})
            self._f.close()
            self._f = None
            os.replace(self.part, self.path)
        return self.path

    def abort(self) -> Path:
        """Stop without an `end` line: the `.part` file stays and is never used for training."""
        if self._f is not None:
            self._f.close()
            self._f = None
        return self.part

    def __enter__(self) -> "SessionRecorder":
        return self

    def __exit__(self, exc_type: Any, *exc: Any) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()
