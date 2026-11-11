"""Train only requested binary gesture models and publish the completed bundle atomically."""
from __future__ import annotations

import glob
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]

from src.action_bindings import get_action_registry
from src.gesture_catalog import BY_KEY, CLASSES, is_motion_task
from src.gesture_models import load_single, publish_bank, training_lock
from src.motion_mode import train_motion_templates_from_recordings
from src.recorder import finished_recordings, public_source
from src.tcn_features import FEATURES, FEATURE_VERSION


def selected_labels(keys):
    get_action_registry()  # ensure custom temporal motions are synced into BY_KEY / CLASSES
    labels = []
    for key in keys:
        if key not in BY_KEY:
            raise ValueError(f"알 수 없는 동작: {key}")
        label = BY_KEY[key].label
        if label == "none":
            raise ValueError("이동·중립은 선택한 동작의 오답 자료로 함께 사용됩니다.")
        if label not in labels:
            labels.append(label)
    if not labels:
        raise ValueError("학습할 동작을 하나 이상 선택하세요.")
    return labels


def binary_sessions(sessions, label):
    target = CLASSES.index(label)
    return [{**session, "y": np.where(session["y"] < 0, -1, (session["y"] == target).astype(np.int64)),
             "classes": ["none", label]} for session in sessions]


def run(args, training):
    try:
        labels = selected_labels(args.gestures)
        opts = training.resolve_training_options(args)
        if opts["epochs"] < 1 or not 0 < args.thr <= 1 or args.k < 1:
            raise ValueError("학습 반복과 확률·연속 프레임 기준을 확인하세요.")
        folder = args.output_dir / "gestures"
        with training_lock(folder):
            from src.training_center import (
                backup_current_model,
                filter_selected_recording_paths,
            )
            paths = sorted(Path(path) for path in glob.glob(args.recordings))
            complete = {p.resolve() for parent in {p.parent for p in paths}
                        for p in finished_recordings(parent)[0]}
            paths = [path for path in paths if path.resolve() in complete]
            paths = [Path(p) for p in filter_selected_recording_paths(paths)]
            if not paths:
                raise ValueError("선택된 완료 녹화가 없습니다.")
            sessions, eval_sessions, opts = training.load_training_sessions(paths, args, window=training.WINDOW)
            all_y = np.concatenate([session["y"] for session in sessions])
            if not (all_y == CLASSES.index("none")).any():
                raise ValueError("중립·쉬기 구간의 유효 녹화가 필요합니다.")
            missing = [label for label in labels if not (all_y == CLASSES.index(label)).any()]
            if missing:
                raise ValueError("유효한 녹화가 없는 동작: " + ", ".join(missing))
            sources = []
            for path, session in zip(paths, eval_sessions):
                pc, camera = public_source(session["meta"])
                sources.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                "pc": pc, "camera": {"note": camera}, "frames": len(session["frames"])})
            from src.spatial_mlp import (
                SpatialMlpModel,
                blend_tcn_and_spatial_probs,
                ensure_session_spatial_features,
                pick_spatial_from_sessions,
                train_binary_spatial_specialist,
                train_spatial_mlp,
            )
            # Finished evaluation folds are kept per gesture (docs/training-resume.md): a pause or an unexpected stop
            # costs at most the fold that was running.
            from src.fold_cache import (PAUSE_EXIT_CODE, FoldCache, clear_pause, code_hash, derived_key,
                                        pause_requested, recordings_key)
            code_key, rec_key = code_hash(), recordings_key(paths)
            caches = {}
            for label in labels:
                caches[label] = FoldCache(
                    ROOT / "logs" / "fold_cache",
                    {"kind": "selected", "label": label, "recordings": rec_key, "code": code_key,
                     "seed": getattr(args, "seed", 0), "thr": args.thr, "k": args.k, "epochs": opts["epochs"],
                     "stride": opts["window_stride"], "device": opts["effective_device"],
                     "augment": bool(opts["offline_augment"]), "no_cleaned": bool(getattr(args, "no_cleaned", False)),
                     "derived": derived_key(paths)},
                    enabled=bool(getattr(args, "resume", False)),
                )
            if getattr(args, "resume", False):
                next(iter(caches.values())).prune_except(c.fingerprint for c in caches.values())
            resumed_folds = 0
            staged = {}
            staged_spatial: dict[str, SpatialMlpModel] = {}
            for index, label in enumerate(labels, 1):
                fold_cache = caches[label]
                training.emit("gesture_start", target=label, index=index, count=len(labels))
                selected = binary_sessions(sessions, label)
                eval_selected = selected[: len(paths)]
                classes = ["none", label]
                conds = sorted({int(c) for s in eval_selected for c, y in zip(s["cond"], s["y"]) if c >= 0 and y >= 0})
                xa, ya, ca = training.pick_with_conds(selected, conds)
                _, _, wa = training.pick_with_weights(selected, conds)
                xa_eval, ya_eval = training.pick(eval_selected, conds)
                held = training.resolve_held_out_conds(conds, opts)
                training.emit(
                    "loaded",
                    windows=len(ya),
                    folds=len(held),
                    per_class=dict(Counter(ya_eval.tolist())),
                    device=opts["effective_device"],
                    device_name=opts["device_name"],
                )
                results = []
                evaluated_folds: list[int] = []
                for fold, test in enumerate(held, 1):
                    train_conds = [c for c in conds if c != test]
                    x, y, w_fold = training.pick_with_weights(selected, train_conds)
                    if test not in conds or not len(y) or len(set(y)) != 2:
                        training.emit("note", text=f"{label}: 조건 {test} 제외 평가는 학습 자료 부족으로 보류합니다.")
                        continue
                    evaluated_folds.append(int(test))
                    cached = fold_cache.load(test)
                    if cached is not None:
                        resumed_folds += 1
                        results.extend(cached["results"])
                        training.emit("fold_start", posture=test, fold=fold, folds=len(held))
                        continue
                    if pause_requested():
                        clear_pause()
                        training.emit("paused", folds_done=len(evaluated_folds) - 1, folds=len(held))
                        return PAUSE_EXIT_CODE
                    n_results = len(results)
                    training.emit("fold_start", posture=test, fold=fold, folds=len(held))
                    model = training.train(
                        x,
                        y,
                        opts["epochs"],
                        classes=classes,
                        window_stride=opts["window_stride"],
                        device=opts["effective_device"],
                        sample_weights=w_fold,
                    )
                    sp_fold_spec = None
                    if not is_motion_task(label):
                        try:
                            xs_fold, ys_fold, gs_fold, ws_fold = pick_spatial_from_sessions(
                                sessions, train_conds, return_groups=True, return_weights=True
                            )
                            if len(ys_fold) >= 2 and (ys_fold == CLASSES.index(label)).any():
                                training.emit("substage", text=f"선택 동작 평가 {fold}/{len(held)} · Spatial MLP 보조 모델 학습 중…")
                                sp_fold_spec, _ = train_binary_spatial_specialist(
                                    xs_fold,
                                    ys_fold,
                                    classes=CLASSES,
                                    target_label=label,
                                    independent=True,
                                    device=opts["effective_device"],
                                    groups=gs_fold,
                                    weights_all=ws_fold,
                                )
                        except Exception:
                            sp_fold_spec = None
                    for session in eval_selected:
                        prob = training.predict(model, session["x"])
                        if sp_fold_spec is not None:
                            s_feats = ensure_session_spatial_features(session)
                            if len(s_feats) == len(prob):
                                sp_prob = sp_fold_spec.predict_proba(s_feats)
                                prob = blend_tcn_and_spatial_probs(prob, classes, sp_prob, sp_fold_spec.classes)
                        negative = (session["y"] == 0) & (session["cond"] == test)
                        results.append({"test_posture": test, "rules": training.evaluate(session, [test]),
                                        "tcn": training.evaluate(session, [test], prob, args.thr, args.k),
                                        "negative_frames": int(negative.sum()),
                                        "negative_frames_above_threshold": int((negative & (prob[:, 1] >= args.thr)).sum()),
                                        "free_minutes": training.free_minutes(session, [test])})
                    fold_cache.save(test, {"results": results[n_results:]})
                if resumed_folds:
                    training.emit("note", text=f"이전에 끝낸 평가 {resumed_folds}판은 다시 하지 않고 이어서 했어요.")
                if pause_requested():
                    clear_pause()
                    training.emit("paused", folds_done=len(evaluated_folds), folds=len(held))
                    return PAUSE_EXIT_CODE
                training.emit("final_start")
                losses = []
                model = training.train(
                    xa,
                    ya,
                    opts["epochs"],
                    losses=losses,
                    classes=classes,
                    window_stride=opts["window_stride"],
                    device=opts["effective_device"],
                    sample_weights=wa,
                )
                # Train dedicated per-gesture binary Spatial MLP (`["none", label]`) for this gesture
                sp_spec = None
                sp_spec_doc = None
                motion_traj_doc = None
                if is_motion_task(label):
                    try:
                        training.emit("substage", text="시계열 모션 동작 전용 궤적·속도 프로파일 (MotionTrajectory) 템플릿 학습 중…")
                        rec_dirs = {p.parent for p in paths}
                        rec_folder = next(iter(rec_dirs)) if rec_dirs else (Path(__file__).resolve().parents[1] / "recordings")
                        m_res = train_motion_templates_from_recordings(
                            rec_dir=rec_folder,
                            target_keys=[label],
                            output_path=args.output_dir / "motion_templates.json",
                        )
                        motion_traj_doc = (m_res.get("templates") or {}).get(label)
                    except Exception:
                        motion_traj_doc = None
                else:
                    try:
                        xs_all_sp, ys_all_sp, gs_all_sp, ws_all_sp = pick_spatial_from_sessions(
                            sessions, conds, return_groups=True, return_weights=True
                        )
                        if len(ys_all_sp) >= 2 and (ys_all_sp == CLASSES.index(label)).any():
                            training.emit("substage", text="선택 동작 Spatial MLP (0-lag 보조 모델) 학습 및 ONNX 저장 중…")
                            sp_spec, _ = train_binary_spatial_specialist(
                                xs_all_sp,
                                ys_all_sp,
                                classes=CLASSES,
                                target_label=label,
                                independent=True,
                                device=opts["effective_device"],
                                groups=gs_all_sp,
                                weights_all=ws_all_sp,
                            )
                            staged_spatial[label] = sp_spec
                            sp_spec_doc = sp_spec.to_dict()
                    except Exception:
                        sp_spec = None
                        sp_spec_doc = None
                model, final_results, boost_info = training.adaptive_boost_postures(
                    model,
                    xa,
                    ya,
                    ca,
                    eval_selected,
                    conds,
                    results,
                    classes=classes,
                    thr=args.thr,
                    k=args.k,
                    epochs=opts["epochs"],
                    window_stride=opts["window_stride"],
                    device=opts["effective_device"],
                    sp_model=sp_spec,
                    losses=losses,
                    condition_groups=sessions[0]["condition_groups"],
                )
                pred = training.predict(model, xa_eval).argmax(1)
                accuracy = {name: round(float((pred[ya_eval == i] == i).mean()), 3) for i, name in enumerate(classes)}
                per_class = {name: int((ya_eval == i).sum()) for i, name in enumerate(classes)}
                card = {
                    "classes": classes, "target": label, "model_kind": "gesture_binary",
                    "window": training.WINDOW, "features": FEATURES, "feature_version": FEATURE_VERSION,
                    "threshold": args.thr, "frames_in_a_row": args.k,
                    **training.card_training_metadata(opts, evaluated_folds),
                    "trained": time.strftime("%Y-%m-%d %H:%M:%S"), "data": sources,
                    "condition_groups": sessions[0]["condition_groups"],
                    "negative_classes": [name for i, name in enumerate(CLASSES)
                                         if name != label and (all_y == i).any()],
                    "held_out_results": results,
                    "final_results": final_results,
                    "adaptive_boost": boost_info,
                    "loss_curve": losses,
                    "per_gesture": training.per_gesture(results, per_class, accuracy, classes, final_results=final_results, adaptive_boost=boost_info),
                    "per_task": training.per_gesture(results, per_class, accuracy, classes, by_task=True, final_results=final_results, adaptive_boost=boost_info),
                    "spatial_mlp": sp_spec_doc,
                    "motion_trajectory": motion_traj_doc,
                }
                if not args.no_export:
                    path = folder / f"{label}-{uuid4().hex}.onnx"
                    model_cpu = model.cpu().eval()
                    torch.onnx.export(model_cpu, torch.zeros(1, training.WINDOW, FEATURES), str(path),
                                      input_names=["x"], output_names=["logits"],
                                      dynamic_axes={"x": {0: "batch"}, "logits": {0: "batch"}},
                                      opset_version=18, dynamo=False)
                    path.with_suffix(".json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
                    load_single(path, FEATURE_VERSION)
                    staged[label] = {"model": path.name, "card": path.with_suffix(".json").name,
                                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                training.emit("gesture_done", target=label)
            if not args.no_export:
                before_rep = None
                backup_folder = None
                try:
                    from src.training_center import evaluate_training_improvement, model_report
                    before_rep = model_report(args.output_dir / "gesture_tcn.onnx")
                    backup_folder = backup_current_model(
                        model=args.output_dir / "gesture_tcn.onnx",
                        backup_dir=args.output_dir / "backups",
                        reason="before-selected-train",
                    )
                except Exception:
                    pass
                publish_bank(folder, staged)
                # Update selected gestures' specialist MLPs in spatial_mlp.json and refresh existing specialists with new negatives
                try:
                    sp_out_path = args.output_dir / "spatial_mlp.json"
                    if sp_out_path.is_file() and staged_spatial:
                        sp_existing = SpatialMlpModel.load(sp_out_path)
                        merged_specs = dict(sp_existing.specialists)
                        try:
                            xs_ref, ys_ref, gs_ref, ws_ref = pick_spatial_from_sessions(
                                sessions, return_groups=True, return_weights=True
                            )
                            if len(ys_ref) >= 2:
                                for ex_lbl in list(merged_specs.keys()):
                                    if ex_lbl not in staged_spatial and ex_lbl in CLASSES and (ys_ref == CLASSES.index(ex_lbl)).any():
                                        ex_spec, _ = train_binary_spatial_specialist(
                                            xs_ref,
                                            ys_ref,
                                            classes=CLASSES,
                                            target_label=ex_lbl,
                                            independent=True,
                                            device=opts["effective_device"],
                                            groups=gs_ref,
                                            weights_all=ws_ref,
                                        )
                                        merged_specs[ex_lbl] = ex_spec
                        except Exception:
                            pass
                        merged_specs.update(staged_spatial)
                        sp_updated = SpatialMlpModel(
                            sp_existing.w1,
                            sp_existing.b1,
                            sp_existing.w2,
                            sp_existing.b2,
                            classes=sp_existing.base_classes,
                            feature_version=sp_existing.feature_version,
                            metadata=sp_existing.metadata,
                            specialists=merged_specs,
                        )
                        sp_updated.save(sp_out_path)
                    else:
                        present_idx = sorted({int(y) for y in all_y if y >= 0})
                        sp_classes = [CLASSES[i] for i in present_idx]
                        if "none" in sp_classes and len(sp_classes) >= 2:
                            remapped = []
                            for s in sessions:
                                y_remap = np.full_like(s["y"], -1)
                                for ci, cname in enumerate(sp_classes):
                                    y_remap[s["y"] == CLASSES.index(cname)] = ci
                                remapped.append({**s, "y": y_remap})
                            xs_sp, ys_sp = pick_spatial_from_sessions(remapped)
                            if len(ys_sp) >= 2:
                                sp_model, _ = train_spatial_mlp(xs_sp, ys_sp, classes=sp_classes, device=opts["effective_device"])
                                for lbl_k, spec_v in staged_spatial.items():
                                    sp_model.specialists[lbl_k] = spec_v
                                sp_model.save(sp_out_path)
                except Exception:
                    pass
                main_card = args.output_dir / "gesture_tcn.json"
                if main_card.is_file():
                    try:
                        mc = json.loads(main_card.read_text(encoding="utf-8"))
                        disabled = [x for x in (mc.get("disabled_gestures") or []) if x not in labels]
                        if disabled != (mc.get("disabled_gestures") or []):
                            mc["disabled_gestures"] = disabled
                            main_card.write_text(json.dumps(mc, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception:
                        pass
                try:
                    from src.training_center import evaluate_training_improvement, model_report
                    after_rep = model_report(args.output_dir / "gesture_tcn.onnx")
                    bak_id = f"backup:{backup_folder.name}" if backup_folder is not None else None
                    evaluate_training_improvement(
                        after_report=after_rep,
                        before_report=before_rep,
                        target_keys=args.gestures,
                        backup_id=bak_id,
                        model=args.output_dir / "gesture_tcn.onnx",
                        backup_dir=args.output_dir / "backups",
                    )
                except Exception:
                    pass
            training.emit("done", targets=labels, published=not args.no_export)
        return 0
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        training.emit("error", text=str(error))
        if not training.PROGRESS:
            print(str(error), flush=True)
        return 1
