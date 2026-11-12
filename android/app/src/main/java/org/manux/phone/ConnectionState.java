package org.manux.phone;

import java.io.IOException;

/** Connection phase timing and safe diagnostics contain no peer identifiers or exception messages. */
final class ConnectionState {
    enum Phase {
        DISCOVERY("BT-DISCOVERY", "휴대폰의 기기 검색을 정리하는 중…", 10000),
        CONNECT("BT-CONNECT", "PC Bluetooth 서비스에 연결하는 중…", 30000),
        AUTH("BT-AUTH", "PC 연결 코드를 확인하는 중…", 10000),
        STREAM("BT-STREAM", "Bluetooth 연결됨", 0);

        final String code, progress;
        final long timeoutMs;
        Phase(String code, String progress, long timeoutMs) {
            this.code = code;
            this.progress = progress;
            this.timeoutMs = timeoutMs;
        }
    }

    private Phase phase = Phase.DISCOVERY;
    private long phaseStarted;

    ConnectionState(long now) { phaseStarted = now; }
    synchronized void enter(Phase value, long now) { phaseStarted = now; phase = value; }
    synchronized Phase phase() { return phase; }
    synchronized boolean timedOut(long now) { return phase.timeoutMs > 0 && now - phaseStarted >= phase.timeoutMs; }
    synchronized boolean closeOnPause() { return phase == Phase.STREAM; }
    synchronized String progress() { return "[" + phase.code + "] " + phase.progress; }

    synchronized String failure(String category) {
        String advice = switch (phase) {
            case DISCOVERY -> "근처 기기 권한을 허용하고 Bluetooth 설정 화면에서 앱으로 돌아와 다시 연결하세요.";
            case CONNECT -> "PC Bluetooth 서비스에 연결하지 못했습니다. PC 앱의 Bluetooth 연결 창, 선택한 PC와 페어링을 확인하세요.";
            case AUTH -> "Bluetooth는 연결됐지만 PC 인증이 완료되지 않았습니다. PC에서 새 코드를 받아 다시 연결하세요.";
            case STREAM -> "영상 전송 연결이 중단됐습니다. PC에 가까이 이동한 뒤 새 코드로 다시 연결하세요.";
        };
        return "[" + phase.code + " · " + category + "] " + advice;
    }

    synchronized String failure(Throwable error) {
        String category = error instanceof SecurityException ? "PERMISSION"
                : error instanceof IOException ? "IOException"
                : error instanceof RuntimeException ? "RuntimeException" : "PROTOCOL";
        return failure(category);
    }
}
