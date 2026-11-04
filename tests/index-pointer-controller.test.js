import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {IndexPointerController} from '../src/index-pointer-controller.js';
import {validateMouseEvents} from '../desktop/mouse-bridge.mjs';

const reference = JSON.parse(readFileSync(new URL('./fixtures/pointer-engine-reference.json', import.meta.url), 'utf8'));
// The engine's decisions are mirror-invariant, so the reference hands can be fed as camera input.
const toLandmarks = hand => hand && hand.map(([x, y, z]) => ({x, y, z}));
const trace = reference.frames.filter(f => f.style === 'trigger' && f.anchor === 'index');

function replay(controller, frames = trace) {
  return frames.map(f => controller.update({landmarks:toLandmarks(f.hand), now:f.t * 1000, enabled:f.active,
    quality:Boolean(f.hand), width:1280, height:720}));
}

test('trigger preset turns engine decisions into native mouse events the bridge accepts', () => {
  const states = replay(new IndexPointerController({clickStyle:'trigger', screenWidth:1920}));
  for (const state of states) assert.doesNotThrow(() => validateMouseEvents(state.events));
  const events = states.flatMap(s => s.events);
  const buttons = events.filter(e => e.type === 'button');
  const count = (button, down) => buttons.filter(e => e.button === button && e.down === down).length;
  assert.ok(count('left', true) >= 2, 'a click and a drag both press the left button');
  assert.equal(count('left', true), count('left', false), 'every left press is released');
  assert.equal(count('right', true), 1);
  assert.equal(count('right', false), 1);
  assert.ok(events.some(e => e.type === 'move'), 'a moving hand moves the cursor');
});

test('a repeated timestamp changes nothing and emits nothing', () => {
  const controller = new IndexPointerController();
  const [first] = trace;
  const input = {landmarks:toLandmarks(first.hand), now:1000, enabled:true, width:1280, height:720};
  controller.update(input);
  const stale = controller.update(input);
  assert.equal(stale.phase, 'stale');
  assert.deepEqual(stale.events, []);
  assert.equal(controller.update({...input, now:900}).phase, 'stale');
});

test('losing the hand during a drag releases the left button', () => {
  const controller = new IndexPointerController();
  const dragStart = trace.findIndex(f => f.out?.action === 'drag_hold');
  const states = replay(controller, trace.slice(0, dragStart + 3));
  assert.ok(states.flatMap(s => s.events).some(e => e.type === 'button' && e.button === 'left' && e.down));
  const lost = controller.update({landmarks:null, now:(trace[dragStart + 3].t) * 1000, enabled:true, quality:false});
  assert.equal(lost.phase, 'invalid');
  assert.deepEqual(lost.events, [{type:'button', button:'left', down:false}]);
});

test('turning control off resets and releases; tracking-only never clicks', () => {
  const controller = new IndexPointerController();
  replay(controller, trace.slice(0, 10));
  const off = controller.update({landmarks:toLandmarks(trace[10].hand), now:trace[10].t * 1000, enabled:false});
  assert.equal(off.phase, 'disabled');
  assert.deepEqual(off.events, [{type:'release'}]);
  const tracking = replay(new IndexPointerController({clickStyle:'off'}));
  assert.ok(!tracking.flatMap(s => s.events).some(e => e.type === 'button'));
});

test('moves never exceed the bridge limits even for a fast hand', () => {
  const controller = new IndexPointerController({sensitivity:3600, screenWidth:3840});
  const hand = toLandmarks(trace[0].hand);
  const states = [];
  for (let i = 0; i < 12; i++) {
    const shifted = hand.map(p => ({...p, x:p.x + i * .08}));
    states.push(controller.update({landmarks:shifted, now:1000 + i * 33, enabled:true, width:1280, height:720}));
  }
  for (const state of states) {
    assert.doesNotThrow(() => validateMouseEvents(state.events));
    assert.ok(state.events.length <= 8);
  }
});

test('invalid options are rejected', () => {
  assert.throws(() => new IndexPointerController({clickStyle:'pinch'}), RangeError);
  assert.throws(() => new IndexPointerController({sensitivity:0}), RangeError);
});
