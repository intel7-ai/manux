import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {loadPlaywright} from './browser-runtime.mjs';

// Start-up check of the unpacked Windows build (npm run desktop:pack first).
// The native helper runs with MANUX_MOUSE_DRY_RUN=1, so no OS input is ever sent.
const root = fileURLToPath(new URL('..', import.meta.url));
const unpacked = path.join(root, 'dist/win-unpacked');
const executable = path.join(unpacked, 'MANUX.exe');
const expectedModel = JSON.parse(await fs.readFile(path.join(root, 'public/models/model-source.json'), 'utf8'));
await fs.access(executable).catch(() => { throw new Error('dist/win-unpacked/MANUX.exe가 없습니다. 먼저 npm run desktop:pack 을 실행하세요.'); });
const {_electron} = await loadPlaywright();
const checks = [];

async function launch(extraEnv) {
  const env = {...process.env, MANUX_MOUSE_DRY_RUN: '1', MANUX_DESKTOP_PORT: '0', ...extraEnv};
  delete env.ELECTRON_RUN_AS_NODE;
  if (!('MANUX_AUTO_UPDATE' in extraEnv)) delete env.MANUX_AUTO_UPDATE;
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'manux-packaged-'));
  const app = await _electron.launch({executablePath: executable, args: [`--user-data-dir=${profile}`], env, timeout: 60000});
  return {app, profile};
}

// 1) Packaged run with updates switched off: assets, model, helper, IPC surface.
let session = await launch({MANUX_AUTO_UPDATE: '0'});
try {
  const page = await session.app.firstWindow();
  await page.waitForFunction(async () => (await window.manuxDesktop.getStatus()).available, {}, {timeout: 30000});
  const status = await page.evaluate(() => window.manuxDesktop.getStatus());
  assert.equal(status.enabled, false, 'mouse control must start OFF');
  assert.equal(status.dryRun, true);
  checks.push('packaged window opens; Windows PowerShell helper from resources/app started (dry-run); mouse control OFF');

  const origin = new URL(page.url()).origin;
  const get = async pathname => fetch(`${origin}${pathname}`);
  assert.equal((await get('/health')).status, 200);
  assert.equal((await get('/')).status, 200);
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
  assert.equal(await page.evaluate(() => typeof window.require), 'undefined');
  checks.push('app.isPackaged is true; helper script exists on disk next to the app; renderer has no Node require');

  const update = await page.evaluate(() => window.manuxDesktop.getUpdateStatus());
  assert.equal(update.enabled, false); assert.equal(update.state, 'disabled');
  const keys = await page.evaluate(() => Object.keys(window.manuxDesktop).sort());
  assert.deepEqual(keys, ['checkForUpdates', 'getStatus', 'getUpdateStatus', 'installUpdate', 'onStatus', 'onUpdateStatus', 'send', 'setEnabled', 'stop']);
  const install = await page.evaluate(() => window.manuxDesktop.installUpdate());
  assert.equal(install.ok, false, 'install is refused when no update is downloaded');
  checks.push('update API exposes only status/check/install/onUpdateStatus; disabled when MANUX_AUTO_UPDATE=0; install refused without a downloaded update');
} finally { await session.app.close(); await fs.rm(session.profile, {recursive: true, force: true}); }

// 2) Packaged run with updates enabled: electron-updater must load. The first check is
//    scheduled 20 s after start and this run closes earlier, so GitHub is not contacted.
session = await launch({});
try {
  const page = await session.app.firstWindow();
  await page.waitForFunction(async () => (await window.manuxDesktop.getUpdateStatus()).enabled, {}, {timeout: 30000});
  await page.waitForTimeout(1500);
  const update = await page.evaluate(() => window.manuxDesktop.getUpdateStatus());
  assert.equal(update.enabled, true); assert.equal(update.state, 'idle', `electron-updater should load: ${update.error ?? ''}`);
  checks.push('with updates enabled the packaged app loads electron-updater and waits idle (no network check performed in this test)');
} finally { await session.app.close(); await fs.rm(session.profile, {recursive: true, force: true}); }

const report = {passed: true, method: 'Real packaged MANUX.exe (unpacked) + Playwright Electron; dry-run helper; no OS input, no real update install, no webcam.', checks};
await fs.mkdir(path.join(root, 'test-results'), {recursive: true});
await fs.writeFile(path.join(root, 'test-results/packaged-app.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
