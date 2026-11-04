import test from 'node:test';
import assert from 'node:assert/strict';
import {TwoHandController} from '../src/two-hand-controller.js';
import {validateMouseEvents} from '../desktop/mouse-bridge.mjs';
import {hand} from './helpers/mouse-hand.js';

const open = hand('move');
const thumbIndex = hand('left');          // helper pose: thumb tip on the index tip
const thumbMiddle = hand('right');        // helper pose: thumb tip on the middle tip
let clock = 1000;
const step = (c, moveHand, actionHand, enabled = true) => c.update({moveHand, actionHand, now:clock += 33, enabled, width:1280, height:720});
const buttons = states => states.flatMap(s => s.events).filter(e => e.type === 'button');

test('the action hand presses and releases the left button with hysteresis, the move hand moves', () => {
  const c = new TwoHandController({screenWidth:1920});
  const states = [];
  for (let i = 0; i < 4; i++) states.push(step(c, open.map(p => ({...p, x:p.x + i * .01})), open));
  states.push(step(c, open, thumbIndex));                 // one contact frame: not yet confirmed
  assert.equal(buttons(states).length, 0);
  states.push(step(c, open, thumbIndex));                 // second frame confirms the press
  assert.deepEqual(buttons(states), [{type:'button', button:'left', down:true}]);
  assert.equal(states.at(-1).mode, 'left');
  for (let i = 0; i < 3; i++) states.push(step(c, open.map(p => ({...p, x:p.x + .05 + i * .01})), thumbIndex)); // drag
  states.push(step(c, open, open)); states.push(step(c, open, open));
  assert.deepEqual(buttons(states).map(e => e.down), [true, false]);
  assert.ok(states.flatMap(s => s.events).some(e => e.type === 'move'));
  for (const s of states) assert.doesNotThrow(() => validateMouseEvents(s.events));
});

test('thumb and middle give one right click per touch', () => {
  const c = new TwoHandController();
  const states = [step(c, open, open)];
  for (let i = 0; i < 6; i++) states.push(step(c, open, thumbMiddle));
  states.push(step(c, open, open)); states.push(step(c, open, open));
  for (let i = 0; i < 3; i++) states.push(step(c, open, thumbMiddle));
  const right = buttons(states).filter(e => e.button === 'right' && e.down);
  assert.equal(right.length, 2, 'held contact does not repeat; a new touch clicks again');
});

test('losing either hand or turning control off releases a held button; stale frames do nothing', () => {
  const c = new TwoHandController();
  step(c, open, thumbIndex); step(c, open, thumbIndex);
  assert.equal(c.leftDown, true);
  const lost = step(c, open, null);
  assert.equal(lost.phase, 'action-hand-missing');
  assert.deepEqual(buttons([lost]), [{type:'button', button:'left', down:false}]);
  step(c, open, thumbIndex); step(c, open, thumbIndex);
  assert.equal(step(c, null, thumbIndex).phase, 'invalid');
  assert.equal(c.leftDown, false);
  const stale = c.update({moveHand:open, actionHand:open, now:clock - 100, enabled:true});
  assert.equal(stale.phase, 'stale'); assert.deepEqual(stale.events, []);
  step(c, open, open);
  assert.deepEqual(step(c, open, open, false).events, [{type:'release'}]);
});
