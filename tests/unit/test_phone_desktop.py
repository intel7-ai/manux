"""Desktop phone composition: generated images, fake tracking and no real OS input."""
import asyncio
import json
import time
from types import SimpleNamespace

import numpy as np
import pytest

from manux.app import schema
from manux.capture.remote import RemoteCamera
from manux.core.contracts import FrameInfo, RemoteFrameMetadata, RuntimeRequest, Stage
from manux.desktop import CameraInput, build
from manux.engine.commands import SetBusy, SetControl, SetStage
from manux.link.session import PhoneSession
from manux.settings.store import Settings


class FakeLink:
    def __init__(self, data_dir, *, host):
        self.source = RemoteCamera()
        self.url = f"https://{host}:8443"
        self.blocked = False
        self.device = None
        self.stopped = False
        self.issues = 0

    def start(self): pass
    def issue_code(self):
        if self.blocked or self.device:
            raise RuntimeError("unavailable")
        self.issues += 1
        return str(self.issues).zfill(6)
    def disconnect(self):
        self.source.disconnect()
        self.device = None
    def block(self):
        self.blocked = True
        self.disconnect()
    def unblock(self): self.blocked = False
    def stop(self):
        self.stopped = True
        self.block()


class FakeBluetoothLink(FakeLink):
    def __init__(self, data_dir):
        super().__init__(data_dir, host="unused")
        self.url = ""
        self.local_name = "Synthetic PC"


@pytest.mark.parametrize("configured", [False, True])
def test_internet_composition_selects_fixed_gateway_only_when_configured(tmp_path, monkeypatch, configured):
    from manux.desktop import create_internet_link
    from manux.platform import fixed_gateway, quick_tunnel

    created = []
    monkeypatch.setattr(fixed_gateway, "load_gateway_config", lambda path: SimpleNamespace(url="https://stable.example.test") if configured else None)
    monkeypatch.setattr(fixed_gateway, "FixedGatewayTunnel", lambda path: created.append(("fixed", path)) or "fixed-tunnel")
    monkeypatch.setattr(quick_tunnel, "QuickTunnel", lambda: created.append(("quick", None)) or "quick-tunnel")
    link = create_internet_link(tmp_path)
    assert not created and not link._running  # configuration never starts a listener on its own
    assert link.tunnel_factory() == ("fixed-tunnel" if configured else "quick-tunnel")
    assert created == ([("fixed", tmp_path)] if configured else [("quick", None)])


def test_invalid_fixed_gateway_config_does_not_fall_back_or_replace_current_source(tmp_path, monkeypatch):
    from manux.desktop import create_internet_link
    from manux.platform import fixed_gateway, quick_tunnel

    def invalid(path):
        raise ValueError("synthetic protected configuration failure")
    monkeypatch.setattr(fixed_gateway, "load_gateway_config", invalid)
    monkeypatch.setattr(quick_tunnel, "QuickTunnel", lambda: pytest.fail("must not bypass configured gateway"))
    session = PhoneSession(tmp_path, factory=FakeLink, internet_factory=create_internet_link)
    session.command("start", "127.0.0.1")
    old = session.link
    with pytest.raises(ValueError):
        session.command("internet")
    assert session.link is old and not old.stopped and not session.view()["fixed_address"]
    session.close()


def test_fixed_address_session_view_does_not_affect_other_transports(tmp_path):
    class FixedInternet(FakeLink):
        def __init__(self, data_dir):
            super().__init__(data_dir, host="stable.example.test")
            self.fixed_address = True
    session = PhoneSession(tmp_path, factory=FakeLink, internet_factory=FixedInternet)
    assert not session.view()["fixed_address"]
    session.command("internet")
    assert session.view()["fixed_address"] and session.view()["url"] == "https://stable.example.test:8443"
    session.command("start", "127.0.0.1")
    assert not session.view()["fixed_address"]
    session.close()


def test_bluetooth_source_switch_preserves_existing_link_if_new_start_fails(tmp_path):
    session = PhoneSession(tmp_path, factory=FakeLink, bluetooth_factory=FakeBluetoothLink)
    session.command("start", "127.0.0.1")
    wifi = session.link
    class Broken(FakeBluetoothLink):
        def start(self):
            raise OSError("unavailable adapter")
    session.bluetooth_factory = Broken
    with pytest.raises(OSError):
        session.command("bluetooth")
    assert session.link is wifi and not wifi.stopped
    assert session.view()["transport"] == "wifi"
    session.bluetooth_factory = FakeBluetoothLink
    session.command("bluetooth")
    bluetooth = session.link
    assert wifi.stopped and session.view()["transport"] == "bluetooth"
    assert session.view()["local_name"] == "Synthetic PC"
    assert session.view()["url"] == "" and session.view()["code"]
    session.command("start", "127.0.0.1")
    assert bluetooth.stopped and session.view()["transport"] == "wifi"
    assert session.view()["local_name"] == ""
    session.close()
    assert session.view()["transport"] == "" and not session.view()["enabled"]


def test_consumed_code_is_not_shown_after_remote_disconnect(tmp_path):
    session = PhoneSession(tmp_path, bluetooth_factory=FakeBluetoothLink)
    session.command("bluetooth")
    session.link.pairing = SimpleNamespace(code=session.code)
    assert session.view()["code"]
    session.link.pairing.code = None
    assert session.view()["code"] == "" and session.view()["seconds"] == 0
    assert "새 연결 코드" in session.view()["status"]
    session.close()


@pytest.mark.parametrize("busy", [None, "recording", "enrolling", "calibrating"])
@pytest.mark.parametrize("transport", ["bluetooth", "internet"])
def test_remote_runtime_releases_input_and_respects_busy_work(tmp_path, busy, transport):
    source = source_with_phone(tmp_path)
    setattr(source.phone, transport + "_factory", FakeBluetoothLink)
    released = []
    source.camera = SimpleNamespace(release=lambda: released.append(True))
    runtime = build(source.settings, source, windows=False)
    runtime.engine.commands.put(SetStage(Stage.MAIN))
    runtime.engine.commands.put(SetControl(True))
    if busy:
        runtime.engine.commands.put(SetBusy(busy, True))
    runtime.request(RuntimeRequest("phone_camera", transport))
    runtime._drain_runtime_requests()
    assert not runtime.engine.state.control_on
    assert released == ([] if busy else [True])
    assert bool(source.phone.link) == (busy is None)
    assert source.settings.get("pinch.long_press_drag_multiplier") == 2.
    runtime.stop()


def test_internet_start_failure_preserves_existing_source_and_close_stops_tunnel(tmp_path):
    created = []
    class Internet(FakeLink):
        def __init__(self, data_dir):
            super().__init__(data_dir, host="synthetic.example.invalid")
            created.append(self)
        def start(self):
            if len(created) == 1:
                raise TimeoutError("tunnel unavailable")
    session = PhoneSession(tmp_path, factory=FakeLink, internet_factory=Internet)
    session.command("start", "127.0.0.1")
    old = session.link
    with pytest.raises(TimeoutError):
        session.command("internet")
    assert session.link is old and not old.stopped and created[0].stopped
    session.command("internet")
    assert old.stopped and session.view()["transport"] == "internet"
    assert session.view()["url"].startswith("https://synthetic.example.invalid")
    session.close()
    assert created[-1].stopped and not session.view()["enabled"]


def source_with_phone(tmp_path, factory=FakeLink):
    source = CameraInput(Settings(schema(), {"camera.source": "1", "pinch.long_press_drag_multiplier": 2.}), tmp_path/"unused.task", "1")
    source.phone = PhoneSession(tmp_path, factory=factory)
    source.landmarker = SimpleNamespace(close=lambda: None)
    source.tracker = SimpleNamespace(reset=lambda: None, min_score=.5,
        step=lambda *args: SimpleNamespace(hand=None, hands=(), detect_ms=1.))
    return source


def test_opt_in_pair_expiry_block_disconnect_and_local_return(tmp_path):
    now=[100.]
    session=PhoneSession(tmp_path, factory=FakeLink, clock=lambda:now[0])
    assert session.link is None and not session.view()["enabled"]
    session.command("start", "127.0.0.1")
    link=session.link
    assert session.view()["code"]=="000001"
    now[0]+=121
    assert session.view()["code"]==""
    session.command("pair")
    assert session.view()["code"]=="000002"
    session.command("block")
    assert session.view()["blocked"] and session.view()["code"]==""
    session.command("unblock")
    assert session.view()["code"]=="000003"
    link.device={"name":"Synthetic Android"}
    link.source.connect()
    assert session.view()["connected"] and session.view()["code"]==""
    session.command("disconnect")
    assert not session.view()["connected"] and session.view()["code"]=="000004"
    session.command("local")
    assert link.stopped and not session.view()["enabled"]


def test_failed_start_keeps_local_camera_and_releases_partial_listener(tmp_path):
    links=[]
    class Broken(FakeLink):
        def start(self):
            links.append(self)
            raise OSError("occupied port")
    source=source_with_phone(tmp_path, Broken)
    released=[]
    camera=SimpleNamespace(release=lambda:released.append(True))
    source.camera=camera
    with pytest.raises(OSError): source.phone_action("start", "127.0.0.1")
    assert source.camera is camera and not released and source.phone.link is None
    assert links[0].stopped


def test_runtime_source_switch_pauses_and_preserves_settings(tmp_path):
    source=source_with_phone(tmp_path)
    released=[]
    source.camera=SimpleNamespace(release=lambda:released.append(True))
    runtime=build(source.settings, source, windows=False)
    runtime.engine.commands.put(SetStage(Stage.MAIN))
    runtime.engine.commands.put(SetControl(True))
    runtime.engine.commands.put(SetBusy("connecting", True))
    runtime.request(RuntimeRequest("phone_camera","start",{"host":"127.0.0.1"}))
    runtime._drain_runtime_requests()
    assert released==[True] and source.camera is None
    assert not runtime.engine.state.control_on
    assert source.settings.get("camera.source")=="1"
    assert source.settings.get("pinch.long_press_drag_multiplier")==2.
    assert runtime.latest()[1].phone["enabled"]
    runtime.stop()
    assert source.phone.link is None


@pytest.mark.parametrize("busy",["enrolling","recording","calibrating"])
def test_active_work_rejects_camera_switch(tmp_path,busy):
    source=source_with_phone(tmp_path)
    runtime=build(source.settings,source,windows=False)
    runtime.engine.commands.put(SetBusy(busy,True))
    runtime.request(RuntimeRequest("phone_camera","start",{"host":"127.0.0.1"}))
    runtime._drain_runtime_requests()
    assert source.phone.link is None and "마친 뒤" in source.phone.view()["status"]
    runtime.stop()


def test_waiting_phone_is_not_reopened_as_usb_and_local_returns(tmp_path,monkeypatch):
    import manux.desktop as desktop
    source=source_with_phone(tmp_path)
    source.phone_action("start","127.0.0.1")
    assert source.read("Right",False) is None
    remote=source.camera
    source._last_camera_frame=0.
    assert source.read("Right",False) is None and source.camera is remote
    assert "휴대폰" in source.error
    source.phone_action("local")
    camera=SimpleNamespace(open=lambda:True,release=lambda:None)
    monkeypatch.setattr(desktop,"LatestFrameCamera",lambda config:camera)
    assert source._open() and source.camera is camera
    source.close()


@pytest.mark.parametrize("renumbered", [False, True])
def test_refresh_recovers_pc_camera_after_missing_frames(tmp_path, monkeypatch, renumbered):
    import manux.desktop as desktop

    source = source_with_phone(tmp_path)
    image = np.full((36, 48, 3), 128, dtype=np.uint8)
    released = []
    opened = []
    source.camera = SimpleNamespace(read=lambda **_: None, release=lambda: released.append("old"))
    source._last_camera_frame = time.perf_counter() - 4
    source._next_try = time.perf_counter() + 100
    assert source.read("Right", False) is None and "카메라 새로고침" in source.error
    runtime = build(source.settings, source, windows=False)
    runtime.engine.commands.put(SetStage(Stage.MAIN))
    runtime.engine.commands.put(SetControl(True))

    def new_camera(config):
        opened.append(config.source)
        can_open = not renumbered or config.source == 0
        return SimpleNamespace(
            open=lambda: can_open, release=lambda: released.append("new"),
            read=lambda **_: (FrameInfo(0, time.perf_counter(), 48, 36, True), image),
            actual_mode=lambda: (48, 36, 30.), fps=30.,
        )

    monkeypatch.setattr(desktop, "LatestFrameCamera", new_camera)
    runtime.request(RuntimeRequest("camera_reconnect"))
    runtime._drain_runtime_requests()
    assert released == ["old"] and not runtime.engine.state.control_on
    assert not runtime.latest()[1].camera_ok and runtime.latest()[1].image is None
    runtime.step()
    assert opened == (["1", 0] if renumbered else ["1"])
    assert runtime.latest()[1].image is image
    assert runtime.latest()[1].camera_ok and source.settings.get("camera.source") == "1"
    runtime.stop()
    assert released == ["old", "new"]


def test_actual_tls_phone_frame_enters_desktop_and_disconnect_clears_image(tmp_path):
    aiohttp=pytest.importorskip("aiohttp")
    cv2=pytest.importorskip("cv2")
    from manux.link.server import PhoneLink
    source=source_with_phone(tmp_path,lambda data_dir,host:PhoneLink(data_dir,host=host,port=0))
    runtime=build(source.settings,source,windows=False)
    runtime.request(RuntimeRequest("phone_camera","start",{"host":"127.0.0.1"}))
    runtime._drain_runtime_requests()
    link=source.phone.link
    assert link is not None
    async def exercise():
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as client:
            headers={"Origin":link.url}
            response=await client.post(link.url+"/pair",json={"code":source.phone.code,"device":"Synthetic Android"},headers=headers)
            assert response.status==200
            token=(await response.json())["token"]
            async with client.ws_connect(link.url+"/ws",headers=headers) as ws:
                await ws.send_json({"kind":"auth","token":token})
                assert (await ws.receive_json())["kind"]=="ready"
                pixels=np.full((36,48,3),120,dtype=np.uint8)
                ok,jpeg=cv2.imencode(".jpg",pixels);assert ok
                meta=json.dumps(dict(kind="frame",seq=1,captured_at=time.time(),width=48,height=36,
                                     mirrored=False,rotation=0,facing="environment")).encode()
                await ws.send_bytes(len(meta).to_bytes(4,"big")+meta+jpeg.tobytes())
                assert (await ws.receive_json())["accepted"]
                runtime.step()
                view=runtime.latest()[1]
                assert view.camera_ok and view.image.shape==(36,48,3)
                assert view.phone["connected"] and view.snapshot is not None
                assert not view.snapshot.control_on
        for _ in range(30):
            if link.device is None: break
            await asyncio.sleep(.01)
        runtime.step()
        view=runtime.latest()[1]
        assert not view.camera_ok and view.image is None and not view.phone["connected"]
    try:
        asyncio.run(exercise())
    finally:
        runtime.stop()
    assert source.phone.link is None


def test_runtime_rejects_unknown_phone_command():
    with pytest.raises(ValueError): RuntimeRequest("phone_camera","send_click")


@pytest.fixture
def phone_stream(tmp_path, monkeypatch):
    """Real remote decode/metadata with an asymmetric synthetic image and controlled time."""
    import manux.desktop as desktop
    cv2 = pytest.importorskip("cv2")
    now = [100.0]
    monkeypatch.setattr(desktop.time, "perf_counter", lambda: now[0])
    source = source_with_phone(tmp_path)
    source.phone_action("start", "127.0.0.1")
    remote = RemoteCamera(clock=lambda: now[0], wall_clock=lambda: now[0])
    source.phone.link.source = remote
    source.phone.link.device = {"name": "Synthetic Android"}
    remote.connect()
    pixels = np.arange(12 * 16 * 3, dtype=np.uint8).reshape(12, 16, 3)
    jpeg = cv2.imencode(".jpg", pixels)[1].tobytes()
    decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    def send(seq, mirrored=False, rotation=0):
        meta = RemoteFrameMetadata(seq, now[0], 16, 12, mirrored, rotation,
                                   "user" if mirrored else "environment")
        assert remote.submit(meta, jpeg)
    yield source, remote, now, send, decoded
    source.close()


def test_phone_frame_gaps_keep_preview_but_still_release_missing_hand(phone_stream, monkeypatch):
    source, remote, now, send, _ = phone_stream
    runtime = build(source.settings, source, windows=False)
    tracked, released = [], []
    original_track, original_release = source.tracker.step, runtime.engine._release
    def track(*args):
        tracked.append(args[0].seq)
        return original_track(*args)
    def release(reason):
        released.append(reason)
        return original_release(reason)
    monkeypatch.setattr(source.tracker, "step", track)
    monkeypatch.setattr(runtime.engine, "_release", release)
    try:
        send(0)
        runtime.step()
        previous = runtime.latest()
        assert previous[1].camera_ok and previous[1].image is not None
        # 15-20fps Wi-Fi may exceed the 50ms read timeout. Do not flash black.
        for gap in (.07, .10, .25, .45):
            now[0] = 100.0 + gap
            runtime.step()
            assert source.error == ""
            assert runtime.latest() == previous
        assert "hand_lost" in released and tracked == [0]
        now[0] = 100.5
        send(1)
        runtime.step()
        assert runtime.latest()[1].camera_ok and tracked == [0, 1]
        remote.disconnect()
        runtime.step()
        assert not runtime.latest()[1].camera_ok and runtime.latest()[1].image is None
    finally:
        runtime.stop()


@pytest.mark.parametrize("failure", ["stall", "invalid_images", "reconnect"])
def test_phone_preview_never_survives_loss_or_a_new_empty_session(phone_stream, monkeypatch, failure):
    source, remote, now, send, _ = phone_stream
    runtime = build(source.settings, source, windows=False)
    try:
        send(0)
        runtime.step()
        assert runtime.latest()[1].camera_ok
        if failure == "reconnect":
            remote.disconnect()
            remote.connect()
        else:
            now[0] += 2.01
            if failure == "invalid_images":
                # Packets can keep arriving even when the decoder produces no usable images.
                monkeypatch.setattr(remote, "_decoder", lambda _: None)
                send(1)
                assert remote.is_open
        runtime.step()
        view = runtime.latest()[1]
        assert source.error and not view.camera_ok
        assert view.image is None and view.landmarks is None
    finally:
        runtime.stop()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("front", [False, True])
@pytest.mark.parametrize("desired", [False, True])
def test_phone_front_and_rear_match_desktop_mirror_before_tracking(phone_stream, monkeypatch, rotation, front, desired):
    from manux.capture.camera import transform
    source, _, _, send, raw = phone_stream
    source.settings.set("camera.mirror", desired)
    tracked = []
    def track(frame, image, side):
        tracked.append((frame, image.copy(), side))
        # The tracking label must be interpreted using the final image's mirror state.
        hand = SimpleNamespace(label="Left" if desired else "Right", score=.99)
        return SimpleNamespace(hand=None, hands=(hand,), detect_ms=1.)
    monkeypatch.setattr(source.tracker, "step", track)
    send(0, mirrored=front, rotation=rotation)
    frame, image, _, hands, _ = source.read("Right", False)
    expected = transform(raw, desired, rotation)
    assert frame.mirrored is desired and frame.rotation == rotation
    assert (frame.width, frame.height) == (expected.shape[1], expected.shape[0])
    np.testing.assert_array_equal(image, expected)
    np.testing.assert_array_equal(tracked[0][1], expected)
    assert tracked[0][0] is frame and tracked[0][2] == "Right"
    assert hands[0].side == "Right"


def test_local_camera_pixels_and_metadata_are_not_transformed_again(tmp_path):
    source = source_with_phone(tmp_path)
    image = np.arange(36, dtype=np.uint8).reshape(3, 4, 3)
    frame = FrameInfo(0, time.perf_counter(), 4, 3, True)
    source.camera = SimpleNamespace(read=lambda **kwargs: (frame, image), release=lambda: None)
    source.settings.set("camera.mirror", False)
    result = source.read("Right", False)
    assert result[0] is frame and result[1] is image
    source.close()
