"""Gaze accuracy measurement: 9-point calibration, separate validation points, optional drift.

Run from hand-mouse/:
  uv run --locked python scripts/gaze_validate.py --synthetic              # no camera, no model
  uv run --locked python scripts/fetch_face_model.py                        # once, real mode
  uv run --locked python scripts/gaze_validate.py --consent --distance-cm 60 --screen-cm 53.1x29.9 \
      --screen-px 1920x1080 [--drift-minutes 10]

Real mode needs a person who agreed to be measured (`--consent`). It shows a full-screen dot and
looks at the camera frames with the Face Landmarker. It NEVER saves video or images. The JSON
report keeps only targets and statistics (no per-frame gaze points or landmarks).
Esc aborts without saving. Synthetic results say only that the code is consistent.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.gaze.estimator import GazeEstimator  # noqa: E402
from src.gaze.metrics import ScreenSpec  # noqa: E402
from src.gaze.protocol import run_session  # noqa: E402
from src.gaze.synthetic import SyntheticSource  # noqa: E402

DEFAULT_MODEL = ROOT / "models" / "face_landmarker.task"
DEFAULT_OUT = ROOT / "logs" / "gaze"  # logs/ is already git-ignored


def _pair(text: str, cast) -> Tuple[Any, Any]:
    a, b = text.lower().split("x")
    return cast(a), cast(b)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--synthetic", action="store_true", help="simulated person, no camera or model")
    p.add_argument("--consent", action="store_true", help="real mode: the person being measured agreed")
    p.add_argument("--screen-px", default="1920x1080", help="WIDTHxHEIGHT in pixels")
    p.add_argument("--screen-cm", default="53.1x29.9", help="WIDTHxHEIGHT of the visible panel in cm")
    p.add_argument("--distance-cm", type=float, default=60.0, help="eyes to screen")
    p.add_argument("--frames-per-point", type=int, default=25)
    p.add_argument("--settle-frames", type=int, default=20, help="frames skipped after the dot moves")
    p.add_argument("--degree", type=int, default=1, choices=(1, 2), help="1 = linear, 2 = quadratic features")
    p.add_argument("--alpha", type=float, default=1.0, help="ridge strength")
    p.add_argument("--drift-minutes", type=float, default=0.0)
    p.add_argument("--drift-interval-s", type=float, default=60.0)
    p.add_argument("--out-dir", default=str(DEFAULT_OUT))
    p.add_argument("--camera-source", default="0")
    p.add_argument("--model", default=str(DEFAULT_MODEL))
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--synthetic-noise", type=float, default=0.005)
    p.add_argument("--synthetic-drift-per-min", type=float, default=0.0,
                   help="synthetic iris-offset drift (eye widths per minute)")
    return p


class CameraSource:
    """Real source: draws the dot, reads camera frames, runs the Face Landmarker. Saves nothing."""

    def __init__(self, screen: ScreenSpec, camera_source: str, model_path: Path) -> None:
        import cv2  # lazy: only real mode needs these
        import mediapipe as mp
        import numpy as np

        self.cv2, self.np, self.screen = cv2, np, screen
        # Read the model into memory instead of passing a path: MediaPipe fails to open model
        # files under folders with non-ASCII names on Windows (seen on this project).
        buffer = model_path.read_bytes()
        opts = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_buffer=buffer),
            running_mode=mp.tasks.vision.RunningMode.VIDEO, num_faces=1,
            output_face_blendshapes=False, output_facial_transformation_matrixes=False)
        self.mp = mp
        self.landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(opts)
        src: Any = int(camera_source) if camera_source.isdigit() else camera_source
        self.cap = cv2.VideoCapture(src)
        if not self.cap.isOpened():
            raise RuntimeError("camera could not be opened")
        self.t0 = time.perf_counter()
        self.last_ms = 0
        self.win = "gaze-validate (Esc = abort)"
        cv2.namedWindow(self.win, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(self.win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        self.aspect: Optional[float] = None

    def now(self) -> float:
        return time.perf_counter() - self.t0

    def advance(self, seconds: float) -> None:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:  # keep the window alive, show a neutral center dot
            self._draw((0.5, 0.5), "look at the dot")
            self._poll()

    def _poll(self) -> None:
        if self.cv2.waitKey(1) & 0xFF == 27:
            raise KeyboardInterrupt

    def _draw(self, target: Tuple[float, float], text: str = "") -> None:
        img = self.np.full((self.screen.height_px, self.screen.width_px, 3), 255, dtype=self.np.uint8)
        x, y = int(target[0] * self.screen.width_px), int(target[1] * self.screen.height_px)
        self.cv2.circle(img, (x, y), 14, (0, 0, 220), -1)
        self.cv2.circle(img, (x, y), 3, (255, 255, 255), -1)
        if text:
            self.cv2.putText(img, text, (40, 60), self.cv2.FONT_HERSHEY_SIMPLEX, 1.0, (60, 60, 60), 2)
        self.cv2.imshow(self.win, img)

    def capture(self, target: Tuple[float, float], n_frames: int) -> List[Tuple[float, Any]]:
        frames = []
        for _ in range(int(n_frames)):
            self._draw(target)
            self._poll()
            ok, frame = self.cap.read()
            t = self.now()
            if not ok or frame is None:
                frames.append((t, None))
                continue
            h, w = frame.shape[:2]
            self.aspect = h / float(w)
            rgb = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB)
            ms = max(self.last_ms + 1, int(t * 1000))
            self.last_ms = ms
            res = self.landmarker.detect_for_video(
                self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb), ms)
            if res.face_landmarks:
                frames.append((t, self.np.array([[p.x, p.y, p.z] for p in res.face_landmarks[0]], dtype=float)))
            else:
                frames.append((t, None))
        return frames

    def close(self) -> None:
        self.cap.release()
        self.cv2.destroyAllWindows()


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    w_px, h_px = _pair(args.screen_px, int)
    w_cm, h_cm = _pair(args.screen_cm, float)
    screen = ScreenSpec(w_px, h_px, w_cm, h_cm, args.distance_cm)
    estimator = GazeEstimator(alpha=args.alpha, degree=args.degree, min_samples=30)
    close = None
    if args.synthetic:
        source: Any = SyntheticSource(noise=args.synthetic_noise, seed=args.seed,
                                      drift_h_per_min=args.synthetic_drift_per_min,
                                      head_wobble_deg=1.0, blink_rate=0.02, dropout_rate=0.01)
        mode = "synthetic"
    else:
        if not args.consent:
            print("Real mode needs --consent (the person measured agreed). Nothing was started.", file=sys.stderr)
            return 2
        model = Path(args.model)
        if not model.is_file():
            print(f"Model not found: {model}\nRun: uv run --locked python scripts/fetch_face_model.py", file=sys.stderr)
            return 2
        print("Privacy: no video or image is saved; the report keeps targets and statistics only.")
        source = CameraSource(screen, args.camera_source, model)
        close = source.close
        mode = "camera"
    try:
        report = run_session(source, screen, estimator, args.frames_per_point, args.settle_frames,
                             args.drift_minutes, args.drift_interval_s)
        if mode == "camera" and getattr(source, "aspect", None):
            report["settings"]["camera_aspect"] = source.aspect
    except KeyboardInterrupt:
        print("Aborted (Esc). Nothing was saved.", file=sys.stderr)
        return 1
    finally:
        if close:
            close()
    report["meta"] = {"mode": mode, "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "stage": "gaze-experiment-1",
                      "interpretation": ("synthetic data: checks the code, says nothing about real gaze accuracy"
                                         if mode == "synthetic" else "real measurement, one person, one setup")}
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"gaze-{mode}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    cal = report["calibration"]
    print(f"mode={mode} calibration ok={cal['ok']} reason={cal['reason']} samples={cal['n_samples']}")
    val = report.get("validation")
    if val:
        d, px = val["error_deg"], val["error_px"]
        print(f"validation: median {d['median']:.2f} deg / {px['median']:.0f} px, p90 {d['p90']:.2f} deg, "
              f"valid rate {d['valid_rate']:.0%}" if d["median"] is not None else "validation: no valid frames")
    if report.get("drift_slope_deg_per_min") is not None:
        print(f"drift slope: {report['drift_slope_deg_per_min']:.3f} deg/min")
    print(f"saved {path}")
    return 0 if cal["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
