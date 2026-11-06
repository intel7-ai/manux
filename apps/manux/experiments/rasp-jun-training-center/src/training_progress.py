"""Presentation of a training job, including a speed-based (not promised) completion estimate."""
import math
import time

from src.gesture_catalog import NAMES


def duration(seconds):
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}시간 {minutes}분" if hours else f"{minutes}분 {seconds:02d}초"


def progress_details(state, now=None):
    now = time.time() if now is None else now
    started = state.get("started")
    status = ("error" if state.get("error") else "success" if state.get("success") else
              "stopping" if state.get("running") and state.get("cancelled") else
              "cancelled" if state.get("cancelled") else "running" if state.get("running") else "idle")
    phase = state.get("phase", "loading" if status == "running" else "idle")
    elapsed = max(0, (state.get("finished") or now) - started) if started is not None else 0
    progress = min(.99, max(0., float(state.get("progress", 0)))) if status != "success" else 1.
    epochs = int(state.get("epochs", 0))
    total = (int(state.get("folds", 0)) + 1) * epochs
    remaining_epochs = max(0, total - state.get("completed_epochs", 0))
    samples = [s for s in state.get("epoch_seconds", [])[-8:] if s > 0 and math.isfinite(s)]
    remaining, finish, eta_seconds = "계산 중", "계산 중", None
    hint = "몇 차례 학습한 뒤 실제 속도로 예상 시간을 계산합니다."
    if status == "running" and phase in ("training", "evaluating") and remaining_epochs and len(samples) >= 2:
        seconds_per_epoch = sum(samples) / len(samples)
        current = max(0, now - state.get("last_epoch_at", now)) if phase == "training" else 0
        eta_seconds = max(1, math.ceil(remaining_epochs * seconds_per_epoch - current))
        evaluations = state.get("evaluation_seconds", [])
        pending = max(0, state.get("folds", 0) - len(state.get("evaluated_folds", [])))
        if evaluations and pending:
            eta_seconds += math.ceil(sum(evaluations) / len(evaluations) * pending)
        remaining = "약 " + duration(eta_seconds)
        finish = time.strftime("%H:%M:%S", time.localtime(now + eta_seconds))
        hint = "최근 학습 속도 기준 추정입니다. 평가·저장 시간과 PC 부하에 따라 달라질 수 있습니다."
    elif status == "running" and phase in ("saving", "evaluating") and not remaining_epochs:
        remaining, finish = "마무리 중", "마무리 중"
        hint = "학습 반복을 마쳤습니다. 평가·모델 저장이 끝나면 100%로 표시합니다."
    elif status != "running":
        remaining = "완료" if status == "success" else "—"
        finish = (time.strftime("%H:%M:%S", time.localtime(state.get("finished") or now))
                  if status == "success" else "—")
        hint = {"idle": "학습센터에서 동작을 선택하고 학습을 시작하세요.",
                "success": "모델 저장과 실행 검증을 마쳤습니다. 새 모델은 시험 모드로 시작하며, 학습센터에서 동작별 적용을 선택합니다.",
                "cancelled": "학습을 중단했습니다. 기존 모델을 유지합니다.",
                "stopping": "학습 프로세스가 종료되기를 기다립니다.",
                "error": "아래 오류 내용을 확인한 뒤 학습센터에서 다시 시작할 수 있습니다."}[status]
        if status == "success" and state.get("published") is False:
            hint = "후보 모델의 구분 성능이 기준에 못 미쳐 기존 모델을 유지했습니다. 진행 기록에서 이유를 확인하세요."
    names = [NAMES.get(key, key) for key in state.get("tasks") or []]
    if not names and started is not None:
        names = [NAMES.get(key, key) for key in state.get("per_class", {}) if key != "none"]
    losses = list(state.get("losses", []))
    return {
        "status": status, "statusText": {"idle": "학습 대기", "running": "학습 진행 중", "success": "학습 완료",
                                           "error": "학습 실패", "cancelled": "학습 중단", "stopping": "중단 중"}[status],
        "stage": state.get("stage") or "아직 시작한 학습이 없습니다", "phase": phase,
        "progress": progress, "percent": min(99, round(progress * 100)) if status != "success" else 100,
        "elapsed": duration(elapsed), "remaining": remaining, "finish": finish,
        "etaSeconds": eta_seconds if eta_seconds is not None else -1, "etaHint": hint,
        "tasks": " · ".join(names) if names else "선택한 학습 동작이 여기에 표시됩니다.",
        "epoch": f"{state.get('epoch', 0)} / {epochs}" if epochs else "—",
        "stageCount": f"{state.get('fold', 0)} / {state.get('folds', 0) + 1}" if epochs else "—",
        "data": (f"녹화 {state.get('recordings', 0)}개 · 자세 {len(state.get('postures', []))}개 · "
                 f"학습 구간 {state.get('windows', 0):,}개") if "windows" in state else "데이터를 읽으면 표시됩니다.",
        "loss": f"{state['loss']:.4f}" if state.get("loss") is not None else "—",
        "bestLoss": f"{min(losses):.4f}" if losses else "—", "losses": losses,
        "events": list(state.get("events", [])), "log": "\n".join(state.get("log", [])),
        "error": state.get("error", ""), "cancellable": status == "running",
        "skipped": len(state.get("skipped", [])),
    }
