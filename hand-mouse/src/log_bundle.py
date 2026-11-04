"""
One zip of the logs for "it felt slow / it missed my hand" reports (2026-11-04).

What goes in: the performance log, failure records (joint coordinates and timings, never camera
images), tracking-test results, the app log, the settings file and a short system description.
What stays out: calibration profiles (their names can be people's names) and the foreground-window
titles in the performance log (they can show an e-mail subject or a document name); only whether
MANUX itself was in front is kept.

Nothing is sent anywhere here: the user gets a file and decides what to do with it.
"""
from __future__ import annotations

import csv
import io
import json
import os
import platform
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

MAX_FAILURES = 60               # newest failure records; older ones rarely matter and make the zip large
REDACTED = "(hidden)"


def _perf_without_titles(path: Path) -> str:
    """perf_latest.csv with the foreground_window column blanked."""
    out = io.StringIO()
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = csv.reader(f)
        writer = csv.writer(out)
        header = next(rows, None)
        if header is None:
            return ""
        writer.writerow(header)
        hidden = header.index("foreground_window") if "foreground_window" in header else -1
        for row in rows:
            if 0 <= hidden < len(row) and row[hidden]:
                row[hidden] = REDACTED
            writer.writerow(row)
    return out.getvalue()


def system_info(app_version: Optional[str], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "app_version": app_version or "source",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "cpu": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
        **(extra or {}),
    }


def build_bundle(log_dir: Path, config_path: Optional[Path], out_dir: Path,
                 app_version: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> Path:
    """Write MANUX-logs-<date>-<time>.zip into out_dir and return its path."""
    log_dir, out_dir = Path(log_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"MANUX-logs-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("system.json", json.dumps(system_info(app_version, extra), indent=2, ensure_ascii=False))
        perf = log_dir / "perf_latest.csv"
        if perf.is_file():
            z.writestr("perf_latest.csv", _perf_without_titles(perf))
        for name in ("app_latest.log",):
            if (log_dir / name).is_file():
                z.write(log_dir / name, name)
        for csv_path in sorted(log_dir.glob("tracking-*.csv")):
            z.write(csv_path, f"tracking/{csv_path.name}")
        failures = log_dir / "failures"
        if failures.is_dir():
            if (failures / "index.csv").is_file():
                z.write(failures / "index.csv", "failures/index.csv")
            newest = sorted(failures.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in newest[:MAX_FAILURES]:
                z.write(path, f"failures/{path.name}")
        if config_path is not None and Path(config_path).is_file():
            z.write(config_path, "config/gestures.json")
    return target
