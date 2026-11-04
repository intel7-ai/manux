import test from 'node:test';
import assert from 'node:assert/strict';
import {HandSelection, physicalSide, selectedDetection} from '../src/hand-selection.js';

const hand = (handedness, score = .9) => ({handedness, score});

test('physical side uses the label unless the frame was mirrored, and needs a confident label', () => {
  assert.equal(physicalSide(hand('Right')), 'right');
  assert.equal(physicalSide(hand('Right'), true), 'left');
  assert.equal(physicalSide(hand('Right', .5)), '');
  assert.equal(physicalSide({handedness:'Unknown', score:1}), '');
});

test('only a single hand on the chosen side is selected', () => {
  assert.equal(selectedDetection([hand('Left')], 'left').handedness, 'Left');
  assert.equal(selectedDetection([hand('Left'), hand('Left')], 'left'), null);
  assert.equal(selectedDetection([hand('Right')], 'left'), null);
  assert.equal(selectedDetection([hand('Left')], ''), null);
});

test('one hand held for five seconds chooses its side; a gap, a switch or two hands restart the count', () => {
  const selection = new HandSelection();
  let chosen = '', t = 0;
  for (; t < 4.9; t += .1) assert.equal(selection.observe([hand('Right')], t), '');
  for (; t < 5.2 && !chosen; t += .1) chosen = selection.observe([hand('Right')], t);
  assert.equal(chosen, 'right');

  const restart = new HandSelection();
  for (t = 0; t < 3; t += .1) restart.observe([hand('Right')], t);
  restart.observe([hand('Left')], t);                     // switching hands restarts
  assert.equal(restart.side, 'left');
  assert.ok(restart.secondsRemaining(t) > 4.9);
  restart.observe([hand('Left'), hand('Right')], t + .1);   // two hands: nothing chosen
  assert.equal(restart.side, '');
  restart.observe([hand('Left')], t + .2);
  restart.observe([hand('Left')], t + 1);                    // a gap over 0.25 s restarts
  assert.ok(restart.secondsRemaining(t + 1) === 5);
});
