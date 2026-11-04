import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {MouseBridge, validateMouseEvents} from '../desktop/mouse-bridge.mjs';
import {isAppDocument, canRequestCamera, canCheckCamera, readDesktopPort} from '../desktop/security.mjs';

test('desktop origin has stable default with explicit ephemeral test opt-in', () => {
  assert.equal(readDesktopPort({}), 4175);
  assert.equal(readDesktopPort({MANUX_DESKTOP_PORT:'0'}), 0);
  assert.equal(readDesktopPort({MANUX_DESKTOP_PORT:'4176'}), 4176);
  assert.equal(readDesktopPort({MANUX_DESKTOP_PORT:'65535'}), 65535);
  for (const value of ['', ' ', '-1', '65536', '1.5', '12x', '4175 ', 4175]) {
    assert.throws(() => readDesktopPort({MANUX_DESKTOP_PORT:value}), /integer/);
  }
});

test('camera and IPC document checks accept local fragments, reject other origins/frames/audio', () => {
  const origin = 'http://127.0.0.1:4175', contents = {};
  const request = {isMainFrame:true, requestingUrl:origin + '/#mouse', mediaTypes:['video']};
  assert.equal(isAppDocument(origin + '/', origin), true);
  assert.equal(isAppDocument(origin + '/#mouse', origin), true);
  assert.equal(isAppDocument(origin + '/studio.html', origin), true);
  for (const url of [origin + '/studio.html?x=1', origin + '/overlay.html', origin + '/studio.css']) assert.equal(isAppDocument(url, origin), false, url);
  for (const url of ['https://example.com/', origin + '/src/app.js', origin + '/?remote=true', 'http://localhost:4175/', 'not-a-url']) assert.equal(isAppDocument(url, origin), false);
  assert.equal(canRequestCamera(contents, contents, 'media', request, origin), true);
  assert.equal(canRequestCamera(contents, contents, 'media', {...request, mediaTypes:['video','audio']}, origin), false);
  assert.equal(canRequestCamera(contents, contents, 'media', {...request, isMainFrame:false}, origin), false);
  assert.equal(canRequestCamera({}, contents, 'media', request, origin), false);
  const check = {isMainFrame:true, requestingUrl:origin+'/', mediaType:'video'};
  assert.equal(canCheckCamera(contents, contents, 'media', origin + '/', check, origin), true);
  assert.equal(canCheckCamera(contents, contents, 'media', origin, {...check,mediaType:'audio'}, origin), false);
  assert.equal(canCheckCamera(contents, contents, 'media', origin, {...check,isMainFrame:false}, origin), false);
  assert.equal(canCheckCamera(contents, contents, 'media', 'https://example.com/', check, origin), false);
});

function fixture() {
  let now = 1000;
  const child = new EventEmitter();
  child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
  child.kill = () => child.emit('exit', 0);
  const commands = [];
  child.stdin.on('data', chunk => commands.push(...String(chunk).trim().split('\n').map(JSON.parse)));
  const bridge = new MouseBridge({platform:'win32', now:() => now,
    spawnHelper:() => { queueMicrotask(() => child.stdout.write('{"kind":"ready"}\n')); return child; }});
  return {bridge, child, commands, advance:delta => { now += delta; }};
}

test('mouse validator accepts only bounded finite mouse events and copies data', () => {
  const events = [{type:'move',dx:2,dy:-3.25}, {type:'button',button:'left',down:true}, {type:'scroll',dy:240}, {type:'release'}];
  assert.deepEqual(validateMouseEvents(events), events);
  assert.notEqual(validateMouseEvents(events)[0], events[0]);
  for (const invalid of [[{type:'move',dx:101,dy:0}], [{type:'move',dx:NaN,dy:0}], [{type:'move',dx:0,dy:Infinity}],
    [{type:'scroll',dy:-241}], [{type:'button',button:'middle',down:true}], [{type:'button',button:'left',down:1}],
    [{type:'key',key:'Enter'}], [{type:'release',command:'calc'}], [{type:'move',dx:'1',dy:0}], new Array(9).fill({type:'release'}), null]) {
    assert.throws(() => validateMouseEvents(invalid), TypeError);
  }
});

test('OS control starts off and rejects old generations after stop/re-enable', async t => {
  const {bridge, commands} = fixture(); t.after(() => bridge.close());
  await bridge.start();
  assert.equal(bridge.status().enabled, false);
  assert.equal(bridge.send([{type:'move',dx:5,dy:4}]), false);
  const first = await bridge.setEnabled(true);
  assert.equal(first.enabled, true);
  assert.equal(bridge.send([{type:'button',button:'left',down:true}], {generation:first.generation,sentAt:1000}), true);
  bridge.disable();
  await bridge.setEnabled(true);
  const count = commands.length;
  assert.equal(bridge.send([{type:'move',dx:5,dy:4}], {generation:first.generation,sentAt:1000}), false);
  assert.equal(commands.length, count);
  assert.equal(commands.find(command => command.kind === 'disable').generation, first.generation + 1);
});

test('stalled camera expires even without renderer events and never auto-enables', async t => {
  const {bridge, advance, commands} = fixture(); t.after(() => bridge.close());
  await bridge.setEnabled(true);
  advance(700); assert.equal(bridge.send([]), true); // Fresh inference without motion renews lease.
  advance(1001); bridge.tick();
  assert.equal(bridge.status().enabled, false);
  assert.match(bridge.status().error, /카메라/);
  assert.equal(commands.at(-1).kind, 'disable');
  assert.equal(bridge.send([{type:'move',dx:5,dy:4}]), false);
});

test('emergency stop while a button is held tells the helper to release, publishes why, and ignores buffered input', async t => {
  const {bridge, commands} = fixture(); t.after(() => bridge.close());
  const statuses = []; bridge.on('status', status => statuses.push(status));
  const enabled = await bridge.setEnabled(true);
  assert.equal(bridge.send([{type:'button',button:'left',down:true}]), true);
  bridge.disable('Ctrl+Alt+M으로 마우스 제어를 멈췄습니다.');
  const disable = commands.at(-1);
  assert.equal(disable.kind, 'disable');
  assert.equal(disable.generation, enabled.generation + 1);
  assert.equal(statuses.at(-1).enabled, false);
  assert.match(statuses.at(-1).error, /Ctrl\+Alt\+M/);
  const count = commands.length;
  for (const events of [[{type:'move',dx:3,dy:3}], [], [{type:'button',button:'left',down:true}]]) {
    assert.equal(bridge.send(events), false);
  }
  assert.equal(commands.length, count);
  // Only an explicit new enable resumes, with a newer generation.
  const again = await bridge.setEnabled(true);
  assert.equal(again.enabled, true);
  assert.ok(again.generation > disable.generation);
});

test('old or invalid batch disables without partially executing valid members', async t => {
  const {bridge, commands, advance} = fixture(); t.after(() => bridge.close());
  await bridge.setEnabled(true);
  assert.equal(bridge.send([{type:'button',button:'left',down:true}, {type:'move',dx:1000,dy:0}]), false);
  assert.equal(commands.some(command => command.kind === 'batch'), false);
  await bridge.setEnabled(true);
  advance(400);
  assert.equal(bridge.send([], {sentAt:1000}), false);
  assert.equal(bridge.status().enabled, false);
});

test('native watchdog and process failure revoke control', async t => {
  const {bridge, child, commands} = fixture(); t.after(() => bridge.close());
  await bridge.setEnabled(true);
  child.stdout.write(JSON.stringify({kind:'stopped',generation:bridge.generation,reason:'watchdog'}) + '\n');
  assert.equal(bridge.enabled, false);
  assert.equal(bridge.error, 'watchdog');
  await bridge.setEnabled(true);
  child.emit('exit', 1);
  assert.equal(bridge.enabled, false);
  assert.equal(bridge.status().available, false);
  assert.equal(commands.at(-1).kind, 'disable');
});

test('stop during initialization cancels pending enable and close is idempotent', async () => {
  const {bridge, commands} = fixture();
  const pending = bridge.setEnabled(true);
  bridge.disable();
  await pending;
  assert.equal(bridge.enabled, false);
  assert.equal(commands.some(command => command.kind === 'enable'), false);
  bridge.close(); bridge.close();
  assert.equal(commands.filter(command => command.kind === 'shutdown').length, 1);
});

test('unsupported platform cannot enable native input', async t => {
  const bridge = new MouseBridge({platform:'linux', spawnHelper:() => { throw new Error('must not spawn'); }});
  t.after(() => bridge.close());
  const status = await bridge.setEnabled(true);
  assert.equal(status.enabled, false);
  assert.equal(status.available, false);
});

function glideFixture() {
  let now = 1000;
  const timers = [];
  const child = new EventEmitter();
  child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
  child.kill = () => child.emit('exit', 0);
  const commands = [];
  child.stdin.on('data', chunk => commands.push(...String(chunk).trim().split('\n').map(JSON.parse)));
  const bridge = new MouseBridge({platform:'win32', now:() => now, interpolate:true,
    scheduler:{set:fn => { timers.push(fn); return timers.length; }, clear:() => {}},
    spawnHelper:() => { queueMicrotask(() => child.stdout.write('{"kind":"ready"}\n')); return child; }});
  const runTimers = () => { while (timers.length) timers.shift()(); };
  const moved = () => commands.filter(c => c.kind === 'batch').flatMap(c => c.events).filter(e => e.type === 'move')
    .reduce((sum, e) => [sum[0] + e.dx, sum[1] + e.dy], [0, 0]);
  return {bridge, commands, runTimers, moved, advance:delta => { now += delta; }};
}

test('interpolation spreads one frame of movement over several small moves and loses none', async t => {
  const {bridge, commands, runTimers, moved} = glideFixture(); t.after(() => bridge.close());
  await bridge.setEnabled(true);
  assert.equal(bridge.send([{type:'move', dx:90, dy:-45}]), true);
  runTimers();
  const batches = commands.filter(c => c.kind === 'batch');
  assert.ok(batches.length > 2, 'the move is split into steps');
  assert.deepEqual(moved(), [90, -45]);
});

test('a button flushes the remaining glide first, and stopping drops unsent movement', async t => {
  const {bridge, commands, moved} = glideFixture(); t.after(() => bridge.close());
  await bridge.setEnabled(true);
  bridge.send([{type:'move', dx:60, dy:0}]);
  bridge.send([{type:'button', button:'left', down:true}]);
  const batches = commands.filter(c => c.kind === 'batch');
  assert.deepEqual(moved(), [60, 0], 'all movement is written before the button');
  assert.equal(batches.at(-1).events[0].type, 'button');
  bridge.send([{type:'move', dx:80, dy:0}]);
  const before = moved()[0];
  bridge.disable();
  assert.ok(moved()[0] - before < 80, 'unsent glide steps are dropped after stop');
});
