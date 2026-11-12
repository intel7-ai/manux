package org.manux.phone;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.http.SslError;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.ByteArrayInputStream;
import java.util.Arrays;

/** Isolated HTTPS-only app screen. No JavaScript/native bridge and no certificate bypass. */
@SuppressWarnings("deprecation")
public final class InternetActivity extends Activity {
    private String endpoint;
    private FrameLayout root, browserArea;
    private LinearLayout normal;
    private TextView status;
    private WebView browser;
    private View customView;
    private WebChromeClient.CustomViewCallback customCallback;
    private boolean resumed;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        try { endpoint = InternetAddress.validate(getIntent().getStringExtra("endpoint")); }
        catch (IllegalArgumentException e) { finish(); return; }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(15, 23, 42));
        normal = new LinearLayout(this);
        normal.setOrientation(LinearLayout.VERTICAL);
        normal.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);
        Button back = new Button(this);
        back.setText("닫기");
        back.setOnClickListener(v -> finish());
        toolbar.addView(back);
        TextView title = new TextView(this);
        title.setText("인터넷 연결 · net.1");
        title.setTextColor(Color.WHITE);
        toolbar.addView(title, new LinearLayout.LayoutParams(0, -2, 1));
        Button reload = new Button(this);
        reload.setText("다시 열기");
        reload.setOnClickListener(v -> requestCameraAndLoad());
        toolbar.addView(reload);
        normal.addView(toolbar);
        status = new TextView(this);
        status.setTextColor(Color.LTGRAY);
        status.setPadding(12, 4, 12, 8);
        status.setText("PC의 임시 인터넷 주소에 연결합니다. PC 앱을 켜 두세요.");
        normal.addView(status);
        browserArea = new FrameLayout(this);
        normal.addView(browserArea, new LinearLayout.LayoutParams(-1, 0, 1));
        root.addView(normal, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        requestCameraAndLoad();
    }

    private void requestCameraAndLoad() {
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.CAMERA}, 21);
            return;
        }
        load();
    }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(request, permissions, grants);
        if (request != 21) return;
        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) load();
        else status.setText("인터넷 손 인식에는 카메라 권한이 필요합니다. 허용 후 ‘다시 열기’를 누르세요.");
    }

    private void load() {
        disposeBrowser();
        status.setText("인터넷 연결 페이지를 여는 중…");
        WebView view = new WebView(this);
        browser = view;
        WebSettings settings = view.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(false);
        settings.setDatabaseEnabled(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setSupportMultipleWindows(false);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        settings.setSafeBrowsingEnabled(true);
        CookieManager.getInstance().setAcceptCookie(false);
        CookieManager.getInstance().setAcceptThirdPartyCookies(view, false);
        WebView.setWebContentsDebuggingEnabled(false);
        view.setSaveEnabled(false);
        view.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView web, WebResourceRequest request) {
                if (InternetAddress.samePage(endpoint, request.getUrl().toString())) return false;
                status.setText("선택한 PC 이외의 페이지 이동은 차단했습니다.");
                return true;
            }
            @Override public WebResourceResponse shouldInterceptRequest(WebView web, WebResourceRequest request) {
                if (InternetAddress.sameOrigin(endpoint, request.getUrl().toString())) return null;
                return new WebResourceResponse("text/plain", "utf-8", 403, "Blocked", null,
                        new ByteArrayInputStream(new byte[0]));
            }
            @Override public void onPageFinished(WebView web, String url) {
                if (browser == web && InternetAddress.samePage(endpoint, url))
                    status.setText("PC의 새 6자리 코드를 입력하세요. 카메라·PC 화면은 연결 후에만 전송됩니다.");
            }
            @Override public void onReceivedSslError(WebView web, SslErrorHandler handler, SslError error) {
                handler.cancel();
                status.setText("HTTPS 인증서를 확인할 수 없어 차단했습니다. PC에서 새 인터넷 주소를 받아 주세요.");
            }
            @Override public void onReceivedError(WebView web, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) status.setText("페이지에 연결할 수 없습니다. PC 인터넷 연결이 켜져 있는지 확인하고 ‘다시 열기’를 누르세요.");
            }
            @Override public boolean onRenderProcessGone(WebView web, RenderProcessGoneDetail detail) {
                disposeBrowser();
                status.setText("인터넷 화면이 종료됐습니다. Android System WebView를 업데이트하고 다시 열어 주세요.");
                return true;
            }
        });
        view.setWebChromeClient(new WebChromeClient() {
            @Override public void onPermissionRequest(PermissionRequest request) {
                if (browser != view || !resumed || !InternetAddress.sameOrigin(endpoint, request.getOrigin().toString())
                        || checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED
                        || !Arrays.asList(request.getResources()).contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE)) {
                    request.deny();
                    return;
                }
                request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
            }
            @Override public void onShowCustomView(View view, CustomViewCallback callback) {
                if (customView != null) { callback.onCustomViewHidden(); return; }
                customView = view;
                customCallback = callback;
                normal.setVisibility(View.GONE);
                root.addView(view, new FrameLayout.LayoutParams(-1, -1));
                getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
            }
            @Override public void onHideCustomView() { exitFullscreen(); }
        });
        view.setDownloadListener((url, userAgent, disposition, mime, length) -> status.setText("이 화면에서는 파일 다운로드를 허용하지 않습니다."));
        browserArea.addView(view, new FrameLayout.LayoutParams(-1, -1));
        view.loadUrl(endpoint);
    }

    private void exitFullscreen() {
        if (customView == null) return;
        root.removeView(customView);
        customView = null;
        normal.setVisibility(View.VISIBLE);
        if (customCallback != null) customCallback.onCustomViewHidden();
        customCallback = null;
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_VISIBLE);
    }
    private void disposeBrowser() {
        exitFullscreen();
        WebView old = browser;
        browser = null;
        if (old == null) return;
        // Destroying the renderer document also releases tracks if JavaScript is suspended.
        try {
            old.evaluateJavascript("window.manuxDisconnect && window.manuxDisconnect('앱이 대기 상태가 되어 연결을 종료했습니다.')", null);
            old.stopLoading();
            old.onPause();
            old.clearHistory();
        } catch (RuntimeException ignored) { /* Renderer may already have exited. */ }
        if (old.getParent() instanceof ViewGroup) ((ViewGroup) old.getParent()).removeView(old);
        old.destroy();
    }
    @Override protected void onResume() { super.onResume(); resumed = true; }
    @Override protected void onPause() {
        resumed = false;
        disposeBrowser();
        if (status != null) status.setText("대기 상태에서 카메라와 연결을 종료했습니다. ‘다시 열기’로 연결하세요.");
        super.onPause();
    }
    @Override protected void onDestroy() { disposeBrowser(); super.onDestroy(); }
    @Override public void onBackPressed() { if (customView != null) exitFullscreen(); else finish(); }
}
