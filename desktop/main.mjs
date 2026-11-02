import {app, BrowserWindow, ipcMain, globalShortcut, powerMonitor, dialog} from 'electron';
import {fileURLToPath} from 'node:url';
import {createServer} from '../server.js';
import {MouseBridge} from './mouse-bridge.mjs';
import {isAppDocument, canRequestCamera, canCheckCamera, readDesktopPort} from './security.mjs';

let window, server, origin, bridge, shortcutReady = false;
const emergencyKey = 'Control+Alt+M';
const appStatus = () => ({...bridge.status(), available:bridge.status().available && shortcutReady,
  error:shortcutReady ? bridge.status().error : 'Ctrl+Alt+M 정지 키를 등록하지 못했습니다. 다른 앱의 단축키 설정을 확인하세요.'});
const trusted = event => Boolean(window && !window.isDestroyed() && event.sender === window.webContents
  && event.senderFrame === window.webContents.mainFrame && isAppDocument(event.senderFrame.url, origin));
const statusToWindow = () => {
  if (window && !window.isDestroyed()) window.webContents.send('manux:mouse-status', appStatus());
};

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.focus(); } });
  app.whenReady().then(async () => {
    const port = readDesktopPort();
    server = createServer();
    await new Promise((resolve, reject) => {
      server.once('error', error => reject(error.code === 'EADDRINUSE'
        ? new Error(`MANUX 데스크톱 포트 ${port}가 이미 사용 중입니다. 해당 포트를 사용하는 앱을 확인하거나 MANUX_DESKTOP_PORT를 다른 고정 포트로 지정하세요. 다른 포트에서는 저장된 프로필이 별도로 관리됩니다.`)
        : error));
      server.listen(port, '127.0.0.1', resolve);
    });
    origin = `http://127.0.0.1:${server.address().port}`;
    bridge = new MouseBridge();
    shortcutReady = globalShortcut.register(emergencyKey, () => bridge.disable('Ctrl+Alt+M으로 마우스 제어를 멈췄습니다.'));
    window = new BrowserWindow({width:1460, height:1040, minWidth:760, minHeight:620,
      show:process.env.MANUX_MOUSE_DRY_RUN !== '1',
      title:'MANUX · Air Mouse', backgroundColor:'#F5F7F3', autoHideMenuBar:true,
      webPreferences:{preload:fileURLToPath(new URL('./preload.cjs', import.meta.url)),
        nodeIntegration:false, contextIsolation:true, sandbox:true, webSecurity:true,
        backgroundThrottling:false, spellcheck:false}});
    const web = window.webContents;
    web.setWindowOpenHandler(() => ({action:'deny'}));
    web.on('will-navigate', (event, url) => { if (!isAppDocument(url, origin)) event.preventDefault(); });
    web.on('will-attach-webview', event => event.preventDefault());
    web.on('did-start-navigation', (_event, _url, inPlace, isMainFrame) => {
      if (isMainFrame && !inPlace) bridge.disable();
    });
    web.on('render-process-gone', () => bridge.disable('화면 프로세스가 종료되어 마우스를 해제했습니다.'));
    web.on('unresponsive', () => bridge.disable('앱 응답이 없어 마우스를 해제했습니다.'));
    window.on('minimize', () => bridge.disable('앱을 최소화하여 마우스 제어를 멈췄습니다.'));
    window.on('close', () => bridge.close());
    window.on('closed', () => { window = null; });
    web.session.setPermissionRequestHandler((contents, permission, callback, details) => {
      callback(canRequestCamera(contents, web, permission, details, origin));
    });
    web.session.setPermissionCheckHandler((contents, permission, requestingOrigin, details) =>
      canCheckCamera(contents, web, permission, requestingOrigin, details, origin));
    // Only the bundled local page and local assets can load in the desktop shell.
    web.session.webRequest.onBeforeRequest((details, callback) => {
      let allowed = false;
      try { allowed = new URL(details.url).origin === origin || /^(blob:|data:)/.test(details.url); } catch {}
      callback({cancel:!allowed});
    });
    ipcMain.handle('manux:mouse-status', event => { if (!trusted(event)) throw new Error('Untrusted sender.'); return appStatus(); });
    ipcMain.handle('manux:mouse-enable', async (event, value) => {
      if (!trusted(event) || typeof value !== 'boolean') throw new Error('Invalid mouse request.');
      if (value && !shortcutReady) return appStatus();
      await bridge.setEnabled(value);
      return appStatus();
    });
    ipcMain.on('manux:mouse-input', (event, payload) => {
      if (!trusted(event) || !payload || !Number.isSafeInteger(payload.generation)) return;
      bridge.send(payload.events, {generation:payload.generation, sentAt:payload.sentAt});
    });
    ipcMain.on('manux:mouse-stop', event => { if (trusted(event)) bridge.disable(); });
    bridge.on('status', statusToWindow);
    powerMonitor.on('suspend', () => bridge.disable('절전 모드에서 마우스 제어를 해제했습니다.'));
    powerMonitor.on('lock-screen', () => bridge.disable('화면이 잠겨 마우스 제어를 해제했습니다.'));
    void bridge.start().catch(error => { console.error(`MANUX mouse: ${error.message}`); statusToWindow(); });
    await window.loadURL(`${origin}/`);
    console.log(`MANUX desktop ready: ${origin}/ (mouse control starts OFF)`);
  }).catch(error => { dialog.showErrorBox('MANUX 실행 오류', error.message); app.quit(); });
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', () => { bridge?.close(); server?.close(); });
  app.on('will-quit', () => globalShortcut.unregisterAll());
}
