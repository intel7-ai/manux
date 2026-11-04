"""
Entry point of the installed MANUX app (PyInstaller builds MANUX.exe from this file).

Same app as `python main.py`, with two differences: Velopack's install/update hooks run first,
and with no `--ui` given it opens the Qt dashboard (the product screen) instead of the camera
window. Any other argument is passed through to main.py unchanged.
"""
from __future__ import annotations

import sys

from src import updater


def run() -> int:
    updater.run_install_hooks()      # must be first: the installer starts the app with hook arguments
    if not any(a == "--ui" or a.startswith("--ui=") for a in sys.argv[1:]):
        sys.argv[1:1] = ["--ui", "qt"]
    updater.start_background_check()
    import main
    return main.main()


if __name__ == "__main__":
    sys.exit(run())
