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


def recording(path, split):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"type": "meta", "schema": "hand-mouse-recording/1", "mode": "guided",
             "person": "synthetic", "preset": "pinch", "split": split,
             "camera": {"mode": [640, 480, 30], "mirror": False}}]
    labels = (["none"] * 40 + ["thumb_ring"] * 40) * 4 + ["none"] * 40
    for i, label in enumerate(labels):
        world = [[j % 5 * .01, j // 5 * .01, j % 2 * .001] for j in range(21)]
        world[0], world[9], world[5], world[17] = [0., 0., 0.], [0., .1, 0.], [-.04, .08, 0.], [.04, .08, 0.]
        world[8] = [-.04, .08, 0.]
        world[4] = [-.04, .08, 0.] if label == "thumb_ring" else [.15, .08, 0.]
        rows.append({"type": "frame", "t": i / 30, "world": world,
                     "lm": [[.4 + j * .005, .6 - j * .005, 0.] for j in range(21)],
                     "hand": ["Right", .95], "label": {"target": label,
                         "gesture": "pinch.thumb_ring" if label != "none" else None},
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
        before = active_token(root / "store")
        (job / "cancel").touch()
        cancelled = subprocess.run([*command, "--request", str(path)], capture_output=True, timeout=60, env=env,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert cancelled.returncode != 0 and active_token(root / "store") == before
        report = {"packaged": args.worker is not None, "components": json.loads((root / "components.json").read_text()),
                  "training": True, "independent_evaluation": done["metrics"],
                  "runtime_activation": True, "cancel_preserved_model": True}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))


if __name__ == "__main__":
    main()
