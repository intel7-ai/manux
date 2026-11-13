"""Spatial 96-dimensional geometric feature extractor for single-frame hand posture MLP.

Combines:
1. Intrinsic 3D palm basis (Gram-Schmidt orthogonalization: wrist 0 -> middle MCP 9, pinky MCP 17 -> index MCP 5)
2. 21 normalized 3D relative joint coordinates (63 dims)
3. 5 finger-tip to wrist distances (5 dims)
4. 8 inter-finger clearances & pinch distances (thumb to index/middle/ring/pinky tips, index-middle V-gap, etc.) (8 dims)
5. 12 3D joint angle cosines (cos3D) and intrinsic palm-frame projections (12 dims)
6. 8 high-precision 3-segment collinearity ratios and finger curl/extension features (8 dims)

Total: 96 features (float32). Pure numpy, zero state, 0-frame lag.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence, Tuple

import numpy as np

SPATIAL_FEATURE_VERSION = "spatial-mlp-v1"
SPATIAL_FEATURES = 96

Pt = Tuple[float, float, float]


def _to_pts(landmarks: Any) -> Optional[np.ndarray]:
    """Convert sequence of 21 landmarks (tuples, lists, dicts, or MediaPipe objects) to (21, 3) float64 array."""
    if landmarks is None:
        return None
    if isinstance(landmarks, np.ndarray):
        if landmarks.shape == (21, 3) and np.isfinite(landmarks).all():
            return landmarks.astype(np.float64, copy=False)
        return None
    if not isinstance(landmarks, Sequence) or len(landmarks) != 21:
        return None
    out = np.zeros((21, 3), dtype=np.float64)
    for i, pt in enumerate(landmarks):
        if isinstance(pt, dict):
            x, y, z = float(pt.get("x", 0.0)), float(pt.get("y", 0.0)), float(pt.get("z", 0.0))
        elif hasattr(pt, "x") and hasattr(pt, "y"):
            x, y = float(pt.x), float(pt.y)
            z = float(getattr(pt, "z", 0.0))
        elif isinstance(pt, Sequence) and len(pt) >= 2:
            x, y = float(pt[0]), float(pt[1])
            z = float(pt[2]) if len(pt) >= 3 else 0.0
        else:
            return None
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            return None
        out[i, 0], out[i, 1], out[i, 2] = x, y, z
    return out


def _cos3d(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    ux, uy, uz = a[0] - b[0], a[1] - b[1], (a[2] - b[2]) * 0.45
    vx, vy, vz = c[0] - b[0], c[1] - b[1], (c[2] - b[2]) * 0.45
    lu = math.sqrt(ux * ux + uy * uy + uz * uz)
    lv = math.sqrt(vx * vx + vy * vy + vz * vz)
    denom = lu * lv
    return (ux * vx + uy * vy + uz * vz) / denom if denom > 1e-8 else 0.0


def _dist3d(a: np.ndarray, b: np.ndarray) -> float:
    dx, dy, dz = a[0] - b[0], a[1] - b[1], (a[2] - b[2]) * 0.45
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _dist2d(a: np.ndarray, b: np.ndarray) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def extract_spatial_features(landmarks: Any) -> Optional[np.ndarray]:
    """Extract 96-dimensional spatial feature vector from 21 hand landmarks.

    Returns None if landmarks are missing or degenerate.
    """
    pts = _to_pts(landmarks)
    if pts is None:
        return None

    p0 = pts[0]
    p9 = pts[9]
    palm = _dist2d(p9, p0)
    if palm < 1e-5:
        return None
    palm3d = max(palm, _dist3d(p0, p9), _dist3d(pts[5], pts[17]), 1e-4)

    # Intrinsic palm basis (Gram-Schmidt)
    vx, vy, vz = p9[0] - p0[0], p9[1] - p0[1], (p9[2] - p0[2]) * 0.45
    v_len = math.sqrt(vx * vx + vy * vy + vz * vz) or 1e-5
    vh_x, vh_y, vh_z = vx / v_len, vy / v_len, vz / v_len

    ax, ay, az = pts[5][0] - pts[17][0], pts[5][1] - pts[17][1], (pts[5][2] - pts[17][2]) * 0.45
    a_dot_v = ax * vh_x + ay * vh_y + az * vh_z
    ux, uy, uz = ax - a_dot_v * vh_x, ay - a_dot_v * vh_y, az - a_dot_v * vh_z
    u_len = math.sqrt(ux * ux + uy * uy + uz * uz) or 1e-5
    uh_x, uh_y, uh_z = ux / u_len, uy / u_len, uz / u_len

    def proj_v(a: np.ndarray, b: np.ndarray) -> float:
        return ((a[0] - b[0]) * vh_x + (a[1] - b[1]) * vh_y + ((a[2] - b[2]) * 0.45) * vh_z) / palm3d

    def proj_u(a: np.ndarray, b: np.ndarray) -> float:
        return ((a[0] - b[0]) * uh_x + (a[1] - b[1]) * uh_y + ((a[2] - b[2]) * 0.45) * uh_z) / palm3d

    feats = np.zeros(SPATIAL_FEATURES, dtype=np.float32)
    idx = 0

    # 1. Normalized 3D relative positions (21 * 3 = 63 features, indices 0..62)
    for i in range(21):
        feats[idx] = (pts[i, 0] - p0[0]) / palm
        feats[idx + 1] = (pts[i, 1] - p0[1]) / palm
        feats[idx + 2] = ((pts[i, 2] - p0[2]) * 0.45) / palm
        idx += 3

    # 2. Key anatomical finger-to-wrist distances (5 features, indices 63..67)
    for tip in (4, 8, 12, 16, 20):
        feats[idx] = _dist2d(pts[tip], p0) / palm
        idx += 1

    # 3. Pinch & inter-finger 3D/2D clearances (8 features, indices 68..75)
    # Specifically tailored to distinguish thumb_index, thumb_middle, thumb_ring, thumb_pinky, v_sign, palm_side
    feats[idx] = _dist3d(pts[4], pts[8]) / palm3d        # 68: Thumb tip to Index tip (thumb_index scroll)
    feats[idx + 1] = _dist3d(pts[4], pts[12]) / palm3d   # 69: Thumb tip to Middle tip (thumb_middle left click)
    feats[idx + 2] = _dist3d(pts[4], pts[16]) / palm3d   # 70: Thumb tip to Ring tip (thumb_ring right click)
    feats[idx + 3] = _dist3d(pts[4], pts[20]) / palm3d   # 71: Thumb tip to Pinky tip (thumb_pinky task switcher)
    feats[idx + 4] = _dist2d(pts[8], pts[12]) / palm     # 72: Index tip to Middle tip gap (v_sign)
    feats[idx + 5] = min(_dist2d(pts[4], pts[13]), _dist2d(pts[4], pts[17])) / palm  # 73: Thumb tuck across palm (palm_side)
    feats[idx + 6] = _dist2d(pts[4], pts[5]) / palm      # 74: Thumb tip to Index MCP
    feats[idx + 7] = _dist2d(pts[20], pts[17]) / palm    # 75: Pinky tip to Pinky MCP
    idx += 8

    # 4. 3D Joint Angle Cosines & Intrinsic Basis Projections (12 features, indices 76..87)
    feats[idx] = _cos3d(pts[2], pts[3], pts[4])          # 76: Thumb IP straightness
    feats[idx + 1] = _cos3d(pts[1], pts[2], pts[4])      # 77: Thumb MCP straightness
    feats[idx + 2] = _cos3d(pts[5], pts[6], pts[8])      # 78: Index PIP straightness
    feats[idx + 3] = _cos3d(pts[9], pts[10], pts[12])    # 79: Middle PIP straightness
    feats[idx + 4] = _cos3d(pts[13], pts[14], pts[16])   # 80: Ring PIP straightness
    feats[idx + 5] = _cos3d(pts[17], pts[18], pts[20])   # 81: Pinky PIP-Tip straightness
    feats[idx + 6] = _dist3d(pts[4], pts[6]) / palm3d    # 82: Thumb to Index PIP 3D clearance
    ring_span = max(0.20 * palm3d, _dist3d(pts[16], pts[13]))
    feats[idx + 7] = min(4.0, _dist3d(pts[20], pts[17]) / ring_span)  # 83: Pinky vs Ring span ratio
    feats[idx + 8] = proj_v(pts[20], pts[17])            # 84: Pinky axial forward projection
    feats[idx + 9] = proj_v(pts[8], pts[5])              # 85: Index axial forward projection
    feats[idx + 10] = proj_v(pts[12], pts[9])            # 86: Middle axial forward projection
    feats[idx + 11] = proj_u(pts[4], pts[2])             # 87: Thumb radial abduction projection
    idx += 12

    # 5. High-Precision Collinearity & Anatomical Ratios (8 features, indices 88..95)
    idx_bone = _dist3d(pts[5], pts[6]) + _dist3d(pts[6], pts[7]) + _dist3d(pts[7], pts[8]) or 1e-4
    mid_bone = _dist3d(pts[9], pts[10]) + _dist3d(pts[10], pts[11]) + _dist3d(pts[11], pts[12]) or 1e-4
    rg_bone = _dist3d(pts[13], pts[14]) + _dist3d(pts[14], pts[15]) + _dist3d(pts[15], pts[16]) or 1e-4
    pk_bone = _dist3d(pts[17], pts[18]) + _dist3d(pts[18], pts[19]) + _dist3d(pts[19], pts[20]) or 1e-4

    feats[idx] = _dist3d(pts[5], pts[8]) / idx_bone      # 88: Index 3-segment collinearity ratio
    feats[idx + 1] = _dist3d(pts[9], pts[12]) / mid_bone # 89: Middle 3-segment collinearity ratio
    feats[idx + 2] = _dist3d(pts[13], pts[16]) / rg_bone # 90: Ring 3-segment collinearity ratio
    feats[idx + 3] = _dist3d(pts[17], pts[20]) / pk_bone # 91: Pinky 3-segment collinearity ratio
    feats[idx + 4] = (pts[5, 1] - pts[4, 1]) / palm      # 92: Thumb vertical elevation above Index MCP (thumbs_up)
    feats[idx + 5] = _dist2d(pts[5], pts[17]) / palm     # 93: Palm width ratio (palm_side vs side view)
    feats[idx + 6] = proj_v(pts[4], pts[0])              # 94: Thumb axial projection along hand frame
    feats[idx + 7] = proj_u(pts[20], pts[17])            # 95: Pinky lateral ulnar projection

    return feats


def session_spatial_features(frames: Sequence[Mapping[str, Any]]) -> Tuple[np.ndarray, np.ndarray]:
    """Extract (N, 96) float32 spatial features and (N,) bool validity mask for a sequence of recorded frames."""
    n = len(frames)
    out = np.zeros((n, SPATIAL_FEATURES), dtype=np.float32)
    valid = np.zeros(n, dtype=bool)
    for i, f in enumerate(frames):
        lm = f.get("lm") if isinstance(f, Mapping) else None
        if lm is None and isinstance(f, Mapping):
            lm = f.get("world")
        vec = extract_spatial_features(lm)
        if vec is not None:
            out[i] = vec
            valid[i] = True
    return out, valid
