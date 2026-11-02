import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {createUpdateManager, updateDisabledReason} from '../desktop/updater.mjs';

function fakeUpdater({checkResult = async () => {}} = {}) {
  const updater = new EventEmitter();
  Object.assign(updater, {autoDownload: false, autoInstallOnAppQuit: true, allowDowngrade: true, allowPrerelease: true,
    checks: 0, installs: [], async checkForUpdates() { this.checks++; return checkResult.call(this); },
    quitAndInstall(...args) { this.installs.push(args); }});
  return updater;
}
function manager(overrides = {}) {
  const updater = overrides.updater ?? fakeUpdater();
  const timers = [];
  const statuses = [];
  const mouse = {active: false};
  const prompts = [];
  const instance = createUpdateManager({
    isPackaged: true, platform: 'win32', env: {},
    loadUpdater: overrides.loadUpdater ?? (async () => updater),
    isMouseActive: () => mouse.active,
    onStatus: status => statuses.push(status),
    promptInstall: async version => { prompts.push(version); return overrides.approve ?? false; },
    setTimer: (fn, ms) => { const timer = {fn, ms, unref() {}}; timers.push(timer); return timer; },
    clearTimer: timer => { timer.cleared = true; },
    now: () => 1000, ...overrides.options,
  });
  return {instance, updater, timers, statuses, mouse, prompts};
}

test('updates stay off in development, tests, other platforms and when opted out, and never load the updater', async () => {
  for (const [input, pattern] of [[{isPackaged: false, platform: 'win32', env: {}}, /개발/],
    [{isPackaged: true, platform: 'linux', env: {}}, /Windows/],
    [{isPackaged: true, platform: 'win32', env: {MANUX_AUTO_UPDATE: '0'}}, /MANUX_AUTO_UPDATE/]]) {
    assert.match(updateDisabledReason(input), pattern);
    let loaded = 0;
    const instance = createUpdateManager({...input, loadUpdater: async () => { loaded++; return fakeUpdater(); }});
    assert.equal(instance.enabled, false);
    await instance.start();
    assert.equal((await instance.check()).state, 'disabled');
    assert.deepEqual(await instance.install(), {ok: false, ...instance.status(), error: '설치할 업데이트가 준비되지 않았습니다.'});
    assert.equal(loaded, 0);
  }
  assert.equal(updateDisabledReason({isPackaged: true, platform: 'win32', env: {}}), null);
});

test('packaged start applies the safe policy and schedules the first check, then a repeat', async () => {
  const {instance, updater, timers} = manager();
  await instance.start();
  assert.deepEqual([updater.autoDownload, updater.autoInstallOnAppQuit, updater.allowDowngrade, updater.allowPrerelease], [true, false, false, false]);
  assert.equal(timers.length, 1);
  assert.equal(updater.checks, 0, 'no network check happens during start');
  await timers[0].fn();
  assert.equal(updater.checks, 1);
  assert.equal(timers.length, 2, 'the next check is scheduled after the first one');
  instance.stop();
  assert.equal(timers.at(-1).cleared, true);
});

test('status follows the updater events from checking to downloaded', async () => {
  const {instance, updater, statuses} = manager();
  await instance.start();
  updater.emit('checking-for-update');
  assert.equal(instance.status().state, 'checking');
  updater.emit('update-available', {version: '0.3.0'});
  assert.deepEqual([instance.status().state, instance.status().version, instance.status().percent], ['downloading', '0.3.0', 0]);
  updater.emit('download-progress', {percent: 41.6});
  assert.equal(instance.status().percent, 42);
  updater.emit('update-downloaded', {version: '0.3.0'});
  assert.deepEqual([instance.status().state, instance.status().percent], ['downloaded', 100]);
  assert.ok(statuses.length >= 4);
  updater.emit('update-not-available');
  assert.equal(instance.status().state, 'not-available');
});

test('failed checks never throw and are shown as a short status; the next check still works', async () => {
  let fail = true;
  const updater = fakeUpdater({checkResult: async () => { if (fail) throw new Error('getaddrinfo ENOTFOUND github.com\n    at secret/stack/path.js:1:1'); }});
  const {instance} = manager({updater});
  await instance.start();
  const failed = await instance.check();
  assert.equal(failed.state, 'error');
  assert.equal(failed.error, 'getaddrinfo ENOTFOUND github.com');
  updater.emit('error', new Error('404 Not Found'));
  assert.equal(instance.status().error, '404 Not Found');
  fail = false;
  assert.equal((await instance.check()).state, 'checking', 'recovers; the final state comes from updater events');
  const broken = manager({loadUpdater: async () => { throw new Error('module missing'); }});
  assert.equal((await broken.instance.start()).state, 'error');
  assert.equal((await broken.instance.check()).state, 'error', 'a broken updater never throws into the app');
});

test('overlapping checks are ignored while checking or downloading', async () => {
  const {instance, updater} = manager();
  await instance.start();
  updater.emit('checking-for-update');
  await instance.check();
  updater.emit('update-available', {version: '1.0.0'});
  await instance.check();
  assert.equal(updater.checks, 0);
});

test('install requires a downloaded update and is refused while mouse control is on', async () => {
  const {instance, updater, mouse} = manager();
  await instance.start();
  assert.equal((await instance.install()).ok, false);
  updater.emit('update-downloaded', {version: '0.3.0'});
  mouse.active = true;
  const refused = await instance.install();
  assert.equal(refused.ok, false);
  assert.match(refused.error, /마우스 제어/);
  assert.equal(updater.installs.length, 0);
  mouse.active = false;
  assert.equal((await instance.install()).ok, true);
  assert.deepEqual(updater.installs, [[false, true]], 'visible installer and relaunch; the shell starts with control OFF');
});

test('a downloaded update asks for approval and never installs without it', async () => {
  const declined = manager({approve: false});
  await declined.instance.start();
  declined.updater.emit('update-downloaded', {version: '0.3.0'});
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(declined.prompts, ['0.3.0']);
  assert.equal(declined.updater.installs.length, 0);
  declined.updater.emit('update-downloaded', {version: '0.3.0'});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(declined.prompts.length, 1, 'the same version is not prompted twice');

  const approved = manager({approve: true});
  await approved.instance.start();
  approved.updater.emit('update-downloaded', {version: '0.4.0'});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(approved.updater.installs.length, 1);
});

test('while mouse control is on the prompt waits until control turns off', async () => {
  const {instance, updater, mouse, prompts} = manager({approve: true});
  await instance.start();
  mouse.active = true;
  updater.emit('update-downloaded', {version: '0.3.0'});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(prompts.length, 0);
  assert.equal(updater.installs.length, 0);
  instance.mouseStateChanged();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(prompts.length, 0, 'still on: nothing happens');
  mouse.active = false;
  instance.mouseStateChanged();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(prompts, ['0.3.0']);
  assert.equal(updater.installs.length, 1);
});

test('a throwing status listener or prompt cannot break updating', async () => {
  const updater = fakeUpdater();
  const instance = createUpdateManager({isPackaged: true, platform: 'win32', env: {}, loadUpdater: async () => updater,
    onStatus: () => { throw new Error('listener'); }, promptInstall: async () => { throw new Error('dialog'); },
    setTimer: () => ({unref() {}})});
  await instance.start();
  assert.doesNotThrow(() => updater.emit('update-downloaded', {version: '0.3.0'}));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(instance.status().state, 'downloaded');
  assert.equal(updater.installs.length, 0);
});
