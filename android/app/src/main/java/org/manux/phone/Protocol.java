package org.manux.phone;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.util.UUID;

/** Length includes the type byte. All multi-byte lengths are network byte order. */
final class Protocol {
    static final UUID SERVICE = UUID.fromString("6f8c5d2a-4e10-4a61-9c23-58f0c17b2e90");
    static final int MAX_PACKET = 1024 * 1024;
    static final int AUTH = 1, READY = 2, FRAME = 3, ACK = 4;
    static final int SCREEN_REQUEST = 5, SCREEN_JPEG = 6, ERROR = 7;

    static final class Packet {
        final int type;
        final byte[] payload;
        Packet(int type, byte[] payload) { this.type = type; this.payload = payload; }
    }

    static Packet read(DataInputStream in) throws IOException {
        int size = in.readInt();
        if (size < 1 || size > MAX_PACKET) throw new IOException("잘못된 패킷 크기");
        int type = in.readUnsignedByte();
        if (type < AUTH || type > ERROR) throw new IOException("지원하지 않는 메시지");
        byte[] payload = new byte[size - 1];
        in.readFully(payload);
        return new Packet(type, payload);
    }

    static void write(DataOutputStream out, int type, byte[] payload) throws IOException {
        if (type < AUTH || type > ERROR || payload.length >= MAX_PACKET)
            throw new IOException("전송할 패킷이 너무 큽니다");
        out.writeInt(payload.length + 1);
        out.writeByte(type);
        out.write(payload);
        out.flush();
    }

    private Protocol() { }
}
