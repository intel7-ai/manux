"""V hold to toggle control; independent of cursor/MLP feature state (contract section 8)."""
from __future__ import annotations

import math
from typing import Optional

from manux.core.contracts import Activity, FrameContext, HandObservation, Intent, Stage
from manux.perception.spatial_features import extract_spatial_features
from manux.perception.pose_angles import observation_pose_angles

ID = "control"
V_SIGN = "control.v_sign"
GESTURES = (V_SIGN,)
GESTURE_PHASES = {V_SIGN: frozenset({"tap"})}
DEFAULT_BINDINGS = {V_SIGN: "control.toggle"}
SETTINGS = {
    "control.hold": 0.7,
    "control.cooldown": 1.5,
    "control.max_gap": 0.3,
    "control.mlp_confidence": 0.8,
    "control.extended_ratio": 1.4,
    "control.folded_ratio": 1.15,
    "control.thumb_tuck": 0.9,
}


def _value(ctx: FrameContext, key: str) -> float:
    default = SETTINGS[f"control.{key}"]
    value = ctx.settings.get(key, default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) and number > 0 else default


def is_v_sign(hand: HandObservation, ctx: FrameContext) -> bool:
    """Palm-relative extension ratios work after camera rotation and at different hand sizes."""
    p = hand.landmarks
    def distance(a, b):
        return math.hypot(p[a][0] - p[b][0], (p[a][1] - p[b][1]) * ctx.frame.aspect)
    palm = distance(0, 9)
    if palm <= 1e-6:
        return False
    ratios = [distance(0, tip) / max(1e-6, distance(0, mcp))
              for mcp, tip in ((5, 8), (9, 12), (13, 16), (17, 20))]
    return (min(ratios[:2]) >= _value(ctx, "extended_ratio")
            and max(ratios[2:]) <= _value(ctx, "folded_ratio")
            and min(distance(4, 13), distance(4, 17)) / palm <= _value(ctx, "thumb_tuck"))


class ControlFeature:
    id = ID
    modes = frozenset({"pointer", "pinch", "motion"})
    gestures = GESTURES

    def __init__(self, model=None):
        self._model = model
        self._since: Optional[float] = None
        self._last_t: Optional[float] = None
        self._last_fire = -math.inf
        self._latched = False
        self._visible_v = False

    @property
    def model(self):
        return self._model

    def set_model(self, model) -> None:
        """Composition root injects the selected optional MLP; no feature imports another feature."""
        self._model = model
        self._since = None

    def step(self, hand: Optional[HandObservation], ctx: FrameContext):
        now = ctx.frame.t
        if (self._last_t is not None
                and (now <= self._last_t or now - self._last_t > _value(ctx, "max_gap"))):
            self._since = None
        self._last_t = now
        if ctx.stage not in (Stage.MAIN, Stage.TUTORIAL) or hand is None or not is_v_sign(hand, ctx):
            self._since = None
            self._latched = False
            self._visible_v = False
            return ()
        self._visible_v = True
        if self._model is not None and "v_sign" in self._model.classes:
            # A selected model's failure/negative must never become a rule-only acceptance.
            features = (observation_pose_angles(hand) if self._model.feature_version == "pose-angles-v1"
                        else extract_spatial_features(hand.landmarks))
            if features is None:
                self._since = None
                return ()
            prediction = self._model.predict(features)
            probability = float(prediction.get("probabilities", {}).get("v_sign", 0.0))
            if not math.isfinite(probability) or probability < _value(ctx, "mlp_confidence"):
                self._since = None
                return ()
        if self._latched:
            return ()
        if self._since is None:
            self._since = now
        if now - self._since >= _value(ctx, "hold") and now - self._last_fire >= _value(ctx, "cooldown"):
            self._latched = True
            self._last_fire = now
            return (Intent(now, ID, V_SIGN, "tap"),)
        return ()

    def activity(self):
        return Activity(holding=self._visible_v, last_release_t=self._last_fire)

    def reset(self, reason: str):
        self._since = None
        self._last_t = None
        self._visible_v = False
        # A pause/settings toggle cannot make the same held V fire again.
        if reason in ("hand_lost", "stage_change", "shutdown"):
            self._latched = False
