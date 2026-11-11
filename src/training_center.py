"""
Training center (2026-11-05): what has been recorded, how well the model knows each gesture, what to record next,
training in the background, and exporting the model for other apps. Used by the Qt screen (src/qt_dashboard.py)
and the console tools (scripts/training_report.py, scripts/export_model.py, studio.py). No Qt here.

The loop it supports: record -> train -> look at each gesture's score -> record more of the weak ones -> train again
-> export.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from src.gesture_catalog import BY_KEY, NAMES, NEAR_MISS_TIPS, CLASSES, is_free

ROOT = Path(__file__).resolve().parents[1]


def gpu_training_python() -> Optional[str]:
    """Python of the separate CUDA-torch environment, if it exists (docs/gpu-training.md).

    `uv sync` / `uv run --locked` put the CPU torch back into the project's .venv, so GPU torch lives in its own
    environment next to the project (`../hand-mouse-gpuenv`, or the folder named by HAND_MOUSE_GPU_ENV). Only the
    training subprocess uses it; the app itself keeps running in .venv.
    """
    env_dir = Path(os.environ.get("HAND_MOUSE_GPU_ENV") or ROOT.parent / "hand-mouse-gpuenv")
    exe = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(exe) if exe.is_file() else None


def training_python() -> str:
    return gpu_training_python() or sys.executable
REC_DIR = ROOT / "recordings"
MODEL = ROOT / "models" / "gesture_tcn.onnx"
EXPORT_DIR = ROOT / "exports"
BACKUP_DIR = ROOT / "models" / "backups"
SELECTION_FILE_NAME = "training_selection.json"

GOAL_REPS = 30            # repetitions per gesture worth aiming for (docs: 3 postures x 10-15)
GOAL_POSTURES = 3
GOAL_RATE = 0.8           # recognition in postures the model did not train on
GOAL_FREE_MIN = 9.0       # minutes of "nothing" (half-fist moving)
GOAL_NEAR_MISS_MIN = 3.0  # minutes of one finger coming close without touching (1 minute x 3 postures)
MAX_MISFIRES_PER_MIN = 0.5
STORAGE_LIMIT_MB = 500    # recordings travel with the code in git; past this, suggest a repository of their own


# ---------------------------------------------------------------- recording selection, augmentation settings & deletion
AUGMENT_STRENGTH_PRESETS: Dict[str, Dict[str, Any]] = {
    "light": {
        "key": "light",
        "label": "약하게 (회전 ±20° · 크기 ±8% · 시간 ±8%)",
        "rotation_deg": 20.0,
        "scale_range": 0.08,
        "jitter_std": 0.0015,
        "time_stretch_range": (0.92, 1.08),
        "frame_drop_prob": 0.03,
    },
    "normal": {
        "key": "normal",
        "label": "보통 (회전 ±35° · 크기 ±15% · 시간 ±15%)",
        "rotation_deg": 35.0,
        "scale_range": 0.15,
        "jitter_std": 0.003,
        "time_stretch_range": (0.85, 1.15),
        "frame_drop_prob": 0.05,
    },
    "strong": {
        "key": "strong",
        "label": "강하게 (회전 ±50° · 크기 ±22% · 시간 ±22%)",
        "rotation_deg": 50.0,
        "scale_range": 0.22,
        "jitter_std": 0.005,
        "time_stretch_range": (0.78, 1.22),
        "frame_drop_prob": 0.08,
    },
}


def _selection_path(rec_dir: Path = REC_DIR) -> Path:
    return Path(rec_dir) / SELECTION_FILE_NAME


def _normalize_aug_settings(raw: Any) -> Dict[str, Any]:
    d = raw if isinstance(raw, dict) else {}
    enabled = bool(d.get("enabled", True))
    strength = str(d.get("strength", "normal"))
    if strength not in AUGMENT_STRENGTH_PRESETS:
        strength = "normal"
    try:
        factor = int(d.get("factor", 2))
    except (TypeError, ValueError):
        factor = 2
    factor = max(1, min(8, factor))
    preset = AUGMENT_STRENGTH_PRESETS[strength]
    return {
        "enabled": enabled,
        "strength": strength,
        "strength_label": preset["label"],
        "factor": factor,
        "rotation_deg": preset["rotation_deg"],
        "scale_range": preset["scale_range"],
        "jitter_std": preset["jitter_std"],
        "time_stretch_range": preset["time_stretch_range"],
        "frame_drop_prob": preset["frame_drop_prob"],
    }


_GPU_CAPABILITY_CACHE: Optional[Dict[str, Any]] = None


def _probe_nvidia_hardware() -> Tuple[str, float, int, str]:
    """Return (first_gpu_name, vram_gb, device_count, driver_version) reported by nvidia-smi if present."""
    if not isinstance(getattr(subprocess, "Popen", None), type):
        return "", 0.0, 0, ""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return "", 0.0, 0, ""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            [exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2.5,
            creationflags=flags,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            lines = [ln.strip() for ln in res.stdout.strip().splitlines() if ln.strip()]
            if lines:
                parts = [p.strip() for p in lines[0].split(",")]
                name = parts[0]
                vram_gb = 0.0
                if len(parts) > 1:
                    try:
                        vram_gb = round(float(parts[1]) / 1024.0, 1)
                    except ValueError:
                        vram_gb = 0.0
                driver_ver = parts[2] if len(parts) > 2 else ""
                return name, vram_gb, len(lines), driver_ver
    except Exception:
        pass
    return "", 0.0, 0, ""


def _recommended_pytorch_cuda_index(driver_version: str = "") -> str:
    """Pick the best PyTorch CUDA wheel index URL for the host PC's NVIDIA driver version."""
    try:
        major = int(float(str(driver_version).strip().split(".")[0]))
        if major >= 580:
            return "https://download.pytorch.org/whl/cu132"
        if major >= 560:
            return "https://download.pytorch.org/whl/cu128"
        if major >= 525:
            return "https://download.pytorch.org/whl/cu124"
        if major > 0:
            return "https://download.pytorch.org/whl/cu118"
    except (ValueError, IndexError):
        pass
    return "https://download.pytorch.org/whl/cu132"


def _probe_torch_version_lightweight() -> Tuple[str, str]:
    """Read torch.__version__ and torch.version.cuda from torch/version.py without importing torch (~1 ms)."""
    gpu_py = gpu_training_python()
    if gpu_py:  # the training subprocess runs there, so its torch decides whether the GPU can be used
        try:
            for ver_file in sorted(Path(gpu_py).parents[1].glob("**/site-packages/torch/version.py")):
                ns_gpu: Dict[str, Any] = {}
                exec(ver_file.read_text(encoding="utf-8", errors="replace"), ns_gpu)  # noqa: S102
                return str(ns_gpu.get("__version__") or ""), str(ns_gpu.get("cuda") or "")
        except Exception:
            pass
    torch_mod = sys.modules.get("torch")
    if torch_mod is not None:
        ver = str(getattr(torch_mod, "__version__", "") or "")
        cuda_ver = str(getattr(getattr(torch_mod, "version", None), "cuda", "") or "")
        return ver, cuda_ver
    try:
        import importlib.util

        spec = importlib.util.find_spec("torch")
        if spec and spec.origin:
            ver_file = Path(spec.origin).with_name("version.py")
            if ver_file.is_file():
                ns: Dict[str, Any] = {}
                exec(ver_file.read_text(encoding="utf-8", errors="replace"), ns)  # noqa: S102
                ver = str(ns.get("__version__") or "")
                cuda_ver = str(ns.get("cuda") or "")
                return ver, cuda_ver
    except Exception:
        pass
    return "", ""


def detect_gpu_training_capability(*, force_refresh: bool = False) -> Dict[str, Any]:
    """Detect whether CUDA GPU training is available on this PC without importing heavy torch DLLs in the UI."""
    global _GPU_CAPABILITY_CACHE
    if _GPU_CAPABILITY_CACHE is not None and not force_refresh:
        return dict(_GPU_CAPABILITY_CACHE)

    nvidia_hw_name, hw_vram_gb, hw_count, driver_ver = _probe_nvidia_hardware()
    nvidia_hw_present = bool(nvidia_hw_name)
    torch_version, torch_cuda = _probe_torch_version_lightweight()

    cuda_available = False
    device_count = 0
    device_name = ""
    vram_gb = 0.0

    torch_mod = None if gpu_training_python() else sys.modules.get("torch")
    if torch_mod is not None and hasattr(torch_mod, "cuda"):
        try:
            cuda_available = bool(torch_mod.cuda.is_available())
            if cuda_available:
                device_count = int(torch_mod.cuda.device_count())
                device_name = str(torch_mod.cuda.get_device_name(0) or nvidia_hw_name or "NVIDIA CUDA GPU")
                try:
                    props = torch_mod.cuda.get_device_properties(0)
                    vram_gb = round(float(getattr(props, "total_memory", 0)) / (1024 ** 3), 1)
                except Exception:
                    vram_gb = hw_vram_gb
        except Exception:
            cuda_available = False
    elif nvidia_hw_present and bool(torch_cuda):
        cuda_available = True
        device_count = max(1, hw_count)
        device_name = nvidia_hw_name
        vram_gb = hw_vram_gb

    can_install_cuda = bool(nvidia_hw_present and not cuda_available and bool(torch_version))

    if cuda_available:
        vram_str = f" · {vram_gb:.1f}GB" if vram_gb > 0 else ""
        status_label = f"GPU 가속 가능 ({device_name}{vram_str})"
    elif can_install_cuda:
        status_label = f"NVIDIA GPU 감지됨 ({nvidia_hw_name}) · 현재 CPU 전용 PyTorch 설치됨"
    else:
        status_label = "GPU 미감지 (CPU 학습 전용)"

    info: Dict[str, Any] = {
        "cuda_available": cuda_available,
        "device_count": device_count,
        "device_name": device_name or nvidia_hw_name,
        "vram_gb": vram_gb,
        "driver_version": driver_ver,
        "torch_version": torch_version,
        "nvidia_hardware_present": nvidia_hw_present,
        "nvidia_hardware_name": nvidia_hw_name,
        "can_install_cuda_torch": can_install_cuda,
        "recommended_cuda_index": _recommended_pytorch_cuda_index(driver_ver),
        "recommended_device": "gpu" if cuda_available else "cpu",
        "status_label": status_label,
    }
    _GPU_CAPABILITY_CACHE = dict(info)
    return info


def install_cuda_torch(index_url: Optional[str] = None) -> Dict[str, Any]:
    """Install CUDA-enabled PyTorch into the current Python environment using uv pip (auto-matching driver)."""
    if not index_url:
        _, _, _, drv = _probe_nvidia_hardware()
        index_url = _recommended_pytorch_cuda_index(drv)
    uv_exe = shutil.which("uv")
    if not uv_exe:
        local_uv = Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
        if local_uv.is_file():
            uv_exe = str(local_uv)
    if not uv_exe:
        raise RuntimeError("uv 실행 파일을 찾을 수 없습니다.")

    cmd = [
        uv_exe,
        "pip",
        "install",
        "--python",
        sys.executable,
        "--reinstall-package",
        "torch",
        "torch",
        "--index-url",
        index_url,
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    res = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        check=False,
    )
    if res.returncode != 0:
        err_tail = (res.stderr or res.stdout or "").strip()[-400:]
        raise RuntimeError(f"CUDA PyTorch 설치 실패: {err_tail}")

    # Re-probe in a fresh subprocess because torch is already imported in this process
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, torch; "
            "ok = bool(torch.cuda.is_available()); "
            "name = torch.cuda.get_device_name(0) if ok else ''; "
            "vram = round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 1) if ok else 0.0; "
            "print(json.dumps({'cuda_available': ok, 'device_name': name, 'vram_gb': vram, 'torch_version': torch.__version__}))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        check=False,
    )
    global _GPU_CAPABILITY_CACHE
    _GPU_CAPABILITY_CACHE = None
    cap = detect_gpu_training_capability(force_refresh=True)
    if probe.returncode == 0 and probe.stdout.strip():
        try:
            sub_info = json.loads(probe.stdout.strip().splitlines()[-1])
            if sub_info.get("cuda_available"):
                vram_gb = float(sub_info.get("vram_gb") or 0.0)
                dev_name = str(sub_info.get("device_name") or cap.get("device_name") or "NVIDIA CUDA GPU")
                vram_str = f" · {vram_gb:.1f}GB" if vram_gb > 0 else ""
                cap.update({
                    "cuda_available": True,
                    "device_count": max(1, int(cap.get("device_count") or 1)),
                    "device_name": dev_name,
                    "vram_gb": vram_gb,
                    "torch_version": str(sub_info.get("torch_version") or cap.get("torch_version") or ""),
                    "can_install_cuda_torch": False,
                    "recommended_device": "gpu",
                    "status_label": f"GPU 가속 가능 ({dev_name}{vram_str})",
                })
                _GPU_CAPABILITY_CACHE = dict(cap)
        except Exception:
            pass
    return cap


def load_training_selection(rec_dir: Path = REC_DIR) -> Dict[str, Any]:
    path = _selection_path(rec_dir)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                excluded = [str(x) for x in data.get("excluded", []) if x]
                aug = _normalize_aug_settings(data.get("augmentation"))
                raw_mode = str(data.get("training_mode", "fast"))
                mode = raw_mode if raw_mode in ("fast", "thorough") else "fast"
                raw_dev = str(data.get("training_device", "auto")).strip().lower()
                device = raw_dev if raw_dev in ("auto", "gpu", "cpu") else "auto"
                return {
                    "excluded": sorted(set(excluded)),
                    "augmentation": aug,
                    "training_mode": mode,
                    "training_device": device,
                }
        except Exception:
            pass
    return {
        "excluded": [],
        "augmentation": _normalize_aug_settings({}),
        "training_mode": "fast",
        "training_device": "auto",
    }


def save_training_selection(selection: Dict[str, Any], rec_dir: Path = REC_DIR) -> None:
    folder = Path(rec_dir)
    folder.mkdir(parents=True, exist_ok=True)
    current = load_training_selection(folder)
    excluded = sorted({Path(str(x)).name for x in selection.get("excluded", current.get("excluded", [])) if x})
    aug_raw = selection.get("augmentation", current.get("augmentation", {}))
    aug = _normalize_aug_settings(aug_raw)
    raw_mode = str(selection.get("training_mode", current.get("training_mode", "fast")))
    mode = raw_mode if raw_mode in ("fast", "thorough") else "fast"
    raw_dev = str(selection.get("training_device", current.get("training_device", "auto"))).strip().lower()
    device = raw_dev if raw_dev in ("auto", "gpu", "cpu") else "auto"
    payload = {
        "excluded": excluded,
        "augmentation": {
            "enabled": aug["enabled"],
            "strength": aug["strength"],
            "factor": aug["factor"],
        },
        "training_mode": mode,
        "training_device": device,
    }
    # written whole or not at all: a half-written file read back as "nothing excluded", and the next save kept that
    path = _selection_path(folder)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def get_training_mode(rec_dir: Path = REC_DIR) -> str:
    mode = str(load_training_selection(rec_dir).get("training_mode", "fast"))
    return mode if mode in ("fast", "thorough") else "fast"


def set_training_mode(mode: str, rec_dir: Path = REC_DIR) -> str:
    norm = str(mode) if str(mode) in ("fast", "thorough") else "fast"
    with _SELECTION_LOCK:
        sel = load_training_selection(rec_dir)
        sel["training_mode"] = norm
        save_training_selection(sel, rec_dir)
    return norm


def get_training_device(rec_dir: Path = REC_DIR) -> str:
    dev = str(load_training_selection(rec_dir).get("training_device", "auto")).strip().lower()
    return dev if dev in ("auto", "gpu", "cpu") else "auto"


def set_training_device(device: str, rec_dir: Path = REC_DIR) -> str:
    raw = str(device or "auto").strip().lower()
    if raw == "cuda":
        raw = "gpu"
    norm = raw if raw in ("auto", "gpu", "cpu") else "auto"
    with _SELECTION_LOCK:
        sel = load_training_selection(rec_dir)
        sel["training_device"] = norm
        save_training_selection(sel, rec_dir)
    return norm


def resolve_effective_training_device(
    requested: Optional[str] = None,
    rec_dir: Path = REC_DIR,
    *,
    cuda_available: Optional[bool] = None,
) -> str:
    """Resolve user preference ('auto', 'gpu', 'cpu') against actual GPU capability -> 'gpu' or 'cpu'."""
    choice = str(requested or get_training_device(rec_dir)).strip().lower()
    if choice == "cuda":
        choice = "gpu"
    if choice not in ("auto", "gpu", "cpu"):
        choice = "auto"
    has_gpu = bool(detect_gpu_training_capability()["cuda_available"]) if cuda_available is None else bool(cuda_available)
    if choice in ("auto", "gpu") and has_gpu:
        return "gpu"
    return "cpu"


def get_augmentation_settings(rec_dir: Path = REC_DIR) -> Dict[str, Any]:
    return _normalize_aug_settings(load_training_selection(rec_dir).get("augmentation"))


def set_augmentation_settings(
    *,
    enabled: Optional[bool] = None,
    strength: Optional[str] = None,
    factor: Optional[int] = None,
    rec_dir: Path = REC_DIR,
) -> Dict[str, Any]:
    with _SELECTION_LOCK:
        sel = load_training_selection(rec_dir)
        aug = dict(sel.get("augmentation") or {})
        if enabled is not None:
            aug["enabled"] = bool(enabled)
        if strength is not None and str(strength) in AUGMENT_STRENGTH_PRESETS:
            aug["strength"] = str(strength)
        if factor is not None:
            aug["factor"] = max(1, min(8, int(factor)))
        sel["augmentation"] = aug
        save_training_selection(sel, rec_dir)
    return get_augmentation_settings(rec_dir)


def is_recording_selected(filename: str, rec_dir: Path = REC_DIR) -> bool:
    name = Path(filename).name
    excluded = set(load_training_selection(rec_dir).get("excluded", []))
    return name not in excluded


_SELECTION_LOCK = threading.RLock()   # the file is read, changed and written back: the check thread, the recording thread
                                       # and the screen all do that (two of them at once would lose one change)


def set_recording_selected(filename: str, selected: bool, rec_dir: Path = REC_DIR) -> bool:
    name = Path(filename).name
    with _SELECTION_LOCK:
        sel = load_training_selection(rec_dir)
        excluded = set(sel.get("excluded", []))
        if selected:
            excluded.discard(name)
        else:
            excluded.add(name)
        sel["excluded"] = sorted(excluded)
        save_training_selection(sel, rec_dir)
    return selected


def filter_selected_recording_paths(
    paths: List[Path | str],
    rec_dir: Optional[Path] = None,
    extra_excluded: Optional[List[str]] = None,
) -> List[str]:
    if not paths:
        return []
    folder = Path(rec_dir) if rec_dir is not None else Path(paths[0]).parent
    excluded = set(load_training_selection(folder).get("excluded", []))
    if extra_excluded:
        excluded.update(Path(x).name for x in extra_excluded if x)
    return [str(p) for p in paths if Path(p).name not in excluded]


def delete_recording_file(filename: str, rec_dir: Path = REC_DIR) -> bool:
    folder = Path(rec_dir)
    name = Path(filename).name
    if not name.endswith(".jsonl.gz"):
        return False
    target = folder / name
    removed = False
    for candidate in (
        target,
        folder / f"{name}.summary.json",
        folder / f"{name}.quality.json",
        folder / "cleaned" / name,
    ):
        if candidate.is_file():
            try:
                candidate.unlink()
                if candidate == target:
                    removed = True
            except OSError:
                pass
    stem = name[:-9]
    aug_dir = folder / "augmented"
    if aug_dir.is_dir():
        for aug in aug_dir.glob(f"{stem}_aug*.jsonl.gz"):
            try:
                aug.unlink()
            except OSError:
                pass
    set_recording_selected(name, True, folder)
    return removed


def get_recording_quality(path: Path, *, compute_if_missing: bool = True) -> Dict[str, Any]:
    q_cache = path.with_name(path.name + ".quality.json")
    if q_cache.is_file():
        try:
            data = json.loads(q_cache.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "score" in data and "status" in data:
                return data
        except Exception:
            pass
    if not compute_if_missing:
        return {"status": "unknown", "score": None, "summary": "품질 미검사", "issues": []}
    try:
        from src.data_quality import check_recording
        rep = check_recording(path)
        d = rep.to_dict()
        try:
            q_cache.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        return d
    except Exception as exc:
        return {"status": "fail", "score": 0.0, "summary": f"품질 검사 실패: {exc}", "issues": [str(exc)]}


def evaluate_all_recordings_quality(
    rec_dir: Path = REC_DIR,
    *,
    force: bool = False,
    auto_exclude_failed: bool = False,
    progress_cb=None,
) -> List[Dict[str, Any]]:
    folder = Path(rec_dir)
    files = sorted(folder.glob("*.jsonl.gz"))
    total = len(files)
    results = []
    for idx, p in enumerate(files, 1):
        if callable(progress_cb):
            try:
                progress_cb(round((idx - 0.5) / max(1, total), 3), f"[{idx}/{total}개 파일] '{p.name}' 품질 검사 중…")
            except Exception:
                pass
        q_cache = p.with_name(p.name + ".quality.json")
        if force and q_cache.is_file():
            try:
                q_cache.unlink()
            except OSError:
                pass
        q = get_recording_quality(p, compute_if_missing=True)
        if auto_exclude_failed and q.get("status") == "fail":
            set_recording_selected(p.name, False, folder)
        results.append({"file": p.name, "selected": is_recording_selected(p.name, folder), **q})
    if callable(progress_cb):
        try:
            progress_cb(1.0, f"전체 {total}개 녹화 파일 품질 전수검사 완료")
        except Exception:
            pass
    return results


# ---------------------------------------------------------------- what has been recorded
_SCREEN_ZONE_LABELS: Dict[Tuple[int, int], str] = {
    (0, 0): "왼쪽 위",
    (1, 0): "위",
    (2, 0): "오른쪽 위",
    (0, 1): "왼쪽",
    (1, 1): "중앙",
    (2, 1): "오른쪽",
    (0, 2): "왼쪽 아래",
    (1, 2): "아래",
    (2, 2): "오른쪽 아래",
}


def _classify_screen_zone(px: float, py: float) -> str:
    """Classify normalized 2D camera coordinate (px, py in 0..1) into a 3x3 screen region."""
    gx = 0 if px < 0.36 else (2 if px > 0.64 else 1)
    gy = 0 if py < 0.36 else (2 if py > 0.64 else 1)
    return _SCREEN_ZONE_LABELS.get((gx, gy), "중앙")


def _format_screen_zones_summary(zone_counts: Dict[str, int], planned_positions: Sequence[str] = ()) -> str:
    """Format a human-readable summary of 3x3 camera screen regions covered in a recording."""
    total = sum(zone_counts.values())
    if total <= 0:
        if planned_positions:
            return "목표 위치: " + " · ".join(planned_positions)
        return "감지된 화면 영역 없음"
    ranked = sorted(zone_counts.items(), key=lambda kv: kv[1], reverse=True)
    top_parts = [f"{z}({int(round(100.0 * c / total))}%)" for z, c in ranked[:4] if c > 0]
    zone_str = f"화면 {len(ranked)}/9영역: " + " · ".join(top_parts)
    if len(ranked) > 4:
        zone_str += f" 외 {len(ranked) - 4}곳"
    if planned_positions:
        zone_str += f" (지정 위치: {', '.join(planned_positions[:3])})"
    return zone_str


def _build_recording_intent_fields(
    mode: Optional[str],
    focus_key: str,
    intended_tasks: List[str],
    reps_dict: Dict[str, Dict[str, int]],
    free_minutes: Dict[str, float],
    conditions: List[str],
) -> Dict[str, Any]:
    """Build explicit human-readable descriptions of which gesture(s) and postures a recording is capturing."""
    ordered_keys: List[str] = []
    if focus_key and focus_key in BY_KEY:
        ordered_keys.append(focus_key)
    for tk in intended_tasks:
        if tk in BY_KEY and tk not in ordered_keys:
            ordered_keys.append(tk)
    for tk, by_p in reps_dict.items():
        if sum(by_p.values()) > 0 and tk in BY_KEY and tk not in ordered_keys:
            ordered_keys.append(tk)
    for tk, mins in free_minutes.items():
        if mins > 0.01 and tk in BY_KEY and tk not in ordered_keys:
            ordered_keys.append(tk)
    if not ordered_keys and mode == "free":
        ordered_keys.append("none")

    non_free_keys = [k for k in ordered_keys if k != "none"]
    display_keys = non_free_keys + ([k for k in ordered_keys if k == "none"] if not non_free_keys or len(ordered_keys) <= 3 else [])

    gesture_parts: List[str] = []
    short_parts: List[str] = []
    instructions: List[str] = []
    for k in display_keys:
        g_name = NAMES.get(k, k)
        task_obj = BY_KEY.get(k)
        rep_cnt = sum((reps_dict.get(k) or {}).values())
        f_min = float(free_minutes.get(k, 0.0))
        if is_free(k):
            if f_min >= 0.1:
                gesture_parts.append(f"{g_name} (자유 {f_min:.1f}분)")
            else:
                gesture_parts.append(f"{g_name} (오작동 방지 이동)")
            short_parts.append(g_name)
        else:
            if rep_cnt > 0:
                gesture_parts.append(f"{g_name} ({rep_cnt}회 반복)")
                short_parts.append(f"{g_name} {rep_cnt}회")
            else:
                gesture_parts.append(f"{g_name} (목표 지정됨 · 완료 반복 0회)")
                short_parts.append(f"{g_name}")
        if task_obj and task_obj.do and task_obj.do not in instructions:
            instructions.append(f"{g_name}: {task_obj.do}" if len(display_keys) > 1 else task_obj.do)

    mode_ko = "자유 이동 녹화" if mode == "free" else ("집중 가이드 녹화" if focus_key else "통합 가이드 녹화")
    target_gestures_text = " · ".join(gesture_parts) if gesture_parts else ("자유 이동 (그냥 이동)" if mode == "free" else "지정된 동작 없음")
    target_short_text = " · ".join(short_parts[:2]) + (f" 외 {len(short_parts) - 2}개" if len(short_parts) > 2 else "")
    if not target_short_text:
        target_short_text = "자유 이동" if mode == "free" else "미지정"
    target_conditions_text = " · ".join(conditions) if conditions else ("일상 마우스 사용 자세" if mode == "free" else "기본 촬영 자세")
    target_instruction_text = " / ".join(instructions[:2]) if instructions else "카메라 앞에서 안내된 손동작을 수행합니다."

    return {
        "focus": focus_key,
        "recorded_mode_label": mode_ko,
        "intended_tasks": ordered_keys,
        "conditions": conditions,
        "target_gestures_text": target_gestures_text,
        "target_short_text": target_short_text,
        "target_conditions_text": target_conditions_text,
        "target_instruction_text": target_instruction_text,
    }


def _summarise_file(path: Path) -> Dict[str, Any]:
    """Repetitions per gesture and posture, minutes of free moving, marks - cached next to the recording."""
    cache = path.with_name(path.name + ".summary.json")
    stat = path.stat()
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("summary_version") == 5 and data.get("size") == stat.st_size and data.get("mtime") == int(stat.st_mtime):
                return data
        except Exception:
            pass
    from src.recorder import public_source, read_session

    meta, frames, events = read_session(path)
    if not meta.get("complete"):
        raise ValueError("끝나지 않은 녹화 (학습에서 빠짐)")
    reps: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    from src.capture_dataset import frame_durations, usable
    from src.recording_plan import label_condition_key
    repetitions = defaultdict(lambda: {"do": 0.0, "rest": False})
    free = defaultdict(float)
    seconds = frame_durations(frames)
    coverage = {k: {} for k in NAMES}
    valid_frames = 0
    pose_frames = 0
    intended_tasks: List[str] = []
    conditions: List[str] = []
    planned_positions: List[str] = []
    zone_counts: Dict[str, int] = defaultdict(int)
    spatial_acc: Dict[str, Dict[Tuple[int, int], List[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))
    for row_cfg in (meta.get("capture_plan") or {}).get("conditions") or []:
        if isinstance(row_cfg, dict) and row_cfg.get("enabled", True):
            pos_val = str(row_cfg.get("position") or "").strip()
            if pos_val and pos_val not in planned_positions:
                planned_positions.append(pos_val)
    focus_key = str(meta.get("focus") or (meta.get("capture_plan") or {}).get("focus") or "")
    for i, f in enumerate(frames):
        if isinstance(f.get("pose"), list) and len(f["pose"]) >= 6:
            pose_frames += 1
        lm_f = f.get("lm")
        px_val: Optional[float] = None
        py_val: Optional[float] = None
        if isinstance(lm_f, list) and len(lm_f) == 21:
            try:
                px_val = sum(float(lm_f[j][0]) for j in (0, 5, 9, 13, 17)) / 5.0
                py_val = sum(float(lm_f[j][1]) for j in (0, 5, 9, 13, 17)) / 5.0
                zone_counts[_classify_screen_zone(px_val, py_val)] += 1
            except Exception:
                px_val = None
                py_val = None
        lab = f.get("label") or {}
        pos_lbl = lab.get("position")
        if isinstance(pos_lbl, str) and pos_lbl.strip() and pos_lbl.strip() not in planned_positions:
            planned_positions.append(pos_lbl.strip())
        t_cand = lab.get("task") or lab.get("target")
        if isinstance(t_cand, str) and t_cand in BY_KEY and t_cand not in intended_tasks:
            intended_tasks.append(t_cand)
        cond_str = lab.get("condition")
        if isinstance(cond_str, str) and cond_str.strip() and cond_str.strip() not in conditions:
            conditions.append(cond_str.strip())
        if not usable(f):
            continue
        valid_frames += 1
        ck = label_condition_key(lab)
        task = lab.get("task")
        if px_val is not None and py_val is not None:
            c_px = min(0.999, max(0.0, px_val))
            c_py = min(0.999, max(0.0, py_val))
            gx = min(15, max(0, int(c_px * 16.0)))
            gy = min(9, max(0, int(c_py * 10.0)))
            s_lbl: Optional[str] = None
            if lab.get("phase") == "do" and task in BY_KEY and not is_free(task):
                s_lbl = BY_KEY[task].label
            elif lab.get("phase") == "free" and is_free(task):
                s_lbl = "none"
            elif meta.get("mode") == "free" and lab.get("target") == "none":
                s_lbl = "none"
            if s_lbl:
                cell = spatial_acc[s_lbl][(gx, gy)]
                cell[0] += 1.0
                cell[1] += c_px
                cell[2] += c_py
        if lab.get("phase") in ("do", "rest") and task in BY_KEY and not is_free(task):
            key = (lab["task"], ck, lab.get("set"), lab.get("rep"))
            item = repetitions[key]
            item["new"] = bool(lab.get("condition_key"))
            if lab["phase"] == "do":
                item["do"] += seconds[i]
            else:
                item["rest"] = True
        elif lab.get("phase") == "free" and is_free(task):
            free[(task, ck)] += seconds[i]
        elif meta.get("mode") == "free" and lab.get("target") == "none":
            free[("none", "use")] += seconds[i]
    for (task, ck, _, _), item in repetitions.items():
        if item["do"] > 0 and (not item.get("new") or (item["do"] >= 0.25 and (item["rest"] or item["do"] >= 1.0))):
            reps[task][ck] += 1
    for task, by_condition in reps.items():
        coverage[task] = {ck: {"reps": n, "seconds": 0.0} for ck, n in by_condition.items()}
    for (task, ck), value in free.items():
        coverage[task][ck] = {"reps": 0, "seconds": value}
    reps_plain = {k: dict(v) for k, v in reps.items()}
    free_mins = {task: sum(v for (t, _), v in free.items() if t == task) / 60 for task in NAMES if is_free(task)}
    intent_fields = _build_recording_intent_fields(
        meta.get("mode"),
        focus_key,
        intended_tasks,
        reps_plain,
        free_mins,
        conditions,
    )
    screen_zones_dict = dict(zone_counts)
    screen_zones_text = _format_screen_zones_summary(screen_zones_dict, planned_positions)
    spatial_bins_dict: Dict[str, List[List[float]]] = {}
    for s_lbl, grid_map in spatial_acc.items():
        bins_list = []
        for (gx, gy), (cnt, sx, sy) in sorted(grid_map.items()):
            if cnt > 0:
                bins_list.append([int(gx), int(gy), int(cnt), round(sx / cnt, 4), round(sy / cnt, 4)])
        if bins_list:
            spatial_bins_dict[s_lbl] = bins_list
    data = {
        "summary_version": 5,
        "file": path.name, "size": stat.st_size, "mtime": int(stat.st_mtime), "mode": meta.get("mode"),
        "created": meta.get("created"), "pc": public_source(meta)[0], "camera": public_source(meta)[1],
        "frames": len(frames), "reps": reps_plain,
        "free_minutes": free_mins,
        "coverage": coverage, "valid_frames": valid_frames, "pose_frames": pose_frames,
        "screen_zones": screen_zones_dict,
        "screen_zones_text": screen_zones_text,
        "spatial_bins": spatial_bins_dict,
        "planned_positions": planned_positions,
        "seconds": float(seconds.sum()),
        "marks": sum(1 for e in events if e.get("kind") == "mark"),
        **intent_fields,
    }
    try:
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return data


def _get_file_spatial_bins(path: Path) -> Dict[str, List[List[float]]]:
    """Return cached 16x10 per-gesture spatial bins for a recording, computing and caching if missing."""
    if not path.is_file():
        return {}
    cache = path.with_name(path.name + ".summary.json")
    stat = path.stat()
    if cache.exists():
        try:
            doc = json.loads(cache.read_text(encoding="utf-8"))
            if (
                doc.get("summary_version") == 5
                and doc.get("size") == stat.st_size
                and doc.get("mtime") == int(stat.st_mtime)
                and isinstance(doc.get("spatial_bins"), dict)
            ):
                return doc["spatial_bins"]
        except Exception:
            pass
    try:
        if cache.exists():
            cache.unlink(missing_ok=True)
        doc = _summarise_file(path)
        return dict(doc.get("spatial_bins") or {})
    except Exception:
        return {}


def dataset_summary(rec_dir: Path = REC_DIR, *, compute_quality: bool = True) -> Dict[str, Any]:
    from src.action_bindings import get_action_registry
    get_action_registry()
    folder = Path(rec_dir)
    files = sorted(folder.glob("*.jsonl.gz"))
    excluded = set(load_training_selection(folder).get("excluded", []))
    per: Dict[str, Dict[str, Any]] = {k: {"reps": 0, "postures": {}, "minutes": 0.0} for k in NAMES}
    out_files = []
    coverage = {k: {} for k in NAMES}
    selected_mtimes = []
    for p in files:
        is_sel = p.name not in excluded
        try:
            s = dict(_summarise_file(p))
        except Exception as exc:          # a broken / unfinished file must not hide the others
            out_files.append({
                "file": p.name, "error": str(exc), "selected": False,
                "quality_status": "fail", "quality_score": 0.0, "quality_summary": str(exc),
            })
            continue
        q = get_recording_quality(p, compute_if_missing=compute_quality)
        s["selected"] = is_sel
        s["quality_status"] = q.get("status", "unknown")
        s["quality_score"] = q.get("score")
        s["quality_summary"] = q.get("summary", "")
        s["quality_issues"] = q.get("issues", [])
        out_files.append(s)
        if not is_sel:
            continue
        selected_mtimes.append(p.stat().st_mtime)
        for task, conditions in s.get("coverage", {}).items():
            for key, values in conditions.items():
                current = coverage[task].setdefault(key, {"reps": 0, "seconds": 0.0})
                current["reps"] += values["reps"]
                current["seconds"] += values["seconds"]
        for task, by_posture in s["reps"].items():
            g = per.setdefault(task, {"reps": 0, "postures": {}, "minutes": 0.0})
            for posture, n in by_posture.items():
                g["reps"] += n
                g["postures"][posture] = g["postures"].get(posture, 0) + n
        for task, minutes in s["free_minutes"].items():
            per[task]["minutes"] += minutes
    newest = max(selected_mtimes, default=0.0)
    total = sum(p.stat().st_size for p in files)
    all_conditions = sorted({ck for g in per.values() for ck in g.get("postures", {}).keys() if ck})
    return {
        "files": out_files,
        "per_gesture": per,
        "newest": newest,
        "total_bytes": total,
        "coverage": coverage,
        "condition_count": len(all_conditions),
        "excluded_count": sum(1 for f in out_files if not f.get("selected", True) and "error" not in f),
    }


def storage_text(summary: Dict[str, Any]) -> Tuple[str, bool]:
    """(text, too big) for the recordings kept in the git repository together with the code (user's choice,
    2026-11-05). Past STORAGE_LIMIT_MB: suggest moving them to a repository of their own."""
    mb = summary.get("total_bytes", 0) / 1024 / 1024
    if mb > STORAGE_LIMIT_MB:
        return (f"녹화 총 {mb:.0f} MB: {STORAGE_LIMIT_MB} MB를 넘었어요. 받기가 느려지니 학습 데이터를 "
                f"전용 저장소로 나누는 걸 권해요 (docs/training-studio.md)", True)
    return f"녹화 총 {mb:.0f} MB / 분리 권장 {STORAGE_LIMIT_MB} MB", False


def model_report(model: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    model = Path(model) if model is not None else MODEL
    info_path = Path(model).with_suffix(".json")
    bank_dir = Path(model).parent / "gestures"
    from src.gesture_models import read_bank

    bank_entries = read_bank(bank_dir) if (bank_dir / "manifest.json").is_file() else {}
    has_main = Path(model).exists() and info_path.exists()
    if not has_main and not bank_entries:
        return None
    if has_main:
        info = json.loads(info_path.read_text(encoding="utf-8"))
        info["mtime"] = Path(model).stat().st_mtime
    else:
        info = {"classes": ["none"], "per_gesture": {}, "per_task": {}, "mtime": 0.0}
    info["specialists"] = {}
    for label, entry in bank_entries.items():
        path = bank_dir / entry["card"]
        if not path.is_file():
            continue
        card = json.loads(path.read_text(encoding="utf-8"))
        card["mtime"] = path.stat().st_mtime
        info["specialists"][label] = card
        if path.stat().st_mtime > float(info.get("mtime") or 0.0):
            info["mtime"] = path.stat().st_mtime
    sp_path = Path(model).parent / "spatial_mlp.json"
    if sp_path.is_file():
        try:
            sp_doc = json.loads(sp_path.read_text(encoding="utf-8"))
            sp_meta = dict(sp_doc.get("metadata") or {})
            sp_specs = list((sp_doc.get("specialists") or {}).keys())
            info["spatial_mlp_live"] = {
                "test_accuracy": sp_meta.get("test_accuracy"),
                "train_accuracy": sp_meta.get("train_accuracy"),
                "specialists": sp_specs,
            }
            if not isinstance(info.get("spatial_mlp"), dict):
                info["spatial_mlp"] = info["spatial_mlp_live"]
        except Exception:
            pass
    return info


def _extract_label_metrics(report: Optional[Dict[str, Any]], label: str) -> Dict[str, Any]:
    """Extract effective evaluation metrics for a gesture label ('none' or gesture class) from a model report."""
    if not report:
        return {"trained": False}
    disabled = set(report.get("disabled_gestures") or ())
    spec = (report.get("specialists") or {}).get(label) if label != "none" else None
    is_disabled = (label in disabled) and (spec is None)
    if is_disabled:
        return {"trained": False, "disabled": True}
    source = spec if spec is not None else report
    pg = source.get("per_gesture") or {}
    m = dict(pg.get(label) or {})
    classes = list(source.get("classes") or [])
    is_trained = bool(spec is not None) or (label in classes and not is_disabled)
    if label == "none" and not m and (report.get("specialists")):
        # Aggregate free-move misfires across specialist cards when no main card exists
        specs = list((report.get("specialists") or {}).values())
        none_entries = [(s.get("per_gesture") or {}).get("none") or {} for s in specs]
        mins = max([float(e.get("free_minutes") or 0.0) for e in none_entries] or [0.0])
        tcn_mis = sum(int(e.get("misfires") or 0) for e in none_entries)
        rule_mis = max([int(e.get("rules_misfires") or 0) for e in none_entries] or [0])
        if mins > 0:
            m = {
                "free_minutes": round(mins, 2),
                "misfires": tcn_mis,
                "rules_misfires": rule_mis,
                "misfires_per_min": round(tcn_mis / mins, 2),
                "rules_misfires_per_min": round(rule_mis / mins, 2),
            }
            is_trained = True

    sp_acc = None
    if spec and isinstance(spec.get("spatial_mlp"), dict):
        sp_meta = spec["spatial_mlp"].get("metadata") or {}
        sp_acc = sp_meta.get("test_accuracy")
    if sp_acc is None:
        sp_live = report.get("spatial_mlp_live") or report.get("spatial_mlp") or {}
        if isinstance(sp_live, dict):
            spec_map = sp_live.get("specialists")
            if isinstance(spec_map, dict) and isinstance(spec_map.get(label), dict):
                sp_acc = spec_map[label].get("test_accuracy")
            elif label != "none":
                per_cls = sp_live.get("per_class") or {}
                if isinstance(per_cls.get(label), dict):
                    sp_acc = per_cls[label].get("accuracy")
    # Compute per-posture breakdown for this label from held_out_results / final_results + condition_groups
    from src.recording_plan import resolve_condition_group_name

    cond_groups = list(source.get("condition_groups") or [])
    cond_names: Dict[int, str] = {
        idx_c: resolve_condition_group_name(cg, idx_c) for idx_c, cg in enumerate(cond_groups)
    }

    label_task_keys = [k for k, t in BY_KEY.items() if t.label == label]
    held_by_posture: Dict[int, Dict[str, int]] = {}
    for hr in source.get("held_out_results") or []:
        p = hr.get("posture", hr.get("test_posture"))
        if p is None:
            continue
        p_int = int(p)
        bucket = held_by_posture.setdefault(p_int, {"recog": 0, "total": 0, "confused": 0, "misfires": 0})
        tcn = hr.get("tcn") or {}
        reps = tcn.get("reps") or {}
        for tk in label_task_keys:
            if tk in reps and len(reps[tk]) >= 2:
                bucket["recog"] += int(reps[tk][0])
                bucket["total"] += int(reps[tk][1])
        bucket["confused"] += int(tcn.get("confused") or 0)
        bucket["misfires"] += int(sum((tcn.get("misfires") or {}).values()))

    final_by_posture: Dict[int, Dict[str, int]] = {}
    for fr in source.get("final_results") or []:
        p = fr.get("posture", fr.get("test_posture"))
        if p is None:
            continue
        p_int = int(p)
        bucket = final_by_posture.setdefault(p_int, {"recog": 0, "total": 0, "confused": 0, "misfires": 0})
        tcn = fr.get("tcn") or {}
        reps = tcn.get("reps") or {}
        for tk in label_task_keys:
            if tk in reps and len(reps[tk]) >= 2:
                bucket["recog"] += int(reps[tk][0])
                bucket["total"] += int(reps[tk][1])
        bucket["confused"] += int(tcn.get("confused") or 0)
        bucket["misfires"] += int(sum((tcn.get("misfires") or {}).values()))

    posture_breakdown: List[Dict[str, Any]] = []
    all_p_indices = sorted(set(held_by_posture.keys()) | set(final_by_posture.keys()))
    for p_int in all_p_indices:
        hb = held_by_posture.get(p_int, {})
        fb = final_by_posture.get(p_int, {})
        zs_r = round(hb["recog"] / hb["total"], 3) if hb.get("total") else None
        fn_r = round(fb["recog"] / fb["total"], 3) if fb.get("total") else None
        posture_breakdown.append({
            "posture": p_int,
            "name": cond_names.get(p_int, f"자세 #{p_int + 1}"),
            "zero_shot_rate": zs_r,
            "zero_shot_recognised": hb.get("recog", 0),
            "zero_shot_total": hb.get("total", 0),
            "final_rate": fn_r,
            "final_recognised": fb.get("recog", 0),
            "final_total": fb.get("total", 0),
            "confused": hb.get("confused", 0),
            "misfires": hb.get("misfires", 0),
        })

    return {
        "trained": is_trained,
        "specialist": spec is not None,
        "rate": m.get("rate"),
        "zero_shot_rate": m.get("zero_shot_rate", m.get("rate")),
        "final_rate": m.get("final_rate"),
        "adaptive_boosted": bool(m.get("adaptive_boosted")),
        "adaptive_boost": source.get("adaptive_boost"),
        "rules_rate": m.get("rules_rate"),
        "recognised": m.get("recognised"),
        "total": m.get("total"),
        "train_accuracy": m.get("train_accuracy"),
        "misfires_per_min": m.get("misfires_per_min"),
        "rules_misfires_per_min": m.get("rules_misfires_per_min"),
        "misfires": m.get("misfires"),
        "rules_misfires": m.get("rules_misfires"),
        "free_minutes": m.get("free_minutes"),
        "spatial_accuracy": sp_acc,
        "training_mode": source.get("training_mode"),
        "folds": list(source.get("folds") or []),
        "files": [str(d.get("file") or "") for d in (source.get("data") or []) if isinstance(d, dict) and d.get("file")],
        "posture_breakdown": posture_breakdown,
    }


def _diagnose_label_performance(
    cur: Dict[str, Any],
    prev: Dict[str, Any],
    label: str,
) -> Dict[str, Any]:
    """
    Analyze why recognition rate or metrics dropped/changed after training and summarize
    adaptive posture boosting recovery for heterogeneous hand angles/postures.
    """
    reasons: List[str] = []
    boost_summary = ""
    cur_rate = cur.get("rate")
    prev_rate = prev.get("rate") if prev.get("trained") else None
    zs_rate = cur.get("zero_shot_rate")
    final_rate = cur.get("final_rate")
    cur_fit = cur.get("train_accuracy")
    prev_fit = prev.get("train_accuracy") if prev.get("trained") else None
    delta_pp = round((cur_rate - prev_rate) * 100.0, 1) if (cur_rate is not None and prev_rate is not None) else None

    if label == "none":
        cur_mpm = cur.get("misfires_per_min")
        prev_mpm = prev.get("misfires_per_min") if prev.get("trained") else None
        if cur_mpm is not None and prev_mpm is not None and (cur_mpm - prev_mpm) >= 0.3:
            pb = cur.get("posture_breakdown") or []
            worst_mis = sorted([p for p in pb if p.get("misfires", 0) > 0], key=lambda x: x["misfires"], reverse=True)
            if worst_mis:
                w = worst_mis[0]
                reasons.append(
                    f"원인 분석: 자유 이동 중 [{w['name']}] 조건에서 오작동({w['misfires']}회)이 집중 발생하여 분당 오작동이 증가했습니다."
                )
            else:
                reasons.append("원인 분석: 자유 이동 구간에서 목표 동작과 유사한 손가락 간격이 감지되어 분당 오작동이 증가했습니다.")
        return {
            "diagnosis_reasons": reasons,
            "diagnosis_summary": " · ".join(reasons),
            "boost_summary": "",
        }

    pb = cur.get("posture_breakdown") or []
    valid_zs = [p for p in pb if p.get("zero_shot_rate") is not None and p.get("zero_shot_total", 0) > 0]
    weak_postures = [p for p in valid_zs if float(p["zero_shot_rate"]) < 0.78]
    strong_postures = [p for p in valid_zs if float(p["zero_shot_rate"]) >= 0.80]
    total_confused = sum(int(p.get("confused") or 0) for p in pb)

    # 1. Check Adaptive Posture Boosting recovery
    ab = cur.get("adaptive_boost") or {}
    if isinstance(ab, dict) and ab.get("triggered"):
        rounds = int(ab.get("rounds") or 1)
        p_details = ab.get("posture_details") or []
        boosted_names = [
            f"{d.get('name')} (미학습 교차 {float(d['zero_shot_rate']) * 100:.0f}% → 재학습 후 {float(d.get('after_final_rate') or 1.0) * 100:.0f}%)"
            for d in p_details
            if d.get("boosted") and d.get("zero_shot_rate") is not None
        ]
        if boosted_names:
            boost_summary = (
                f"적응형 자세 재학습({rounds}회) 완료: 이질적인 각도·자세 [{', '.join(boosted_names[:2])}]를 집중 추가 학습하여 인식률을 끌어올렸습니다."
            )
        elif zs_rate is not None and final_rate is not None and final_rate > zs_rate:
            boost_summary = (
                f"적응형 자세 재학습({rounds}회) 완료: 새로운 각도·자세 교차검증({zs_rate * 100:.0f}%) 약점을 보완하여 전체 자세 인식률을 {final_rate * 100:.0f}%로 향상했습니다."
            )

    # 2. Analyze why rate dropped or why zero-shot cross-posture rate is low
    is_dropped = (delta_pp is not None and delta_pp <= -1.5) or (cur_rate is not None and cur_rate < 0.80)
    prev_folds = prev.get("folds") or []
    cur_folds = cur.get("folds") or []
    prev_files = set(prev.get("files") or [])
    cur_files = set(cur.get("files") or [])
    added_files = sorted(cur_files - prev_files)

    if is_dropped or weak_postures:
        if weak_postures:
            weak_desc = ", ".join(
                f"{p['name']}({float(p['zero_shot_rate']) * 100:.0f}%)"
                for p in sorted(weak_postures, key=lambda x: float(x["zero_shot_rate"]))[:2]
            )
            if strong_postures:
                best_p = max(strong_postures, key=lambda x: float(x["zero_shot_rate"]))
                reasons.append(
                    f"이질적인 자세·각도 차이: 기본 자세[{best_p['name']} {float(best_p['zero_shot_rate']) * 100:.0f}%]와 달리 "
                    f"[{weak_desc}] 자세는 손목 회전·카메라 각도가 이질적이라 해당 자세를 제외하고 채점하는 미학습 교차검증(Zero-shot) 점수가 낮게 측정되었습니다."
                )
            else:
                reasons.append(
                    f"자세·각도 편차: [{weak_desc}] 조건에서 손 방향/각도 변화가 커서 교차 자세 인식률이 낮게 측정되었습니다."
                )
        if prev.get("trained") and len(cur_folds) > len(prev_folds) and len(prev_folds) <= 1:
            reasons.append(
                f"평가 범위 확대 영향: 직전 모델은 '빠른 학습(대표 자세 {max(1, len(prev_folds))}개만 검사)'이었고, "
                f"이번에는 '정밀 학습(전체 {len(cur_folds)}개 자세 엄격 교차검사)'으로 채점 대상에 까다로운 각도가 모두 포함되었습니다."
            )
        if added_files and delta_pp is not None and delta_pp < 0:
            reasons.append(
                f"신규 녹화 데이터 영향: 새로 추가된 녹화({', '.join(added_files[:2])})에 기존과 다른 손 각도 또는 전환 구간이 포함되어 교차 검증 기준이 달라졌습니다."
            )
        if cur_fit is not None and prev_fit is not None and (cur_fit - prev_fit) <= -0.03:
            reasons.append(
                f"동작 일관성 저하: 학습 데이터 일치도가 {prev_fit * 100:.0f}% → {cur_fit * 100:.0f}%로 낮아졌습니다 (녹화 중 손 모양 흔들림 가능성)."
            )
        elif cur_fit is not None and cur_fit >= 0.97 and (zs_rate is not None and zs_rate < 0.85):
            if final_rate is not None:
                reasons.append(
                    f"실제 최종 모델 성능 정상: 학습 데이터 일치도({cur_fit * 100:.0f}%)와 모든 자세를 학습한 최종 모델 인식률({final_rate * 100:.0f}%)은 정상입니다."
                )
            else:
                reasons.append(
                    f"실제 최종 모델 성능 정상: 모든 자세를 함께 학습한 최종 모델의 일치도는 {cur_fit * 100:.0f}%로 새로운 각도를 정상 학습했습니다."
                )
        if total_confused > 0:
            reasons.append(f"타 동작 혼동: 반복 수행 중 다른 손동작으로 오인식된 횟수가 {total_confused}회 있었습니다.")

    summary_parts = []
    if boost_summary:
        summary_parts.append(boost_summary)
    if reasons:
        summary_parts.extend(reasons[:2])
    return {
        "diagnosis_reasons": reasons,
        "diagnosis_summary": " / ".join(summary_parts),
        "boost_summary": boost_summary,
    }


def evaluate_training_improvement(
    after_report: Optional[Dict[str, Any]] = None,
    before_report: Optional[Dict[str, Any]] = None,
    *,
    target_keys: Optional[Sequence[str]] = None,
    backup_id: Optional[str] = None,
    model: Path = MODEL,
    backup_dir: Path = BACKUP_DIR,
) -> Dict[str, Any]:
    """Compare the newly trained model (`after_report`) against `before_report` (or latest backup) and rules baseline."""
    if after_report is None:
        after_report = model_report(model)
    if not after_report:
        return {"available": False, "headline": "평가할 학습 모델이 없습니다.", "items": []}

    resolved_backup_id = backup_id
    if before_report is None and Path(backup_dir).is_dir():
        backups = sorted(
            [d for d in Path(backup_dir).glob("backup-*") if d.is_dir()],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if backups:
            latest_bak = backups[0]
            resolved_backup_id = f"backup:{latest_bak.name}"
            before_report = model_report(latest_bak / "gesture_tcn.onnx")

    has_before = bool(before_report)
    target_labels: Optional[set[str]] = None
    if target_keys:
        target_labels = {BY_KEY[k].label for k in target_keys if k in BY_KEY and BY_KEY[k].label != "none"}

    # Map each gesture label to its primary display name in Korean
    label_Title: Dict[str, str] = {}
    label_Keys: Dict[str, List[str]] = defaultdict(list)
    for k, t in BY_KEY.items():
        if t.label == "none":
            continue
        label_Keys[t.label].append(k)
        if t.label not in label_Title:
            label_Title[t.label] = t.name
        elif t.label == "thumb_middle":
            label_Title[t.label] = "좌클릭 · 드래그 (중지)"

    items: List[Dict[str, Any]] = []
    by_task_delta: Dict[str, Dict[str, Any]] = {}

    rate_deltas_vs_prev: List[float] = []
    rate_deltas_vs_rules: List[float] = []
    improved_cnt = 0
    regressed_cnt = 0

    for label in CLASSES:
        if label == "none":
            continue
        if target_labels is not None and label not in target_labels:
            continue
        cur = _extract_label_metrics(after_report, label)
        if not cur.get("trained"):
            continue
        prev = _extract_label_metrics(before_report, label) if has_before else {"trained": False}

        cur_rate = cur.get("rate")
        prev_rate = prev.get("rate") if prev.get("trained") else None
        rules_rate = cur.get("rules_rate")
        cur_fit = cur.get("train_accuracy")
        prev_fit = prev.get("train_accuracy") if prev.get("trained") else None
        cur_sp = cur.get("spatial_accuracy")
        prev_sp = prev.get("spatial_accuracy") if prev.get("trained") else None

        delta_prev_pp = round((cur_rate - prev_rate) * 100.0, 1) if (cur_rate is not None and prev_rate is not None) else None
        delta_rules_pp = round((cur_rate - rules_rate) * 100.0, 1) if (cur_rate is not None and rules_rate is not None) else None
        fit_delta_pp = round((cur_fit - prev_fit) * 100.0, 1) if (cur_fit is not None and prev_fit is not None) else None

        if delta_prev_pp is not None:
            rate_deltas_vs_prev.append(delta_prev_pp)
        if delta_rules_pp is not None:
            rate_deltas_vs_rules.append(delta_rules_pp)

        # Determine badge & status
        status = "same"
        badge = "유지 ="
        if delta_prev_pp is not None:
            if delta_prev_pp >= 1.0:
                status = "improved"
                badge = f"인식 +{delta_prev_pp:.0f}%p ▲"
                improved_cnt += 1
            elif delta_prev_pp <= -2.0:
                status = "regressed"
                badge = f"인식 {delta_prev_pp:.0f}%p ▼"
                regressed_cnt += 1
            else:
                status = "same"
                badge = "이전 수준 유지 ="
        elif fit_delta_pp is not None and abs(fit_delta_pp) >= 1.0:
            if fit_delta_pp > 0:
                status = "improved"
                badge = f"학습정확도 +{fit_delta_pp:.0f}%p ▲"
                improved_cnt += 1
            else:
                status = "regressed"
                badge = f"학습정확도 {fit_delta_pp:.0f}%p ▼"
                regressed_cnt += 1
        elif delta_rules_pp is not None:
            if delta_rules_pp >= 1.0:
                status = "improved"
                badge = f"규칙 대비 +{delta_rules_pp:.0f}%p ▲"
                improved_cnt += 1
            elif delta_rules_pp <= -2.0:
                status = "regressed"
                badge = f"규칙 대비 {delta_rules_pp:.0f}%p ▼"
            else:
                status = "same"
                badge = "규칙 동률 ="
        elif not prev.get("trained"):
            status = "new"
            badge = "신규 학습 ★"
            improved_cnt += 1

        # Build concise detail string
        parts: List[str] = []
        if cur_rate is not None:
            if prev_rate is not None:
                sign = "+" if (delta_prev_pp or 0) > 0 else ""
                parts.append(f"인식률 {prev_rate * 100:.0f}% → {cur_rate * 100:.0f}% ({sign}{delta_prev_pp:.0f}%p)")
            else:
                parts.append(f"인식률 {cur_rate * 100:.0f}%")
            if rules_rate is not None:
                r_sign = "+" if (delta_rules_pp or 0) > 0 else ""
                parts.append(f"규칙 {rules_rate * 100:.0f}% 대비 {r_sign}{delta_rules_pp:.0f}%p")
        if cur_fit is not None:
            if prev_fit is not None and fit_delta_pp is not None and abs(fit_delta_pp) >= 1.0:
                f_sign = "+" if fit_delta_pp > 0 else ""
                parts.append(f"학습일치 {prev_fit * 100:.0f}%→{cur_fit * 100:.0f}%({f_sign}{fit_delta_pp:.0f}%p)")
            else:
                parts.append(f"학습일치 {cur_fit * 100:.0f}%")
        if isinstance(cur_sp, (int, float)):
            if isinstance(prev_sp, (int, float)) and abs(float(cur_sp) - float(prev_sp)) >= 0.5:
                sp_d = float(cur_sp) - float(prev_sp)
                sp_s = "+" if sp_d > 0 else ""
                parts.append(f"전용MLP {float(prev_sp):.0f}%→{float(cur_sp):.0f}%({sp_s}{sp_d:.0f}%p)")
            else:
                parts.append(f"전용MLP {float(cur_sp):.0f}%")

        diag = _diagnose_label_performance(cur, prev, label)
        zs_rate = cur.get("zero_shot_rate")
        final_rate = cur.get("final_rate")
        if final_rate is not None and zs_rate is not None and abs(final_rate - cur_rate) >= 0.01:
            parts.append(f"최종모델 전체자세 {final_rate * 100:.0f}%")

        item = {
            "label": label,
            "name": label_Title.get(label, label),
            "status": status,
            "badge": badge,
            "detail": " · ".join(parts) if parts else "학습 완료",
            "diagnosis_summary": diag["diagnosis_summary"],
            "diagnosis_reasons": diag["diagnosis_reasons"],
            "boost_summary": diag["boost_summary"],
            "rate_after": round(cur_rate * 100.0, 1) if cur_rate is not None else None,
            "rate_before": round(prev_rate * 100.0, 1) if prev_rate is not None else None,
            "zero_shot_rate": round(zs_rate * 100.0, 1) if zs_rate is not None else None,
            "final_rate": round(final_rate * 100.0, 1) if final_rate is not None else None,
            "rules_rate": round(rules_rate * 100.0, 1) if rules_rate is not None else None,
            "delta_prev_pp": delta_prev_pp,
            "delta_rules_pp": delta_rules_pp,
        }
        items.append(item)
        for tk in label_Keys.get(label, []):
            by_task_delta[tk] = item

    # Also evaluate free-movement misfire suppression ('none')
    cur_none = _extract_label_metrics(after_report, "none")
    prev_none = _extract_label_metrics(before_report, "none") if has_before else {"trained": False}
    cur_mpm = cur_none.get("misfires_per_min")
    prev_mpm = prev_none.get("misfires_per_min") if prev_none.get("trained") else None
    rules_mpm = cur_none.get("rules_misfires_per_min")
    mpm_delta_prev = round(cur_mpm - prev_mpm, 2) if (cur_mpm is not None and prev_mpm is not None) else None
    mpm_delta_rules = round(cur_mpm - rules_mpm, 2) if (cur_mpm is not None and rules_mpm is not None) else None

    if cur_mpm is not None:
        n_status = "same"
        n_badge = f"오작동 {cur_mpm:.1f}회/분"
        if mpm_delta_prev is not None:
            if mpm_delta_prev <= -0.1:
                n_status = "improved"
                n_badge = f"오작동 {mpm_delta_prev:.1f}/분 감소 ▲"
                improved_cnt += 1
            elif mpm_delta_prev >= 0.3:
                n_status = "regressed"
                n_badge = f"오작동 +{mpm_delta_prev:.1f}/분 증가 ▼"
                regressed_cnt += 1
            else:
                n_badge = "오작동 유지 ="
        elif mpm_delta_rules is not None and mpm_delta_rules <= -0.1:
            n_status = "improved"
            n_badge = f"규칙 대비 {mpm_delta_rules:.1f}/분 감소 ▲"

        n_parts = []
        if prev_mpm is not None:
            sign = "+" if (mpm_delta_prev or 0) > 0 else ""
            n_parts.append(f"자유이동 오작동 {prev_mpm:.1f} → {cur_mpm:.1f}회/분 ({sign}{mpm_delta_prev:.1f})")
        else:
            n_parts.append(f"자유이동 오작동 {cur_mpm:.1f}회/분")
        if rules_mpm is not None:
            r_sign = "+" if (mpm_delta_rules or 0) > 0 else ""
            n_parts.append(f"규칙 {rules_mpm:.1f}회/분 ({r_sign}{mpm_delta_rules:.1f})")

        none_diag = _diagnose_label_performance(cur_none, prev_none, "none")
        none_item = {
            "label": "none",
            "name": "커서 이동 중 오작동 억제",
            "status": n_status,
            "badge": n_badge,
            "detail": " · ".join(n_parts),
            "diagnosis_summary": none_diag["diagnosis_summary"],
            "diagnosis_reasons": none_diag["diagnosis_reasons"],
            "boost_summary": "",
            "misfires_after": cur_mpm,
            "misfires_before": prev_mpm,
            "rules_misfires": rules_mpm,
            "delta_prev": mpm_delta_prev,
            "delta_rules": mpm_delta_rules,
        }
        items.append(none_item)
        for tk, t in BY_KEY.items():
            if t.label == "none":
                by_task_delta[tk] = none_item

    # Overall Spatial MLP summary
    sp_after = (after_report.get("spatial_mlp_live") or after_report.get("spatial_mlp") or {}).get("test_accuracy")
    sp_before = (
        ((before_report or {}).get("spatial_mlp_live") or (before_report or {}).get("spatial_mlp") or {}).get("test_accuracy")
        if has_before
        else None
    )
    sp_delta = round(float(sp_after) - float(sp_before), 1) if (isinstance(sp_after, (int, float)) and isinstance(sp_before, (int, float))) else None

    avg_prev_pp = round(float(sum(rate_deltas_vs_prev) / len(rate_deltas_vs_prev)), 1) if rate_deltas_vs_prev else None
    avg_rules_pp = round(float(sum(rate_deltas_vs_rules) / len(rate_deltas_vs_rules)), 1) if rate_deltas_vs_rules else None

    headline_parts: List[str] = []
    if avg_prev_pp is not None:
        sign = "+" if avg_prev_pp > 0 else ""
        headline_parts.append(f"직전 모델 대비 평균 인식률 {sign}{avg_prev_pp:.1f}%p")
    elif avg_rules_pp is not None:
        sign = "+" if avg_rules_pp > 0 else ""
        headline_parts.append(f"기본 규칙 대비 평균 인식률 {sign}{avg_rules_pp:.1f}%p")

    if mpm_delta_prev is not None and abs(mpm_delta_prev) >= 0.05:
        sign = "+" if mpm_delta_prev > 0 else ""
        headline_parts.append(f"이동 오작동 {sign}{mpm_delta_prev:.1f}회/분")
    elif cur_mpm is not None and rules_mpm is not None:
        headline_parts.append(f"이동 오작동 {cur_mpm:.1f}회/분 (규칙 {rules_mpm:.1f})")

    if isinstance(sp_after, (int, float)):
        if sp_delta is not None and abs(sp_delta) >= 0.2:
            sign = "+" if sp_delta > 0 else ""
            headline_parts.append(f"96차원 전용 MLP {float(sp_after):.1f}% ({sign}{sp_delta:.1f}%p)")
        else:
            headline_parts.append(f"96차원 전용 MLP {float(sp_after):.1f}%")

    overall_status = "ok"
    if regressed_cnt > 0 and improved_cnt == 0:
        overall_status = "warn"
    elif improved_cnt > 0:
        overall_status = "improved"

    scope_prefix = "선택 동작 학습 효과" if target_labels else "전체 학습 개선 효과"
    headline = f"[{scope_prefix}] " + (" · ".join(headline_parts) if headline_parts else f"총 {len(items)}개 항목 학습 및 검증 완료")

    result = {
        "diagnosis_version": 2,
        "available": bool(items),
        "overall_status": overall_status,
        "has_before_model": has_before,
        "headline": headline,
        "improved_count": improved_cnt,
        "regressed_count": regressed_cnt,
        "can_rollback": bool(resolved_backup_id),
        "backup_id": resolved_backup_id or "",
        "spatial_accuracy_after": sp_after,
        "spatial_accuracy_before": sp_before,
        "spatial_accuracy_delta": sp_delta,
        "items": items,
        "by_task": by_task_delta,
        "evaluated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dismissed": False,
    }
    try:
        imp_path = Path(model).parent / "last_training_improvement.json"
        imp_path.parent.mkdir(parents=True, exist_ok=True)
        imp_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return result


def load_last_training_improvement(model: Path = MODEL) -> Dict[str, Any]:
    """Load `models/last_training_improvement.json` or compute on the fly from latest backup if missing."""
    imp_path = Path(model).parent / "last_training_improvement.json"
    if imp_path.is_file():
        try:
            doc = json.loads(imp_path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and doc.get("available") and doc.get("diagnosis_version") == 2:
                doc.setdefault("dismissed", False)
                return doc
        except Exception:
            pass
    rep = model_report(model)
    if rep is not None:
        return evaluate_training_improvement(after_report=rep, model=model, backup_dir=Path(model).parent / "backups")
    return {"available": False, "headline": "", "items": [], "by_task": {}, "dismissed": False}


# ---------------------------------------------------------------- what to do next
def gesture_rows(
    summary: Dict[str, Any],
    report: Optional[Dict[str, Any]],
    improvement: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """One row per gesture: data, scores, shortcut binding, track format, and advice, for the screen."""
    from src.action_bindings import get_action_registry
    from src.motion_mode import MotionTrajectoryModel

    reg = get_action_registry()
    motion_templates = MotionTrajectoryModel.load().templates
    rows = []
    pg = (report or {}).get("per_gesture", {})
    disabled = set((report or {}).get("disabled_gestures") or ())
    imp_by_task = (improvement or {}).get("by_task") or {}
    valid_files = sorted(
        [f for f in summary.get("files", []) if "error" not in f],
        key=lambda f: int(f.get("mtime", 0)),
        reverse=True,
    )
    for key, name in NAMES.items():
        task = BY_KEY[key]
        track = task.track
        binding = reg.get_binding(key)
        d = summary["per_gesture"].get(key, {"reps": 0, "postures": {}, "minutes": 0.0})
        latest_file = ""
        for vf in valid_files:
            if (
                sum((vf.get("reps", {}).get(key) or {}).values()) > 0
                or float((vf.get("free_minutes") or {}).get(key, 0.0)) > 0.0
                or key in (vf.get("target_gestures") or [])
            ):
                latest_file = str(vf.get("file") or "")
                break
        specialist = (report or {}).get("specialists", {}).get(task.label)
        motion_tmpl = (specialist or {}).get("motion_trajectory") or motion_templates.get(key)
        has_motion_tmpl = isinstance(motion_tmpl, dict) and bool(motion_tmpl.get("prototype_trajectory_x"))
        is_disabled = (task.label in disabled) and (specialist is None)
        current = {} if is_disabled else (specialist or report or {})
        m = {} if is_disabled else current.get("per_task", {}).get(key, current.get("per_gesture", pg).get(task.label, {}))
        classes = [c for c in current.get("classes", list(pg)) if c not in disabled or specialist is not None]
        is_trained = bool(specialist) or has_motion_tmpl or (not is_disabled and task.label != "none" and task.label in classes)
        stale = bool(current) and summary.get("newest", 0) > current.get("mtime", 0)
        cur_mode = current.get("training_mode")
        cur_folds = current.get("folds") or []
        is_partial_eval = bool(cur_mode == "fast" or (isinstance(cur_folds, list) and len(cur_folds) == 1))
        mode_tag = " · 빠른 학습(일부 자세 평가)" if is_partial_eval else (" · 정밀 학습(전체 자세 평가)" if cur_mode == "thorough" else "")
        has_sp_spec = bool(
            (specialist and isinstance(specialist.get("spatial_mlp"), dict))
            or (
                isinstance((report or {}).get("spatial_mlp"), dict)
                and task.label in ((report or {}).get("spatial_mlp", {}).get("specialists") or [])
            )
        )
        mlp_tag = " · 전용 MLP" if has_sp_spec else ""
        traj_tag = " · 시계열 궤적 템플릿" if has_motion_tmpl else ""
        if specialist:
            model_text = "선택 동작 모델 · " + str(specialist.get("trained", "")) + traj_tag + mlp_tag + mode_tag
        elif is_disabled:
            model_text = "학습 초기화됨 · 현재 규칙으로 동작"
        elif task.label in classes:
            model_text = "TCN 학습됨" + traj_tag + mlp_tag + mode_tag
        elif has_motion_tmpl:
            model_text = f"시계열 궤적 템플릿 학습됨 ({motion_tmpl.get('trained_at', '')})"
        else:
            model_text = "TCN 학습 전 · 현재 규칙으로 동작"

        if track == "motion":
            training_format_badge = "시계열 모션 학습 (16-step 궤적·속도 프로파일 + TCN)"
        elif track == "static":
            training_format_badge = "정지 동작·핀치 학습 (96D Spatial MLP + TCN)"
        else:
            training_format_badge = "연속 이동·중립 배경 데이터 (오작동 억제용)"

        if key in NEAR_MISS_TIPS:
            shortcut_badge, shortcut_detail = "오답 억제 전용", "닿을 듯 말 듯한 손가락을 동작으로 오판하지 않게 가르치는 자료"
        elif task.free:
            shortcut_badge, shortcut_detail = "포인터 이동 · 오답 억제", "커서 이동 및 클릭 오작동 억제용 배경 동작"
        else:
            shortcut_badge, shortcut_detail = str(binding.get("badge") or "미지정"), str(binding.get("detail") or "")
        motion_metrics_text = ""
        if has_motion_tmpl and isinstance(motion_tmpl, dict):
            tr_rate = float(motion_tmpl.get("trajectory_recognition_rate", 0.0)) * 100.0
            ret_rate = float(motion_tmpl.get("return_suppression_rate", 1.0)) * 100.0
            mean_spd = float(motion_tmpl.get("mean_speed", 0.0))
            motion_metrics_text = f"시계열 궤적 일치 {tr_rate:.0f}% · 복귀(역방향) 오발화 차단 {ret_rate:.0f}% · 평균 속도 {mean_spd:.2f}"

        task_imp = {} if is_disabled else (imp_by_task.get(key) or {})
        diag_summary = str(task_imp.get("diagnosis_summary") or "")
        if not diag_summary and not is_disabled and is_trained and report is not None:
            cur_l = _extract_label_metrics(report, task.label)
            diag_summary = _diagnose_label_performance(cur_l, {"trained": False}, task.label).get("diagnosis_summary", "")
        row = {"key": key, "name": name, "reps": d["reps"], "postures": len(d["postures"]),
               "category": task.category, "instruction": task.do,
               "track": track,
               "training_format_badge": training_format_badge,
               "shortcut_badge": shortcut_badge,
               "shortcut_detail": shortcut_detail,
               "action_type": str(binding.get("action_type", "mouse_action")),
               "action_value": str(binding.get("action_value", "none")),
               "app_overrides": list(binding.get("app_overrides") or []),
               "binding_enabled": bool(binding.get("enabled", True)),
               "is_custom_motion": key in reg.custom_motions,
               "has_motion_template": has_motion_tmpl,
               "motion_metrics_text": motion_metrics_text,
               "model_text": model_text,
               "can_train": task.label != "none", "label": task.label,
               "is_trained": is_trained, "is_disabled": is_disabled,
               "minutes": d.get("minutes", 0.0), "train_accuracy": m.get("train_accuracy"),
               "rate": m.get("rate"), "rules_rate": m.get("rules_rate"),
               "zero_shot_rate": m.get("zero_shot_rate"), "final_rate": m.get("final_rate"),
               "misfires_per_min": m.get("misfires_per_min"), "rules_misfires_per_min": m.get("rules_misfires_per_min"),
               "delta_badge": str(task_imp.get("badge") or ""),
               "delta_status": str(task_imp.get("status") or ""),
               "delta_detail": str(task_imp.get("detail") or ""),
               "diagnosis_summary": diag_summary,
               "latest_file": latest_file}
        advice, level = [], "ok"
        if task.free:
            near_miss = key in NEAR_MISS_TIPS
            goal_min = GOAL_NEAR_MISS_MIN if near_miss else GOAL_FREE_MIN
            data_ok = row["minutes"] >= goal_min
            row["data_text"] = f"{row['minutes']:.1f}분 / 목표 {goal_min:.0f}분"
            row["data_ratio"] = min(1.0, row["minutes"] / goal_min)
            if not data_ok:
                advice.append(f"이 {'오판 방지' if near_miss else '이동'} 동작의 녹화가 부족해요 ({goal_min:.0f}분 이상 권장)")
            if row["misfires_per_min"] is not None and row["misfires_per_min"] > MAX_MISFIRES_PER_MIN:
                advice.append("오작동이 많아요: " + ("이 동작을 다른 자세에서 더 녹화하세요" if near_miss else "반주먹 이동을 더 녹화하세요"))
            score = row["misfires_per_min"]
            row["score_text"] = "아직 평가 없음" if score is None else f"오작동 {score:.1f}회/분"
            if score is not None and row["rules_misfires_per_min"] is not None:
                row["score_text"] += f" (규칙 {row['rules_misfires_per_min']:.1f})"
            if score is not None and is_partial_eval:
                row["score_text"] += " · 일부 자세 평가"
        else:
            data_ok = row["reps"] >= GOAL_REPS and row["postures"] >= GOAL_POSTURES
            row["data_text"] = f"{row['reps']}회 / 목표 {GOAL_REPS}회 · 자세 {row['postures']}/{GOAL_POSTURES}"
            row["data_ratio"] = min(1.0, row["reps"] / GOAL_REPS)
            if row["reps"] < GOAL_REPS:
                advice.append(f"데이터가 부족해요 ({GOAL_REPS}회 이상 권장)")
            if row["postures"] < GOAL_POSTURES:
                advice.append("여러 자세로 녹화하세요")
            if row["rate"] is not None and row["rate"] < GOAL_RATE:
                advice.append(f"인식률이 낮아요 ({row['rate'] * 100:.0f}%): 이 동작을 집중 녹화하세요")
            row["score_text"] = "아직 평가 없음" if row["rate"] is None else f"인식 {row['rate'] * 100:.0f}%"
            if row["rate"] is not None and row["rules_rate"] is not None:
                row["score_text"] += f" (규칙 {row['rules_rate'] * 100:.0f}%)"
            if row["rate"] is not None and is_partial_eval:
                row["score_text"] += " · 일부 자세 평가"
        if row["train_accuracy"] is not None:
            row["fit_text"] = f"학습 {row['train_accuracy'] * 100:.0f}%"
        else:
            row["fit_text"] = "학습 전"
        if stale:
            advice.append("새 녹화가 있어요: 다시 학습하세요")
        if report is None:
            advice.append("아직 학습한 모델이 없어요")
        elif is_disabled:
            advice.append("학습이 초기화되어 규칙으로 동작 중입니다 (이 동작만 학습으로 다시 추가 가능)")
        elif task.label not in classes:
            advice.append("이 동작을 녹화하고 다시 학습하면 모델에 추가됩니다")
        if advice:
            level = "need" if any(("부족" in a or "낮아요" in a or "많아요" in a) for a in advice) else "retrain"
        row["advice"] = " · ".join(advice) if advice else "좋아요"
        row["level"] = level
        rows.append(row)
    return rows


# ---------------------------------------------------------------- training in the background
def terminate_training_process(proc):
    """Windows venv launchers have a child interpreter; stop that child as well."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    else:
        proc.terminate()


class TrainingJob:
    """Runs scripts/train_tcn.py --folds --progress in its own process (torch stays out of the app) and keeps its state."""

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.state: Dict[str, Any] = {"running": False}
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return bool(self.state.get("running"))

    def start(
        self,
        epochs: int = 25,
        gestures=None,
        *,
        mode: Optional[str] = None,
        offline_augment: Optional[bool] = None,
        device: Optional[str] = None,
    ) -> bool:
        if self.running:
            return False
        resolved_mode = mode if mode in ("fast", "thorough") else get_training_mode()
        effective_device = resolve_effective_training_device(device)
        if offline_augment is None:
            offline_augment = bool(get_augmentation_settings().get("enabled", True))
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        args = [
            training_python(),
            str(ROOT / "scripts" / "train_tcn.py"),
            "--mode",
            resolved_mode,
            "--device",
            effective_device,
            "--progress",
            "--resume",  # evaluation folds finished by an earlier, stopped run are reused (docs/training-resume.md)
            "--epochs",
            str(epochs),
        ]
        if resolved_mode == "thorough":
            args.append("--folds")
        if offline_augment:
            args.append("--offline-augment")
        if gestures is not None:
            from src.action_bindings import get_action_registry
            get_action_registry()
            if not gestures or any(key not in BY_KEY or BY_KEY[key].label == "none" for key in gestures):
                raise ValueError("학습할 동작을 선택하세요. 중립은 오답 자료로 함께 사용됩니다.")
            args.extend(["--gestures", *gestures])
        from src.fold_cache import clear_pause
        clear_pause()
        self._write_job_marker({"epochs": epochs, "gestures": list(gestures) if gestures is not None else None,
                                "mode": resolved_mode, "offline_augment": bool(offline_augment),
                                "device": effective_device, "folds": 0, "started": time.time()})
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(args, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace", env=env, creationflags=flags)
        dev_badge = "GPU" if effective_device == "gpu" else "CPU"
        try:
            self._before_report = model_report()
        except Exception:
            self._before_report = None
        self._target_keys = list(gestures) if gestures else None
        with self._lock:
            self.state = {"running": True, "stage": f"데이터 읽는 중 ({dev_badge})", "mode": resolved_mode,
                          "device": effective_device,
                          "fold": 0, "folds": 0, "epoch": 0, "epochs": epochs,
                          "progress": 0.0, "started": time.time(), "log": []}
        threading.Thread(target=self._read, daemon=True).start()
        return True

    # -- pause / resume (docs/training-resume.md) -------------------------------------------------------------------
    # logs/fold_cache/last_job.json says "a training was started and has not finished". It is written when a run starts and
    # removed when it ends normally or the user throws it away, so after a stop, a pause, a crash or a forced shutdown the
    # app still finds it, offers to go on, and starts again with the same options (finished evaluation folds are reused).
    @staticmethod
    def _marker_path() -> Path:
        from src.fold_cache import CACHE_ROOT
        return CACHE_ROOT / "last_job.json"

    def _write_job_marker(self, info: Dict[str, Any]) -> None:
        try:
            path = self._marker_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

    def _read_job_marker(self) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(self._marker_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _drop_job_marker(self) -> None:
        try:
            self._marker_path().unlink()
        except OSError:
            pass

    def interrupted(self) -> Optional[Dict[str, Any]]:
        """What an unfinished earlier training looks like, or None. Never while a training is running."""
        if self.running:
            return None
        info = self._read_job_marker()
        if not info:
            return None
        from src.fold_cache import saved_fold_count
        return {"done": saved_fold_count(), "total": int(info.get("folds") or 0),
                "paused": bool(self.state.get("paused")), "started": info.get("started")}

    def resume(self) -> bool:
        info = self._read_job_marker()
        if not info or self.running:
            return False
        kwargs = {"epochs": int(info.get("epochs") or 25), "gestures": info.get("gestures"), "mode": info.get("mode"),
                  "offline_augment": info.get("offline_augment"), "device": info.get("device")}
        return self.start(**kwargs)

    def discard_interrupted(self) -> None:
        """Throw the unfinished training away: it will start from the first fold next time."""
        if self.running:
            return
        self._drop_job_marker()
        try:
            from src.fold_cache import CACHE_ROOT
            for child in CACHE_ROOT.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
        except OSError:
            pass
        with self._lock:
            self.state.pop("paused", None)

    def pause(self) -> None:
        """Stop after the evaluation fold that is running now; nothing finished is lost."""
        if self.proc is not None and self.running and not self.state.get("pausing"):
            from src.fold_cache import request_pause
            request_pause()
            with self._lock:
                self.state["pausing"] = True
                self.state["stage"] = "일시중지 요청됨 · 지금 판이 끝나면 멈춰요"

    def record_quality_result(self, report_dict: Dict[str, Any], message: str) -> None:
        with self._lock:
            self.state["last_quality"] = report_dict
            log = list(self.state.get("log", []))
            log.append(message)
            self.state["log"] = log[-20:]
            if report_dict.get("status") == "fail":
                self.state["quality_warning"] = message
            else:
                self.state.pop("quality_warning", None)

    def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                with self._lock:
                    self.state["log"] = (self.state.get("log", []) + [line])[-20:]
                continue
            self._apply(ev)
        code = self.proc.wait()
        from src.fold_cache import PAUSE_EXIT_CODE
        if code == 0 and not self.state.get("error"):
            self._drop_job_marker()  # finished normally: nothing left to go on with
        improvement = None
        if code == 0 and not self.state.get("error"):
            with self._lock:
                self.state["stage"] = "학습 전후 모델 인식률 비교 분석 중…"
            try:
                after_rep = model_report()
                improvement = evaluate_training_improvement(
                    after_report=after_rep,
                    before_report=getattr(self, "_before_report", None),
                    target_keys=getattr(self, "_target_keys", None),
                )
            except Exception:
                improvement = None
        with self._lock:
            self.state["running"] = False
            self.state.pop("pausing", None)
            if code == PAUSE_EXIT_CODE:
                self.state["paused"] = True
                self.state["stage"] = "일시중지됨 · [이어서 학습]으로 계속할 수 있어요"
            elif code != 0 and not self.state.get("error"):
                self.state["error"] = "학습이 실패했어요: " + " / ".join(self.state.get("log", [])[-3:])
            if improvement and improvement.get("available"):
                self.state["improvement"] = improvement
                if improvement.get("headline"):
                    self.state["stage"] = f"완료! {improvement['headline']}"
            self.state["finished"] = time.time()

    def _apply(self, ev: Dict[str, Any]) -> None:
        with self._lock:
            s = self.state
            kind = ev.get("event")
            if kind == "gesture_start":
                s["gesture_index"], s["gesture_count"] = ev["index"], ev["count"]
                s["target"] = ev["target"]
                s["stage"] = "선택 동작 학습 · " + NAMES.get(ev["target"], ev["target"])
            elif kind == "loaded":
                s["folds"] = max(0, int(ev.get("folds", 0)))
                marker = self._read_job_marker()
                if marker is not None:
                    marker["folds"] = s["folds"]
                    self._write_job_marker(marker)
                s["windows"] = ev.get("windows")
                if ev.get("device"):
                    s["device"] = "gpu" if str(ev.get("device")) == "cuda" else str(ev.get("device"))
                if ev.get("device_name"):
                    s["device_name"] = str(ev.get("device_name"))
                dev_tag = "GPU" if s.get("device") in ("gpu", "cuda") else "CPU"
                s["stage"] = f"데이터 {ev.get('windows')}개 구간 ({dev_tag})"
            elif kind == "fold_start":
                s["fold"] = int(ev.get("fold", 0))
                s["stage"] = f"평가 학습 {s['fold']}/{s['folds']} (자세 {ev.get('posture')} 빼고)"
                s["epoch"], s["losses"] = 0, []
            elif kind == "final_start":
                s["fold"] = s["folds"] + 1
                s["stage"] = "최종 모델 학습"
                s["epoch"], s["losses"] = 0, []
            elif kind == "epoch":
                s["epoch"], s["loss"] = int(ev["epoch"]), float(ev["loss"])
                s.setdefault("losses", []).append(float(ev["loss"]))
            elif kind == "fold_result":
                s.setdefault("results", []).append(ev)
            elif kind == "done":
                s["stage"] = "끝! 새 모델 저장됨"
                s["per_gesture"] = ev.get("per_gesture")
            elif kind == "skipped":
                s.setdefault("skipped", []).append(ev.get("file"))
            elif kind == "note":
                s["log"] = (s.get("log", []) + [ev.get("text", "")])[-20:]
            elif kind == "substage":
                msg = ev.get("text") or ev.get("stage")
                if msg:
                    s["stage"] = str(msg)
                if "progress" in ev and ev["progress"] is not None:
                    p = round(max(0.0, min(0.99, float(ev["progress"]))), 3)
                    if s.get("gesture_count"):
                        p = round((s.get("gesture_index", 1) - 1 + p) / s["gesture_count"], 3)
                    s["progress"] = p
                elif float(s.get("progress", 0.0)) <= 0.02:
                    s["progress"] = 0.04
                return
            elif kind == "error":
                s["error"] = ev.get("text")
            steps = max(1, int(s.get("folds", 0)) + 1)
            cur_fold = max(0, int(s.get("fold", 0)))
            cur_epoch = max(0, int(s.get("epoch", 0)))
            total_epochs = max(1, int(s.get("epochs", 1)))
            done = 0.0 if cur_fold == 0 else ((cur_fold - 1) + min(1.0, cur_epoch / total_epochs)) / steps
            s["progress"] = 1.0 if kind == "done" else round(max(0.0, min(0.99, done)), 3)
            if kind != "done" and s.get("gesture_count"):
                s["progress"] = round((s["gesture_index"] - 1 + s["progress"]) / s["gesture_count"], 3)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.state)

    def stop(self) -> None:
        if self.proc is not None and self.running:
            with self._lock:
                self.state["error"] = "학습 중단을 요청했습니다. 게시 전 모델은 적용되지 않습니다."
            terminate_training_process(self.proc)


# ---------------------------------------------------------------- export for other apps
EXAMPLE = '''"""
Use the exported gesture model in another program (only numpy and onnxruntime needed).

For every camera frame, give it MediaPipe Hand Landmarker's results for one hand:
  world  = result.hand_world_landmarks[i]  -> 21 (x, y, z) in metres
  image  = result.hand_landmarks[i]        -> 21 (x, y, z) normalised to the frame
and the frame aspect (height / width). It keeps the last {window} frames and returns the class probabilities.
"""
import json
from collections import deque

import numpy as np
from tcn_features import FEATURE_VERSION, frame_features, windows
from gesture_models import load_single, compose_predictor


class GestureModel:
    def __init__(self, folder="."):
        from pathlib import Path
        path = Path(folder) / "gesture_tcn.onnx"
        base, info = load_single(path, FEATURE_VERSION)
        self.predict, self.info = compose_predictor(path, base, info)
        self.frames = deque(maxlen=self.info["window"] + 1)

    def step(self, t, world, image, aspect):
        """t: seconds; world / image: 21 x (x, y, z) or None when no hand. Returns {{class: probability}}."""
        self.frames.append({{"t": t, "world": world, "lm": image}} if world else {{"t": t}})
        x = windows(frame_features(list(self.frames), aspect), self.info["window"])[-1:]
        p = self.predict(x)
        return dict(zip(self.info["classes"], p.tolist()))

# A gesture counts when its probability stays >= info["threshold"] for info["frames_in_a_row"] frames in a row.
'''


def export_package(out_root: Path = EXPORT_DIR, with_dataset: bool = True, *, progress_cb=None) -> Path:
    """exports/gesture-tcn-<time>/ (+ .zip): model, its info, the feature code, an example, a README, and the
    per-frame features + labels of every recording (dataset.npz) for training elsewhere."""
    if callable(progress_cb):
        try:
            progress_cb(0.15, "[1/4단계] 현재 모델 및 메타데이터 확인 중…")
        except Exception:
            pass
    report = model_report()
    if report is None:
        raise FileNotFoundError("학습한 모델이 없어요. 먼저 학습하세요.")
    from uuid import uuid4
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    out = Path(out_root) / f"gesture-tcn-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    if callable(progress_cb):
        try:
            progress_cb(0.35, "[2/4단계] ONNX 모델 및 추론 코드 복사 중…")
        except Exception:
            pass
    shutil.copy2(MODEL, out / "gesture_tcn.onnx")
    shutil.copy2(MODEL.with_suffix(".json"), out / "gesture_tcn.json")
    shutil.copy2(ROOT / "src" / "tcn_features.py", out / "tcn_features.py")
    shutil.copy2(ROOT / "src" / "gesture_models.py", out / "gesture_models.py")
    for extra_src in ("spatial_features.py", "spatial_mlp.py"):
        extra_path = ROOT / "src" / extra_src
        if extra_path.is_file():
            shutil.copy2(extra_path, out / extra_src)
    spatial_json = MODEL.parent / "spatial_mlp.json"
    if spatial_json.is_file():
        shutil.copy2(spatial_json, out / spatial_json.name)
    from src.gesture_models import SCHEMA, read_bank
    entries = read_bank(MODEL.parent / "gestures")
    if entries:
        target = out / "gestures"
        target.mkdir()
        for entry in entries.values():
            for key in ("model", "card"):
                shutil.copy2(MODEL.parent / "gestures" / entry[key], target / entry[key])
        (target / "manifest.json").write_text(
            json.dumps({"schema": SCHEMA, "models": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "example_use.py").write_text(EXAMPLE.format(window=report["window"]), encoding="utf-8")
    pg = report.get("per_gesture", {})
    lines = [f"# 손짓 TCN 모델 ({report.get('trained')})", "",
             f"- 정답 종류: {', '.join(report['classes'])}", f"- 입력: 최근 {report['window']}프레임, 특징 {report['features']}개 ({report['feature_version']})",
             f"- 판정: 확률 {report['threshold']} 이상이 {report['frames_in_a_row']}프레임 연속", "",
             "## 데이터 출처"] + [f"- {d['file']} · {d.get('pc')} · {(d.get('camera') or {}).get('note', '')} · {d['frames']}프레임" for d in report.get("data", [])] + [
             "", "## 동작별 성적 (학습에 안 쓴 자세)"]
    for key, g in pg.items():
        if "rate" in g:
            pct = lambda v: "-" if v is None else f"{v * 100:.0f}%"  # noqa: E731
            lines.append(f"- {key}: 인식 {g['recognised']}/{g['total']} = {pct(g.get('rate'))} (규칙만 {pct(g.get('rules_rate'))})"
                         f" · 학습 데이터 정확도 {pct(g.get('train_accuracy'))}")
        else:
            lines.append(f"- {key}: 오작동 {g.get('misfires_per_min')}회/분 (규칙 {g.get('rules_misfires_per_min')}회/분)")
    lines += ["", "## 쓰는 법", "`example_use.py` 참고. 필요한 것: numpy, onnxruntime, MediaPipe Hand Landmarker(world landmarks 포함)."]
    if entries:
        lines += ["", "## 선택 학습 모델", "선택 모델은 해당 동작에 우선 적용되며 각 확률은 독립적입니다.",
                  ", ".join(entries)]
    if with_dataset:
        if callable(progress_cb):
            try:
                progress_cb(0.65, "[3/4단계] 전체 녹화 데이터셋(dataset.npz) 압축 묶는 중…")
            except Exception:
                pass
        lines += ["", "## dataset.npz", "녹화 전체의 프레임별 특징 `x` (n, features), 정답 `y` (-1 = 학습 제외, 그 외 classes 순서), "
                  "자세 `posture`, 녹화 번호 `recording`. 다른 프레임워크에서 다시 학습할 때 쓴다."]
        _export_dataset(out / "dataset.npz", report)
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if callable(progress_cb):
        try:
            progress_cb(0.88, "[4/4단계] 배포 패키지 ZIP 아카이브 생성 중…")
        except Exception:
            pass
    shutil.make_archive(str(out), "zip", root_dir=out)
    return out


def _export_dataset(path: Path, report: Dict[str, Any]) -> None:
    import numpy as np

    from src.recorder import finished_recordings
    from src.capture_dataset import load_sessions

    classes = CLASSES
    xs, ys, ps, fs, tasks, segments = [], [], [], [], [], []
    sessions = load_sessions(finished_recordings(REC_DIR)[0], include_windows=False)
    if not sessions:
        raise ValueError("내보낼 완성된 녹화가 없습니다.")
    for i, session in enumerate(sessions):
        xs.append(session["features"])
        ys.append(session["y"])
        ps.append(session["cond"])
        fs.append(np.full(len(session["frames"]), i, dtype=np.int32))
        tasks.extend((f.get("label") or {}).get("task", "") for f in session["frames"])
        segments.append(session["segments"])
    np.savez_compressed(path, x=np.concatenate(xs), y=np.concatenate(ys), posture=np.concatenate(ps),
                        recording=np.concatenate(fs), classes=np.array(classes),
                        condition_keys=np.array(sessions[0]["condition_groups"]),
                        task=np.array(tasks), segment=np.concatenate(segments), model_classes=np.array(report["classes"]))


# ---------------------------------------------------------------- post-recording pipeline
def count_recorded_frames(path: Path | str) -> int:
    """Frames in a recording file (every line with a time; the first line is the meta)."""
    n = 0
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    if "t" in json.loads(line):
                        n += 1
                except ValueError:
                    continue
    except OSError:
        return 0
    return n


def usable_share(report, cleaned_path: Optional[Path | str]) -> Optional[Dict[str, Any]]:
    """How much of a recording can still be trained on: the frames of the cleaned copy (the training reads that one), or the
    frames with a hand when there is no cleaned copy. None when the report has no frame count."""
    total = int((getattr(report, "metrics", None) or {}).get("total_frames") or 0)
    if total <= 0:
        return None
    if cleaned_path is not None:
        used = count_recorded_frames(cleaned_path)
    else:
        used = int(report.metrics.get("valid_frames") or 0)
    return {"used": used, "total": total, "ratio": min(1.0, used / total), "after_cleaning": cleaned_path is not None}


def run_post_recording_pipeline(
    path: Path | str,
    config: Optional[Dict[str, Any]] = None,
    *,
    training_job: Optional[TrainingJob] = None,
    epochs: int = 25,
) -> Dict[str, Any]:
    """Execute the post-recording pipeline on a finished recording file:

    1. Quality check (`check_recording`) if `auto_quality_check` is enabled.
    2. If status is `fail`: record warning/log for the training center and stop immediately.
    3. If status is `warning` or `pass` and `auto_clean` is enabled: run `clean_recording`.
    4. If `auto_offline_augment` is enabled: run `augment_recording_file`.
    5. If `auto_retrain_after_recording` is enabled and quality >= `min_quality_score`: trigger retraining.
    """
    from src.data_quality import QualityReport, check_recording, clean_recording
    from src.offline_augment import augment_recording_file

    cfg = config or {}
    rec_path = Path(path)

    auto_quality_check = bool(cfg.get("auto_quality_check", True))
    auto_clean = bool(cfg.get("auto_clean", True))
    auto_offline_augment = bool(cfg.get("auto_offline_augment", True))
    auto_retrain = bool(cfg.get("auto_retrain_after_recording", False))
    min_quality_score = float(cfg.get("min_quality_score", 0.7))
    aug_factor = int(cfg.get("offline_augment_factor", 4))
    aug_rot = float(cfg.get("offline_augment_rotation_deg", 40.0))
    aug_scale = float(cfg.get("offline_augment_scale", 0.15))

    report: Optional[QualityReport] = None
    if auto_quality_check or auto_clean or auto_retrain:
        report = check_recording(rec_path, min_quality_score=min_quality_score)
        try:
            q_cache = rec_path.with_name(rec_path.name + ".quality.json")
            q_cache.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    if auto_quality_check and report is not None and report.status == "fail":
        try:
            set_recording_selected(rec_path.name, False, rec_path.parent)
        except Exception:
            pass
        msg = f"품질 미달로 학습 제외 ({report.score:.2f}): {report.summary}"
        if training_job is not None:
            training_job.record_quality_result(report.to_dict(), msg)
        return {
            "path": str(rec_path),
            "status": "fail",
            "score": report.score,
            "report": report,
            "cleaned_path": None,
            "augmented_paths": [],
            "retrain_eligible": False,
            "retrain_started": False,
            "message": msg,
        }

    cleaned_path: Optional[Path] = None
    if auto_clean:
        try:
            cleaned_path = clean_recording(rec_path, report)
        except Exception as exc:
            cleaned_path = None
            if report is not None:
                report.summary += f" (정제 실패: {exc})"

    # A warning recording is kept (its short broken stretches are cleaned away), but one that has too little left to train on
    # is excluded from training: auto_exclude_usable_ratio (default 0.5 = less than half usable).
    usable = usable_share(report, cleaned_path) if report is not None else None
    min_usable = float(cfg.get("auto_exclude_usable_ratio", 0.5))
    too_little = bool(usable is not None and usable["ratio"] < min_usable)
    if too_little:
        try:
            set_recording_selected(rec_path.name, False, rec_path.parent)
        except Exception:
            pass

    augmented_paths: List[Path] = []
    if auto_offline_augment and not too_little:
        src_for_aug = cleaned_path if cleaned_path is not None else rec_path
        try:
            augmented_paths = augment_recording_file(
                src_for_aug,
                factor=aug_factor,
                rotation_deg=aug_rot,
                scale_range=aug_scale,
            )
        except Exception:
            augmented_paths = []

    quality_sufficient = (
        report is None
        or (report.status != "fail" and report.score >= min_quality_score)
    )
    retrain_eligible = bool(auto_retrain and quality_sufficient and not too_little)
    retrain_started = False
    if retrain_eligible and training_job is not None and not training_job.running:
        retrain_started = training_job.start(epochs=epochs, offline_augment=auto_offline_augment)

    parts = []
    if usable is not None:
        label = "정제 후" if usable["after_cleaning"] else "손이 잡힌 프레임"
        parts.append(f"{label} {usable['ratio']:.0%} 사용 ({usable['used']:,}/{usable['total']:,}프레임)")
        if too_little:
            parts.append(f"{min_usable:.0%} 미만이라 학습에서 제외")
    if report is not None:
        parts.append(report.summary)
    if cleaned_path is not None:
        parts.append("자동 정제 완료")
    if augmented_paths:
        parts.append(f"오프라인 증강 {len(augmented_paths)}배 생성")
    if retrain_started:
        parts.append("자동 재학습 시작")
    elif auto_retrain and not quality_sufficient and report is not None:
        parts.append(f"품질 점수 부족({report.score:.2f}<{min_quality_score:.2f})으로 자동 재학습 보류")

    msg = " · ".join(parts) if parts else "녹화 후처리 완료"
    if training_job is not None and report is not None:
        training_job.record_quality_result(report.to_dict(), msg)

    return {
        "path": str(rec_path),
        "status": report.status if report is not None else "pass",
        "score": report.score if report is not None else 1.0,
        "report": report,
        "cleaned_path": str(cleaned_path) if cleaned_path is not None else None,
        "augmented_paths": [str(p) for p in augmented_paths],
        "retrain_eligible": retrain_eligible,
        "retrain_started": retrain_started,
        "usable_ratio": usable["ratio"] if usable is not None else None,
        "excluded_too_little": too_little,
        "message": msg,
    }


# ---------------------------------------------------------------- model backup, rollback & per-gesture reset
def backup_current_model(
    model: Path = MODEL,
    backup_dir: Path = BACKUP_DIR,
    reason: str = "before-train",
    max_backups: int = 12,
) -> Optional[Path]:
    """Snapshot the current model + specialist bank before overwriting or resetting."""
    card = model.with_suffix(".json")
    bank_dir = model.parent / "gestures"
    has_main = model.is_file() and card.is_file()
    has_bank = (bank_dir / "manifest.json").is_file()
    if not has_main and not has_bank:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = Path(backup_dir) / f"backup-{stamp}-{reason}"
    suffix = 1
    while folder.exists():
        suffix += 1
        folder = Path(backup_dir) / f"backup-{stamp}-{reason}-{suffix}"
    folder.mkdir(parents=True, exist_ok=True)
    if has_main:
        shutil.copy2(model, folder / model.name)
        shutil.copy2(card, folder / card.name)
    spatial_file = model.parent / "spatial_mlp.json"
    if spatial_file.is_file():
        shutil.copy2(spatial_file, folder / spatial_file.name)
    if has_bank:
        dst_bank = folder / "gestures"
        dst_bank.mkdir(parents=True, exist_ok=True)
        for item in bank_dir.glob("*"):
            if item.is_file() and item.name != "training.lock":
                shutil.copy2(item, dst_bank / item.name)
    meta = {
        "id": folder.name,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": time.time(),
        "reason": reason,
    }
    (folder / "backup_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    all_backups = sorted(
        [d for d in Path(backup_dir).glob("backup-*") if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for old in all_backups[max_backups:]:
        shutil.rmtree(old, ignore_errors=True)
    return folder


def _describe_backup_folder(folder: Path, source_kind: str) -> Optional[Dict[str, Any]]:
    card = folder / "gesture_tcn.json"
    bank_idx = folder / "gestures" / "manifest.json"
    if not card.is_file() and not bank_idx.is_file():
        return None
    trained = ""
    classes: List[str] = []
    disabled: List[str] = []
    rates: List[str] = []
    if card.is_file():
        try:
            c = json.loads(card.read_text(encoding="utf-8"))
            trained = str(c.get("trained", ""))
            classes = [str(x) for x in c.get("classes", []) if x != "none"]
            disabled = [str(x) for x in c.get("disabled_gestures", [])]
            pg = c.get("per_gesture", {})
            for cls in classes:
                if cls in disabled:
                    continue
                r = (pg.get(cls) or {}).get("rate")
                if r is not None:
                    rates.append(f"{cls}:{r * 100:.0f}%")
        except Exception:
            pass
    specialists: List[str] = []
    if bank_idx.is_file():
        try:
            idx = json.loads(bank_idx.read_text(encoding="utf-8"))
            specialists = list((idx.get("models") or {}).keys())
        except Exception:
            pass
    meta_file = folder / "backup_meta.json"
    reason = "내보내기 패키지" if source_kind == "export" else "자동 백업"
    created = trained or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(folder.stat().st_mtime))
    if meta_file.is_file():
        try:
            m = json.loads(meta_file.read_text(encoding="utf-8"))
            created = str(m.get("created_at") or created)
            r_code = str(m.get("reason", ""))
            reason_map = {
                "before-train": "학습 직전 자동 백업",
                "before-selected-train": "선택 학습 직전 백업",
                "before-reset": "동작 초기화 직전 백업",
                "manual": "수동 백업",
            }
            reason = reason_map.get(r_code, r_code or reason)
        except Exception:
            pass
    active_gestures = sorted(set([c for c in classes if c not in disabled] + specialists))
    summary_parts = [reason, f"동작 {len(active_gestures)}개"]
    if rates:
        summary_parts.append(" ".join(rates[:3]))
    return {
        "id": f"{source_kind}:{folder.name}",
        "name": folder.name,
        "source": source_kind,
        "path": str(folder),
        "created": created,
        "reason": reason,
        "gestures": active_gestures,
        "summary": f"{created} · {' · '.join(summary_parts)}",
        "mtime": folder.stat().st_mtime,
    }


def list_model_backups(backup_dir: Path = BACKUP_DIR, export_dir: Path = EXPORT_DIR) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if Path(backup_dir).is_dir():
        for d in Path(backup_dir).glob("backup-*"):
            if d.is_dir():
                info = _describe_backup_folder(d, "backup")
                if info is not None:
                    items.append(info)
    if Path(export_dir).is_dir():
        for d in Path(export_dir).glob("gesture-tcn-*"):
            if d.is_dir():
                info = _describe_backup_folder(d, "export")
                if info is not None:
                    items.append(info)
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def restore_model_backup(
    backup_id: str,
    model: Path = MODEL,
    backup_dir: Path = BACKUP_DIR,
    export_dir: Path = EXPORT_DIR,
) -> Dict[str, Any]:
    """Restore `models/gesture_tcn.onnx` (+ `.json`) and `models/gestures/` from a backup or export folder."""
    if ":" in backup_id:
        kind, folder_name = backup_id.split(":", 1)
    else:
        kind, folder_name = "backup", backup_id
    src_root = Path(export_dir) if kind == "export" else Path(backup_dir)
    src_folder = src_root / Path(folder_name).name
    if not src_folder.is_dir():
        raise FileNotFoundError(f"백업을 찾을 수 없습니다: {backup_id}")

    src_model = src_folder / "gesture_tcn.onnx"
    src_card = src_folder / "gesture_tcn.json"
    src_bank = src_folder / "gestures"
    if not (src_model.is_file() and src_card.is_file()) and not (src_bank / "manifest.json").is_file():
        raise FileNotFoundError(f"백업 폴더에 복원 가능한 모델 파일이 없습니다: {src_folder.name}")

    model.parent.mkdir(parents=True, exist_ok=True)
    card = model.with_suffix(".json")
    if src_model.is_file() and src_card.is_file():
        shutil.copy2(src_model, model)
        shutil.copy2(src_card, card)
    src_spatial = src_folder / "spatial_mlp.json"
    if src_spatial.is_file():
        shutil.copy2(src_spatial, model.parent / "spatial_mlp.json")

    bank_dir = model.parent / "gestures"
    if bank_dir.is_dir():
        for f in bank_dir.glob("*"):
            if f.is_file() and f.name != "training.lock":
                try:
                    f.unlink()
                except OSError:
                    pass
    if (src_bank / "manifest.json").is_file():
        bank_dir.mkdir(parents=True, exist_ok=True)
        for f in src_bank.glob("*"):
            if f.is_file() and f.name != "training.lock":
                shutil.copy2(f, bank_dir / f.name)

    try:
        (model.parent / "last_training_improvement.json").unlink(missing_ok=True)
    except OSError:
        pass

    return {"restored": src_folder.name, "path": str(src_folder)}


def reset_gesture_training(
    key: str,
    model: Path = MODEL,
    backup_dir: Path = BACKUP_DIR,
) -> Dict[str, Any]:
    """Reset the trained state of a single gesture so it falls back to rule-based detection immediately."""
    from src.gesture_models import remove_from_bank, training_lock
    if key not in BY_KEY:
        raise ValueError(f"알 수 없는 동작입니다: {key}")
    task = BY_KEY[key]
    label = task.label
    if label == "none":
        raise ValueError("이동·중립 동작은 개별 초기화 대상이 아닙니다.")

    backup_current_model(model=model, backup_dir=backup_dir, reason="before-reset")
    bank_dir = model.parent / "gestures"
    removed_specialist = False
    if (bank_dir / "manifest.json").is_file():
        with training_lock(bank_dir):
            removed_specialist = remove_from_bank(bank_dir, label)

    disabled_in_main = False
    card_path = model.with_suffix(".json")
    if card_path.is_file():
        try:
            card = json.loads(card_path.read_text(encoding="utf-8"))
            classes = card.get("classes", [])
            if label in classes:
                disabled = list(dict.fromkeys([*(card.get("disabled_gestures") or []), label]))
                card["disabled_gestures"] = disabled
                card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
                disabled_in_main = True
        except Exception:
            pass

    sp_path = model.parent / "spatial_mlp.json"
    if sp_path.is_file():
        try:
            from src.spatial_mlp import SpatialMlpModel
            sp_model = SpatialMlpModel.load(sp_path)
            if label in sp_model.specialists:
                del sp_model.specialists[label]
                sp_model.save(sp_path)
        except Exception:
            pass

    return {
        "key": key,
        "label": label,
        "name": task.name,
        "removed_specialist": removed_specialist,
        "disabled_in_main": disabled_in_main,
    }


# ---------------------------------------------------------------- selectable ONNX models, per-model gesture rates & 2D spatial spray heatmap
_LABEL_DISPLAY_NAMES: Dict[str, str] = {
    "thumb_middle": "좌클릭 · 드래그 (중지)",
    "thumb_ring": "우클릭 (약지)",
    "thumb_index": "스크롤 잡기 (검지)",
    "thumb_index_middle": "창 이동 3손가락 핀치",
    "v_sign": "브이(V) 사인",
    "rock_sign": "락(Rock) 사인",
    "thumbs_up": "따봉 (엄지 척)",
    "swipe_left": "← 왼쪽 스와이프",
    "swipe_right": "→ 오른쪽 스와이프",
    "swipe_down": "↓ 아래로 스와이프",
}


def get_active_onnx_selection(model: Path = MODEL) -> Dict[str, Any]:
    """Return the user's active ONNX runtime selection from `models/active_onnx_selection.json`."""
    sel_file = Path(model).parent / "active_onnx_selection.json"
    default_sel = {
        "mode": "composite",
        "id": "composite:active",
        "path": str(model),
    }
    if sel_file.is_file():
        try:
            doc = json.loads(sel_file.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and doc.get("id"):
                return {**default_sel, **doc}
        except Exception:
            pass
    return default_sel


def _build_onnx_card_gesture_rates(
    card: Dict[str, Any],
    *,
    specialists: Optional[Dict[str, Dict[str, Any]]] = None,
    use_specialists: bool = True,
) -> Tuple[List[Dict[str, Any]], Optional[float], Optional[float], Optional[float]]:
    """Build per-gesture recognition breakdown rows for a given ONNX model card."""
    spec_map = (specialists or {}) if use_specialists else {}
    disabled = set(card.get("disabled_gestures") or ()) if not use_specialists else set(card.get("disabled_gestures") or ()) - set(spec_map.keys())
    card_classes = [str(c) for c in (card.get("classes") or []) if c != "none"]
    active_labels: List[str] = []
    for c in CLASSES:
        if c == "none":
            continue
        if (c in spec_map) or (c in card_classes and c not in disabled):
            active_labels.append(c)

    rows: List[Dict[str, Any]] = []
    rate_vals: List[float] = []
    fit_vals: List[float] = []

    for lbl in active_labels:
        spec_card = spec_map.get(lbl)
        src_card = spec_card if spec_card is not None else card
        pg = (src_card.get("per_gesture") or {}).get(lbl) or {}
        rate = pg.get("rate")
        rules_rate = pg.get("rules_rate")
        train_acc = pg.get("train_accuracy")
        rec_cnt = int(pg.get("recognised") or 0)
        tot_cnt = int(pg.get("total") or 0)

        sp_acc = None
        if spec_card and isinstance(spec_card.get("spatial_mlp"), dict):
            sp_acc = (spec_card["spatial_mlp"].get("metadata") or {}).get("test_accuracy")
        if sp_acc is None:
            sp_doc = src_card.get("spatial_mlp_live") or src_card.get("spatial_mlp") or {}
            if isinstance(sp_doc, dict):
                sp_acc = sp_doc.get("test_accuracy")

        rate_pct = round(float(rate) * 100.0, 1) if isinstance(rate, (int, float)) else None
        rules_pct = round(float(rules_rate) * 100.0, 1) if isinstance(rules_rate, (int, float)) else None
        fit_pct = round(float(train_acc) * 100.0, 1) if isinstance(train_acc, (int, float)) else None
        sp_pct = round(float(sp_acc), 1) if isinstance(sp_acc, (int, float)) else None

        if rate_pct is not None:
            rate_vals.append(rate_pct)
        if fit_pct is not None:
            fit_vals.append(fit_pct)

        primary_pct = rate_pct if rate_pct is not None else (fit_pct if fit_pct is not None else (sp_pct or 0.0))
        grade = "high" if primary_pct >= 85.0 else ("mid" if primary_pct >= 65.0 else "low")
        source_badge = "전용 ONNX" if (spec_card is not None or len(card_classes) == 1) else "통합 TCN"

        badge_parts: List[str] = []
        if rate_pct is not None:
            badge_parts.append(f"실전 인식 {rate_pct:.0f}% ({rec_cnt}/{tot_cnt}회)")
        if fit_pct is not None:
            badge_parts.append(f"학습일치 {fit_pct:.0f}%")
        if rules_pct is not None and rate_pct is not None:
            diff = rate_pct - rules_pct
            sign = "+" if diff >= 0 else ""
            badge_parts.append(f"규칙 대비 {sign}{diff:.0f}%p")
        if sp_pct is not None:
            badge_parts.append(f"MLP {sp_pct:.0f}%")

        # Find canonical task keys for training this gesture
        task_keys = [k for k, t in BY_KEY.items() if t.label == lbl]
        primary_key = lbl if lbl in BY_KEY else (task_keys[0] if task_keys else lbl)
        disp_name = _LABEL_DISPLAY_NAMES.get(lbl, NAMES.get(primary_key, lbl))

        rows.append({
            "label": lbl,
            "key": primary_key,
            "task_keys": task_keys,
            "name": disp_name,
            "source": source_badge,
            "rate": round(float(primary_pct) / 100.0, 4),
            "rate_pct": rate_pct,
            "rules_rate_pct": rules_pct,
            "train_acc_pct": fit_pct,
            "spatial_acc_pct": sp_pct,
            "score_pct": round(float(primary_pct), 1),
            "hits": rec_cnt,
            "reps": tot_cnt,
            "recognised": rec_cnt,
            "total": tot_cnt,
            "grade": grade,
            "detail": " · ".join(badge_parts) if badge_parts else "학습됨",
        })

    # Extract 'none' misfires_per_min
    none_pg = ((card.get("per_gesture") or {}).get("none")) or {}
    mpm = none_pg.get("misfires_per_min")
    if mpm is None and spec_map:
        mpm_vals = [
            float(((sc.get("per_gesture") or {}).get("none") or {}).get("misfires_per_min"))
            for sc in spec_map.values()
            if isinstance(((sc.get("per_gesture") or {}).get("none") or {}).get("misfires_per_min"), (int, float))
        ]
        if mpm_vals:
            mpm = round(sum(mpm_vals), 2)

    avg_rate = round(sum(rate_vals) / len(rate_vals), 1) if rate_vals else None
    avg_fit = round(sum(fit_vals) / len(fit_vals), 1) if fit_vals else None
    mpm_out = round(float(mpm), 2) if isinstance(mpm, (int, float)) else None
    return rows, avg_rate, avg_fit, mpm_out


def list_available_onnx_models(
    model: Path = MODEL,
    backup_dir: Path = BACKUP_DIR,
    export_dir: Path = EXPORT_DIR,
) -> List[Dict[str, Any]]:
    """List all selectable ONNX models (Composite Hybrid, Main TCN, each Specialist ONNX in `models/gestures/`, Backups, Exports)."""
    from src.gesture_models import read_bank

    model = Path(model)
    card_path = model.with_suffix(".json")
    bank_dir = model.parent / "gestures"
    active_sel = get_active_onnx_selection(model)
    active_id = str(active_sel.get("id") or "composite:active")

    has_main = model.is_file() and card_path.is_file()
    main_card: Dict[str, Any] = {}
    if has_main:
        try:
            main_card = json.loads(card_path.read_text(encoding="utf-8"))
        except Exception:
            main_card = {}

    sp_path = model.parent / "spatial_mlp.json"
    if sp_path.is_file():
        try:
            sp_doc = json.loads(sp_path.read_text(encoding="utf-8"))
            main_card["spatial_mlp_live"] = dict(sp_doc.get("metadata") or {})
        except Exception:
            pass

    bank_entries = read_bank(bank_dir) if (bank_dir / "manifest.json").is_file() else {}
    spec_cards: Dict[str, Dict[str, Any]] = {}
    for lbl, entry in bank_entries.items():
        c_file = bank_dir / entry.get("card", "")
        if c_file.is_file():
            try:
                spec_cards[lbl] = json.loads(c_file.read_text(encoding="utf-8"))
            except Exception:
                pass

    items: List[Dict[str, Any]] = []

    # 1. Composite Hybrid (Main TCN + Specialist ONNX Bank + Spatial MLP)
    if has_main or spec_cards:
        g_rows, avg_rate, avg_fit, mpm = _build_onnx_card_gesture_rates(
            main_card, specialists=spec_cards, use_specialists=True
        )
        data_files: List[str] = []
        for d in main_card.get("data") or []:
            if isinstance(d, dict) and d.get("file") and d["file"] not in data_files:
                data_files.append(str(d["file"]))
        for sc in spec_cards.values():
            for d in sc.get("data") or []:
                if isinstance(d, dict) and d.get("file") and d["file"] not in data_files:
                    data_files.append(str(d["file"]))

        g_names = [r["name"] for r in g_rows]
        t_keys: List[str] = []
        for r in g_rows:
            for tk in r.get("task_keys") or [r["key"]]:
                if tk in BY_KEY and BY_KEY[tk].label != "none" and tk not in t_keys:
                    t_keys.append(tk)

        rate_badge = f"평균 인식 {avg_rate:.0f}%" if avg_rate is not None else (f"평균 학습일치 {avg_fit:.0f}%" if avg_fit is not None else "평가 대기")
        mpm_badge = f" · 이동오작동 {mpm:.1f}회/분" if mpm is not None else ""
        items.append({
            "id": "composite:active",
            "title": f"[통합 하이브리드]gesture_tcn.onnx + 전용 ONNX {len(spec_cards)}종 ({len(g_rows)}개 동작 · {rate_badge})",
            "short_title": f"통합 하이브리드 ({len(g_rows)}종 동작)",
            "kind": "composite",
            "onnx_path": str(model),
            "active": active_id == "composite:active",
            "trained_at": str(main_card.get("trained") or ""),
            "classes": [r["label"] for r in g_rows],
            "gesture_names": g_names,
            "trainable_keys": t_keys,
            "avg_rate_pct": avg_rate,
            "avg_fit_pct": avg_fit,
            "misfires_per_min": mpm,
            "gesture_rates": g_rows,
            "data_files": data_files,
            "summary": f"메인 TCN과 동작별 전용 ONNX {len(spec_cards)}개를 결합한 권장 모델 · {rate_badge}{mpm_badge}",
        })

    # 2. Standalone Main TCN (`models/gesture_tcn.onnx`)
    if has_main:
        g_rows, avg_rate, avg_fit, mpm = _build_onnx_card_gesture_rates(
            main_card, specialists=None, use_specialists=False
        )
        data_files = [
            str(d["file"])
            for d in (main_card.get("data") or [])
            if isinstance(d, dict) and d.get("file")
        ]
        t_keys = []
        for r in g_rows:
            for tk in r.get("task_keys") or [r["key"]]:
                if tk in BY_KEY and BY_KEY[tk].label != "none" and tk not in t_keys:
                    t_keys.append(tk)
        rate_badge = f"평균 인식 {avg_rate:.0f}%" if avg_rate is not None else (f"평균 학습일치 {avg_fit:.0f}%" if avg_fit is not None else "평가 대기")
        items.append({
            "id": "main:gesture_tcn.onnx",
            "title": f"[메인 단독 ONNX] models/gesture_tcn.onnx ({len(g_rows)}개 동작 · {rate_badge})",
            "short_title": "gesture_tcn.onnx (메인 단독)",
            "kind": "main_only",
            "onnx_path": str(model),
            "active": active_id == "main:gesture_tcn.onnx",
            "trained_at": str(main_card.get("trained") or ""),
            "classes": [r["label"] for r in g_rows],
            "gesture_names": [r["name"] for r in g_rows],
            "trainable_keys": t_keys,
            "avg_rate_pct": avg_rate,
            "avg_fit_pct": avg_fit,
            "misfires_per_min": mpm,
            "gesture_rates": g_rows,
            "data_files": data_files,
            "summary": f"전용 뱅크 없이 메인 gesture_tcn.onnx 단독 추론 · {rate_badge} ({main_card.get('trained', '')})",
        })

    # 3. Individual Specialist ONNX files in `models/gestures/*.onnx`
    if bank_dir.is_dir():
        for onnx_file in sorted(bank_dir.glob("*.onnx")):
            c_file = onnx_file.with_suffix(".json")
            if not c_file.is_file():
                continue
            try:
                sc = json.loads(c_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            g_rows, avg_rate, avg_fit, mpm = _build_onnx_card_gesture_rates(
                sc, specialists=None, use_specialists=False
            )
            data_files = [
                str(d["file"])
                for d in (sc.get("data") or [])
                if isinstance(d, dict) and d.get("file")
            ]
            lbl = g_rows[0]["label"] if g_rows else onnx_file.stem
            disp_name = _LABEL_DISPLAY_NAMES.get(lbl, NAMES.get(lbl, lbl))
            t_keys = [k for k, t in BY_KEY.items() if t.label == lbl and t.label != "none"]
            if not t_keys and lbl in BY_KEY:
                t_keys = [lbl]
            item_id = f"specialist:{onnx_file.name}"
            rate_badge = f"인식 {avg_rate:.0f}%" if avg_rate is not None else (f"학습일치 {avg_fit:.0f}%" if avg_fit is not None else "학습됨")
            items.append({
                "id": item_id,
                "title": f"[전용 ONNX] gestures/{onnx_file.name} — {disp_name} ({rate_badge})",
                "short_title": f"{onnx_file.name} ({disp_name})",
                "kind": "specialist",
                "onnx_path": str(onnx_file),
                "active": active_id == item_id,
                "trained_at": str(sc.get("trained") or ""),
                "classes": [r["label"] for r in g_rows],
                "gesture_names": [r["name"] for r in g_rows],
                "trainable_keys": t_keys,
                "avg_rate_pct": avg_rate,
                "avg_fit_pct": avg_fit,
                "misfires_per_min": mpm,
                "gesture_rates": g_rows,
                "data_files": data_files,
                "summary": f"'{disp_name}' 전용 단일 ONNX 모델 ({onnx_file.name}) · {rate_badge} · 학습일시 {sc.get('trained', '-')}",
            })

    # 4. Backups & Exports with ONNX models
    for bak in list_model_backups(backup_dir=backup_dir, export_dir=export_dir)[:8]:
        folder = Path(bak["path"])
        bak_onnx = folder / "gesture_tcn.onnx"
        bak_rep = model_report(bak_onnx)
        if not bak_rep:
            continue
        g_rows, avg_rate, avg_fit, mpm = _build_onnx_card_gesture_rates(
            bak_rep, specialists=bak_rep.get("specialists"), use_specialists=True
        )
        data_files = [
            str(d["file"])
            for d in (bak_rep.get("data") or [])
            if isinstance(d, dict) and d.get("file")
        ]
        t_keys = []
        for r in g_rows:
            for tk in r.get("task_keys") or [r["key"]]:
                if tk in BY_KEY and BY_KEY[tk].label != "none" and tk not in t_keys:
                    t_keys.append(tk)
        kind_ko = "내보내기 패키지" if bak["source"] == "export" else "백업 스냅샷"
        rate_badge = f"평균 인식 {avg_rate:.0f}%" if avg_rate is not None else f"{len(g_rows)}개 동작"
        items.append({
            "id": bak["id"],
            "title": f"[{kind_ko}] {bak['name']} ({rate_badge} · {bak['created']})",
            "short_title": f"{bak['name']} ({kind_ko})",
            "kind": bak["source"],
            "onnx_path": str(bak_onnx),
            "active": active_id == bak["id"],
            "trained_at": str(bak["created"]),
            "classes": [r["label"] for r in g_rows],
            "gesture_names": [r["name"] for r in g_rows],
            "trainable_keys": t_keys,
            "avg_rate_pct": avg_rate,
            "avg_fit_pct": avg_fit,
            "misfires_per_min": mpm,
            "gesture_rates": g_rows,
            "data_files": data_files,
            "summary": f"{kind_ko} ({bak['reason']}) · {rate_badge} · 생성 {bak['created']}",
        })

    for it in items:
        prefix = "★ [실사용 중] " if it.get("active") else ""
        it["combo_label"] = prefix + str(it.get("title") or it.get("id"))
        avg_r = it.get("avg_rate_pct")
        avg_f = it.get("avg_fit_pct")
        ov_pct = avg_r if isinstance(avg_r, (int, float)) else (avg_f if isinstance(avg_f, (int, float)) else 0.0)
        it["overall_rate"] = round(float(ov_pct) / 100.0, 4)
        it["description"] = str(it.get("summary") or "")
        tks = list(it.get("trainable_keys") or [])
        it["trainable_key"] = tks[0] if len(tks) == 1 else "all"

    return items


def activate_onnx_model(
    model_id: str,
    model: Path = MODEL,
    backup_dir: Path = BACKUP_DIR,
    export_dir: Path = EXPORT_DIR,
) -> Dict[str, Any]:
    """Activate the user-selected ONNX model for live runtime gesture recognition & simulator inspection."""
    model = Path(model)
    sel_file = model.parent / "active_onnx_selection.json"
    sel_file.parent.mkdir(parents=True, exist_ok=True)
    target_id = str(model_id or "composite:active").strip()

    if target_id in ("", "composite:active"):
        payload = {
            "mode": "composite",
            "id": "composite:active",
            "path": str(model),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        sel_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            **payload,
            "message": "통합 하이브리드 ONNX 모델(메인 TCN + 동작별 전용 ONNX 뱅크)을 실사용 모델로 불러왔습니다.",
        }

    if target_id == "main:gesture_tcn.onnx":
        if not model.is_file():
            raise FileNotFoundError(f"메인 ONNX 파일이 없습니다: {model}")
        payload = {
            "mode": "main_only",
            "id": "main:gesture_tcn.onnx",
            "path": str(model),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        sel_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            **payload,
            "message": "메인 단독 ONNX 모델(models/gesture_tcn.onnx)을 실사용 모델로 불러왔습니다.",
        }

    if target_id.startswith("specialist:"):
        fname = Path(target_id.split(":", 1)[1]).name
        spec_path = model.parent / "gestures" / fname
        if not spec_path.is_file():
            raise FileNotFoundError(f"전용 ONNX 파일을 찾을 수 없습니다: {spec_path}")
        payload = {
            "mode": "single_onnx",
            "id": target_id,
            "path": str(spec_path),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        sel_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            **payload,
            "message": f"개별 동작 전용 ONNX 모델(gestures/{fname})을 단독 실사용 모델로 불러왔습니다.",
        }

    if target_id.startswith("backup:") or target_id.startswith("export:"):
        backup_current_model(model=model, backup_dir=backup_dir, reason="before-switch")
        res = restore_model_backup(target_id, model=model, backup_dir=backup_dir, export_dir=export_dir)
        payload = {
            "mode": "composite",
            "id": "composite:active",
            "restored_from": target_id,
            "path": str(model),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        sel_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            **payload,
            "message": f"'{res['restored']}' ONNX 모델 패키지를 현재 활성 모델로 복원 및 적용했습니다.",
        }

    raise ValueError(f"지원하지 않는 ONNX 모델 ID입니다: {target_id}")


def compute_model_spatial_spray(
    model_id: str = "",
    gesture_filter: str = "all",
    rec_dir: Path = REC_DIR,
    model: Path = MODEL,
    backup_dir: Path = BACKUP_DIR,
    export_dir: Path = EXPORT_DIR,
) -> Dict[str, Any]:
    """Compute 2D screen-space spray/heatmap distribution showing where on the screen the selected ONNX model
    was trained to recognize gestures well (density + recognition quality across a 16x10 normalized grid)."""
    import math
    from src.recorder import finished_recordings

    folder = Path(rec_dir)
    models = list_available_onnx_models(model=model, backup_dir=backup_dir, export_dir=export_dir)
    selected_entry: Optional[Dict[str, Any]] = None
    if model_id:
        for m in models:
            if m["id"] == model_id:
                selected_entry = m
                break
    if selected_entry is None and models:
        selected_entry = next((m for m in models if m.get("active")), models[0])

    trained_classes = list((selected_entry or {}).get("classes") or [])
    g_rates_list = list((selected_entry or {}).get("gesture_rates") or [])
    rate_by_label: Dict[str, float] = {}
    for gr in g_rates_list:
        lbl = str(gr.get("label") or "")
        sc = gr.get("score_pct")
        if lbl:
            rate_by_label[lbl] = max(0.25, min(1.0, float(sc) / 100.0)) if isinstance(sc, (int, float)) and sc > 0 else 0.75

    data_files = list((selected_entry or {}).get("data_files") or [])
    complete_paths, _ = finished_recordings(folder) if folder.is_dir() else ([], [])
    by_name = {p.name: p for p in complete_paths}

    if data_files:
        used_paths = [by_name[fn] for fn in data_files if fn in by_name]
        if not used_paths:
            used_paths = filter_selected_recording_paths(complete_paths, folder)
    else:
        used_paths = filter_selected_recording_paths(complete_paths, folder)

    norm_filter = str(gesture_filter or "all").strip()
    if norm_filter in BY_KEY and BY_KEY[norm_filter].label != "none":
        norm_filter = BY_KEY[norm_filter].label

    # Aggregate 16x10 grid: (gx, gy) -> {frames, weighted_quality, sx, sy, per_label_frames}
    cell_map: Dict[Tuple[int, int], Dict[str, Any]] = {}
    per_gesture_totals: Dict[str, int] = defaultdict(int)
    zone_3x3_counts: Dict[str, int] = defaultdict(int)

    for rp in used_paths:
        bins_by_lbl = _get_file_spatial_bins(rp)
        for lbl, cell_rows in bins_by_lbl.items():
            if lbl == "none":
                continue
            if trained_classes and lbl not in trained_classes:
                continue
            for row in cell_rows:
                if not isinstance(row, list) or len(row) < 5:
                    continue
                gx, gy, cnt, cx, cy = int(row[0]), int(row[1]), int(row[2]), float(row[3]), float(row[4])
                if cnt <= 0:
                    continue
                per_gesture_totals[lbl] += cnt
                if norm_filter not in ("", "all") and lbl != norm_filter:
                    continue
                c_entry = cell_map.get((gx, gy))
                if c_entry is None:
                    c_entry = {
                        "frames": 0,
                        "quality_sum": 0.0,
                        "sx": 0.0,
                        "sy": 0.0,
                        "by_label": defaultdict(int),
                    }
                    cell_map[(gx, gy)] = c_entry
                g_qual = rate_by_label.get(lbl, 0.8)
                c_entry["frames"] += cnt
                c_entry["quality_sum"] += cnt * g_qual
                c_entry["sx"] += cnt * cx
                c_entry["sy"] += cnt * cy
                c_entry["by_label"][lbl] += cnt
                zone_3x3_counts[_classify_screen_zone(cx, cy)] += cnt

    total_frames = sum(c["frames"] for c in cell_map.values())
    max_cell_frames = max((c["frames"] for c in cell_map.values()), default=0)

    cells_out: List[Dict[str, Any]] = []
    for (gx, gy), c in sorted(cell_map.items()):
        cnt = int(c["frames"])
        if cnt <= 0 or max_cell_frames <= 0:
            continue
        cx = round(c["sx"] / cnt, 4)
        cy = round(c["sy"] / cnt, 4)
        linear_ratio = cnt / max_cell_frames
        # Smooth log/sqrt blend so spray density shows both high-concentration cores and soft outer mist
        density = round(min(1.0, 0.45 * linear_ratio + 0.55 * math.sqrt(linear_ratio)), 3)
        qual = round(max(0.0, min(1.0, c["quality_sum"] / cnt)), 3)
        dom_lbl = max(c["by_label"].items(), key=lambda kv: kv[1])[0]
        dom_name = _LABEL_DISPLAY_NAMES.get(dom_lbl, NAMES.get(dom_lbl, dom_lbl))
        cells_out.append({
            "gx": gx,
            "gy": gy,
            "x": cx,
            "y": cy,
            "cx": cx,
            "cy": cy,
            "frames": cnt,
            "density": density,
            "quality": qual,
            "dominant_label": dom_lbl,
            "dominant_name": dom_name,
            "zone": _classify_screen_zone(cx, cy),
        })

    # Build 3x3 zone summary and blindspot hints
    all_9_zones = list(_SCREEN_ZONE_LABELS.values())
    covered_zones = [z for z, c in sorted(zone_3x3_counts.items(), key=lambda kv: kv[1], reverse=True) if c > 0]
    blind_zones = [z for z in all_9_zones if zone_3x3_counts.get(z, 0) < max(5, int(total_frames * 0.02))]
    coverage_pct = int(round(100.0 * len(covered_zones) / 9.0)) if total_frames > 0 else 0

    top_zone_parts = [
        f"{z} {int(round(100.0 * zone_3x3_counts[z] / total_frames))}%"
        for z in covered_zones[:3]
    ] if total_frames > 0 else []

    filter_Options: List[Dict[str, Any]] = [
        {"key": "all", "name": f"전체 동작 ({sum(per_gesture_totals.values())}프레임)"}
    ]
    for lbl in (trained_classes or sorted(per_gesture_totals.keys())):
        if lbl == "none":
            continue
        cnt = int(per_gesture_totals.get(lbl, 0))
        disp = _LABEL_DISPLAY_NAMES.get(lbl, NAMES.get(lbl, lbl))
        r_pct = rate_by_label.get(lbl)
        r_str = f" · 인식 {r_pct * 100:.0f}%" if r_pct is not None else ""
        filter_Options.append({
            "key": lbl,
            "name": f"{disp} ({cnt}f{r_str})",
            "frames": cnt,
        })

    if total_frames > 0:
        hotspot_str = " · ".join(top_zone_parts)
        blind_str = (
            f" | 학습 부족 영역: {', '.join(blind_zones[:3])}" + (f" 외 {len(blind_zones) - 3}곳" if len(blind_zones) > 3 else "")
            if blind_zones
            else " | 9개 화면 영역 고르게 학습됨"
        )
        headline = f"집중 학습 영역: {hotspot_str} (화면 {len(covered_zones)}/9영역 · 총 {total_frames}프레임){blind_str}"
    else:
        headline = "선택한 조건에 해당하는 화면 위치별 동작 학습 데이터가 아직 없습니다."

    return {
        "model_id": (selected_entry or {}).get("id", "composite:active"),
        "model_title": (selected_entry or {}).get("short_title", "통합 하이브리드"),
        "gesture_filter": norm_filter or "all",
        "grid_w": 16,
        "grid_h": 10,
        "total_frames": total_frames,
        "max_cell_frames": max_cell_frames,
        "coverage_pct": coverage_pct,
        "covered_zone_count": len(covered_zones),
        "hotspot_zones": top_zone_parts,
        "blind_zones": blind_zones,
        "headline": headline,
        "cells": cells_out,
        "filter_options": filter_Options,
    }


# ---------------------------------------------------------------- model & recording simulator + data evaluation / calibration / augmentation
def evaluate_recording_in_simulator(
    filename: str,
    rec_dir: Path = REC_DIR,
    *,
    force_recheck: bool = False,
) -> Dict[str, Any]:
    """Evaluate raw and calibrated (cleaned) recording quality + augmentation status for the simulator."""
    from src.capture_dataset import discover_augmented_paths
    from src.data_quality import check_recording

    folder = Path(rec_dir)
    raw_path = folder / Path(filename).name
    if not raw_path.is_file():
        raise FileNotFoundError(f"녹화 파일을 찾을 수 없습니다: {filename}")

    cleaned_path = folder / "cleaned" / raw_path.name
    has_cleaned = cleaned_path.is_file()

    if force_recheck:
        raw_rep = check_recording(raw_path).to_dict()
        try:
            raw_path.with_name(raw_path.name + ".quality.json").write_text(
                json.dumps(raw_rep, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
    else:
        raw_rep = get_recording_quality(raw_path, compute_if_missing=True)

    active_rep = raw_rep
    if has_cleaned:
        try:
            active_rep = check_recording(cleaned_path).to_dict()
        except Exception:
            active_rep = raw_rep

    metrics = active_rep.get("metrics") or {}
    total_f = int(metrics.get("total_frames", 0))
    valid_f = int(metrics.get("valid_frames", 0))
    missing_pct = round(float(metrics.get("missing_hand_ratio", 0.0)) * 100.0, 1)
    outlier_cnt = int(metrics.get("outlier_count", 0))
    short_do_cnt = int(metrics.get("short_do_count", 0))
    abnormal_cnt = int(metrics.get("abnormal_size_count", 0))
    issues = list(active_rep.get("issues") or [])

    aug_paths = discover_augmented_paths([raw_path])
    aug_cfg = get_augmentation_settings(folder)
    aug_summary = (
        f"증강 {'사용 중' if aug_cfg['enabled'] else '꺼짐'} · "
        f"강도: {aug_cfg['strength_label']} · "
        f"수량: {aug_cfg['factor']}배 · "
        f"현재 생성된 증강본 {len(aug_paths)}개"
    )

    raw_score = float(raw_rep.get("score") or 0.0)
    act_score = float(active_rep.get("score") or 0.0)
    act_status = str(active_rep.get("status") or "unknown")
    if has_cleaned:
        eval_headline = (
            f"[보정본 적용 중] 품질 {act_score * 100:.0f}점 (원본 {raw_score * 100:.0f}점) · "
            f"유효 {valid_f}/{total_f}프레임 · 손 유실 {missing_pct}% · 관절 점프 {outlier_cnt}건 · 짧은 구간 {short_do_cnt}건"
        )
    else:
        eval_headline = (
            f"[원본 데이터] 품질 {act_score * 100:.0f}점 ({active_rep.get('summary', '')}) · "
            f"유효 {valid_f}/{total_f}프레임 · 손 유실 {missing_pct}% · 관절 점프 {outlier_cnt}건 · 짧은 구간 {short_do_cnt}건"
        )

    # Extract explicit target gesture & condition intent for this recording
    try:
        file_sum = _summarise_file(raw_path)
    except Exception:
        file_sum = {}
    if not file_sum.get("target_gestures_text"):
        try:
            from src.recorder import read_session
            rmeta, rframes, _ = read_session(raw_path)
            itasks: List[str] = []
            conds: List[str] = []
            p_pos: List[str] = []
            z_cnts: Dict[str, int] = defaultdict(int)
            p_cnt = 0
            for rf in rframes:
                if isinstance(rf.get("pose"), list) and len(rf["pose"]) >= 6:
                    p_cnt += 1
                lm_rf = rf.get("lm")
                if isinstance(lm_rf, list) and len(lm_rf) == 21:
                    try:
                        px = sum(float(lm_rf[j][0]) for j in (0, 5, 9, 13, 17)) / 5.0
                        py = sum(float(lm_rf[j][1]) for j in (0, 5, 9, 13, 17)) / 5.0
                        z_cnts[_classify_screen_zone(px, py)] += 1
                    except Exception:
                        pass
                rlab = rf.get("label") or {}
                pos_l = rlab.get("position")
                if isinstance(pos_l, str) and pos_l.strip() and pos_l.strip() not in p_pos:
                    p_pos.append(pos_l.strip())
                tcand = rlab.get("task") or rlab.get("target")
                if isinstance(tcand, str) and tcand in BY_KEY and tcand not in itasks:
                    itasks.append(tcand)
                cstr = rlab.get("condition")
                if isinstance(cstr, str) and cstr.strip() and cstr.strip() not in conds:
                    conds.append(cstr.strip())
            f_key = str(rmeta.get("focus") or (rmeta.get("capture_plan") or {}).get("focus") or "")
            file_sum = {
                "frames": len(rframes),
                "pose_frames": p_cnt,
                "screen_zones_text": _format_screen_zones_summary(dict(z_cnts), p_pos),
                **_build_recording_intent_fields(rmeta.get("mode"), f_key, itasks, {}, {}, conds),
            }
        except Exception:
            file_sum = {}

    pose_f = int(file_sum.get("pose_frames", 0))
    pose_pct = int(round(100.0 * pose_f / max(1, total_f or int(file_sum.get("frames", 1)))))

    return {
        "file": raw_path.name,
        "cleaned_exists": has_cleaned,
        "raw_score": round(raw_score, 3),
        "raw_status": str(raw_rep.get("status") or "unknown"),
        "raw_summary": str(raw_rep.get("summary") or ""),
        "active_score": round(act_score, 3),
        "active_status": act_status,
        "active_summary": str(active_rep.get("summary") or ""),
        "headline": eval_headline,
        "total_frames": total_f,
        "valid_frames": valid_f,
        "missing_pct": missing_pct,
        "outlier_count": outlier_cnt,
        "short_do_count": short_do_cnt,
        "abnormal_size_count": abnormal_cnt,
        "issues": issues[:12],
        "recorded_mode_label": str(file_sum.get("recorded_mode_label") or "가이드 녹화"),
        "target_gestures_text": str(file_sum.get("target_gestures_text") or "목표 동작 정보 확인 중"),
        "target_short_text": str(file_sum.get("target_short_text") or ""),
        "target_conditions_text": str(file_sum.get("target_conditions_text") or "기본 촬영 자세"),
        "target_instruction_text": str(file_sum.get("target_instruction_text") or ""),
        "screen_zones_text": str(file_sum.get("screen_zones_text") or "화면 영역 분석 중"),
        "pose_coverage_pct": pose_pct,
        "augmentation": {
            "enabled": aug_cfg["enabled"],
            "strength": aug_cfg["strength"],
            "strength_label": aug_cfg["strength_label"],
            "factor": aug_cfg["factor"],
            "existing_copies": len(aug_paths),
            "summary": aug_summary,
        },
    }


def calibrate_recording_data(
    filename: str,
    *,
    max_interp_gap: int = 3,
    max_joint_jump: float = 0.38,
    min_do_frames: int = 4,
    rec_dir: Path = REC_DIR,
) -> Dict[str, Any]:
    """Run automated data calibration (dropout interpolation, outlier smoothing, short-do masking) on a recording."""
    from src.data_quality import check_recording, clean_recording

    folder = Path(rec_dir)
    raw_path = folder / Path(filename).name
    if not raw_path.is_file():
        raise FileNotFoundError(f"녹화 파일을 찾을 수 없습니다: {filename}")

    raw_rep = check_recording(
        raw_path,
        min_do_frames=int(min_do_frames),
        max_joint_jump=float(max_joint_jump),
    )
    cleaned_path = clean_recording(
        raw_path,
        raw_rep,
        output_dir=folder / "cleaned",
        min_do_frames=int(min_do_frames),
        max_interp_gap=int(max_interp_gap),
        max_joint_jump=float(max_joint_jump),
    )
    clean_rep = check_recording(
        cleaned_path,
        min_do_frames=int(min_do_frames),
        max_joint_jump=float(max_joint_jump),
    )
    # Update the quality cache so the recording list also reflects calibrated status
    q_dict = clean_rep.to_dict()
    q_dict["summary"] = (
        f"보정 완료 ({raw_rep.score * 100:.0f}점→{clean_rep.score * 100:.0f}점): " + clean_rep.summary
    )
    try:
        raw_path.with_name(raw_path.name + ".quality.json").write_text(
            json.dumps(q_dict, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass

    return {
        "file": raw_path.name,
        "cleaned_path": str(cleaned_path),
        "before_score": round(float(raw_rep.score), 3),
        "after_score": round(float(clean_rep.score), 3),
        "before_frames": int(raw_rep.metrics.get("total_frames", 0)),
        "after_frames": int(clean_rep.metrics.get("total_frames", 0)),
        "before_valid": int(raw_rep.metrics.get("valid_frames", 0)),
        "after_valid": int(clean_rep.metrics.get("valid_frames", 0)),
        "message": (
            f"'{raw_path.name}' 데이터 보정 완료: 품질 점수 {raw_rep.score * 100:.0f}점 → {clean_rep.score * 100:.0f}점 "
            f"(유효 프레임 {raw_rep.metrics.get('valid_frames', 0)} → {clean_rep.metrics.get('valid_frames', 0)}, "
            f"관절 점프 {raw_rep.metrics.get('outlier_count', 0)} → {clean_rep.metrics.get('outlier_count', 0)}건)"
        ),
    }


def reset_calibrated_recording(filename: str, rec_dir: Path = REC_DIR) -> Dict[str, Any]:
    """Remove the calibrated `cleaned/` copy for a recording and restore raw quality report."""
    from src.data_quality import check_recording

    folder = Path(rec_dir)
    raw_path = folder / Path(filename).name
    cleaned_path = folder / "cleaned" / raw_path.name
    removed = False
    if cleaned_path.is_file():
        try:
            cleaned_path.unlink()
            removed = True
        except OSError:
            pass
    if raw_path.is_file():
        rep = check_recording(raw_path)
        try:
            raw_path.with_name(raw_path.name + ".quality.json").write_text(
                json.dumps(rep.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
    return {
        "file": raw_path.name,
        "removed": removed,
        "message": f"'{raw_path.name}' 보정본을 초기화하고 원본 데이터로 되돌렸습니다.",
    }


def apply_recording_augmentation(
    filename: str,
    *,
    enabled: bool = True,
    strength: str = "normal",
    factor: int = 2,
    rec_dir: Path = REC_DIR,
) -> Dict[str, Any]:
    """Save augmentation settings (enabled, strength, factor) and generate/clear offline augmented copies for `filename`."""
    from src.capture_dataset import discover_augmented_paths, resolve_cleaned_path
    from src.offline_augment import augment_recording_file

    folder = Path(rec_dir)
    aug_cfg = set_augmentation_settings(
        enabled=bool(enabled),
        strength=str(strength),
        factor=int(factor),
        rec_dir=folder,
    )

    if not filename:
        return {
            "file": "",
            "settings": aug_cfg,
            "generated_files": [],
            "message": (
                f"학습 증강 설정 저장됨: {'사용' if aug_cfg['enabled'] else '사용 안 함'} · "
                f"강도 {aug_cfg['strength_label']} · 수량 {aug_cfg['factor']}배"
            ),
        }

    if filename == "__all__":
        from src.recorder import finished_recordings
        complete_paths, _ = finished_recordings(folder)
        selected_paths = [Path(p) for p in filter_selected_recording_paths(complete_paths, folder)]
        for old_aug in discover_augmented_paths(complete_paths):
            try:
                Path(old_aug).unlink()
            except OSError:
                pass
        if not aug_cfg["enabled"]:
            return {
                "file": "__all__",
                "settings": aug_cfg,
                "generated_files": [],
                "message": "전체 녹화 데이터 증강 사용 안 함으로 설정 (기존 증강본 모두 정리 완료)",
            }
        written_all: List[Path] = []
        for rp in selected_paths:
            src_for_aug = resolve_cleaned_path(rp, use_cleaned=True)
            written_all.extend(
                augment_recording_file(
                    src_for_aug,
                    factor=aug_cfg["factor"],
                    rotation_deg=aug_cfg["rotation_deg"],
                    scale_range=aug_cfg["scale_range"],
                    jitter_std=aug_cfg["jitter_std"],
                    time_stretch_range=aug_cfg["time_stretch_range"],
                    frame_drop_prob=aug_cfg["frame_drop_prob"],
                    output_dir=folder / "augmented",
                )
            )
        return {
            "file": "__all__",
            "settings": aug_cfg,
            "generated_files": [p.name for p in written_all],
            "message": (
                f"선택된 녹화 {len(selected_paths)}개 전체 증강 완료: {aug_cfg['strength_label']} · "
                f"각 {aug_cfg['factor']}배씩 총 {len(written_all)}개 증강본 생성됨"
            ),
        }

    raw_path = folder / Path(filename).name
    if not raw_path.is_file():
        raise FileNotFoundError(f"녹화 파일을 찾을 수 없습니다: {filename}")

    # Remove old augmented copies for this recording first
    for old_aug in discover_augmented_paths([raw_path]):
        try:
            Path(old_aug).unlink()
        except OSError:
            pass

    if not aug_cfg["enabled"]:
        return {
            "file": raw_path.name,
            "settings": aug_cfg,
            "generated_files": [],
            "message": f"'{raw_path.name}' 증강 사용 안 함으로 설정 (기존 증강본 정리 완료)",
        }

    src_for_aug = resolve_cleaned_path(raw_path, use_cleaned=True)
    written = augment_recording_file(
        src_for_aug,
        factor=aug_cfg["factor"],
        rotation_deg=aug_cfg["rotation_deg"],
        scale_range=aug_cfg["scale_range"],
        jitter_std=aug_cfg["jitter_std"],
        time_stretch_range=aug_cfg["time_stretch_range"],
        frame_drop_prob=aug_cfg["frame_drop_prob"],
        output_dir=folder / "augmented",
    )
    return {
        "file": raw_path.name,
        "settings": aug_cfg,
        "generated_files": [p.name for p in written],
        "message": (
            f"'{raw_path.name}' 데이터 증강 완료: {aug_cfg['strength_label']} · "
            f"{len(written)}배 증강본 생성됨 ({', '.join(p.name for p in written[:2])}{' 외' if len(written) > 2 else ''})"
        ),
    }


def _extract_rule_targets(rule: Any) -> List[str]:
    """Return canonical gesture keys detected by rule-based engine in a recorded frame's `rule` dict."""
    if not isinstance(rule, dict):
        return []
    found: List[str] = []
    targets = rule.get("targets")
    if isinstance(targets, dict):
        for k, v in targets.items():
            if v and k not in found:
                found.append(str(k))
    elif isinstance(targets, (list, tuple, set)):
        for k in targets:
            if isinstance(k, str) and k not in found:
                found.append(k)
    act = rule.get("action")
    gest = rule.get("gesture") or ""
    if act and act not in ("idle", "move", "none"):
        for t in BY_KEY.values():
            if t.action and t.action == act:
                if act == "drag_hold" and ((t.key == "thumb_index_middle") != (gest == "thumb_index_middle_pinch")):
                    continue
                if t.label not in found:
                    found.append(t.label)
    return found


_PINCH_JOINT_MAP: Dict[str, List[int]] = {
    "thumb_index": [4, 8],
    "thumb_middle": [4, 12],
    "thumb_ring": [4, 16],
    "thumb_index_middle": [4, 8, 12],
}
_SWIPE_DIR_MAP: Dict[str, str] = {
    "swipe_left": "left",
    "swipe_right": "right",
    "swipe_down": "down",
}
_SIGN_JOINT_MAP: Dict[str, List[int]] = {
    "v_sign": [8, 12],
    "rock_sign": [8, 20],
    "thumbs_up": [4],
}


def _compute_elbow_angle_deg(shoulder: Sequence[float], elbow: Sequence[float], wrist: Sequence[float]) -> int:
    """Compute interior angle (degrees) at the elbow between shoulder-elbow and wrist-elbow vectors."""
    import math
    ux, uy = float(shoulder[0]) - float(elbow[0]), float(shoulder[1]) - float(elbow[1])
    vx, vy = float(wrist[0]) - float(elbow[0]), float(wrist[1]) - float(elbow[1])
    nu = math.hypot(ux, uy)
    nv = math.hypot(vx, vy)
    if nu < 1e-6 or nv < 1e-6:
        return 0
    cos_a = max(-1.0, min(1.0, (ux * vx + uy * vy) / (nu * nv)))
    return int(round(math.degrees(math.acos(cos_a))))


def _build_playback_frames(
    raw_frames: List[Dict[str, Any]],
    features: Any,
    probs: Optional[Any],
    thresholds_by_label: Dict[str, float],
    default_thr: float,
    issues: List[Dict[str, Any]],
    y_labels: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Build per-frame hand + arm/shoulder skeleton & event descriptors for the interactive simulator media player."""
    import math
    from src.capture_dataset import CLASSES
    n = len(raw_frames)
    if n == 0:
        return []
    t0 = float(raw_frames[0].get("t", 0.0))
    non_none_indices = [idx for idx, c in enumerate(CLASSES) if c != "none"]

    issue_by_frame: Dict[int, str] = {}
    for iss in issues:
        sf = int(iss.get("start_frame", 0))
        ef = int(iss.get("end_frame", sf))
        msg = str(iss.get("message", ""))
        for fi in range(max(0, sf), min(n, ef + 1)):
            if fi not in issue_by_frame:
                issue_by_frame[fi] = msg

    out: List[Dict[str, Any]] = []
    smoothed_pose: Optional[List[List[float]]] = None
    last_pose_t: float = -10.0

    for i, rf in enumerate(raw_frames):
        cur_t = float(rf.get("t", t0))
        lm_raw = rf.get("lm")
        hand_ok = isinstance(lm_raw, list) and len(lm_raw) == 21
        lm_2d = (
            [[round(float(p[0]), 4), round(float(p[1]), 4)] for p in lm_raw]
            if hand_ok
            else []
        )
        palm_pt = (
            [
                round(sum(lm_2d[j][0] for j in (0, 5, 9, 13, 17)) / 5.0, 4),
                round(sum(lm_2d[j][1] for j in (0, 5, 9, 13, 17)) / 5.0, 4),
            ]
            if hand_ok
            else None
        )
        hand_meta = rf.get("hand")
        handedness = str(hand_meta[0]) if isinstance(hand_meta, list) and hand_meta else "Right"

        # Extract & smooth MediaPipe Pose joints 11..16:
        # [0]=L_shoulder(11), [1]=R_shoulder(12), [2]=L_elbow(13), [3]=R_elbow(14), [4]=L_wrist(15), [5]=R_wrist(16)
        pose_raw = rf.get("pose")
        raw_pose_ok = isinstance(pose_raw, list) and len(pose_raw) >= 6 and all(
            isinstance(pt, (list, tuple)) and len(pt) >= 2 for pt in pose_raw[:6]
        )
        if raw_pose_ok:
            cur_pose: List[List[float]] = []
            for j_idx in range(6):
                pt = pose_raw[j_idx]
                px, py = float(pt[0]), float(pt[1])
                pv = float(pt[2]) if len(pt) >= 3 else 1.0
                if smoothed_pose is not None and (cur_t - last_pose_t) < 0.6:
                    ox, oy, ov = smoothed_pose[j_idx]
                    alpha = 0.35 if pv >= 0.45 else 0.12
                    px = ox + alpha * (px - ox)
                    py = oy + alpha * (py - oy)
                cur_pose.append([round(px, 4), round(py, 4), round(pv, 3)])
            smoothed_pose = cur_pose
            last_pose_t = cur_t

        pose_present = smoothed_pose is not None and (cur_t - last_pose_t) <= 0.45
        pose_joints = smoothed_pose if pose_present else []
        active_arm: Optional[Dict[str, Any]] = None

        if pose_present and len(pose_joints) >= 6:
            # Left arm = (0, 2, 4), Right arm = (1, 3, 5)
            l_s, r_s, l_e, r_e, l_w, r_w = pose_joints[:6]
            if hand_ok:
                hwx, hwy = lm_2d[0][0], lm_2d[0][1]
                d_left = math.hypot(l_w[0] - hwx, l_w[1] - hwy)
                d_right = math.hypot(r_w[0] - hwx, r_w[1] - hwy)
                side = "left" if d_left <= d_right else "right"
            else:
                v_left = l_s[2] + l_e[2] + l_w[2]
                v_right = r_s[2] + r_e[2] + r_w[2]
                side = "left" if v_left >= v_right else "right"

            s_pt, e_pt, w_pt = (l_s, l_e, l_w) if side == "left" else (r_s, r_e, r_w)
            other_s, other_e, other_w = (r_s, r_e, r_w) if side == "left" else (l_s, l_e, l_w)
            wrist_for_angle = [lm_2d[0][0], lm_2d[0][1], w_pt[2]] if hand_ok else w_pt
            elbow_deg = _compute_elbow_angle_deg(s_pt, e_pt, wrist_for_angle)
            active_arm = {
                "source": "pose",
                "side": side,
                "side_ko": "왼팔" if side == "left" else "오른팔",
                "shoulder": s_pt,
                "elbow": e_pt,
                "wrist": w_pt,
                "other_shoulder": other_s,
                "other_elbow": other_e,
                "other_wrist": other_w,
                "elbow_deg": elbow_deg,
            }
        elif hand_ok:
            # Anatomical fallback estimate when Pose was not captured in this frame
            hwx, hwy = lm_2d[0][0], lm_2d[0][1]
            mcpx, mcpy = lm_2d[9][0], lm_2d[9][1]
            dx, dy = hwx - mcpx, hwy - mcpy
            hand_len = max(0.06, math.hypot(dx, dy))
            ux, uy = dx / hand_len, dy / hand_len
            side = "left" if handedness.lower().startswith("l") else "right"
            lat = -1.0 if side == "left" else 1.0
            ex = round(hwx + ux * (hand_len * 2.1) + lat * 0.04, 4)
            ey = round(hwy + uy * (hand_len * 2.1) + 0.06, 4)
            sx = round(ex - lat * (hand_len * 0.55), 4)
            sy = round(ey - hand_len * 1.95, 4)
            s_pt = [sx, sy, 0.35]
            e_pt = [ex, ey, 0.35]
            w_pt = [hwx, hwy, 0.8]
            active_arm = {
                "source": "estimated",
                "side": side,
                "side_ko": ("왼팔" if side == "left" else "오른팔") + "(추정)",
                "shoulder": s_pt,
                "elbow": e_pt,
                "wrist": w_pt,
                "other_shoulder": [round(sx - lat * (hand_len * 1.8), 4), sy, 0.25],
                "other_elbow": [],
                "other_wrist": [],
                "elbow_deg": _compute_elbow_angle_deg(s_pt, e_pt, w_pt),
            }

        lab = rf.get("label") or {}
        target_lbl = str(lab.get("target") or "none")
        task_lbl = str(lab.get("task") or target_lbl or "none")
        refined_target = str(lab.get("refined_target") or "")
        condition_str = str(lab.get("condition") or "")
        target_pos_str = str(lab.get("position") or "")
        screen_zone = _classify_screen_zone(palm_pt[0], palm_pt[1]) if palm_pt else ""
        palm_xy_pct = [int(round(palm_pt[0] * 100.0)), int(round(palm_pt[1] * 100.0))] if palm_pt else []
        raw_cur = rf.get("cursor")
        cursor_pt = [int(raw_cur[0]), int(raw_cur[1])] if isinstance(raw_cur, list) and len(raw_cur) == 2 else []
        phase = str(lab.get("phase") or "")
        rep_val = lab.get("rep")
        rep_num = int(rep_val) if isinstance(rep_val, int) else -1

        task_obj = BY_KEY.get(task_lbl)
        is_guided_target = task_obj is not None and not task_obj.free and task_obj.label not in ("none", "idle", "")
        scheduled_cls = (
            task_obj.label
            if is_guided_target
            else (target_lbl if target_lbl not in ("none", "idle", "") else "")
        )
        y_val = int(y_labels[i]) if (y_labels is not None and i < len(y_labels)) else None

        if phase in ("waiting", "paused") or target_lbl == "idle":
            zone = "waiting"
            zone_ko = "세트 대기 구간"
        elif phase == "do" and scheduled_cls:
            if refined_target == "transition" or (y_val is not None and y_val <= 0):
                zone = "transition"
                zone_ko = "전환 과정 (진입·해제 경계)"
            else:
                zone = "judgment"
                zone_ko = "판정 구간"
        elif phase in ("prep", "prepare", "rest"):
            zone = "transition"
            zone_ko = "전환 과정 (준비·복귀 이동)"
        else:
            zone = "transition"
            zone_ko = "자유 이동 / 중립"

        phase_ko = {
            "do": (
                ("판정 구간 · 동작 수행 중" if zone == "judgment" else "전환 과정 · 진입/해제 경계")
                + (f" (#{rep_num}회차)" if rep_num > 0 else "")
            ),
            "rest": "전환 과정 · 동작 사이 복귀/휴식",
            "prepare": "전환 과정 · 자세 준비 구간",
            "prep": "전환 과정 · 자세 준비 구간",
            "waiting": "다음 세트 시작 대기 (Space 키 대기 구간)",
            "paused": "녹화 일시정지 구간",
            "free": "자유 이동 (오작동 방지)",
            "use": "일반 마우스 사용",
        }.get(phase, phase or "대기")

        rule = rf.get("rule") or {}
        raw_rule_targets = _extract_rule_targets(rule)
        interfering_targets: List[str] = []
        if is_guided_target:
            interfering_targets = list(
                rule.get("interfering_targets")
                or ([t for t in raw_rule_targets if t != task_obj.key] if phase == "do" else [])
            )
            if phase == "do" and (task_obj.key in raw_rule_targets or zone == "judgment"):
                rule_targets = [task_obj.key]
                rule_action = task_obj.action or task_obj.label
            else:
                rule_targets = []
                rule_action = "none"
        elif phase in ("prep", "prepare", "rest", "waiting", "paused"):
            rule_targets = []
            rule_action = "none"
        else:
            rule_targets = raw_rule_targets
            rule_action = str(rule.get("action") or "idle")

        best_cls = "none"
        best_p = 0.0
        model_hit = False
        if probs is not None and i < len(probs):
            if scheduled_cls and scheduled_cls in CLASSES:
                ci = CLASSES.index(scheduled_cls)
                best_p = float(probs[i, ci])
                best_cls = scheduled_cls if best_p > 0.0 else "none"
                lbl_thr = float(thresholds_by_label.get(scheduled_cls, default_thr))
                model_hit = best_p >= lbl_thr
            else:
                for ci in non_none_indices:
                    pv = float(probs[i, ci])
                    if pv > best_p:
                        best_p = pv
                        best_cls = CLASSES[ci]
                lbl_thr = float(thresholds_by_label.get(best_cls, default_thr))
                model_hit = best_p >= lbl_thr and best_cls != "none"

        vx = float(features[i, 130]) if features is not None and i < len(features) else 0.0
        vy = float(features[i, 131]) if features is not None and i < len(features) else 0.0

        active_ev = "none"
        if scheduled_cls:
            if zone == "judgment":
                active_ev = scheduled_cls
        else:
            if model_hit:
                active_ev = best_cls
            elif rule_targets:
                active_ev = BY_KEY[rule_targets[0]].label if rule_targets[0] in BY_KEY else rule_targets[0]

        pinch_joints = _PINCH_JOINT_MAP.get(active_ev, [])
        swipe_dir = _SWIPE_DIR_MAP.get(active_ev, "")
        sign_joints = _SIGN_JOINT_MAP.get(active_ev, [])

        if pinch_joints:
            ev_kind = "pinch"
            ev_name = NAMES.get(active_ev, active_ev)
            ev_text = f"판정 구간 · PINCH ({ev_name})"
        elif swipe_dir:
            ev_kind = "swipe"
            dir_ko = {"left": "← 왼쪽 스와이프", "right": "→ 오른쪽 스와이프", "down": "↓ 아래로 스와이프"}.get(swipe_dir, "스와이프")
            ev_text = f"판정 구간 · SWIPE ({dir_ko})"
        elif sign_joints:
            ev_kind = "sign"
            ev_text = f"판정 구간 · SIGN ({NAMES.get(active_ev, active_ev)})"
        elif zone == "transition" and scheduled_cls:
            ev_kind = "transition"
            tgt_ko = NAMES.get(task_lbl, NAMES.get(scheduled_cls, scheduled_cls))
            ev_text = f"전환 과정 · {tgt_ko} ({'진입·해제 경계' if phase == 'do' else '준비·복귀 이동'})"
        elif zone == "waiting":
            ev_kind = "none"
            ev_text = "세트 전환 대기 구간"
        else:
            ev_kind = "none"
            ev_text = "대기 / 커서 이동" if hand_ok else ("팔·어깨만 감지됨" if pose_present else "손 감지 안 됨")

        out.append({
            "idx": i,
            "t": round(cur_t - t0, 2),
            "hand_present": hand_ok,
            "handedness": handedness,
            "lm": lm_2d,
            "palm": palm_pt,
            "screen_zone": screen_zone,
            "palm_xy_pct": palm_xy_pct,
            "cursor": cursor_pt,
            "target_position": target_pos_str,
            "pose_present": pose_present,
            "pose_joints": pose_joints,
            "active_arm": active_arm,
            "task": task_lbl,
            "task_name": NAMES.get(
                task_lbl,
                "세트 전환 대기"
                if (task_lbl == "idle" or phase in ("waiting", "paused"))
                else ("중립/이동" if task_lbl in ("none", "") else task_lbl),
            ),
            "condition": condition_str,
            "target": target_lbl,
            "target_name": NAMES.get(
                target_lbl,
                "세트 전환 대기"
                if (target_lbl == "idle" or phase in ("waiting", "paused"))
                else (
                    f"{NAMES.get(task_lbl, NAMES.get(scheduled_cls, scheduled_cls))} (전환 과정)"
                    if (scheduled_cls and zone == "transition")
                    else ("중립/이동" if target_lbl in ("none", "") else target_lbl)
                ),
            ),
            "zone": zone,
            "zone_ko": zone_ko,
            "phase": phase,
            "phase_ko": phase_ko,
            "rep": rep_num,
            "model_cls": best_cls,
            "model_name": NAMES.get(best_cls, best_cls) if best_cls != "none" else "중립",
            "model_prob": round(best_p, 3),
            "model_hit": model_hit,
            "rule_action": rule_action,
            "rule_targets": rule_targets,
            "rule_names": [NAMES.get(k, k) for k in rule_targets],
            "interfering_targets": interfering_targets,
            "interfering_names": [NAMES.get(k, k) for k in interfering_targets],
            "vx": round(vx, 2),
            "vy": round(vy, 2),
            "pinch_joints": pinch_joints,
            "swipe_dir": swipe_dir,
            "sign_joints": sign_joints,
            "event_kind": ev_kind,
            "event_text": ev_text,
            "issue_text": issue_by_frame.get(i, ""),
        })
    return out


def simulate_model_on_recording(
    filename: str,
    rec_dir: Path = REC_DIR,
    model: Path = MODEL,
    *,
    use_cleaned: bool = True,
    model_id: str = "",
) -> Dict[str, Any]:
    """Replay a recorded session through the selected ONNX model (or current composite model) vs. recorded rules."""
    import numpy as np
    from src.capture_dataset import CLASSES, load_sessions, resolve_cleaned_path
    from src.gesture_gate import DEFAULT_THRESHOLD
    from src.gesture_models import compose_predictor, load_single, read_bank
    from src.recorder import read_session
    from src.tcn_features import FEATURE_VERSION

    folder = Path(rec_dir)
    path = folder / Path(filename).name
    if not path.is_file():
        raise FileNotFoundError(f"녹화 파일을 찾을 수 없습니다: {filename}")

    eval_info = evaluate_recording_in_simulator(path.name, rec_dir=folder, force_recheck=False)
    sim_path = resolve_cleaned_path(path, use_cleaned=use_cleaned)
    using_cleaned = sim_path.resolve() != path.resolve()
    _, raw_frames, _ = read_session(sim_path)

    effective_model = Path(model)
    sel_id = str(model_id or "").strip()
    if not sel_id and effective_model.resolve() == MODEL.resolve():
        sel_id = str(get_active_onnx_selection(effective_model).get("id") or "composite:active")

    sim_mode = "composite"
    if sel_id == "main:gesture_tcn.onnx":
        sim_mode = "main_only"
    elif sel_id.startswith("specialist:"):
        sim_mode = "single_onnx"
        fname = Path(sel_id.split(":", 1)[1]).name
        effective_model = effective_model.parent / "gestures" / fname
    elif sel_id.startswith("backup:") or sel_id.startswith("export:"):
        kind_part, f_name = sel_id.split(":", 1)
        b_root = EXPORT_DIR if kind_part == "export" else BACKUP_DIR
        b_cand = b_root / Path(f_name).name / "gesture_tcn.onnx"
        if b_cand.parent.is_dir():
            effective_model = b_cand

    card_path = effective_model.with_suffix(".json")
    win = 15
    if card_path.is_file():
        try:
            win = int(json.loads(card_path.read_text(encoding="utf-8")).get("window", 15))
        except Exception:
            win = 15

    sessions = load_sessions([str(sim_path)], win, use_cleaned=False)
    if not sessions or len(sessions[0]["x"]) == 0:
        pb_frames = _build_playback_frames(raw_frames, None, None, {}, DEFAULT_THRESHOLD, eval_info.get("issues", []))
        return {
            "file": path.name,
            "using_cleaned": using_cleaned,
            "frames": len(raw_frames),
            "duration_s": round(len(raw_frames) / 30.0, 1),
            "has_model": False,
            "summary": "시뮬레이션 가능한 손 랜드마크 구간이 없습니다.",
            "gestures": [],
            "timeline": [],
            "reps": [],
            "playback_frames": pb_frames,
            "quality": eval_info,
            "augmentation": eval_info["augmentation"],
        }
    session = sessions[0]

    bank_dir = effective_model.parent / "gestures" if sim_mode != "single_onnx" else effective_model.parent
    base_pred = None
    info: Dict[str, Any] = {"classes": ["none"], "window": win, "feature_version": FEATURE_VERSION}
    disabled: set[str] = set()
    if effective_model.is_file() and card_path.is_file():
        try:
            base_pred, info = load_single(effective_model, FEATURE_VERSION)
            disabled = set(info.get("disabled_gestures") or ()) if sim_mode != "single_onnx" else set()
        except Exception:
            base_pred = None

    specialists = read_bank(bank_dir) if (sim_mode == "composite" and (bank_dir / "manifest.json").is_file()) else {}
    if base_pred is None and not specialists:
        sess_y = session.get("y")
        pb_frames = _build_playback_frames(
            raw_frames,
            session.get("features"),
            None,
            {},
            DEFAULT_THRESHOLD,
            eval_info.get("issues", []),
            y_labels=sess_y,
        )
        n_f = int(len(session["x"]))
        bins_untrained = min(80, max(1, n_f))
        untrained_timeline = []
        for b in range(bins_untrained):
            s_i = int(b * n_f / bins_untrained)
            e_i = max(s_i + 1, int((b + 1) * n_f / bins_untrained))
            target_ids = [int(v) for v in sess_y[s_i:e_i] if int(v) > 0] if sess_y is not None else []
            if target_ids and target_ids[0] < len(CLASSES):
                t_lbl = CLASSES[target_ids[0]]
                z_kind = "judgment"
                z_name = "판정 구간"
            elif sess_y is not None and any(int(v) == 0 for v in sess_y[s_i:e_i]):
                t_lbl = "none"
                z_kind = "transition"
                z_name = "전환 과정"
            else:
                t_lbl = "idle"
                z_kind = "waiting"
                z_name = "세트 전환 대기"
            untrained_timeline.append({
                "bin": b,
                "prob": 0.65 if z_kind == "judgment" else (0.22 if z_kind == "transition" else 0.1),
                "predicted": "none",
                "predicted_name": "학습 전",
                "target": t_lbl,
                "target_name": f"판정 구간 · {NAMES.get(t_lbl, t_lbl)}" if z_kind == "judgment" else z_name,
                "zone": z_kind,
                "zone_name": z_name,
                "confirmed": False,
            })
        return {
            "file": path.name,
            "using_cleaned": using_cleaned,
            "frames": n_f,
            "duration_s": round(float(n_f) / 30.0, 1),
            "has_model": False,
            "summary": "학습 모델 적용 전 · 녹화된 데이터의 판정 구간과 전환 과정을 표시합니다.",
            "gestures": [],
            "timeline": untrained_timeline,
            "reps": [],
            "playback_frames": pb_frames,
            "quality": eval_info,
            "augmentation": eval_info["augmentation"],
        }

    if base_pred is None:
        base_pred = lambda _x: np.array([1.0], dtype=np.float64)
    if sim_mode == "composite":
        predict_one, merged_info = compose_predictor(effective_model, base_pred, info)
    else:
        predict_one, merged_info = base_pred, dict(info)
    active_classes = list(merged_info.get("classes", []))
    threshold = float(merged_info.get("threshold", DEFAULT_THRESHOLD))
    thresholds_by_label = dict(merged_info.get("thresholds") or {})

    # Evaluate across all windows in session["x"] (shape: (N, win, features))
    xs = session["x"]
    n_frames = len(xs)
    probs = np.zeros((n_frames, len(CLASSES)), dtype=np.float64)
    cls_to_global = {c: CLASSES.index(c) for c in active_classes if c in CLASSES}
    for i in range(n_frames):
        sc = predict_one(xs[i:i + 1])
        for local_idx, cname in enumerate(active_classes):
            g_idx = cls_to_global.get(cname)
            if g_idx is not None:
                probs[i, g_idx] = float(sc[local_idx])

    for lbl in disabled:
        if lbl in CLASSES and lbl not in specialists:
            probs[:, CLASSES.index(lbl)] = 0.0

    # Blend 0-lag 96-dim Spatial MLP (+ per-gesture specialist MLPs) if available
    sp_file = effective_model.parent / "spatial_mlp.json" if sim_mode != "single_onnx" else effective_model.parent.parent / "spatial_mlp.json"
    if sp_file.is_file() or (bank_dir / "manifest.json").is_file() or sim_mode == "single_onnx":
        try:
            from src.spatial_mlp import (
                SpatialMlpModel,
                blend_tcn_and_spatial_probs,
                compose_spatial_mlp,
                ensure_session_spatial_features,
            )
            sp_model = None
            if sim_mode == "single_onnx":
                sp_dict = info.get("spatial_mlp")
                if isinstance(sp_dict, dict):
                    sp_model = SpatialMlpModel.from_dict(sp_dict)
            elif sim_mode == "main_only":
                sp_model = SpatialMlpModel.load(sp_file) if sp_file.is_file() else None
            else:
                base_sp = SpatialMlpModel.load(sp_file) if sp_file.is_file() else None
                sp_model = compose_spatial_mlp(effective_model, base_sp)
            s_feats = ensure_session_spatial_features(session)
            if sp_model is not None and len(s_feats) == n_frames:
                sp_probs = sp_model.predict_proba(s_feats)
                probs = blend_tcn_and_spatial_probs(probs, CLASSES, sp_probs, sp_model.classes)
                for lbl in disabled:
                    if lbl in CLASSES and lbl not in specialists:
                        probs[:, CLASSES.index(lbl)] = 0.0
        except Exception:
            pass

    y = session["y"]
    frames = session["frames"]
    t0 = float(frames[0].get("t", 0.0)) if n_frames else 0.0
    t_end = float(frames[-1].get("t", t0)) if n_frames else 0.0
    duration_s = max(0.1, t_end - t0) if t_end > t0 else round(n_frames / 30.0, 1)

    # Identify contiguous positive action blocks (or distinct label["rep"] runs)
    rep_blocks: List[List[int]] = []
    cur_block: List[int] = []
    cur_key: Optional[Tuple[int, Any, Any]] = None
    for i in range(n_frames):
        cls_id = int(y[i])
        if cls_id > 0:
            lab_f = frames[i].get("label") or {}
            lab_set = lab_f.get("set")
            lab_rep = lab_f.get("rep")
            key = (cls_id, lab_set, lab_rep)
            if cur_block and key == cur_key:
                cur_block.append(i)
            else:
                if cur_block:
                    rep_blocks.append(cur_block)
                cur_block = [i]
                cur_key = key
        elif cur_block and cur_key is not None and cur_key[2] is not None:
            lab_f = frames[i].get("label") or {}
            if lab_f.get("phase") == "do" and lab_f.get("set") == cur_key[1] and lab_f.get("rep") == cur_key[2]:
                continue
            rep_blocks.append(cur_block)
            cur_block = []
            cur_key = None
        else:
            if cur_block:
                rep_blocks.append(cur_block)
                cur_block = []
                cur_key = None
    if cur_block:
        rep_blocks.append(cur_block)

    rep_details = []
    per_label_reps: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "model_ok": 0, "rule_ok": 0})
    for r_id, idx in enumerate(rep_blocks):
        if not idx:
            continue
        cls_id = int(y[idx[0]])
        lbl = CLASSES[cls_id] if 0 <= cls_id < len(CLASSES) else "none"
        if lbl == "none":
            continue
        lbl_thr = float(thresholds_by_label.get(lbl, threshold))
        peak_p = float(probs[idx, cls_id].max())
        mean_p = float(probs[idx, cls_id].mean())
        model_hit = peak_p >= lbl_thr
        rep_s = max(0, idx[0] - 12)
        rep_e = min(n_frames, idx[-1] + 5)
        rule_hit = any(lbl in _extract_rule_targets(frames[i].get("rule")) for i in range(rep_s, rep_e))
        t_rel = round(float(frames[idx[0]].get("t", t0)) - t0, 1)
        per_label_reps[lbl]["total"] += 1
        if model_hit:
            per_label_reps[lbl]["model_ok"] += 1
        if rule_hit:
            per_label_reps[lbl]["rule_ok"] += 1
        rep_details.append({
            "rep": r_id + 1,
            "label": lbl,
            "name": NAMES.get(lbl, lbl),
            "time_s": t_rel,
            "start_frame": int(idx[0]),
            "end_frame": int(idx[-1]),
            "peak_prob": round(peak_p, 3),
            "mean_prob": round(mean_p, 3),
            "model_ok": model_hit,
            "rule_ok": rule_hit,
        })

    scheduled_targets = {
        CLASSES[int(v)] for v in y if 0 < int(v) < len(CLASSES)
    } | {
        str((f.get("label") or {}).get("target") or "")
        for f in frames
        if (f.get("label") or {}).get("phase") == "do"
        and str((f.get("label") or {}).get("target") or "") in CLASSES
        and str((f.get("label") or {}).get("target") or "") != "none"
    }

    gesture_summaries = []
    seen_labels: set[str] = set()
    for key, name in NAMES.items():
        task = BY_KEY[key]
        lbl = task.label
        if lbl == "none" or lbl not in CLASSES or lbl in seen_labels:
            continue
        if scheduled_targets and lbl not in scheduled_targets:
            continue
        seen_labels.add(lbl)
        c_idx = CLASSES.index(lbl)
        col = probs[:, c_idx]
        lbl_thr = float(thresholds_by_label.get(lbl, threshold))
        is_active = (lbl in active_classes) and (lbl not in disabled or lbl in specialists)
        if not is_active and per_label_reps[lbl]["total"] == 0 and float(col.max()) < 0.05:
            continue
        other_mask = (y == 0)
        false_fires = 0
        was_high = False
        for i in range(n_frames):
            if bool(other_mask[i]):
                high = float(col[i]) >= lbl_thr
                if high and not was_high:
                    false_fires += 1
                was_high = high
            else:
                was_high = False
        rep_stat = per_label_reps[lbl]
        total_r = rep_stat["total"]
        m_rate = (rep_stat["model_ok"] / total_r) if total_r > 0 else None
        r_rate = (rep_stat["rule_ok"] / total_r) if total_r > 0 else None
        gesture_summaries.append({
            "key": key,
            "label": lbl,
            "name": name,
            "trained": is_active,
            "reps": total_r,
            "model_hits": rep_stat["model_ok"],
            "rule_hits": rep_stat["rule_ok"],
            "model_rate": round(m_rate, 3) if m_rate is not None else None,
            "rule_rate": round(r_rate, 3) if r_rate is not None else None,
            "max_prob": round(float(col.max()), 3),
            "mean_prob": round(float(col.mean()), 3),
            "false_fires": false_fires,
        })

    gesture_summaries.sort(
        key=lambda g: (
            0 if g["reps"] > 0 else (1 if g["false_fires"] > 0 else 2),
            -g["reps"],
            -g["false_fires"],
            -g["max_prob"],
        )
    )

    bins = min(80, max(1, n_frames))
    timeline = []
    non_none_indices = (
        [CLASSES.index(t) for t in sorted(scheduled_targets) if t in CLASSES]
        if scheduled_targets
        else [i for i, c in enumerate(CLASSES) if c != "none"]
    )
    for b in range(bins):
        s_i = int(b * n_frames / bins)
        e_i = max(s_i + 1, int((b + 1) * n_frames / bins))
        chunk = probs[s_i:e_i]
        best_cls = "none"
        best_p = 0.0
        for ci in non_none_indices:
            p_val = float(chunk[:, ci].max())
            if p_val > best_p:
                best_p = p_val
                best_cls = CLASSES[ci]
        target_ids = [int(v) for v in y[s_i:e_i] if int(v) > 0]
        raw_do_targets = [
            str((frames[fi].get("label") or {}).get("target") or "")
            for fi in range(s_i, e_i)
            if (frames[fi].get("label") or {}).get("phase") == "do"
            and str((frames[fi].get("label") or {}).get("target") or "") in CLASSES
            and str((frames[fi].get("label") or {}).get("target") or "") != "none"
        ]
        if target_ids and target_ids[0] < len(CLASSES):
            target_lbl = CLASSES[target_ids[0]]
            zone = "judgment"
            zone_name = "판정 구간"
        elif raw_do_targets:
            target_lbl = "none"
            zone = "transition"
            zone_name = "전환 과정 (진입·해제 경계)"
        elif any(int(v) == 0 for v in y[s_i:e_i]) or any(
            (frames[fi].get("label") or {}).get("phase") in ("prep", "prepare", "rest", "free", "use")
            for fi in range(s_i, e_i)
        ):
            target_lbl = "none"
            zone = "transition"
            zone_name = "전환 과정 (준비·복귀 이동)"
        else:
            target_lbl = "idle"
            zone = "waiting"
            zone_name = "세트 전환 대기"
        lbl_thr = float(thresholds_by_label.get(best_cls, threshold))
        timeline.append({
            "bin": b,
            "prob": round(best_p, 3),
            "predicted": best_cls if best_p >= lbl_thr else "none",
            "predicted_name": NAMES.get(best_cls, best_cls) if best_p >= lbl_thr else "중립",
            "target": target_lbl,
            "target_name": (
                "세트 전환 대기"
                if target_lbl == "idle"
                else (f"판정 구간 · {NAMES.get(target_lbl, target_lbl)}" if target_lbl != "none" else zone_name)
            ),
            "zone": zone,
            "zone_name": zone_name,
            "confirmed": best_p >= lbl_thr,
        })

    total_reps = sum(g["reps"] for g in gesture_summaries)
    total_hits = sum(g["model_hits"] for g in gesture_summaries)
    total_rule_hits = sum(g["rule_hits"] for g in gesture_summaries)
    total_ff = sum(g["false_fires"] for g in gesture_summaries)
    src_tag = "보정본" if using_cleaned else "원본"
    if total_reps > 0:
        summary_text = (
            f"{path.name} [{src_tag}] ({duration_s:.1f}초, {n_frames}프레임) · "
            f"판정 구간 모델 인식 {total_hits}/{total_reps}회 ({total_hits / total_reps * 100:.0f}%) vs "
            f"규칙 {total_rule_hits}/{total_reps}회 ({total_rule_hits / total_reps * 100:.0f}%) · "
            f"전환 과정 간섭 억제 완료"
        )
    else:
        summary_text = (
            f"{path.name} [{src_tag}] ({duration_s:.1f}초, {n_frames}프레임 자유/이동 녹화) · "
            f"중립 구간 모델 오작동 {total_ff}회 (임계값 {threshold:.2f})"
        )

    pb_frames = _build_playback_frames(
        raw_frames,
        session.get("features"),
        probs,
        thresholds_by_label,
        threshold,
        eval_info.get("issues", []),
        y_labels=y,
    )

    return {
        "file": path.name,
        "using_cleaned": using_cleaned,
        "frames": n_frames,
        "duration_s": round(duration_s, 1),
        "threshold": threshold,
        "has_model": True,
        "summary": summary_text,
        "recorded_mode_label": eval_info.get("recorded_mode_label", "가이드 녹화"),
        "target_gestures_text": eval_info.get("target_gestures_text", ""),
        "target_short_text": eval_info.get("target_short_text", ""),
        "target_conditions_text": eval_info.get("target_conditions_text", ""),
        "target_instruction_text": eval_info.get("target_instruction_text", ""),
        "pose_coverage_pct": eval_info.get("pose_coverage_pct", 0),
        "gestures": gesture_summaries,
        "timeline": timeline,
        "reps": rep_details,
        "playback_frames": pb_frames,
        "quality": eval_info,
        "augmentation": eval_info["augmentation"],
    }


def evaluate_gesture_judgment(
    gesture_key: str,
    rec_dir: Path = REC_DIR,
    model: Path = MODEL,
) -> Dict[str, Any]:
    """Run rule + AI model judgment verification across recordings of a specific gesture."""
    task = BY_KEY.get(gesture_key)
    if task is None:
        return {"ok": False, "key": gesture_key, "summary": f"알 수 없는 동작: {gesture_key}"}
    summary = dataset_summary(rec_dir=rec_dir, compute_quality=False)
    matching = [
        f
        for f in sorted(
            [f for f in summary.get("files", []) if "error" not in f and f.get("selected", True)],
            key=lambda f: int(f.get("mtime", 0)),
            reverse=True,
        )
        if (
            sum((f.get("reps", {}).get(gesture_key) or {}).values()) > 0
            or float((f.get("free_minutes") or {}).get(gesture_key, 0.0)) > 0.0
            or gesture_key in (f.get("target_gestures") or [])
        )
    ]
    if not matching:
        return {
            "ok": False,
            "key": gesture_key,
            "summary": f"'{task.name}' 동작이 포함된 녹화 파일이 없습니다.",
        }
    total_reps = 0
    model_hits = 0
    rule_hits = 0
    false_fires = 0
    file_notes: List[str] = []
    latest_sim: Optional[Dict[str, Any]] = None
    target_lbl = task.label
    for f_info in matching[:5]:
        fname = str(f_info["file"])
        try:
            sim = simulate_model_on_recording(fname, rec_dir=rec_dir, model=model, use_cleaned=True)
            if latest_sim is None:
                latest_sim = sim
            g_entry = next((g for g in sim.get("gestures", []) if g.get("label") == target_lbl or g.get("key") == gesture_key), None)
            if g_entry and int(g_entry.get("reps", 0)) > 0:
                r_cnt = int(g_entry["reps"])
                m_cnt = int(g_entry.get("model_hits", 0))
                rl_cnt = int(g_entry.get("rule_hits", 0))
                total_reps += r_cnt
                model_hits += m_cnt
                rule_hits += rl_cnt
                false_fires += int(g_entry.get("false_fires", 0))
                cond = str(f_info.get("target_conditions_text") or "").strip()
                cond_tag = f"({cond})" if cond else ""
                file_notes.append(f"{fname}{cond_tag}: AI {m_cnt}/{r_cnt} · 규칙 {rl_cnt}/{r_cnt}")
        except Exception:
            continue
    if total_reps > 0:
        m_pct = round(model_hits / total_reps * 100)
        r_pct = round(rule_hits / total_reps * 100)
        headline = (
            f"[{task.name} 판정 검증] 최근 {len(file_notes)}개 녹화 총 {total_reps}회 — "
            f"AI 모델 {model_hits}/{total_reps}회 ({m_pct}%) · 규칙 {rule_hits}/{total_reps}회 ({r_pct}%)"
            + (f" · 오작동 {false_fires}회" if false_fires else "")
        )
    else:
        headline = f"[{task.name} 판정 검증] 최근 녹화 {len(matching[:5])}개 점검 완료"
    return {
        "ok": True,
        "key": gesture_key,
        "latest_file": str(matching[0]["file"]),
        "total_reps": total_reps,
        "model_hits": model_hits,
        "rule_hits": rule_hits,
        "summary": headline,
        "detail": " / ".join(file_notes[:3]),
        "latest_sim": latest_sim,
    }


# ---------------------------------------------------------------- app self-diagnostics & bug detection
def run_app_diagnostics(
    rec_dir: Path = REC_DIR,
    model: Path = MODEL,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Inspect model integrity, config schema, MediaPipe assets, dataset quality, and recent logs for bugs."""
    from src.gesture_engine import DEFAULT_CONFIG, DEFAULT_CONFIG_PATH
    from src.gesture_models import load_single, read_bank
    from src.tcn_features import FEATURES, FEATURE_VERSION

    checks: List[Dict[str, Any]] = []

    # 1. Config check
    cfg_file = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    if cfg_file.is_file():
        try:
            raw_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            bad_keys = []
            for k in ("pinch_threshold", "release_threshold", "click_threshold", "min_quality_score"):
                if k in raw_cfg:
                    val = float(raw_cfg[k])
                    if not (0.0 < val <= 1.0):
                        bad_keys.append(f"{k}={val}")
            if bad_keys:
                checks.append({
                    "name": "설정 파일 (config/gestures.json)",
                    "status": "warn",
                    "detail": f"비정상 임계값 감지: {', '.join(bad_keys)}",
                })
            else:
                checks.append({
                    "name": "설정 파일 (config/gestures.json)",
                    "status": "ok",
                    "detail": f"정상 ({len(raw_cfg)}개 항목 로드됨)",
                })
        except Exception as exc:
            checks.append({
                "name": "설정 파일 (config/gestures.json)",
                "status": "fail",
                "detail": f"JSON 파싱 오류: {exc}",
            })
    else:
        checks.append({
            "name": "설정 파일 (config/gestures.json)",
            "status": "ok",
            "detail": f"기본 설정 사용 중 ({len(DEFAULT_CONFIG)}개 기본값)",
        })

    # 2. MediaPipe hand landmarker asset check
    mp_model = ROOT / "models" / "hand_landmarker.task"
    if mp_model.is_file() and mp_model.stat().st_size > 100_000:
        checks.append({
            "name": "MediaPipe 손 추적 모델",
            "status": "ok",
            "detail": f"{mp_model.name} ({mp_model.stat().st_size / (1024 * 1024):.1f} MB)",
        })
    else:
        checks.append({
            "name": "MediaPipe 손 추적 모델",
            "status": "fail",
            "detail": "models/hand_landmarker.task 파일이 없거나 손상되었습니다.",
        })

    # 3. TCN ONNX & Specialist Bank live inference check
    card_path = model.with_suffix(".json")
    bank_dir = model.parent / "gestures"
    if model.is_file() and card_path.is_file():
        try:
            import numpy as np
            pred, card = load_single(model, FEATURE_VERSION)
            win = int(card.get("window", 15))
            n_feat = int(card.get("features", FEATURES))
            dummy = np.zeros((1, win, n_feat), dtype=np.float32)
            out = pred(dummy)
            disabled = card.get("disabled_gestures") or []
            specialists = read_bank(bank_dir) if (bank_dir / "manifest.json").is_file() else {}
            spec_info = f", 개별 동작 모델 {len(specialists)}개" if specialists else ""
            dis_info = f", 초기화됨 {len(disabled)}개" if disabled else ""
            if out.shape == (len(card.get("classes", [])),) and np.isfinite(out).all():
                checks.append({
                    "name": "TCN 동작 모델 (ONNX 추론 검증)",
                    "status": "ok",
                    "detail": f"정상 추론 확인 (클래스 {len(card.get('classes', []))}개{spec_info}{dis_info})",
                })
            else:
                checks.append({
                    "name": "TCN 동작 모델 (ONNX 추론 검증)",
                    "status": "fail",
                    "detail": f"비정상 출력 형태 또는 NaN 감지: shape={out.shape}",
                })
        except Exception as exc:
            checks.append({
                "name": "TCN 동작 모델 (ONNX 추론 검증)",
                "status": "fail",
                "detail": f"모델 로드/추론 오류: {exc}",
            })
    else:
        checks.append({
            "name": "TCN 동작 모델 (ONNX 추론 검증)",
            "status": "warn",
            "detail": "아직 학습된 gesture_tcn.onnx 모델이 없습니다.",
        })

    # 3b. Spatial MLP (96-dim 0-lag posture model + per-gesture specialist MLPs) live inference check
    sp_path = model.parent / "spatial_mlp.json"
    if sp_path.is_file() or (bank_dir / "manifest.json").is_file():
        try:
            import numpy as np
            from src.spatial_features import SPATIAL_FEATURES
            from src.spatial_mlp import SpatialMlpModel, compose_spatial_mlp
            base_sp = SpatialMlpModel.load(sp_path) if sp_path.is_file() else None
            sp_model = compose_spatial_mlp(model, base_sp)
            if sp_model is not None:
                sp_out = sp_model.predict_proba(np.zeros((1, SPATIAL_FEATURES), dtype=np.float32))
                test_acc = sp_model.metadata.get("test_accuracy")
                acc_str = f", 테스트 정확도 {test_acc:.1f}%" if isinstance(test_acc, (int, float)) else ""
                spec_cnt = len(sp_model.specialists)
                spec_str = f", 동작별 전용 MLP {spec_cnt}개" if spec_cnt > 0 else ""
                if sp_out.shape == (1, len(sp_model.classes)) and np.isfinite(sp_out).all():
                    checks.append({
                        "name": "Spatial MLP 자세 모델 (96차원 0-지연 가드)",
                        "status": "ok",
                        "detail": f"정상 추론 확인 (클래스 {len(sp_model.classes)}개, 은닉층 {sp_model.hidden_units}{spec_str}{acc_str})",
                    })
                else:
                    checks.append({
                        "name": "Spatial MLP 자세 모델 (96차원 0-지연 가드)",
                        "status": "fail",
                        "detail": f"비정상 출력 형태 감지: shape={sp_out.shape}",
                    })
            else:
                checks.append({
                    "name": "Spatial MLP 자세 모델 (96차원 0-지연 가드)",
                    "status": "warn",
                    "detail": "아직 학습된 models/spatial_mlp.json 모델이 없습니다.",
                })
        except Exception as exc:
            checks.append({
                "name": "Spatial MLP 자세 모델 (96차원 0-지연 가드)",
                "status": "fail",
                "detail": f"Spatial MLP 로드/추론 오류: {exc}",
            })
    else:
        checks.append({
            "name": "Spatial MLP 자세 모델 (96차원 0-지연 가드)",
            "status": "warn",
            "detail": "아직 학습된 models/spatial_mlp.json 모델이 없습니다.",
        })

    # 3c. GPU / CPU training compute capability check
    gpu_cap = detect_gpu_training_capability()
    eff_dev = resolve_effective_training_device(rec_dir= Path(rec_dir))
    if gpu_cap.get("cuda_available"):
        dev_mode_str = "GPU 가속 선택됨" if eff_dev == "gpu" else "CPU 학습 선택됨"
        checks.append({
            "name": "학습 연산 장치 (GPU / CPU)",
            "status": "ok",
            "detail": f"{gpu_cap.get('status_label')} · {dev_mode_str}",
        })
    elif gpu_cap.get("can_install_cuda_torch"):
        checks.append({
            "name": "학습 연산 장치 (GPU / CPU)",
            "status": "warn",
            "detail": f"{gpu_cap.get('status_label')} — 학습 센터에서 [GPU 가속 활성화]로 CUDA 사용 가능",
        })
    else:
        checks.append({
            "name": "학습 연산 장치 (GPU / CPU)",
            "status": "ok",
            "detail": f"{gpu_cap.get('status_label')} · CPU 학습 모드 사용 중",
        })

    # 4. Recordings & quality integrity check
    folder = Path(rec_dir)
    rec_files = sorted(folder.glob("*.jsonl.gz")) if folder.is_dir() else []
    if not rec_files:
        checks.append({
            "name": "녹화 데이터셋 품질 및 무결성",
            "status": "warn",
            "detail": "녹화된 .jsonl.gz 데이터 파일이 없습니다.",
        })
    else:
        excluded = set(load_training_selection(folder).get("excluded", []))
        broken, failed_q, warn_q, ok_q = [], [], [], 0
        for p in rec_files:
            try:
                _summarise_file(p)
            except Exception:
                broken.append(p.name)
                continue
            q = get_recording_quality(p, compute_if_missing=True)
            st = q.get("status")
            if st == "fail":
                failed_q.append(p.name)
            elif st == "warning":
                warn_q.append(p.name)
            else:
                ok_q += 1
        active_count = len([p for p in rec_files if p.name not in excluded and p.name not in broken])
        if broken:
            checks.append({
                "name": "녹화 데이터셋 품질 및 무결성",
                "status": "fail",
                "detail": f"손상된 파일 {len(broken)}개 발견: {', '.join(broken[:3])}",
            })
        elif failed_q:
            unexcluded_fails = [f for f in failed_q if f not in excluded]
            status_code = "warn" if unexcluded_fails else "ok"
            checks.append({
                "name": "녹화 데이터셋 품질 및 무결성",
                "status": status_code,
                "detail": f"총 {len(rec_files)}개 중 학습 선택 {active_count}개 (양호 {ok_q}, 주의 {len(warn_q)}, 불량 {len(failed_q)})",
            })
        else:
            checks.append({
                "name": "녹화 데이터셋 품질 및 무결성",
                "status": "ok",
                "detail": f"총 {len(rec_files)}개 파일 무결성 통과 · 학습 선택 {active_count}개 (양호 {ok_q}, 주의 {len(warn_q)})",
            })

    # 5. PyTorch / CUDA training environment check
    try:
        import torch
        cuda_ok = torch.cuda.is_available()
        dev = torch.cuda.get_device_name(0) if cuda_ok else "CPU"
        checks.append({
            "name": "PyTorch 학습 엔진 및 가속기",
            "status": "ok",
            "detail": f"PyTorch {torch.__version__} · {'CUDA GPU (' + dev + ')' if cuda_ok else 'CPU 모드'}",
        })
    except Exception as exc:
        checks.append({
            "name": "PyTorch 학습 엔진 및 가속기",
            "status": "fail",
            "detail": f"PyTorch 로드 실패: {exc}",
        })

    # 6. Recent failure/bug log inspection
    log_dir = ROOT / "logs"
    fail_logs = sorted(log_dir.glob("failures-*.jsonl")) if log_dir.is_dir() else []
    recent_failures = 0
    if fail_logs:
        try:
            lines = fail_logs[-1].read_text(encoding="utf-8", errors="ignore").splitlines()
            recent_failures = len(lines)
        except Exception:
            pass
    checks.append({
        "name": "런타임 오류 및 오작동 기록",
        "status": "ok" if recent_failures == 0 else "warn",
        "detail": "최근 기록된 런타임 예외 없음" if recent_failures == 0 else f"최근 로그에 사용자 오작동 표시 {recent_failures}건 기록됨",
    })

    n_fail = sum(1 for c in checks if c["status"] == "fail")
    n_warn = sum(1 for c in checks if c["status"] == "warn")
    overall = "fail" if n_fail > 0 else ("warn" if n_warn > 0 else "ok")
    summary_msg = (
        f"진단 완료 ({time.strftime('%H:%M:%S')}): "
        f"정상 {len(checks) - n_fail - n_warn} · 주의 {n_warn} · 오류 {n_fail}"
    )
    return {
        "overall": overall,
        "summary": summary_msg,
        "checks": checks,
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

