"""Presentation of a worker snapshot; no Qt or training dependencies."""
from __future__ import annotations

import math
import time


def duration(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return (f"{hours}시간 {minutes}분" if hours else f"{minutes}분 {seconds}초" if minutes else f"{seconds}초")


def view(state, now=None):
    now = time.time() if now is None else now
    running, success = bool(state.get("running")), bool(state.get("success"))
    status = ("stopping" if running and state.get("cancelled") else "running" if running else
              "success" if success else "cancelled" if state.get("cancelled") else
              "error" if state.get("error") else "idle")
    elapsed = max(0., (state.get("finished") or now) - state.get("started", now))
    progress = 1. if success else min(.99, max(0., state.get("progress", 0.)))
    epochs, epoch = int(state.get("epochs", 0)), int(state.get("epoch", 0))
    steps, step = max(1, int(state.get("steps", 1))), max(1, int(state.get("step", 1)))
    samples = [v for v in state.get("epoch_seconds", [])[-8:] if math.isfinite(v) and v > 0]
    left = max(0, (steps - step) * epochs + epochs - epoch)
    remaining = finish = "계산 중"
    eta = -1
    hint = "몇 차례 학습한 뒤 실제 반복 속도로 예상합니다. 검증·저장 시간은 별도입니다."
    if status == "running" and state.get("phase") == "training" and len(samples) >= 2 and left:
        eta = max(1, math.ceil(left * sum(samples) / len(samples) - max(0, now - state.get("last_epoch_at", now))))
        remaining = "약 " + duration(eta)
        finish = time.strftime("%H:%M:%S", time.localtime(now + eta))
        hint = "학습 반복의 완료 예상입니다. 별도 평가·저장과 PC 부하에 따라 최종 완료 시각이 달라집니다."
    elif status == "running" and state.get("phase") in ("evaluating", "saving"):
        remaining = finish = "검증·저장 중"
        hint = "모든 동작의 별도 평가와 모델 저장이 끝나야 100%로 표시합니다."
    elif not running:
        remaining = "완료" if success else "—"
        finish = time.strftime("%H:%M:%S", time.localtime(state.get("finished") or now)) if success else "—"
    losses = list(state.get("losses", []))[-120:]
    return {
        "phase": state.get("phase", "idle"), "skipped": state.get("skipped", 0),
        "log": "\n".join(map(str, state.get("log", []))) if isinstance(state.get("log"), list) else str(state.get("log") or ""),
        "status": status, "statusText": {"idle": "학습 대기", "running": "학습 진행 중", "stopping": "중단 중",
            "success": "학습 완료", "cancelled": "학습 중단", "error": "학습 실패"}[status],
        "stage": state.get("stage") or "학습센터에서 동작을 선택하세요", "progress": progress,
        "percent": 100 if success else min(99, round(progress * 100)), "elapsed": duration(elapsed),
        "remaining": remaining, "finish": finish, "etaSeconds": eta, "etaHint": hint,
        "epoch": f"{epoch} / {epochs}" if epochs else "—", "stageCount": f"{step} / {steps}",
        "loss": f"{state['loss']:.4f}" if state.get("loss") is not None else "—", "losses": losses,
        "bestLoss": f"{min(losses):.4f}" if losses else "—",
        "data": (f"학습 녹화 {state['train_files']}개 · 평가 녹화 {state.get('eval_files', 0)}개 · "
                 f"자세 {state.get('conditions', 0)}개 · 관측 프레임 {state.get('frames', 0):,}개") if "train_files" in state else "녹화 자료를 읽으면 수집량이 표시됩니다.",
        "restored": bool(state.get("restored")),
        "tasks": " · ".join(state.get("task_names", [])) or "전체 학습 가능한 동작",
        "events": list(state.get("events", []))[-120:], "error": state.get("error", ""),
        "cancellable": status == "running",
    }
