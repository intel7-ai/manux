import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {loadPlaywright} from './browser-runtime.mjs';
import {desktopChannel} from './desktop-channel.mjs';

// Real packaged Electron + its bundled NsisUpdater/ElectronHttpExecutor. Only the
// AppAdapter's installed version is simulated. This never installs or publishes.
const root = fileURLToPath(new URL('..', import.meta.url));
const channel = desktopChannel();
const unpacked = path.join(root, 'dist/win-unpacked');
const executable = path.join(unpacked, `${channel.executableName}.exe`);
const require = createRequire(import.meta.url);
const {load: loadYaml} = require('js-yaml');
function bounded(promise, milliseconds, label, cancel = () => {}) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      try { cancel(); } catch { /* reject with the specific timeout below */ }
      reject(new Error(`${label} timed out after ${milliseconds} ms`));
    }, milliseconds);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}
// The GitHub publishing provider writes latest.yml even for prerelease builds.
// Preview metadata is only a CI artifact and is never sent to the stable feed.
const metadataName = 'latest.yml';
const metadata = loadYaml(await fs.readFile(path.join(root, 'dist', metadataName), 'utf8'));
const artifact = metadata.files?.find(file => typeof file.url === 'string' && /\.exe$/i.test(file.url));
assert.ok(artifact, 'installer entry is required in the generated update metadata');
const installerName = decodeURIComponent(artifact.url);
assert.equal(path.basename(installerName), installerName, 'the installer must be a file in dist');
assert.ok(!/[\\/]/.test(installerName), 'metadata cannot refer to another directory or URL');
const installer = path.join(root, 'dist', installerName);
async function hashFile(file) {
  const hash = createHash('sha512');
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest('base64');
}
const installerBytes = (await fs.stat(installer)).size;
const sha512 = await hashFile(installer);
assert.equal(artifact.sha512, sha512, 'generated metadata must match the actual NSIS installer');
assert.equal(artifact.size, installerBytes);
await fs.access(executable);
console.log(`CHECK installer metadata and SHA-512 (${channel.channel}, ${metadata.version})`);

const tempBase = await fs.realpath(os.tmpdir());
const tempRoot = await fs.mkdtemp(path.join(tempBase, 'manux-update-download-'));
const requests = [];
const validFeed = {...metadata, files:[{...artifact, url:installerName}], path:installerName, sha512};
const badSha512 = Buffer.alloc(64).toString('base64');
const invalidFeed = {...validFeed, files:[{...validFeed.files[0], sha512:badSha512}], sha512:badSha512};
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://127.0.0.1').pathname;
  requests.push(pathname);
  const match = /^\/(valid|invalid)\/(.+)$/.exec(pathname);
  if (req.method !== 'GET' || !match) { res.writeHead(404); res.end(); return; }
  if (match[2] === metadataName) {
    const body = JSON.stringify(match[1] === 'valid' ? validFeed : invalidFeed);
    res.writeHead(200, {'Content-Type':'application/json', 'Content-Length':Buffer.byteLength(body)});
    res.end(body);
  } else if (decodeURIComponent(match[2]) === installerName) {
    res.writeHead(200, {'Content-Type':'application/octet-stream', 'Content-Length':installerBytes});
    const stream = createReadStream(installer);
    stream.on('error', () => res.destroy());
    stream.pipe(res);
  } else { res.writeHead(404); res.end(); }
});
server.headersTimeout = 10000;
server.requestTimeout = 30000;

let application;
try {
  await bounded(new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); }), 10000, 'loopback server startup');
  const address = server.address();
  assert.equal(address.address, '127.0.0.1');
  const baseUrl = `http://127.0.0.1:${address.port}`;
  const profile = path.join(tempRoot, 'electron-profile');
  await fs.mkdir(profile);
  const env = {...process.env, MANUX_MOUSE_DRY_RUN:'1', MANUX_DESKTOP_PORT:'0', MANUX_AUTO_UPDATE:'0'};
  delete env.ELECTRON_RUN_AS_NODE;
  const {_electron} = await loadPlaywright();
  application = await _electron.launch({executablePath:executable, args:[`--user-data-dir=${profile}`], env, timeout:60000});
  await application.firstWindow({timeout:30000});
  const runtimeProfile = await bounded(application.evaluate(({app}) => app.getPath('userData')), 15000, 'isolated profile check');
  assert.equal((await fs.realpath(runtimeProfile)).toLowerCase(), (await fs.realpath(profile)).toLowerCase(), 'the real app must use the isolated test profile');
  console.log('CHECK packaged app launched with an isolated profile; normal updates disabled');
  const checks = [];
  for (const scenario of ['valid', 'invalid']) {
    const caseRoot = path.join(tempRoot, scenario);
    await fs.mkdir(path.join(caseRoot, 'user-data'), {recursive:true});
    await fs.mkdir(path.join(caseRoot, 'cache'));
    const configPath = path.join(caseRoot, 'app-update.yml');
    const feedUrl = `${baseUrl}/${scenario}/`;
    await fs.writeFile(configPath, JSON.stringify({provider:'generic', url:feedUrl,
      updaterCacheDirName:`manux-download-${scenario}`}));
    console.log(`CHECK ${scenario} feed discovery and installer download`);
    const result = await bounded(application.evaluate(async ({app, session}, input) => {
      // Playwright's Electron VM does not provide a dynamic-import callback.
      const {createRequire} = process.getBuiltinModule('module');
      const path = process.getBuiltinModule('path');
      const within = async (promise, milliseconds, label, cancel = () => {}) => {
        let timer;
        const timeout = new Promise((_, reject) => { timer = setTimeout(() => {
          try { cancel(); } catch { /* keep the stage-specific timeout */ }
          reject(new Error(`${label} timed out after ${milliseconds} ms`));
        }, milliseconds); });
        try { return await Promise.race([promise, timeout]); }
        finally { clearTimeout(timer); }
      };
      const bundledRequire = createRequire(path.join(app.getAppPath(), 'package.json'));
      const {NsisUpdater} = bundledRequire('electron-updater');
      const {ElectronHttpExecutor} = bundledRequire('electron-updater/out/electronHttpExecutor.js');
      let installAttempts = 0;
      const forbidden = () => { installAttempts++; throw new Error('Installer execution is forbidden in the download test'); };
      const adapter = {version:'0.0.0', name:'MANUX Update Download Test', isPackaged:true,
        appUpdateConfigPath:input.configPath, userDataPath:path.join(input.caseRoot, 'user-data'),
        baseCachePath:path.join(input.caseRoot, 'cache'), whenReady:() => app.whenReady(),
        relaunch:forbidden, quit:forbidden, onQuit:forbidden};
      const updater = new NsisUpdater(undefined, adapter);
      updater.autoDownload = false;
      updater.autoInstallOnAppQuit = false;
      updater.allowPrerelease = input.channel === 'preview';
      updater.allowDowngrade = false;
      updater.disableDifferentialDownload = true;
      updater.disableWebInstaller = true;
      updater.quitAndInstall = forbidden;
      updater.install = forbidden;
      updater.doInstall = forbidden;
      updater.logger = {info() {}, warn() {}, error() {}, debug() {}};
      const netSession = session.fromPartition(`manux-update-test-${input.scenario}-${Date.now()}`, {cache:false});
      await within(netSession.setProxy({mode:'direct'}), 10000, 'loopback-only session initialization');
      const blockedRequests = [];
      netSession.webRequest.onBeforeRequest((details, callback) => {
        const allowed = details.url.startsWith(input.feedUrl);
        if (!allowed) blockedRequests.push(details.url);
        callback({cancel:!allowed});
      });
      const executor = new ElectronHttpExecutor();
      executor.cachedSession = netSession;
      const pendingRequests = new Set();
      const makeRequest = executor.createRequest.bind(executor);
      executor.createRequest = (options, callback) => {
        const request = makeRequest(options, callback);
        pendingRequests.add(request);
        request.once('close', () => pendingRequests.delete(request));
        request.once('error', () => pendingRequests.delete(request));
        return request;
      };
      const abortRequests = () => { for (const request of pendingRequests) request.abort(); };
      updater.httpExecutor = executor;
      updater.setFeedURL({provider:'generic', url:input.feedUrl, channel:'latest'});
      let downloadedEvents = 0, errorCode = null, errorMessage = null;
      updater.on('update-downloaded', () => { downloadedEvents++; });
      const observedErrors = [];
      updater.on('error', error => observedErrors.push(String(error.code ?? 'error')));
      const update = await within(updater.checkForUpdates(), 30000, `${input.scenario} update metadata check`, abortRequests);
      if (!update?.isUpdateAvailable) throw new Error('The simulated old AppAdapter did not discover the generated newer version');
      let downloadedFiles = [];
      try { downloadedFiles = await within(updater.downloadUpdate(update.cancellationToken), 120000,
        `${input.scenario} installer download`, () => { update.cancellationToken.cancel(); abortRequests(); }); }
      catch (error) { errorCode = String(error.code ?? 'error'); errorMessage = String(error.message ?? '').slice(0,300); }
      finally { abortRequests(); }
      return {targetVersion:update.updateInfo.version, downloadedFiles, downloadedEvents,
        errorCode, errorMessage, observedErrors, installAttempts, blockedRequests};
    }, {caseRoot, configPath, feedUrl, channel:channel.channel, scenario}), 180000, `${scenario} updater scenario`);
    assert.equal(result.targetVersion, metadata.version);
    assert.equal(result.installAttempts, 0);
    assert.deepEqual(result.blockedRequests, [], 'the updater must request only the loopback feed');
    if (scenario === 'valid') {
      assert.equal(result.errorCode, null, result.errorMessage);
      assert.equal(result.downloadedEvents, 1);
      assert.equal(result.downloadedFiles.length, 1);
      const downloadedPath = await fs.realpath(result.downloadedFiles[0]);
      const relative = path.relative(await fs.realpath(caseRoot), downloadedPath);
      assert.ok(relative && !relative.startsWith('..') && !path.isAbsolute(relative), 'download cache must stay in this test case');
      assert.equal(await hashFile(downloadedPath), sha512);
      checks.push('real bundled NsisUpdater detected and downloaded the generated installer; SHA-512 matched');
    } else {
      assert.equal(result.downloadedEvents, 0);
      assert.equal(result.downloadedFiles.length, 0);
      assert.match(`${result.errorCode} ${result.errorMessage}`, /sha512|checksum/i, 'the deliberately wrong checksum must be the reason for rejection');
      checks.push('a fresh cache with incorrect SHA-512 metadata rejected the installer before update-downloaded');
    }
  }
  for (const scenario of ['valid', 'invalid']) {
    assert.ok(requests.includes(`/${scenario}/${metadataName}`));
    assert.ok(requests.some(request => decodeURIComponent(request) === `/${scenario}/${installerName}`));
  }
  const report = {passed:true, channel:channel.channel, installer:installerName, targetVersion:metadata.version,
    simulatedInstalledVersion:'0.0.0', installerBytes, sha512, checks,
    method:'Packaged Electron, bundled NsisUpdater and ElectronHttpExecutor, isolated AppAdapter/cache, loopback-only feed.',
    limitations:'Download and checksum verification only. No installer execution, installed v1→v2 upgrade, real OS mouse input, webcam, or public Release was tested.'};
  await fs.mkdir(path.join(root, 'test-results'), {recursive:true});
  await fs.writeFile(path.join(root, 'test-results/update-download.json'), `${JSON.stringify(report, null, 2)}\n`);
  console.log(`PASS real updater download and checksum rejection (${channel.channel}, ${metadata.version}); no installation performed.`);
} finally {
  try {
    if (application) await bounded(application.close(), 15000, 'test Electron shutdown', () => application.process()?.kill());
  }
  finally {
    server.closeAllConnections();
    await bounded(new Promise(resolve => server.close(resolve)), 5000, 'loopback server shutdown');
    const resolved = await fs.realpath(tempRoot);
    assert.equal(path.dirname(resolved).toLowerCase(), tempBase.toLowerCase());
    assert.ok(path.basename(resolved).startsWith('manux-update-download-'));
    await fs.rm(resolved, {recursive:true, force:true});
  }
}
