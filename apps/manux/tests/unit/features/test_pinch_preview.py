from manux.features.pinch.preview import PinchPreview, pinch_guides
from manux.features.pinch.engine import PinchFrame
from manux.features.pinch.settings import DEFAULTS
from pinch_hands import obs, middle_pinch, open_hand


def test_guides_preserve_original_nearest_pinch_and_action_colors():
    near = PinchFrame(distances={"index": .035, "middle": .5})
    assert pinch_guides(near, DEFAULTS, set()) == ((8, "near"),)
    assert pinch_guides(PinchFrame(action="left_click"), DEFAULTS, {"middle"}) == ((12, "action"),)
    assert pinch_guides(PinchFrame(active_gesture="fist"), DEFAULTS, {"middle"}) == ()


def test_preview_keeps_anchor_and_clears_on_missing_hand_without_intents():
    preview = PinchPreview()
    for n in range(8):
        guides, anchor, moving = preview.step(obs(n/30, middle_pinch()), DEFAULTS)
    assert anchor is not None and any(tip == 12 for tip, state in guides)
    assert not hasattr(preview, "mapper")
    assert preview.step(None, DEFAULTS) == ((), None, False)
    guides, anchor, moving = preview.step(obs(1., open_hand()), DEFAULTS)
    assert anchor is not None
    assert not preview.core.pinched_fingers
