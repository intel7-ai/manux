"""Original camera guides, with state isolated from the input-producing pinch feature."""
from manux.features.pinch.engine import PinchCore, palm_anchor_point
from manux.features.pinch.settings import resolve


TIPS = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}
ACTION_FINGER = {"left_click": "middle", "right_click": "ring", "drag_hold": "middle",
                 "task_switcher": "pinky", "scroll_grab": "index"}


def pinch_guides(frame, config, pinched):
    """v0.3 draw_hand guide states; visual feedback, never an input command."""
    if frame.active_gesture in ("fist", "thumb_tucked_open"):
        return ()
    fired = ACTION_FINGER.get(frame.action)
    guides = tuple((TIPS[finger], "action" if finger == fired and frame.action != "scroll_grab" else "pinched")
                   for finger in TIPS if finger in pinched or finger == fired)
    if guides:
        return guides
    best, ratio = None, 1.8
    for finger, key in zip(TIPS, ("pinch_threshold", "click_threshold", "ring_click_threshold", "pinky_click_threshold")):
        distance, threshold = frame.distances.get(finger), config[key]
        if distance is not None and threshold > 0 and distance / threshold <= ratio:
            best, ratio = TIPS[finger], distance / threshold
    return ((best, "near"),) if best is not None else ()


class PinchPreview:
    """No mapper, intents, router or sink. Running it while paused cannot enable control."""
    def __init__(self):
        self.core = PinchCore()
        self.previous = None

    def step(self, hand, settings):
        if hand is None:
            self.core = PinchCore()
            self.previous = None
            return (), None, False
        config = resolve(settings)
        identity = (hand.handedness, config)
        if self.previous != identity:
            self.core = PinchCore()
            self.previous = identity
        frame = self.core.step(hand.frame.t, hand.landmarks, config)
        return (pinch_guides(frame, config, self.core.pinched_fingers),
                palm_anchor_point(hand.landmarks, config["cursor_anchor_ratio"]), frame.is_moving)
