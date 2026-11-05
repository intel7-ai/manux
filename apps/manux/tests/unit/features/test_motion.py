import pytest

from manux.core.contracts import Activity
from manux.features.motion import MotionFeature, entry_blocked
from manux.features.motion.detector import MotionCore

from pinch_hands import ctx, motion_hand, motion_sequence, obs, v03_modules


@pytest.mark.parametrize("seed", range(4))
def test_motion_replay_matches_v03(seed):
    with v03_modules() as v03:
        legacy = v03.mm.MotionMode()
        current = MotionCore()
        for t, points, request, blocked in motion_sequence(seed, 2000):
            expected = legacy.update(t, points, request, blocked)
            actual = current.update(t, points, request, blocked)
            assert current.active == legacy.active
            assert ((actual.kind, actual.info) if actual else None) == (
                (expected.kind, expected.info) if expected else None
            )


def test_motion_requests_entry_after_the_other_feature_releases():
    feature = MotionFeature()
    hand = motion_hand(fingers="folded")
    for i in range(30):
        t = 1 + i / 30
        assert feature.step(obs(t, hand), ctx(t, others={"pinch": Activity(holding=True)})) == []
    assert entry_blocked({"pinch": Activity(last_release_t=2)}, 2.5, 1.0)
    assert not entry_blocked({"pinch": Activity(last_release_t=2)}, 3, 1.0)
    intents = []
    for i in range(30):
        t = 3 + i / 30
        intents.extend(feature.step(obs(t, hand), ctx(t)))
        if intents:
            break
    assert [i.gesture for i in intents] == ["motion.enter"]
    assert feature.step(obs(t + 0.04, hand), ctx(t + 0.04, mode="motion")) == []
    feature.reset("shutdown")
    assert feature.activity() == Activity()
