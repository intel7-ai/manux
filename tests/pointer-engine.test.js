import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {PointerEngine, triggerRatio, thumbContacts, MoveLatch} from '../src/pointer-engine.js';

// Reference trace from the Python engine (hand-mouse/src/pointer_engine.py); see scripts/pointer-reference.py.
const reference = JSON.parse(readFileSync(new URL('./fixtures/pointer-engine-reference.json', import.meta.url), 'utf8'));
const toPoints = hand => hand && hand.map(([x, y, z]) => ({x, y, z}));
const close = (a, b, eps = 1e-5) => Math.abs(a - b) <= eps;

test('JS pointer engine reproduces the Python reference trace frame by frame', () => {
  const engine = new PointerEngine();
  const mismatches = [];
  for (const [i, frame] of reference.frames.entries()) {
    const out = engine.step(frame.t, toPoints(frame.hand), reference.aspect,
      {clickStyle:frame.style, anchor:frame.anchor}, {active:frame.active});
    if (frame.record) engine.recordCursor(frame.t, frame.record);
    const want = frame.out;
    if ((out === null) !== (want === null)) { mismatches.push(`${i}: null`); continue; }
    if (!want) continue;
    const pointsEqual = (a, b) => (a === null && b === null) || (a && b && close(a[0], b[0]) && close(a[1], b[1]));
    const fields = {
      action:out.action === want.action,
      preview:pointsEqual(out.preview, want.preview),
      cursor:pointsEqual(out.cursor, want.cursor),
      warp:JSON.stringify(out.warp) === JSON.stringify(want.warp),
      latched:out.latched === want.latched,
      armed:out.armed === want.armed,
      source:out.source === want.source,
      ratio:want.ratio === null ? Number.isNaN(out.ratio) : close(out.ratio, want.ratio),
    };
    const bad = Object.entries(fields).filter(([, ok]) => !ok).map(([name]) => name);
    if (bad.length) mismatches.push(`${i}: ${bad.join(',')} (js ${out.action} / py ${want.action})`);
  }
  assert.deepEqual(mismatches.slice(0, 10), []);
  const actions = reference.frames.map(f => f.out?.action);
  // The trace exercises every trigger outcome, so a match here is meaningful.
  for (const action of ['left_click', 'right_click', 'drag_hold', 'pointer_wait', 'move_cursor']) assert.ok(actions.includes(action), action);
});

test('thumb contacts are reported only when unambiguous', () => {
  assert.deepEqual(thumbContacts([.1, .9, .9, .9]), [0, 1]);
  assert.deepEqual(thumbContacts([.3, .9, .9, .9]), []);
  assert.deepEqual(thumbContacts([.9, .9, .9, .9]), []);
});

test('trigger ratio is NaN for a degenerate palm', () => {
  const points = Array.from({length:21}, () => ({x:.5, y:.5, z:0}));
  assert.ok(Number.isNaN(triggerRatio(points, 9 / 16)));
});

test('move latch starts on fast motion and ends after staying still', () => {
  const latch = new MoveLatch();
  let t = 0, x = .5;
  for (let i = 0; i < 6; i++, t += 1 / 30) { x += .02; latch.update(t, [x, .5], 9 / 16); }
  assert.equal(latch.latched, true);
  for (let i = 0; i < 30; i++, t += 1 / 30) latch.update(t, [x, .5], 9 / 16);
  assert.equal(latch.latched, false);
});
