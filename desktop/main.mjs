import {app, BrowserWindow, ipcMain, globalShortcut, powerMonitor, dialog, Menu} from 'electron';
import {fileURLToPath} from 'node:url';
import {readFileSync, mkdirSync} from 'node:fs';
import path from 'node:path';
import {createServer} from '../server.js';
import {MouseBridge} from './mouse-bridge.mjs';
import {isAppDocument, canRequestCamera, canCheckCamera, readDesktopPort} from './security.mjs';
import {createUpdateManager} from './updater.mjs';
import {createOverlay} from './overlay.mjs';

const manifest = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
const buildChannel = manifest.manuxBuildChannel ?? 'stable';
const isPreview = buildChannel === 'preview';
if (isPreview) {
  app.setName('MANUX Preview');
  // A PR build must not share profiles, storage, or the instance lock with the release app.
  const previewData = app.commandLine.getSwitchValue('user-data-dir')
    || path.join(app.getPath('appData'), 'MANUX Preview');
  mkdirSync(previewData, {recursive: true});
  app.setPath('userData', previewData);
  app.setPath('sessionData', previewData);
}

let window, server, origin, bridge, updates, overlay, shortcutReady = false;
const baseTitle = isPreview ? 'MANUX Preview · Air Mouse' : 'MANUX · Air Mouse';
const emergencyKey = 'Control+Alt+M';
const appStatus = () => ({...bridge.status(), available:bridge.status().available && shortcutReady,
  error:shortcutReady ? bridge.status().error : 'Ctrl+Alt+M 정지 키를 등록하지 못했습니다. 다른 앱의 단축키 설정을 확인하세요.'});
const trusted = event => Boolean(window && !window.isDestroyed() && event.sender === window.webContents
  && event.senderFrame === window.webContents.mainFrame && isAppDocument(event.senderFrame.url, origin));
const statusToWindow = () => {
  if (window && !window.isDestroyed()) window.webContents.send('manux:mouse-status', appStatus());
  refreshUpdateMenu();
};
const refreshUpdateMenu = () => {
  const menu = Menu.getApplicationMenu();
  const status = updates?.status();
  const check = menu?.getMenuItemById('manux-update-check');
  const install = menu?.getMenuItemById('manux-update-install');
  if (check) check.enabled = Boolean(status?.enabled && !['checking', 'downloading', 'downloaded'].includes(status.state));
  if (install) install.enabled = Boolean(status?.state === 'downloaded' && !bridge?.status().enabled);
};
const updateToWindow = status => {
  refreshUpdateMenu();
  if (!window || window.isDestroyed()) return;
  window.webContents.send('manux:update-status', status);
  window.setTitle(status.state === 'downloaded' ? `${baseTitle} · 업데이트 ${status.version ?? ''} 준비됨` : baseTitle);
};

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.focus(); } });
  app.whenReady().then(async () => {
    const port = readDesktopPort({...process.env,
      MANUX_DESKTOP_PORT: process.env.MANUX_DESKTOP_PORT ?? (isPreview ? '4176' : '4175')});
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
      title:baseTitle, backgroundColor:'#F5F7F3', autoHideMenuBar:false,
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
    window.on('close', () => { bridge.close(); overlay?.close(); });
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
    // Screen finger tracker: draw-only window, fed by validated frames from the app page.
    overlay = createOverlay({origin});
    ipcMain.handle('manux:overlay-enable', (event, value) => {
      if (!trusted(event) || typeof value !== 'boolean') throw new Error('Invalid overlay request.');
      return overlay.setEnabled(value);
    });
    ipcMain.on('manux:overlay-frame', (event, frame) => { if (trusted(event)) overlay.send(frame); });
    bridge.on('status', () => { statusToWindow(); updates?.mouseStateChanged(); });
    // Updates are checked only in a packaged run; the user approves every install.
    updates = createUpdateManager({
      isPackaged: app.isPackaged,
      buildChannel,
      loadUpdater: async () => (await import('electron-updater')).default.autoUpdater,
      isMouseActive: () => bridge.status().enabled,
      onStatus: updateToWindow,
      promptInstall: async version => (await dialog.showMessageBox(window, {type:'question', buttons:['지금 설치', '나중에'],
        defaultId:1, cancelId:1, title:'MANUX 업데이트', message:`새 버전 ${version ?? ''}이 준비됐습니다.`,
        detail:'지금 설치하면 앱이 다시 시작되며 마우스 제어는 꺼진 상태로 시작합니다. 서명되지 않은 설치본이라 Windows 경고가 나올 수 있습니다.'})).response === 0,
    });
    ipcMain.handle('manux:update-status', event => { if (!trusted(event)) throw new Error('Untrusted sender.'); return updates.status(); });
    ipcMain.handle('manux:update-check', async event => { if (!trusted(event)) throw new Error('Untrusted sender.'); return updates.check(); });
    ipcMain.handle('manux:update-install', async event => { if (!trusted(event)) throw new Error('Untrusted sender.'); return updates.install(); });
    Menu.setApplicationMenu(Menu.buildFromTemplate([{label:'MANUX', submenu:[
      {label:`버전 ${app.getVersion()}${isPreview ? ' · 시험용' : ''}`, enabled:false},
      {type:'separator'},
      {id:'manux-update-check', label:'업데이트 확인', enabled:updates.enabled,
        click:async () => {
          const status = await updates.check();
          if (window && !window.isDestroyed() && ['not-available', 'error'].includes(status.state)) {
            await dialog.showMessageBox(window, {type:status.state === 'error' ? 'warning' : 'info',
              title:'MANUX 업데이트', message:status.state === 'error' ? '업데이트를 확인하지 못했습니다. 나중에 다시 확인해 주세요.' : '현재 최신 버전을 사용하고 있습니다.'});
          }
        }},
      {id:'manux-update-install', label:'준비된 업데이트 설치', enabled:false,
        click:() => { void updates.install(); }},
      {type:'separator'},
      {label:'종료', role:'quit'},
    ]}]));
    powerMonitor.on('suspend', () => bridge.disable('절전 모드에서 마우스 제어를 해제했습니다.'));
    powerMonitor.on('lock-screen', () => bridge.disable('화면이 잠겨 마우스 제어를 해제했습니다.'));
    void bridge.start().catch(error => { console.error(`MANUX mouse: ${error.message}`); statusToWindow(); });
    await window.loadURL(`${origin}/studio.html`);
    void updates.start();
    console.log(`MANUX desktop ready: ${origin}/ (mouse control starts OFF)`);
  }).catch(error => { dialog.showErrorBox('MANUX 실행 오류', error.message); app.quit(); });
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', () => { updates?.stop(); bridge?.close(); overlay?.close(); server?.close(); });
  app.on('will-quit', () => globalShortcut.unregisterAll());
}
