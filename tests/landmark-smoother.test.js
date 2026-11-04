import test from 'node:test';
import assert from 'node:assert/strict';
import {LandmarkSmoother} from '../src/landmark-smoother.js';
import {hand} from './helpers/mouse-hand.js';

// Deterministic pseudo-noise so the test does not depend on Math.random.
const noise = i => (Math.sin(i * 12.9898) * 43758.5453 % 1) * .006 - .003;
const jitter = (points, i) => points.map((p, j) => ({x:p.x + noise(i * 21 + j), y:p.y + noise(i * 21 + j + 7), z:0}));
const spread = values => Math.max(...values) - Math.min(...values);

test('a still but noisy hand comes out steadier', () => {
  const smoother = new LandmarkSmoother(), base = hand('move');
  const raw = [], smooth = [];
  for (let i = 0; i < 60; i++) {
    const noisy = jitter(base, i);
    raw.push(noisy[8].x);
    smooth.push(smoother.smooth('right', noisy, i / 30)[8].x);
  }
  assert.ok(spread(smooth.slice(20)) < spread(raw.slice(20)) / 2, `${spread(smooth.slice(20))} vs ${spread(raw.slice(20))}`);
});

test('a fast move is followed without large lag', () => {
  const smoother = new LandmarkSmoother(), base = hand('move');
  let out;
  for (let i = 0; i < 30; i++) out = smoother.smooth('right', base.map(p => ({...p, x:p.x + i * .02})), i / 30);
  assert.ok(Math.abs(out[8].x - (base[8].x + 29 * .02)) < .05);
});

test('hands keep separate history, and a gap or bad input resets', () => {
  const smoother = new LandmarkSmoother(), base = hand('move');
  smoother.smooth('right', base, 0);
  const moved = base.map(p => ({...p, x:p.x + .3}));
  assert.equal(smoother.smooth('left', moved, .033)[8].x, moved[8].x, 'a new hand starts from its own first frame');
  assert.equal(smoother.smooth('right', moved, 1)[8].x, moved[8].x, 'a gap longer than 0.25 s restarts the filter');
  assert.equal(smoother.smooth('right', null, 1.1), null);
  assert.equal(smoother.hands.has('right'), false);
});
