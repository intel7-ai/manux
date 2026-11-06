import numpy as np

from src.tcn_evaluation import replay_interactions, summarise_interactions
from src.tcn_features import FEATURES


def session():
    frames, features = [], np.zeros((40, FEATURES), np.float32)
    features[:, -1] = 1
    features[:, 126:130] = [.9, .8, 1., 1.]
    for i in range(40):
        phase = "do" if 10 <= i < 20 else "rest" if 20 <= i < 26 else "free"
        frames.append({"t": i / 30, "label": {"phase": phase, "target": "thumb_middle" if phase == "do" else "none",
                      "task": "thumb_middle" if phase in ("do", "rest") else "none", "rep": 1, "set": 0, "requested_hold": .35},
                       "rule": {"action": "none", "gesture": "NONE"}})
    features[10:20, 127] = .15
    return {"frames": frames, "features": features, "x": np.zeros((40, 15, FEATURES), np.float32),
            "classes": ["none", "thumb_middle"], "cond": np.zeros(40), "meta": {}}


def test_interaction_evaluation_counts_recovered_click_latency_and_short_rep():
    s = session()
    prob = np.tile([1., 0.], (40, 1))
    prob[10:20] = [0., 1.]
    r = replay_interactions(s, [0], prob)["thumb_middle"]
    assert r["reps"] == r["added_reps"] == r["tcn_success"] == r["quick_success"] == 1
    assert r["rules_success"] == r["lost_reps"] == r["tcn_false"] == 0
    assert r["latencies_ms"] == [33]
    result = summarise_interactions([{"thumb_middle": r}], s["classes"])["thumb_middle"]
    assert not result["evidence_sufficient"] and not result["non_regression"]
    assert "검증 녹화 부족" in result["text"]


def test_failed_model_cannot_drop_rule_click_and_late_rest_prediction_is_not_success():
    s = session()
    prob = np.tile([1., 0.], (40, 1))
    s["frames"][12]["rule"]["action"] = "left_click"
    r = replay_interactions(s, [0], prob)["thumb_middle"]
    assert r["rules_success"] == r["tcn_success"] == 1 and r["lost_reps"] == 0
    s["frames"][12]["rule"]["action"] = "none"
    prob[30:] = [0., 1.]
    s["features"][30:, 127] = .15
    r = replay_interactions(s, [0], prob)["thumb_middle"]
    assert r["tcn_success"] == 0 and r["tcn_false"] == 1


def test_a_good_long_hold_score_cannot_certify_quick_clicks_or_alias_drags():
    good = {"reps": 90, "rules_success": 70, "tcn_success": 90, "negative_seconds": 600,
            "quick_reps": 0, "rules_false": 2, "tcn_false": 0, "lost_reps": 0}
    summary = summarise_interactions([{"thumb_middle": good}], ["none", "thumb_middle", "thumb_middle_drag"])
    assert not summary["thumb_middle"]["evidence_sufficient"]
    assert summary["thumb_middle_drag"]["reps"] == 0


def test_unfinished_repetition_and_unmarked_long_hold_are_not_quick_click_evidence():
    s = session()
    for f in s["frames"]:
        f["label"].pop("requested_hold", None)
    prob = np.tile([0., 1.], (40, 1))
    assert replay_interactions(s, [0], prob)["thumb_middle"]["quick_reps"] == 0
    for f in s["frames"]:
        if f["label"]["phase"] == "rest":
            f["label"]["phase"] = "paused"
    assert replay_interactions(s, [0], prob)["thumb_middle"]["reps"] == 0


def test_collapsed_candidate_is_rejected_and_progress_does_not_claim_application():
    from src.tcn_validation import publication_issues
    from src.training_center import TrainingJob
    from src.training_progress import progress_details
    bad = {"classes": ["none", "thumb_middle"], "train_accuracy": {"thumb_middle": 0},
           "per_gesture": {"thumb_middle": {"rate": 0, "total": 90}}}
    issues = publication_issues(bad)
    assert len(issues) == 2
    job = TrainingJob()
    job.state = {"running": True, "started": 0, "epochs": 1, "folds": 0, "fold": 1}
    job._apply({"event": "done", "published": False, "publication_issues": issues, "model": "candidate.onnx"})
    view = progress_details(job.snapshot())
    assert view["percent"] == 100 and "기존 모델" in view["etaHint"]
    assert "기존 모델" in view["stage"]
    good = {**bad, "train_accuracy": {"thumb_middle": .9}, "per_gesture": {"thumb_middle": {"rate": .85, "total": 90}}}
    assert publication_issues(good) == []
