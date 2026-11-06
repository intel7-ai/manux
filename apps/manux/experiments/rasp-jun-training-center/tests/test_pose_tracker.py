from src.elbow_pivot import ElbowPivot
from src.pose_tracker import pick_arm


def _pose(left_wrist, right_wrist, vis=0.9):
    lms = [(0.5, 0.5, 0.1)] * 33
    lms = list(lms)
    lms[11], lms[13], lms[15] = (0.65, 0.3, vis), (0.7, 0.6, vis), left_wrist + (vis,)
    lms[12], lms[14], lms[16] = (0.35, 0.3, vis), (0.3, 0.6, vis), right_wrist + (vis,)
    return lms


def test_pick_arm_takes_the_arm_whose_wrist_is_at_the_hand():
    lms = _pose((0.72, 0.45), (0.28, 0.45))
    assert pick_arm(lms, (0.27, 0.46))["elbow"][:2] == (0.3, 0.6)
    assert pick_arm(lms, (0.71, 0.44))["elbow"][:2] == (0.7, 0.6)
    assert pick_arm(lms[:10], (0.5, 0.5)) is None


def test_pose_elbow_wins_over_the_arcs_while_it_is_seen():
    ep = ElbowPivot()
    ep.set_pose((0.4, 0.9), aspect=0.5625, now=10.0)
    assert ep.source == "pose" and abs(ep.elbow[1] - 0.9 * 0.5625) < 1e-9
    # left/right sweeps during the hold do not replace it
    import math
    for k in range(20):
        a = math.radians(-20 + 2 * k)
        ep.observe(10.0 + k * 0.02, (0.4 + 0.5 * math.sin(a), (0.9 * 0.5625 - 0.5 * math.cos(a)) / 0.5625), 0.5625)
    assert ep.source == "pose"
