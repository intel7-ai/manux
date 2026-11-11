"""
모델 내보내기: exports/gesture-tcn-<시각>/ 과 같은 이름의 .zip
(모델, 정보, 특징 코드, 사용 예시, 설명서, 데이터셋 npz).
Run from hand-mouse/:  uv run --locked --extra qt --extra train python scripts/export_model.py [--no-dataset]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(ROOT))

from src.training_center import export_package  # noqa: E402


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    out = export_package(with_dataset="--no-dataset" not in sys.argv)
    print(f"내보냈어요: {out}  (+ {out.name}.zip)")
    for p in sorted(out.iterdir()):
        print(f"  - {p.name} ({p.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
