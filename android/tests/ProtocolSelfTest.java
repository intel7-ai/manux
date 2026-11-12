package org.manux.phone;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.util.Arrays;

/** Dependency-free synthetic wire and camera orientation checks; no physical device input. */
public final class ProtocolSelfTest {
    public static void main(String[] args) throws Exception {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        Protocol.write(new DataOutputStream(bytes), Protocol.FRAME, new byte[]{4, 5, 6});
        check(Arrays.equals(bytes.toByteArray(), new byte[]{0, 0, 0, 4, 3, 4, 5, 6}), "network byte order and length");
        Protocol.Packet packet = Protocol.read(input(bytes.toByteArray()));
        check(packet.type == Protocol.FRAME && Arrays.equals(packet.payload, new byte[]{4, 5, 6}), "round trip");
        reject(new byte[]{0, 0, 0, 0}, "empty frame");
        reject(new byte[]{-1, -1, -1, -1}, "unsigned oversized length");
        reject(new byte[]{0, 16, 0, 1}, "oversized frame");
        reject(new byte[]{0, 0, 0, 1, 8}, "unknown type");
        reject(new byte[]{0, 0, 0, 4, 3, 1}, "truncated frame");
        bytes.reset();
        Protocol.write(new DataOutputStream(bytes), Protocol.ACK, new byte[]{1});
        Protocol.write(new DataOutputStream(bytes), Protocol.SCREEN_JPEG, new byte[]{2, 3});
        DataInputStream concatenated = input(bytes.toByteArray());
        check(Protocol.read(concatenated).type == Protocol.ACK, "first packed response");
        check(Arrays.equals(Protocol.read(concatenated).payload, new byte[]{2, 3}), "next packed response");

        // Asymmetric 4x2 luma and two VU chroma pairs catch wrong axis/channel ordering.
        byte[] nv21 = {1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 21, 22};
        byte[] cw = CameraGeometry.uprightNv21(nv21, 4, 2, 90);
        check(Arrays.equals(cw, new byte[]{5, 1, 6, 2, 7, 3, 8, 4, 11, 12, 21, 22}), "NV21 clockwise upright");
        check(Arrays.equals(CameraGeometry.uprightNv21(cw, 2, 4, 270), nv21), "portrait inverse rotation");
        check(Arrays.equals(CameraGeometry.uprightNv21(nv21, 4, 2, 180), new byte[]{8, 7, 6, 5, 4, 3, 2, 1, 21, 22, 11, 12}), "NV21 half turn keeps VU");
        for (int angle : new int[]{0, 90, 180, 270}) {
            byte[] rotated = CameraGeometry.uprightNv21(nv21, 4, 2, angle);
            int w = angle % 180 == 0 ? 4 : 2, h = angle % 180 == 0 ? 2 : 4;
            check(Arrays.equals(CameraGeometry.uprightNv21(rotated, w, h, (360 - angle) % 360), nv21), "rotation inverse " + angle);
        }
        ConnectionState connection = new ConnectionState(1000);
        connection.enter(ConnectionState.Phase.CONNECT, 1000);
        check(!connection.timedOut(30999), "connection has its own 30-second deadline");
        check(connection.timedOut(31000), "blocked connection eventually closes");
        connection.enter(ConnectionState.Phase.AUTH, 30000);
        check(!connection.timedOut(35000), "slow socket setup does not consume authentication deadline");
        check(connection.timedOut(40000), "silent authentication response times out");
        check(!connection.closeOnPause(), "transient system dialog preserves a pending connection");
        String safeDiagnostic = connection.failure(new IOException("secret-code=123456 peer=AA:BB:CC:DD:EE:FF"));
        check(safeDiagnostic.contains("BT-AUTH") && safeDiagnostic.contains("IOException"), "failure identifies the stage");
        check(!safeDiagnostic.contains("123456") && !safeDiagnostic.contains("AA:BB"), "exception payload stays private");
        connection.enter(ConnectionState.Phase.STREAM, 40000);
        check(connection.closeOnPause(), "active camera session closes when paused");
        check(!connection.timedOut(100000), "completed auth has no connection deadline");
        check(!DeviceSelection.allowed(0x400), "audio/video device is not a MANUX PC");
        check(DeviceSelection.allowed(0) && DeviceSelection.allowed(0x100), "unknown-class Windows PCs remain selectable");
        check(DeviceSelection.rank(0x100) < DeviceSelection.rank(0), "computers sort before unknown devices");
        check(DeviceSelection.deviceIndex(0, 3) == -1, "initial prompt can never connect to the first paired device");
        check(DeviceSelection.deviceIndex(1, 3) == 0, "explicit first-device selection maps correctly");
        check(DeviceSelection.deviceIndex(4, 3) == -1, "removed/stale selection is rejected");
        check(DeviceSelection.restorePosition(Arrays.asList("pc-two", "pc-one"), "pc-one") == 2, "refresh preserves identity after reordering");
        check(DeviceSelection.restorePosition(Arrays.asList("pc-two"), "pc-one") == 0, "missing former PC returns to prompt");
        check(DeviceSelection.restorePosition(Arrays.asList("pc-only"), null) == 0, "even a single PC is never auto-selected");
        check(InternetAddress.validate("https://Example.test").equals("https://example.test/"), "HTTPS endpoint normalization");
        check(InternetAddress.sameOrigin("https://example.test/", "https://example.test:443/screen"), "same secure origin API allowed");
        check(!InternetAddress.sameOrigin("https://example.test/", "https://example.test.evil.invalid/"), "hostname suffix attack denied");
        check(!InternetAddress.sameOrigin("https://example.test/", "http://example.test/"), "cleartext denied");
        check(!InternetAddress.sameOrigin("https://example.test/", "https://example.test:8443/"), "different port denied");
        check(!InternetAddress.sameOrigin("https://example.test/", "https://user@example.test/"), "URL credentials denied");
        check(!InternetAddress.samePage("https://example.test/", "https://example.test/elsewhere"), "other-page navigation denied");
        for (String unsafe : new String[]{"http://example.test", "javascript:alert(1)", "file:///tmp/test", "content://example.test", "https://example.test/?token=synthetic", "https://user@example.test", "https://example.test/#secret"}) {
            boolean denied = false;
            try { InternetAddress.validate(unsafe); } catch (IllegalArgumentException expected) { denied = true; }
            check(denied, "unsafe or credential-bearing URL rejected");
        }
        System.out.println("PASS: framing, NV21, connection privacy, PC selection, HTTPS origin/navigation boundary");
    }
    private static DataInputStream input(byte[] bytes) { return new DataInputStream(new ByteArrayInputStream(bytes)); }
    private static void reject(byte[] bytes, String label) throws Exception {
        try { Protocol.read(input(bytes)); } catch (IOException expected) { return; }
        throw new AssertionError("Accepted " + label);
    }
    private static void check(boolean valid, String label) { if (!valid) throw new AssertionError(label); }
}
