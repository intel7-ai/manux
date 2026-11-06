"""Personal calibration logic (synthetic hands only: no camera, no model)."""
import json
import random

from src.calibration import (CalibrationSession, ENTER_KEYS, FINGER_TIPS, OPEN_MIN_INDEX, STEPS, analyze_pinch,
                             analyze_selectivity, apply_updates, report_dict)

TIP_X = {"index": 0.50, "middle": 0.56, "ring": 0.62, "pinky": 0.68}   # fingertips 0.06 apart in a row


def hand(thumb_at=None, thumb_dx=0.0, wobble=0.0, rng=None):
    """21 points; fingertips in a row at y=0.4. The thumb tip sits `thumb_dx` to the right of the chosen
    finger's tip (None = far from everything)."""
    r = rng or random.Random(0)
    pts = [(0.5 + (i % 5) * 0.02, 0.6 + (i // 5) * 0.02) for i in range(21)]
    for f, tip in FINGER_TIPS.items():
        pts[tip] = (TIP_X[f], 0.40)
    pts[4] = (0.30, 0.80)
    if thumb_at is not None:
        pts[4] = (TIP_X[thumb_at] + thumb_dx, 0.40)
    if wobble:
        pts = [(x + r.gauss(0, wobble), y + r.gauss(0, wobble)) for x, y in pts]
    return pts


def pinch_series(contact=0.02, opened=0.10, reps=3, fps=30, hold=1.0, gap=0.8, t0=0.0):
    out, t = [], t0
    for _ in range(reps):
        for _ in range(int(gap * fps)):
            out.append((t, opened)); t += 1 / fps
        for _ in range(int(hold * fps)):
            out.append((t, contact)); t += 1 / fps
    for _ in range(int(gap * fps)):
        out.append((t, opened)); t += 1 / fps
    return out


# ---------------------------------------------------------------------- thumb + index distances
def test_clean_index_pinches_give_good_quality_and_thresholds_between_contact_and_open():
    r = analyze_pinch(pinch_series(contact=0.02, opened=0.10), OPEN_MIN_INDEX)
    assert r.ok and r.quality == "good" and r.reps == 3
    assert 0.02 < r.enter < r.release < 0.10
    assert abs(r.enter - (0.02 + 0.35 * 0.08)) < 0.005


def test_each_users_distances_give_their_own_thresholds():
    small = analyze_pinch(pinch_series(contact=0.012, opened=0.07), OPEN_MIN_INDEX)
    large = analyze_pinch(pinch_series(contact=0.03, opened=0.16), OPEN_MIN_INDEX)
    assert small.ok and large.ok and small.enter < large.enter and large.enter > 1.5 * small.enter


def test_a_small_but_real_opening_gives_tight_thresholds_flagged_as_small():
    """Three real runs of 2026-11-04: this user releases to only ~0.027 (contact ~0.013). That is their real
    gap, so thresholds follow it, flagged: the engine's release limit (0.045 + 0.015) is far above it."""
    r = analyze_pinch(pinch_series(contact=0.014, opened=0.03), 0.0, 0.012)
    assert r.ok and r.small_open and 0.014 < r.enter < r.release < 0.03
    assert r.release - r.enter >= 0.008


def test_a_gap_at_the_noise_level_is_refused():
    r = analyze_pinch(pinch_series(contact=0.015, opened=0.021), 0.0, 0.004)
    assert not r.ok and r.reason
    assert not analyze_pinch(pinch_series(contact=0.015, opened=0.03), OPEN_MIN_INDEX, 0.012).ok   # explicit gate still works


def test_too_few_pinches_or_poor_separation_are_not_saved():
    assert not analyze_pinch(pinch_series(reps=2), OPEN_MIN_INDEX).ok
    poor = analyze_pinch(pinch_series(contact=0.05, opened=0.07), OPEN_MIN_INDEX)
    assert poor.quality == "poor" and not poor.ok
    still = analyze_pinch([(i / 30, 0.1) for i in range(100)], OPEN_MIN_INDEX)
    assert not still.ok and "움직임" in still.reason


def test_flicker_shorter_than_a_real_contact_is_not_counted():
    assert analyze_pinch(pinch_series(reps=3, hold=0.06), OPEN_MIN_INDEX).reps == 0


# ---------------------------------------------------------------------- side fingers: selectivity
def side_frames(target, confuse_share=0.0, confuser="pinky", n=90):
    """Thumb on the target for most frames; for `confuse_share` of them closer to the confuser."""
    frames, rng = [], random.Random(2)
    for i in range(n):
        t = i / 30
        near = target if rng.random() >= confuse_share else confuser
        tip_target = TIP_X[target]
        thumb_x = TIP_X[near] + 0.004
        d = {f: abs(thumb_x - x) for f, x in TIP_X.items()}
        d[target] = abs(thumb_x - tip_target)
        frames.append((t, d))
    return frames


def test_selectivity_is_high_when_the_intended_tip_is_the_nearest():
    r = analyze_selectivity(side_frames("ring"), "ring")
    assert r["selectivity"] > 0.95 and r["quality"] == "good" and r["margin"] > 0.02


def test_selectivity_names_the_finger_it_is_confused_with():
    r = analyze_selectivity(side_frames("ring", confuse_share=0.5, confuser="pinky", n=300), "ring")
    assert r["quality"] in ("poor", "fair") and r["confuser"] == "pinky" and r["confuser_share"] > 0.2


# ---------------------------------------------------------------------- whole session
def run_session(contact=0.02, opened=0.10, wobble=0.0006, skip=(), confuse=None):
    s = CalibrationSession()
    rng = random.Random(1)
    t = 0.0
    while not s.done and t < 300:
        step = s.step
        if step.finger is None or step.finger in skip:
            lm = hand(None, wobble=wobble, rng=rng)
        else:
            phase = (t - (s._step_t0 or t) - 2.5) % 3.0
            touching = 1.5 <= phase < 2.7
            if step.finger == "index":
                lm = hand("index", thumb_dx=contact if touching else opened, wobble=wobble, rng=rng)
            else:
                lm = hand(step.finger, thumb_dx=0.004 if touching else 0.09, wobble=wobble, rng=rng)
        s.feed(t, lm)
        t += 1 / 30
    return s


def test_full_session_sets_the_index_thresholds_and_reports_the_side_fingers():
    s = run_session()
    assert s.done
    res = s.finish({})
    assert res.complete and res.fingers["index"].ok
    assert "pinch_threshold" in res.updates and "release_threshold" in res.updates
    for f in ("middle", "ring", "pinky"):
        assert res.fingers[f].selectivity_quality == "good", (f, res.fingers[f].selectivity)
        assert ENTER_KEYS[f] not in res.updates            # side-finger thresholds are NOT derived from distances
    assert "tremor_deadzone" not in res.updates            # reported only
    assert len(res.summary_lines()) >= 5


def test_a_side_click_threshold_is_only_raised_when_the_contact_is_out_of_reach():
    # the user's real contact is 0.06 away from the tip, the ring threshold is 0.044: the click could not land
    s = CalibrationSession()
    rng = random.Random(3)
    t = 0.0
    while not s.done and t < 300:
        step = s.step
        if step.finger is None:
            lm = hand(None)
        else:
            phase = (t - (s._step_t0 or t) - 2.5) % 3.0
            touching = 1.5 <= phase < 2.7
            if step.finger == "index":
                lm = hand("index", thumb_dx=0.02 if touching else 0.10)
            else:
                lm = hand(step.finger, thumb_dx=0.02 if touching else 0.09)
        s.feed(t, lm)
        t += 1 / 30
    res = s.finish({"ring_click_threshold": 0.015})       # a threshold below this user's contact (0.02)
    assert res.updates.get("ring_click_threshold", 0) > 0.02
    assert "click_threshold" not in res.updates           # 0.052 default is above the contact: left alone


def test_session_warns_when_the_users_opening_is_below_the_engines_release_limit():
    res = run_session(contact=0.012, opened=0.032).finish({"release_threshold": 0.045, "scroll_release_margin": 0.015})
    assert res.fingers["index"].ok and res.fingers["index"].small_open
    assert any("pinch가 유지" in w for w in res.warnings)
    assert res.updates["release_threshold"] < 0.045


def test_an_unmeasured_index_leaves_no_thresholds_and_says_why():
    res = run_session(skip=("index",)).finish({})
    assert not res.fingers["index"].ok and "pinch_threshold" not in res.updates


def test_prompts_progress_and_live_coaching():
    s = CalibrationSession()
    assert s.step.key == "rest" and s.progress(0.0)[:2] == (1, len(STEPS))
    assert [st.key for st in STEPS][-2:] == ["fist", "open"]
    # on the index step, staying close (never opening) makes the wizard coach the user
    t = 0.0
    while s.step.key != "pinch_index":
        s.feed(t, hand(None)); t += 1 / 30
    for _ in range(200):
        s.feed(t, hand("index", thumb_dx=0.014 + 0.003 * ((int(t * 10)) % 2))); t += 1 / 30
        if s.step.key != "pinch_index":
            break
    assert s.step.key == "pinch_index" and "크게" in s.coach()


def test_a_step_waits_for_the_hand_and_gives_get_ready_time():
    s = CalibrationSession()
    for i in range(60):                      # 2 s without a hand: the first step has not started
        s.feed(i / 30, None)
    assert s.index == 0 and s.preparing(2.0) == -1.0
    s.feed(2.0, hand(None))                  # hand appears: clock starts, nothing recorded yet
    assert s.preparing(2.0) > 2.0 and not s.rest_points
    for i in range(30):                      # still in the get-ready time
        s.feed(2.0 + i / 30, hand(None))
    assert not s.rest_points
    s.feed(4.6, hand(None))
    assert len(s.rest_points) == 1


def test_apply_updates_keeps_a_backup_and_other_keys(tmp_path):
    cfg = tmp_path / "gestures.json"
    cfg.write_text(json.dumps({"pinch_threshold": 0.025, "smoothing": 0.35}), encoding="utf-8")
    res = run_session().finish({})
    backup = apply_updates(cfg, res.updates, report_dict(res))
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["smoothing"] == 0.35 and data["pinch_threshold"] == res.updates["pinch_threshold"]
    assert backup is not None and json.loads(backup.read_text(encoding="utf-8"))["pinch_threshold"] == 0.025
    assert json.loads((tmp_path / "calibration.json").read_text(encoding="utf-8"))["fingers"]["index"]["ok"]
