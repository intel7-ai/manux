"""Recording alone, far from the screen: a countdown after [start] and the next set starting by itself."""
from src.gesture_catalog import BY_KEY
from src.guided_recording import GuidedSession

CFG = {"rec_reps": 2, "rec_prep": 1.0, "rec_do": 1.0, "rec_rest": 1.0, "rec_start_delay": 5.0, "rec_auto_next": 4.0}


def session(**over):
    return GuidedSession({**CFG, **over}, tasks=[BY_KEY["thumb_ring"]], conditions=("a", "b"))


def test_nothing_is_recorded_during_the_start_countdown_and_it_does_not_wait_for_the_hand():
    s = session()
    s.advance(100.0)
    assert s.countdown(101.0) == 4.0
    assert s.label(102.0) == {"target": "idle", "phase": "waiting"}      # not training data
    s.update(102.0, available=False)                                       # nobody in front of the camera yet
    s.update(104.0, available=False)
    assert s.countdown(104.5) == 0.5                                       # the countdown kept running
    assert "초 뒤 시작" in s.view(101.0)["prompt"]
    assert s.label(105.2)["phase"] == "prep"                               # then the set starts as usual
    assert s.label(106.2)["phase"] == "do"


def test_without_delay_nothing_changes():
    s = session(rec_start_delay=0.0, rec_auto_next=0.0)
    s.advance(100.0)
    assert s.countdown(100.0) == 0.0 and s.label(100.5)["phase"] == "prep"
    s.update(100.5)
    assert s.update(106.0) is True                                         # 2 reps x 3 s
    assert s.running is False and s.index == 1                             # waits for [start] again


def test_the_next_set_starts_by_itself_after_the_pause_and_the_last_one_ends_the_session():
    s = session()
    s.advance(100.0)
    s.update(105.0)
    assert s.update(111.0) is True                                         # set 1 over (5 s delay + 6 s)
    assert s.running is True and s.index == 1 and s.countdown(111.5) == 3.5
    assert s.label(112.0) == {"target": "idle", "phase": "waiting"}
    s.update(115.0)
    assert s.label(115.5)["phase"] == "prep"
    assert s.update(121.0) is True
    assert s.done is True and s.running is False                           # nothing left to start


def _run_to_the_end(s):
    s.advance(0.0)
    s.update(0.0)
    s.update(6.0)            # set 1 over (2 reps x 3 s); set 2 waits for [start] because auto-next is off
    s.advance(6.0)
    s.update(6.0)
    s.update(12.0)           # set 2 over: the session is finished


def test_a_finished_session_asks_to_be_saved_by_itself_when_configured():
    s = session(rec_auto_save=6.0, rec_auto_next=0.0, rec_start_delay=0.0)
    _run_to_the_end(s)
    view = s.view(13.0)
    assert view["finished"] is True and view["auto_save"] == 6.0
    off = session(rec_auto_save=0.0, rec_start_delay=0.0, rec_auto_next=0.0)
    _run_to_the_end(off)
    assert off.view(13.0)["auto_save"] == 0.0                         # off: the button is still needed
