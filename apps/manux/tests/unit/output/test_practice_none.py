"""Output sinks `practice` and `none` (docs/contracts.md section 8)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from manux.core.contracts import ActionCommand, OutputResult, OutputSink, OutputTarget
from manux.output.null import NullSink
from manux.output.practice import PracticeSink

OUTPUT = Path(__file__).resolve().parents[3] / "src" / "manux" / "output"


def cmd(action="mouse.left_click", phase="tap", t=1.0, point=None):
    return ActionCommand(t=t, action=action, phase=phase, gesture="pointer.left_click", point=point)


@pytest.mark.parametrize("sink,target", [(PracticeSink(), OutputTarget.PRACTICE), (NullSink(), OutputTarget.NONE)])
def test_sinks_follow_the_protocol(sink, target):
    assert isinstance(sink, OutputSink) and sink.target is target


def test_practice_records_what_it_accepts():
    sink = PracticeSink()
    assert sink.apply(cmd()) == OutputResult(True, "ok")
    move = cmd("mouse.move", "hold", 1.1, (.4, .5))
    assert sink.apply(move).accepted
    assert list(sink.commands) == [cmd(), move] and sink.last == move
    sink.clear()
    assert not sink.commands


def test_practice_keeps_only_the_latest_commands():
    sink = PracticeSink(limit=5)
    for i in range(20):
        sink.apply(cmd("mouse.move", "hold", float(i), (.5, .5)))
    assert [c.t for c in sink.commands] == [15.0, 16.0, 17.0, 18.0, 19.0]


def test_practice_tracks_begun_actions_and_release_all_is_idempotent():
    sink = PracticeSink()
    sink.apply(cmd("mouse.drag", "begin", 1.0, (.5, .5)))
    sink.apply(cmd("mouse.drag", "hold", 1.1, (.6, .5)))
    assert sink.held == {"mouse.drag"}
    sink.release_all("hand_lost")
    sink.release_all("hand_lost")
    sink.release_all("shutdown")
    assert sink.held == frozenset() and list(sink.releases) == [("hand_lost", ("mouse.drag",))]
    sink.apply(cmd("mouse.drag", "begin", 2.0, (.5, .5)))
    sink.apply(cmd("mouse.drag", "end", 2.1, (.5, .5)))
    assert sink.held == frozenset()


def test_practice_never_raises_and_blocks_after_close():
    sink = PracticeSink()
    bad = sink.apply("not a command")  # type: ignore[arg-type]
    assert not bad.accepted and bad.reason.startswith("error:")
    sink.apply(cmd("mouse.drag", "begin", 1.0, (.5, .5)))
    sink.close()
    sink.close()
    assert sink.held == frozenset()
    assert sink.apply(cmd()) == OutputResult(False, "blocked")
    sink.release_all("shutdown")


def test_none_accepts_nothing_and_never_raises():
    sink = NullSink()
    for c in (cmd(), cmd("mouse.drag", "begin", 1.0, (.5, .5)), None, "x", object()):
        assert sink.apply(c) == OutputResult(False, "blocked")  # type: ignore[arg-type]
    assert sink.blocked == 5
    for _ in range(3):
        sink.release_all("pause")
        sink.close()


@pytest.mark.parametrize("name", ["practice.py", "null.py"])
def test_practice_and_none_cannot_reach_windows(name):
    """Only output/windows may send real input (contracts section 8): these import only core."""
    tree = ast.parse((OUTPUT / name).read_text(encoding="utf-8"))
    modules = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    modules |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert modules <= {"__future__", "collections", "typing", "manux.core.contracts"}, modules
