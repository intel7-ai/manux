package org.manux.phone;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothClass;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothSocket;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.Camera;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.Settings;
import android.text.InputFilter;
import android.text.InputType;
import android.view.Gravity;
import android.view.Surface;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.WindowManager;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.Spinner;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/** Foreground-only companion: one camera image in flight and one screen request. */
@SuppressWarnings("deprecation")
public final class MainActivity extends Activity implements SurfaceHolder.Callback {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService encoder = Executors.newSingleThreadExecutor();
    private final List<BluetoothDevice> devices = new ArrayList<>();
    private final AtomicLong nextSequence = new AtomicLong();
    private LinearLayout root, controls, wifiControls;
    private ScrollView controlScroll;
    private FrameLayout display;
    private Spinner devicePicker;
    private EditText codeInput, urlInput;
    private TextView status, screenHint;
    private Button connectButton, switchButton, fullscreenButton;
    private AlertDialog connectionGuide;
    private SurfaceView preview;
    private ImageView monitor;
    private BluetoothAdapter adapter;
    private volatile Session session;
    private Camera camera;
    private volatile long cameraGeneration;
    private boolean resumed, surfaceReady, front = true, fullscreen, connectAfterPermission;
    private int cameraWidth, cameraHeight, cameraRotation;
    private long lastScreenRequest, lastStatus;
    private String message = "PC와 휴대폰을 먼저 블루투스 설정에서 페어링하세요.";

    private static final class Session {
        final BluetoothSocket socket;
        final String deviceName;
        final Object writeLock = new Object();
        final AtomicBoolean encoding = new AtomicBoolean();
        final AtomicBoolean screenPending = new AtomicBoolean();
        final AtomicLong pendingSequence = new AtomicLong(-1);
        final ConnectionState connection = new ConnectionState(SystemClock.elapsedRealtime());
        volatile DataOutputStream out;
        volatile boolean authenticated, closed;
        volatile double clockOffset;
        volatile long authSent, pendingSince, screenSince, lastFrame;
        volatile long screenGeneration;
        volatile long accepted, screens, rejected;
        long started = SystemClock.elapsedRealtime();
        Session(BluetoothSocket socket, String deviceName) { this.socket = socket; this.deviceName = deviceName; }
        void send(int type, byte[] payload) throws IOException {
            synchronized (writeLock) {
                if (closed || out == null) throw new IOException("연결이 종료됐습니다");
                Protocol.write(out, type, payload);
            }
        }
        void close() {
            closed = true;
            authenticated = false;
            try { socket.close(); } catch (IOException ignored) { }
        }
    }

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        BluetoothManager manager = getSystemService(BluetoothManager.class);
        adapter = manager == null ? null : manager.getAdapter();
        buildUi();
        main.post(tick);
    }

    private int dp(int n) { return Math.round(n * getResources().getDisplayMetrics().density); }
    private LinearLayout row() {
        LinearLayout line = new LinearLayout(this);
        line.setOrientation(LinearLayout.HORIZONTAL);
        line.setGravity(Gravity.CENTER_VERTICAL);
        return line;
    }
    private Button button(String text, View.OnClickListener action) {
        Button b = new Button(this);
        b.setText(text);
        b.setTextSize(13);
        b.setAllCaps(false);
        b.setOnClickListener(action);
        return b;
    }
    private void addWeighted(LinearLayout line, View v) {
        line.addView(v, new LinearLayout.LayoutParams(0, -2, 1));
    }

    private void buildUi() {
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(17, 24, 36));
        root.setPadding(dp(12), dp(8), dp(12), dp(8));
        // Target 35 draws behind bars. Keep controls within the current system insets.
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            v.setPadding(dp(12) + insets.getSystemWindowInsetLeft(), dp(8) + insets.getSystemWindowInsetTop(),
                    dp(12) + insets.getSystemWindowInsetRight(), dp(8) + insets.getSystemWindowInsetBottom());
            return insets;
        });
        setContentView(root);
        controls = new LinearLayout(this);
        controls.setOrientation(LinearLayout.VERTICAL);
        controlScroll = new ScrollView(this);
        controlScroll.addView(controls);
        root.addView(controlScroll);
        TextView title = new TextView(this);
        title.setText(R.string.app_title);
        title.setTextColor(Color.WHITE);
        title.setTextSize(21);
        LinearLayout heading = row();
        addWeighted(heading, title);
        heading.addView(button(getString(R.string.connection_guide_title), v -> showConnectionGuide()));
        controls.addView(heading);
        Button internetButton = button("인터넷 연결 (테스트)", v -> openInternet());
        internetButton.setTextSize(16);
        controls.addView(internetButton, new LinearLayout.LayoutParams(-1, -2));
        LinearLayout deviceRow = row();
        devicePicker = new Spinner(this);
        addWeighted(deviceRow, devicePicker);
        deviceRow.addView(button("새로고침", v -> refreshDevices(true)));
        controls.addView(deviceRow);
        LinearLayout connectionRow = row();
        codeInput = new EditText(this);
        codeInput.setHint("PC 연결 코드 6자리");
        codeInput.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        codeInput.setFilters(new InputFilter[]{new InputFilter.LengthFilter(6)});
        codeInput.setSingleLine(true);
        codeInput.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        addWeighted(connectionRow, codeInput);
        connectButton = button("연결", v -> {
            if (session != null) disconnect("연결을 종료했습니다."); else connect();
        });
        connectionRow.addView(connectButton);
        controls.addView(connectionRow);
        LinearLayout actionRow = row();
        addWeighted(actionRow, button("블루투스 설정", v -> startActivity(new Intent(Settings.ACTION_BLUETOOTH_SETTINGS))));
        switchButton = button("전면 → 후면", v -> {
            front = !front;
            switchButton.setText(front ? "전면 → 후면" : "후면 → 전면");
            setFullscreen(false);
            stopCamera();
            monitor.setImageDrawable(null);
            updateMonitorVisibility();
            startCamera();
        });
        addWeighted(actionRow, switchButton);
        controls.addView(actionRow);
        status = new TextView(this);
        status.setTextColor(Color.rgb(187, 213, 240));
        status.setTextSize(13);
        status.setPadding(0, dp(6), 0, dp(6));
        status.setText(message);
        root.addView(status);
        display = new FrameLayout(this);
        display.setBackgroundColor(Color.BLACK);
        root.addView(display, new LinearLayout.LayoutParams(-1, 0, 1));
        preview = new SurfaceView(this);
        preview.getHolder().addCallback(this);
        display.addView(preview, new FrameLayout.LayoutParams(-1, -1));
        monitor = new ImageView(this);
        monitor.setBackgroundColor(Color.BLACK);
        monitor.setScaleType(ImageView.ScaleType.FIT_CENTER);
        display.addView(monitor, new FrameLayout.LayoutParams(-1, -1));
        screenHint = new TextView(this);
        screenHint.setGravity(Gravity.CENTER);
        screenHint.setTextColor(Color.LTGRAY);
        screenHint.setText("전면 카메라 연결 후\nPC 모니터 전체 화면이 표시됩니다.");
        display.addView(screenHint, new FrameLayout.LayoutParams(-1, -1));
        fullscreenButton = button("PC 화면 전체보기", v -> {
            if (!front) { toast("전면 카메라에서 PC 화면을 볼 수 있습니다."); return; }
            setFullscreen(!fullscreen);
        });
        root.addView(fullscreenButton, new LinearLayout.LayoutParams(-1, -2));
        wifiControls = new LinearLayout(this);
        wifiControls.setOrientation(LinearLayout.VERTICAL);
        urlInput = new EditText(this);
        urlInput.setHint("Wi-Fi 연결 주소 (https://…)");
        urlInput.setSingleLine(true);
        urlInput.setText(getPreferences(MODE_PRIVATE).getString("wifi_url", ""));
        urlInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        LinearLayout wifiRow = row();
        addWeighted(wifiRow, urlInput);
        wifiRow.addView(button("Wi-Fi 연결", v -> openWifi()));
        wifiControls.addView(wifiRow);
        TextView hint = new TextView(this);
        hint.setText(R.string.connection_hint);
        hint.setTextColor(Color.GRAY);
        hint.setTextSize(11);
        wifiControls.addView(hint);
        root.addView(wifiControls);
        updateMonitorVisibility();
        applyLayoutMode();
    }

    private void showConnectionGuide() {
        if (connectionGuide != null && connectionGuide.isShowing()) return;
        connectionGuide = new AlertDialog.Builder(this)
                .setTitle(R.string.connection_guide_title)
                .setMessage(R.string.connection_guide_body)
                .setPositiveButton(R.string.connection_guide_close, (dialog, which) -> dialog.dismiss())
                .create();
        connectionGuide.show();
    }

    private void openInternet() {
        EditText address = new EditText(this);
        address.setSingleLine(true);
        address.setHint("PC의 인터넷 주소 https://…");
        address.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        address.setText(getPreferences(MODE_PRIVATE).getString("internet_url", ""));
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("다른 Wi-Fi · LTE에서 연결")
                .setMessage("PC MANUX에서 인터넷 연결을 켜고 표시된 HTTPS 주소를 입력하세요. PC와 휴대폰의 인터넷 연결이 모두 필요합니다.")
                .setView(address).setNegativeButton("취소", (d, which) -> d.dismiss())
                .setPositiveButton("앱에서 열기", null).create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            try {
                String endpoint = InternetAddress.validate(address.getText().toString());
                getPreferences(MODE_PRIVATE).edit().putString("internet_url", endpoint).apply();
                dialog.dismiss();
                startActivity(new Intent(this, InternetActivity.class).putExtra("endpoint", endpoint));
            } catch (IllegalArgumentException e) { address.setError(e.getMessage()); }
        }));
        dialog.show();
    }

    private void openWifi() {
        String value = urlInput.getText().toString().trim();
        Uri uri = Uri.parse(value);
        if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null) {
            toast("PC 연결 창에 표시된 https:// 주소를 입력하세요.");
            return;
        }
        getPreferences(MODE_PRIVATE).edit().putString("wifi_url", value).apply();
        try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); }
        catch (RuntimeException e) { toast("주소를 열 수 있는 브라우저를 설치하세요."); }
    }

    private boolean havePermissions() {
        return checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
                && (Build.VERSION.SDK_INT < 31 || (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED
                && checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN) == PackageManager.PERMISSION_GRANTED));
    }
    private void askPermissions() {
        List<String> missing = new ArrayList<>();
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) missing.add(Manifest.permission.CAMERA);
        if (Build.VERSION.SDK_INT >= 31 && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED)
            missing.add(Manifest.permission.BLUETOOTH_CONNECT);
        if (Build.VERSION.SDK_INT >= 31 && checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN) != PackageManager.PERMISSION_GRANTED)
            missing.add(Manifest.permission.BLUETOOTH_SCAN);
        requestPermissions(missing.toArray(new String[0]), 10);
    }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(request, permissions, grants);
        if (request != 10) return;
        if (havePermissions()) {
            refreshDevices(false);
            if (connectAfterPermission) setMessage("권한이 준비됐습니다. 연결할 PC를 직접 선택한 뒤 ‘연결’을 눌러 주세요.");
        } else setMessage("카메라와 근처 기기 권한이 필요합니다. 앱 설정에서 허용하세요.");
        connectAfterPermission = false;
    }

    private void refreshDevices(boolean requestPermission) {
        if (adapter == null) { setMessage("이 휴대폰에는 블루투스 기능이 없습니다. Wi-Fi 연결을 이용하세요."); return; }
        if (Build.VERSION.SDK_INT >= 31 && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) {
            if (requestPermission) askPermissions();
            return;
        }
        try {
            if (!adapter.isEnabled()) { setMessage("휴대폰 블루투스를 켜고 PC와 페어링하세요."); return; }
            int previousIndex = DeviceSelection.deviceIndex(devicePicker.getSelectedItemPosition(), devices.size());
            String selected = previousIndex < 0 ? null : devices.get(previousIndex).getAddress();
            devices.clear();
            for (BluetoothDevice paired : adapter.getBondedDevices()) {
                if (DeviceSelection.allowed(majorClass(paired))) devices.add(paired);
            }
            devices.sort(Comparator.comparingInt((BluetoothDevice d) -> DeviceSelection.rank(majorClass(d)))
                    .thenComparing(d -> d.getName() == null ? "" : d.getName()));
            List<String> labels = new ArrayList<>();
            List<String> addresses = new ArrayList<>();
            labels.add("연결할 PC를 선택하세요");
            for (int i = 0; i < devices.size(); i++) {
                BluetoothDevice d = devices.get(i);
                String group = majorClass(d) == DeviceSelection.COMPUTER ? "[PC] " : "[기타 · PC인지 확인] ";
                labels.add(group + displayName(d) + " · " + d.getAddress());
                addresses.add(d.getAddress());
            }
            ArrayAdapter<String> choices = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, labels);
            choices.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
            devicePicker.setAdapter(choices);
            devicePicker.setSelection(DeviceSelection.restorePosition(addresses, selected));
            if (devices.isEmpty()) setMessage("선택할 PC가 없습니다. PC와 페어링한 뒤 새로고침하세요. 스피커·이어폰은 목록에서 제외합니다.");
        } catch (SecurityException e) { setMessage("근처 기기 권한을 허용하세요."); }
    }

    private static int majorClass(BluetoothDevice device) {
        try {
            BluetoothClass classification = device.getBluetoothClass();
            return classification == null ? 0 : classification.getMajorDeviceClass();
        } catch (SecurityException ignored) { return 0; }
    }
    private static String displayName(BluetoothDevice device) {
        try {
            String name = device.getName();
            if (name == null || name.trim().isEmpty()) return "이름 없는 기기";
            name = name.replace('\n', ' ').replace('\r', ' ').trim();
            return name.length() <= 80 ? name : name.substring(0, 80);
        } catch (SecurityException ignored) { return "기기 이름 권한 필요"; }
    }

    private void connect() {
        if (!havePermissions()) { connectAfterPermission = true; askPermissions(); return; }
        if (session != null) return;
        try {
            if (adapter == null || !adapter.isEnabled()) { setMessage("블루투스를 먼저 켜세요."); return; }
        } catch (SecurityException e) { setMessage("근처 기기 권한을 허용하고 다시 연결하세요."); return; }
        if (devices.isEmpty()) { refreshDevices(false); setMessage("블루투스 설정에서 PC와 먼저 페어링하세요."); return; }
        int selectedIndex = DeviceSelection.deviceIndex(devicePicker.getSelectedItemPosition(), devices.size());
        if (selectedIndex < 0) { setMessage("위 목록에서 MANUX가 실행 중인 PC를 직접 선택하세요. 스피커·이어폰에는 연결할 수 없습니다."); return; }
        String code = codeInput.getText().toString();
        if (!code.matches("[0-9]{6}")) { toast("PC 앱의 새 연결 코드 6자리를 입력하세요."); return; }
        BluetoothDevice device = devices.get(selectedIndex);
        try {
            if (!DeviceSelection.allowed(majorClass(device))) { setMessage("오디오 기기에는 연결할 수 없습니다. MANUX가 실행 중인 PC를 선택하세요."); return; }
            Session candidate = new Session(device.createRfcommSocketToServiceRecord(Protocol.SERVICE), displayName(device));
            session = candidate;
            connectButton.setText("연결 취소");
            devicePicker.setEnabled(false);
            codeInput.setEnabled(false);
            setMessage(candidate.connection.progress() + "\n선택한 PC: " + candidate.deviceName);
            Thread reader = new Thread(() -> runConnection(candidate, code), "manux-bluetooth");
            reader.start();
        } catch (IOException | SecurityException e) { setMessage("연결을 시작할 수 없습니다. PC 페어링 상태를 확인하세요."); }
    }

    private void runConnection(Session current, String code) {
        try {
            // Android requires discovery to be cancelled before SDP/RFCOMM connection,
            // even if another app or system Settings started it. No discovery is initiated here.
            adapter.cancelDiscovery();
            if (current.closed) return;
            setConnectionPhase(current, ConnectionState.Phase.CONNECT);
            current.socket.connect();
            if (current.closed) return;
            setConnectionPhase(current, ConnectionState.Phase.AUTH);
            current.out = new DataOutputStream(current.socket.getOutputStream());
            DataInputStream in = new DataInputStream(current.socket.getInputStream());
            current.authSent = System.currentTimeMillis();
            JSONObject auth = new JSONObject().put("code", code).put("device", "MANUX Android");
            current.send(Protocol.AUTH, json(auth));
            while (!current.closed) handlePacket(current, Protocol.read(in));
        } catch (SecurityException e) {
            if (!current.closed) fail(current, current.connection.failure(e));
        } catch (Exception e) {
            if (!current.closed) fail(current, current.connection.failure(e));
        } finally {
            current.close();
        }
    }

    private void setConnectionPhase(Session current, ConnectionState.Phase phase) {
        current.connection.enter(phase, SystemClock.elapsedRealtime());
        main.post(() -> {
            if (session == current && !current.closed && current.connection.phase() == phase)
                setMessage(current.connection.progress() + "\n선택한 PC: " + current.deviceName);
        });
    }

    private static byte[] json(JSONObject object) { return object.toString().getBytes(StandardCharsets.UTF_8); }
    private static JSONObject object(byte[] data) throws Exception {
        if (data.length > 16 * 1024) throw new IOException("설정 메시지 크기 초과");
        return new JSONObject(new String(data, StandardCharsets.UTF_8));
    }

    private void handlePacket(Session current, Protocol.Packet packet) throws Exception {
        if (session != current || current.closed) return;
        if (packet.type == Protocol.READY) {
            if (current.authenticated) throw new IOException("중복 인증 메시지");
            double server = object(packet.payload).getDouble("server_time");
            if (!Double.isFinite(server) || server <= 0) throw new IOException("PC 시간 오류");
            current.clockOffset = server - (current.authSent + System.currentTimeMillis()) / 2000.0;
            current.authenticated = true;
            current.connection.enter(ConnectionState.Phase.STREAM, SystemClock.elapsedRealtime());
            current.started = SystemClock.elapsedRealtime();
            main.post(() -> {
                if (session != current || current.closed) return;
                codeInput.setText("");
                connectButton.setText("연결 종료");
                setMessage(resumed ? "연결됨 · 카메라를 시작합니다." : "연결됨 · 앱으로 돌아오면 카메라를 시작합니다.");
                startCamera();
            });
        } else if (packet.type == Protocol.ACK) {
            if (!current.authenticated) throw new IOException("인증 전 응답");
            JSONObject ack = object(packet.payload);
            long sequence = ack.getLong("seq");
            if (current.pendingSequence.compareAndSet(sequence, -1)) {
                if (ack.optBoolean("accepted", false)) current.accepted++; else current.rejected++;
            }
        } else if (packet.type == Protocol.SCREEN_JPEG) {
            if (!current.authenticated || !current.screenPending.get()) return;
            BitmapFactory.Options bounds = new BitmapFactory.Options();
            bounds.inJustDecodeBounds = true;
            BitmapFactory.decodeByteArray(packet.payload, 0, packet.payload.length, bounds);
            if (bounds.outWidth <= 0 || bounds.outHeight <= 0 || bounds.outWidth > 1920 || bounds.outHeight > 1920
                    || (long) bounds.outWidth * bounds.outHeight > 1920L * 1080) throw new IOException("PC 화면 크기 오류");
            Bitmap image = BitmapFactory.decodeByteArray(packet.payload, 0, packet.payload.length);
            if (image == null) throw new IOException("PC 화면 해독 실패");
            main.post(() -> {
                if (session == current && front && !current.closed && current.screenGeneration == cameraGeneration) {
                    monitor.setImageBitmap(image);
                    screenHint.setVisibility(View.GONE);
                    current.screens++;
                } else image.recycle();
                current.screenPending.set(false);
            });
        } else if (packet.type == Protocol.ERROR) {
            JSONObject error = object(packet.payload);
            String detail = switch (error.optString("error")) {
                case "authentication_failed" -> "[BT-AUTH · AUTH_REJECTED] 연결 코드가 만료됐거나 올바르지 않습니다. PC의 새 코드로 연결하세요.";
                case "screen_unavailable" -> "PC 화면을 준비하는 중입니다. 잠시 후 다시 요청합니다.";
                case "screen_busy" -> "PC 화면을 받는 중입니다. 잠시 기다려 주세요.";
                case "invalid_request" -> "PC와 휴대폰 앱 버전을 확인하세요. 지원하지 않는 연결 요청입니다.";
                default -> "PC 연결 상태를 확인하세요.";
            };
            if (!current.authenticated) { fail(current, detail); return; }
            if ("screen".equals(error.optString("request"))) current.screenPending.set(false);
            else { fail(current, detail); return; }
            final String shown = detail;
            main.post(() -> { if (session == current) setMessage(shown); });
        } else throw new IOException("PC에서 잘못된 메시지를 받았습니다");
    }

    private void fail(Session current, String reason) {
        current.close();
        main.post(() -> { if (session == current) disconnect(reason); });
    }

    private void disconnect(String reason) {
        Session old = session;
        session = null;
        if (old != null) old.close();
        stopCamera();
        setFullscreen(false);
        monitor.setImageDrawable(null);
        updateMonitorVisibility();
        connectButton.setText("연결");
        devicePicker.setEnabled(true);
        codeInput.setEnabled(true);
        codeInput.setText("");
        setMessage(reason);
    }

    private final Runnable tick = new Runnable() {
        @Override public void run() {
            Session current = session;
            long now = SystemClock.elapsedRealtime();
            if (current != null && !current.closed) {
                if (!current.authenticated && current.connection.timedOut(now)) {
                    disconnect(current.connection.failure("TIMEOUT"));
                } else if (current.authenticated && current.pendingSequence.get() >= 0 && now - current.pendingSince > 5000) {
                    disconnect("영상 응답이 멈췄습니다. PC에 가까이 이동한 뒤 새 코드로 연결하세요.");
                } else if (current.authenticated) {
                    if (current.screenPending.get() && now - current.screenSince > 5000) {
                        disconnect("PC 화면 응답이 멈췄습니다. 새 코드로 다시 연결하세요.");
                    } else if (front && current.accepted > 0 && now - lastScreenRequest >= 600
                            && current.screenPending.compareAndSet(false, true)) {
                        lastScreenRequest = now;
                        current.screenSince = now;
                        current.screenGeneration = cameraGeneration;
                        encoder.execute(() -> {
                            if (session != current || current.closed) return;
                            try { current.send(Protocol.SCREEN_REQUEST, json(new JSONObject().put("profile", "compact"))); }
                            catch (Exception e) { fail(current, "PC 화면 요청 중 연결이 끊겼습니다."); }
                        });
                    }
                    if (now - lastStatus > 1000) {
                        lastStatus = now;
                        double elapsed = Math.max(1, (now - current.started) / 1000.0);
                        status.setText(String.format(Locale.KOREA, "Bluetooth 연결됨 · 카메라 %.1f fps · PC 화면 %.1f fps%s",
                                current.accepted / elapsed, current.screens / elapsed,
                                current.rejected > 0 ? "\n지연된 프레임은 건너뜁니다. PC에 가까이 이동하세요." : ""));
                    }
                }
            }
            main.postDelayed(this, 200);
        }
    };

    private void startCamera() {
        Session current = session;
        if (!resumed || !surfaceReady || camera != null || current == null || !current.authenticated || current.closed) return;
        if (!havePermissions()) { disconnect("카메라 권한이 필요합니다."); return; }
        try {
            int wanted = front ? Camera.CameraInfo.CAMERA_FACING_FRONT : Camera.CameraInfo.CAMERA_FACING_BACK;
            int selected = -1;
            Camera.CameraInfo info = new Camera.CameraInfo();
            for (int i = 0; i < Camera.getNumberOfCameras(); i++) {
                Camera.getCameraInfo(i, info);
                if (info.facing == wanted) { selected = i; break; }
            }
            if (selected < 0) throw new IOException(front ? "전면 카메라가 없습니다. 후면으로 전환하세요." : "후면 카메라가 없습니다. 전면으로 전환하세요.");
            camera = Camera.open(selected);
            Camera.Parameters params = camera.getParameters();
            Camera.Size size = params.getSupportedPreviewSizes().stream().min(Comparator.comparingLong(s ->
                    Math.abs(s.width * s.height - 320L * 240) + 3L * Math.abs(s.width * 3 - s.height * 4))).get();
            cameraWidth = size.width;
            cameraHeight = size.height;
            params.setPreviewSize(cameraWidth, cameraHeight);
            params.setPreviewFormat(ImageFormat.NV21);
            List<String> focus = params.getSupportedFocusModes();
            if (focus != null && focus.contains(Camera.Parameters.FOCUS_MODE_CONTINUOUS_VIDEO))
                params.setFocusMode(Camera.Parameters.FOCUS_MODE_CONTINUOUS_VIDEO);
            camera.setParameters(params);
            int rotation = getWindowManager().getDefaultDisplay().getRotation();
            int degrees = rotation == Surface.ROTATION_90 ? 90 : rotation == Surface.ROTATION_180 ? 180 : rotation == Surface.ROTATION_270 ? 270 : 0;
            cameraRotation = front ? (info.orientation + degrees) % 360 : (info.orientation - degrees + 360) % 360;
            camera.setDisplayOrientation(front ? (360 - cameraRotation) % 360 : cameraRotation);
            camera.setPreviewDisplay(preview.getHolder());
            camera.setErrorCallback((error, source) -> {
                if (camera == source) disconnect("카메라 연결이 중단됐습니다. 다른 카메라 앱을 닫고 다시 연결하세요.");
            });
            camera.setPreviewCallbackWithBuffer(this::onCameraFrame);
            int bytes = cameraWidth * cameraHeight * ImageFormat.getBitsPerPixel(ImageFormat.NV21) / 8;
            for (int i = 0; i < 3; i++) camera.addCallbackBuffer(new byte[bytes]);
            camera.startPreview();
            updateMonitorVisibility();
            preview.post(this::fitPreview);
        } catch (Exception e) {
            stopCamera();
            disconnect("카메라를 열 수 없습니다. 카메라 권한과 다른 앱의 카메라 사용을 확인하세요.");
        }
    }

    private void onCameraFrame(byte[] data, Camera source) {
        try {
            Session current = session;
            long now = SystemClock.elapsedRealtime();
            if (data == null || camera != source || current == null || !current.authenticated || current.closed
                    || current.pendingSequence.get() >= 0 || now - current.lastFrame < 100
                    || !current.encoding.compareAndSet(false, true)) return;
            current.lastFrame = now;
            byte[] copy = Arrays.copyOf(data, data.length);
            long generation = cameraGeneration;
            int width = cameraWidth, height = cameraHeight, rotation = cameraRotation;
            boolean mirrored = front;
            double capturedAt = System.currentTimeMillis() / 1000.0 + current.clockOffset;
            long sequence = nextSequence.incrementAndGet();
            encoder.execute(() -> {
                try {
                    if (session != current || current.closed || generation != cameraGeneration) return;
                    byte[] upright = CameraGeometry.uprightNv21(copy, width, height, rotation);
                    int uprightWidth = rotation % 180 == 0 ? width : height;
                    int uprightHeight = rotation % 180 == 0 ? height : width;
                    ByteArrayOutputStream jpeg = new ByteArrayOutputStream();
                    YuvImage yuv = new YuvImage(upright, ImageFormat.NV21, uprightWidth, uprightHeight, null);
                    if (!yuv.compressToJpeg(new Rect(0, 0, uprightWidth, uprightHeight), 45, jpeg)) throw new IOException("카메라 영상 압축 실패");
                    byte[] meta = json(new JSONObject().put("kind", "frame").put("seq", sequence)
                            .put("captured_at", capturedAt).put("width", uprightWidth).put("height", uprightHeight)
                            .put("mirrored", mirrored).put("rotation", 0).put("facing", mirrored ? "user" : "environment"));
                    ByteArrayOutputStream envelope = new ByteArrayOutputStream(meta.length + jpeg.size() + 4);
                    DataOutputStream packet = new DataOutputStream(envelope);
                    packet.writeInt(meta.length);
                    packet.write(meta);
                    jpeg.writeTo(envelope);
                    if (session != current || current.closed || generation != cameraGeneration) return;
                    current.pendingSince = SystemClock.elapsedRealtime();
                    current.pendingSequence.set(sequence);
                    current.send(Protocol.FRAME, envelope.toByteArray());
                } catch (Exception e) { fail(current, "카메라 영상 전송이 중단됐습니다. 다시 연결하세요."); }
                finally { current.encoding.set(false); }
            });
        } finally {
            if (camera == source && data != null) {
                try { source.addCallbackBuffer(data); } catch (RuntimeException ignored) { }
            }
        }
    }

    private void stopCamera() {
        cameraGeneration++;
        Camera old = camera;
        camera = null;
        if (old == null) return;
        try { old.setPreviewCallbackWithBuffer(null); old.setErrorCallback(null); old.stopPreview(); }
        catch (RuntimeException ignored) { }
        finally { old.release(); }
    }

    private void updateMonitorVisibility() {
        monitor.setVisibility(front ? View.VISIBLE : View.GONE);
        screenHint.setVisibility(front ? View.VISIBLE : View.GONE);
        fullscreenButton.setEnabled(front);
    }
    private void setFullscreen(boolean value) {
        fullscreen = value;
        applyLayoutMode();
        fullscreenButton.setText(value ? "전체보기 닫기" : "PC 화면 전체보기");
        getWindow().getDecorView().setSystemUiVisibility(value ? View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_STABLE : View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
        preview.post(this::fitPreview);
    }
    private void applyLayoutMode() {
        boolean landscape = getResources().getConfiguration().orientation == Configuration.ORIENTATION_LANDSCAPE;
        controlScroll.setVisibility(fullscreen ? View.GONE : View.VISIBLE);
        controlScroll.setLayoutParams(new LinearLayout.LayoutParams(-1, landscape
                ? dp(Math.min(150, Math.max(96, getResources().getConfiguration().screenHeightDp / 3))) : -2));
        wifiControls.setVisibility(fullscreen || landscape ? View.GONE : View.VISIBLE);
        status.setVisibility(fullscreen ? View.GONE : View.VISIBLE);
        status.setMaxLines(landscape ? 1 : 3);
    }
    private void fitPreview() {
        if (display.getWidth() == 0 || display.getHeight() == 0 || cameraWidth == 0 || cameraHeight == 0) return;
        int width = cameraRotation % 180 == 0 ? cameraWidth : cameraHeight;
        int height = cameraRotation % 180 == 0 ? cameraHeight : cameraWidth;
        double scale = Math.min(display.getWidth() / (double) width, display.getHeight() / (double) height);
        preview.setLayoutParams(new FrameLayout.LayoutParams((int) (width * scale), (int) (height * scale), Gravity.CENTER));
    }
    private void setMessage(String value) { message = value; status.setText(value); }
    private void toast(String value) { Toast.makeText(this, value, Toast.LENGTH_LONG).show(); }

    @Override public void surfaceCreated(SurfaceHolder holder) { surfaceReady = true; startCamera(); }
    @Override public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) { }
    @Override public void surfaceDestroyed(SurfaceHolder holder) { surfaceReady = false; stopCamera(); }
    @Override public void onConfigurationChanged(Configuration configuration) {
        super.onConfigurationChanged(configuration);
        applyLayoutMode();
        stopCamera();
        startCamera();
    }
    @Override protected void onResume() { super.onResume(); resumed = true; refreshDevices(false); startCamera(); }
    @Override protected void onPause() {
        resumed = false;
        stopCamera();
        // A pairing/system dialog can pause this Activity without hiding it. Preserve
        // the bounded pre-camera connection attempt until resume or a real onStop.
        if (session != null && session.connection.closeOnPause())
            disconnect("앱이 대기 상태가 되어 연결을 종료했습니다. PC의 새 코드로 다시 연결하세요.");
        super.onPause();
    }
    @Override protected void onStop() {
        if (session != null) {
            String stage = session.connection.phase().code;
            disconnect("[" + stage + " · APP_BACKGROUND] 앱이 화면에서 닫혀 연결을 종료했습니다. 앱으로 돌아와 다시 연결하세요.");
        }
        super.onStop();
    }
    @Override protected void onDestroy() {
        if (connectionGuide != null) connectionGuide.dismiss();
        main.removeCallbacks(tick);
        Session old = session;
        session = null;
        if (old != null) old.close();
        stopCamera();
        encoder.shutdownNow();
        super.onDestroy();
    }
    @Override public void onBackPressed() {
        if (fullscreen) setFullscreen(false); else super.onBackPressed();
    }
}
