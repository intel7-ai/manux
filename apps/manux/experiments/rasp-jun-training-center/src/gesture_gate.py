"""Per-motion TCN decisions and conservative 3D click recovery.

Rule clicks stay immediate. Applied models can recover a missed click from a
fresh, exclusive 3D contact. Other actions keep their rule state machines.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np

from src.tcn_features import MAX_FRAME_GAP, temporal_inputs
from src.gesture_catalog import BY_KEY, NAMES
from src.tcn_policy import mode_for

DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "models" / "gesture_tcn.onnx"
GATED = {"right_click": "thumb_ring", "left_click": "thumb_middle", "scroll_grab": "thumb_index",
         "task_switcher": "thumb_pinky", "show_desktop": "palm_side"}
CLICKS = {"thumb_middle": ("left_click", 1), "thumb_ring": ("right_click", 2)}


def load_predictor(model_path=DEFAULT_MODEL):
    from src.tcn_models import load_predictor as load
    return load(model_path)


class GestureGate:
    def __init__(self, predict, info, config=None, policies=None):
        cfg = config or {}
        self.info, self.config = info, cfg
        self.policies = policies or {}
        self.predict = predict
        self.classes = list(info["classes"])
        self.window = int(info["window"])
        self.threshold = float(cfg.get("gate_threshold", info.get("threshold", .7)))
        self.k = int(cfg.get("gate_frames", info.get("frames_in_a_row", 3)))
        self.decisions = {label: (float(cfg.get("gate_threshold", values["threshold"])),
                                  int(cfg.get("gate_frames", values["frames_in_a_row"])))
                          for label, values in info.get("class_decisions", {}).items()}
        self.lookback = float(cfg.get("gate_lookback", .5))
        self.wait = float(cfg.get("gate_wait", .3))
        self._frames = deque(maxlen=self.window + 1)
        self._probs = deque(maxlen=60)
        self.last = self.features = None
        self.valid = False
        self._pending = self._continuous = None
        self._clicks = {}
        self.blocked = self.passed = self.assisted = 0
        self.last_note = ""
        self.last_decision = {}
        self._last_filter_t = None

    def mode(self, task):
        return mode_for(task, self.info, self.policies)

    def reset(self):
        self.last_note = ""
        self._frames.clear()
        self._probs.clear()
        self._pending = self._continuous = None
        self._clicks.clear()
        self.last = self.features = None
        self.valid = False
        self.last_decision = {}
        self._last_filter_t = None

    def step(self, now, hand, aspect):
        if self._frames and not 0 < now - self._frames[-1]["t"] <= MAX_FRAME_GAP:
            self.reset()
        frame = {"t": now}
        if hand is not None and getattr(hand, "world", None) is not None:
            frame.update(world=hand.world, lm=[[p.x, p.y, p.z] for p in hand.landmarks])
        self._frames.append(frame)
        feats, x, _ = temporal_inputs(list(self._frames), aspect, self.window)
        return self.observe(now, self.predict(x[-1:]), feats[-1])

    def observe(self, now, probabilities, features):
        """Offline evaluation supplies the same features/probabilities through here."""
        if self._probs and not 0 < now - self._probs[-1][0] <= MAX_FRAME_GAP:
            self.reset()
        self.features = np.asarray(features)
        self.valid = bool(self.features[-1])
        self.last = np.asarray(probabilities).copy()
        self.last_decision = {}
        if not self.valid:
            self.last[:] = 0
            self.last[self.classes.index("none")] = 1
            self._probs.clear()
            self._pending = self._continuous = None
            self._clicks.clear()
        self._probs.append((now, self.last))
        return self.last

    def confirmed(self, name, now, *, current=False, threshold=None, frames=None):
        if name not in self.classes or not self.valid:
            return False
        c = self.classes.index(name)
        thr, k = self.decisions.get(name, (self.threshold, self.k))
        thr = thr if threshold is None else threshold
        k = k if frames is None else frames
        run, previous, found = 0, None, False
        for t, p in self._probs:
            if not 0 <= now - t <= self.lookback:
                continue
            if previous is not None and not 0 < t - previous <= MAX_FRAME_GAP:
                run = 0
            run = run + 1 if p[c] >= thr else 0
            found = found or run >= k
            previous = t
        return run >= k if current else found

    def allows(self, name, now):
        label = BY_KEY[name].label if name in BY_KEY else name
        confirmed = self.confirmed(label, now)
        mode = self.mode(name)
        self.last_decision[name] = {"mode": mode, "confirmed": confirmed}
        return mode != "apply" or label not in self.classes or confirmed

    def _click(self, now, result):
        """Strong model evidence plus 3D contact; rearm only after a visible release."""
        action = getattr(result, "action", None)
        distances = self.features[126:130] if self.valid and self.features is not None else None
        for task, (kind, finger) in CLICKS.items():
            state = self._clicks.setdefault(task, {"armed": False, "open": None, "contact": None, "assisted": False})
            mode = self.mode(task)
            if distances is None:
                continue
            d = float(distances[finger])
            if d > .45:
                # A brief release may be a genuine fast second rule click. Only
                # rearming an *assisted* click needs the longer open dwell.
                state["assisted"] = False
                if state["open"] is None:
                    state["open"] = now
                if now - state["open"] >= .08:
                    state.update(armed=True, assisted=False)
            else:
                state["open"] = None
            if action == kind:
                if mode == "apply" and state["assisted"]:
                    result.action, result.is_moving = "none", False
                    self.last_decision[task] = {"mode": mode, "reason": "duplicate_after_assist"}
                else:
                    self.last_decision[task] = {"mode": mode, "reason": "rule_click",
                                                "confirmed": self.confirmed(task, now)}
                    self.passed += 1
                state["armed"] = False
                continue
            mapping = self.config.get("gestures", {}).get(task + "_pinch", {})
            enabled = mapping.get("enabled", True) and mapping.get("action", kind) == kind
            exclusive = min(float(distances[j]) for j in range(4) if j != finger) > d + .08
            if d <= .30 and exclusive:
                if state["contact"] is None:
                    state["contact"] = now
            else:
                state["contact"] = None
            supports_assist = self.info.get("class_pipeline_versions", {}).get(task, self.info.get("pipeline_version")) == "continuous-v2"
            candidate = (supports_assist and enabled and state["armed"] and d <= .30 and exclusive
                         and state["contact"] is not None and now - state["contact"] >= .03
                         and self.confirmed(task, now, current=True, threshold=.90, frames=2)
                         and np.linalg.norm(self.features[130:132]) < .8
                         and action in (None, "none", "move_cursor"))
            if candidate and mode != "rules":
                state["armed"] = False
                self.last_decision[task] = {"mode": mode, "reason": "3d_click_candidate", "distance": round(d, 3)}
                if mode == "apply":
                    state["assisted"] = True
                    result.action, result.is_moving = kind, False
                    result.active_gesture = task + "_pinch"
                    self.assisted += 1
                    self.last_note = "TCN 3D 보완: " + NAMES[task]
                    break
        return result

    def filter(self, now, result):
        if self._last_filter_t is not None and not 0 <= now - self._last_filter_t <= MAX_FRAME_GAP:
            self._clicks.clear()
            self._pending = self._continuous = None
        self._last_filter_t = now
        action = getattr(result, "action", None)
        task = GATED.get(action)
        if action == "drag_hold":
            task = "thumb_index_middle" if getattr(result, "active_gesture", "") == "thumb_index_middle_pinch" else "thumb_middle_drag"
        label = BY_KEY[task].label if task in BY_KEY else None
        continuous = action in ("scroll_grab", "drag_hold")
        if self._continuous != (action, label):
            self._continuous = None
        if action not in (None, "none", "move_cursor"):
            self._pending = None
        if action in ("left_click", "right_click"):
            return self._click(now, result)
        if label in self.classes:
            mode = self.mode(task)
            confirmed = self.confirmed(label, now)
            self.last_decision[task] = {"mode": mode, "confirmed": confirmed}
            if mode != "apply" or self._continuous == (action, label) or confirmed:
                if continuous:
                    self._continuous = (action, label)
                self.passed += 1
                return self._click(now, result)
            if not continuous:
                self._pending = (action, label, now)
            result.action, result.is_moving = "none", False
            self.last_note = "TCN 확인 대기: " + NAMES.get(task, task)
            return result
        if self._pending is not None:
            kind, pending_label, since = self._pending
            if now - since > self.wait:
                self._pending = None
                self.blocked += 1
            elif self.confirmed(pending_label, now):
                self._pending = None
                self.passed += 1
                if action in ("none", "move_cursor", None):
                    result.action = kind
        return self._click(now, result)

    def diagnostics(self):
        return {"valid": self.valid, "scores": {c: round(float(self.last[i]), 4) for i, c in enumerate(self.classes)}
                if self.last is not None else {}, "decisions": dict(self.last_decision),
                "assisted": self.assisted, "blocked": self.blocked}
