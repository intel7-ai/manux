import time

from src.guided_recording import CONDITIONS, TASKS, GuidedSession
from src.hand_tracker import HandDetection
from src.gesture_engine import Point3D
from src.recorder import SessionRecorder, read_session
from src.qt_runtime import UiState
from tests.test_qt_runtime import runtime, steps

FAST = {"rec_reps": 2, "rec_prep": 0.1, "rec_do": 0.2, "rec_rest": 0.2, "rec_free_seconds": 0.3}


def test_guided_session_labels_each_step_and_waits_for_start():
    g = GuidedSession(FAST)
    assert len(g.sets) == len(TASKS) * len(CONDITIONS)
    assert g.label(0.0)["target"] == "idle" and g.view(0.0)["waiting"]
    g.advance(1.0)
    assert g.label(1.05)["target"] == "transition"
    lab = g.label(1.2)
    assert lab["target"] == "thumb_ring" and lab["rep"] == 1 and lab["condition"] == CONDITIONS[0]
    assert g.label(1.4)["target"] == "none"                       # rest
    assert g.label(1.65)["rep"] == 2
    assert not g.update(1.9) and g.update(2.01)                    # set over after 2 x 0.5 s
    assert not g.running and g.index == 1 and g.view(2.1)["waiting"]


def test_guided_session_free_task_is_labelled_none_and_runs_to_the_end():
    g = GuidedSession(FAST)
    t = 0.0
    while not g.done:
        g.advance(t)
        lab = g.label(t + 0.05)
        if g.current[1].free:
            assert lab["target"] == "none" and lab["phase"] == "free"
        t += 1.2
        g.update(t)
    assert g.view(t)["prompt"].startswith("녹화 끝")


def test_recorder_writes_meta_frames_events_and_counts(tmp_path):
    rec = SessionRecorder(tmp_path, "guided", {"camera": {"note": "phone"}}, now=10.0)
    hand = HandDetection(landmarks=[Point3D(0.5, 0.5, 0.0)] * 21, handedness="Left", score=0.9,
                         world=[(0.01, 0.02, 0.0)] * 21)
    rec.frame(10.1, hand, pose=[[0.4, 0.3, 0.9]] * 6, label={"target": "thumb_ring"}, rule={"action": "none"})
    rec.frame(10.2, None, label={"target": "none"})
    rec.event(10.25, "mark")
    path = rec.close()
    meta, frames, events = read_session(path)
    assert meta["schema"].startswith("hand-mouse-recording") and meta["camera"]["note"] == "phone" and "pc" in meta
    assert len(frames) == 2 and len(frames[0]["lm"]) == 21 and len(frames[0]["world"]) == 21 and len(frames[0]["pose"]) == 6
    assert frames[0]["label"]["target"] == "thumb_ring" and "lm" not in frames[1]
    assert events[0]["kind"] == "mark" and rec.counts == {"thumb_ring": 1, "no_hand": 1}


def test_runtime_guided_recording_keeps_the_mouse_still_and_saves_labelled_frames(tmp_path):
    rt = runtime(tmp_path)
    rt.rec_dir = tmp_path / "recordings"
    rt.engine.config.update(FAST)
    ui = dict(stage="main", control_hand="Right", click_style="off", use_index_pointer=False)
    rt.ui = UiState(**ui)
    rt.post("key", ord(" "))                                   # control on
    steps(rt, 3)
    rt.ui = UiState(**ui, rec_mode="guided")
    steps(rt, 2)
    assert rt._snap.rec["waiting"]
    pos = rt.mouse.last_screen_pos
    rt.ui = UiState(**ui, rec_mode="guided", rec_advance=1)    # start the first set
    t_end = time.perf_counter() + 2.0
    while rt._guided is not None and rt._guided.index == 0 and time.perf_counter() < t_end:
        steps(rt, 1, move=0.004)                               # the hand moves, the cursor must not
    assert rt.mouse.last_screen_pos == pos
    rt.post("key", ord("f"))                                   # a "that was wrong" mark
    steps(rt, 1)
    rt.ui = UiState(**ui, rec_mode="off")
    steps(rt, 1)
    files = list((tmp_path / "recordings").glob("*-guided.jsonl.gz"))
    assert len(files) == 1
    meta, frames, events = read_session(files[0])
    targets = {f["label"]["target"] for f in frames}
    assert {"idle", "transition", "thumb_ring", "none"} <= targets
    assert all("lm" in f for f in frames) and meta["control_hand"] == "Right"
    assert any(e["kind"] == "mark" for e in events) and any(e["kind"] == "set_done" for e in events)
