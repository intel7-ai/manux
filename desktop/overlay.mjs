import {BrowserWindow, screen} from 'electron';
import {fileURLToPath} from 'node:url';
import {validateOverlayFrame, isOverlayDocument} from './overlay-protocol.mjs';

/**
 * A transparent, click-through, always-on-top window that only draws. It never takes focus or
 * mouse input, so it cannot steal clicks from the app the user is controlling.
 */
export function createOverlay({origin}) {
  let overlay = null;
  const ensure = () => {
    if (overlay && !overlay.isDestroyed()) return overlay;
    overlay = new BrowserWindow({show:false, frame:false, transparent:true, backgroundColor:'#00000000',
      hasShadow:false, resizable:false, movable:false, focusable:false, skipTaskbar:true, alwaysOnTop:true,
      webPreferences:{preload:fileURLToPath(new URL('./overlay-preload.cjs', import.meta.url)),
        nodeIntegration:false, contextIsolation:true, sandbox:true, webSecurity:true,
        backgroundThrottling:false, spellcheck:false}});
    overlay.setAlwaysOnTop(true, 'screen-saver');
    overlay.setIgnoreMouseEvents(true);
    const web = overlay.webContents;
    web.setWindowOpenHandler(() => ({action:'deny'}));
    web.on('will-navigate', (event, url) => { if (!isOverlayDocument(url, origin)) event.preventDefault(); });
    web.on('will-attach-webview', event => event.preventDefault());
    overlay.on('closed', () => { overlay = null; });
    void overlay.loadURL(`${origin}/overlay.html`);
    return overlay;
  };
  const place = () => {
    const display = screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
    const bounds = overlay.getBounds(), target = display.bounds;
    if (bounds.x !== target.x || bounds.y !== target.y || bounds.width !== target.width || bounds.height !== target.height) overlay.setBounds(target);
    return target;
  };
  return {
    setEnabled(enabled) {
      if (!enabled) { if (overlay && !overlay.isDestroyed()) overlay.hide(); return false; }
      ensure(); place(); overlay.showInactive();
      return true;
    },
    send(frame) {
      if (!overlay || overlay.isDestroyed() || !overlay.isVisible()) return;
      const checked = validateOverlayFrame(frame);
      if (!checked) return;
      const bounds = place(), cursor = screen.getCursorScreenPoint();
      overlay.webContents.send('manux:overlay-frame', {...checked, cursor:{x:cursor.x - bounds.x, y:cursor.y - bounds.y}});
    },
    close() { if (overlay && !overlay.isDestroyed()) overlay.destroy(); overlay = null; },
  };
}
