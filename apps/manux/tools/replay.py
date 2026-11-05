"""Replay tool: check what a training recording turns into for the v0.4 engine.

    cd apps/manux
    uv run --python 3.12 python tools/replay.py <recording.jsonl.gz> [more files] [--json]

Prints, per file, the counts from `manux.data.recording.summary` (frames, hands that replay as
valid HandObservations, labels, marks, duration, fps, frame size). Engine stages (S2+) will add
`--feature <id>` to run a feature over the replayed hands and print its intents.
Recordings are personal data: read them where they are, do not copy them into the repository.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from manux.data.recording import RecordingError, read, summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--json", action="store_true", help="one JSON object per file")
    args = ap.parse_args(argv)
    status = 0
    for path in args.files:
        try:
            info = summary(read(path))
        except (OSError, RecordingError) as exc:
            print(f"{path.name}: {exc}", file=sys.stderr)
            status = 1
            continue
        if args.json:
            print(json.dumps(info, ensure_ascii=False))
            continue
        print(f"{info['file']}  ({info['schema']}, {info['mode']}, {'complete' if info['complete'] else 'INCOMPLETE'})")
        for key in ("frames", "with_hand", "replayed_hands", "invalid_hands", "labels", "marks",
                    "duration_s", "fps", "frame_size", "frame_size_known", "mirrored", "world_joints", "bad_lines"):
            print(f"  {key:17} {info[key]}")
        if not info["complete"]:
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
