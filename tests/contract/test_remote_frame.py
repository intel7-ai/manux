"""Phone metadata contract, independent of optional transport and vision dependencies."""
import pytest

from manux.core.contracts import RemoteFrameMetadata, ScreenCaptureRequest


@pytest.mark.parametrize("field,value", [("seq", -1), ("seq", True), ("width", 0), ("width", 50000),
                                        ("captured_at", float("nan")), ("captured_at", True),
                                        ("mirrored", 1), ("rotation", 45), ("facing", "other")])
def test_invalid_metadata(field, value):
    data = dict(seq=0, captured_at=1000, width=480, height=360, mirrored=False, rotation=0, facing="user")
    data[field] = value
    with pytest.raises(ValueError):
        RemoteFrameMetadata(**data)


@pytest.mark.parametrize("profile", ["detail", "balanced", "compact"])
def test_screen_capture_request_has_bounded_profiles(profile):
    assert ScreenCaptureRequest(profile).profile == profile


@pytest.mark.parametrize("profile", [None, "", "unbounded", "1920x1080"])
def test_screen_capture_request_rejects_arbitrary_dimensions(profile):
    with pytest.raises(ValueError):
        ScreenCaptureRequest(profile)
