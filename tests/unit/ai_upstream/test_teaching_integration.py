"""Exercise the original trainer on synthetic independent looks and host-format recordings."""
import gzip
import json

from manux.compat.hand_mouse import custom_postures, posture_teach as pt
from tests.unit.ai_upstream.hands import open_hand
from tests.unit.ai_upstream.test_sequence import v_hand


def test_named_shape_trains_with_existing_recordings_and_keeps_models_separate(tmp_path):
    key = custom_postures.add("합성 테스트 손")
    assert key in custom_postures.load()
    joints = [[p.x,p.y,p.z] for p in v_hand(down=True)]
    for _ in range(2):
        pt.save_example(key, [joints for _ in range(18)], ["v_sign_down"] * 18)
    assert pt.example_counts()[key] == 2
    pt.REC_DIR.mkdir(parents=True)
    for group in range(2):
        lm = [[p.x,p.y,p.z] for p in open_hand(.5 + group*.02, .5)]
        rows = [{"type":"meta", "schema":"hand-mouse-recording/1", "frame_aspect":.5625}]
        rows += [{"type":"frame", "lm":lm, "t":i/30} for i in range(120)]
        rows += [{"type":"end", "frames":120}]
        (pt.REC_DIR / f"synthetic-{group}.jsonl.gz").write_bytes(gzip.compress(
            "\n".join(json.dumps(row) for row in rows).encode()))
    originals = {p: p.read_bytes() for p in pt.REC_DIR.iterdir()}
    report = pt.train()
    assert report["examples"][key] == 2
    assert report["false_rate"] is not None and report["false_frames"][1] > 0
    assert pt.CANDIDATE_PATH.is_file() and not pt.MODEL_PATH.exists()
    assert all(p.read_bytes() == data for p,data in originals.items())
    if report["can_apply"]:
        assert pt.apply_candidate()
        assert pt.active_model() is not None
    else:
        pt.discard_candidate()
        assert not pt.MODEL_PATH.exists()
    custom_postures.delete(key)
    assert key not in custom_postures.load() and key not in pt.example_counts()
