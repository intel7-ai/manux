"""`python -m manux`: the desktop app (see manux/desktop.py). `python -m manux.app` stays the headless runner."""
from manux.desktop import main

raise SystemExit(main())
