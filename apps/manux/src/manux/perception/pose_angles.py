"""`pose-angles-v1`: 33 non-coordinate pose features of one hand (docs/pose-assist.md, contracts section 4).

One frame in, one 33-value row out. No history, no timing: the same joints always give the same
row. The values are computed in the hand's own frame from `local3d-v1` (`perception.local3d.hand_frame`:
MediaPipe world joints in metres, origin at the wrist, y toward the middle knuckle, x toward the index
side, everything divided by the wrist to middle-knuckle length). Because that frame removes position,
size and the hand's orientation, **the row does not change when the whole hand is moved, scaled or
rotated** (tested). The row also does not change for a mirrored hand: only distances, cosines and the
x / y components are used, never the z axis (which flips between a left and a right hand).

**Any change to a value, the column order or a formula means a new version string**
(`pose-angles-v2`). A model trained on v1 would otherwise read wrong numbers without any error.
`tests/unit/perception/test_pose_angles.py` pins the values of a synthetic hand, as local3d does.

Origin. The columns follow the non-coordinate part of the SpatialPoseModel features of the `park`
branch. On park's own data (one person, one day, segment-grouped 5-fold; docs/pose-assist.md section
"Evidence") these 33 columns generalised to new recording segments as well as, or slightly better
than, all 96 columns; the 63 coordinate columns did not help. That is a measurement on park's image
coordinates, not on these world-coordinate columns. Differences from park, all deliberate:

- park computed them on image coordinates with an ad-hoc `z * 0.45`; here they use world metres
  (no constant), already scaled by hand size;
- park's last 8 columns were pinky-anatomy features for its trigger vocabulary (thumb / little-finger
  extension). Here they are the thumb-tip distances to the four fingertips and one straightness
  ratio per finger, which fit the pinch vocabulary of MANUX v0.4;
- park's thumb-tip to little-fingertip clearance moved into those four distances, and `middle tip to
  ring tip` took its place in the clearance block, so the three finger gaps are all present.

Columns (hand-size units unless noted; cosines and ratios are dimensionless):

| #     | name                                  | what |
| ----- | ------------------------------------- | ---- |
| 0-4   | `tip_wrist_thumb` ... `tip_wrist_pinky` | fingertip (4, 8, 12, 16, 20) to wrist distance |
| 5     | `gap_thumb_tip_index_mcp`             | joints 4 - 5 |
| 6     | `gap_pinky_tip_pinky_mcp`             | 20 - 17 |
| 7     | `gap_pinky_tip_ring_tip`              | 20 - 16 |
| 8     | `gap_index_tip_middle_tip`            | 8 - 12 |
| 9     | `gap_middle_tip_ring_tip`             | 12 - 16 |
| 10    | `gap_thumb_tip_middle_mcp`            | 4 - 9 |
| 11    | `gap_thumb_tip_thumb_cmc`             | 4 - 2 |
| 12    | `gap_pinky_tip_middle_mcp`            | 20 - 9 |
| 13-18 | `cos_thumb_ip`, `cos_thumb_mcp`, `cos_index_pip`, `cos_middle_pip`, `cos_ring_pip`, `cos_pinky_pip` | cosine of the angle at the middle joint of (2,3,4), (1,2,4), (5,6,8), (9,10,12), (13,14,16), (17,18,20); -1 straight, 0 right angle, +1 folded flat |
| 19    | `dist_thumb_tip_index_pip`            | joints 4 - 6 |
| 20    | `ratio_pinky_ring_span`               | `min(4, |20-17| / max(0.2, |16-13|))`: little finger length against ring finger length |
| 21-23 | `along_pinky`, `along_index`, `along_middle` | y component of (20-17), (8-5), (12-9): how far the finger points along the palm axis |
| 24    | `across_thumb`                        | x component of (4-2): thumb spread toward the index side |
| 25-28 | `thumb_to_index_tip` ... `thumb_to_pinky_tip` | thumb tip (4) to fingertip (8, 12, 16, 20) distance |
| 29-32 | `straight_index` ... `straight_pinky` | distance knuckle to tip divided by the three bone lengths: 1 straight, smaller when bent |

A frame without a usable hand (no joints, not 21 joints, non-finite values, a zero-size hand) gives
`None`, never an exception. numpy only.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence, Tuple

import numpy as np

from manux.core.contracts import HandObservation
from manux.perception.local3d import hand_frame

FEATURE_VERSION = "pose-angles-v1"
FEATURES = 33

COLUMNS: Tuple[str, ...] = (
    "tip_wrist_thumb", "tip_wrist_index", "tip_wrist_middle", "tip_wrist_ring", "tip_wrist_pinky",
    "gap_thumb_tip_index_mcp", "gap_pinky_tip_pinky_mcp", "gap_pinky_tip_ring_tip", "gap_index_tip_middle_tip",
    "gap_middle_tip_ring_tip", "gap_thumb_tip_middle_mcp", "gap_thumb_tip_thumb_cmc", "gap_pinky_tip_middle_mcp",
    "cos_thumb_ip", "cos_thumb_mcp", "cos_index_pip", "cos_middle_pip", "cos_ring_pip", "cos_pinky_pip",
    "dist_thumb_tip_index_pip", "ratio_pinky_ring_span",
    "along_pinky", "along_index", "along_middle", "across_thumb",
    "thumb_to_index_tip", "thumb_to_middle_tip", "thumb_to_ring_tip", "thumb_to_pinky_tip",
    "straight_index", "straight_middle", "straight_ring", "straight_pinky",
)

_TIPS = (4, 8, 12, 16, 20)
_GAPS = ((4, 5), (20, 17), (20, 16), (8, 12), (12, 16), (4, 9), (4, 2), (20, 9))
_ANGLES = ((2, 3, 4), (1, 2, 4), (5, 6, 8), (9, 10, 12), (13, 14, 16), (17, 18, 20))
_FINGERS = ((5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20))   # index, middle, ring, pinky
_EPS = 1e-9
_MIN_RING_SPAN = 0.2     # hand sizes: floor of the ring finger length in the span ratio
_MAX_SPAN_RATIO = 4.0

assert len(COLUMNS) == FEATURES == len(set(COLUMNS))


def _dist(p: np.ndarray, a: int, b: int) -> float:
    return float(np.linalg.norm(p[a] - p[b]))


def _cos(p: np.ndarray, a: int, b: int, c: int) -> float:
    """Cosine of the angle at joint b between the directions to a and c (0 when a joint coincides)."""
    u, v = p[a] - p[b], p[c] - p[b]
    n = float(np.linalg.norm(u) * np.linalg.norm(v))
    return float(np.dot(u, v) / n) if n > _EPS else 0.0


def _straight(p: np.ndarray, finger: Tuple[int, int, int, int]) -> float:
    mcp, pip, dip, tip = finger
    bones = _dist(p, mcp, pip) + _dist(p, pip, dip) + _dist(p, dip, tip)
    return _dist(p, mcp, tip) / bones if bones > _EPS else 0.0


def pose_angles(world: Any) -> Optional[np.ndarray]:
    """The 33 values (float32) of one hand's world joints, or None when the hand is not usable."""
    try:
        joints = np.asarray(world, dtype=np.float64)
        if joints.shape != (21, 3) or not np.isfinite(joints).all():
            return None
        p = hand_frame(joints)
    except (TypeError, ValueError):
        return None
    if p is None or not np.isfinite(p).all():
        return None
    out = np.empty(FEATURES, dtype=np.float64)
    i = 0
    for tip in _TIPS:
        out[i] = np.linalg.norm(p[tip])            # the wrist is the origin
        i += 1
    for a, b in _GAPS:
        out[i] = _dist(p, a, b)
        i += 1
    for a, b, c in _ANGLES:
        out[i] = _cos(p, a, b, c)
        i += 1
    out[i] = _dist(p, 4, 6)
    i += 1
    out[i] = min(_MAX_SPAN_RATIO, _dist(p, 20, 17) / max(_MIN_RING_SPAN, _dist(p, 16, 13)))
    i += 1
    for tip, base in ((20, 17), (8, 5), (12, 9)):
        out[i] = p[tip, 1] - p[base, 1]
        i += 1
    out[i] = p[4, 0] - p[2, 0]
    i += 1
    for tip in _TIPS[1:]:
        out[i] = _dist(p, 4, tip)
        i += 1
    for finger in _FINGERS:
        out[i] = _straight(p, finger)
        i += 1
    assert i == FEATURES
    return out.astype(np.float32)


def observation_pose_angles(hand: Optional[HandObservation]) -> Optional[np.ndarray]:
    """The 33 values of a live hand; None without a hand or without world joints."""
    if hand is None or hand.world is None:
        return None
    return pose_angles(hand.world)


def frame_pose_angles(frames: Sequence[Mapping[str, Any]]) -> Tuple[np.ndarray, np.ndarray]:
    """For recorded frames (dicts with "world"): `(n, 33)` float32 rows and an `(n,)` bool "usable" mask.

    Rows of unusable frames are zeros and must be ignored through the mask.
    """
    rows = np.zeros((len(frames), FEATURES), dtype=np.float32)
    valid = np.zeros(len(frames), dtype=bool)
    for k, frame in enumerate(frames):
        world = frame.get("world") if isinstance(frame, Mapping) else None
        row = pose_angles(world) if world else None
        if row is not None:
            rows[k] = row
            valid[k] = True
    return rows, valid
