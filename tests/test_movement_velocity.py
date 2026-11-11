"""Unit tests for Step 1 (Offline Movement & Return-Stroke Eval Harness)
and Step 2 (Velocity-Based Pointer Acceleration `movement_scheme="velocity"`).
"""

from __future__ import annotations

import math
from typing import Dict, Any, Tuple

from src.gesture_engine import GestureEngine, GestureResult
from src.mouse_controller import MouseController
from src.movement_eval import (
    detect_return_strokes,
    load_config,
    replay_sequence,
    segment_strokes_and_returns,
    summarize_group,
)


def _base_velocity_cfg(**overrides: Any) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "movement_mode": "relative",
        "movement_scheme": "velocity",
        "return_suppression": "off",
        "tremor_filter": False,
        "smoothing": 1.0,
        "path_smoothing": False,
        "velocity_gain_min": 0.55,
        "velocity_gain_mid": 2.8,
        "velocity_gain_max": 6.5,
        "velocity_deadzone": 0.02,
        "velocity_stationary_speed": 0.05,
        "velocity_mid_speed": 0.40,
        "velocity_high_speed": 1.30,
        "velocity_ema_alpha": 0.35,
        "velocity_slew_up": 14.0,
        "velocity_slew_down": 18.0,
        "cursor_max_step": 0.12,
    }
    cfg.update(overrides)
    return cfg


def test_velocity_gain_curve_is_monotonic_and_bounded() -> None:
    cfg = _base_velocity_cfg()
    g_min = cfg["velocity_gain_min"]
    g_mid = cfg["velocity_gain_mid"]
    g_max = cfg["velocity_gain_max"]

    # Deadzone holds constant g_min
    assert math.isclose(MouseController.compute_velocity_gain(0.0, cfg), g_min)
    assert math.isclose(MouseController.compute_velocity_gain(0.01, cfg), g_min)
    assert math.isclose(MouseController.compute_velocity_gain(0.02, cfg), g_min)

    # Mid-speed knot hits g_mid
    assert math.isclose(MouseController.compute_velocity_gain(0.40, cfg), g_mid, rel_tol=1e-6)

    # High-speed knot and above saturate at g_max
    assert math.isclose(MouseController.compute_velocity_gain(1.30, cfg), g_max, rel_tol=1e-6)
    assert math.isclose(MouseController.compute_velocity_gain(2.50, cfg), g_max, rel_tol=1e-6)

    # Strictly non-decreasing across the entire speed range
    speeds = [i * 0.01 for i in range(200)]
    gains = [MouseController.compute_velocity_gain(s, cfg) for s in speeds]
    for g0, g1 in zip(gains, gains[1:]):
        assert g1 >= g0 - 1e-9
        assert g_min - 1e-9 <= g1 <= g_max + 1e-9


def test_velocity_scheme_freezes_multiplier_when_stationary_despite_curl_changes() -> None:
    cfg = _base_velocity_cfg()
    mouse = MouseController(dry_run=True, screen_size=(1920, 1080))
    mouse.frame_aspect = 0.75

    # First move at moderate speed so _effective_gain rises above g_min
    t = 0.0
    dt = 1.0 / 30.0
    x, y = 0.5, 0.5
    for _ in range(10):
        t += dt
        x += 0.015  # speed = 0.45 frame widths/s
        res = GestureResult(
            active_gesture="no_pinch",
            action="move_cursor",
            cursor_point=(x, y),
            is_moving=True,
            dpi_multiplier=1.0,
            hand_shape="open",
        )
        mouse.apply(res, cfg, now=t)

    gain_before_stop = mouse.dpi_multiplier
    assert gain_before_stop > cfg["velocity_gain_min"]

    # Now hold the hand nearly stationary (speed < 0.05) while varying legacy dpi_multiplier wildly (1x -> 35x -> 5x)
    recorded_mults = []
    for fake_curl_mult in (1.0, 12.0, 35.0, 22.0, 3.0, 30.0):
        t += dt
        x += 0.0004  # speed = 0.012 < 0.05 stationary threshold
        res = GestureResult(
            active_gesture="no_pinch",
            action="move_cursor",
            cursor_point=(x, y),
            is_moving=True,
            dpi_multiplier=fake_curl_mult,
            hand_shape="fist",
        )
        mouse.apply(res, cfg, now=t)
        recorded_mults.append(mouse.dpi_multiplier)

    # Multiplier must remain completely frozen across stationary frames
    for m in recorded_mults:
        assert math.isclose(m, gain_before_stop, abs_tol=1e-9)


def test_velocity_scheme_enforces_slew_rate_limit() -> None:
    cfg = _base_velocity_cfg(velocity_slew_up=10.0, velocity_slew_down=12.0, velocity_ema_alpha=1.0)
    mouse = MouseController(dry_run=True, screen_size=(1920, 1080))
    mouse.frame_aspect = 1.0

    dt = 0.02
    t = 0.0
    x, y = 0.2, 0.5
    # Anchor frame
    mouse.apply(
        GestureResult(active_gesture="no_pinch", action="move_cursor", cursor_point=(x, y), is_moving=True),
        cfg,
        now=t,
    )
    prev_m = mouse.dpi_multiplier

    # Sudden fast movement (speed = 2.0 -> target_gain = 6.5)
    for _ in range(5):
        t += dt
        x += 2.0 * dt
        mouse.apply(
            GestureResult(active_gesture="no_pinch", action="move_cursor", cursor_point=(x, y), is_moving=True),
            cfg,
            now=t,
        )
        slew = (mouse.dpi_multiplier - prev_m) / dt
        assert slew <= cfg["velocity_slew_up"] + 1e-6
        prev_m = mouse.dpi_multiplier


def test_legacy_shape_scheme_remains_unchanged_by_default() -> None:
    cfg = load_config()
    assert cfg["movement_scheme"] == "legacy_shape"
    assert cfg["return_suppression"] == "off"

    mouse = MouseController(dry_run=True, screen_size=(1920, 1080))
    res = GestureResult(
        active_gesture="no_pinch",
        action="move_cursor",
        cursor_point=(0.5, 0.5),
        is_moving=True,
        dpi_multiplier=18.5,
        hand_shape="half",
    )
    mouse.apply(res, {**cfg, "ui_magnet_enabled": False}, now=1.0)
    assert math.isclose(mouse.dpi_multiplier, 18.5)


def test_movement_eval_detects_synthetic_forward_and_return_stroke() -> None:
    # Build a synthetic trace: forward stroke right (+x), short pause, return stroke left (-x)
    trace = []
    t = 0.0
    dt = 1.0 / 30.0
    hx, hy = 0.4, 0.3
    cx, cy = 960, 540

    def _add_frame(dx: float, dy: float, cdx: float, cdy: float) -> None:
        nonlocal t, hx, hy, cx, cy
        t = round(t + dt, 4)
        hx += dx
        hy += dy
        cx += int(round(cdx))
        cy += int(round(cdy))
        spd = math.hypot(dx, dy) / dt
        cstep = math.hypot(cdx, cdy)
        trace.append({
            "t": t,
            "dt": dt,
            "has_hand": True,
            "action": "move_cursor",
            "gesture": "no_pinch",
            "is_moving": True,
            "hand_shape": "half",
            "dpi_multiplier": 3.0,
            "curl": 1.2,
            "raw_curl": 1.2,
            "hand_size": 0.12,
            "hand_pt": (hx, hy),
            "hand_dx": dx,
            "hand_dy": dy,
            "hand_speed": spd,
            "cursor": (cx, cy),
            "cursor_dx": float(cdx),
            "cursor_dy": float(cdy),
            "cursor_step": cstep,
            "capped": False,
            "carry_mag": 0.0,
            "clutch_state": "inactive",
            "return_state": "none",
            "label": "none",
        })

    for _ in range(10):
        _add_frame(0.012, 0.0, 45.0, 0.0)
    for _ in range(3):
        _add_frame(0.0, 0.0, 0.0, 0.0)
    for _ in range(10):
        _add_frame(-0.010, 0.0, -35.0, 0.0)

    fwd, ret = segment_strokes_and_returns(trace)
    assert len(fwd) >= 1
    assert len(ret) == 1
    assert ret[0]["angle_deg"] >= 170.0
    assert ret[0]["cursor_path_px"] > 300.0
    assert len(detect_return_strokes(trace)) == 1


def test_discrete_shape_mode_hysteresis_and_min_hold_blocks_flicker() -> None:
    cfg = _base_velocity_cfg(
        shape_mode_curl_smooth=1.0,
        shape_mode_precise_enter=1.50,
        shape_mode_precise_exit=1.34,
        shape_mode_fast_enter=0.90,
        shape_mode_fast_exit=1.08,
        shape_mode_min_hold=0.20,
    )
    engine = GestureEngine()
    engine.config.update(cfg)
    assert engine._shape_mode == "normal"

    # Enter "precise" only after holding curl >= 1.50 for >= 0.20s
    t = 1.0
    assert engine._update_shape_mode(1.55, False, t) == "normal"
    t += 0.10
    assert engine._update_shape_mode(1.55, False, t) == "normal"
    t += 0.12
    assert engine._update_shape_mode(1.55, False, t) == "precise"

    # Hysteresis band: dropping to 1.38 (> precise_exit=1.34) stays "precise" without starting a transition
    for _ in range(10):
        t += 0.05
        assert engine._update_shape_mode(1.38, False, t) == "precise"

    # Rapid <0.5s oscillation between curl=1.20 and curl=1.60 must not produce <=0.5s round-trip flickers
    modes = []
    for i in range(30):
        t += 0.03
        curl_val = 1.20 if (i % 4 < 2) else 1.60
        modes.append((t, engine._update_shape_mode(curl_val, False, t)))

    transitions = []
    flickers_05s = 0
    for (t0, s0), (t1, s1) in zip(modes, modes[1:]):
        if s0 != s1:
            if transitions and transitions[-1][1] == s1 and (t1 - transitions[-1][0]) <= 0.5:
                flickers_05s += 1
            transitions.append((t1, s0, s1))
    assert flickers_05s == 0

    # Sustained fist / low curl enters "fast", and hysteresis holds "fast" until curl >= fast_exit (1.08)
    for _ in range(10):
        t += 0.10
        mode = engine._update_shape_mode(0.82, True, t)
    assert mode == "fast"
    t += 0.30
    assert engine._update_shape_mode(1.02, False, t) == "fast"


def test_velocity_mode_bounds_adjust_gain_curve_by_discrete_shape_mode() -> None:
    cfg = _base_velocity_cfg(velocity_ema_alpha=1.0, velocity_slew_up=100.0, velocity_slew_down=100.0)

    def _run_mode(shape_mode: str) -> float:
        mouse = MouseController(dry_run=True, screen_size=(1920, 1080))
        mouse.frame_aspect = 1.0
        t = 0.0
        dt = 0.02
        x, y = 0.2, 0.5
        for _ in range(8):
            t += dt
            x += 0.50 * dt  # moderate speed = 0.50 widths/s
            res = GestureResult(
                active_gesture="no_pinch",
                action="move_cursor",
                cursor_point=(x, y),
                is_moving=True,
                dpi_multiplier=1.0,
                hand_shape=shape_mode,
            )
            mouse.apply(res, cfg, now=t)
        return mouse.dpi_multiplier

    g_precise = _run_mode("precise")
    g_normal = _run_mode("normal")
    g_fast = _run_mode("fast")
    assert g_precise < g_normal < g_fast


def test_coarse_to_fine_deceleration_assist_lowers_gain_faster_after_flick() -> None:
    def _run_flick_then_decel(c2f_enabled: bool) -> Tuple[float, bool]:
        cfg = _base_velocity_cfg(
            velocity_c2f_enabled=c2f_enabled,
            velocity_c2f_entry_speed=0.45,
            velocity_c2f_aim_speed=0.32,
            velocity_c2f_decel_thresh=0.80,
            velocity_c2f_window=0.28,
            velocity_c2f_gain_scale=0.75,
            velocity_c2f_slew_boost=2.2,
            edge_reclutch_enabled=False,
        )
        mouse = MouseController(dry_run=True, screen_size=(1920, 1080))
        mouse.frame_aspect = 1.0
        t = 0.0
        dt = 0.02
        x, y = 0.25, 0.5
        # Fast flick at 1.0 widths/s
        for _ in range(8):
            t += dt
            x += 1.0 * dt
            mouse.apply(
                GestureResult(active_gesture="no_pinch", action="move_cursor", cursor_point=(x, y), is_moving=True, hand_shape="normal"),
                cfg,
                now=t,
            )
        # Sudden deceleration into fine aiming at 0.15 widths/s (dv/dt = -0.85 / 0.02 = -42.5 <= -0.80)
        for _ in range(3):
            t += dt
            x += 0.15 * dt
            mouse.apply(
                GestureResult(active_gesture="no_pinch", action="move_cursor", cursor_point=(x, y), is_moving=True, hand_shape="normal"),
                cfg,
                now=t,
            )
        return mouse.dpi_multiplier, mouse.c2f_active

    gain_no_c2f, active_no = _run_flick_then_decel(False)
    gain_with_c2f, active_yes = _run_flick_then_decel(True)
    assert not active_no
    assert active_yes
    assert gain_with_c2f < gain_no_c2f


def test_velocity_adaptive_filter_and_ui_magnet_precision_coupling() -> None:
    cfg = _base_velocity_cfg(
        tremor_filter=True,
        velocity_adaptive_filter=True,
        ui_magnet_enabled=True,
        ui_magnet_precision_scale=0.80,
        edge_reclutch_enabled=False,
    )
    mouse = MouseController(dry_run=True, screen_size=(1920, 1080))
    mouse.frame_aspect = 1.0
    mouse.slowdown_provider = lambda x, y: 0.50

    t = 0.0
    dt = 0.02
    x, y = 0.3, 0.5
    for _ in range(6):
        t += dt
        x += 0.25 * dt
        mouse.apply(
            GestureResult(
                active_gesture="no_pinch",
                action="move_cursor",
                cursor_point=(x, y),
                is_moving=True,
                hand_shape="precise",
            ),
            cfg,
            now=t,
        )
    # Adaptive filter sets min_cutoff to velocity_filter_c2f_cutoff (0.80) in precise mode
    assert math.isclose(mouse._tremor._fx.min_cutoff, 0.80, rel_tol=1e-6)


def test_workspace_edge_reclutch_freezes_inward_recovery_but_respects_drag_and_browse_lock() -> None:
    cfg = _base_velocity_cfg(
        edge_reclutch_enabled=True,
        edge_reclutch_margin=0.06,
        edge_reclutch_release_margin=0.12,
    )
    mouse = MouseController(dry_run=True, screen_size=(1920, 1080))
    mouse.frame_aspect = 1.0

    t = 0.0
    dt = 0.02
    # Move outward to the right camera border (hx >= 0.94)
    for hx in (0.88, 0.92, 0.96):
        t += dt
        mouse.apply(
            GestureResult(active_gesture="no_pinch", action="move_cursor", cursor_point=(hx, 0.5), is_moving=True, hand_shape="normal"),
            cfg,
            now=t,
        )
    edge_pos = mouse.last_screen_pos

    # Now reverse inward from 0.96 -> 0.93 -> 0.90 (still within release_margin=0.12 -> hx >= 0.88):
    # Edge reclutch must hold the cursor at edge_pos instead of pulling it left
    for hx in (0.93, 0.90):
        t += dt
        pos = mouse.apply(
            GestureResult(active_gesture="no_pinch", action="move_cursor", cursor_point=(hx, 0.5), is_moving=True, hand_shape="normal"),
            cfg,
            now=t,
        )
        assert mouse._edge_reclutch_active
        assert pos == edge_pos

    # While dragging, edge reclutch must NOT freeze movement
    mouse.is_dragging = True
    t += dt
    pos_drag = mouse.apply(
        GestureResult(active_gesture="drag_hold", action="drag_hold", cursor_point=(0.89, 0.5), is_moving=True, hand_shape="normal"),
        cfg,
        now=t,
    )
    assert not mouse._edge_reclutch_active


def test_depth_clutch_freezes_cursor_and_reanchors_without_jump() -> None:
    cfg = _base_velocity_cfg(
        return_suppression="clutch",
        clutch_size_ema_alpha=0.80,
        clutch_depth_enter_ratio=0.87,
        clutch_depth_exit_ratio=0.91,
        clutch_min_hold=0.15,
        edge_reclutch_enabled=False,
    )
    mouse = MouseController(dry_run=True, screen_size=(1920, 1080))
    mouse.frame_aspect = 1.0

    t = 0.0
    dt = 0.03
    hx = 0.30
    # 1. Forward stroke at normal hand_size = 0.24
    for _ in range(8):
        t += dt
        hx += 0.015
        mouse.apply(
            GestureResult(
                active_gesture="no_pinch",
                action="move_cursor",
                cursor_point=(hx, 0.5),
                is_moving=True,
                hand_shape="normal",
                distances={"hand_size": 0.24},
            ),
            cfg,
            now=t,
        )
    assert mouse.clutch_state == "inactive"
    frozen_pos = mouse.last_screen_pos
    assert 960 < frozen_pos[0] < 1800

    # 2. Pull hand back toward body (hand_size = 0.18, ratio = 0.75 <= 0.87) and move hand all the way left
    for back_x in (0.40, 0.35, 0.30, 0.27, 0.25):
        t += dt
        pos = mouse.apply(
            GestureResult(
                active_gesture="no_pinch",
                action="move_cursor",
                cursor_point=(back_x, 0.5),
                is_moving=True,
                hand_shape="normal",
                distances={"hand_size": 0.18},
            ),
            cfg,
            now=t,
        )
        assert mouse.clutch_state == "active"
        assert pos == frozen_pos

    # 3. Push hand forward (hand_size = 0.24) at new anchor (0.25, 0.5):
    # Must hold for clutch_min_hold (0.15s) before releasing, then re-anchor with ZERO jump
    for step_i in range(4):
        t += dt
        pos = mouse.apply(
            GestureResult(
                active_gesture="no_pinch",
                action="move_cursor",
                cursor_point=(0.25, 0.5),
                is_moving=True,
                hand_shape="normal",
                distances={"hand_size": 0.24},
            ),
            cfg,
            now=t,
        )
        assert pos == frozen_pos
        assert mouse.clutch_state == "active"

    # At >= 0.15s of exit candidate hold, clutch releases and re-anchors at (0.25, 0.5) with 0 jump
    t += 0.08
    pos_release = mouse.apply(
        GestureResult(
            active_gesture="no_pinch",
            action="move_cursor",
            cursor_point=(0.25, 0.5),
            is_moving=True,
            hand_shape="normal",
            distances={"hand_size": 0.24},
        ),
        cfg,
        now=t,
    )
    assert mouse.clutch_state == "inactive"
    assert pos_release == frozen_pos

    # Subsequent forward frame from (0.25, 0.5) -> (0.28, 0.5) moves smoothly rightward from frozen_pos
    t += dt
    pos_move = mouse.apply(
        GestureResult(
            active_gesture="no_pinch",
            action="move_cursor",
            cursor_point=(0.28, 0.5),
            is_moving=True,
            hand_shape="normal",
            distances={"hand_size": 0.24},
        ),
        cfg,
        now=t,
    )
    assert pos_move[0] > frozen_pos[0]


def test_depth_clutch_never_activates_during_drag() -> None:
    cfg = _base_velocity_cfg(
        return_suppression="clutch",
        clutch_size_ema_alpha=1.0,
        calibrated_hand_size=0.25,
    )
    mouse = MouseController(dry_run=True, screen_size=(1920, 1080))
    mouse.frame_aspect = 1.0
    t = 0.0
    for hx in (0.40, 0.45, 0.50):
        t += 0.03
        mouse.apply(
            GestureResult(
                active_gesture="drag_hold",
                action="drag_hold",
                cursor_point=(hx, 0.5),
                is_moving=True,
                hand_shape="normal",
                distances={"hand_size": 0.16},  # Way below 0.87 * 0.25
            ),
            cfg,
            now=t,
        )
        assert mouse.clutch_state == "inactive"


def test_calibrate_depth_clutch_computes_hysteresis_ratios() -> None:
    from src.calibration import CalibrationSession, calibrate_depth_clutch

    res = calibrate_depth_clutch([0.24, 0.25, 0.24, 0.25], [0.18, 0.19, 0.18, 0.18])
    assert res["ok"] is True
    assert 0.65 <= res["clutch_depth_enter_ratio"] < res["clutch_depth_exit_ratio"] <= 0.97

    sess = CalibrationSession(steps=())
    for s in (0.24, 0.25, 0.24, 0.25):
        sess.feed_depth_clutch_sample(s, pulled_back=False)
    for s in (0.18, 0.19, 0.18, 0.18):
        sess.feed_depth_clutch_sample(s, pulled_back=True)
    cal_out = sess.finish()
    assert "clutch_depth_enter_ratio" in cal_out.updates
    assert "clutch_depth_exit_ratio" in cal_out.updates


def test_auto_return_suppression_discards_buffered_return_stroke_and_freezes_cursor() -> None:
    cfg = _base_velocity_cfg(
        return_suppression="clutch_auto",
        edge_reclutch_enabled=False,
        return_auto_min_fwd_dist=0.035,
        return_auto_min_fwd_speed=0.16,
        return_auto_buffer_s=0.085,
        return_auto_confirm_dist=0.020,
        return_auto_release_pause=0.15,
    )
    mouse = MouseController(dry_run=True, screen_size=(1920, 1080))
    mouse.frame_aspect = 1.0

    t = 0.0
    dt = 0.025
    hx = 0.30
    # 1. Forward stroke rightward (0.30 -> 0.46)
    for _ in range(8):
        t += dt
        hx += 0.02
        mouse.apply(
            GestureResult(
                active_gesture="no_pinch",
                action="move_cursor",
                cursor_point=(hx, 0.5),
                is_moving=True,
                hand_shape="normal",
                distances={"hand_size": 0.22},
            ),
            cfg,
            now=t,
        )
    assert mouse.return_state == "none"
    fwd_end_pos = mouse.last_screen_pos
    assert 960 < fwd_end_pos[0] < 1850

    # 2. Immediate 180-deg return stroke back toward origin (0.46 -> 0.30):
    # First frame enters "pending" (deferred-commit buffer holds cursor at fwd_end_pos),
    # subsequent frames confirm return stroke ("confirmed"), discarding buffered steps -> 0 px wrong-way drift!
    seen_states = []
    for _ in range(8):
        t += dt
        hx -= 0.02
        pos = mouse.apply(
            GestureResult(
                active_gesture="no_pinch",
                action="move_cursor",
                cursor_point=(hx, 0.5),
                is_moving=True,
                hand_shape="normal",
                distances={"hand_size": 0.22},
            ),
            cfg,
            now=t,
        )
        seen_states.append(mouse.return_state)
        assert pos == fwd_end_pos

    assert "pending" in seen_states
    assert mouse.return_state == "confirmed"

    # 3. Pause at origin for >= return_auto_release_pause (0.15s) -> transitions to "released" with 0 jump
    for _ in range(7):
        t += 0.03
        pos = mouse.apply(
            GestureResult(
                active_gesture="no_pinch",
                action="move_cursor",
                cursor_point=(hx, 0.5),
                is_moving=True,
                hand_shape="normal",
                distances={"hand_size": 0.22},
            ),
            cfg,
            now=t,
        )
        assert pos == fwd_end_pos
    assert mouse.return_state in ("released", "none")

    # 4. Next forward stroke from re-anchored origin moves further right from fwd_end_pos without jump
    t += dt
    pos_next = mouse.apply(
        GestureResult(
            active_gesture="no_pinch",
            action="move_cursor",
            cursor_point=(hx + 0.025, 0.5),
            is_moving=True,
            hand_shape="normal",
            distances={"hand_size": 0.22},
        ),
        cfg,
        now=t,
    )
    assert pos_next[0] > fwd_end_pos[0]


def test_auto_return_suppression_flushes_pending_buffer_on_small_aim_correction() -> None:
    cfg = _base_velocity_cfg(
        return_suppression="clutch_auto",
        edge_reclutch_enabled=False,
        return_auto_min_fwd_dist=0.035,
        return_auto_min_fwd_speed=0.16,
        return_auto_buffer_s=0.085,
        return_auto_confirm_dist=0.025,
    )
    mouse = MouseController(dry_run=True, screen_size=(1920, 1080))
    mouse.frame_aspect = 1.0

    t = 0.0
    dt = 0.025
    hx = 0.30
    for _ in range(8):
        t += dt
        hx += 0.02
        mouse.apply(
            GestureResult(
                active_gesture="no_pinch",
                action="move_cursor",
                cursor_point=(hx, 0.5),
                is_moving=True,
                hand_shape="normal",
                distances={"hand_size": 0.22},
            ),
            cfg,
            now=t,
        )
    fwd_end_pos = mouse.last_screen_pos

    # Tiny 1-step aiming pullback (-0.005 widths < confirm_dist * 0.65), then stationary stop:
    # Step 1 enters "pending", Step 2 (stationary) flushes the buffered step without loss!
    t += dt
    hx -= 0.005
    pos_p = mouse.apply(
        GestureResult(
            active_gesture="no_pinch",
            action="move_cursor",
            cursor_point=(hx, 0.5),
            is_moving=True,
            hand_shape="normal",
            distances={"hand_size": 0.22},
        ),
        cfg,
        now=t,
    )
    assert mouse.return_state == "pending"
    assert pos_p == fwd_end_pos

    t += dt
    pos_flushed = mouse.apply(
        GestureResult(
            active_gesture="no_pinch",
            action="move_cursor",
            cursor_point=(hx, 0.5),
            is_moving=True,
            hand_shape="normal",
            distances={"hand_size": 0.22},
        ),
        cfg,
        now=t,
    )
    assert mouse.return_state == "none"
    assert pos_flushed[0] < fwd_end_pos[0]


def test_auto_return_suppression_never_activates_during_drag() -> None:
    cfg = _base_velocity_cfg(return_suppression="clutch_auto", edge_reclutch_enabled=False)
    mouse = MouseController(dry_run=True, screen_size=(1920, 1080))
    mouse.frame_aspect = 1.0
    t = 0.0
    dt = 0.025
    for hx in (0.30, 0.35, 0.40, 0.45, 0.40, 0.35, 0.30):
        t += dt
        mouse.apply(
            GestureResult(
                active_gesture="drag_hold",
                action="drag_hold",
                cursor_point=(hx, 0.5),
                is_moving=True,
                hand_shape="normal",
                distances={"hand_size": 0.22},
            ),
            cfg,
            now=t,
        )
        assert mouse.return_state == "none"


def test_failure_log_records_clutch_return_states_and_auto_detects_return_drift(tmp_path) -> None:
    import json
    from types import SimpleNamespace
    from src.failure_log import FailureRecorder

    def _hand(cx: float, cy: float, size: float = 0.12):
        pts = []
        for i in range(21):
            fx, fy = (i % 5) / 4 - 0.5, (i // 5) / 4 - 0.5
            pts.append(SimpleNamespace(x=cx + fx * size, y=cy + fy * size, z=0.0))
        return pts

    rec = FailureRecorder(tmp_path, before=1.5, after=0.3, cooldown=1.0)
    t = 0.0
    dt = 0.04
    kinds = []
    # Forward stroke from frame center (0.50 -> 0.80) and cursor (500 -> 900)
    for step_i in range(8):
        t += dt
        hx = 0.50 + step_i * 0.04
        cx = 500 + step_i * 60
        k = rec.record(
            t,
            landmarks=_hand(hx, 0.5),
            score=0.95,
            moving=True,
            cursor=[cx, 540],
            clutch_state="inactive",
            return_state="none",
            hand_shape_mode="normal",
        )
        if k:
            kinds.append(k)

    # Return stroke back toward center (0.78 -> 0.54) while cursor drifts back (920 -> 600)
    for step_i in range(1, 7):
        t += dt
        hx = 0.78 - step_i * 0.04
        cx = 920 - step_i * 55
        k = rec.record(
            t,
            landmarks=_hand(hx, 0.5),
            score=0.95,
            moving=True,
            cursor=[cx, 540],
            clutch_state="inactive",
            return_state="none",
            hand_shape_mode="normal",
        )
        if k:
            kinds.append(k)

    assert "return_drift" in kinds
    # Feed `after` frames so poll() writes the JSON
    for _ in range(12):
        t += dt
        rec.record(
            t,
            landmarks=_hand(0.52, 0.5),
            score=0.95,
            moving=False,
            cursor=[600, 540],
            clutch_state="inactive",
            return_state="none",
            hand_shape_mode="precise",
        )
    saved = rec.poll(t, {"frame_px": [1280, 720]})
    assert saved is not None and saved.exists()
    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["kind"] == "return_drift"
    assert payload["summary"].get("return_drift_px", 0) >= 180.0
    assert any("복귀" in h for h in payload["summary"]["hints"])
    assert "복귀 드리프트 1건" in rec.summary_text()


def test_failure_log_g_key_marks_return_blocked_fp_and_negative_candidate(tmp_path) -> None:
    from types import SimpleNamespace
    from src.failure_log import FailureRecorder

    lm = [SimpleNamespace(x=0.5, y=0.5, z=0.0) for _ in range(21)]
    rec = FailureRecorder(tmp_path, before=1.0, after=0.2, cooldown=0.5)
    for i in range(5):
        rec.record(
            i * 0.05,
            landmarks=lm,
            score=0.92,
            moving=False,
            cursor=[800, 500],
            clutch_state="inactive",
            return_state="confirmed",
            hand_shape_mode="normal",
        )
    ok = rec.mark_return_block_fp(0.25, note="User pressed G key")
    assert ok is True
    for i in range(6, 15):
        rec.record(
            i * 0.05,
            landmarks=lm,
            score=0.92,
            moving=False,
            cursor=[800, 500],
            clutch_state="inactive",
            return_state="released",
            hand_shape_mode="normal",
        )
    saved = rec.poll(1.0, {"frame_px": [1280, 720]})
    assert saved is not None and "return_blocked_fp" in saved.name
    neg_items = rec.load_negative_candidates()
    assert len(neg_items) == 1
    assert neg_items[0]["source"] == "g_key"
    assert neg_items[0]["auto_retrain"] is False
    assert neg_items[0]["rejection_reason"] == "return_blocked_fp"
    assert neg_items[0]["frames"][-1]["return_state"] == "confirmed"


def test_tracking_test_iso9241_throughput_overshoot_clutch_and_aggregation(tmp_path) -> None:
    from src.screens import Monitor
    from src.tracking_test import TrackingTest, aggregate_condition_results

    mon = Monitor(0, 0, 1920, 1080, primary=True)
    test = TrackingTest(mon, count=6, seed=42, include_far_targets=True, condition="velocity+clutch_auto")
    assert any(t.is_far_target for t in test.targets)

    t = 0.0
    dt = 1 / 30
    pos = mon.center
    target_idx = 0
    while not test.done:
        target = test.current
        cx, cy = test.center_px(target)
        # On target 0, simulate one overshoot (enter target briefly, leave, then settle) and one clutch event
        if target_idx == 0:
            test.update(t, (cx, cy), hand=True, active=True, clutch_state="inactive", return_state="none")
            t += dt
            test.update(t, (cx + target.size_px, cy), hand=True, active=True, clutch_state="active", return_state="confirmed")
            t += dt
        for step in range(25):
            k = min(1.0, (step + 1) / 8)
            x = pos[0] + (cx - pos[0]) * k
            y = pos[1] + (cy - pos[1]) * k
            test.update(t, (x, y), hand=True, active=True, fps=30, detect_ms=10, clutch_state="inactive", return_state="none")
            t += dt
            if test.current is not target:
                pos = (x, y)
                break
        target_idx += 1

    s = test.summary()
    assert s["reached"] == 6
    assert s["mean_id_bits"] is not None and s["mean_id_bits"] > 1.0
    assert s["mean_throughput_bps"] is not None and s["mean_throughput_bps"] > 0.5
    assert s["effective_throughput_bps"] is not None and s["effective_throughput_bps"] > 0.5
    assert s["total_overshoots"] >= 1
    assert s["total_clutches"] >= 1
    assert s["error_rate"] == 0.0

    csv_path = test.save_csv(tmp_path, {"condition": "velocity+clutch_auto", "movement_scheme": "velocity", "return_suppression": "clutch_auto"})
    agg = aggregate_condition_results([csv_path])
    assert "velocity+clutch_auto" in agg
    cond_stats = agg["velocity+clutch_auto"]
    assert cond_stats["runs"] == 1
    assert cond_stats["mean_throughput_bps"] == s["mean_throughput_bps"]
    assert cond_stats["mean_clutches"] == 1.0

