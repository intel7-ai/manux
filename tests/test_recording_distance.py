"""More distances, a distance course to walk through, and recommended counts per gesture."""
from src.gesture_catalog import BY_KEY
from src.guided_recording import GuidedSession
from src.recording_plan import DISTANCE_COURSE, OPTIONS, condition_key, normalise, preset, task_timings


def test_there_are_many_distances_the_old_ones_keep_their_places_and_a_plan_accepts_them():
    d = OPTIONS["distance"]
    assert d[:3] == ["가까이 (약 30cm)", "보통 (약 60cm)", "멀리 (약 100cm)"]
    assert d[-1] == "직접 지정" and len(d) >= 10
    for needed in ("약 1.5m", "약 2m", "약 2.5m", "약 3m", "약 4m", "약 5m"):
        assert needed in d
    plan = preset("basic", focus="thumb_index")
    plan["conditions"][0]["distance"] = "약 3m"
    assert normalise(plan)["conditions"][0]["distance"] == "약 3m"


def test_the_distance_course_has_one_posture_per_distance_and_each_is_its_own_condition():
    for focus in ("thumb_index", "thumbs_up", "swipe_left", "none", ""):
        rows = preset("distance", focus=focus)["conditions"]
        assert [r["distance"] for r in rows] == list(DISTANCE_COURSE)
        assert len({condition_key(r) for r in rows}) == len(rows)        # the distance alone tells them apart: no memo needed
        assert [r["name"] for r in rows] == ["거리 코스 1/3 · 약 1m", "거리 코스 2/3 · 약 2m", "거리 코스 3/3 · 약 3m"]
        normalise({"schema": "manux-capture-plan/1", "conditions": rows})   # and the plan is valid


def test_recommended_counts_and_times_per_kind_of_gesture():
    click = preset("basic", focus="thumb_middle")["conditions"][0]
    assert (click["reps"], click["prep"], click["hold"], click["rest"]) == (12, 2.0, 1.5, 2.0)
    scroll = preset("basic", focus="thumb_index")["conditions"][0]
    assert (scroll["reps"], scroll["hold"]) == (10, 3.0)
    sign = preset("basic", focus="v_sign")["conditions"][0]
    assert (sign["reps"], sign["hold"]) == (8, 2.5)
    assert preset("basic", focus="none")["conditions"][0]["free_seconds"] == 120
    assert preset("basic", focus="near_thumb_index")["conditions"][0]["free_seconds"] == 60
    assert task_timings("") is None and task_timings("swipe_left") is None
    motion = preset("basic", focus="swipe_left")["conditions"][0]            # the motions keep their own numbers
    assert (motion["reps"], motion["hold"]) == (10, 1.2)
    course = preset("distance", focus="swipe_left")["conditions"][0]
    assert (course["reps"], course["hold"]) == (8, 1.2)
    for focus in ("thumb_middle", "thumb_ring", "thumb_index", "thumb_pinky", "palm_side", "thumbs_up", "v_sign", "rock_sign"):
        assert BY_KEY[focus]                                                # every name used above is a real gesture


def test_the_countdown_tells_where_to_stand_next():
    plan = preset("distance", focus="thumb_index")
    session = GuidedSession({"rec_start_delay": 5.0, "rec_auto_next": 5.0}, tasks=[BY_KEY["thumb_index"]], plan=plan)
    session.advance(0.0)
    assert "약 1m 자리로 가세요" in session.view(1.0)["prompt"]
    session.update(5.0)
    length = session._set_length(BY_KEY["thumb_index"])
    session.update(5.0 + length + 0.1)                       # the first set is over, the next one counts down
    assert "약 2m 자리로 가세요" in session.view(5.0 + length + 0.5)["prompt"]
