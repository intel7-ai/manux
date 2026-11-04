# PyInstaller spec for the installed MANUX app (build with packaging/build.ps1).
# One folder (not one file): starts faster, and Velopack ships only the files that changed.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "config" / "gestures.json"), "config"),
    (str(ROOT / "models" / "hand_landmarker.task"), "models"),
    (str(ROOT / "src" / "dashboard.qml"), "src"),
    (str(ROOT / "src" / "assets"), "src/assets"),
]
datas += collect_data_files("mediapipe")
binaries = collect_dynamic_libs("mediapipe")
# uiautomation (scroll target finder, aim assist) loads its DLL from <package>/bin by path
binaries += collect_dynamic_libs("uiautomation", destdir="uiautomation/bin")

a = Analysis(
    [str(ROOT / "manux_app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    # src/ modules are partly imported inside functions (by UI mode); take them all
    hiddenimports=collect_submodules("src") + ["main", "velopack"],
    excludes=["pytest", "tkinter.test"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MANUX",
    console=False,          # main.py writes to logs/app_latest.log when there is no console
    icon=str(ROOT / "packaging" / "manux.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="MANUX")
