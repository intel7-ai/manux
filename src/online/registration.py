"""Adding a new gesture from a few demonstrations (design 5). Frame by frame on the camera thread, no OS input while it runs.

prep -> do -> rest for each demonstration; a lost hand stops the clock. Then the demonstrations are checked against each other and
against the other classes, and shown to the person. Only their approval adds the class (`class_added`) and stores the demonstrations
as `user_confirmed`. This updates prototypes only: no neural weight changes.
"""
from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence

import numpy as np

from src.online.dataset import SKIP_DO_FRAMES
from src.online.memory import PrototypeMemory
from src.online.samples import USER_CONFIRMED, SampleStore

USER_SOURCE = "user_registered"
EVENT_CLASS_ADDED = "class_added"
CONTEXT_FRAMES = 15
MIN_FRAMES = 25
MIN_GOOD_DEMOS = 3
CONFUSED_SHARE = .3       # share of demonstration frames taken for one other class that deserves a warning


@dataclass
class RegistrationConfig:
    demos: int = 5
    prep_seconds: float = 1.5
    do_seconds: float = 2.0
    rest_seconds: float = 1.0
    run_frames: int = 4
    required_self_hits: float = .6     # share of demonstrations recognised by the others to call it reliable


class RegistrationError(ValueError):
    pass


def check_name(name: str, memory: PrototypeMemory) -> str:
    name = (name or "").strip()
    if not name:
        raise RegistrationError("동작 이름을 입력해 주세요")
    if len(name) > 30:
        raise RegistrationError("이름은 30자까지 쓸 수 있어요")
    if name.lower() in ("none", "unknown") or name in memory.classes:
        raise RegistrationError(f"'{name}' 이름은 이미 있어요")
    return name


def has_run(hit: np.ndarray, run: int) -> bool:
    count = 0
    for value in hit:
        count = count + 1 if value else 0
        if count >= run:
            return True
    return False


class RegistrationSession:
    def __init__(self, name: str, *, memory: PrototypeMemory, embed: Callable[[Sequence[Dict[str, Any]]], np.ndarray], store: SampleStore,
                 config: Optional[RegistrationConfig] = None, session: str = "", meta: Optional[Dict[str, Any]] = None,
                 confident: Optional[Callable[[np.ndarray], np.ndarray]] = None) -> None:
        self.name = check_name(name, memory)
        self.memory, self.embed, self.store = memory, embed, store
        self.confident = confident   # where the classifier is sure of a class it knows: the new gesture cannot claim those windows
        self.config = config or RegistrationConfig()
        self.session = session or "reg-" + uuid.uuid4().hex[:10]
        self.meta = dict(meta or {})
        self.state = "prep"       # prep | do | rest | review | failed | done | cancelled
        self.demo = 0
        self._left = self.config.prep_seconds
        self._last_t: Optional[float] = None
        self.hand_seen = False
        self._ring: Deque[Dict[str, Any]] = deque(maxlen=CONTEXT_FRAMES)
        self._current: List[Dict[str, Any]] = []
        self._current_context = 0
        self._rest: List[Dict[str, Any]] = []
        self.demos: List[Dict[str, Any]] = []
        self.report: Optional[Dict[str, Any]] = None
        self.message = ""

    # ---------------------------------------------------------------- camera thread
    @property
    def active(self) -> bool:
        return self.state in ("prep", "do", "rest")

    def update(self, t: float, lm: Any, world: Any) -> None:
        if not self.active:
            return
        dt = 0.0 if self._last_t is None else max(0.0, min(t - self._last_t, .25))
        self._last_t = t
        self.hand_seen = lm is not None and world is not None
        frame = {"t": float(t), "lm": lm, "world": world}
        if self.state == "do":
            if self.hand_seen:
                self._current.append(frame)
        elif self.state == "rest" and self.hand_seen:
            self._rest.append(frame)
        if self.hand_seen:
            self._left -= dt             # no hand: the clock waits
        if self.state != "do":
            self._ring.append(frame)
        if self._left > 0:
            return
        if self.state == "prep":
            self.state, self._left = "do", self.config.do_seconds
            self._current = list(self._ring)
            self._current_context = len(self._current)
        elif self.state == "do":
            self.demos.append({"frames": self._current, "context": self._current_context})
            self._current = []
            self.state, self._left = "rest", self.config.rest_seconds
        else:
            self.demo += 1
            if self.demo >= self.config.demos:
                self._finish()
            else:
                self.state, self._left = "prep", self.config.prep_seconds

    def view(self) -> Dict[str, Any]:
        return {"state": self.state, "name": self.name, "demo": min(self.demo + 1, self.config.demos), "demos": self.config.demos,
                "seconds": max(0.0, round(self._left, 1)), "hand": self.hand_seen, "message": self.message, "report": self.report}

    # ---------------------------------------------------------------- checks
    def _demo_rows(self, demo: Dict[str, Any], z: np.ndarray) -> np.ndarray:
        return z[demo["context"] + SKIP_DO_FRAMES:]

    def _finish(self) -> None:
        good = [d for d in self.demos if len(d["frames"]) - d["context"] >= MIN_FRAMES]
        if len(good) < MIN_GOOD_DEMOS:
            self.state, self.message = "failed", "시연이 너무 짧거나 손이 잘 보이지 않았어요. 다시 해 주세요"
            return
        self.demos = good
        embedded = [self._demo_rows(d, self.embed(d["frames"])) for d in good]
        self.report = self.check(embedded)
        self._embedded = embedded
        self.state = "review"

    def check(self, embedded: List[np.ndarray]) -> Dict[str, Any]:
        """Each demonstration against a class made from the others; then how the full class sits among the others."""
        recognised = []
        confused: Dict[str, int] = {}
        total = 0
        for i, held in enumerate(embedded):
            trial = type(self.memory).from_dict(self.memory.to_dict())
            others = [z for j, z in enumerate(embedded) if j != i]
            ids = np.concatenate([np.full(len(z), j) for j, z in enumerate(others)])
            trial.add_class(self.name, np.concatenate(others), demo_ids=ids)
            index = trial.classify_arrays(held)[0]
            own = trial.labels.index(self.name)
            mine = index == own
            if self.confident is not None:
                mine &= ~self.confident(held)
            recognised.append(has_run(mine, self.config.run_frames))
            taken = index[(index >= 0) & (index != own)]
            for c in taken:
                confused[trial.labels[int(c)]] = confused.get(trial.labels[int(c)], 0) + 1
            total += len(held)
        trial = type(self.memory).from_dict(self.memory.to_dict())
        ids = np.concatenate([np.full(len(z), j) for j, z in enumerate(embedded)])
        trial.add_class(self.name, np.concatenate(embedded), demo_ids=ids)
        rest_fires = False
        if self._rest:
            rest_z = self.embed(self._rest)
            rest_mine = trial.classify_arrays(rest_z)[0] == trial.labels.index(self.name)
            if self.confident is not None:
                rest_mine &= ~self.confident(rest_z)
            rest_fires = has_run(rest_mine, self.config.run_frames)
        hits = int(sum(recognised))
        reliable = hits / len(embedded) >= self.config.required_self_hits and not rest_fires
        # What really happened to the demonstrations, not how near prototypes lie: nearness warned on every registration
        top = max(confused, key=confused.get) if confused else ""
        if top and confused[top] < CONFUSED_SHARE * total:
            top = ""
        return {"demos": len(embedded), "recognised": hits, "reliable": bool(reliable), "rest_fires": bool(rest_fires),
                "conflicts": [], "confused_with": top, "event": EVENT_CLASS_ADDED}

    # ---------------------------------------------------------------- the person decides
    def approve(self, path: Any = None) -> Dict[str, Any]:
        """Adds the class and keeps the demonstrations. Prototypes only: `class_added`, not a neural update."""
        if self.state != "review":
            raise RegistrationError("확인할 시연이 없어요")
        ids = np.concatenate([np.full(len(z), j) for j, z in enumerate(self._embedded)])
        entry = self.memory.add_class(self.name, np.concatenate(self._embedded), demo_ids=ids, source=USER_SOURCE,
                                      extra={"event": EVENT_CLASS_ADDED, "session": self.session})
        for i, demo in enumerate(self.demos):
            self.store.add_frames(demo["frames"], label=self.name, label_source=USER_CONFIRMED, session=self.session, approved=True,
                                  task=self.name, extra={"kind": "do", "context_frames": demo["context"], "rep": i, "reason": "registration"},
                                  **self.meta)
        if path is not None:
            self.memory.save(path)
        self.state = "done"
        return {"event": EVENT_CLASS_ADDED, "label": entry.label, "prototypes": len(entry.prototypes), "neural_weights_changed": False}

    def cancel(self) -> None:
        self.state, self.demos, self._rest = "cancelled", [], []
