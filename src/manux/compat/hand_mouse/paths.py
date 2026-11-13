"""All original relative paths point at this user's isolated add-on data directory."""
from manux.settings.paths import resolve

DATA_ROOT = resolve().data_dir / "ai-customization"
USER_RECORDINGS = resolve().recordings_dir
