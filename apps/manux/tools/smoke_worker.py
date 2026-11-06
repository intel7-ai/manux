"""Offline installed-worker check with generated hands; never reads a camera or user recordings."""
from __future__ import annotations
import argparse
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def recording(path, split, target="thumb_ring"):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"type": "meta", "schema": "hand-mouse-recording/1", "mode": "guided",
             "person": "synthetic", "preset": "pinch", "split": split,
             "camera": {"mode": [640, 480, 30], "mirror": False}}]
    labels = (["none"] * 40 + [target] * 40) * 4 + ["none"] * 40
    for i, label in enumerate(labels):
        world = [[j % 5 * .01, j // 5 * .01, j % 2 * .001] for j in range(21)]
        world[0], world[9], world[5], world[17] = [0., 0., 0.], [0., .1, 0.], [-.04, .08, 0.], [.04, .08, 0.]
        world[8] = [-.04, .08, 0.]
        world[4] = ([-.04, .08, 0.] if label == "thumb_ring" else
                    [.15, .25, 0.] if label == "thumb_middle" else [.15, .08, 0.])
        rows.append({"type": "frame", "t": i / 30, "world": world,
                     "lm": [[.4 + j * .005, .6 - j * .005, 0.] for j in range(21)],
                     "hand": ["Right", .95], "label": {"target": label,
                         "gesture": "pinch." + label if label != "none" else None},
                     "rule": {"action": "none"}})
    rows.append({"type": "end", "frames": len(labels)})
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write("\n".join(json.dumps(r) for r in rows) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    command = [str(args.worker.resolve())] if args.worker else [sys.executable, str(Path(__file__).with_name("app_worker.py"))]
    with tempfile.TemporaryDirectory(prefix="manux-worker-smoke-") as directory:
        root = Path(directory)
        checked = subprocess.run([*command, "--self-check", str(root / "components.json")], capture_output=True,
                                 timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if checked.returncode:
            raise RuntimeError(f"worker component check failed: {checked.stderr[-2000:]}")
        for split in ("train", "eval"):
            recording(root / "recordings" / "profile-synthetic" / "pinch" / split / "test.jsonl.gz", split)
        job = root / "job"
        job.mkdir()
        request = {"schema": "manux-worker-job/1", "kind": "train", "job_id": "smoke",
                   "job_dir": str(job), "cancel_path": str(job / "cancel"), "profile": "synthetic",
                   "recordings_dir": str(root / "recordings"), "store": str(root / "store"),
                   "cache_dir": str(root / "cache"), "epochs": 60,
                   "specs": {"pinch.thumb_ring": {"kind": "tap", "modes": ["pinch"], "point": "none"}}}
        path = job / "request.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        env = {**os.environ, "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "PYTHONIOENCODING": "utf-8"}
        process = subprocess.run([*command, "--request", str(path)], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", env=env, timeout=240,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        events = []
        for line in process.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
        done = next((event for event in events if event.get("event") == "done"), None)
        if process.returncode or done is None:
            raise RuntimeError(f"worker failed ({process.returncode}): {process.stdout[-2000:]} {process.stderr[-2000:]}")
        assert any(event.get("epoch") == 60 for event in events), "no training progress events"
        from manux.ml.store import load_active, active_token
        from manux import desktop
        from manux.app import schema
        from manux.core.contracts import RuntimeRequest
        from manux.settings.store import Settings
        from types import SimpleNamespace
        model = load_active(root / "store", "local3d-v1", 133)
        runtime = desktop.build(Settings(schema()), SimpleNamespace(close=lambda: None), windows=False)
        runtime.request(RuntimeRequest("activate_model", str(root / "store")))
        runtime._drain_runtime_requests()
        assert runtime.engine.features[-1].card.gestures == model.card.gestures
        runtime.stop()
        from uuid import uuid4
        def studio_job(action, **values):
            studio_dir = root / ("studio-" + uuid4().hex)
            studio_dir.mkdir()
            payload = {**request, "kind": "studio", "action": action, "job_id": uuid4().hex,
                       "data_dir": str(root / "data"),
                       "job_dir": str(studio_dir), "cancel_path": str(studio_dir / "cancel"), **values}
            studio_request = studio_dir / "request.json"
            studio_request.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run([*command, "--request", str(studio_request)], capture_output=True,
                                    timeout=60, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode: raise RuntimeError(f"studio worker failed: {result.stdout[-2000:]}")
            return json.loads((studio_dir / "result.json").read_text(encoding="utf-8"))
        library = studio_job("scan")
        trial = next(row for row in library["recordings"] if row["split"] == "eval")
        simulated = studio_job("simulate", key=trial["key"])
        assert simulated["simulation"]["counts"].get("preview", simulated["simulation"]["counts"]["model"]) > 0
        assert simulated["simulation"]["errors"] == 0
        old_version = json.loads(active_token(root / "store"))["version"]
        restored = studio_job("restore", version=old_version)
        assert len(restored["versions"]) == 2 and json.loads(active_token(root / "store"))["version"] != old_version
        named = studio_job("scan")["models"][0]
        renamed = studio_job("model_rename", identifier=named["id"], name="Synthetic desk v2")
        assert renamed["models"][0]["name"] == "Synthetic desk v2"
        token = active_token(root / "store")
        studio_job("model_preview", identifier=named["id"])
        assert active_token(root / "store") == token
        candidate = root / "data/models/library" / named["id"] / "model.onnx"
        runtime = desktop.build(Settings(schema()), SimpleNamespace(close=lambda: None), windows=False,
                                tcn_store=root / "store", tcn_mode="direct")
        runtime.request(RuntimeRequest("preview_model", str(candidate)))
        runtime._drain_runtime_requests()
        assert runtime.preview_only and runtime.current_model.path == candidate
        applied = studio_job("model_apply", identifier=named["id"])
        runtime.request(RuntimeRequest("activate_model", str(root / "store")))
        runtime._drain_runtime_requests()
        assert not runtime.preview_only and active_token(root / "store") != token
        runtime.stop()
        assert len(applied["versions"]) == 3
        evaluation_file = root / "recordings/profile-synthetic/pinch/eval/test.jsonl.gz"
        evaluation_file.rename(root / "evaluation.not-used")
        before_failed_apply = active_token(root / "store")
        try:
            studio_job("model_apply", identifier=named["id"])
            raise AssertionError("missing independent evaluation was accepted")
        except RuntimeError:
            assert active_token(root / "store") == before_failed_apply
        before = active_token(root / "store")
        (job / "cancel").touch()
        cancelled = subprocess.run([*command, "--request", str(path)], capture_output=True, timeout=60, env=env,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert cancelled.returncode != 0 and active_token(root / "store") == before
        report = {"packaged": args.worker is not None, "components": json.loads((root / "components.json").read_text()),
                  "training": True, "independent_evaluation": done["metrics"],
                  "runtime_activation": True, "cancel_preserved_model": True}
        report["studio"] = {"recordings": len(library["recordings"]), "simulation": simulated["simulation"],
                            "restore_revalidated": True, "versions": len(restored["versions"])}
        report["model_library"] = {"named_artifact": True, "preview_with_existing_active": True,
                                   "independent_apply": True, "missing_eval_preserved_active": True}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))


if __name__ == "__main__":
    main()
