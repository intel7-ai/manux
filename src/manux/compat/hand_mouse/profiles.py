"""
User profiles (2026-11-04): the values the calibration wizard measured, stored per person.

config/profiles/<name>.json   {"name", "created", "updates": {key: number}, "report": {...}}
config/active_profile.txt     name of the profile in use (absent / empty = none)

A profile only OVERLAYS the base config (defaults < config/gestures.json < profile). Selecting
another profile or none never edits gestures.json. Profiles hold measured numbers only (no camera
images); the folder is git-ignored because a profile name can be a person's name.

2026-11-11 (the person's rule): no profile, or a profile nothing was changed in, IS the app's default state. A profile
keeps only what differs from the default:
    "updates" in <name>.json                 engine settings changed while the profile is on (any JSON value)
    config/profiles/bindings/<name>.json     gesture -> command bindings that differ (src/action_bindings.py)
    the app's settings under profile/<name>/ and center/<name>/   screen options (src/qt_dashboard.py)
so a change made to the defaults (in code or the shared config files) reaches every profile that did not touch it.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Process-wide choice for this run (set by `main.py --profile`): None = use active_profile.txt,
# "" = no profile at all, "name" = that profile.
RUN_OVERRIDE: Optional[str] = None

NAME_RE = re.compile(r"^[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ _\-.]{1,32}$")
ACTIVE_FILE = "active_profile.txt"
PROFILES_DIR = "profiles"


def valid_name(name: str) -> bool:
    name = (name or "").strip()
    return bool(NAME_RE.match(name)) and name not in (".", "..") and not name.endswith(".")


def profiles_dir(config_dir: Path) -> Path:
    return Path(config_dir) / PROFILES_DIR


def profile_path(config_dir: Path, name: str) -> Path:
    if not valid_name(name):
        raise ValueError(f"사용할 수 없는 프로필 이름입니다: {name!r} (한글·영문·숫자·공백·-_. 32자 이하)")
    return profiles_dir(config_dir) / f"{name.strip()}.json"


def list_profiles(config_dir: Path) -> List[str]:
    try:
        return sorted(p.stem for p in profiles_dir(config_dir).glob("*.json") if valid_name(p.stem))
    except OSError:
        return []


BINDINGS_DIR = "bindings"      # config/profiles/bindings/<name>.json (src/action_bindings.bindings_path)
EXPORT_FORMAT = "manux-hand-mouse-profile"


def bindings_file(config_dir: Path, name: str) -> Path:
    profile_path(config_dir, name)          # validates the name
    return profiles_dir(config_dir) / BINDINGS_DIR / f"{name.strip()}.json"


def _write(path: Path, doc: Dict[str, Any]) -> None:
    """Whole or not at all (the runtime thread and the screens both save profiles)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def save_profile(config_dir: Path, name: str, updates: Dict[str, float],
                 report: Optional[Dict[str, Any]] = None) -> Path:
    """Write (or replace) a profile from measured numbers (the calibration wizard); a replaced one is kept as
    `<name>.json.bak`."""
    path = profile_path(config_dir, name)
    clean = {str(k): float(v) for k, v in updates.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_name(path.name + ".bak").write_bytes(path.read_bytes())
    payload = {"name": name.strip(), "created": time.strftime("%Y-%m-%d %H:%M:%S"),
               "updates": clean, "report": report or {}}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def create_profile(config_dir: Path, name: str) -> Path:
    """A new profile with nothing changed: the app's default state until the person changes something in it."""
    path = profile_path(config_dir, name)
    if path.exists():
        raise ValueError(f"'{name.strip()}' 프로필이 이미 있어요.")
    _write(path, {"name": name.strip(), "created": time.strftime("%Y-%m-%d %H:%M:%S"), "updates": {}, "report": {}})
    return path


def load_values(config_dir: Path, name: str) -> Optional[Dict[str, Any]]:
    """Every engine setting the profile changes, as stored (numbers, switches, words, lists). None = no such profile."""
    doc = load_profile_document(config_dir, name)
    if doc is None:
        return None
    raw = doc.get("updates", {})
    return dict(raw) if isinstance(raw, dict) else {}


def save_values(config_dir: Path, name: str, updates: Dict[str, Any]) -> Path:
    """Replace the engine settings the profile changes (the rest of its file stays). The profile must exist: one
    that was just renamed or deleted is not brought back by a save that was on its way."""
    path = profile_path(config_dir, name)
    doc = load_profile_document(config_dir, name)
    if doc is None:
        raise ValueError(f"'{name}' 프로필이 없어요.")
    doc["updates"] = dict(updates)
    _write(path, doc)
    return path


def rename_profile(config_dir: Path, old: str, new: str) -> Path:
    """The profile under another name (its bindings file with it; the active choice follows)."""
    src, dst = profile_path(config_dir, old), profile_path(config_dir, new)
    if not src.exists():
        raise ValueError(f"'{old}' 프로필이 없어요.")
    if dst.exists():
        raise ValueError(f"'{new.strip()}' 프로필이 이미 있어요.")
    doc = load_profile_document(config_dir, old) or {}
    doc["name"] = new.strip()
    _write(dst, doc)
    src.unlink()
    b_old, b_new = bindings_file(config_dir, old), bindings_file(config_dir, new)
    if b_old.exists():
        b_new.parent.mkdir(parents=True, exist_ok=True)
        b_old.replace(b_new)
    if get_active(config_dir, ignore_override=True) == old.strip():
        set_active(config_dir, new)
    return dst


def changed_counts(config_dir: Path, name: str) -> Dict[str, int]:
    """How much the profile differs from the default state: engine settings and gesture bindings (for the list)."""
    values = load_values(config_dir, name) or {}
    bindings = 0
    try:
        raw = json.loads(bindings_file(config_dir, name).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            bindings = sum(len(raw.get(k) or ()) for k in ("bindings", "composite_gestures", "custom_motions", "deleted"))
    except (OSError, ValueError):
        pass
    return {"settings": len(values), "bindings": bindings}


def export_document(config_dir: Path, name: str) -> Dict[str, Any]:
    """The profile as one JSON document (its file and its bindings); the screens add their own parts."""
    doc = load_profile_document(config_dir, name)
    if doc is None:
        raise ValueError(f"'{name}' 프로필이 없어요.")
    out: Dict[str, Any] = {"format": EXPORT_FORMAT, "version": 1, "name": name.strip(),
                           "exported": time.strftime("%Y-%m-%d %H:%M:%S"), "profile": doc, "bindings": {}}
    try:
        raw = json.loads(bindings_file(config_dir, name).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            out["bindings"] = raw
    except (OSError, ValueError):
        pass
    return out


def free_name(config_dir: Path, wanted: str) -> str:
    """`wanted`, or "wanted 2", "wanted 3" ... when taken."""
    base = (wanted or "").strip() or "가져온 프로필"
    if not valid_name(base):
        base = "가져온 프로필"
    names = set(list_profiles(config_dir))
    name, n = base, 2
    while name in names:
        name, n = f"{base[:28]} {n}", n + 1
    return name


def import_document(config_dir: Path, doc: Dict[str, Any], name: Optional[str] = None) -> str:
    """Make a profile from an exported document; returns its name (a taken name gets a number)."""
    if not isinstance(doc, dict) or doc.get("format") != EXPORT_FORMAT:
        raise ValueError("이 앱에서 내보낸 프로필 파일이 아니에요.")
    inner = doc.get("profile") if isinstance(doc.get("profile"), dict) else {}
    final = free_name(config_dir, name or str(doc.get("name") or inner.get("name") or ""))
    body = dict(inner)
    body["name"] = final
    body.setdefault("created", time.strftime("%Y-%m-%d %H:%M:%S"))
    if not isinstance(body.get("updates"), dict):
        body["updates"] = {}
    _write(profile_path(config_dir, final), body)
    bindings = doc.get("bindings")
    if isinstance(bindings, dict) and bindings:
        _write(bindings_file(config_dir, final), bindings)
    return final


def load_profile(config_dir: Path, name: str, known_keys: Optional[set] = None) -> Optional[Dict[str, float]]:
    """The numeric overrides of a profile, or None when it is missing / unreadable.
    With `known_keys`, unknown keys are dropped (a hand-edited file cannot inject arbitrary config)."""
    try:
        data = json.loads(profile_path(config_dir, name).read_text(encoding="utf-8"))
        raw = data.get("updates", {})
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(raw, dict):
        return None
    return {k: float(v) for k, v in raw.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and (known_keys is None or k in known_keys)}


def load_profile_document(config_dir: Path, name: str) -> Optional[Dict[str, Any]]:
    """Return the full JSON document of a profile (including `report`, `custom_gestures`, `rollback_points`)."""
    try:
        data = json.loads(profile_path(config_dir, name).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, AttributeError):
        return None


def save_profile_adaptation(
    config_dir: Path,
    name: str,
    *,
    onboarding_adaptation: Optional[Dict[str, Any]] = None,
    custom_gesture: Optional[Dict[str, Any]] = None,
    rollback_point: Optional[Dict[str, Any]] = None,
) -> Path:
    """Persist Stage 8 user onboarding prototypes/OOD thresholds, custom gestures, and rollback points into a profile."""
    path = profile_path(config_dir, name)
    doc = load_profile_document(config_dir, name) or {
        "name": name.strip(),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updates": {},
        "report": {},
    }
    if onboarding_adaptation:
        report = doc.setdefault("report", {})
        if isinstance(report, dict):
            report["onboarding_adaptation"] = dict(onboarding_adaptation)
    if custom_gesture and isinstance(custom_gesture.get("label"), str):
        custom_map = doc.setdefault("custom_gestures", {})
        if isinstance(custom_map, dict):
            custom_map[str(custom_gesture["label"])] = dict(custom_gesture)
    if rollback_point:
        rb_list = doc.setdefault("rollback_points", [])
        if isinstance(rb_list, list):
            rb_list.append(dict(rollback_point))
            if len(rb_list) > 10:
                del rb_list[:-10]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def apply_profile_adaptation_to_model(
    config_dir: Path,
    name: str,
    spatial_model: Any,
) -> Dict[str, Any]:
    """Overlay a profile's Stage 8 onboarding prototypes, OOD thresholds, and custom gestures onto `spatial_model`."""
    import numpy as np

    doc = load_profile_document(config_dir, name)
    if not doc or spatial_model is None:
        return {"applied": False, "reason": "no_profile_or_model"}
    spatial_model.create_rollback_point(f"pre_profile_{name}")
    report = doc.get("report") if isinstance(doc.get("report"), dict) else {}
    onboard = report.get("onboarding_adaptation") if isinstance(report.get("onboarding_adaptation"), dict) else {}
    updated_protos: List[str] = []
    if onboard:
        u_protos = onboard.get("user_prototypes") if isinstance(onboard.get("user_prototypes"), dict) else {}
        if u_protos and getattr(spatial_model, "prototypes", None) is not None:
            for cls_name, vec in u_protos.items():
                if cls_name in spatial_model.base_classes:
                    idx = spatial_model.base_classes.index(cls_name)
                    arr = np.asarray(vec, dtype=np.float64).reshape(-1)
                    if arr.size == spatial_model.hidden_units:
                        arr = arr / max(float(np.linalg.norm(arr)), 1e-8)
                        spatial_model.prototypes[idx] = arr
                        updated_protos.append(cls_name)
            if updated_protos:
                spatial_model._prototypes_adapted = True
                spatial_model.metadata["prototypes_adapted"] = True
        u_ood = onboard.get("user_ood_thresholds") if isinstance(onboard.get("user_ood_thresholds"), dict) else {}
        for k, v in u_ood.items():
            if isinstance(v, (int, float)):
                spatial_model.ood_thresholds[str(k)] = float(v)

    custom_loaded: List[str] = []
    custom_map = doc.get("custom_gestures") if isinstance(doc.get("custom_gestures"), dict) else {}
    for c_label, c_info in custom_map.items():
        if not isinstance(c_info, dict) or "prototype" not in c_info:
            continue
        arr = np.asarray(c_info["prototype"], dtype=np.float64).reshape(-1)
        if arr.size != spatial_model.hidden_units:
            continue
        arr = arr / max(float(np.linalg.norm(arr)), 1e-8)
        spatial_model.custom_prototypes[str(c_label)] = arr
        spatial_model._anchor_custom_prototypes[str(c_label)] = arr.copy()
        if isinstance(c_info.get("ood_threshold"), (int, float)):
            spatial_model.ood_thresholds[str(c_label)] = float(c_info["ood_threshold"])
        if str(c_label) not in spatial_model.classes:
            spatial_model.classes = tuple(list(spatial_model.classes) + [str(c_label)])
        custom_loaded.append(str(c_label))

    return {
        "applied": bool(updated_protos or custom_loaded),
        "profile": name,
        "updated_prototypes": updated_protos,
        "custom_gestures": custom_loaded,
    }


def delete_profile(config_dir: Path, name: str) -> bool:
    path = profile_path(config_dir, name)
    if not path.exists():
        return False
    path.unlink()
    for extra in (bindings_file(config_dir, name), path.with_name(path.name + ".bak")):
        try:
            extra.unlink()
        except OSError:
            pass
    if get_active(config_dir, ignore_override=True) == name.strip():
        set_active(config_dir, None)
    return True


def get_active(config_dir: Path, ignore_override: bool = False) -> Optional[str]:
    """The profile in use: this run's override, else active_profile.txt. None = no profile."""
    if RUN_OVERRIDE is not None and not ignore_override:
        return RUN_OVERRIDE or None
    try:
        name = (Path(config_dir) / ACTIVE_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name if valid_name(name) else None


def set_active(config_dir: Path, name: Optional[str]) -> None:
    """Remember the chosen profile (None / "" = no profile)."""
    path = Path(config_dir) / ACTIVE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if name:
        profile_path(config_dir, name)   # validates the name
        path.write_text(name.strip(), encoding="utf-8")
    else:
        path.write_text("", encoding="utf-8")
