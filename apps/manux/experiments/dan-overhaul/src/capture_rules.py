"""Rule-only observations during guided capture. These detectors never send OS input."""
from src.close_sign import CloseSign
from src.toggle_sign import ToggleSign
from src.motion_mode import MotionMode
from src.gesture_catalog import BY_KEY, MOTION_TARGETS


class CaptureRules:
    def __init__(self, config):
        self.config = config
        self.token = None

    def observe(self, now, hand, result, label):
        token = (label.get("task"), label.get("set"))
        task = BY_KEY.get(label.get("task"))
        lm = hand.landmarks if hand else None
        if token != self.token:
            self.token = token
            self.close = CloseSign(self.config)
            self.toggle = ToggleSign(self.config)
            self.motion = MotionMode(self.config)
            if task and task.direction:
                self.motion.update(now, lm, request="on")
        if label.get("phase") not in ("prep", "do", "rest", "free"):
            return []
        targets = []
        action = getattr(result, "action", None)
        gesture = getattr(result, "active_gesture", "")
        for t in BY_KEY.values():
            if t.action and action == t.action:
                if action == "drag_hold" and ((t.key == "thumb_index_middle") != (gesture == "thumb_index_middle_pinch")):
                    continue
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
