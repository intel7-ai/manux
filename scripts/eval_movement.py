"""CLI Runner for Offline Movement & Return-Stroke Evaluation (`scripts/eval_movement.py`).

Usage:
    .venv\\Scripts\\python.exe scripts/eval_movement.py [--scheme legacy_shape|velocity] [--return-suppression off|clutch|clutch_auto] [--save reports/movement_baseline.json] [--compare reports/movement_baseline.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.movement_eval import evaluate_all, extract_sequences, load_config


def _parse_kv(items: list[str]) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        k = k.strip()
        v = v.strip()
        if v.lower() in ("true", "false"):
            overrides[k] = v.lower() == "true"
        else:
            try:
                if "." in v or "e" in v.lower():
                    overrides[k] = float(v)
                else:
                    overrides[k] = int(v)
            except ValueError:
                overrides[k] = v
    return overrides


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline cursor movement & return-stroke evaluation harness")
    parser.add_argument("--scheme", type=str, default=None, help="Override movement_scheme (legacy_shape | velocity)")
    parser.add_argument(
        "--return-suppression",
        type=str,
        default=None,
        help="Override return_suppression (off | clutch | clutch_auto)",
    )
    parser.add_argument("--set", action="append", default=[], help="Additional config key=value overrides")
    parser.add_argument("--include-raw-windows", action="store_true", help="Include un-deduplicated field-log windows")
    parser.add_argument("--save", type=str, default=None, help="Save JSON report to path")
    parser.add_argument("--compare", type=str, default=None, help="Compare against a saved baseline JSON report")
    args = parser.parse_args()

    overrides = _parse_kv(args.set)
    if args.scheme is not None:
        overrides["movement_scheme"] = args.scheme
    if args.return_suppression is not None:
        overrides["return_suppression"] = args.return_suppression

    cfg = load_config(overrides=overrides)
    seqs = extract_sequences(ROOT)
    report = evaluate_all(cfg=cfg, sequences=seqs, include_raw_windows=args.include_raw_windows)

    if args.save:
        out_path = Path(args.save)
        if not out_path.is_absolute():
            out_path = ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.compare:
        comp_path = Path(args.compare)
        if not comp_path.is_absolute():
            comp_path = ROOT / comp_path
        if comp_path.exists():
            base = json.loads(comp_path.read_text(encoding="utf-8"))
            b_comb = (base.get("groups") or {}).get("combined_unique") or {}
            c_comb = (report.get("groups") or {}).get("combined_unique") or {}
            diff_keys = [
                "still_mult_within_seg_std_mean",
                "still_mult_global_std",
                "still_mult_max_slew_per_s",
                "still_mult_p95_slew_per_s",
                "step_px_mean",
                "step_px_p90",
                "step_px_p99",
                "step_px_max",
                "cursor_max_step_triggers",
                "cursor_max_step_pct",
                "mode_switches_per_min",
                "flickers_per_min",
                "halffist_misfires",
                "forward_strokes_count",
                "forward_cursor_px_mean",
                "return_strokes_count",
                "return_strokes_per_moving_min",
                "return_cursor_path_px_total",
                "return_cursor_path_px_mean",
                "return_cursor_opp_px_mean",
                "return_duration_s_mean",
                "return_cursor_to_hand_ratio_mean",
                "return_event_suppressed_frame_pct",
            ]
            comparison = {}
            for k in diff_keys:
                bv = b_comb.get(k, 0.0)
                cv = c_comb.get(k, 0.0)
                comparison[k] = {
                    "before": bv,
                    "after": cv,
                    "delta": round(cv - bv, 3) if isinstance(bv, (int, float)) and isinstance(cv, (int, float)) else None,
                }
            report["comparison_combined_unique"] = comparison

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
