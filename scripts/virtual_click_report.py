"""How the virtual hand's clicks went in everyday use, from logs/virtual_clicks (src/click_log.py).

    uv run --locked --extra qt --extra train python scripts/virtual_click_report.py [--folder logs/virtual_clicks] [--no-tcn]

Per PC (never mixed): clicks of each kind and how many the person marked with F as "not a click". Then, unless --no-tcn, the TCN
click check (src/click_check.py) replayed over each click's joints: at each threshold, how many real clicks it would keep and how
many marked ones it would have dropped. Marked clicks are the only reliable "wrong" labels: a click that was wrong but not marked
counts as a real one here, so the false share is a lower bound.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.click_check import LOOK_BACK  # noqa: E402
from src.click_log import KINDS, read_clicks  # noqa: E402

NAMES = {"left": "좌클릭", "double": "더블클릭", "drag": "끌기", "right": "우클릭"}
THRESHOLDS = (.2, .3, .4, .5, .6, .7)


def tcn_peak(gate, index: int, click) -> float | None:
    """Replay the click's joints through a fresh gate: the thumb_index peak from LOOK_BACK before the press until the click."""
    gate.reset()
    pressed = click.get("pressed") or click["t"]
    peak = None
    for f in click.get("frames") or []:
        hand = None
        if f.get("lm") and f.get("world"):
            hand = SimpleNamespace(landmarks=[SimpleNamespace(x=p[0], y=p[1], z=p[2]) for p in f["lm"]], world=f["world"], score=1.0)
        gate.step(f["t"], hand, 720 / 1280)
        if pressed - LOOK_BACK <= f["t"] <= click["t"] and gate.last_tcn is not None:
            p = float(gate.last_tcn[index])
            peak = p if peak is None else max(peak, p)
    return peak


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--folder", type=Path, default=ROOT / "logs" / "virtual_clicks")
    ap.add_argument("--no-tcn", action="store_true", help="skip the TCN replay")
    args = ap.parse_args(argv)
    clicks = read_clicks(args.folder)
    if not clicks:
        print(f"아직 기록이 없어요: {args.folder}")
        print("가상 손 모드로 쓰다가 잘못 눌린 클릭이 있으면 바로 F를 누르세요 (5초 안).")
        return 0
    gate = index = None
    if not args.no_tcn:
        try:
            from src.gesture_gate import DEFAULT_MODEL, GestureGate, load_predictor
            predict, info = load_predictor(DEFAULT_MODEL)
            gate = GestureGate(predict, info, {})
            index = gate.classes.index("thumb_index")
        except Exception as exc:
            print(f"(TCN을 불러오지 못해 모델 비교는 건너뜀: {exc})")
    by_pc = defaultdict(list)
    for c in clicks:
        by_pc[c["pc"]].append(c)
    for pc, items in sorted(by_pc.items()):
        files = sorted({c["file"] for c in items})
        print(f"\n== {pc}  (기록 파일 {len(files)}개, 클릭 {len(items)}번)")
        for kind in KINDS:
            ks = [c for c in items if c["kind"] == kind]
            if ks:
                marked = sum(c["marked"] for c in ks)
                print(f"  {NAMES[kind]:5s} {len(ks):5d}번  클릭 아님(F) {marked:4d}  ({marked / len(ks):.0%})")
        if gate is None:
            continue
        judged = [c for c in items if c["kind"] in ("left", "drag")]      # the check only looks at thumb + index
        peaks = [(c, tcn_peak(gate, index, c)) for c in judged]
        real = [p for c, p in peaks if not c["marked"] and p is not None]
        wrong = [p for c, p in peaks if c["marked"] and p is not None]
        if not real and not wrong:
            continue
        print(f"  TCN 확인기 (좌클릭·끌기 {len(real)}번 + 클릭 아님 {len(wrong)}번):")
        for th in THRESHOLDS:
            kept = sum(p >= th for p in real)
            dropped = sum(p < th for p in wrong)
            print(f"    기준 {th:.1f}: 진짜 클릭 {kept}/{len(real)} 유지 ({kept / max(1, len(real)):.0%}),"
                  f"  클릭 아님 {dropped}/{len(wrong)} 막음 ({dropped / max(1, len(wrong)):.0%})")
    print("\n켜려면 config/gestures.json에 \"virtual_click_check\": true, 기준은 \"virtual_click_threshold\".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
