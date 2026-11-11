"""A video tracker keeps the crop on the hand while the detector has lost it (no camera, a fake tracker)."""
from types import SimpleNamespace

import numpy as np
import pytest

from src.hand_crop import HandCrop
from src.roi_tracker import RoiTracker, hand_box, make_cv_tracker

W, H = 1280, 720


class FakeTracker:
    def __init__(self, path):
        self.path = path            # the boxes it will report, one per update
        self.inited = None
        self.updates = 0

    def init(self, frame, box):
        self.inited = box

    def update(self, frame):
        self.updates += 1
        if not self.path:
            return False, (0, 0, 0, 0)
        return True, self.path.pop(0)


def tracker_with(path, seconds=3.0):
    made = []

    def factory(kind):
        made.append(FakeTracker(list(path)))
        return made[-1]

    return RoiTracker("mil", seconds, factory=factory), made


def test_the_tracker_starts_on_the_last_good_frame_and_follows_the_hand_while_it_is_lost():
    rt, made = tracker_with([(110, 100, 40, 40), (120, 100, 40, 40)])
    good_frame, now_frame = object(), object()
    rt.good(good_frame, (100, 100, 40, 40))
    assert rt.lost(now_frame, 1.0) == (110, 100, 40, 40)
    assert made[0].inited == (100, 100, 40, 40)                      # started where the hand was last seen
    assert rt.lost(now_frame, 1.1) == (120, 100, 40, 40)             # and keeps following
    assert len(made) == 1


def test_a_new_detection_resets_it_and_a_lost_track_or_a_long_loss_gives_up():
    rt, made = tracker_with([(10, 10, 30, 30)])
    rt.good(object(), (100, 100, 40, 40))
    assert rt.lost(object(), 1.0) is not None
    assert rt.lost(object(), 1.1) is None                            # the fake has no more boxes: the tracker lost it
    rt.good(object(), (200, 200, 40, 40))                            # the detector found the hand again
    assert rt.lost(object(), 2.0) is not None and len(made) == 2     # a fresh tracker on the new box
    slow, _ = tracker_with([(1, 1, 30, 30)] * 10, seconds=1.0)
    slow.good(object(), (100, 100, 40, 40))
    assert slow.lost(object(), 0.0) is not None
    assert slow.lost(object(), 1.5) is None                          # lost for longer than `seconds`


def test_off_and_missing_pieces_do_nothing_and_never_raise():
    off = RoiTracker("off")
    off.good(object(), (1, 1, 20, 20))
    assert not off.enabled and off.lost(object(), 0.0) is None
    on, _ = tracker_with([])
    assert on.lost(object(), 0.0) is None                            # no good frame yet
    broken = RoiTracker("mil", 3.0, factory=lambda kind: (_ for _ in ()).throw(RuntimeError("no tracker")))
    broken.good(object(), (1, 1, 20, 20))
    assert broken.lost(object(), 0.0) is None


def test_the_crop_follows_the_tracked_box_and_stays_inside_the_frame():
    crop = HandCrop(min_px=64, scale=1.9)
    roi = crop.follow((600, 300, 40, 40), W, H, now=5.0)
    x0, y0, x1, y1 = roi
    assert x1 - x0 == y1 - y0 and x0 <= 620 <= x1 and y0 <= 320 <= y1
    corner = crop.follow((0, 700, 30, 20), W, H, now=5.1)
    assert corner[0] >= 0 and corner[3] <= H
    assert crop.last_seen == 5.1                                     # miss() would not drop it right after


def test_the_hand_box_is_padded_clipped_and_empty_for_nothing():
    pts = [SimpleNamespace(x=0.5 + 0.01 * (i % 5), y=0.5 + 0.01 * (i // 5)) for i in range(21)]
    x, y, w, h = hand_box(pts, W, H)
    assert x < 0.5 * W and w > 0.04 * W and y < 0.5 * H
    edge = hand_box([SimpleNamespace(x=0.0, y=0.0) for _ in range(21)], W, H)
    assert edge is None or edge[0] == 0
    assert hand_box([], W, H) is None


def test_the_opencv_factory_gives_a_working_mil_tracker_and_falls_back_when_weights_are_missing(tmp_path):
    for kind in ("mil", "nano", "vit"):
        tracker = make_cv_tracker(kind, models_dir=tmp_path)         # no weight files in tmp_path: MIL
        assert tracker is not None
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    frame[100:140, 100:140] = 255
    tracker = make_cv_tracker("mil", models_dir=tmp_path)
    tracker.init(frame, (98, 98, 44, 44))
    ok, box = tracker.update(frame)
    assert ok and abs(box[0] - 98) < 10


def test_with_the_downloaded_weights_the_vit_tracker_is_the_neural_one():
    from src.roi_tracker import MODELS_DIR, VIT_FILE
    if not (MODELS_DIR / VIT_FILE).is_file():
        pytest.skip("models/trackers/ has no ViT weights (docs/far-hand-loss.md says how to get them)")
    tracker = make_cv_tracker("vit")
    assert type(tracker).__name__ == "TrackerVit"
