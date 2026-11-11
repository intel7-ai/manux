"""After losing a far hand the crop searches around the last place; collapsed joints do not move the crop (no camera)."""
from types import SimpleNamespace

from src.hand_crop import HandCrop

W, H = 1280, 720


def hand(x=.5, y=.5, size=.04):
    return [SimpleNamespace(x=x - size / 2, y=y - size / 2), SimpleNamespace(x=x + size / 2, y=y + size / 2)]


def side(crop):
    return crop.roi[2] - crop.roi[0]


def centre(crop):
    return (crop.roi[0] + crop.roi[2]) / 2, (crop.roi[1] + crop.roi[3]) / 2


def seen(**options):
    crop = HandCrop(scale=2.0, min_px=64, **options)
    crop.update(hand(), W, H, now=0.0)
    return crop


def test_without_a_search_the_crop_is_dropped_after_the_hold_as_before():
    crop = seen()
    crop.miss(.5)
    assert crop.roi is not None and not crop.searching
    crop.miss(1.0)
    assert crop.roi is None and crop.last_seen is None


def test_the_search_starts_after_the_hold_and_tries_bigger_crops_around_the_last_place():
    crop = seen(search_seconds=10.0)
    base = side(crop)
    crop.miss(.5)
    assert side(crop) == base and not crop.searching
    sides = []
    for t in (.9, 1.3, 1.7, 2.1):
        crop.miss(t)
        assert crop.searching
        sides.append(side(crop))
        assert abs(centre(crop)[0] - W / 2) <= 1 and abs(centre(crop)[1] - H / 2) <= 1
    assert sides == [base * 2, base * 3, min(base * 5, 480), base * 2]


def test_the_search_stays_inside_the_frame_and_under_the_limit():
    crop = HandCrop(scale=2.0, min_px=64, search_seconds=10.0)
    crop.update(hand(x=.02, y=.02, size=.1), W, H, now=0.0)
    for t in (.9, 1.3, 1.7):
        crop.miss(t)
        x0, y0, x1, y1 = crop.roi
        assert x0 >= 0 and y0 >= 0 and x1 <= W and y1 <= H and x1 - x0 <= 480 and x1 - x0 == y1 - y0


def test_the_search_gives_up_and_a_found_hand_ends_it():
    crop = seen(search_seconds=10.0)
    crop.miss(5.0)
    assert crop.searching
    crop.miss(10.9)
    assert crop.roi is None and not crop.searching
    crop = seen(search_seconds=10.0)
    crop.miss(5.0)
    crop.update(hand(x=.55), W, H, now=5.1)
    assert not crop.searching and side(crop) < 200


def test_a_tracked_hand_moves_the_search():
    crop = seen(search_seconds=10.0)
    crop.follow((100, 100, 40, 40), W, H, 1.0)
    assert not crop.searching
    crop.miss(2.0)
    assert crop.searching and centre(crop) == (120, 120)


def test_collapsed_joints_do_not_change_the_crop_or_the_search_place():
    crop = HandCrop(scale=2.0, min_px=64, search_seconds=10.0, collapse_ratio=.6)
    for i in range(6):
        crop.update(hand(), W, H, now=i * .03)
    before = crop.roi
    assert crop.update(hand(x=.8, size=.01), W, H, now=.2) == before
    assert crop.collapsed and crop.roi == before
    crop.miss(1.2)
    assert crop.searching and abs(centre(crop)[0] - W / 2) <= 1


def test_a_small_hand_that_stays_small_is_believed():
    crop = HandCrop(scale=2.0, min_px=64, collapse_ratio=.6)
    for i in range(6):
        crop.update(hand(), W, H, now=i * .03)
    before = crop.roi
    for i in range(HandCrop.COLLAPSE_MAX_FRAMES):
        crop.update(hand(x=.8, size=.01), W, H, now=.2 + i * .03)
        assert crop.collapsed
    crop.update(hand(x=.8, size=.01), W, H, now=1.0)
    assert not crop.collapsed and crop.roi != before


def test_the_collapse_rule_is_off_by_default_and_needs_a_history():
    crop = HandCrop(scale=2.0, min_px=64)
    for i in range(6):
        crop.update(hand(), W, H, now=i * .03)
    crop.update(hand(x=.8, size=.01), W, H, now=.2)
    assert not crop.collapsed
    crop = HandCrop(scale=2.0, min_px=64, collapse_ratio=.6)
    crop.update(hand(), W, H, now=0.0)
    crop.update(hand(x=.8, size=.01), W, H, now=.03)
    assert not crop.collapsed
