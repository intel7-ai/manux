"""거리별 핀치 인식 보고서. 안내 녹화(엄지+검지 등)를 자리(거리)별로 나눠 회차마다 엔진·TCN이 읽었는지 센다.

프로젝트 폴더에서:
  uv run --locked --extra qt --extra train python scripts/pinch_by_distance.py            # thumb_index, 가장 최근 녹화
  uv run --locked --extra qt --extra train python scripts/pinch_by_distance.py thumb_middle
  uv run --locked --extra qt --extra train python scripts/pinch_by_distance.py thumb_index recordings/<파일>.jsonl.gz
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.gesture_catalog import BY_KEY  # noqa: E402
from src.pinch_report import analyze, format_report, newest_recording_with_engine_reading, read_recording  # noqa: E402


def main(argv):
    key = argv[1] if len(argv) > 1 else "thumb_index"
    if key not in BY_KEY:
        print("알 수 없는 동작:", key)
        return 1
    path = Path(argv[2]) if len(argv) > 2 else newest_recording_with_engine_reading(ROOT / "recordings", key)
    if path is None:
        print("진짜 엔진 판정(engine_action)이 기록된 녹화가 없어요. 2026-11-09 저녁 이후 앱으로 안내 녹화를 해 주세요.")
        return 1
    meta, frames = read_recording(path)
    print("녹화:", path.name)
    rows = analyze(meta, frames, key)
    if not rows:
        print("이 녹화에는 그 동작의 'do' 구간이 없어요.")
        return 1
    print(format_report(rows, key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
