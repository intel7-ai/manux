"""Synthetic acceleration measurements; no camera, user recordings, installation or OS input.

Train/export in the selected Python environment, then compare the same exported model across
inference backends. Synthetic repeats are workload, never independent user attempts.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from time import perf_counter

import numpy as np

from manux.data.recording import read, replay
from manux.features.tcn import TcnFeature
from manux.core.contracts import FrameContext, Stage, OutputTarget
from manux.ml.predictor import load_model
from manux.ml.backends import describe
from manux.perception.local3d import frame_features, windows, FEATURE_VERSION, FEATURES
from smoke_worker import recording


def synthetic(folder, copies):
    path = folder / "synthetic-train.jsonl.gz"
    evaluation = folder / "synthetic-eval.jsonl.gz"
    recording(path, "train")
    recording(evaluation, "eval")
    rec = read(path)
    frames = []
    import copy
    duration = rec.frames[-1]["t"]+1/30
    for n in range(copies):
        for frame in rec.frames:
            row = copy.deepcopy(frame)
            row["t"] += n*duration
            frames.append(row)
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for row in [rec.meta, *frames, {"type":"end","frames":len(frames)}]:
            stream.write(json.dumps(row)+"\n")
    return path, evaluation


def events(model, rec):
    recognizer = TcnFeature(model.predictor, model.card)
    result = []
    for item in replay(rec):
        ctx = FrameContext(item.frame, Stage.TUTORIAL, OutputTarget.PRACTICE, "pinch")
        result.extend((item.frame.seq, intent.gesture, intent.phase) for intent in recognizer.step(item.hand, ctx))
    return result


def inference(model_path, eval_path, devices, count=100):
    rec = read(eval_path)
    data = windows(frame_features(rec.frames, .75), 15)
    reference = load_model(model_path, FEATURE_VERSION, FEATURES, device="cpu")
    selected = data[np.linspace(0, len(data)-1, num=min(64,len(data)), dtype=int)]
    expected = np.stack([reference.predictor(x) for x in selected])
    baseline_events = events(reference, rec)
    results = []
    for device in devices:
        started = perf_counter()
        try:
            model = load_model(model_path, FEATURE_VERSION, FEATURES, device=device)
            startup = (perf_counter()-started)*1000
            actual = np.stack([model.predictor(x) for x in selected])
            maximum = float(np.max(np.abs(actual-expected)))
            compared = events(model, rec)
            for _ in range(10): model.predictor(selected[0])
            times = []
            for i in range(count):
                now = perf_counter()
                model.predictor(selected[i % len(selected)])
                times.append((perf_counter()-now)*1000)
            execution = describe(model.predictor)
            results.append({"requested":device, "execution":execution,
                "execution_devices":list(getattr(model.predictor.session,"execution_devices",[])),
                "startup_ms":startup, "p50_ms":float(np.median(times)), "p95_ms":float(np.percentile(times,95)),
                "max_abs_probability_error":maximum, "probability_tolerance":.001,
                "events_equal":compared==baseline_events, "event_count":len(compared),
                "passed":maximum<=.001 and compared==baseline_events})
        except Exception as error:
            results.append({"requested":device,"passed":False,"error":str(error),"startup_ms":(perf_counter()-started)*1000})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--train-devices", nargs="*", default=["cpu"])
    parser.add_argument("--inference-devices", nargs="*", default=["cpu"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--copies", type=int, default=8)
    parser.add_argument("--model", type=Path, help="reuse one model and only compare inference")
    parser.add_argument("--worker", action="store_true", help="include independent evaluation and atomic installation")
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args(argv)
    if not 1 <= args.repeats <= 5 or not 1 <= args.copies <= 100 or args.epochs<1:
        parser.error("use 1..5 repeats, 1..100 copies and positive epochs")
    from manux.data.repository import repository_root
    repository = repository_root(Path(__file__))
    if repository is not None and args.work_dir.resolve().is_relative_to(repository): parser.error("write benchmark models outside the repository")
    args.work_dir.mkdir(parents=True,exist_ok=True)
    train, evaluation = synthetic(args.work_dir, args.copies)
    declarations = args.work_dir / "decisions.json"
    declarations.write_text(json.dumps({"thumb_ring":{"kind":"tap","modes":["pinch"],"point":"none"}}),encoding="utf-8")
    result = {"synthetic":True,"copies_are_not_independent_trials":True,"seed":0,
              "epochs":args.epochs,"copies":args.copies,"threads":args.threads,
              "scope":"worker_with_eval_install" if args.worker else "training_cli_export", "training":[],"inference":[]}
    model = args.model
    environment = {**os.environ,"OMP_NUM_THREADS":str(args.threads),"MKL_NUM_THREADS":str(args.threads),
                   "PYTHONIOENCODING":"utf-8","MANUX_INFERENCE_DEVICE":"cpu"}
    if model is None:
        for device in args.train_devices:
            runs = []
            for n in range(args.repeats):
                path = args.work_dir/f"{device}-{n}.onnx"
                command = [sys.executable,str(Path(__file__).with_name("train_tcn.py")),str(train),"--output",str(path),
                           "--classes","none","thumb_ring","--decisions",str(declarations),"--device",device,
                           "--epochs",str(args.epochs),"--skip-folds","--cache-dir",str(args.work_dir/"cache")]
                if args.worker:
                    job = args.work_dir / f"job-{device}-{n}-{uuid4().hex}"
                    job.mkdir()
                    store = job / "store"
                    request = {"schema":"manux-worker-job/1","kind":"train","job_id":uuid4().hex,
                        "job_dir":str(job),"cancel_path":str(job/"cancel"),"profile":"synthetic","preset":"pinch",
                        "mode":"pinch","recordings_dir":str(args.work_dir),"store":str(store),
                        "cache_dir":str(args.work_dir/"cache"),"epochs":args.epochs,"device":device,
                        "focus_labels":["thumb_ring"],
                        "specs":{"pinch.thumb_ring":{"kind":"tap","modes":["pinch"],"point":"none"}}}
                    (job/"request.json").write_text(json.dumps(request),encoding="utf-8")
                    command = [sys.executable,str(Path(__file__).with_name("app_worker.py")),"--request",str(job/"request.json")]
                started = perf_counter()
                process = subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace",env=environment,
                                         timeout=600,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                row = {"run":n+1,"total_s":perf_counter()-started,"passed":process.returncode==0}
                if process.returncode==0:
                    if args.worker:
                        pointer = json.loads((store/"active.json").read_text(encoding="utf-8"))
                        path = store/"versions"/pointer["version"]/"model.onnx"
                    card=json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
                    row.update(execution=card["execution"],timings=card["timings"],export_max_abs_error=card["export_max_abs_error"])
                    model=path
                else: row["error"]=(process.stderr or process.stdout)[-1500:]
                runs.append(row)
                print(json.dumps({"device":device,**row}),flush=True)
            result["training"].append({"requested":device,"runs":runs,
                "median_total_s":float(np.median([r["total_s"] for r in runs]))})
    if model is not None:
        result["model_sha256"]=sha256(model.read_bytes()).hexdigest()
        result["inference"]=inference(model,evaluation,args.inference_devices)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps({"inference":result["inference"],"model_file":model.name if model else None}),flush=True)
    return 0 if model is not None else 1


if __name__=="__main__": raise SystemExit(main())
