"""Spatial Pose Assist feature & guard (hybrid 96-dim Spatial MLP integration).

Provides two capabilities in one package:
1. `SpatialPoseGuard` (IntentFilter): Protects against false-positive clicks/gestures.
   When the heuristic rule triggers a click (e.g. right-click thumb_ring) but the Spatial MLP
   is confident (>= tau_veto) that the hand is resting/moving ('none'), the click is vetoed.
2. `SpatialPoseFeature` (Feature): Optional direct pose trigger.
   When enabled or in hybrid mode, confident pinch classifications can directly generate intents.
3. Zero-restart Hot-Reloading: When training finishes in PoseSnapshotStudio, the active model
   is swapped atomically on the live engine without restarting the app.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np

from manux.core.contracts import Activity, Feature, FrameContext, HandObservation, Intent, IntentFilter
from manux.ml.spatial_mlp import SpatialMlpModel
from manux.perception.spatial_features import extract_spatial_features
from manux.perception.pose_angles import observation_pose_angles

FEATURE_ID = "spatial_pose"
GUARD_ID = "spatial_pose_guard"

# Mapping from Studio class names to preview engine gesture IDs
CLASS_TO_GESTURE: Mapping[str, str] = {
    "none": "pointer.move",
    "thumb_middle": "pinch.thumb_middle",
    "thumb_ring": "pinch.thumb_ring",
    "thumb_index": "pinch.scroll",
    "thumb_pinky": "pinch.thumb_pinky",
    "palm_side": "pinch.thumb_tucked_open",
    "thumbs_up": "motion.enter",
    "v_sign": "control.v_sign",
    "thumb_middle_ring": "pinch.thumb_middle_ring",
    "rock_sign": "spatial_pose.rock_sign",
}

DEFAULT_BINDINGS = {"spatial_pose.rock_sign": "window.close"}
GESTURE_PHASES = {"spatial_pose.rock_sign": frozenset({"tap"})}

GESTURE_TO_CLASS: Mapping[str, str] = {v: k for k, v in CLASS_TO_GESTURE.items()}
GESTURE_TO_CLASS = {**GESTURE_TO_CLASS, "pointer.left_click": "thumb_middle", "pointer.right_click": "thumb_ring"}
BOTH_CLICK_GESTURES = frozenset({"pinch.thumb_middle", "pinch.thumb_ring", "pointer.left_click", "pointer.right_click"})
DEFAULT_VETO_GESTURES = BOTH_CLICK_GESTURES
BOOST_CLASSES = frozenset({"thumb_middle", "thumb_ring"})


@dataclass(frozen=True)
class SpatialPoseEvidence:
    """Frame evidence from the Spatial MLP."""
    action: str
    confidence: float
    probabilities: Mapping[str, float]
    t: float


class SpatialPoseFeature(Feature):
    """Generates evidence from 96-dim Spatial MLP and optionally triggers gestures."""

    def __init__(self, model: Optional[SpatialMlpModel] = None, *, mode: str = "assist",
                 trigger_confidence: float = 0.88) -> None:
        self._model = model
        self._mode = mode  # "assist" (evidence only for guard) or "direct" (triggers intents)
        self._trigger_confidence = trigger_confidence
        self._latest_evidence: Optional[SpatialPoseEvidence] = None
        self._active_gesture: Optional[str] = None
        self._consecutive_direct = 0

    @property
    def id(self) -> str:
        return FEATURE_ID

    @property
    def gestures(self) -> Sequence[str]:
        return tuple(CLASS_TO_GESTURE.values())

    @property
    def model(self) -> Optional[SpatialMlpModel]:
        return self._model

    def set_model(self, model: Optional[SpatialMlpModel]) -> None:
        self._model = model
        self._latest_evidence = None
        self._active_gesture = None
        self._consecutive_direct = 0

    @property
    def evidence(self) -> Optional[SpatialPoseEvidence]:
        return self._latest_evidence

    def step(self, hand: Optional[HandObservation], ctx: FrameContext) -> Sequence[Intent]:
        self._latest_evidence = None
        if hand is None or hand.landmarks is None or self._model is None:
            if self._active_gesture is not None and self._active_gesture != "spatial_pose.rock_sign":
                g = self._active_gesture
                self._active_gesture = None
                return [Intent(ctx.frame.t, FEATURE_ID, g, "end")]
            return ()

        feats = (observation_pose_angles(hand) if self._model.feature_version == "pose-angles-v1"
                 else extract_spatial_features(hand.landmarks))
        if feats is None:
            if self._active_gesture is not None and self._active_gesture != "spatial_pose.rock_sign":
                g = self._active_gesture
                self._active_gesture = None
                return [Intent(ctx.frame.t, FEATURE_ID, g, "end")]
            return ()

        pred = self._model.predict(feats)
        act = pred["action"]
        conf = float(pred["confidence"])
        now = ctx.frame.t
        self._latest_evidence = SpatialPoseEvidence(
            action=act,
            confidence=conf,
            probabilities=dict(pred.get("probabilities", {})),
            t=now,
        )

        # In assist-only mode, this feature never directly issues intents (the guard handles vetoing)
        if self._mode != "direct":
            return ()

        # The timed control recognizer consumes V evidence; direct pose mode cannot bypass its hold.
        target_gesture = None if act == "v_sign" else CLASS_TO_GESTURE.get(act)
        intents: List[Intent] = []
        if target_gesture == "spatial_pose.rock_sign" and conf >= self._trigger_confidence:
            if self._active_gesture != target_gesture:
                intents.append(Intent(now, FEATURE_ID, target_gesture, "tap"))
            self._active_gesture = target_gesture
            return tuple(intents)
        if target_gesture and target_gesture != "pointer.move" and conf >= self._trigger_confidence:
            if self._active_gesture == target_gesture:
                intents.append(Intent(now, FEATURE_ID, target_gesture, "hold"))
            else:
                if self._active_gesture is not None and self._active_gesture != "spatial_pose.rock_sign":
                    intents.append(Intent(now, FEATURE_ID, self._active_gesture, "end"))
                intents.append(Intent(now, FEATURE_ID, target_gesture, "begin"))
                self._active_gesture = target_gesture
        else:
            if self._active_gesture is not None and self._active_gesture != "spatial_pose.rock_sign":
                intents.append(Intent(now, FEATURE_ID, self._active_gesture, "end"))
                self._active_gesture = None

        return tuple(intents)

    def activity(self) -> Activity:
        return Activity(holding=self._active_gesture is not None)

    def reset(self, reason: str) -> None:
        self._active_gesture = None
        self._consecutive_direct = 0
        self._latest_evidence = None


class SpatialPoseGuard(IntentFilter):
    """Protects against heuristic misclassifications by consulting SpatialPoseFeature evidence."""

    def __init__(self, feature: SpatialPoseFeature, *, tau_veto: float = 0.80, k_frames: int = 2,
                 enabled: bool = True, boost: bool = True, tau_boost: float = 0.93, k_boost: int = 3) -> None:
        self.feature = feature
        self.tau_veto = tau_veto
        self.k_frames = k_frames
        self.enabled = enabled
        self.boost = boost
        self.tau_boost = tau_boost
        self.k_boost = k_boost
        self.vetoed_count = 0
        self.passed_count = 0
        self.boosted_count = 0
        self._boost_class: Optional[str] = None
        self._boost_run = 0
        self._boost_fast = False
        self._boost_done = False
        self._boost_t = float("-inf")
        self._idle_run = 0
        self._events: List[str] = []
        self._dragging: bool = False
        self._drag_last_point: Optional[Tuple[float, float]] = None
        self._drag_last_scale: Optional[float] = None
        self._scrolling: bool = False
        self._scroll_last_point: Optional[Tuple[float, float]] = None
        self._scroll_last_scale: Optional[float] = None
        self._scroll_run: int = 0

    @staticmethod
    def _circle_score(hand: Optional[HandObservation], cls: str) -> float:
        """Proximity score (0..1) of the thumb-to-finger pinch circle (1.0 when pinched, >0 when circle is near)."""
        if hand is None or hand.landmarks is None or len(hand.landmarks) < 17:
            return 0.0
        thumb = hand.landmarks[4]
        tip = hand.landmarks[12 if cls == "thumb_middle" else 16]
        thr = 0.052 if cls == "thumb_middle" else 0.044
        dist = math.hypot(thumb[0] - tip[0], thumb[1] - tip[1])
        if dist <= thr:
            return 1.0
        if dist >= thr * 1.6:
            return 0.0
        return max(0.0, min(1.0, 1.0 - (dist - thr) / (thr * 0.6)))

    @property
    def id(self) -> str:
        return GUARD_ID

    def observe(self, hand: Optional[HandObservation], ctx: FrameContext) -> None:
        if not self.enabled:
            self._idle_run = 0
            return
        ev = self.feature.evidence
        if ev is None or ev.t != ctx.frame.t:
            self.feature.step(hand, ctx)
            ev = self.feature.evidence

        if ev is not None and ev.action == "none" and ev.confidence >= self.tau_veto:
            self._idle_run += 1
        else:
            self._idle_run = 0

        if ev is not None and ev.action == "thumb_index" and ev.confidence >= 0.55:
            self._scroll_run += 1
        else:
            self._scroll_run = 0

        # boost: fuse physical pinch circle proximity with Spatial MLP confidence
        cls = None
        fast = False
        if ev is not None and ev.action in BOOST_CLASSES:
            c_score = self._circle_score(hand, ev.action)
            hybrid_conf = 0.45 * c_score + 0.55 * ev.confidence if c_score > 0.0 else ev.confidence
            if ev.confidence >= self.tau_boost or (c_score >= 0.35 and ev.confidence >= 0.68 and hybrid_conf >= 0.72):
                cls = ev.action
                fast = c_score >= 0.65 and ev.confidence >= 0.72
        if cls is None or cls != self._boost_class:
            self._boost_class = cls
            self._boost_run = 0
            self._boost_fast = False
            self._boost_done = False
        if cls is not None:
            self._boost_run += 1
            self._boost_fast = self._boost_fast or fast

    def filter(self, intents: Sequence[Intent], ctx: FrameContext) -> Sequence[Intent]:
        if not self.enabled or self.feature.model is None:
            return intents

        ev = self.feature.evidence
        if ev is None or ev.t != ctx.frame.t:
            return intents

        pose_active = (ev.action in ("thumb_middle", "thumb_middle_ring") and ev.confidence >= 0.55)
        scroll_active = (ev.action == "thumb_index" and ev.confidence >= 0.55)
        out: List[Intent] = []

        if not intents and self._scrolling:
            if scroll_active:
                out.append(Intent(ctx.frame.t, "pinch", "pinch.scroll", "hold",
                                  point=self._scroll_last_point, scale=self._scroll_last_scale))
                return tuple(out)
            else:
                out.append(Intent(ctx.frame.t, "pinch", "pinch.scroll", "end",
                                  point=self._scroll_last_point, scale=self._scroll_last_scale))
                self._scrolling = False
                self._events.append("spatial_guard:scroll_ended_on_release")
                return tuple(out)

        if not intents and self._dragging:
            if pose_active:
                out.append(Intent(ctx.frame.t, "pinch", "pinch.drag", "hold",
                                  point=self._drag_last_point, scale=self._drag_last_scale))
                return tuple(out)
            else:
                out.append(Intent(ctx.frame.t, "pinch", "pinch.drag", "end",
                                  point=self._drag_last_point, scale=self._drag_last_scale))
                self._dragging = False
                self._events.append("spatial_guard:drag_ended_on_release")
                return tuple(out)

        for intent in intents:
            # Handle scroll intents
            if intent.gesture == "pinch.scroll":
                if intent.phase in ("begin", "hold"):
                    self._scrolling = True
                    if intent.point is not None:
                        self._scroll_last_point = intent.point
                    if intent.scale is not None:
                        self._scroll_last_scale = intent.scale
                    out.append(intent)
                    continue
                elif intent.phase == "end":
                    if scroll_active:
                        sustained = Intent(
                            intent.t, intent.source, "pinch.scroll", "hold",
                            point=intent.point or self._scroll_last_point,
                            scale=intent.scale or self._scroll_last_scale,
                        )
                        self._scrolling = True
                        self._events.append("spatial_guard:scroll_sustained_on_end")
                        out.append(sustained)
                        continue
                    else:
                        self._scrolling = False
                        self._events.append("spatial_guard:scroll_ended")
                        out.append(intent)
                        continue

            # If scroll was active and MLP confirms pose is still held,
            # convert accidental fallback to move into scroll hold.
            if self._scrolling and intent.phase == "hold" and intent.gesture in ("pinch.move", "pinch.move_fist", "pointer.move"):
                if scroll_active:
                    scroll_intent = Intent(
                        intent.t, "pinch", "pinch.scroll", "hold",
                        point=intent.point or self._scroll_last_point,
                        scale=intent.scale or self._scroll_last_scale,
                    )
                    if intent.point is not None:
                        self._scroll_last_point = intent.point
                    self._events.append("spatial_guard:move_converted_to_scroll")
                    out.append(scroll_intent)
                    continue
                else:
                    out.append(Intent(
                        ctx.frame.t, "pinch", "pinch.scroll", "end",
                        point=self._scroll_last_point,
                        scale=self._scroll_last_scale,
                    ))
                    self._scrolling = False
                    self._events.append("spatial_guard:scroll_ended_on_release")

            # If scroll was NOT active yet (e.g. horizontal posture blocked by 2D rule),
            # but MLP detects thumb_index steadily (>= 2 frames), convert move into scroll begin!
            if not self._scrolling and not self._dragging and intent.phase == "hold" and intent.gesture in ("pinch.move", "pinch.move_fist", "pointer.move"):
                if scroll_active and self._scroll_run >= 2:
                    self._scrolling = True
                    self._scroll_last_point = intent.point
                    self._scroll_last_scale = intent.scale
                    self._events.append("spatial_guard:move_converted_to_scroll_begin")
                    out.append(Intent(
                        intent.t, "pinch", "pinch.scroll", "begin",
                        point=intent.point,
                        scale=intent.scale,
                    ))
                    continue

            # Handle drag intents
            if intent.gesture == "pinch.drag":
                if intent.phase in ("begin", "hold"):
                    self._dragging = True
                    if intent.point is not None:
                        self._drag_last_point = intent.point
                    if intent.scale is not None:
                        self._drag_last_scale = intent.scale
                    out.append(intent)
                    continue
                elif intent.phase == "end":
                    if pose_active:
                        sustained = Intent(
                            intent.t, intent.source, "pinch.drag", "hold",
                            point=intent.point or self._drag_last_point,
                            scale=intent.scale or self._drag_last_scale,
                        )
                        self._dragging = True
                        self._events.append("spatial_guard:drag_sustained_on_end")
                        out.append(sustained)
                        continue
                    else:
                        self._dragging = False
                        self._events.append("spatial_guard:drag_ended")
                        out.append(intent)
                        continue

            # If drag was active and MLP confirms pose is still held,
            # convert accidental fallback to move into drag hold.
            if self._dragging and intent.phase == "hold" and intent.gesture in ("pinch.move", "pinch.move_fist", "pointer.move"):
                if pose_active:
                    drag_intent = Intent(
                        intent.t, "pinch", "pinch.drag", "hold",
                        point=intent.point or self._drag_last_point,
                        scale=intent.scale or self._drag_last_scale,
                    )
                    if intent.point is not None:
                        self._drag_last_point = intent.point
                    self._events.append("spatial_guard:move_converted_to_drag")
                    out.append(drag_intent)
                    continue
                else:
                    out.append(Intent(
                        ctx.frame.t, "pinch", "pinch.drag", "end",
                        point=self._drag_last_point,
                        scale=self._drag_last_scale,
                    ))
                    self._dragging = False
                    self._events.append("spatial_guard:drag_ended_on_release")

            # A click the rules fire (or the MLP already fired) for the pose being held counts as done.
            if intent.phase == "tap" and intent.gesture in BOTH_CLICK_GESTURES:
                if (self._boost_class is not None and self._boost_done
                        and GESTURE_TO_CLASS.get(intent.gesture) == self._boost_class):
                    continue          # one click per pose episode (the MLP already clicked for it)
                if GESTURE_TO_CLASS.get(intent.gesture) == self._boost_class:
                    self._boost_done = True
                self._boost_t = ctx.frame.t     # a click just happened: the boost stays quiet for a while
            # Mode arbiter transitions are protected and never vetoed
            if "." in intent.gesture and intent.gesture.split(".", 1)[1] in ("enter", "exit"):
                out.append(intent)
                continue

            # Check if this click or pinch intent should be vetoed
            if intent.phase in ("tap", "begin") and intent.gesture in DEFAULT_VETO_GESTURES:
                # If MLP is confident that hand is 'none' (idle/moving) for k consecutive frames
                if self._idle_run >= self.k_frames:
                    self.vetoed_count += 1
                    self._events.append(f"spatial_guard:vetoed:{intent.gesture}:{intent.phase}")
                    continue

                # If MLP is confident that hand is doing a completely different explicit pinch
                if ev is not None and ev.confidence >= self.tau_veto and ev.action != "none":
                    expected_gesture = CLASS_TO_GESTURE.get(ev.action)
                    # "Both click" = left and right fire independently; never veto either one.
                    both_click = ev.action == "thumb_middle_ring" and intent.gesture in BOTH_CLICK_GESTURES
                    if expected_gesture and GESTURE_TO_CLASS.get(intent.gesture) != ev.action and not both_click:
                        self.vetoed_count += 1
                        self._events.append(f"spatial_guard:vetoed_mismatch:{intent.gesture}_vs_{expected_gesture}")
                        continue

            self.passed_count += 1
            out.append(intent)

        # boost: confident, steady MLP + circle click pose that the rules have not fired yet -> click for it
        need_k = min(self.k_boost, 2) if self._boost_fast else self.k_boost
        if (self.boost and self._boost_class is not None and not self._boost_done
                and self._boost_run >= need_k and ctx.mode == "pinch"
                and ctx.frame.t - self._boost_t >= 0.35
                and not self._dragging
                and not any(i.gesture in ("pinch.scroll", "pinch.drag") and i.phase != "end" for i in out)
                and not any(act.holding for act in ctx.others.values())):
            gesture = CLASS_TO_GESTURE[self._boost_class]
            out.append(Intent(ctx.frame.t, "pinch", gesture, "tap", confidence=ev.confidence))
            self._boost_done = True
            self._boost_t = ctx.frame.t
            self.boosted_count += 1
            self._events.append(f"spatial_guard:boosted:{gesture}")

        return tuple(out)

    def drain_events(self) -> List[str]:
        ev, self._events = self._events, []
        return ev

    def reset(self, reason: str) -> None:
        self._idle_run = 0
        self._boost_class = None
        self._boost_run = 0
        self._boost_done = False
        self._events.clear()
        self._dragging = False
        self._drag_last_point = None
        self._drag_last_scale = None
        self._scrolling = False
        self._scroll_last_point = None
        self._scroll_last_scale = None
        self._scroll_run = 0
