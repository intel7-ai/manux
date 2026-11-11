"""Rule-only observations during guided capture. These detectors never send OS input."""
from src.close_sign import CloseSign
from src.toggle_sign import ToggleSign
from src.motion_mode import MotionMode, hand_features
from src.gesture_catalog import BY_KEY, MOTION_TARGETS


def _is_target_pinch_posture(lm, label: str) -> bool:
    """Target-centric pinch contact check during guided `do` capture (ignores competing fingers)."""
    if lm is None or len(lm) < 21:
        return False
    try:
        pts = [
            (
                float(p.x if hasattr(p, "x") else p[0]),
                float(p.y if hasattr(p, "y") else p[1]),
            )
            for p in lm[:21]
        ]
    except (TypeError, ValueError, IndexError, AttributeError):
        return False

    def d2(i: int, j: int) -> float:
        dx = pts[i][0] - pts[j][0]
        dy = pts[i][1] - pts[j][1]
        return float((dx * dx + dy * dy) ** 0.5)

    hs = max(d2(0, 5), d2(0, 9), d2(0, 17), 1.4 * d2(5, 17), 1e-4)
    if label == "thumb_index":
        return min(d2(4, 8), d2(4, 7), d2(3, 8)) <= max(0.072, 0.40 * hs)
    if label == "thumb_middle":
        return min(d2(4, 12), d2(4, 11), d2(3, 12)) <= max(0.072, 0.40 * hs)
    if label == "thumb_ring":
        return min(d2(4, 16), d2(4, 15), d2(3, 16)) <= max(0.072, 0.40 * hs)
    if label == "thumb_pinky":
        return min(d2(4, 20), d2(4, 19), d2(3, 20)) <= max(0.075, 0.42 * hs)
    if label == "thumb_index_middle":
        di = min(d2(4, 8), d2(4, 7), d2(3, 8))
        dm = min(d2(4, 12), d2(4, 11), d2(3, 12))
        thr = max(0.075, 0.42 * hs)
        return di <= thr and dm <= thr
    return False


def _is_target_sign_posture(lm, label: str, toggle: ToggleSign, close: CloseSign) -> bool:
    """Target-centric static sign posture check during guided `do` capture."""
    if lm is None:
        return False
    feats = hand_features(lm)
    if feats is None:
        return False
    i, m, r, p = feats["curl"]
    if label == "v_sign":
        if toggle.pose(feats):
            return True
        # Target-centric V-sign posture: index & middle extended above folded ring & pinky
        return i >= 1.22 and m >= 1.22 and r <= 1.28 and p <= 1.28 and (min(i, m) - max(r, p)) >= 0.16
    if label == "rock_sign":
        if close.pose(feats):
            return True
        return i >= 1.18 and p >= 1.10 and m <= 1.28 and r <= 1.28 and (min(i, p) - max(m, r)) >= 0.15
    if label == "thumbs_up":
        return bool(feats.get("thumb_up")) or (max(i, m, r, p) <= 1.28)
    if label == "palm_side":
        return min(i, m, r, p) >= 1.25
    return False


def detect_interfering_signals(lm, result, target_key: str | None) -> list[str]:
    """Return competing gesture keys whose raw signal or pinch proximity co-occurs with `target_key`."""
    if not target_key:
        return []
    interfering: list[str] = []
    raw_action = getattr(result, "action", None)
    raw_gesture = getattr(result, "active_gesture", "") or ""
    for t in BY_KEY.values():
        if t.key == target_key or t.free or t.label == "none":
            continue
        if t.action and raw_action == t.action:
            if raw_action == "drag_hold" and ((t.key == "thumb_index_middle") != (raw_gesture == "thumb_index_middle_pinch")):
                continue
            if t.key not in interfering:
                interfering.append(t.key)
        elif raw_gesture == f"{t.label}_pinch" and t.key not in interfering:
            interfering.append(t.key)
    if lm is not None:
        for pinch_key in ("thumb_index", "thumb_middle", "thumb_ring", "thumb_pinky", "thumb_index_middle"):
            if pinch_key != target_key and pinch_key not in interfering and _is_target_pinch_posture(lm, pinch_key):
                interfering.append(pinch_key)
    return interfering


class CaptureRules:
    def __init__(self, config):
        self.config = config
        self.token = None

    def observe(self, now, hand, result, label):
        token = (label.get("task"), label.get("set"), label.get("rep"), label.get("phase") == "do")
        task = BY_KEY.get(label.get("task"))
        lm = hand.landmarks if hand else None
        if token != self.token:
            self.token = token
            self.close = CloseSign(self.config)
            self.toggle = ToggleSign(self.config)
            self.motion = MotionMode(self.config)
            if task and task.direction:
                self.motion.update(now, lm, request="on")
        phase = label.get("phase")
        if phase not in ("prep", "do", "rest", "free"):
            return []

        # Guided capture of a specific target gesture: judge ONLY around the scheduled target command,
        # never injecting interfering signals from other fingers into the recording.
        if task is not None and not task.free and task.label != "none":
            if phase != "do":
                return []
            action = getattr(result, "action", None)
            gesture = getattr(result, "active_gesture", "")
            tgt_key = task.key
            tgt_lbl = task.label

            if tgt_lbl == "v_sign":
                ev = self.toggle.update(now, lm)
                if (
                    ev is not None
                    or bool(self.toggle.last_pose)
                    or self.toggle.holding(now, settle=0.05)
                    or _is_target_sign_posture(lm, "v_sign", self.toggle, self.close)
                ):
                    return [tgt_key]
                return []

            if tgt_lbl == "rock_sign":
                ev = self.close.update(now, lm)
                if (
                    (ev and ev.kind in ("ask", "close"))
                    or bool(self.close.last_pose)
                    or self.close.holding(now, settle=0.05)
                    or _is_target_sign_posture(lm, "rock_sign", self.toggle, self.close)
                ):
                    return [tgt_key]
                return []

            if tgt_lbl == "thumbs_up":
                ev = self.motion.update(now, lm)
                if (
                    (ev and ev.kind in ("enter", "exit") and ev.info.get("why") not in ("timeout", "lost"))
                    or bool(getattr(self.motion, "active", False))
                    or _is_target_sign_posture(lm, "thumbs_up", self.toggle, self.close)
                ):
                    return [tgt_key]
                return []

            if tgt_lbl == "palm_side":
                if (
                    (task.action and action == task.action)
                    or gesture in ("palm_side", "thumb_tucked_open")
                    or _is_target_sign_posture(lm, "palm_side", self.toggle, self.close)
                ):
                    return [tgt_key]
                return []

            if task.direction:
                ev = self.motion.update(now, lm)
                if ev:
                    if ev.kind == "close_ask" and tgt_key == "swipe_down":
                        return [tgt_key]
                    if ev.kind == "swipe" and MOTION_TARGETS.get(ev.action) == tgt_key:
                        return [tgt_key]
                return []

            if tgt_lbl in ("thumb_index", "thumb_middle", "thumb_ring", "thumb_pinky", "thumb_index_middle"):
                matched_action = bool(task.action and action == task.action)
                if matched_action and action == "drag_hold":
                    matched_action = (tgt_key == "thumb_index_middle") == (gesture == "thumb_index_middle_pinch")
                if (
                    matched_action
                    or gesture == f"{tgt_lbl}_pinch"
                    or _is_target_pinch_posture(lm, tgt_lbl)
                ):
                    return [tgt_key]
                return []

        targets = []
        action = getattr(result, "action", None)
        gesture = getattr(result, "active_gesture", "")
        for t in BY_KEY.values():
            if t.action and action == t.action:
                if action == "drag_hold" and ((t.key == "thumb_index_middle") != (gesture == "thumb_index_middle_pinch")):
                    continue
                targets.append(t.key)
            elif phase == "do" and t.key in ("thumb_middle", "thumb_ring", "thumb_pinky", "thumb_index_middle") and gesture == f"{t.key}_pinch":
                targets.append(t.key)
        if self.toggle.update(now, lm):
            targets.append("v_sign")
        event = self.close.update(now, lm)
        if event and event.kind in ("ask", "close"):
            targets.append("rock_sign")
        event = self.motion.update(now, lm)
        if event:
            if event.kind in ("enter", "exit") and event.info.get("why") not in ("timeout", "lost"):
                targets.append("thumbs_up")
            elif event.kind == "close_ask":
                targets.append("swipe_down")
            elif event.kind == "swipe" and event.action in MOTION_TARGETS:
                targets.append(MOTION_TARGETS[event.action])
        return targets
