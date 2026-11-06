"""Desktop runtime: the pose assist survives model reloads (docs/pose-assist.md), and off stays unchanged."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here / "ml"))
sys.path.insert(0, str(here))
from test_app_pose import assist  # noqa: E402
from test_model_store import candidate, evidence  # noqa: E402

from manux import desktop  # noqa: E402
from manux.app import schema  # noqa: E402
from manux.core.contracts import RuntimeRequest  # noqa: E402
from manux.features import pose  # noqa: E402
from manux.ml import store  # noqa: E402
from manux.settings.store import Settings  # noqa: E402


def make_runtime(tmp_path, monkeypatch, with_pose=True):
    """A direct-mode runtime on a synthetic TCN store whose active model can be replaced."""
    current = {"model": candidate(tmp_path / "first")}
    folder = tmp_path / "store"
    store.install(folder, current["model"], evidence(current["model"]), None)
    monkeypatch.setattr(desktop, "load_active", lambda *a: current["model"])
    first = assist() if with_pose else None
    monkeypatch.setattr(desktop, "_load_pose_assist", lambda settings, model: first)
    runtime = desktop.build(Settings(schema()), SimpleNamespace(close=lambda: None), windows=False,
                            tcn_store=folder, tcn_mode="direct")

    def new_tcn(data=b"second synthetic model", name="second"):
        current["model"] = candidate(tmp_path / name, data)
        store.install(folder, current["model"], evidence(current["model"]), store.active_token(folder))
    return runtime, first, new_tcn


def test_the_pose_pair_is_built_in_and_survives_a_tcn_reload(tmp_path, monkeypatch):
    runtime, first, new_tcn = make_runtime(tmp_path, monkeypatch)
    assert runtime.pose_assist is first and runtime.engine.filters == [first.guard]
    tcn_before = runtime.engine.features[-2]
    new_tcn()
    assert runtime.reload_model(force=True)
    engine = runtime.engine
    assert engine.filters == [first.guard] and engine.features[-1] is first.feature   # same instances, same place
    assert engine.features[-2] is not tcn_before and engine.features[-2].id == "tcn"   # the TCN really was replaced
    assert [f.id for f in engine.features].count("pose_assist") == 1
    runtime.stop()


def test_a_tcn_reload_starts_the_pose_counts_from_zero(tmp_path, monkeypatch):
    runtime, first, new_tcn = make_runtime(tmp_path, monkeypatch)
    guard = first.guard
    guard._veto_run[next(iter(guard._veto_run))] = 5
    guard._idle_run = 9
    guard._dropped.add("pinch.scroll")
    assert guard.resting
    new_tcn()
    assert runtime.reload_model(force=True)
    assert not guard.resting and not any(guard._veto_run.values()) and not guard._dropped and first.feature.evidence is None
    runtime.stop()


def test_a_failed_tcn_reload_keeps_the_pose_pair_as_it_was(tmp_path, monkeypatch):
    runtime, first, _ = make_runtime(tmp_path, monkeypatch)
    (tmp_path / "store" / "active.json").write_bytes(b"broken")
    monkeypatch.setattr(desktop, "load_active", lambda *a: (_ for _ in ()).throw(desktop.ModelError("invalid")))
    assert not runtime.reload_model(force=True)
    assert runtime.engine.filters == [first.guard] and runtime.engine.features[-1] is first.feature
    assert runtime.pose_assist is first
    runtime.stop()


def test_without_the_assist_a_reload_calls_replace_pipeline_exactly_as_before(tmp_path, monkeypatch):
    runtime, _, new_tcn = make_runtime(tmp_path, monkeypatch, with_pose=False)
    assert runtime.pose_assist is None and runtime.engine.filters == []
    calls = []
    real = runtime.engine.replace_pipeline
    monkeypatch.setattr(runtime.engine, "replace_pipeline", lambda *a: (calls.append(a), real(*a))[1])
    new_tcn()
    assert runtime.reload_model(force=True)
    (features, filters, router, sources), = calls
    assert filters == () and [f.id for f in features] == ["pointer", "pinch", "motion", "tcn"]   # no pose part at all
    assert runtime.engine.filters == [] and "pose_assist" not in [f.id for f in runtime.engine.features]
    runtime.stop()


def test_pose_off_does_not_even_reach_the_pose_loader(tmp_path, monkeypatch):
    monkeypatch.undo()
    called = []
    monkeypatch.setattr(pose, "create_pose_assist", lambda *a, **k: called.append(a))
    runtime = desktop.build(Settings(schema()), SimpleNamespace(close=lambda: None), windows=False)
    assert runtime.pose_assist is None and called == [] and runtime.engine.filters == []
    runtime.stop()


# ---------------------------------------------------------------- swapping the pose model itself

def swap_in(monkeypatch, result):
    monkeypatch.setattr(desktop.pose, "create_pose_assist", lambda *a, **k: result)


def test_a_new_pose_model_replaces_the_pair_and_leaves_the_tcn_alone(tmp_path, monkeypatch):
    runtime, first, _ = make_runtime(tmp_path, monkeypatch)
    new = assist()
    swap_in(monkeypatch, pose.PoseLoad(new))
    tcn, router, sources = runtime.engine.features[-2], runtime.engine.router, runtime.engine.gesture_sources
    assert runtime.request(RuntimeRequest("activate_pose_model", "other.onnx"))
    runtime._drain_runtime_requests()
    engine = runtime.engine
    assert runtime.pose_assist is new and engine.filters == [new.guard] and engine.features[-1] is new.feature
    assert first.feature not in engine.features and first.guard not in engine.filters
    assert engine.features[-2] is tcn and engine.router is router and engine.gesture_sources == sources
    assert "갱신됨" in runtime.pose_status
    runtime.stop()


def test_a_pose_model_that_does_not_load_keeps_the_previous_assist(tmp_path, monkeypatch):
    runtime, first, _ = make_runtime(tmp_path, monkeypatch)
    swap_in(monkeypatch, pose.PoseLoad(None, "invalid"))
    before = list(runtime.engine.features)
    assert not runtime.reload_pose(Path("bad.onnx"))
    assert runtime.pose_assist is first and runtime.engine.filters == [first.guard] and runtime.engine.features == before
    assert "기존 유지" in runtime.pose_status
    runtime.stop()


def test_a_pose_model_can_be_added_when_there_was_none_and_a_bad_one_changes_nothing(tmp_path, monkeypatch):
    runtime, _, _ = make_runtime(tmp_path, monkeypatch, with_pose=False)
    swap_in(monkeypatch, pose.PoseLoad(None, "missing"))
    before = list(runtime.engine.features)
    assert not runtime.reload_pose(Path("nothing.onnx"))
    assert runtime.pose_assist is None and runtime.engine.filters == [] and runtime.engine.features == before
    new = assist()
    swap_in(monkeypatch, pose.PoseLoad(new))
    assert runtime.reload_pose(Path("good.onnx"))
    assert runtime.engine.filters == [new.guard] and runtime.engine.features[-1] is new.feature
    runtime.stop()


def test_a_stopped_runtime_does_not_swap(tmp_path, monkeypatch):
    runtime, first, _ = make_runtime(tmp_path, monkeypatch)
    runtime.stop()
    swap_in(monkeypatch, pose.PoseLoad(assist()))
    assert not runtime.reload_pose(Path("x.onnx")) and runtime.pose_assist is first


def test_the_request_kinds_are_checked():
    assert RuntimeRequest("activate_pose_model", "p.onnx").kind == "activate_pose_model"
    with pytest.raises(ValueError):
        RuntimeRequest("activate_pose_model", "")
    with pytest.raises(ValueError):
        RuntimeRequest("anything_else", "p")  # type: ignore[arg-type]
