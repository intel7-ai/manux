"""Recording reader and replay, on small synthetic recordings (never real ones: D-006)."""
from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

from manux.data.recording import RecordingError, read, replay, summary

APP = Path(__file__).resolve().parents[3]
HAND = [[0.4 + 0.01 * i, 0.5, 0.0] for i in range(21)]
WORLD = [[0.001 * i, 0.0, 0.0] for i in range(21)]


def write(path: Path, lines, gz=True):
    text = "\n".join(json.dumps(x) if not isinstance(x, str) else x for x in lines) + "\n"
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def synthetic(tmp_path: Path, name="s.jsonl.gz", end=True, mode=(1280, 720, 30.0), extra=()):
    meta = {"type": "meta", "schema": "hand-mouse-recording/1", "mode": "guided", "pc": "pc-test",
            "camera": {"source": "0", "mode": list(mode) if mode else None, "mirror": True}, "frame_aspect": 0.5625}
    frames = [
        {"type": "frame", "t": 0.0, "label": {"target": "none"}},
        {"type": "frame", "t": 0.033, "lm": HAND, "world": WORLD, "hand": ["Left", 0.97], "label": {"target": "thumb_ring"}},
        {"type": "frame", "t": 0.066, "lm": HAND[:20], "hand": ["Left", 0.97], "label": {"target": "thumb_ring"}},
        {"type": "event", "t": 0.07, "kind": "mark"},
    ]
    tail = [{"type": "end", "frames": 3, "counts": {}, "marks": 1}] if end else []
    return write(tmp_path / name, [meta, *frames, *extra, *tail])


def test_replay_gives_observations_like_the_tracker(tmp_path):
    rec = read(synthetic(tmp_path))
    out = list(replay(rec))
    assert [r.hand is not None for r in out] == [False, True, False]   # no hand / ok / 20 points
    hand = out[1].hand
    assert hand.handedness == "Right"                                   # mirrored: label flipped
    assert hand.world is not None and hand.frame.width == 1280 and hand.frame.t == 0.033
    assert out[1].record["label"]["target"] == "thumb_ring"


def test_summary_counts(tmp_path):
    info = summary(read(synthetic(tmp_path)))
    assert info["complete"] and info["frames"] == 3 and info["with_hand"] == 2
    assert info["replayed_hands"] == 1 and info["invalid_hands"] == 1
    assert info["marks"] == 1 and info["labels"] == {"thumb_ring": 2}
    assert info["frame_size"] == [1280, 720] and info["frame_size_known"]


def test_incomplete_and_unknown_size(tmp_path):
    rec = read(synthetic(tmp_path, end=False, mode=None, extra=['{"type": "frame", "t": 0.1, "lm"']))
    assert not rec.complete and rec.bad_lines == 1
    assert rec.frame_size() == (1280, 720, False)
    assert not read(synthetic(tmp_path, name="x.jsonl.gz.part")).complete


def test_wrong_files_are_refused(tmp_path):
    with pytest.raises(RecordingError):
        read(write(tmp_path / "a.jsonl", [{"type": "frame"}], gz=False))
    with pytest.raises(RecordingError):
        read(write(tmp_path / "b.jsonl", [{"type": "meta", "schema": "other/9"}], gz=False))
    with pytest.raises(RecordingError):
        read(write(tmp_path / "c.jsonl", [""], gz=False))


def test_replay_tool_prints_a_summary(tmp_path):
    path = synthetic(tmp_path)
    out = subprocess.run([sys.executable, str(APP / "tools" / "replay.py"), str(path), "--json"],
                         capture_output=True, text=True, cwd=APP, env={"PYTHONPATH": str(APP / "src"), "SYSTEMROOT": "C:\\Windows"})
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["replayed_hands"] == 1
