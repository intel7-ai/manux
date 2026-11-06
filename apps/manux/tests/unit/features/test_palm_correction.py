from types import SimpleNamespace

import pytest

from manux.features.pinch import PinchFeature
from manux.features.pinch.engine import PinchFrame


@pytest.mark.parametrize("action", ["move_cursor", "drag_hold", "scroll_grab"])
def test_palm_axes_correct_move_and_drag_but_preserve_scroll(action, monkeypatch):
    feature = PinchFeature()
    point = (.6, .7)
    monkeypatch.setattr(feature.core, "step", lambda *args: PinchFrame(action=action, cursor_point=point))
    context = SimpleNamespace(frame=SimpleNamespace(t=1., aspect=.5), settings={"palm_axes": (1., -.5, 0., 1.)})
    intents = feature.step(SimpleNamespace(landmarks=()), context)
    assert len(intents) == 1
    assert intents[0].point == pytest.approx(point if action == "scroll_grab" else (.57, .7))
