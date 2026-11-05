# PyInstaller spec for the installed MANUX app (build with packaging/build.ps1).
# One folder (not one file): starts faster, and Velopack ships only the files that changed.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / "models" / "hand_landmarker.task"), "models")]
build_info = ROOT / "build" / "build_info.json"          # written by build.ps1
if build_info.exists():
    datas.append((str(build_info), "."))
datas += collect_data_files("manux")                     # ui/qml/dashboard.qml and its assets
datas += collect_data_files("mediapipe")
binaries = collect_dynamic_libs("mediapipe")

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    # layers import cv2, mediapipe, PySide6 and onnxruntime inside functions; take every manux module
    hiddenimports=collect_submodules("manux") + ["velopack"],
    excludes=["pytest", "torch", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MANUX",
    console=False,          # entry.py sends output to <user data>/logs/app_latest.log
    icon=str(ROOT / "packaging" / "manux.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="MANUX")
