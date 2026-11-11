"""Unit tests for camera frame boundary proximity calculation."""
from __future__ import annotations

import pytest

from manux.core.boundary import EdgeProximity, calc_edge_proximity


def _make_hand(center_x: float = 0.5, center_y: float = 0.5, span: float = 0.05) -> list[tuple[float, float, float]]:
    """Create a synthetic 21-point hand bounded within [center - span, center + span]."""
    pts = []
    for i in range(21):
        angle = i * 0.3
        r = span * (0.5 + 0.5 * (i % 5) / 5.0)
        pts.append((center_x + r * 0.8, center_y + r * 0.8, 0.0))
    return pts


def test_boundary_proximity_none_or_empty():
    res = calc_edge_proximity(None)
    assert not res.active
    assert res.left == 0.0 and res.right == 0.0 and res.top == 0.0 and res.bottom == 0.0

    res_short = calc_edge_proximity([(0.5, 0.5, 0.0)] * 20)
    assert not res_short.active


def test_boundary_proximity_center_is_safe():
    hand = _make_hand(0.5, 0.5, span=0.1)
    res = calc_edge_proximity(hand, margin=0.06)
    assert not res.active
    assert res.left == 0.0
    assert res.right == 0.0
    assert res.top == 0.0
    assert res.bottom == 0.0
    assert res.max_intensity == 0.0


def test_boundary_proximity_right_edge():
    # Place hand so maximum x is 0.98. Margin is 0.06 -> dist = 1.0 - 0.98 = 0.02 -> intensity = (0.06 - 0.02) / 0.06 = 0.6667
    hand = _make_hand(0.95, 0.5, span=0.03)  # max x = 0.95 + 0.03*0.8 = ~0.974
    # Explicitly ensure one joint is at x=0.98
    hand[8] = (0.98, 0.5, 0.0)
    res = calc_edge_proximity(hand, mirrored=True, margin=0.06)
    assert res.active
    assert res.right == pytest.approx((0.06 - 0.02) / 0.06, abs=1e-3)
    assert res.left == 0.0
    assert res.top == 0.0
    assert res.bottom == 0.0


def test_boundary_proximity_left_edge():
    hand = _make_hand(0.1, 0.5, span=0.03)
    hand[4] = (0.015, 0.5, 0.0)  # dist = 0.015 -> intensity = (0.06 - 0.015) / 0.06 = 0.75
    res = calc_edge_proximity(hand, mirrored=True, margin=0.06)
    assert res.active
    assert res.left == pytest.approx(0.75, abs=1e-3)
    assert res.right == 0.0


def test_boundary_proximity_top_and_bottom():
    hand_top = _make_hand(0.5, 0.08, span=0.04)
    hand_top[12] = (0.5, 0.01, 0.0)
    res_top = calc_edge_proximity(hand_top, margin=0.06)
    assert res_top.active
    assert res_top.top > 0.8
    assert res_top.bottom == 0.0

    hand_bot = _make_hand(0.5, 0.92, span=0.04)
    hand_bot[0] = (0.5, 0.99, 0.0)
    res_bot = calc_edge_proximity(hand_bot, margin=0.06)
    assert res_bot.active
    assert res_bot.bottom == pytest.approx((0.06 - 0.01) / 0.06, abs=1e-3)
    assert res_bot.top == 0.0


def test_boundary_proximity_diagonal_corner():
    hand = _make_hand(0.95, 0.05, span=0.03)
    hand[8] = (0.98, 0.02, 0.0)  # Near top-right corner
    res = calc_edge_proximity(hand, mirrored=True, margin=0.06)
    assert res.active
    assert res.right > 0.0
    assert res.top > 0.0
    assert res.left == 0.0
    assert res.bottom == 0.0


def test_boundary_proximity_mirrored_false():
    # If not mirrored, camera x=0 is screen right (1.0 - x)
    hand = _make_hand(0.5, 0.5, span=0.02)
    hand[8] = (0.01, 0.5, 0.0)  # in camera x=0.01. With mirrored=False, sx = 0.99 -> near right!
    res = calc_edge_proximity(hand, mirrored=False, margin=0.06)
    assert res.active
    assert res.right > 0.8
    assert res.left == 0.0


def test_boundary_proximity_past_edge_clamps_to_one():
    hand = _make_hand(0.5, 0.5, span=0.02)
    hand[8] = (1.05, 0.5, 0.0)  # already past edge
    res = calc_edge_proximity(hand, mirrored=True, margin=0.06)
    assert res.right == 1.0

