"""features/pose: evidence only (PoseFeature) and a drop-only intent filter (PoseGuard), fake predictor."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perception"))
import pose_hands as H   # noqa: E402

from manux.core.contracts import (  # noqa: E402
    Feature, FrameContext, FrameInfo, HandObservation, Intent, IntentFilter, OutputTarget, Stage,
)
from manux.features.pose import GUARD_ID, ID, SETTINGS, PoseEvidence, PoseFeature, PoseGuard  # noqa: E402
from manux.ml import ModelError, validate_card  # noqa: E402

CLASSES = ["none", "thumb_ring", "thumb_index", "thumb_middle"]
GESTURES = {"thumb_ring": "pinch.thumb_ring", "thumb_index": "pinch.scroll", "thumb_middle": "pinch.thumb_middle"}
RING, SCROLL, MIDDLE = "pinch.thumb_ring", "pinch.scroll", "pinch.thumb_middle"
FPS = 30.0


def make_card(**changes):
    data = {"classes": CLASSES, "gestures": GESTURES, "role": "pose_evidence", "window": 1, "features": 33,
            "feature_version": "pose-angles-v1", "threshold": 0.9, "frames_in_a_row": 3, "trained": "x", "data": [],
            "held_out_results": []}
    data.update(changes)
    return validate_card(data, "pose-angles-v1", 33)


def probs(**by_class):
    """Probability vector: the named classes get the given mass, `none` takes the rest."""
    p = np.zeros(len(CLASSES))
    for name, value in by_class.items():
        p[CLASSES.index(name)] = value
    p[0] += 1.0 - p.sum()
    return p


class FakeModel:
    """Returns `self.out` (None = raise) and remembers what it was asked."""

    def __init__(self):
        self.classes = tuple(CLASSES)
        self.out = probs(none=1.0)
        self.seen = []

    def __call__(self, window):
        self.seen.append(np.asarray(window))
        if self.out is None:
            raise RuntimeError("inference failed")
        return self.out


def hand_at(t, world=True):
    frame = FrameInfo(seq=int(round(t * FPS)), t=t, width=1280, height=720, mirrored=True)
    w = tuple(tuple(p) for p in H.make_hand())
    return HandObservation(frame, [(0.5, 0.5, 0.0)] * 21, "Right", 0.9, world=w if world else None)


def ctx_at(t):
    return FrameContext(FrameInfo(seq=int(round(t * FPS)), t=t, width=1280, height=720, mirrored=True),
                        Stage.MAIN, OutputTarget.WINDOWS, "pinch")


def tap(g=RING, source="pinch", t=0.0):
    return Intent(t=t, source=source, gesture=g, phase="tap")


def span(phase, g=SCROLL, source="pinch", t=0.0):
    return Intent(t=t, source=source, gesture=g, phase=phase, point=(0.5, 0.5))


class Rig:
    """One feature + guard stepped the way the engine does (feature step, then observe + filter)."""

    def __init__(self, card=None, threshold=0.9, **settings):
        self.model = FakeModel()
        self.card = card or make_card()
        self.feature = PoseFeature(self.model, self.card, threshold)
        self.guard = PoseGuard(self.feature, settings)
        self.t = 0.0

    def frame(self, out=..., intents=(), hand=True, step_feature=True, world=True):
        self.t += 1.0 / FPS
        if out is not ...:
            self.model.out = out
        h = hand_at(self.t, world) if hand else None
        c = ctx_at(self.t)
        if step_feature:
            assert list(self.feature.step(h, c)) == []
        self.guard.observe(h, c)
        got = list(self.guard.filter([self._stamp(i) for i in intents], c))
        return got, self.guard.drain_events()

    def _stamp(self, intent):
        return Intent(t=self.t, source=intent.source, gesture=intent.gesture, phase=intent.phase, point=intent.point)

    def build_evidence(self, out, n):
        """n frames of the same evidence, no intents."""
        for _ in range(n):
            self.frame(out)


AGAINST_RING = probs(none=1.0)                       # "no gesture pose"
SAYS_RING = probs(thumb_ring=0.97, none=0.03)
SAYS_INDEX = probs(thumb_index=0.95, none=0.05)


# ================================================================ PoseFeature

def test_contract_shapes():
    rig = Rig()
    assert isinstance(rig.feature, Feature) and isinstance(rig.guard, IntentFilter)
    assert rig.feature.id == ID == "pose_assist" and rig.guard.id == GUARD_ID == "pose_guard"
    assert rig.feature.gestures == () and "pinch" in rig.feature.modes
    assert not rig.feature.activity().holding


def test_confident_frame_gives_evidence_and_never_an_intent():
    rig = Rig()
    got, _ = rig.frame(SAYS_RING)
    ev = rig.feature.evidence
    assert got == [] and isinstance(ev, PoseEvidence)
    assert ev.top == "thumb_ring" and ev.confidence == pytest.approx(0.97) and ev.version == "pose-angles-v1"
    assert ev.t == pytest.approx(rig.t) and ev.classes == tuple(CLASSES)
    assert ev.prob("thumb_index") == pytest.approx(0.0) and ev.prob("nope") == 0.0


def test_the_model_reads_one_33_value_frame():
    rig = Rig()
    rig.frame(SAYS_RING)
    assert len(rig.model.seen) == 1 and rig.model.seen[0].shape == (1, 33) and rig.model.seen[0].dtype == np.float32
    assert np.isfinite(rig.model.seen[0]).all()


def test_silent_below_the_threshold():
    rig = Rig(threshold=0.9)
    rig.frame(probs(thumb_ring=0.7, none=0.3))
    assert rig.feature.evidence is None
    rig.frame(SAYS_RING)
    assert rig.feature.evidence is not None


@pytest.mark.parametrize("hand,world", [(False, True), (True, False)])
def test_silent_without_a_hand_or_without_world_joints(hand, world):
    rig = Rig()
    rig.frame(SAYS_RING, hand=hand, world=world)
    assert rig.feature.evidence is None and rig.model.seen == []


@pytest.mark.parametrize("bad", [
    None, np.array([0.5, 0.5]), np.array([float("nan"), 0.5, 0.0, 0.5]), np.array([1.2, -0.2, 0.0, 0.0]),
    np.array([0.2, 0.2, 0.2, 0.2]),
])
def test_a_model_error_or_nonsense_means_no_evidence_and_the_frame_goes_on(bad):
    rig = Rig()
    rig.frame(bad)
    assert rig.feature.evidence is None and rig.feature.errors == 1
    rig.frame(SAYS_RING)                                   # recovers on the next frame
    assert rig.feature.evidence is not None and rig.feature.errors == 1


def test_reset_clears_the_evidence():
    rig = Rig()
    rig.frame(SAYS_RING)
    rig.feature.reset("hand_lost")
    assert rig.feature.evidence is None


@pytest.mark.parametrize("changes", [{"window": 15}, {"features": 133}, {"feature_version": "local3d-v1"}])
def test_a_card_that_is_not_a_one_frame_pose_card_is_refused(changes):
    data = dict(changes)
    version = data.get("feature_version", "pose-angles-v1")
    card = validate_card({"classes": CLASSES, "gestures": GESTURES, "window": 1, "features": 33,
                          "feature_version": version, "threshold": 0.9, "frames_in_a_row": 3, "trained": "x",
                          "data": [], "held_out_results": [], **data}, version, data.get("features", 33))
    with pytest.raises(ModelError):
        PoseFeature(FakeModel(), card)


def test_predictor_classes_must_match_the_card():
    model = FakeModel()
    model.classes = ("none", "other")
    with pytest.raises(ModelError):
        PoseFeature(model, make_card())
    with pytest.raises(ValueError):
        PoseFeature(FakeModel(), make_card(), threshold=0.0)


# ================================================================ PoseGuard: settings

def test_settings_declare_everything_off_by_default():
    assert SETTINGS["pose_assist.enabled"] is False and SETTINGS["pose_assist.model"] == ""
    assert SETTINGS["pose_assist.tau_veto"] == 0.9 and SETTINGS["pose_assist.tau_idle"] == 0.9
    assert SETTINGS["pose_assist.k_frames"] == 3
    assert not any("hint" in key for key in SETTINGS)       # v1 has no start hint


@pytest.mark.parametrize("settings", [{"tau_veto": 0.0}, {"tau_idle": 1.5}, {"k_frames": 0}])
def test_bad_tuning_is_refused(settings):
    with pytest.raises(ValueError):
        Rig(**settings)


def test_prefixed_keys_are_accepted():
    rig = Rig(**{"pose_assist.k_frames": 5})
    assert rig.guard.k == 5


# ================================================================ PoseGuard: veto

def test_a_right_click_is_dropped_after_k_frames_of_contradicting_evidence():
    rig = Rig()
    rig.build_evidence(AGAINST_RING, 2)
    got, notes = rig.frame(AGAINST_RING, [tap()])
    assert got == [] and notes == ["pose:veto:pinch.thumb_ring:tap"]
    assert rig.guard.vetoed == 1 and rig.guard.vetoing(RING)
    assert rig.guard.drain_events() == []                              # drained


def test_it_passes_before_k_frames_of_evidence():
    rig = Rig()
    rig.build_evidence(AGAINST_RING, 1)
    got, notes = rig.frame(AGAINST_RING, [tap()])                      # only the 2nd frame of evidence
    assert [i.gesture for i in got] == [RING] and notes == []


def test_it_passes_when_the_model_agrees():
    rig = Rig()
    rig.build_evidence(SAYS_RING, 5)
    got, notes = rig.frame(SAYS_RING, [tap()])
    assert [i.gesture for i in got] == [RING] and notes == []


def test_weaker_than_tau_veto_does_not_veto():
    rig = Rig(tau_veto=0.97)                                           # evidence exists (>= 0.9) but is below 0.97
    rig.build_evidence(probs(thumb_index=0.93, none=0.07), 5)
    assert rig.feature.evidence is not None
    got, _ = rig.frame(probs(thumb_index=0.93, none=0.07), [tap()])
    assert [i.gesture for i in got] == [RING]


def test_a_frame_without_evidence_restarts_the_count():
    rig = Rig()
    rig.build_evidence(AGAINST_RING, 4)
    assert rig.guard.vetoing(RING)
    rig.frame(probs(thumb_ring=0.5, none=0.5))                         # below the threshold: silent
    got, _ = rig.frame(AGAINST_RING, [tap()])                          # one frame of evidence only
    assert [i.gesture for i in got] == [RING]


def test_another_gestures_pose_also_vetoes():
    rig = Rig()
    rig.build_evidence(SAYS_INDEX, 3)
    got, notes = rig.frame(SAYS_INDEX, [tap()])
    assert got == [] and notes == ["pose:veto:pinch.thumb_ring:tap"]


def test_only_gestures_in_the_veto_list_are_checked():
    rig = Rig()
    rig.build_evidence(AGAINST_RING, 5)
    got, notes = rig.frame(AGAINST_RING, [tap(MIDDLE)])
    assert [i.gesture for i in got] == [MIDDLE] and notes == []


def test_a_listed_gesture_the_model_does_not_know_is_ignored():
    rig = Rig()
    assert rig.guard.veto_gestures == (RING,) and rig.guard.unknown_veto == ("pointer.right_click",)
    rig.build_evidence(AGAINST_RING, 5)
    got, _ = rig.frame(AGAINST_RING, [tap("pointer.right_click", source="pointer")])
    assert [i.gesture for i in got] == ["pointer.right_click"]


def test_the_veto_list_is_a_setting():
    rig = Rig(veto_gestures=f"{RING}, {MIDDLE}, bad id")
    assert rig.guard.veto_gestures == (RING, MIDDLE)
    rig.build_evidence(AGAINST_RING, 3)
    got, _ = rig.frame(AGAINST_RING, [tap(MIDDLE)])
    assert got == []


def test_a_vetoed_begin_takes_its_hold_and_end_with_it():
    rig = Rig(veto_gestures=f"{RING},{SCROLL}")
    rig.build_evidence(AGAINST_RING, 3)
    assert rig.frame(AGAINST_RING, [span("begin")])[0] == []
    assert rig.frame(AGAINST_RING, [span("hold")])[0] == []
    assert rig.frame(AGAINST_RING, [span("end")])[0] == []
    rig.build_evidence(SAYS_INDEX, 3)                                   # the pose agrees now
    got, _ = rig.frame(SAYS_INDEX, [span("begin")])
    assert [i.phase for i in got] == ["begin"]


def test_a_passed_begin_keeps_its_hold_and_end_even_if_the_evidence_turns():
    rig = Rig(veto_gestures=f"{RING},{SCROLL}")
    rig.build_evidence(SAYS_INDEX, 3)
    assert [i.phase for i in rig.frame(SAYS_INDEX, [span("begin")])[0]] == ["begin"]
    rig.build_evidence(AGAINST_RING, 5)                                 # now contradicting
    assert [i.phase for i in rig.frame(AGAINST_RING, [span("hold")])[0]] == ["hold"]
    assert [i.phase for i in rig.frame(AGAINST_RING, [span("end")])[0]] == ["end"]
    assert rig.frame(AGAINST_RING, [span("begin")])[0] == []            # a new begin is checked again


def test_mode_intents_always_pass():
    rig = Rig()
    rig.build_evidence(AGAINST_RING, 5)
    enter = Intent(t=rig.t, source="motion", gesture="motion.enter", phase="tap")
    exit_ = Intent(t=rig.t, source="motion", gesture="motion.exit", phase="tap")
    got, notes = rig.frame(AGAINST_RING, [enter, exit_])
    assert [i.gesture for i in got] == ["motion.enter", "motion.exit"] and notes == []
    # even from the source the rest gate holds back (tcn) and as a begin, while the hand rests
    assert rig.guard.resting
    modes = [span("begin", "pinch.enter", source="tcn"), tap("pinch.exit", source="tcn")]
    got, notes = rig.frame(AGAINST_RING, modes)
    assert [i.gesture for i in got] == ["pinch.enter", "pinch.exit"] and notes == []


# ================================================================ PoseGuard: rest evidence

def test_rest_evidence_holds_back_a_tcn_begin_but_not_a_rule_begin():
    rig = Rig()
    rig.build_evidence(AGAINST_RING, 3)
    assert rig.guard.resting
    got, notes = rig.frame(AGAINST_RING, [span("begin", MIDDLE, source="tcn")])
    assert got == [] and notes == ["pose:idle:pinch.thumb_middle:begin"]
    assert rig.guard.idled == 1
    assert [i.gesture for i in rig.frame(AGAINST_RING, [span("begin", MIDDLE, source="pinch")])[0]] == [MIDDLE]


def test_rest_evidence_takes_the_dropped_begins_hold_and_end_too():
    rig = Rig()
    rig.build_evidence(AGAINST_RING, 3)
    assert rig.frame(AGAINST_RING, [span("begin", SCROLL, source="tcn")])[0] == []
    assert rig.frame(AGAINST_RING, [span("hold", SCROLL, source="tcn")])[0] == []
    assert rig.frame(AGAINST_RING, [span("end", SCROLL, source="tcn")])[0] == []


def test_rest_does_not_touch_taps_unless_asked():
    rig = Rig()
    rig.build_evidence(AGAINST_RING, 3)
    assert [i.gesture for i in rig.frame(AGAINST_RING, [tap(MIDDLE, source="tcn")])[0]] == [MIDDLE]
    strict = Rig(idle_taps=True)
    strict.build_evidence(AGAINST_RING, 3)
    assert strict.frame(AGAINST_RING, [tap(MIDDLE, source="tcn")])[0] == []


def test_rest_needs_k_frames_and_tau_idle():
    rig = Rig(tau_idle=0.97)
    rig.build_evidence(probs(none=0.93, thumb_index=0.07), 5)
    assert rig.feature.evidence is not None and not rig.guard.resting
    got, _ = rig.frame(probs(none=0.93, thumb_index=0.07), [span("begin", MIDDLE, source="tcn")])
    assert len(got) == 1
    short = Rig()
    short.build_evidence(AGAINST_RING, 2)
    assert not short.guard.resting


def test_listed_idle_gestures_are_held_back_whatever_their_source():
    rig = Rig(idle_gestures=SCROLL, idle_sources="")
    rig.build_evidence(AGAINST_RING, 3)
    assert rig.frame(AGAINST_RING, [span("begin", SCROLL, source="pinch")])[0] == []
    assert len(rig.frame(AGAINST_RING, [span("begin", MIDDLE, source="pinch")])[0]) == 1


def test_no_idle_class_in_the_card_turns_the_rest_gate_off():
    rig = Rig(idle_class="rest")
    assert rig.guard.idle_class is None
    rig.build_evidence(AGAINST_RING, 5)
    assert not rig.guard.resting
    assert len(rig.frame(AGAINST_RING, [span("begin", MIDDLE, source="tcn")])[0]) == 1


# ================================================================ PoseGuard: freshness and reset

def test_old_evidence_is_not_this_frames_evidence():
    rig = Rig()
    rig.build_evidence(AGAINST_RING, 5)
    got, _ = rig.frame(None, [tap()], step_feature=False)               # the feature was not stepped this frame
    assert [i.gesture for i in got] == [RING] and not rig.guard.vetoing(RING)


def test_hand_loss_restarts_the_count():
    rig = Rig()
    rig.build_evidence(AGAINST_RING, 5)
    rig.frame(AGAINST_RING, hand=False)
    got, _ = rig.frame(AGAINST_RING, [tap()])
    assert [i.gesture for i in got] == [RING]


def test_reset_clears_counts_and_span_state():
    rig = Rig(veto_gestures=f"{RING},{SCROLL}")
    rig.build_evidence(AGAINST_RING, 3)
    rig.frame(AGAINST_RING, [span("begin")])                            # dropped
    rig.guard.reset("hand_lost")
    assert not rig.guard.vetoing(RING) and not rig.guard.resting
    assert [i.gesture for i in rig.frame(AGAINST_RING, [tap()])[0]] == [RING]   # needs k frames again


def test_status_is_plain_data():
    rig = Rig()
    rig.build_evidence(AGAINST_RING, 3)
    rig.frame(AGAINST_RING, [tap()])
    status = rig.guard.status()
    assert status["vetoed"] == 1 and status["resting"] is True and status["vetoing"] == [RING]
    assert status["unknown_veto"] == ["pointer.right_click"] and status["idle_class"] == "none"


def test_the_guard_never_creates_or_changes_an_intent():
    rig = Rig()
    rig.build_evidence(SAYS_RING, 4)
    sent = [tap(), span("begin", MIDDLE, source="tcn"), span("hold", MIDDLE, source="tcn")]
    got, _ = rig.frame(SAYS_RING, sent)
    assert [(i.gesture, i.phase, i.source) for i in got] == [(i.gesture, i.phase, i.source) for i in
                                                               [rig._stamp(s) for s in sent]]
