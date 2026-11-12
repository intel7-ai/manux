"""Real QML dialog, queued commands and URL-only QR; no real phone or camera."""
import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QObject,QMetaObject,Qt
from PySide6.QtTest import QTest
from manux.app import schema
from manux.settings.store import Settings
from manux.ui.bridge import Bridge
from manux.ui.view import ViewFrame
from test_bridge import FakeRuntime


@pytest.mark.parametrize("layout",["studio","standard"])
def test_phone_dialog_commands_qr_and_both_layouts(tmp_path,layout):
    pytest.importorskip("qrcode")
    settings=Settings(schema())
    runtime=FakeRuntime(settings)
    requests=[]
    runtime.request=lambda c:requests.append(c)
    bridge=Bridge(runtime,settings,tmp_path,layout=layout)
    try:
        dialog=bridge.window.findChild(QObject,"phoneCameraDialog")
        assert dialog is not None
        view=ViewFrame(None,phone=dict(available=True,enabled=True,url="https://127.0.0.1:8443",
            code="123456",seconds=120,connected=False,blocked=False,status="휴대폰 연결 대기"))
        runtime.view=(1,view)
        bridge.apply_view(view)
        QMetaObject.invokeMethod(dialog,"open")
        QTest.qWait(100)
        assert dialog.property("visible")
        assert bridge.phoneQrUrl and not bridge.phone_qr_provider.image.isNull()
        bridge.phoneCameraAction("pair", "")
        assert requests[-1].kind=="phone_camera" and requests[-1].path=="pair"
        # QR may contain the public listener URL, never the code or token.
        cv2=pytest.importorskip("cv2")
        import numpy as np
        image=bridge.phone_qr_provider.image
        array=np.frombuffer(image.bits(),dtype=np.uint8).reshape(image.height(),image.bytesPerLine())
        rgb=array[:,:image.width()*4].reshape(image.height(),image.width(),4)
        url,_,_=cv2.QRCodeDetector().detectAndDecode(rgb)
        assert url=="https://127.0.0.1:8443" and "123456" not in url
        commands=runtime.engine.commands.drain()
        assert any(getattr(c,"kind",None)=="connecting" and c.on for c in commands)
        QMetaObject.invokeMethod(dialog,"close")
        QTest.qWait(100)
        assert any(getattr(c,"kind",None)=="connecting" and not c.on for c in runtime.engine.commands.drain())
    finally:
        bridge.close()


@pytest.mark.parametrize("layout", ["studio", "standard"])
def test_bluetooth_dialog_queues_opt_in_and_does_not_show_wifi_qr(tmp_path, layout):
    settings = Settings(schema())
    runtime = FakeRuntime(settings)
    requests = []
    runtime.request = requests.append
    bridge = Bridge(runtime, settings, tmp_path, layout=layout)
    try:
        dialog = bridge.window.findChild(QObject, "phoneCameraDialog")
        def publish(phone):
            view = ViewFrame(None, phone=phone)
            runtime.view = (runtime.view[0] + 1, view)
            bridge.apply_view(view)
        publish(dict(available=True, bluetooth_available=True, internet_available=True, enabled=False, transport="", url=""))
        QMetaObject.invokeMethod(dialog, "open")
        QTest.qWait(50)
        button = dialog.findChild(QObject, "startBluetoothCamera")
        assert button.property("visible") and button.property("enabled")
        QMetaObject.invokeMethod(button, "clicked")
        assert requests[-1].kind == "phone_camera" and requests[-1].path == "bluetooth"
        internet = dialog.findChild(QObject, "startInternetCamera")
        assert internet.property("visible") and internet.property("enabled")
        QMetaObject.invokeMethod(internet, "clicked")
        assert requests[-1].path == "internet"
        publish(dict(available=True, enabled=True, transport="wifi", url="https://127.0.0.1:8443"))
        assert bridge.phoneQrUrl
        publish(dict(available=True, enabled=True, transport="bluetooth", url="", code="123456", seconds=120))
        QTest.qWait(30)
        assert not bridge.phoneQrUrl
        assert not dialog.findChild(QObject, "phoneQr").property("visible")
        assert not dialog.findChild(QObject, "phoneUrl").property("visible")
        assert dialog.findChild(QObject, "phonePairCode").property("visible")
        publish(dict(available=True, enabled=True, transport="internet", url="https://synthetic.example.invalid",
                     code="123456", seconds=120, media_mode="webrtc"))
        QTest.qWait(30)
        assert dialog.property("internet") and bridge.phoneQrUrl
        assert dialog.findChild(QObject, "phoneUrl").property("visible")
        instructions = dialog.findChild(QObject, "phoneConnectionInstructions")
        assert "테스트 주소" in instructions.property("text")
        publish(dict(available=True, enabled=True, transport="internet", fixed_address=True,
                     url="https://stable.example.test", code="123456", seconds=120))
        QTest.qWait(30)
        assert dialog.findChild(QObject, "phoneUrl").property("text") == "https://stable.example.test"
        assert "고정 주소는 다시 켜도 같습니다" in instructions.property("text")
        assert "테스트 주소" not in instructions.property("text")
    finally:
        bridge.close()
