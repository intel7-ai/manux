"""Download the MediaPipe Face Landmarker model and verify its SHA-256.

Run from hand-mouse/:  uv run --locked python scripts/fetch_face_model.py

The model file is NOT committed (see .gitignore). The size and hash below were computed from a
real download on 2026-11-04. If Google republishes the file under the same URL the hash check
fails on purpose: review the change, then update EXPECTED_SHA256 in a reviewed commit.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/"
       "float16/1/face_landmarker.task")
EXPECTED_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
EXPECTED_SIZE = 3758596
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "models" / "face_landmarker.task"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(path: Path) -> bool:
    return path.is_file() and path.stat().st_size == EXPECTED_SIZE and sha256_of(path) == EXPECTED_SHA256


def fetch(path: Path = DEFAULT_PATH) -> int:
    if verify(path):
        print(f"OK: {path} already matches the pinned SHA-256.")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".task.part")
    print(f"Downloading {URL}")
    try:
        urllib.request.urlretrieve(URL, str(tmp))
    except Exception as exc:  # noqa: BLE001 - report any network failure plainly
        tmp.unlink(missing_ok=True)
        print(f"FAILED: download error: {exc}", file=sys.stderr)
        return 2
    if not verify(tmp):
        got = sha256_of(tmp)
        tmp.unlink(missing_ok=True)
        print(f"FAILED: SHA-256 mismatch (got {got}, expected {EXPECTED_SHA256}). File removed.", file=sys.stderr)
        return 3
    tmp.replace(path)
    print(f"OK: saved {path} ({EXPECTED_SIZE} bytes, SHA-256 verified).")
    return 0


if __name__ == "__main__":
    sys.exit(fetch(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH))
