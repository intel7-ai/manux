"""Synthetic movement/click/drag sequences through the real router and fake Windows backend."""
from dataclasses import replace
import math

import pytest

from manux.app import build_engine, schema
from manux.core.contracts import OutputTarget, Stage
from manux.engine.commands import SetControl, SetStage
from manux.features.pinch import DRAG, MOVE_FIST, THUMB_MIDDLE, THUMB_RING, PinchFeature
from manux.output.windows import WindowsOutputSink
from manux.output.windows.backend import FakeBackend
from manux.output.windows.config import WindowsOutputConfig
from manux.platform.monitors import ScreenLayout
from manux.settings.store import Settings
import pinch_hands as H
from test_click_world_contact import world_contact
from test_pinch_ownership import feed, pose, taps


@pytest.mark.parametrize("finger,wanted", [("middle", THUMB_MIDDLE), ("ring", THUMB_RING)])
@pytest.mark.parametrize("fps", [14, 30])
def test_latched_fist_yields_to_clear_contact_without_opening_the_hand(finger, wanted, fps):
    feature = PinchFeature()
    hand = H.fist()
    moving = feed(feature, [(hand, world_contact(hand))] * fps, fps=fps)
    assert any(i.gesture == MOVE_FIST for i in moving)
    out = feed(feature, [(hand, world_contact(hand, finger))] * math.ceil(.22 * fps), start=2., fps=fps)
    assert taps(out) == [wanted]
    assert not any(i.gesture == MOVE_FIST for i in out)


@pytest.mark.parametrize("finger,wanted", [("middle", THUMB_MIDDLE), ("ring", THUMB_RING)])
@pytest.mark.parametrize("with_world", [False, True])
def test_short_click_after_fist_movement_has_no_scroll_lockout(finger, wanted, with_world):
    feature = PinchFeature()
    hand = H.fist()
    feed(feature, [(hand, world_contact(hand) if with_world else None)] * 20)
    points, world = pose(finger)
    out = feed(feature, [(points, world if with_world else None)] * 7, start=1.7)
    assert taps(out) == [wanted]


def test_clear_contact_can_start_while_projection_looks_like_thumbs_up():
    hand = H.thumbs_up()
    assert taps(feed(PinchFeature(), [(hand, world_contact(hand, "middle"))] * 10)) == [THUMB_MIDDLE]


def test_ambiguous_folded_hand_stays_movement_without_click_or_window_command():
    hand = H.fist()
    world = list(world_contact(hand))
    palm = math.dist(world[0], world[9])
    for tip in (8, 12, 16, 20):
        world[tip] = (world[4][0] + .2 * palm, world[4][1], world[4][2])
    out = feed(PinchFeature(), [(hand, tuple(world))] * 60)
    assert taps(out) == []
    assert any(i.gesture == MOVE_FIST for i in out)
    assert not any(i.gesture == DRAG for i in out)


@pytest.mark.parametrize("fps", [14, 30])
@pytest.mark.parametrize("image_scale", [1., .45])
def test_move_click_drag_release_and_next_click_reach_windows_in_order(fps, image_scale):
    settings = Settings(schema(), {"pinch.long_press_time": 1., "pinch.long_press_drag_multiplier": 2.,
                                   "cursor.vertical_input": "hand_position"})
    engine = build_engine(settings, mode="pinch")
    backend = FakeBackend((960, 540))
    clock = [1.]
    sink = WindowsOutputSink(backend, ScreenLayout.single(1920, 1080),
                             WindowsOutputConfig(**settings.section("cursor")),
                             threaded=False, clock=lambda: clock[0])
    engine.sinks[OutputTarget.WINDOWS] = sink
    engine.commands.put(SetStage(Stage.MAIN))
    engine.commands.put(SetControl(True))
    sequence = 0
    results = []

    def hold(points, world, seconds, *, move=False):
        nonlocal sequence
        snaps = []
        for i in range(math.ceil(seconds * fps)):
            clock[0] = 1. + sequence / fps
            hand = tuple((.5 + (x - .5) * image_scale + (i / fps * .04 if move else 0.),
                          .5 + (y - .5) * image_scale, z * image_scale) for x, y, z in points)
            obs = replace(H.obs(clock[0], hand, seq=sequence), world=world)
            snap = engine.step(obs.frame, obs)
            assert not snap.errors and snap.control_on and snap.mode == "pinch"
            assert all(reason == "ok" for _, _, reason in snap.results)
            results.extend(snap.results)
            snaps.append(snap)
            sequence += 1
        return snaps

    fist = H.fist()
    try:
        hold(fist, world_contact(fist), .4, move=True)
        # Clicks are shorter than the old erroneous 0.35 s fist-exit lockout.
        hold(*pose("ring"), .22)
        assert backend.clicks() == ["right"]
        hold(*pose("open"), .3)
        hold(fist, world_contact(fist), .4)
        # The same curled image can carry real thumb-middle contact in depth.
        hold(fist, world_contact(fist, "middle"), 1.2)
        assert backend.buttons_down == {"left"}
        position = backend.pos
        hold(fist, world_contact(fist, "middle"), .4, move=True)
        assert backend.pos != position and backend.buttons_down == {"left"}
        hold(*pose("open"), .3)
        assert not backend.buttons_down
        hold(*pose("ring"), .22)
        hold(*pose("open"), .3)
        hold(*pose("middle"), .22)
        hold(*pose("open"), .3)
        assert backend.clicks() == ["right", "left", "left", "right", "left"]
        assert not backend.buttons_down and not backend.keys_down
        assert not any(e[0] in ("key", "wheel") for e in backend.events)
        assert sum(a == "mouse.drag" and p == "begin" for a, p, _ in results) == 1
        assert sum(a == "mouse.drag" and p == "end" for a, p, _ in results) == 1
        assert settings.get("pinch.long_press_drag_multiplier") == 2.
    finally:
        engine.shutdown()
