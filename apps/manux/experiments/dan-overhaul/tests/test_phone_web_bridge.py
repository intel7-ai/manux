"""
Unit tests for app-less Smartphone Web Browser Camera bridge components.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from src import CameraStream, PhoneWebCameraServer
from src.phone_web_bridge import (
    build_waiting_qr_frame,
    decode_jpeg_bytes,
    ensure_self_signed_cert,
)


def test_package_exports_phone_web_camera_server():
    """Verify PhoneWebCameraServer is exported from src package."""
    assert PhoneWebCameraServer is not None


def test_camera_stream_parse_source_phone():
    """Verify phone and phone-web keywords normalize to 'phone'."""
    assert CameraStream.parse_source("phone") == "phone"
    assert CameraStream.parse_source("phone-web") == "phone"


def test_ensure_self_signed_cert(tmp_path: Path):
    """Verify ensure_self_signed_cert generates valid certificate and key files."""
    cert_dir = tmp_path / "certs"
    cert_path, key_path = ensure_self_signed_cert(cert_dir, "10.10.21.17")

    assert cert_path.exists()
    assert key_path.exists()
    assert cert_path.name == "phone_bridge_cert.pem"
    assert key_path.name == "phone_bridge_key.pem"

    cert_text = cert_path.read_text(encoding="utf-8")
    key_text = key_path.read_text(encoding="utf-8")

    assert "-----BEGIN CERTIFICATE-----" in cert_text
    assert "-----END CERTIFICATE-----" in cert_text
    assert "-----BEGIN RSA PRIVATE KEY-----" in key_text or "-----BEGIN PRIVATE KEY-----" in key_text

    # Re-calling with existing certs should return existing paths without re-generating
    cert_path2, key_path2 = ensure_self_signed_cert(cert_dir, "10.10.21.17")
    assert cert_path2 == cert_path
    assert key_path2 == key_path


def test_build_waiting_qr_frame():
    """Verify build_waiting_qr_frame returns an image with shape (540, 960, 3) and uint8 dtype."""
    frame = build_waiting_qr_frame("https://10.10.21.17:8443", width=960, height=540)
    assert isinstance(frame, np.ndarray)
    assert frame.shape == (540, 960, 3)
    assert frame.dtype == np.uint8


def test_decode_jpeg_bytes():
    """Verify decode_jpeg_bytes decodes valid JPEG byte buffer into expected BGR array."""
    dummy_img = np.zeros((120, 160, 3), dtype=np.uint8)
    # Put some color pattern to ensure pixel data is retained
    dummy_img[10:30, 20:50] = (255, 128, 64)

    success, encoded = cv2.imencode(".jpg", dummy_img)
    assert success
    jpeg_bytes = encoded.tobytes()

    decoded = decode_jpeg_bytes(jpeg_bytes)
    assert decoded is not None
    assert isinstance(decoded, np.ndarray)
    assert decoded.shape == (120, 160, 3)
    assert decoded.dtype == np.uint8

    # Empty bytes test
    assert decode_jpeg_bytes(b"") is None


def test_phone_web_camera_server_lazy_jpeg_decoding(tmp_path: Path):
    """Verify PhoneWebCameraServer implements zero-bufferbloat lazy decoding of newest JPEG packets."""
    server = PhoneWebCameraServer(port=18443, cert_dir=tmp_path / "certs")

    # 1. Unconnected initial state
    assert server.has_client is False
    assert server.has_new_frame is False
    ok, frame_waiting = server.read_frame()
    assert ok is True
    assert frame_waiting.shape == (540, 960, 3)
    assert server.wait_for_new_frame(timeout=0.001) is False

    # 2. Simulate client connection and sending rapid JPEG frames
    img1 = np.full((120, 160, 3), 40, dtype=np.uint8)
    img2 = np.full((120, 160, 3), 210, dtype=np.uint8)
    img3 = np.full((120, 160, 3), 110, dtype=np.uint8)

    _, enc1 = cv2.imencode(".jpg", img1)
    _, enc2 = cv2.imencode(".jpg", img2)
    _, enc3 = cv2.imencode(".jpg", img3)
    jpeg1, jpeg2, jpeg3 = enc1.tobytes(), enc2.tobytes(), enc3.tobytes()

    # Frame 1 arrives
    with server._frame_lock:
        server._client_connected = True
        server._latest_raw_jpeg = jpeg1
        server._frame_seq += 1
    server._frame_event.set()

    # Frame 2 arrives immediately before consumer thread reads frame 1
    # Frame 1 must be overwritten in-place without decoding
    with server._frame_lock:
        server._latest_raw_jpeg = jpeg2
        server._frame_seq += 1
    server._frame_event.set()

    assert server.has_client is True
    assert server.has_new_frame is True
    assert server._frame_seq == 2
    assert server._consumed_seq == -1
    # Prior to read_frame(), no JPEG decoding was performed
    assert server._latest_frame is None

    # wait_for_new_frame returns True since has_new_frame is True
    assert server.wait_for_new_frame(timeout=0.01) is True

    # 3. Consumer thread calls read_frame()
    # Must decode only the latest packet (jpeg2), skipping jpeg1
    ok2, frame2 = server.read_frame()
    assert ok2 is True
    assert frame2.shape == (120, 160, 3)
    assert np.mean(frame2) == pytest.approx(210, abs=5)
    assert server._consumed_seq == 2
    assert server.has_new_frame is False
    assert server._latest_frame is not None

    # 4. Immediate second read without new network packets returns cached frame without re-decoding
    ok_cached, frame_cached = server.read_frame()
    assert ok_cached is True
    assert np.array_equal(frame_cached, frame2)
    assert server.has_new_frame is False

    # 5. Frame 3 arrives
    with server._frame_lock:
        server._latest_raw_jpeg = jpeg3
        server._frame_seq += 1
    server._frame_event.set()

    assert server.has_new_frame is True
    ok3, frame3 = server.read_frame()
    assert ok3 is True
    assert frame3.shape == (120, 160, 3)
    assert np.mean(frame3) == pytest.approx(110, abs=5)
    assert server._consumed_seq == 3
    assert server.has_new_frame is False

    # 6. Disconnect client
    with server._frame_lock:
        server._client_connected = False
    server._frame_event.set()

    assert server.has_client is False
    assert server.has_new_frame is False
    ok_dc, frame_dc = server.read_frame()
    assert ok_dc is True
    assert frame_dc.shape == (540, 960, 3)
