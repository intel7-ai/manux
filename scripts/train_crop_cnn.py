"""손 조각 CNN 학습·평가. 손 조각이 저장된 안내 녹화로, 자세(거리)를 하나씩 빼고 학습해 빠진 자세에서 평가한다.
같은 평가 정의(연속 k프레임, 정답 구간 밖 0.7초 이상 떨어진 반응을 분당으로)라서 TCN 결과(scripts/far_threshold_compare.py)와 나란히 볼 수 있다.

프로젝트 폴더에서:
  uv run --locked --extra qt --extra train python scripts/train_crop_cnn.py                      # 손 조각이 있는 모든 녹화
  uv run --locked --extra qt --extra train python scripts/train_crop_cnn.py <녹화 파일 이름> ...  # 골라서
옵션: --epochs 12  --thr 0.7  --k 4  --device cpu|cuda  --center 36 (그림 가운데 36x36만 사용, 0이면 전체 48x48)
"""
import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[0] = str(ROOT)   # scripts/profile.py would hide the standard profile module that torch imports

import torch  # noqa: E402

from src.crop_cnn import build_samples, centre_crop, evaluate_condition, predict_cnn, train_cnn  # noqa: E402
from src.crop_recorder import crops_path  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("recordings", nargs="*")
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--thr", type=float, default=0.7)
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--center", type=int, default=0, help="그림 가운데 N x N만 쓴다 (0 = 전체)")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    rec_dir = ROOT / "recordings"
    paths = [rec_dir / n for n in a.recordings] if a.recordings else sorted(rec_dir.glob("*.jsonl.gz"))
    paths = [p for p in paths if crops_path(p).is_file()]
    data = build_samples(paths)
    data["pics"] = centre_crop(data["pics"], a.center)
    classes, y, cond = data["classes"], data["y"], data["cond"]
    print(f"녹화 {len(paths)}개, 표본 {len(y)}개, 동작 {classes}, 자세 {len(set(cond))}개, 장치 {a.device}")
    for ci, name in enumerate(classes):
        print(f"  {name}: {int((y == ci).sum())}개")
    held = [c for c in sorted(set(cond)) if (y[cond == c] != 0).any()]
    if len(held) < 2:
        print("자세가 둘 이상 있어야 하나를 빼고 평가할 수 있어요.")
        return 1
    print(f"\n{'자세':34s} {'동작':12s} {'인식':>7s} {'요청 없는 반응':>16s} {'AUC':>6s}")
    totals = {}
    for c in held:
        test = cond == c
        train = ~test
        model = train_cnn(data["pics"], data["idx"][train], y[train], len(classes), epochs=a.epochs, device=a.device, seed=a.seed)
        rows = np.flatnonzero(test)
        prob = predict_cnn(model, data["pics"], data["idx"], rows, a.device)
        tokens = [data["tokens"][i] for i in rows]
        t = data["t"][rows]
        for ci, name in enumerate(classes):
            if name == "none" or not (y[rows] == ci).any():
                continue
            is_do = y[rows] == ci
            rep_of = [tok[:3] for tok in tokens]
            r = evaluate_condition(prob[:, ci], is_do, rep_of, t, tokens, a.thr, a.k)
            label = data["names"].get(c, str(c))[:34]
            print(f"{label:34s} {name:12s} {r['hit']:>3d}/{r['reps']:<3d} {r['false_fires']:>5d}회 ({r['false_per_min']:5.1f}/분) {r['auc']:6.3f}")
            tot = totals.setdefault(name, [0, 0, 0, 0.0])
            tot[0] += r["hit"]
            tot[1] += r["reps"]
            tot[2] += r["false_fires"]
            tot[3] += r["quiet_minutes"]
    print("\n합계:")
    for name, (hit, reps, far, minutes) in totals.items():
        print(f"  {name}: 인식 {hit}/{reps}, 요청 없는 반응 {far}회 ({far / max(minutes, 1e-9):.1f}/분)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
