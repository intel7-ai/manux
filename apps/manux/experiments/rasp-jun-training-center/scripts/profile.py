"""
Manage calibration profiles (config/profiles/*.json).

  uv run --locked python scripts/profile.py list
  uv run --locked python scripts/profile.py use <name>     # next start of Hand-Mouse uses it
  uv run --locked python scripts/profile.py use none       # no profile (gestures.json only)
  uv run --locked python scripts/profile.py show <name>
  uv run --locked python scripts/profile.py delete <name>
Create one with scripts/calibrate.py --name <name>. Profiles only overlay config/gestures.json.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import profiles  # noqa: E402
from src.gesture_engine import DEFAULT_CONFIG, DEFAULT_CONFIG_PATH  # noqa: E402

CONFIG_DIR = DEFAULT_CONFIG_PATH.parent


def main(argv) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    cmd = argv[0] if argv else "list"
    name = " ".join(argv[1:]).strip()
    try:
        if cmd == "list":
            active = profiles.get_active(CONFIG_DIR)
            names = profiles.list_profiles(CONFIG_DIR)
            if not names:
                print("저장된 프로필이 없습니다. scripts/calibrate.py --name <이름> 으로 만드세요.")
            for n in names:
                print(("* " if n == active else "  ") + n)
            print("(* = 사용 중)" if active else "사용 중인 프로필 없음")
        elif cmd == "use":
            if name.lower() in ("none", "없음"):
                profiles.set_active(CONFIG_DIR, None)
                print("프로필을 쓰지 않습니다. 다음 실행부터 gestures.json 값만 씁니다.")
            elif name in profiles.list_profiles(CONFIG_DIR):
                profiles.set_active(CONFIG_DIR, name)
                print(f"'{name}' 프로필을 선택했습니다. 앱을 다시 시작하면 적용됩니다.")
            else:
                print(f"'{name}' 프로필이 없습니다.", file=sys.stderr)
                return 1
        elif cmd == "show":
            vals = profiles.load_profile(CONFIG_DIR, name, set(DEFAULT_CONFIG))
            if vals is None:
                print(f"'{name}' 프로필이 없거나 읽을 수 없습니다.", file=sys.stderr)
                return 1
            for k, v in vals.items():
                print(f"  {k}: {v}   (기본 {DEFAULT_CONFIG.get(k)})")
        elif cmd == "delete":
            print("지웠습니다." if profiles.delete_profile(CONFIG_DIR, name) else f"'{name}' 프로필이 없습니다.")
        else:
            print(__doc__)
            return 1
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
