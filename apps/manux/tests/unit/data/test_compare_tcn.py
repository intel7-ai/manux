"""Comparison failures and event metrics; synthetic features, no torch dependency."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import compare_tcn as compare
from tcn_data import Segment


def segment(person="a", labels=("none", "thumb_index", "thumb_ring")):
    frames = [{"t": i / 30, "label": {"target": name}} for i, name in enumerate(labels)]
    features = np.zeros((len(frames), 133), dtype=np.float32)
    features[:, -1] = 1
    return Segment(person, "0", frames, features)


@pytest.mark.parametrize("option,value", [("--epochs", "0"), ("--count", "0"),
                                        ("--threshold", "nan"), ("--threshold", "1.1")])
def test_invalid_parameters_fail_before_reading_recordings(monkeypatch, capsys, option, value):
    def unexpected_load(paths):
        pytest.fail("invalid parameters must not read recordings")
    monkeypatch.setattr(compare, "load", unexpected_load)
    with pytest.raises(SystemExit) as error:
        compare.main(["synthetic.gz", option, value])
    assert error.value.code == 2
    assert "must be" in capsys.readouterr().err


@pytest.mark.parametrize("segments,message", [
    ([], "at least two gesture classes"),
    ([segment(labels=("none", "thumb_index"))], "at least two gesture classes"),
    ([segment("")], "every segment needs person metadata"),
    ([segment()], "at least two person groups"),
    ([segment(), segment("b", ("none", "thumb_index"))], "no valid training frames for thumb_ring"),
])
def test_unusable_folds_cannot_report_a_successful_comparison(monkeypatch, capsys, segments, message):
    monkeypatch.setattr(compare, "load", lambda paths: segments)
    with pytest.raises(SystemExit) as error:
        compare.main(["synthetic.gz", "--axis", "person"])
    assert error.value.code == 2
    assert message in capsys.readouterr().err


def test_no_neutral_exposure_is_unmeasured_not_zero():
    result = {"per_gesture": {}, "neutral_seconds": 0,
              "tcn_misfires_per_minute": None, "rule_misfires_per_minute": None}
    row = compare.summary(result, ["none"])
    assert row["misfires_per_min"] is None
    assert row["rule_misfires_per_min"] is None
    assert row["neutral_seconds"] == 0


@pytest.mark.parametrize("a,b,expected", [
    ([.9] * 12, [.1] * 6 + [.9] * 6, 1),  # delayed onset still overlaps a long held run
    ([.9] * 12, [.9] * 12, 1),            # one overlap, not two onset events
    ([.9] * 3 + [.1] * 9, [.1] * 3 + [.9] * 9, 0),  # nearby but never simultaneous
])
def test_double_fires_count_actual_overlap_episodes(a, b, expected):
    row = segment(labels=("none",) * len(a))
    scores = {"a": lambda w: np.array(a), "b": lambda w: np.array(b)}
    assert compare.double_fires([row], scores, .7, 3) == expected
