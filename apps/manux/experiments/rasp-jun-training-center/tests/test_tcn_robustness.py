import copy
from types import SimpleNamespace

import numpy as np
import pytest

from src.gesture_gate import GestureGate
from src.tcn_features import FEATURES, frame_features, hand_frame, temporal_inputs
from src.tcn_policy import mode_for, policy_entry
from src import recording_plan as plans
from src.capture_dataset import load_sessions
from src.recorder import SessionRecorder
from tests.test_recording_plan import hand


INFO = {"classes": ["none", "thumb_middle", "thumb_ring"], "window": 15,
        "pipeline_version": "continuous-v2", "threshold": .7, "frames_in_a_row": 3}


def feed(gate, t, *, contact=False, score=.99, action="none", valid=True, ambiguous=False):
    feats = np.zeros(FEATURES, np.float32)
    feats[-1] = valid
    feats[126:130] = [1., .15 if contact else .8, .2 if ambiguous else 1., 1.]
    gate.observe(t, [1 - score, score, 0.], feats)
    return gate.filter(t, SimpleNamespace(action=action, active_gesture="NONE", is_moving=action == "move_cursor")).action


def opened(gate):
    for i in range(4):
        feed(gate, i / 30, score=.01)


def test_new_models_default_to_trial_and_application_is_bound_to_version():
    info = {**INFO, "selected_classes": ["thumb_middle"], "class_model_ids": {"thumb_middle": "v1"}}
    assert mode_for("thumb_middle", info) == "shadow"
    policies = {"thumb_middle": policy_entry("thumb_middle", "apply", info)}
    assert mode_for("thumb_middle", info, policies) == "apply"
    assert mode_for("thumb_middle_drag", info, policies) == "shadow"
    newer = {**info, "class_model_ids": {"thumb_middle": "v2"}}
    assert mode_for("thumb_middle", newer, policies) == "shadow"
    policies["thumb_middle"] = policy_entry("thumb_middle", "rules", info)
    assert mode_for("thumb_middle", newer, policies) == "rules"


def test_trial_mode_does_not_change_rule_clicks_or_emit_recovery():
    gate = GestureGate(None, {**INFO, "selected_classes": ["thumb_middle"]})
    opened(gate)
    out = [feed(gate, .2 + i / 30, contact=True) for i in range(10)]
    assert out == ["none"] * 10
    assert feed(gate, .6, contact=True, score=0, action="left_click") == "left_click"


def test_3d_recovery_is_one_shot_and_deduplicates_a_late_rule_click():
    gate = GestureGate(None, INFO)
    opened(gate)
    assert feed(gate, .2, contact=True) == "none"
    assert feed(gate, .24, contact=True) == "left_click"
    assert feed(gate, .27, contact=True, action="left_click") == "none"
    assert [feed(gate, .3 + i / 30, contact=True) for i in range(10)] == ["none"] * 10
    for t in (.7, .74, .8):
        feed(gate, t, score=0)
    assert feed(gate, .84, contact=True) == "none"
    assert feed(gate, .88, contact=True) == "left_click"


@pytest.mark.parametrize("variant", ["closed_at_start", "ambiguous", "low_probability", "missing", "disabled", "remapped", "legacy", "other_action"])
def test_recovery_rejects_unsafe_or_untrained_candidates(variant):
    info, config = dict(INFO), {}
    if variant == "legacy":
        info.pop("pipeline_version")
    if variant in ("disabled", "remapped"):
        config = {"gestures": {"thumb_middle_pinch": {"enabled": variant != "disabled", "action": "none"}}}
    gate = GestureGate(None, info, config)
    if variant != "closed_at_start":
        opened(gate)
    actions = [feed(gate, .2 + i / 30, contact=True, ambiguous=variant == "ambiguous",
                    score=.75 if variant == "low_probability" else .99, valid=variant != "missing",
                    action="scroll_grab" if variant == "other_action" else "none") for i in range(10)]
    assert "left_click" not in actions


def test_rule_click_never_waits_for_model_even_in_apply_mode():
    gate = GestureGate(None, INFO)
    assert feed(gate, 0, score=0, contact=True, action="left_click") == "left_click"
    assert "left_click" not in [feed(gate, .03 + i / 30, contact=True) for i in range(10)]


def test_fast_rule_double_click_after_an_assisted_click_is_not_deduplicated():
    gate = GestureGate(None, INFO)
    opened(gate)
    feed(gate, .2, contact=True)
    assert feed(gate, .24, contact=True) == "left_click"
    feed(gate, .27, contact=False, score=.1)  # one visible release frame
    assert feed(gate, .30, contact=True, action="left_click") == "left_click"


def test_hand_loss_and_camera_gap_discard_stale_confirmations_and_click_arming():
    gate = GestureGate(None, INFO)
    opened(gate)
    for t in (.2, .24, .28):
        feed(gate, t, contact=True)
    assert gate.confirmed("thumb_middle", .28)
    feed(gate, .3, valid=False)
    assert not gate.confirmed("thumb_middle", .3)
    assert all(feed(gate, .34 + i / 30, contact=True) == "none" for i in range(5))
    feed(gate, 2., contact=True)
    assert not gate.confirmed("thumb_middle", 2.)


def test_training_and_live_windows_match_across_labels_missing_hand_and_gaps(tmp_path):
    rec = SessionRecorder(tmp_path, "guided", {"frame_aspect": .75}, now=0)
    for i in range(80):
        phase = "prep" if i % 20 < 6 else "do" if i % 20 < 14 else "rest"
        rec.frame(i / 30 + (1 if i >= 60 else 0), None if i == 30 else hand(),
                  label={"target": {"prep": "transition", "do": "thumb_middle", "rest": "none"}[phase],
                         "phase": phase, "task": "thumb_middle", "set": i // 20,
                         "condition_index": i // 40, "rep": 1})
    session = load_sessions([rec.close()], selected_tasks=["thumb_middle"])[0]
    captured = []
    gate = GestureGate(lambda x: captured.append(x.copy()) or np.array([1., 0., 0.]), INFO)
    for i, frame in enumerate(session["frames"]):
        item = SimpleNamespace(world=frame["world"], landmarks=[SimpleNamespace(x=p[0], y=p[1], z=p[2]) for p in frame["lm"]]) if frame.get("world") else None
        gate.step(frame["t"], item, .75)
        np.testing.assert_allclose(captured[-1][0], session["x"][i], atol=1e-7)
    assert not session["x"][60, :-1].any()


def test_canonical_coordinates_invariant_under_100_random_rotations_translations_and_scales():
    w = np.asarray(hand().world)
    expected = hand_frame(w)
    rng = np.random.default_rng(20)
    for _ in range(100):
        q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
        q[:, 0] *= np.linalg.det(q)
        transformed = w @ q * rng.uniform(.2, 4) + rng.normal(size=3)
        np.testing.assert_allclose(hand_frame(transformed), expected, atol=1e-8)
    mirrored = hand_frame(w * [-1, 1, 1])
    np.testing.assert_allclose(mirrored, expected * [1, 1, -1], atol=1e-8)


def test_augmentation_keeps_padding_geometry_and_swipe_direction_consistent():
    import torch
    from src.tcn_augmentation import augment
    h = hand()
    f = frame_features([{"t": i / 30, "world": h.world, "lm": [[p.x + i * .001, p.y, p.z] for p in h.landmarks]} for i in range(30)], .75)
    _, win, _ = temporal_inputs([{"t": i / 30, "world": h.world, "lm": [[p.x, p.y, p.z] for p in h.landmarks]} for i in range(4)], .75)
    x = torch.from_numpy(np.stack([f[-15:], win[-1]]))
    x[0, :, 130] = .3
    original = x.clone()
    for _ in range(10):
        out = augment(x)
        assert torch.equal(x, original)
        valid = out[:, :, -1].bool()
        assert not out[~valid].any()
        assert (out[0, :, 130] >= 0).all()
        pts = out[:, :, :63].reshape(2, 15, 21, 3)
        torch.testing.assert_close(out[:, :, 126:130], torch.linalg.vector_norm(pts[:, :, [8, 12, 16, 20]] - pts[:, :, 4:5], dim=-1))
        both = valid[:, 1:] & valid[:, :-1]
        torch.testing.assert_close(out[:, 1:, 63:126][both], (pts[:, 1:] - pts[:, :-1]).reshape(2, 14, 63)[both])


def test_quick_click_plan_covers_each_angle_at_three_speeds_without_splitting_fold_identity():
    plan = plans.normalise(plans.preset("quick_click"))
    groups = {}
    for row in plan["conditions"]:
        groups.setdefault(plans.condition_key(row), set()).add(row["hold"])
    assert len(groups) == 9
    assert all(values == {.3, .5, .7} for values in groups.values())


def test_held_out_pose_never_enters_a_training_window():
    from scripts.train_tcn import sample_mask
    s = {"y": np.ones(60), "cond": np.array([0] * 20 + [1] * 20 + [0] * 20), "segments": np.zeros(60)}
    mask = sample_mask(s, [0])
    assert not mask[20:55].any()
    assert mask[55:].all()


def test_sampling_keeps_neutral_prior_and_balances_different_recording_lengths():
    from scripts.train_tcn import sample_weights
    sessions = [{"y": np.array([0] * n + [1] * 20), "cond": np.zeros(n + 20), "segments": np.zeros(n + 20)} for n in (100, 1000)]
    weights = sample_weights(sessions, [0])
    labels = np.concatenate([s["y"] for s in sessions])
    assert weights[labels == 0].sum() / weights.sum() == pytest.approx(.5)
    assert weights[:100].sum() == pytest.approx(weights[120:1120].sum())


def test_short_capture_is_counted_and_prep_does_not_instruct_a_pinch(tmp_path):
    from src.guided_recording import GuidedSession, tasks_for
    from src.training_center import dataset_summary
    plan = {"schema": plans.SCHEMA, "conditions": [plans.condition(hold=.2, prep=1, rest=1, reps=1)]}
    guided = GuidedSession(tasks=tasks_for("thumb_middle"), plan=plan)
    guided.advance(0)
    assert "붙여 유지" not in guided.view(.1)["prompt"]
    assert "후 붙이세요" in guided.view(.1)["prompt"]
    rec = SessionRecorder(tmp_path, "guided", {}, now=0)
    for i in range(70):
        t = i / 30
        guided.update(t)
        rec.frame(t, hand(), label=guided.label(t))
    rec.close()
    data = dataset_summary(tmp_path)
    assert data["per_gesture"]["thumb_middle"]["reps"] == 1
    assert data["files"][0]["quick_reps"]["thumb_middle"] == 1
