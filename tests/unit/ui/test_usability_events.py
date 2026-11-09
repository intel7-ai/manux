"""UI reads latest images but must not lose accepted practice events between polls."""
import pytest
pytest.importorskip("PySide6.QtQml")

from test_bridge import bridge, drained
from manux.core.contracts import PracticeEvent
from manux.engine.commands import SetControl
from manux.ui.cards import cards
from manux.ui.tutorial import TARGETS, Tutorial, onboarding_steps
from manux.ui.view import ViewFrame


def test_targets_use_retained_click_position_even_after_cursor_has_moved(bridge, monkeypatch):
    b, _ = bridge
    b.tutorial = Tutorial([onboarding_steps(cards(False))[1]])
    b._practice_min_epoch = 1
    events = tuple(PracticeEvent(i+1, 10+i*.1, ("mouse.left_click",), p, i == 0, False, True)
                   for i, p in enumerate(TARGETS))
    monkeypatch.setattr("manux.ui.bridge.time.perf_counter", lambda: 10.3)
    view = ViewFrame(object(), landmarks=((.5,.5,0.),)*21, actions=("mouse.move",),
                     practice_cursor=(.95,.95), practice_events=events, practice_epoch=1)
    b._tutorial_frame(view)
    assert b.tutorial.done == [True]
    assert b._last_practice_seq == 3


def test_old_step_events_are_not_reused_and_each_v_event_is_consumed_once(bridge, monkeypatch):
    b, _ = bridge
    control = next(step for step in onboarding_steps(cards(False)) if step["key"] == "control")
    b.tutorial = Tutorial([control])
    b._practice_min_epoch = 2
    monkeypatch.setattr("manux.ui.bridge.time.perf_counter", lambda: 10.3)
    event = PracticeEvent(1, 10., ("control.toggle",), (.5,.5), False, False, True)
    b._tutorial_frame(ViewFrame(object(), practice_events=(event,), practice_epoch=1))
    assert b.tutorial.progress == 0
    event2 = PracticeEvent(2, 10.1, ("control.toggle",), (.5,.5), False, False, True)
    view = ViewFrame(object(), practice_events=(event2,), practice_epoch=2)
    b._tutorial_frame(view)
    b._tutorial_frame(view)
    assert b.tutorial.progress == .5 and not b.tutorial.done[0]
    event3 = PracticeEvent(3, 10.2, ("control.toggle",), (.5,.5), False, False, True)
    b._tutorial_frame(ViewFrame(object(), practice_events=(event2,event3), practice_epoch=2))
    assert b.tutorial.done == [True]


def test_settings_always_send_pause_when_ui_has_not_observed_engine_v_toggle(bridge):
    b, runtime = bridge
    b._set_stage("main")
    drained(runtime)
    b.paused = True  # stale UI: the engine has just enabled via V, before the next poll
    b.setClickStyle("trigger")
    assert SetControl(False) in drained(runtime)


@pytest.mark.parametrize("key", ["move", "vertical"])
def test_cursor_steps_refresh_the_practice_cursor_every_frame(bridge, monkeypatch, key):
    b, _ = bridge
    steps = onboarding_steps(cards(False), palm=True, pointer_setup=True)
    b.tutorial = Tutorial([next(s for s in steps if s["key"] == key)])
    seen = []
    b.tutorialChanged.connect(lambda: seen.append(b.tutorialCursor))
    for i, x in enumerate((.3, .4, .5)):
        monkeypatch.setattr("manux.ui.bridge.time.perf_counter", lambda i=i: 10. + i * .03)
        b._tutorial_frame(ViewFrame(object(), landmarks=((.5, .5, 0.),) * 21, actions=(),
                                    practice_cursor=(x, .5)))
    assert [round(c[0], 2) for c in seen[-3:]] == [.3, .4, .5]   # was only refreshed on the "speed" step
