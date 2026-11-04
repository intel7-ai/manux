import test from 'node:test';
import assert from 'node:assert/strict';
import {MouseSession, STALL_MS} from '../src/mouse-session.js';

function fakeBridge({available = true} = {}) {
  const calls = {send:[], stop:0, setEnabled:[]};
  let listener = null;
  return {
    calls,
    emit:status => listener?.(status),
    onStatus(callback) { listener = callback; return () => { listener = null; }; },
    getStatus:async () => ({available, enabled:false}),
    setEnabled:async value => { calls.setEnabled.push(value); return {available, enabled:value}; },
    send:events => { calls.send.push(events); return true; },
    stop:() => { calls.stop++; },
  };
}
function fakeController() {
  return {
    resets:0,
    reset() { this.resets++; return {phase:'disabled', events:[{type:'release'}]}; },
    update({now, enabled}) {
      if (this.last !== undefined && now <= this.last) return {phase:'stale', mode:'idle', events:[]};
      this.last = now;
      return {phase:enabled ? 'tracking' : 'disabled', mode:'move', events:enabled ? [{type:'move', dx:1, dy:0}] : []};
    },
  };
}
const noTimers = {setInterval:() => 0, clearInterval:() => {}};
const tick = () => new Promise(resolve => setImmediate(resolve));

test('desktop session sends only fresh frames while enabled', async () => {
  const bridge = fakeBridge();
  let clock = 0;
  const session = new MouseSession({bridge, controller:fakeController(), clock:() => clock, timers:noTimers});
  await tick();
  assert.equal(session.desktop, true);
  session.update({now:10, quality:true});
  assert.equal(bridge.calls.send.length, 0, 'nothing is sent before control starts');
  await session.start();
  session.update({now:20, quality:true});
  session.update({now:20, quality:true});                 // stale: no send, no lease
  assert.equal(bridge.calls.send.length, 1);
  assert.deepEqual(bridge.calls.setEnabled, [true]);
});

test('a late result or a stall stops control and releases the native lease', async () => {
  const bridge = fakeBridge();
  let clock = 0, watchdog = null;
  const session = new MouseSession({bridge, controller:fakeController(), clock:() => clock,
    timers:{setInterval:fn => { watchdog = fn; return 1; }, clearInterval:() => {}}});
  await tick();
  await session.start();
  session.update({now:1, quality:true, latency:600});
  assert.equal(session.enabled, false);
  assert.ok(bridge.calls.stop >= 1);
  await session.start();
  clock = STALL_MS + 1;
  watchdog();
  assert.equal(session.enabled, false);
  assert.match(session.message, /멈춰/);
});

test('without the desktop app the session previews and never sends', async () => {
  const session = new MouseSession({bridge:null, controller:fakeController(), timers:noTimers});
  await session.start();
  assert.equal(session.enabled, true);
  assert.match(session.message, /미리보기/);
  assert.equal(session.update({now:5, quality:true}).phase, 'tracking');
});

test('the bridge turning control off stops the session', async () => {
  const bridge = fakeBridge();
  const session = new MouseSession({bridge, controller:fakeController(), timers:noTimers});
  await tick();
  await session.start();
  bridge.emit({available:true, enabled:false, error:'Ctrl+Alt+M'});
  assert.equal(session.enabled, false);
  assert.equal(session.message, 'Ctrl+Alt+M');
});
