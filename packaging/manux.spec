# PyInstaller spec for the installed MANUX app (build with packaging/build.ps1).
# One folder (not one file): starts faster, and Velopack ships only the files that changed.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / "models" / "hand_landmarker.task"), "models")]
datas.append((str(ROOT / "models" / "pose_landmarker_lite.task"), "models"))
# Only the named team models are bundled; user data stays in APPDATA.
for name in ("gesture_tcn.onnx", "gesture_tcn.json"):
    datas.append((str(ROOT / "models" / name), "models"))
for name in ("active_model.json", "gh.json", "spatial_pose_mlp.json"):
    datas.append((str(ROOT / "models" / "spatial_mlp" / name), "models/spatial_mlp"))
cloudflared = os.environ.get("MANUX_CLOUDFLARED_BUNDLE")
if cloudflared:
    helper = Path(cloudflared).resolve(strict=True)
    if not helper.is_file() or helper.name.lower() != "cloudflared.exe":
        raise ValueError("Expected the verified cloudflared.exe helper")
    datas.append((str(helper), "tools"))
build_info = ROOT / "build" / "build_info.json"          # written by build.ps1
if build_info.exists():
    datas.append((str(build_info), "."))
datas += collect_data_files("manux")                     # ui/qml/dashboard.qml and its assets
datas += collect_data_files("mediapipe")
binaries = collect_dynamic_libs("mediapipe")

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src"), str(ROOT / "packaging")],
    binaries=binaries,
    datas=datas,
    # layers import cv2, mediapipe, PySide6 and onnxruntime inside functions; take every manux module
    hiddenimports=collect_submodules("manux") + collect_submodules("mss") + ["velopack"],
    excludes=["pytest", "torch", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Manux",
    console=False,          # entry.py sends output to <user data>/logs/app_latest.log
    icon=str(ROOT / "packaging" / "manux.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="MANUX")
