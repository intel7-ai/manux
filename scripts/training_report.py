"""
학습 센터 리포트 (콘솔): 동작별 녹화량·학습 정도·인식률과 다음에 무엇을 녹화할지.
Run from hand-mouse/:  uv run --locked --extra qt --extra train python scripts/training_report.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(ROOT))

from src.training_center import dataset_summary, gesture_rows, model_report  # noqa: E402


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    summary = dataset_summary()
    report = model_report()
    print(f"\n녹화 파일 {len(summary['files'])}개")
    for f in summary["files"]:
        if "error" in f:
            print(f"  - {f['file']}: 읽지 못함 ({f['error']})")
        else:
            print(f"  - {f['file']} · {f.get('mode')} · {f.get('frames')}프레임 · {f.get('camera')}")
    print("모델:", f"{report.get('trained')} 학습" if report else "아직 없음")
    print()
    for r in gesture_rows(summary, report):
        mark = {"ok": "✓", "retrain": "↻", "need": "!"}[r["level"]]
        print(f" {mark} {r['name']}")
        print(f"     데이터: {r['data_text']}   {r['fit_text']}   {r['score_text']}")
        print(f"     → {r['advice']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
