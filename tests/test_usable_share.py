"""A recording with too little left to train on is excluded; every other one says how much is usable."""
import gzip
import json

import pytest

import src.data_quality as dq
import src.training_center as tc


def write_frames(path, n):
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "meta"}) + "\n")
        for i in range(n):
            fh.write(json.dumps({"t": i * 0.033}) + "\n")


def fake_report(total, valid, status="warning"):
    return dq.QualityReport(path="x", mode="guided", score=0.7, status=status, summary="품질 주의",
                            metrics={"total_frames": total, "valid_frames": valid})


def test_usable_share_counts_the_cleaned_copy_or_the_frames_with_a_hand(tmp_path):
    cleaned = tmp_path / "c.jsonl.gz"
    write_frames(cleaned, 40)
    assert tc.count_recorded_frames(cleaned) == 40                     # the meta line is not a frame
    got = tc.usable_share(fake_report(100, 90), cleaned)
    assert (got["used"], got["total"], got["after_cleaning"]) == (40, 100, True) and got["ratio"] == pytest.approx(0.4)
    got = tc.usable_share(fake_report(100, 90), None)                  # no cleaned copy: the frames with a hand
    assert (got["used"], got["after_cleaning"]) == (90, False)
    assert tc.usable_share(fake_report(0, 0), None) is None


@pytest.mark.parametrize("kept, excluded", [(40, True), (80, False)])
def test_the_pipeline_excludes_a_recording_below_half_usable_and_says_how_much_is_usable(tmp_path, monkeypatch, kept, excluded):
    rec = tmp_path / "20261109-000000-x-guided.jsonl.gz"
    write_frames(rec, 100)
    cleaned = tmp_path / "cleaned.jsonl.gz"
    write_frames(cleaned, kept)
    monkeypatch.setattr(dq, "check_recording", lambda path, **kw: fake_report(100, 90))
    monkeypatch.setattr(dq, "clean_recording", lambda path, report: cleaned)
    res = tc.run_post_recording_pipeline(rec, {"auto_quality_check": True, "auto_clean": True, "auto_offline_augment": False,
                                               "auto_retrain_after_recording": True})
    assert res["usable_ratio"] == pytest.approx(kept / 100)
    assert f"정제 후 {kept}% 사용" in res["message"]
    assert res["excluded_too_little"] is excluded
    assert (rec.name in tc.load_training_selection(tmp_path)["excluded"]) is excluded
    if excluded:
        assert "50% 미만이라 학습에서 제외" in res["message"] and res["retrain_eligible"] is False
        assert res["augmented_paths"] == []


def test_the_limit_can_be_changed(tmp_path, monkeypatch):
    rec = tmp_path / "20261109-000001-x-guided.jsonl.gz"
    write_frames(rec, 100)
    cleaned = tmp_path / "cleaned2.jsonl.gz"
    write_frames(cleaned, 60)
    monkeypatch.setattr(dq, "check_recording", lambda path, **kw: fake_report(100, 90))
    monkeypatch.setattr(dq, "clean_recording", lambda path, report: cleaned)
    res = tc.run_post_recording_pipeline(rec, {"auto_clean": True, "auto_offline_augment": False,
                                               "auto_exclude_usable_ratio": 0.7})
    assert res["excluded_too_little"] is True and "70% 미만이라 학습에서 제외" in res["message"]
