from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
a = Analysis(
    [str(ROOT / "packaging" / "worker_entry.py")],
    pathex=[str(ROOT / "src"), str(ROOT / "tools")],
    hiddenimports=["app_worker", "train_tcn", "promote_tcn", "tcn_data", "feature_cache", "hub"]
                  + collect_submodules("manux", filter=lambda name: not any(
                      name.startswith("manux." + layer) for layer in ("ui", "capture", "tracking", "platform", "output.windows"))),
    excludes=["pytest", "PySide6", "cv2", "mediapipe", "tkinter", "velopack"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MANUXWorker", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="MANUXWorker")
