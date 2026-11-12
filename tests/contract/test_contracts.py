"""Checks the contract types of src/manux/core/contracts.py (docs/contracts.md)."""
from __future__ import annotations

import math

import pytest

from manux.core.contracts import (
    STAGE_OUTPUT, ActionCommand, Activity, FrameContext, FrameInfo, HandObservation, Intent,
    OutputResult, OutputTarget, Stage,
)

HAND = tuple((i / 40, i / 40, 0.0) for i in range(21))


def frame(**kw) -> FrameInfo:
    return FrameInfo(**{"seq": 0, "t": 1.0, "width": 1280, "height": 720, "mirrored": True, **kw})


def test_frame_aspect_and_validation():
    assert frame().aspect == pytest.approx(720 / 1280)
    for bad in ({"width": 0}, {"rotation": 45}, {"seq": -1}, {"t": math.nan}):
        with pytest.raises(ValueError):
            frame(**bad)


def test_hand_needs_21_finite_points_and_a_physical_side():
    hand = HandObservation(frame(), HAND, "Right", 0.9)
    assert len(hand.landmarks) == 21 and hand.world is None
    with pytest.raises(ValueError):
        HandObservation(frame(), HAND[:20], "Right", 0.9)
    with pytest.raises(ValueError):
        HandObservation(frame(), HAND[:20] + ((math.inf, 0.0, 0.0),), "Right", 0.9)
    with pytest.raises(ValueError):
        HandObservation(frame(), HAND, "left", 0.9)
    with pytest.raises(ValueError):
        HandObservation(frame(), HAND, "Left", 1.5)


def test_intent_ids_keep_the_logical_namespace_and_identify_the_producer():
    ok = Intent(t=1.0, source="pinch", gesture="pinch.thumb_middle", phase="tap", point=(0.5, 0.5))
    assert ok.gesture == "pinch.thumb_middle"
    with pytest.raises(ValueError):
        Intent(t=1.0, source="pinch", gesture="left_click", phase="tap")          # v0.3 action name
    direct = Intent(t=1.0, source="tcn", gesture="pinch.thumb_middle", phase="tap")
    assert direct.source == "tcn" and direct.gesture == "pinch.thumb_middle"
    with pytest.raises(ValueError):
        Intent(t=1.0, source="TCN", gesture="pinch.thumb_middle", phase="tap")
    with pytest.raises(ValueError):
        Intent(t=1.0, source="pinch", gesture="pinch.thumb_middle", phase="press")
    with pytest.raises(ValueError):
        Intent(t=1.0, source="pinch", gesture="pinch.thumb_middle", phase="tap", confidence=2.0)


def test_values_passed_between_layers_cannot_be_changed():
    intent = Intent(t=1.0, source="pinch", gesture="pinch.thumb_ring", phase="tap", info={"k": 1})
    with pytest.raises(AttributeError):
        intent.phase = "begin"  # type: ignore[misc]
    with pytest.raises(TypeError):
        intent.info["k"] = 2  # type: ignore[index]
    ctx = FrameContext(frame(), Stage.MAIN, OutputTarget.WINDOWS, "pointer", others={"pinch": Activity()})
    with pytest.raises(TypeError):
        ctx.others["pinch"] = Activity(holding=True)  # type: ignore[index]


def test_frame_context_distinguishes_actual_mode_without_changing_classifier_mode():
    legacy = FrameContext(frame(), Stage.MAIN, OutputTarget.WINDOWS, "pinch")
    assert legacy.active_mode == legacy.mode == "pinch"
    overlay = FrameContext(frame(), Stage.MAIN, OutputTarget.WINDOWS, "pinch", active_mode="motion")
    assert overlay.mode == "pinch" and overlay.active_mode == "motion"
    with pytest.raises(AttributeError):
        overlay.active_mode = "pinch"


def test_action_command_ids():
    cmd = ActionCommand(t=1.0, action="mouse.left_click", phase="tap", gesture="pinch.thumb_middle")
    assert cmd.action == "mouse.left_click"
    with pytest.raises(ValueError):
        ActionCommand(t=1.0, action="left_click", phase="tap", gesture="pinch.thumb_middle")


def test_output_result_reason_matches_accepted():
    assert OutputResult(True).reason == "ok"
    assert not OutputResult(False, "error:send failed").accepted
    with pytest.raises(ValueError):
        OutputResult(True, "blocked")
    with pytest.raises(ValueError):
        OutputResult(False, "nope")


def test_only_main_reaches_windows_and_the_tutorial_only_practice():
    assert {s for s, o in STAGE_OUTPUT.items() if o is OutputTarget.WINDOWS} == {Stage.MAIN}
    assert STAGE_OUTPUT[Stage.TUTORIAL] is OutputTarget.PRACTICE
    assert set(STAGE_OUTPUT) == set(Stage)


def test_intent_scale_is_a_positive_number_or_none():
    assert Intent(t=1.0, source="pinch", gesture="pinch.move", phase="hold", scale=2.5).scale == 2.5
    assert Intent(t=1.0, source="pinch", gesture="pinch.move", phase="hold").scale is None
    for bad in (0.0, -1.0, math.inf, math.nan):
        with pytest.raises(ValueError):
            Intent(t=1.0, source="pinch", gesture="pinch.move", phase="hold", scale=bad)
