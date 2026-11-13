"""Host Feature adapter for the original a700452 custom-gesture detectors."""
from __future__ import annotations

import hashlib
import math
from types import SimpleNamespace

from manux.core.contracts import Activity, Intent, OutputTarget
from manux.compat.hand_mouse.action_bindings import CompositeGestureDetector, DEFAULT_GESTURE_BINDINGS
from manux.compat.hand_mouse.close_sign import CloseSign
from manux.compat.hand_mouse.gesture_engine import GestureEngine
from manux.compat.hand_mouse.motion_mode import MotionMode
from manux.compat.hand_mouse.path_gesture import PathDetector, PathStore
from manux.compat.hand_mouse.sequence import SequenceDetector, SequenceStore
from manux.compat.hand_mouse.draw_mode import ShapeStore, DrawMode, ensure_bindings
from manux.compat.hand_mouse.pose_probe import hand_posture, set_hand_label
from manux.compat.hand_mouse.virtual_hand import palm_centre, hand_px, DEFAULT_BOX

GESTURES, DEFAULT_BINDINGS, SETTINGS = (), {}, {}


def gesture_id(key):
    return "custom_gestures.g" + hashlib.sha256(key.encode()).hexdigest()[:20]


def landmarks(hand):
    return [SimpleNamespace(x=p[0], y=p[1], z=p[2]) for p in hand.landmarks] if hand else None


class CustomGestureFeature:
    id = "custom_gestures"
    modes = frozenset({"pointer", "pinch", "motion"})

    def __init__(self, registry, extra_keys=(), *, draw_enabled=False):
        self.registry = registry
        self.extra_keys = frozenset(extra_keys)
        self.seq_store, self.path_store, self.shapes = SequenceStore(), PathStore(), ShapeStore()
        self.draw = DrawMode(self.shapes)
        wants_draw = bool(draw_enabled)
        if wants_draw:
            ensure_bindings(registry, self.shapes)
        self.keys = tuple(dict.fromkeys((*registry.composite_gestures, *registry.custom_motions, *self.extra_keys,
                                        *self.seq_store.items, *self.path_store.items,
                                        *(self.shapes.templates() if wants_draw else ()))))
        self.gestures = tuple(gesture_id(key) for key in self.keys)
        self.reset("settings")

    def reset(self, reason):
        if hasattr(self, "draw"):
            self.draw.reset()
        self.composites = CompositeGestureDetector(self.registry)
        self.sequences, self.paths, self.motion = SequenceDetector(), PathDetector(), MotionMode()
        self.sign_motion, self.close_sign = MotionMode(), CloseSign()
        self.palm_engine = GestureEngine() if "thumb_index_middle" in self.extra_keys else None
        self.triple_on = False
        self.last_mode = None
        self.last_time = None
        self.fired = False

    def activity(self):
        return Activity()

    def step(self, hand, ctx):
        now, mode = ctx.frame.t, ctx.active_mode
        self.fired = False
        if ctx.output is OutputTarget.NONE:
            self.reset("pause")
            return ()
        if self.last_mode != mode or (self.last_time is not None and not 0 <= now-self.last_time <= .3):
            self.reset("mode_or_gap")
        self.last_mode, self.last_time = mode, now
        lm = landmarks(hand)
        if self.draw.active:
            hit = (self.draw.step(now, lm, DEFAULT_BOX, ctx.frame.width, ctx.frame.height)
                   if lm else self.draw.lost(now))
            if hit and hit.get("ok") and hit.get("key") in self.keys:
                return (Intent(now, self.id, gesture_id(hit["key"]), "tap"),)
            return ()
        in_motion = mode == "motion"
        sequences, paths = self.seq_store.active(in_motion), self.path_store.active(in_motion)
        aspect = ctx.frame.aspect
        set_hand_label(hand.handedness if hand else "", "")
        posture = hand_posture(lm, aspect) if lm else None
        # Preserve an already held pinch/drag; an unrelated pose must still reach the source classifiers.
        if not self.triple_on and posture in ("thumb_index", "thumb_middle", "thumb_index_middle") and any(
                a.holding for k, a in ctx.others.items() if k in ("pinch", "pointer", "output")):
            self.composites.reset()
            self.sequences.reset()
            self.paths.reset()
            return ()
        keys = []
        spans = []
        if "rock_sign" in self.extra_keys and self.registry.get_binding('rock_sign').get('enabled', True):
            event = self.close_sign.update(now, lm)
            if event and event.kind == "close":
                keys.append("rock_sign")
        if "thumbs_up" in self.extra_keys and not in_motion:
            event = self.sign_motion.update(now, lm)
            if event and event.kind == "enter":
                self.sign_motion.update(now, None, request="off")
                keys.append("thumbs_up")
        if self.palm_engine is not None:
            result = self.palm_engine.process(lm, timestamp=now)
            triple = result.active_gesture == "thumb_index_middle_pinch"
            if triple or self.triple_on:
                phase = "hold" if triple and self.triple_on else "begin" if triple else "end"
                spans.append(Intent(now, self.id, gesture_id("thumb_index_middle"), phase))
            self.triple_on = triple
        hit = self.composites.step(now, lm, "", set(), aspect,
                                   scope="motion" if in_motion else "normal", posture=posture)
        if hit:
            keys.append(hit["key"])
        if lm:
            hit = self.sequences.step(now, posture, sequences)
            if hit:
                keys.append(hit)
            pc = palm_centre(lm)
            size = hand_px(lm, 1.0, aspect)
            tip = 4 if posture == "thumbs_up" else 9
            angle = math.degrees(math.atan2(-(lm[tip].y-lm[0].y)*aspect, lm[tip].x-lm[0].x))
            hit = self.paths.step(now, posture, (pc[0], pc[1]*aspect), size, paths, self.shapes, angle)
        else:
            self.sequences.lost(now)
            hit = self.paths.step(now, None, None, 0., paths, self.shapes)
        if hit:
            keys.append(hit)
        if in_motion and (self.registry.custom_motions or "swipe_down" in self.extra_keys):
            if not self.motion.active:
                self.motion.update(now, lm, request="on")
            event = self.motion.update(now, lm)
            if event and event.kind == "swipe" and event.action in self.registry.custom_motions:
                keys.append(event.action)
            elif event and event.kind == "swipe" and event.action == "window_down" and "swipe_down" in self.extra_keys:
                keys.append("swipe_down")
        self.fired = bool(keys)
        return tuple(spans) + tuple(Intent(now, self.id, gesture_id(key), "tap") for key in dict.fromkeys(keys))


class CustomGestureFilter:
    """A source custom command wins over a competing default tap on the same frame."""
    id = "custom_gestures"

    def __init__(self, feature):
        self.feature = feature

    def observe(self, hand, ctx):
        pass

    def reset(self, reason):
        pass

    def filter(self, intents, ctx):
        if self.feature.draw.active:
            return tuple(i for i in intents if i.source == "custom_gestures" or i.phase == "end")
        if any(i.source == "custom_gestures" for i in intents):
            return tuple(i for i in intents if i.source == "custom_gestures" or i.phase == "end"
                         or i.gesture.endswith(".move") or i.gesture.endswith(".move_fist"))
        return intents
