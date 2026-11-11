"""거리별 평가용 확률 저장. 한 녹화(거리 코스)의 자세마다 그 자세를 빼고 TCN을 학습해, 빠진 자세의 창별 확률을 npz로 저장한다.
저장한 확률로 기준값(임계값) 후보를 몇 초 만에 비교한다(scripts/far_threshold_compare.py). 모델은 저장하지 않는다.

프로젝트 폴더에서:
  python scripts/far_fold_probs.py <녹화 파일 이름> <저장할 npz 경로>   (GPU 환경의 python으로)
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import train_tcn as T  # noqa: E402
from src.training_center import filter_selected_recording_paths  # noqa: E402


def main(argv):
    target, out = argv[1], Path(argv[2])
    paths = [Path(p) for p in filter_selected_recording_paths([str(p) for p in sorted((ROOT / "recordings").glob("*.jsonl.gz"))])]
    args = T.parse_args(["--no-export", "--keep-rest", "--device", "gpu", "--mode", "fast", "--seed", "0"])
    sessions, eval_sessions, opts = T.load_training_sessions(paths, args)
    present = {int(y) for s in sessions for y in s["y"] if y >= 0}
    classes = [name for i, name in enumerate(T.CLASSES) if i in present]
    for s in sessions:
        original = s["y"].copy()
        for c, name in enumerate(classes):
            s["y"][original == T.CLASSES.index(name)] = c
        s["classes"] = classes
    eval_sessions = [{**s, "y": np.where(s["rest_dropped"], -1, s["y"])} for s in eval_sessions]
    conds = sorted({int(c) for s in eval_sessions for c, y in zip(s["cond"], s["y"]) if c >= 0 and y >= 0})
    index = [i for i, p in enumerate(paths) if p.name == target][0]
    session = eval_sessions[index]
    held = sorted({int(c) for c, y in zip(session["cond"], session["y"]) if c >= 0 and y >= 0})
    held = [c for c in held if c in conds]
    print("held-out postures:", held, "classes:", classes, flush=True)
    result = {"classes": np.array(classes), "cond": np.asarray(session["cond"]), "held": np.array(held)}
    for test in held:
        train_conds = [c for c in conds if c != test]
        x, y, w = T.pick_with_weights(sessions, train_conds)
        model = T.train(x, y, opts["epochs"], seed=0, stage=f"far{test}", classes=classes,
                        window_stride=opts["window_stride"], device=opts["effective_device"], sample_weights=w)
        result[f"prob_{test}"] = np.asarray(T.predict(model, session["x"]), dtype=np.float32)
        print(f"posture {test}: trained on {len(y)} windows", flush=True)
    np.savez_compressed(out, **result)
    print("saved", out)


if __name__ == "__main__":
    main(sys.argv)
