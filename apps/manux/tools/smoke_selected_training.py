"""Synthetic end-to-end selective retraining; no camera, personal data or network."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from smoke_worker import recording
from compose_tcn import _parts
from manux.ml.store import active_token, load_active
from manux.settings.tcn_policy import GesturePolicies


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="manux-selective-") as directory:
        root = Path(directory)
        records = root / "recordings" / "profile-synthetic" / "pinch"
        for label in ("thumb_ring", "thumb_middle"):
            for split in ("train", "eval"):
                recording(records / split / f"{label}.jsonl.gz", split, label)
        def run(number, focus, expect_success=True, **overrides):
            job = root / f"job-{number}"
            job.mkdir()
            request = dict(schema="manux-worker-job/1", kind="train", job_id=str(number), job_dir=str(job),
                cancel_path=str(job / "cancel"), profile="synthetic", recordings_dir=str(root / "recordings"),
                store=str(root / "store"), cache_dir=str(root / "cache"), focus_labels=focus, epochs=60,
                specs={"pinch." + label: dict(kind="tap", modes=["pinch"], point="none")
                       for label in ("thumb_ring", "thumb_middle")}, **overrides)
            path = job / "request.json"
            path.write_text(json.dumps(request), encoding="utf-8")
            process = subprocess.run([sys.executable, str(Path(__file__).with_name("app_worker.py")), "--request", str(path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"}, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            events = []
            for line in process.stdout.splitlines():
                try: events.append(json.loads(line))
                except ValueError: pass
            done = next((e for e in events if e.get("event") == "done"), None)
            if expect_success and (process.returncode or done is None):
                raise RuntimeError(f"selective step {number} failed: {process.stdout[-2500:]} {process.stderr[-1000:]}")
            if not expect_success:
                assert process.returncode and done is None
            return done, events

        first, _ = run(1, ["thumb_ring", "thumb_middle"])
        old = load_active(root / "store", "local3d-v1", 133)
        policies = GesturePolicies(root / "policies.json")
        for gesture, row in policies.describe(old.card.raw).items():
            policies.set(old.card.raw, gesture, "apply", row["model_id"])
        # Remove the old right-click training recording from discovery; keep its independent eval.
        (records / "train" / "thumb_ring.jsonl.gz").rename(root / "old-training.not-used")
        second, events = run(2, ["thumb_middle"])
        new = load_active(root / "store", "local3d-v1", 133)
        old_route = old.card.raw["composition"]["routes"]["thumb_ring"]
        new_route = new.card.raw["composition"]["routes"]["thumb_ring"]
        assert old_route == new_route
        a, b = _parts(old.path)[2][old_route], _parts(new.path)[2][new_route]
        assert [x.SerializeToString() for x in a[1] + a[2]] == [x.SerializeToString() for x in b[1] + b[2]]
        assert second["trained_labels"] == ["thumb_middle"]
        assert policies.modes(new.card.raw) == {"pinch.thumb_middle": "shadow", "pinch.thumb_ring": "apply"}
        assert len(new.card.raw["composition"]["components"]) == 2
        token = active_token(root / "store")
        run(3, ["thumb_middle"], expect_success=False, eval=[str(records / "eval/thumb_middle.jsonl.gz")])
        assert active_token(root / "store") == token
        report = dict(synthetic=True, initial=first["metrics"], selected=second["metrics"],
            old_training_absent=True, unchanged_classifier_bytes=True, unchanged_policy=True,
            selected_default_shadow=True, missing_old_eval_preserved_model=True,
            epoch_events=sum("epoch" in e for e in events))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))


if __name__ == "__main__":
    main()
