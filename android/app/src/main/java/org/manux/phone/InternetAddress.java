package org.manux.phone;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/** HTTPS origin boundary for the explicitly selected PC endpoint. No credentials in saved URLs. */
final class InternetAddress {
    static String validate(String input) {
        if (input == null || input.length() > 2048) throw new IllegalArgumentException("HTTPS 주소를 입력하세요.");
        String value = input.trim();
        if (value.indexOf('\\') >= 0 || value.chars().anyMatch(c -> c < 32 || c == 127))
            throw new IllegalArgumentException("올바른 HTTPS 주소를 입력하세요.");
        try {
            URI uri = new URI(value);
            if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null
                    || uri.getRawQuery() != null || uri.getRawFragment() != null || uri.getPort() > 65535 || uri.getPort() == 0)
                throw new IllegalArgumentException("PC의 HTTPS 주소만 입력하세요. 코드·토큰이 포함된 주소는 사용할 수 없습니다.");
            return new URI("https", null, uri.getHost().toLowerCase(Locale.ROOT), uri.getPort(),
                    uri.getPath().isEmpty() ? "/" : uri.getPath(), null, null).toASCIIString();
        } catch (URISyntaxException e) { throw new IllegalArgumentException("올바른 HTTPS 주소를 입력하세요."); }
    }
    static boolean sameOrigin(String selected, String candidate) {
        try {
            URI a = new URI(selected), b = new URI(candidate);
            return "https".equalsIgnoreCase(b.getScheme()) && b.getUserInfo() == null && b.getHost() != null
                    && a.getHost().equalsIgnoreCase(b.getHost()) && port(a) == port(b)
                    && candidate.indexOf('\\') < 0;
        } catch (Exception ignored) { return false; }
    }
    static boolean samePage(String selected, String candidate) {
        if (!sameOrigin(selected, candidate)) return false;
        try {
            URI a = new URI(selected), b = new URI(candidate);
            String path = b.getPath().isEmpty() ? "/" : b.getPath();
            return a.getPath().equals(path) && b.getRawQuery() == null;
        } catch (Exception ignored) { return false; }
    }
    private static int port(URI uri) { return uri.getPort() == -1 ? 443 : uri.getPort(); }
    private InternetAddress() { }
}
