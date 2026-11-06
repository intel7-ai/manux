"""Per-task decisions, bound to the model actually reviewed by the user."""
from src.gesture_catalog import BY_KEY

MODES = ("rules", "shadow", "apply")
MODE_NAMES = ("기존 규칙", "시험 · 기록만", "TCN 적용")


def mode_for(task, info, policies=None):
    label = BY_KEY[task].label if task in BY_KEY else task
    if label == "none" or label not in info.get("classes", []):
        return "rules"
    entry = (policies or {}).get(task, {})
    if isinstance(entry, dict) and entry.get("mode") in MODES:
        # Turning a motion off remains off even after retraining. Application consent
        # belongs to one model version, never silently to its replacement.
        if entry["mode"] == "rules" or entry.get("model_id") == info.get("class_model_ids", {}).get(label):
            return entry["mode"]
    return "shadow" if label in info.get("selected_classes", []) else "apply"


def policy_entry(task, mode, info):
    if task not in BY_KEY or BY_KEY[task].free or mode not in MODES:
        raise ValueError("지원하지 않는 TCN 동작 설정입니다.")
    label = BY_KEY[task].label
    if mode != "rules" and label not in info.get("classes", []):
        raise ValueError("이 동작을 먼저 학습하세요.")
    return {"mode": mode, "model_id": info.get("class_model_ids", {}).get(label)}
