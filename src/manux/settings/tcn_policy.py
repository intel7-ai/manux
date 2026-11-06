"""Per-gesture choices, bound to immutable classifier component identities."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from uuid import uuid4

MODES = ("rules", "shadow", "apply")


class GesturePolicies:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
            self.rows = rows if isinstance(rows, dict) else {}
        except (OSError, ValueError):
            self.rows = {}

    def describe(self, card):
        result = {}
        with self._lock:
            for label, gesture in card.get("gestures", {}).items():
                model_id = str(card.get("class_model_ids", {}).get(label) or card.get("model_sha256") or "")
                default = card.get("class_default_modes", {}).get(label, "apply")
                default = default if default in MODES else "shadow"
                saved = self.rows.get(gesture, {})
                mode = saved.get("mode") if isinstance(saved, dict) and saved.get("model_id") == model_id else default
                result[gesture] = {"mode": mode if mode in MODES else default, "model_id": model_id}
        return result

    def modes(self, card):
        return {gesture: row["mode"] for gesture, row in self.describe(card).items()}

    def set(self, card, gesture, mode, expected_model_id):
        if mode not in MODES:
            raise ValueError("규칙·시험·적용 중에서 선택하세요.")
        with self._lock:
            current = self.describe(card).get(gesture)
            if not current or not expected_model_id or current["model_id"] != expected_model_id:
                raise ValueError("모델이 바뀌었습니다. 새로고침 후 다시 선택하세요.")
            rows = {**self.rows, gesture: {"mode": mode, "model_id": expected_model_id}}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}-{uuid4().hex}.tmp")
            try:
                with temporary.open("x", encoding="utf-8") as stream:
                    json.dump(rows, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                self.rows = rows
            finally:
                temporary.unlink(missing_ok=True)
