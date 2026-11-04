import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {loadPlaywright} from './browser-runtime.mjs';
import {desktopChannel} from './desktop-channel.mjs';

// Start-up check of the unpacked Windows build (npm run desktop:pack first).
// The native helper runs with MANUX_MOUSE_DRY_RUN=1, so no OS input is ever sent.
const root = fileURLToPath(new URL('..', import.meta.url));
const unpacked = path.join(root, 'dist/win-unpacked');
const channel = desktopChannel();
const executable = path.join(unpacked, `${channel.executableName}.exe`);
const expectedModel = JSON.parse(await fs.readFile(path.join(root, 'public/models/model-source.json'), 'utf8'));
await fs.access(executable).catch(() => { throw new Error(`${executable}가 없습니다. 먼저 npm run desktop:pack 을 실행하세요.`); });
const {_electron} = await loadPlaywright();
const checks = [];

async function launch(extraEnv, existingProfile) {
  const env = {...process.env, MANUX_MOUSE_DRY_RUN: '1', MANUX_DESKTOP_PORT: '0', ...extraEnv};
  delete env.ELECTRON_RUN_AS_NODE;
  if (!('MANUX_AUTO_UPDATE' in extraEnv)) delete env.MANUX_AUTO_UPDATE;
  const profile = existingProfile ?? await fs.mkdtemp(path.join(os.tmpdir(), 'manux-packaged-'));
  const app = await _electron.launch({executablePath: executable, args: [`--user-data-dir=${profile}`], env, timeout: 60000});
  return {app, profile};
}

// 1) Packaged run with updates switched off: assets, model, helper, IPC surface.
let session = await launch({MANUX_AUTO_UPDATE: '0'});
const sharedProfile = session.profile;
let sharedPort, firstPassed = false;
try {
  const page = await session.app.firstWindow();
  await page.waitForFunction(async () => (await window.manuxDesktop.getStatus()).available, {}, {timeout: 30000});
  const status = await page.evaluate(() => window.manuxDesktop.getStatus());
  assert.equal(status.enabled, false, 'mouse control must start OFF');
  assert.equal(status.dryRun, true);
  checks.push('packaged window opens; Windows PowerShell helper from resources/app started (dry-run); mouse control OFF');

  const origin = new URL(page.url()).origin;
  sharedPort = new URL(page.url()).port;
  const get = async pathname => fetch(`${origin}${pathname}`);
  assert.equal((await get('/health')).status, 200);
  assert.equal((await get('/')).status, 200);
  assert.equal((await get('/studio.html')).status, 200);
  assert.equal((await get('/src/app.js')).status, 200);
  assert.equal((await get('/vendor/vision_bundle.mjs')).status, 200);
  const wasm = await get('/vendor/wasm/vision_wasm_internal.wasm');
  assert.equal(wasm.status, 200); assert.ok((await wasm.arrayBuffer()).byteLength > 100000);
  const model = Buffer.from(await (await get('/models/gesture_recognizer.task')).arrayBuffer());
  assert.equal(model.length, expectedModel.bytes);
  assert.equal(createHash('sha256').update(model).digest('hex'), expectedModel.sha256);
  checks.push('local server serves index, src, MediaPipe wasm and the pinned gesture model (sha256 match)');

  const appPath = await session.app.evaluate(({app}) => app.getAppPath());
  await fs.access(path.join(appPath, 'desktop/windows-mouse.ps1'));
  assert.equal(await session.app.evaluate(({app}) => app.isPackaged), true);
  assert.equal(path.resolve(await session.app.evaluate(({app}) => app.getPath('userData'))), path.resolve(sharedProfile));
  assert.deepEqual(await session.app.evaluate(({Menu}) => ['manux-update-check', 'manux-update-install'].map(id => Boolean(Menu.getApplicationMenu()?.getMenuItemById(id)))), [true, true]);
  assert.equal(await page.evaluate(() => typeof window.require), 'undefined');
  checks.push('app.isPackaged is true; helper script exists on disk next to the app; renderer has no Node require');

  const update = await page.evaluate(() => window.manuxDesktop.getUpdateStatus());
  assert.equal(update.enabled, false); assert.equal(update.state, 'disabled');
  const keys = await page.evaluate(() => Object.keys(window.manuxDesktop).sort());
  assert.deepEqual(keys, ['checkForUpdates', 'getStatus', 'getUpdateStatus', 'installUpdate', 'onStatus', 'onUpdateStatus', 'send', 'sendOverlayFrame', 'setEnabled', 'setOverlay', 'stop']);
  const install = await page.evaluate(() => window.manuxDesktop.installUpdate());
  assert.equal(install.ok, false, 'install is refused when no update is downloaded');
  checks.push('update API exposes only status/check/install/onUpdateStatus; disabled when MANUX_AUTO_UPDATE=0; install refused without a downloaded update');
  await page.evaluate(() => localStorage.setItem('manux-release-smoke', 'preserve-settings'));
  firstPassed = true;
} finally {
  await session.app.close();
  if (!firstPassed) await fs.rm(sharedProfile, {recursive: true, force: true});
}

// 2) Packaged run with updates enabled: electron-updater must load. The first check is
//    scheduled 20 s after start and this run closes earlier, so GitHub is not contacted.
session = await launch({MANUX_DESKTOP_PORT: sharedPort}, sharedProfile);
try {
  const page = await session.app.firstWindow();
  assert.equal(await page.evaluate(() => localStorage.getItem('manux-release-smoke')), 'preserve-settings');
  checks.push('settings survive an app restart at the same origin and userData path; update menu remains available');
  await page.waitForFunction(async () => (await window.manuxDesktop.getUpdateStatus()).state !== undefined, {}, {timeout: 30000});
  await page.waitForTimeout(1500);
  const update = await page.evaluate(() => window.manuxDesktop.getUpdateStatus());
  assert.equal(update.enabled, channel.channel === 'stable');
  assert.equal(update.state, channel.channel === 'stable' ? 'idle' : 'disabled', `unexpected updater state: ${update.error ?? ''}`);
  if (channel.channel === 'preview') {
    assert.equal(await session.app.evaluate(({app}) => app.getName()), 'MANUX Preview');
    checks.push('preview has a distinct app identity and no automatic production updates');
  } else checks.push('stable packaged app loads electron-updater and waits idle (no network check performed)');
} finally { await session.app.close(); await fs.rm(session.profile, {recursive: true, force: true}); }

const report = {passed: true, method: 'Real packaged MANUX.exe (unpacked) + Playwright Electron; dry-run helper; no OS input, no real update install, no webcam.', checks};
await fs.mkdir(path.join(root, 'test-results'), {recursive: true});
await fs.writeFile(path.join(root, 'test-results/packaged-app.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
