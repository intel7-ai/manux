"""Parity: the ported pointer gives the same per-frame output as v0.3 `PointerEngine`.

v0.3 code is loaded only here, from `hand-mouse/src` by path. `hand-mouse/src/__init__.py`
imports the camera (cv2), so a stand-in `src` package is used and removed afterwards; the four
modules the pointer needs (pointer_engine, pointer_workspace, gesture_engine and its stdlib-only
app_paths / profiles) are imported without it. `apps/manux/src` never imports `hand-mouse/`.

Frames: 12 core runs + 3 feature runs + 4 pinch-detector runs, 4,000 each (76,000). Exact equality (not approx): the port copies the arithmetic, so any difference is a behaviour change.
Compared per frame: action, cursor_point, pointer_point, preview_point, contacts, pointer_source,
warp, latched, speed, click_armed, trigger_ratio (NaN == NaN).

Known, intended difference (not exercised as a mismatch here): v0.3 records the real screen cursor
for the trigger rewind; v0.4 records the last point it sent. The v0.3 side below is fed the same
points the v0.4 feature records, as v0.3's runtime would with an absolute cursor.
"""
from __future__ import annotations

import copy
import math
import sys
import tempfile
import types
from collections import Counter
from pathlib import Path

import pytest

import pointer_hands as H
from manux.features.pointer import PointerFeature
from manux.features.pointer.engine import PointerCore
from manux.features.pointer.settings import THRESHOLDS, PointerSettings, to_v03_config
from manux.features.pointer.workspace import pointer_axes

HAND_MOUSE = Path(__file__).resolve().parents[5] / "hand-mouse"
FIELDS = ("action", "cursor_point", "pointer_point", "preview_point", "contacts", "pointer_source",
          "warp", "latched", "speed", "click_armed", "trigger_ratio")
FRAMES = 4000


@pytest.fixture(scope="module")
def v03():
    if not (HAND_MOUSE / "src" / "pointer_engine.py").exists():
        pytest.skip("hand-mouse/ (v0.3) is not in this checkout")
    saved = {k: v for k, v in sys.modules.items() if k == "src" or k.startswith("src.")}
    for k in saved:
        del sys.modules[k]
    stub = types.ModuleType("src")
    stub.__path__ = [str(HAND_MOUSE / "src")]
    sys.modules["src"] = stub
    try:
        from src import gesture_engine, pointer_engine, profiles  # type: ignore
        yield types.SimpleNamespace(ge=gesture_engine, pe=pointer_engine, profiles=profiles)
    finally:
        for k in [k for k in sys.modules if k == "src" or k.startswith("src.")]:
            del sys.modules[k]
        sys.modules.update(saved)


def _v03_engine(v03, tmp: Path):
    v03.profiles.RUN_OVERRIDE = None
    gestures = v03.ge.GestureEngine(config_path=tmp / "gestures.json")  # missing file: code defaults only
    return v03.pe.PointerEngine(gestures)


def _v03_config(v03, *, motion: bool = True) -> dict:
    """What v0.3 hands the pointer (`engine.config`): defaults + shipped gestures.json + pointer keys."""
    cfg = copy.deepcopy(v03.ge.DEFAULT_CONFIG)
    cfg.update(to_v03_config(THRESHOLDS))
    cfg["motion_enabled"] = motion
    cfg.pop("motion_pose_freeze", None)  # v0.3 reads it with default True
    return cfg


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    if isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _diff(old, new):
    if old is None or new is None:
        return None if old is None and new is None else ("frame", old, new)
    for name in FIELDS:
        if not _same(getattr(old, name), getattr(new, name)):
            return name, getattr(old, name), getattr(new, name)
    return None


AXES = pointer_axes((.5, .5), (.6, .54), (.53, .35), H.ASPECT)
SCENARIOS = [
    # (seed, click_style, anchor, tracking_only, axes, motion_enabled)
    (5, "pinch", "index", False, None, True),
    (2, "pinch", "palm", False, None, True),
    (5, "trigger", "index", False, None, True),
    (4, "trigger", "palm", False, AXES, True),
    (5, "off", "index", False, None, True),
    (6, "pinch", "index", True, AXES, False),
    # more pinch runs: its rarer branches (fist / three-finger lockout, one side pinch at a time,
    # nearest finger) only show up in some sequences
    (1, "pinch", "index", False, None, True),
    (3, "pinch", "index", False, None, False),
    (7, "pinch", "palm", False, None, True),
    (9, "pinch", "index", False, AXES, True),
    (10, "pinch", "palm", False, None, True),
    (14, "pinch", "index", False, None, True),
]


@pytest.mark.parametrize("seed,style,anchor,tracking,axes,motion", SCENARIOS)
def test_core_matches_v03_frame_by_frame(v03, seed, style, anchor, tracking, axes, motion):
    with tempfile.TemporaryDirectory() as tmp:
        old = _v03_engine(v03, Path(tmp))
    new = PointerCore()
    cfg = _v03_config(v03, motion=motion)
    s_old = v03.pe.PointerSettings(click_style=style, anchor=anchor, tracking_only=tracking, axes=axes, config=cfg)
    new_cfg = to_v03_config(THRESHOLDS)
    new_cfg["motion_pose_freeze"] = motion
    s_new = PointerSettings(click_style=style, anchor=anchor, tracking_only=tracking, axes=axes, config=new_cfg)
    seen = Counter()
    for n, (t, pts, active, detecting) in enumerate(H.random_frames(seed, FRAMES)):
        f_old = old.step(t, None if pts is None else [v03.ge.Point3D(*p) for p in pts], H.ASPECT, s_old,
                         active=active, detecting=detecting)
        f_new = new.step(t, None if pts is None else [_P(*p) for p in pts], H.ASPECT, s_new,
                         active=active, detecting=detecting)
        diff = _diff(f_old, f_new)
        assert diff is None, f"seed {seed} frame {n} t={t:.3f}: {diff}"
        if f_new is not None:
            seen[f_new.action] += 1
            seen["warp"] += f_new.warp is not None
            seen["latched"] += f_new.latched
            if f_new.cursor_point is not None:   # the caller's cursor, for the trigger rewind
                old.record_cursor(t, f_new.cursor_point)
                new.record_cursor(t, f_new.cursor_point)
    _coverage(style, tracking, seen)


def test_feature_matches_v03_including_the_trigger_rewind(v03):
    """The Feature wrapper (always active, records its own cursor) against v0.3 driven the same way."""
    total = Counter()
    for seed, style in ((11, "trigger"), (12, "pinch"), (13, "trigger")):
        with tempfile.TemporaryDirectory() as tmp:
            old = _v03_engine(v03, Path(tmp))
        feature = PointerFeature()
        s_old = v03.pe.PointerSettings(click_style=style, config=_v03_config(v03))
        cursor = None
        for n, (t, pts, _a, _d) in enumerate(H.random_frames(seed, FRAMES, with_inactive=False)):
            f_old = old.step(t, None if pts is None else [v03.ge.Point3D(*p) for p in pts], H.ASPECT, s_old,
                             active=True)
            hand = None if pts is None else H.observation(pts, t, n)
            intents = feature.step(hand, H.context(t, n, click_style=style))
            diff = _diff(f_old, feature.last_frame)
            assert diff is None, f"seed {seed} frame {n}: {diff}"
            if f_old is not None:
                if f_old.warp is not None:
                    cursor = f_old.warp
                if f_old.cursor_point is not None:
                    cursor = f_old.cursor_point
                if cursor is not None:
                    old.record_cursor(t, cursor)
                total[f_old.action] += 1
                total["warp"] += f_old.warp is not None
            for i in intents:
                total[(i.gesture, i.phase)] += 1
    assert total["warp"] > 0 and total["left_click"] > 0 and total["right_click"] > 0
    assert total[("pointer.drag", "begin")] == total[("pointer.drag", "end")] > 0
    assert total[("pointer.left_click", "tap")] == total["left_click"]
    assert total[("pointer.right_click", "tap")] == total["right_click"]


PINCH_STATE = ("_index_pinched", "_middle_hold_pinched", "_index_released_at", "_fist", "_secondary_since",
               "_secondary_off", "_click_fresh", "_lost_since", "_index_release_since", "_pinch_candidate_since")


@pytest.mark.parametrize("seed,motion", [(31, True), (32, True), (33, False), (34, True)])
def test_pinch_detector_matches_v03_gesture_engine_state_by_state(v03, seed, motion):
    """PointerPinch against v0.3 GestureEngine under the pointer's override (pointer_engine._pinch)."""
    with tempfile.TemporaryDirectory() as tmp:
        v03.profiles.RUN_OVERRIDE = None
        old = v03.ge.GestureEngine(config_path=Path(tmp) / "gestures.json")
    from manux.features.pointer.pinch import PointerPinch
    new = PointerPinch()
    cfg = _v03_config(v03, motion=motion)
    cfg.update(gestures=v03.pe.POINTER_GESTURES, record_fling_enabled=False, grab_scroll_enabled=False,
               long_press_drag=False)
    old.config.update(cfg)
    new_cfg = to_v03_config(THRESHOLDS)
    new_cfg["motion_pose_freeze"] = motion
    new.config.update(new_cfg)
    seen = Counter()
    for n, (t, pts, active, _d) in enumerate(H.random_frames(seed, FRAMES)):
        if not active:
            old.reset()
            new.reset()
            continue
        g = old.process(None if pts is None else [v03.ge.Point3D(*p) for p in pts], timestamp=t)
        r = new.process(None if pts is None else [_P(*p) for p in pts], t)
        click = g.action if g.action in ("left_click", "right_click") else None
        assert (click, g.active_gesture in v03.pe.POINTER_GESTURES) == (r.click, r.pinching), f"frame {n}"
        for name in PINCH_STATE:
            assert getattr(old, name) == getattr(new, name), f"seed {seed} frame {n}: {name}"
        seen[click] += 1
        seen["index"] += old._index_pinched
        seen["three"] += old._middle_hold_pinched
        seen["locked"] += (t - old._index_released_at) < .35
    assert seen["left_click"] and seen["right_click"] and seen["index"] and seen["three"] and seen["locked"]


class _P:
    __slots__ = ("x", "y", "z")

    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


def _coverage(style, tracking, seen):
    """The synthetic sequence must exercise the paths of its click style, or parity proves little."""
    assert seen["move_cursor"] > 100 and seen["latched"] > 0
    if tracking or style == "off":
        return
    assert seen["left_click"] + seen["right_click"] > 0 and seen["pointer_wait"] > 0
    if style == "pinch":
        assert seen["pointer_release"] > 0
    if style == "trigger":
        assert seen["drag_hold"] > 0 and seen["warp"] > 0
