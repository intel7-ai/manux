import pytest

from manux.features.pinch import DRAG, IntentMapper, PinchFeature
from manux.features.pinch.settings import DEFAULTS

from pinch_hands import (
    check_pairs, ctx, middle_pinch, obs, open_hand, pinch_sequence,
    v03_modules, v03_shipped_config,
)


@pytest.mark.parametrize("seed", range(5))
def test_pinch_replay_matches_v03(seed):
    with v03_modules() as v03:
        config = v03_shipped_config(v03.ge)
        legacy = v03.ge.GestureEngine()
        legacy.config = config
        values = {key: config.get(key, default) for key, default in DEFAULTS.items()}
        feature = PinchFeature()
        mapper = IntentMapper()
        lost_since = None
        received = []
        for seq, (t, points) in enumerate(pinch_sequence(seed, 1000)):
            if points is None:
                lost_since = t if lost_since is None else lost_since
                if t - lost_since >= config["hand_lost_reset"]:
                    feature.reset("hand_lost")
                    mapper.clear()
                    received.clear()
            else:
                lost_since = None
            result = legacy.process(points, t)
            expected = mapper.map(t, result if points is not None else None)
            hand = obs(t, points, seq) if points is not None else None
            actual = feature.step(hand, ctx(t, settings=values, seq=seq))
            assert actual == expected, (seed, seq, result, feature.last_frame)
            received.extend(actual)
            check_pairs(received)


def test_drag_survives_short_loss_and_ends_on_release():
    feature = PinchFeature()
    intents = []
    for frame in range(20):
        t = 1 + frame / 30
        intents.extend(feature.step(obs(t, middle_pinch()), ctx(t, settings={"long_press_time": 0.4})))
    assert any(i.gesture == DRAG and i.phase == "begin" for i in intents)
    assert feature.activity().holding
    assert feature.step(None, ctx(1.67)) == []
    assert feature.activity().holding
    for frame in range(20):
        t = 1.7 + frame / 30
        intents.extend(feature.step(obs(t, open_hand()), ctx(t)))
    check_pairs(intents)
    assert any(i.gesture == DRAG and i.phase == "end" for i in intents)
    feature.reset("hand_lost")
    assert not feature.activity().holding
    assert feature.step(None, ctx(2.4)) == []
