import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import {createHash, randomUUID} from 'node:crypto';
import {createRequire} from 'node:module';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';
import {loadPlaywright} from './browser-runtime.mjs';
import {desktopChannel} from './desktop-channel.mjs';

// This test deliberately runs installers. It must never run on a teammate's PC
// or a self-hosted runner. GitHub discards the VM and its installer registry after
// the job; the app, profile, cache, old build and downloaded installer stay in one
// checked temporary directory. Only the separate Preview application is used.
if (process.platform !== 'win32' || process.env.GITHUB_ACTIONS !== 'true'
  || process.env.RUNNER_ENVIRONMENT !== 'github-hosted' || process.env.RUNNER_OS !== 'Windows') {
  throw new Error('Installed update testing is allowed only on a disposable GitHub-hosted Windows runner.');
}
if (process.env.MANUX_BUILD_CHANNEL !== 'preview') {
  throw new Error('Installed update testing requires MANUX_BUILD_CHANNEL=preview.');
}

const root = fileURLToPath(new URL('..', import.meta.url));
const channel = desktopChannel();
const require = createRequire(import.meta.url);
const {load: loadYaml} = require('js-yaml');
// The GitHub publisher writes latest.yml even for this isolated Preview build.
// The test serves the same metadata at preview.yml for its generic local feed.
const metadata = loadYaml(await fs.readFile(path.join(root, 'dist/latest.yml'), 'utf8'));
const file = metadata.files?.find(entry => typeof entry.url === 'string' && /\.exe$/i.test(entry.url));
assert.ok(file, 'Preview installer metadata is required');
const installerName = decodeURIComponent(file.url);
assert.equal(path.basename(installerName), installerName);
assert.ok(!/[\\/]/.test(installerName));
const installerPath = path.join(root, 'dist', installerName);
const installerBytes = (await fs.stat(installerPath)).size;
async function hashFile(filePath) {
  const hash = createHash('sha512');
  for await (const chunk of createReadStream(filePath)) hash.update(chunk);
  return hash.digest('base64');
}
assert.equal(await hashFile(installerPath), file.sha512);
assert.equal(installerBytes, file.size);
assert.notEqual(metadata.version, '0.0.0');

function bounded(promise, milliseconds, label, cancel = () => {}) {
  let timer;
  const timeout = new Promise((_, reject) => { timer = setTimeout(() => {
    try { cancel(); } catch { /* retain the stage-specific failure */ }
    reject(new Error(`${label} timed out after ${milliseconds} ms`));
  }, milliseconds); });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}
const run = promisify(execFile);
const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
async function vacantPort() {
  const probe = net.createServer();
  await bounded(new Promise((resolve, reject) => { probe.once('error', reject); probe.listen(0, '127.0.0.1', resolve); }), 5000, 'test origin port');
  const port = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  return port;
}
const tempBase = await fs.realpath(os.tmpdir());
const tempRoot = await fs.mkdtemp(path.join(tempBase, 'manux-installed-update-'));
const oldUnpacked = path.join(tempRoot, 'old-unpacked');
const oldOutput = path.join(tempRoot, 'old-installer');
const installDirectory = path.join(tempRoot, 'installed');
const profile = path.join(tempRoot, 'profile');
const cache = path.join(tempRoot, 'cache');
const installError = path.join(tempRoot, 'install-error.json');
const apps = new Set();
let feed;
const checkpoints = [];
async function closeOwnedApp(application) {
  if (!apps.has(application)) return;
  apps.delete(application);
  await bounded(application.close(), 15000, 'owned test app shutdown', () => application.process()?.kill());
}

try {
  console.log('CHECK CI-only Preview installer upgrade: prepare actual 0.0.0 installation');
  await fs.mkdir(profile);
  await fs.mkdir(cache);
  await fs.cp(path.join(root, 'dist/win-unpacked'), oldUnpacked, {recursive:true});
  const oldManifestPath = path.join(oldUnpacked, 'resources/app/package.json');
  const oldManifest = JSON.parse(await fs.readFile(oldManifestPath, 'utf8'));
  assert.equal(oldManifest.manuxBuildChannel, 'preview');
  oldManifest.version = '0.0.0';
  await fs.writeFile(oldManifestPath, `${JSON.stringify(oldManifest, null, 2)}\n`);
  const builder = path.join(root, 'node_modules/electron-builder/cli.js');
  const buildArgs = [builder, '--win', '--x64', '--prepackaged', oldUnpacked, '--config', 'electron-builder.yml', '--publish', 'never',
    `--config.directories.output=${oldOutput}`, `--config.appId=${channel.appId}`, `--config.productName=${channel.productName}`,
    `--config.win.executableName=${channel.executableName}`, `--config.nsis.artifactName=${channel.artifactName}`,
    `--config.extraMetadata.name=${channel.packageName}`, '--config.extraMetadata.version=0.0.0',
    '--config.extraMetadata.manuxBuildChannel=preview', '--config.nsis.runAfterFinish=false'];
  await run(process.execPath, buildArgs, {cwd:root, windowsHide:true, timeout:300000, maxBuffer:2_000_000,
    env:{...process.env, CSC_IDENTITY_AUTO_DISCOVERY:'false'}});
  const oldInstaller = path.join(oldOutput, 'MANUX-Preview-Setup-0.0.0.exe');
  await fs.access(oldInstaller);
  await run(oldInstaller, ['/S', `/D=${installDirectory}`], {windowsHide:true, timeout:120000, maxBuffer:500_000});
  const installedExecutable = path.join(installDirectory, `${channel.executableName}.exe`);
  const installedManifestPath = path.join(installDirectory, 'resources/app/package.json');
  assert.equal(JSON.parse(await fs.readFile(installedManifestPath, 'utf8')).version, '0.0.0');
  await fs.access(installedExecutable);
  checkpoints.push('real Preview NSIS installer installed version 0.0.0 into the isolated installation directory');

  const servedMetadata = {...metadata, files:[{...file, url:installerName}], path:installerName, sha512:file.sha512};
  const requests = [];
  feed = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://127.0.0.1').pathname;
    requests.push(pathname);
    if (req.method !== 'GET') { res.writeHead(405); res.end(); return; }
    if (pathname === '/preview.yml') {
      const body = JSON.stringify(servedMetadata);
      res.writeHead(200, {'Content-Type':'application/json', 'Content-Length':Buffer.byteLength(body)});
      res.end(body);
    } else if (decodeURIComponent(pathname) === `/${installerName}`) {
      res.writeHead(200, {'Content-Type':'application/octet-stream', 'Content-Length':installerBytes});
      const stream = createReadStream(installerPath);
      stream.on('error', () => res.destroy());
      stream.pipe(res);
    } else { res.writeHead(404); res.end(); }
  });
  feed.headersTimeout = 10000;
  feed.requestTimeout = 30000;
  await bounded(new Promise((resolve, reject) => { feed.once('error', reject); feed.listen(0, '127.0.0.1', resolve); }), 10000, 'loopback update feed');
  const feedUrl = `http://127.0.0.1:${feed.address().port}/`;
  await fs.writeFile(path.join(installDirectory, 'resources/app-update.yml'), JSON.stringify({provider:'generic',
    url:feedUrl, channel:'preview', updaterCacheDirName:'manux-installed-update-test'}));
  const port = await vacantPort();
  const env = {...process.env, LOCALAPPDATA:cache, MANUX_MOUSE_DRY_RUN:'1', MANUX_DESKTOP_PORT:String(port), MANUX_AUTO_UPDATE:'0'};
  delete env.ELECTRON_RUN_AS_NODE;
  const {_electron} = await loadPlaywright();
  const launchInstalled = async () => {
    const application = await _electron.launch({executablePath:installedExecutable, args:[`--user-data-dir=${profile}`], env, timeout:60000});
    apps.add(application);
    const page = await application.firstWindow({timeout:30000});
    await page.waitForFunction(async () => (await window.manuxDesktop.getStatus()).available, {}, {timeout:30000});
    assert.equal((await application.evaluate(({app}) => app.getPath('userData'))).toLowerCase(), profile.toLowerCase());
    assert.equal(new URL(page.url()).origin, `http://127.0.0.1:${port}`);
    return {application, page};
  };
  let {application, page} = await launchInstalled();
  assert.equal(await application.evaluate(({app}) => app.getVersion()), '0.0.0');
  const marker = randomUUID();
  await page.evaluate(value => localStorage.setItem('manux-upgrade-test-marker', value), marker);
  checkpoints.push('installed old app started with its actual version and saved a localStorage marker');

  console.log('CHECK installed old app: real updater downloads current Preview and runs its NSIS upgrade');
  const download = await bounded(application.evaluate(async ({app, session}, input) => {
    const {createRequire} = process.getBuiltinModule('module');
    const path = process.getBuiltinModule('path');
    const fs = process.getBuiltinModule('fs');
    const bundledRequire = createRequire(path.join(app.getAppPath(), 'package.json'));
    const {NsisUpdater} = bundledRequire('electron-updater');
    // No AppAdapter is supplied: this uses the actually installed app/version.
    const updater = new NsisUpdater();
    updater.autoDownload = false;
    updater.autoInstallOnAppQuit = false;
    updater.autoRunAppAfterInstall = false;
    updater.allowPrerelease = true;
    updater.allowDowngrade = false;
    updater.disableDifferentialDownload = true;
    updater.disableWebInstaller = true;
    updater.installDirectory = input.installDirectory;
    updater.logger = {info() {}, warn() {}, error() {}, debug() {}};
    const netSession = session.fromPartition(`manux-installed-update-${Date.now()}`, {cache:false});
    await netSession.setProxy({mode:'direct'});
    const blocked = [];
    netSession.webRequest.onBeforeRequest((details, callback) => {
      const allowed = details.url.startsWith(input.feedUrl);
      if (!allowed) blocked.push(details.url);
      callback({cancel:!allowed});
    });
    updater.httpExecutor.cachedSession = netSession;
    updater.on('error', error => fs.writeFileSync(input.installError, JSON.stringify({code:error.code ?? 'error', message:String(error.message).slice(0,300)})));
    const update = await updater.checkForUpdates();
    if (!update?.isUpdateAvailable) throw new Error('Installed old version did not discover its upgrade');
    const timer = setTimeout(() => update.cancellationToken.cancel(), 120000);
    let files;
    try { files = await updater.downloadUpdate(update.cancellationToken); }
    finally { clearTimeout(timer); }
    globalThis.__manuxInstalledUpdateTest = updater;
    return {installedVersion:app.getVersion(), adapterVersion:updater.app.version, isPackaged:app.isPackaged,
      downloadedFiles:files, targetVersion:update.updateInfo.version, blocked};
  }, {feedUrl, installDirectory, installError}), 180000, 'installed app update download');
  assert.equal(download.installedVersion, '0.0.0');
  assert.equal(download.adapterVersion, '0.0.0');
  assert.equal(download.isPackaged, true);
  assert.equal(download.targetVersion, metadata.version);
  assert.deepEqual(download.blocked, []);
  assert.equal(download.downloadedFiles.length, 1);
  const downloadedPath = await fs.realpath(download.downloadedFiles[0]);
  const relativeCache = path.relative(await fs.realpath(cache), downloadedPath);
  assert.ok(relativeCache && !relativeCache.startsWith('..') && !path.isAbsolute(relativeCache));
  assert.equal(await hashFile(downloadedPath), file.sha512);
  checkpoints.push('default ElectronAppAdapter read installed version 0.0.0; real updater downloaded and verified the current installer');
  const appClosed = application.waitForEvent('close', {timeout:60000});
  await application.evaluate(() => { setTimeout(() => globalThis.__manuxInstalledUpdateTest.quitAndInstall(true, false), 100); });
  await appClosed;
  apps.delete(application);
  const upgradeDeadline = Date.now() + 120000;
  let upgraded = false;
  while (Date.now() < upgradeDeadline) {
    const error = await fs.readFile(installError, 'utf8').catch(() => null);
    if (error) throw new Error(`Updater installation failed: ${error}`);
    const current = await fs.readFile(installedManifestPath, 'utf8').then(JSON.parse).catch(() => null);
    if (current?.version === metadata.version) { upgraded = true; break; }
    await sleep(500);
  }
  assert.ok(upgraded, 'NSIS did not replace the installed version within 120 seconds');
  await sleep(2000);
  ({application, page} = await launchInstalled());
  assert.equal(await application.evaluate(({app}) => app.getVersion()), metadata.version);
  assert.equal(await page.evaluate(() => localStorage.getItem('manux-upgrade-test-marker')), marker);
  const mouse = await page.evaluate(() => window.manuxDesktop.getStatus());
  assert.equal(mouse.enabled, false);
  assert.equal(mouse.dryRun, true);
  checkpoints.push('real quitAndInstall/NSIS upgrade replaced the app; relaunch preserved the localStorage marker and kept mouse control OFF');
  await closeOwnedApp(application);
  assert.ok(requests.includes('/preview.yml'));
  assert.ok(requests.some(request => decodeURIComponent(request) === `/${installerName}`));
  const report = {passed:true, channel:'preview', installedVersionBefore:'0.0.0', installedVersionAfter:metadata.version,
    installer:installerName, markerPreserved:true, mouseControlEnabledAfter:false, checkpoints,
    method:'Disposable GitHub-hosted Windows VM; real NSIS old installation, default ElectronAppAdapter, loopback download, real quitAndInstall upgrade and relaunch.',
    limitations:'The old version is the current code repackaged as 0.0.0, not a historical release. Preview channel only; no webcam, real OS input, public update feed or code-signing test.'};
  await fs.mkdir(path.join(root, 'test-results'), {recursive:true});
  await fs.writeFile(path.join(root, 'test-results/installed-update.json'), `${JSON.stringify(report, null, 2)}\n`);
  console.log(`PASS actual installed Preview upgrade 0.0.0 -> ${metadata.version}; profile marker preserved; mouse control OFF.`);
} finally {
  try {
    for (const application of [...apps]) await closeOwnedApp(application);
  } finally {
    if (feed) {
      feed.closeAllConnections();
      await bounded(new Promise(resolve => feed.close(resolve)), 5000, 'loopback feed shutdown');
    }
    const resolved = await fs.realpath(tempRoot);
    assert.equal(path.dirname(resolved).toLowerCase(), tempBase.toLowerCase());
    assert.ok(path.basename(resolved).startsWith('manux-installed-update-'));
    await fs.rm(resolved, {recursive:true, force:true, maxRetries:3, retryDelay:500});
  }
}
