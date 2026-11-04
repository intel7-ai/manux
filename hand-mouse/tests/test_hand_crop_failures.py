"""Hand-area crop and failure recording (no camera, no MediaPipe model)."""
import json
from types import SimpleNamespace

import pytest

from src.failure_log import FailureRecorder, left_through_edge
from src.hand_crop import HandCrop

W, H = 1280, 720


def hand(cx, cy, size):
    """21 points spread over a size x size (normalized) box around (cx, cy)."""
    pts = []
    for i in range(21):
        fx, fy = (i % 5) / 4 - .5, (i // 5) / 4 - .5
        pts.append(SimpleNamespace(x=cx + fx * size, y=cy + fy * size * W / H, z=0.0))
    return pts


def test_crop_follows_a_small_hand_and_stays_put_for_small_moves():
    crop = HandCrop()
    roi = crop.update(hand(.5, .5, .06), W, H)          # far hand: ~77 px wide
    x0, y0, x1, y1 = roi
    assert x1 - x0 == y1 - y0 >= 192                     # square, at least min_px
    assert x0 <= .5 * W <= x1 and y0 <= .5 * H <= y1
    assert crop.update(hand(.505, .5, .06), W, H) == roi  # small move: same crop (stable for MediaPipe)
    moved = crop.update(hand(.62, .5, .06), W, H)        # hand nears the crop edge: recentre
    assert moved != roi and moved[0] <= .62 * W <= moved[2]
    assert crop.moves == 2


def test_crop_is_not_used_for_a_big_close_hand_and_stays_inside_the_frame():
    crop = HandCrop()
    assert crop.update(hand(.5, .5, .3), W, H) is None   # already big: whole frame is fine
    roi = crop.update(hand(.02, .97, .05), W, H)         # near the bottom-left corner
    x0, y0, x1, y1 = roi
    assert x0 >= 0 and y0 >= 0 and x1 <= W and y1 <= H


def test_crop_landmarks_are_mapped_back_to_the_whole_frame():
    import numpy as np
    from src.hand_tracker import HandTracker

    class FakeLandmarker:
        def __init__(self):
            self.shapes = []

        def detect_for_video(self, image, ts):
            self.shapes.append(image.numpy_view().shape)
            lm = SimpleNamespace(x=.5, y=.25, z=-.1)
            cat = SimpleNamespace(category_name="Left", score=.9)
            return SimpleNamespace(hand_landmarks=[[lm] * 21], handedness=[[cat]])

    tracker = HandTracker.__new__(HandTracker)
    tracker._crop_landmarker = FakeLandmarker()
    tracker._crop_timestamp_ms = 0
    tracker.crop_max_side = 480
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    det = tracker.detect(frame, roi=(400, 200, 600, 400))[0]
    assert det.from_crop and det.handedness == "Left"
    p = det.landmarks[0]
    assert p.x == pytest.approx((400 + .5 * 200) / W)
    assert p.y == pytest.approx((200 + .25 * 200) / H)
    assert p.z == pytest.approx(-.1 * 200 / W)
    assert tracker._crop_landmarker.shapes[0][:2] == (200, 200)   # full resolution, not shrunk
    tracker.detect(frame, roi=(0, 0, 720, 720))
    assert max(tracker._crop_landmarker.shapes[1][:2]) == 480     # big crops are capped


def _run(rec, frames, t0=0.0, dt=1 / 30):
    kinds = []
    for i, (lm, moving) in enumerate(frames):
        k = rec.record(t0 + i * dt, landmarks=lm, score=.9 if lm else 0.0, moving=moving, brightness=40)
        if k:
            kinds.append(k)
    return kinds, t0 + len(frames) * dt


def test_losing_the_hand_while_moving_saves_the_window_around_it(tmp_path):
    rec = FailureRecorder(tmp_path, before=1.0, after=.5)
    frames = [(hand(.5, .5, .1), True)] * 45 + [(None, True)] * 30
    kinds, t = _run(rec, frames)
    assert kinds == ["lost_while_moving"]
    path = rec.poll(t, {"anchor": "index"})
    assert path is not None and path.parent == tmp_path
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["kind"] == "lost_while_moving" and data["meta"]["anchor"] == "index"
    ts = [f["t"] for f in data["frames"]]
    assert min(ts) >= -1.0 - 1e-6 and max(ts) <= .5 + 1e-6 and min(ts) < -.9
    with_hand = [f for f in data["frames"] if f["hand"]]
    assert len(with_hand[0]["lm"]) == 21 and "image" not in with_hand[0]
    assert rec.summary_text().startswith("이동 중 놓침 1건")


def test_leaving_through_the_frame_edge_is_not_a_failure(tmp_path):
    rec = FailureRecorder(tmp_path)
    at_edge = hand(.5, .9, .1)                  # box reaches the bottom edge
    assert left_through_edge(at_edge)
    kinds, _ = _run(rec, [(at_edge, False)] * 45 + [(None, False)] * 10)
    assert kinds == []
    kinds, _ = _run(rec, [(hand(.5, .5, .1), False)] * 45 + [(None, False)] * 10, t0=10.0)
    assert kinds == ["lost_mid_frame"]           # vanished in the middle: recorded


def test_manual_mark_cooldown_and_file_cap(tmp_path):
    rec = FailureRecorder(tmp_path, before=.2, after=.1, cooldown=1.0, max_files=2)
    t = 0.0
    for n in range(4):
        _run(rec, [(hand(.5, .5, .1), False)] * 10, t0=t)
        assert rec.trigger(t + .3, "manual", note=f"#{n}")
        assert not rec.trigger(t + .35, "manual")   # one at a time / cooldown
        assert rec.poll(t + .5, {}) is not None
        t += 2.0
    assert len(list(tmp_path.glob("*.json"))) == 2   # oldest pruned
    assert rec.counts == {"manual": 4}


def test_summary_reports_loss_length_candidates_and_index_row(tmp_path):
    rec = FailureRecorder(tmp_path, before=1.0, after=1.0)
    # a hand that shrinks fast (turning sideways), then vanishes mid-frame for ~0.5 s, then returns
    frames = [(hand(.5, .5, .2 - .004 * i), True) for i in range(30)] + [(None, True)] * 15         + [(hand(.5, .5, .1), False)] * 20
    kinds, t = _run(rec, frames)
    assert kinds == ["lost_while_moving"]
    path = rec.poll(t, {"frame_px": [1280, 720]})
    data = json.loads(path.read_text(encoding="utf-8"))
    s = data["summary"]
    assert s["recovered"] is True and 400 <= s["lost_ms"] <= 600 and s["lost_frames"] == 15
    assert s["edge_gap"] > .2 and any("줄어듦" in h for h in s["hints"])
    rows = (tmp_path / "index.csv").read_text(encoding="utf-8-sig").splitlines()
    assert rows[0].startswith("file,kind") and "lost_while_moving" in rows[1]


def test_summary_flags_hand_seen_but_filtered_out(tmp_path):
    rec = FailureRecorder(tmp_path, before=1.0, after=.5)
    _run(rec, [(hand(.5, .5, .1), True)] * 30)
    t0 = 1.0
    for i in range(10):   # MediaPipe still returns a hand ("raw") but the control-hand filter drops it
        rec.record(t0 + i / 30, landmarks=None, moving=True, raw=[["Right", .9]], path="full")
    path = rec.poll(t0 + 2, {"frame_px": [1280, 720]})
    s = json.loads(path.read_text(encoding="utf-8"))["summary"]
    assert s["raw_hands_during_loss"] == 10 and any("handedness" in h for h in s["hints"])
