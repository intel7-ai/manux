"""tools/pose_compare.py on synthetic recordings and scripted fake models.

This checks that the tool runs and counts what it says it counts. It measures nothing about real people,
a real TCN or a real pose model (those need real eval recordings; docs/pose-assist.md, "측정 절차")."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here.parents[2] / "tools"))
sys.path.insert(0, str(here.parent / "features"))
sys.path.insert(0, str(here))
import pose_compare as pc  # noqa: E402
from pose_recordings import Run, write_recording  # noqa: E402
from test_pose import FakeModel, make_card, probs  # noqa: E402
from test_tcn import Predictor as TcnPredictor, card as tcn_card  # noqa: E402

from manux.features.pose import PoseAssist, PoseFeature, PoseGuard  # noqa: E402
from manux.ml.predictor import LoadedModel  # noqa: E402

TCN_SAYS = {"none": "none", "thumb_ring": "ring", "idle": "none", "thumb_index": "none"}


class ScriptedTcn(TcnPredictor):
    def __init__(self, card, script):
        super().__init__(card)
        self.script = script

    def __call__(self, window):
        self.name = self.script[self.calls]
        return super().__call__(window)


class ScriptedPose(FakeModel):
    def __init__(self, script):
        super().__init__()
        self.script = script

    def __call__(self, window):
        self.out = probs(**{self.script[len(self.seen)]: 1.0})
        return super().__call__(window)


def recording(tmp_path, name="eval.jsonl.gz", split="eval", complete=True):
    """idle 10 frames; a right-click attempt (12); a no-gesture stretch (12) where the TCN wrongly sees a ring."""
    runs = [Run("idle", 10, gesture=None), Run("thumb_ring", 12, gap=1.0), Run("none", 12, gap=1.0)]
    path = write_recording(tmp_path / name, runs, split=split, complete=complete)
    # per frame: what the TCN says (none, none, ring...) and what the pose model says
    tcn = ["none"] * 10 + ["none", "none"] + ["ring"] * 10 + ["none", "none"] + ["ring"] * 10
    pose_ = ["none"] * 10 + ["thumb_ring"] * 12 + ["none"] * 12
    return path, tcn, pose_


def make(tmp_path, **kw):
    path, tcn_script, pose_script = recording(tmp_path, **kw)
    card = tcn_card()
    tcn = lambda: LoadedModel(Path("synthetic.onnx"), card, ScriptedTcn(card, tcn_script))  # noqa: E731

    def pose_factory():
        feature = PoseFeature(ScriptedPose(pose_script), make_card())
        return PoseAssist(feature, PoseGuard(feature), feature.card)
    return path, tcn, pose_factory


def run_all(tmp_path, **kw):
    path, tcn, factory = make(tmp_path, **kw)
    recs = pc.load_eval([path], [])
    return recs, pc.compare(recs, tcn_model=tcn, pose_factory=factory)


# ---------------------------------------------------------------- the three conditions on one recording

def test_the_assist_cancels_the_false_right_click_and_keeps_the_real_one(tmp_path):
    _, result = run_all(tmp_path)
    a, b, c = result["A"], result["B"], result["C"]
    assert a["false_right_clicks"]["count"] == 0                                   # rules alone: nothing was sent
    assert b["false_right_clicks"]["count"] == 1 and b["false_right_clicks"]["per_minute"] > 0
    assert c["false_right_clicks"]["count"] == 0                                   # the veto stopped it
    for m in (b, c):                                                               # the real attempt still clicks
        assert m["click_success"]["segments"] == 1 and m["click_success"]["hit"] == 1 and m["click_success"]["rate"] == 1.0
        assert m["click_success"]["per_class"]["thumb_ring"]["action"] == "mouse.right_click"
    assert b["click_success"]["per_class"]["thumb_ring"]["latency_frames_median"] == \
        c["click_success"]["per_class"]["thumb_ring"]["latency_frames_median"] == 4
    assert c["vetoes"]["veto"] == {"justified": 1, "unjustified": 0, "unlabeled": 0}
    assert "vetoes" not in a and "vetoes" not in b and "pose_silent" not in b
    assert c["pose_silent"]["share"] == 0.0 and c["idle"]["frames"] == 10


def test_a_pose_model_that_vetoes_the_real_click_is_counted_as_unjustified(tmp_path):
    path, tcn, _ = make(tmp_path)
    wrong = ["none"] * 34                                                           # always says "no gesture"

    def factory():
        feature = PoseFeature(ScriptedPose(wrong), make_card())
        return PoseAssist(feature, PoseGuard(feature), feature.card)
    result = pc.compare(pc.load_eval([path], []), tcn_model=tcn, pose_factory=factory)
    c = result["C"]
    assert c["click_success"]["rate"] == 0.0 and c["vetoes"]["veto"]["unjustified"] == 1   # the real click is lost and shown
    assert result["B"]["click_success"]["rate"] == 1.0


def test_a_silent_pose_model_changes_nothing_and_is_reported_as_silent(tmp_path):
    import numpy as np
    path, tcn, _ = make(tmp_path)

    def factory():
        model = FakeModel()
        model.out = np.full(4, 0.25)                                               # never sure of anything
        feature = PoseFeature(model, make_card())
        return PoseAssist(feature, PoseGuard(feature), feature.card)
    result = pc.compare(pc.load_eval([path], []), tcn_model=tcn, pose_factory=factory)
    b, c = result["B"], result["C"]
    assert c["pose_silent"]["share"] == 1.0 and c["vetoes"]["veto"] == {"justified": 0, "unjustified": 0, "unlabeled": 0}
    for key in ("false_right_clicks", "idle", "click_success"):
        assert c[key] == b[key]


def test_conditions_without_their_model_are_left_out(tmp_path):
    path, tcn, factory = make(tmp_path)
    recs = pc.load_eval([path], [])
    assert list(pc.compare(recs)) == ["A"]
    assert list(pc.compare(recs, tcn_model=tcn)) == ["A", "B"]
    assert list(pc.compare(recs, pose_factory=factory)) == ["A"]                   # C needs the TCN too


def test_idle_label_choice_is_a_parameter(tmp_path):
    path, tcn, factory = make(tmp_path)
    recs = pc.load_eval([path], [])
    assert pc.compare(recs, idle_labels=("idle",))["A"]["idle"]["frames"] == 10
    assert pc.compare(recs, idle_labels=("idle", "none"))["A"]["idle"]["frames"] == 22
    assert pc.compare(recs, idle_labels=("nothing",))["A"]["idle"]["frames"] == 0


# ---------------------------------------------------------------- inputs are checked

def test_only_complete_eval_recordings_are_used(tmp_path):
    with pytest.raises(ValueError, match="split=eval"):
        pc.load_eval([recording(tmp_path, split="train")[0]], [])
    with pytest.raises(ValueError, match="incomplete"):
        pc.load_eval([recording(tmp_path, "x.jsonl.gz", complete=False)[0]], [])


def test_a_recording_a_model_was_trained_on_is_refused(tmp_path):
    path = recording(tmp_path)[0]
    digest = pc.pose_data.file_sha256(path)
    model = tmp_path / "m.onnx"
    model.with_suffix(".json").write_text(json.dumps({"data": [{"sha256": digest}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="also training"):
        pc.load_eval([path], [model])
    other = tmp_path / "o.onnx"
    other.with_suffix(".json").write_text(json.dumps({"data": [{"sha256": "a" * 64}]}), encoding="utf-8")
    assert len(pc.load_eval([path], [other])) == 1
    for bad in ({"data": []}, {"data": [{"file": "x"}]}):                            # unknown training data cannot be checked
        unknown = tmp_path / "u.onnx"
        unknown.with_suffix(".json").write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ValueError):
            pc.load_eval([path], [unknown])


def test_the_same_recording_twice_is_one(tmp_path):
    path = recording(tmp_path)[0]
    copy = tmp_path / "copy.jsonl.gz"
    copy.write_bytes(path.read_bytes())
    assert len(pc.load_eval([path, copy], [])) == 1


# ---------------------------------------------------------------- output

def test_the_report_has_a_table_and_no_names_or_paths(tmp_path):
    recs, result = run_all(tmp_path)
    report = pc.build_report(recs, result, {"tcn": "abc", "pose": "def"})
    text = json.dumps(report, ensure_ascii=False) + pc.markdown(report)
    assert "tester01" not in text and str(tmp_path) not in text and ".jsonl" not in text and "eval" not in report["recordings"]
    assert report["recordings"] == [recs[0].sha[:12]] and report["level"] == "frame"
    table = pc.markdown(report).splitlines()
    assert table[0].startswith("| 지표 | A 규칙 | B 규칙+TCN | C TCN+자세 보조 |") and len(table) == 12
    assert all(line.count("|") == table[0].count("|") for line in table)


def test_the_markdown_of_a_partial_report_has_only_its_columns(tmp_path):
    path, tcn, _ = make(tmp_path)
    recs = pc.load_eval([path], [])
    report = pc.build_report(recs, pc.compare(recs, tcn_model=tcn), {})
    header = pc.markdown(report).splitlines()[0]
    assert "A 규칙" in header and "B 규칙+TCN" in header and "C " not in header


def test_the_command_refuses_a_training_recording_with_exit_code_1(tmp_path):
    with pytest.raises(SystemExit) as stop:
        pc.main([str(recording(tmp_path, split="train")[0])])
    assert stop.value.code == 1


def test_the_command_runs_with_rules_only_and_writes_json_and_markdown(tmp_path, capsys):
    path = recording(tmp_path)[0]
    pc.main([str(path), "--json", str(tmp_path / "r.json"), "--markdown", str(tmp_path / "r.md")])
    data = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert list(data["conditions"]) == ["A"] and (tmp_path / "r.md").read_text(encoding="utf-8").startswith("| 지표")
    assert "A 규칙" in capsys.readouterr().out


# ---------------------------------------------------------------- the scoring on hand-made logs

def log(t, target, results=(), events=(), silent=None):
    return pc.FrameLog(t, target, True, tuple(results), tuple(events), silent)


def test_scoring_known_numbers():
    ring, move = ("mouse.right_click", "tap"), ("mouse.move", "hold")
    logs = ([log(0.0, "idle", [move]), log(0.5, "idle", [move, ("mouse.left_click", "tap")]), log(1.0, "idle")]
            + [log(10.0 + i * 0.1, "thumb_ring", [ring] if i == 2 else [], silent=False) for i in range(5)]
            + [log(20.0, "none", [ring], ["pose:veto:pinch.thumb_ring:tap"], silent=True),
               log(21.0, "none", [], ["pose:veto:pinch.thumb_ring:tap", "pose:idle:pinch.scroll:begin"], silent=False),
               log(22.0, None, [], ["pose:veto:pinch.thumb_ring:tap"], silent=False),
               log(23.0, "thumb_ring", [], ["pose:veto:pinch.thumb_ring:tap"], silent=False)])
    out = pc.score([(logs, {"thumb_ring": "pinch.thumb_ring"})], {"pinch.thumb_ring": "mouse.right_click"})
    assert out["idle"] == {"frames": 3, "cursor_move_frames": 2, "click_events": 1}
    assert out["false_right_clicks"]["count"] == 1                                    # only the one on the "none" frame
    row = out["click_success"]["per_class"]["thumb_ring"]
    assert row["segments"] == 2 and row["hit"] == 1 and row["success_rate"] == 0.5 and row["latency_frames_median"] == 2
    assert out["vetoes"]["veto"] == {"justified": 2, "unjustified": 1, "unlabeled": 1}
    assert out["vetoes"]["rest"] == {"justified": 1, "unjustified": 0, "unlabeled": 0}
    assert out["pose_silent"] == {"hand_frames": 9, "silent_frames": 1, "share": 0.1111}


def test_a_frame_with_no_hand_is_not_counted_as_pose_silence():
    frames = [pc.FrameLog(0.0, "none", False, (), (), None), log(0.1, "none", silent=True)]
    out = pc.score([(frames, {})], {})
    assert out["pose_silent"] == {"hand_frames": 1, "silent_frames": 1, "share": 1.0}
    assert out["false_right_clicks"] == {"count": 0, "labelled_other_seconds": 0.1, "per_minute": 0.0}
