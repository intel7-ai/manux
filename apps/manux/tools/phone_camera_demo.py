"""Phone camera development demo, ported from Dan (한순중), phone_web_bridge.py @9da1f634e8c0afd9d3d6b32a56c83cddfb23d5f8.

Retains Dan's QR waiting renderer and 480x360 / JPEG .55 browser capture.
No engine or Windows output is created. App integration follows #96 in a separate PR.
"""
from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

import numpy as np

from manux.link.server import PhoneLink
from manux.link.security import private_address
from manux.settings.paths import resolve


def local_ip():
    # UDP connect selects the LAN interface without sending a packet.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.168.255.254", 9))
            ip = sock.getsockname()[0]
        if private_address(ip):
            return ip
    except OSError:
        pass
    return "127.0.0.1"


def build_waiting_qr_frame(url: str, width: int = 960, height: int = 540) -> np.ndarray:
    """Create a visual instruction screen with a scannable QR code while waiting for phone connection."""
    canvas = np.full((height, width, 3), (24, 28, 38), dtype=np.uint8)

    import cv2
    try:
        import qrcode
    except ImportError:
        cv2.putText(canvas, url, (30, height // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 140), 2)
        return canvas

    # Generate QR code image
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr_np = np.repeat(np.where(np.array(qr.get_matrix()), 0, 255).astype(np.uint8)[..., None], 3, axis=2)
    qr_bgr = cv2.cvtColor(qr_np, cv2.COLOR_RGB2BGR)

    qr_size = min(240, height - 120)
    qr_bgr = cv2.resize(qr_bgr, (qr_size, qr_size), interpolation=cv2.INTER_NEAREST)

    qx = 50
    qy = (height - qr_size) // 2
    canvas[qy : qy + qr_size, qx : qx + qr_size] = qr_bgr

    tx = qx + qr_size + 40
    cv2.putText(
        canvas,
        "Phone Camera (No App)",
        (tx, 120),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (80, 220, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "1. Connect phone to the same network ",
        (tx, 175),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.56,
        (230, 230, 230),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "2. Scan the QR code or open this URL in Chrome / Safari:",
        (tx, 215),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.56,
        (230, 230, 230),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        url,
        (tx, 265),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.78,
        (0, 255, 140),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "3. If browser shows 'Security Warning', tap 'Advanced -> Proceed'",
        (tx, 320),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52,
        (180, 200, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "   (Because it uses a local self-signed HTTPS certificate)",
        (tx, 350),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (150, 165, 190),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "Enter the pairing code shown in the PC terminal",
        (tx, 415),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (0, 200, 255),
        2,
        cv2.LINE_AA,
    )
    return canvas


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=local_ip(), help="PC private LAN IP; auto-detected by default")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--hand-model", type=Path)
    parser.add_argument("--no-tracking", action="store_true", help="show camera only; no MediaPipe model needed")
    args = parser.parse_args(argv)
    import cv2

    paths = resolve()
    link = PhoneLink(paths.data_dir, host=args.host, port=args.port)
    landmarker = tracker = None
    title = "MANUX phone camera - NO Windows input"
    try:
        link.start()
        print(f"Phone URL: {link.url}", flush=True)
        print(f"Pairing code (2 minutes, single use): {link.issue_code()}", flush=True)
        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(link.url)
            qr.make(fit=True)
            qr.print_ascii(invert=True)
        except ImportError:
            pass
        waiting = build_waiting_qr_frame(link.url)
        cv2.namedWindow(title, cv2.WINDOW_NORMAL)
        last_device = None
        count, started, processing_fps = 0, time.perf_counter(), 0.0
        while True:
            device = link.device
            if device != last_device:
                print(f"Connected device: {device}" if device else "Phone disconnected", flush=True)
                last_device = device
                if tracker:
                    tracker.reset()
            item = link.source.read(timeout=0.03)
            if item is None:
                if tracker:
                    tracker.reset()
                image = waiting.copy()
                lines = ["BLOCKED" if link.blocked else "Waiting for a fresh phone frame (no tracking)"]
            else:
                frame, image = item
                hand_size = 0.0
                found = 0
                if not args.no_tracking:
                    if tracker is None:
                        from manux.tracking.landmarker import MediaPipeLandmarker, ensure_model
                        from manux.tracking.tracker import HandTracker
                        model = args.hand_model or paths.hand_model
                        # Never download into the checkout's bundled models directory.
                        if not model.exists():
                            model = ensure_model(paths.data_dir / "models/hand_landmarker.task")
                        landmarker = MediaPipeLandmarker(model)
                        tracker = HandTracker(landmarker)
                    result = tracker.step(frame, image, "Right")
                    found = len(result.hands)
                    for hand in result.hands:
                        xs, ys = [], []
                        for x, y, _ in hand.landmarks:
                            xs.append(x * frame.width)
                            ys.append(y * frame.height)
                            cv2.circle(image, (int(x * frame.width), int(y * frame.height)), 3, (40, 240, 120), -1)
                        hand_size = max(hand_size, max(xs) - min(xs), max(ys) - min(ys))
                count += 1
                elapsed = time.perf_counter() - started
                if elapsed >= 1:
                    processing_fps = count / elapsed
                    count, started = 0, time.perf_counter()
                latency = link.source.last_latency_ms
                delay = f"{latency:.1f} ms" if latency is not None else "disconnected"
                lines = [f"Hands: {found}  hand size: {hand_size:.0f}px  processing: {processing_fps:.1f} fps",
                         f"Phone capture -> PC receive (estimated): {delay}",
                         f"Camera: {frame.width}x{frame.height}  {link.source.facing}"]
            lines.append("ESC Exit | D Disconnect | B Block | U Unblock | P New pairing code")
            panel = np.full((len(lines) * 25 + 10, max(960, image.shape[1]), 3), 24, np.uint8)
            for i, line in enumerate(lines):
                cv2.putText(panel, line, (12, 24 + i * 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 1)
            image = cv2.resize(image, (panel.shape[1], round(image.shape[0] * panel.shape[1] / image.shape[1])))
            cv2.imshow(title, np.vstack((panel, image)))
            key = cv2.waitKey(1) & 255
            if key == 27 or cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                break
            if key in (ord("d"), ord("D")):
                link.disconnect()
                print(f"New pairing code (2 minutes): {link.issue_code()}", flush=True)
            if key in (ord("b"), ord("B")):
                link.block()
            if key in (ord("u"), ord("U")):
                link.unblock()
            if key in (ord("p"), ord("P")):
                try:
                    print(f"Pairing code (2 minutes): {link.issue_code()}", flush=True)
                except RuntimeError as error:
                    print(error, flush=True)
    finally:
        link.stop()
        if landmarker:
            landmarker.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
