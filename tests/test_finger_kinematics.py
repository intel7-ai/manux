"""Tests for 5-finger anatomical occlusion reconstruction, pinch-context calibration, and real-time tracking."""
from __future__ import annotations

import numpy as np

from src.finger_kinematics import CausalFingerTracker, reconstruct_hand_sequence


def _make_clean_hand(pinch_target_tip: int | None = 8, open_finger: bool = False) -> np.ndarray:
    """Create a realistic 21-joint 3D hand with 5 anatomical fingers."""
    pts = np.zeros((21, 3), dtype=np.float64)
    # Wrist at (0.5, 0.5, 0.0), middle MCP (#9) at (0.5, 0.4, 0.0) -> palm size = 0.10
    pts[0] = [0.50, 0.50, 0.00]
    mcp_x = {1: 0.44, 5: 0.47, 9: 0.50, 13: 0.53, 17: 0.56}
    mcp_y = {1: 0.47, 5: 0.40, 9: 0.40, 13: 0.41, 17: 0.42}
    seg_lens = {
        1: (0.032, 0.025, 0.020),
        5: (0.042, 0.026, 0.019),
        9: (0.046, 0.029, 0.020),
        13: (0.043, 0.027, 0.019),
        17: (0.034, 0.021, 0.017),
    }
    for base, x0 in mcp_x.items():
        y0 = mcp_y[base]
        pts[base] = [x0, y0, 0.0]
        l1, l2, l3 = seg_lens[base]
        pts[base + 1] = [x0, y0 - l1, 0.0]
        pts[base + 2] = [x0, y0 - l1 - l2, 0.0]
        pts[base + 3] = [x0, y0 - l1 - l2 - l3, 0.0]

    if pinch_target_tip is not None and not open_finger:
        # Curl thumb (#1..#4) and target finger to meet at a pinch contact point while preserving anatomical bone lengths
        contact = np.array([0.465, 0.365, -0.01], dtype=np.float64)
        pts[2] = [0.448, 0.440, -0.004]
        pts[3] = [0.456, 0.416, -0.008]
        pts[4] = [0.463, 0.398, -0.010]

        f_base = pinch_target_tip - 3
        l1, l2, l3 = seg_lens[f_base]
        # Flex PIP, DIP, and TIP toward the thumb tip (#4) while preserving exact bone lengths l1, l2, l3
        pip = np.array([pts[f_base][0] - 0.006, pts[f_base][1] - l1 * 0.72, -l1 * 0.68])
        dip = pip + np.array([-0.002, l2 * 0.48, l2 * 0.87])
        tip = np.array(pts[4]) + np.array([0.003, -0.002, 0.0])
        pts[f_base + 1] = pip
        pts[f_base + 2] = dip
        pts[pinch_target_tip] = tip
    return pts


def test_reconstruct_hand_sequence_restores_occluded_finger_and_pinch_continuity():
    frames = []
    for i in range(12):
        pts = _make_clean_hand(pinch_target_tip=8, open_finger=False)
        # Simulate camera tilt self-occlusion on frames 5 and 6:
        # index fingertip (#8) and DIP (#7) collapse into the palm and falsely break the pinch
        if i in (5, 6):
            pts[7] = pts[6] + np.array([0.002, -0.002, 0.02])  # collapsed bone
            pts[8] = pts[6] + np.array([0.004, -0.003, 0.03])  # falsely separated from thumb #4
        frames.append(
            {
                "t": round(i / 30.0, 4),
                "lm": pts.tolist(),
                "world": (pts - pts[0:1]).tolist(),
                "label": {"phase": "do", "target": "thumb_index", "task": "thumb_index", "set": 1},
            }
        )

    raw_dist_5 = float(np.linalg.norm(np.asarray(frames[5]["lm"])[4] - np.asarray(frames[5]["lm"])[8]))
    cal_frames, num_cal = reconstruct_hand_sequence(frames, in_place=False)
    assert num_cal >= 2

    cal_pts_5 = np.asarray(cal_frames[5]["lm"], dtype=np.float64)
    cal_dist_5 = float(np.linalg.norm(cal_pts_5[4] - cal_pts_5[8]))
    clean_pts = _make_clean_hand(pinch_target_tip=8, open_finger=False)
    clean_dist = float(np.linalg.norm(clean_pts[4] - clean_pts[8]))

    # Calibrated pinch distance on occluded frame 5 is restored close to clean pinch distance
    assert cal_dist_5 < raw_dist_5 * 0.45
    assert abs(cal_dist_5 - clean_dist) < 0.015


def test_reconstruct_hand_sequence_does_not_force_pinch_when_knuckle_opens():
    frames = []
    for i in range(10):
        # On frames 4..6 the user genuinely opens the index finger (PIP #6 extends far out)
        is_open = i in (4, 5, 6)
        pts = _make_clean_hand(pinch_target_tip=8, open_finger=is_open)
        frames.append(
            {
                "t": round(i / 30.0, 4),
                "lm": pts.tolist(),
                "world": (pts - pts[0:1]).tolist(),
                "label": {"phase": "free", "target": "none", "task": "free", "set": 1},
            }
        )

    cal_frames, _ = reconstruct_hand_sequence(frames, in_place=False)
    cal_pts_5 = np.asarray(cal_frames[5]["lm"], dtype=np.float64)
    open_dist_5 = float(np.linalg.norm(cal_pts_5[4] - cal_pts_5[8]))
    # Genuine finger opening remains open (not pulled back into a false pinch)
    assert open_dist_5 > 0.06


def test_causal_finger_tracker_stabilizes_live_occlusion_spike():
    tracker = CausalFingerTracker()
    clean = _make_clean_hand(pinch_target_tip=12, open_finger=False)
    for i in range(5):
        lm_out, _ = tracker.step(i * 0.033, clean.tolist(), (clean - clean[0:1]).tolist())
        assert tracker.occluded_fingers_last_frame == 0

    # Frame 5: middle finger (#11, #12) suddenly collapses due to angle occlusion while MCP (#9) and PIP (#10) stay flexed
    occluded = clean.copy()
    occluded[11] = occluded[10] + np.array([0.001, -0.001, 0.01])
    occluded[12] = occluded[10] + np.array([0.002, -0.002, 0.02])
    raw_gap = float(np.linalg.norm(occluded[4] - occluded[12]))

    lm_fixed, _ = tracker.step(5 * 0.033, occluded.tolist(), (occluded - occluded[0:1]).tolist())
    assert tracker.occluded_fingers_last_frame >= 1
    fixed_pts = np.asarray(lm_fixed, dtype=np.float64)
    fixed_gap = float(np.linalg.norm(fixed_pts[4] - fixed_pts[12]))
    assert fixed_gap < raw_gap * 0.55
