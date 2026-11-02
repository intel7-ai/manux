import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {AirMouseController} from '../src/air-mouse-controller.js';

import {hand} from './helpers/mouse-hand.js';

function session(options = {}) {
  const controller = new AirMouseController({smoothing:1,...options});
  let now = -60;
  const feed = (pose = 'move', transform = {}, overrides = {}) => {
    now += 60;
    return controller.update({landmarks:hand(pose, transform), now, enabled:true, width:1000,height:1000,handedness:'Right',...overrides});
  };
  const hold = (pose = 'move', transform = {}) => [feed(pose,transform),feed(pose,transform),feed(pose,transform),feed(pose,transform)];
  return {controller,feed,hold};
}

const events = states => states.flatMap(s => s.events);
const downs = states => events(states).filter(e => e.type === 'button' && e.down);

test('relaxed open palm settles before relative mirrored pointer movement; first position never teleports', () => {
  const s = session();
  const states = s.hold();
  assert.equal(states[0].phase, 'holding');
  assert.equal(states.at(-1).mode, 'move');
  assert.deepEqual(events(states), []);
  const result = s.feed('move', {x:.02,y:.01});
  assert.equal(result.events.length, 1);
  assert.equal(result.events[0].type, 'move');
  assert.ok(Math.abs(result.events[0].dx + 36) <= 1);
  assert.ok(Math.abs(result.events[0].dy - 18) <= 1);
});

test('left pinch clicks once, moves while held, and releases immediately when fingers separate', () => {
  const s = session(); s.hold('move');
  const entry = s.hold('left');
  assert.deepEqual(downs(entry), [{type:'button',button:'left',down:true}]);
  const drag = s.feed('left', {x:-.02});
  assert.ok(drag.events.some(e => e.type === 'move' && e.dx > 0));
  assert.equal(downs([drag]).length, 0);
  const release = s.feed('move');
  assert.ok(release.events.some(e => e.type === 'button' && e.button === 'left' && !e.down));
  assert.equal(s.feed('move').events.filter(e => e.type === 'button').length, 0);
});

test('right pinch is exactly one click even when held and moved; unpinch rearms it', () => {
  const s = session(); s.hold('move');
  const entry = s.hold('right');
  assert.deepEqual(events(entry), [{type:'button',button:'right',down:true},{type:'button',button:'right',down:false}]);
  const moving = s.hold('right',{x:.03});
  assert.equal(downs(moving).length,0);
  assert.ok(events(moving).some(e=>e.type==='move'&&e.dx<0));
  s.hold('move');
  assert.equal(downs(s.hold('right')).length, 1);
});

test('returning from a confirmed action to the base pose resumes movement without another dwell or jump', () => {
  for (const pose of ['left','right','scroll']) {
    const s = session(); s.hold('move'); s.hold(pose);
    const transition = s.feed('move',{x:.06});
    assert.equal(transition.phase,'active',pose);
    assert.equal(transition.mode,'move',pose);
    assert.equal(transition.events.some(e=>e.type==='move'),false,pose);
    if (pose==='left') assert.ok(transition.events.some(e=>e.type==='button'&&!e.down));
    const next = s.feed('move',{x:.07});
    assert.ok(next.events.some(e=>e.type==='move'&&e.dx<0&&Math.abs(e.dx)<=19),pose);
  }
});

test('an unconfirmed action or lost hand does not bypass the initial movement dwell', () => {
  const s = session(); s.hold('move');
  assert.equal(s.feed('left').phase,'holding');
  assert.equal(s.feed('move').phase,'holding');
  s.hold('left');
  s.feed('left',{}, {landmarks:null});
  assert.equal(s.feed('move').phase,'holding');
});

test('enabling while pinching cannot click until a visible unpinched pose is held', () => {
  for (const pose of ['left','right']) {
    const s = session();
    assert.deepEqual(events(s.hold(pose)), []);
    assert.equal(s.feed(pose).phase, 'release-required');
    s.hold('open');
    assert.equal(downs(s.hold(pose)).length, 1);
  }
});

test('thumb/ring pinch scroll needs movement, ignores horizontal travel, and never moves the cursor', () => {
  const s = session(); s.hold('move');
  assert.deepEqual(events(s.hold('scroll')), []);
  assert.deepEqual(s.feed('scroll',{x:.02}).events, []);
  const down = s.feed('scroll',{x:.02,y:.02});
  assert.ok(down.events.some(e => e.type === 'scroll' && e.dy > 0));
  assert.equal(down.events.some(e => e.type === 'move'), false);
  const up = s.feed('scroll',{x:.02,y:0});
  assert.ok(up.events.some(e => e.type === 'scroll' && e.dy < 0));
  assert.deepEqual(events(s.hold('fist')), []);
});

test('fist and single extended index rest without pointer, click, or scroll', () => {
  const s = session();
  for (const pose of ['fist','index']) {
    assert.deepEqual(events(s.hold(pose)), []);
    assert.equal(s.feed(pose,{x:.02}).mode,'idle');
  }
});

test('ambiguous simultaneous index/middle pinch cannot choose a mouse button', () => {
  const s = session(); s.hold('move');
  assert.deepEqual(events(s.hold('both')), []);
});

test('pinch hysteresis tolerates small finger-distance jitter but releases beyond the outer threshold', () => {
  const s = session(); s.hold('move'); s.hold('left');
  const jitter = hand('left'); jitter[4].x = jitter[8].x - .075; // ~.326 palm widths: hold, not new entry.
  const held = s.feed('left',{}, {landmarks:jitter});
  assert.equal(held.mode, 'left');
  assert.equal(held.events.some(e => e.type === 'button' && !e.down), false);
  const opened = hand('left'); opened[4].x = opened[8].x - .11;
  assert.ok(s.feed('left',{}, {landmarks:opened}).events.some(e => e.type === 'button' && !e.down));
});

test('missing landmarks and poor quality release a drag and require a new unpinched observation', () => {
  for (const lost of [{landmarks:undefined},{quality:false}]) {
    const s = session(); s.hold('move'); s.hold('left');
    const release = s.feed('left',{},lost);
    assert.equal(release.phase,'invalid');
    assert.ok(release.events.some(e => e.type === 'release'));
    assert.deepEqual(events(s.hold('left')), []);
    s.hold('move');
    assert.equal(downs(s.hold('left')).length,1);
  }
});

test('tracking loss does not rearm a held right click', () => {
  const s = session(); s.hold('move'); s.hold('right');
  s.feed('right',{}, {landmarks:[]});
  assert.deepEqual(events(s.hold('right')),[]);
  s.hold('open');
  assert.equal(downs(s.hold('right')).length,1);
});

test('a brief unknown shape cannot repeat a right click without a confirmed release', () => {
  const s = session(); s.hold('move'); s.hold('right');
  s.feed('open');
  assert.deepEqual(events(s.hold('right')),[]);
});

test('reset and disable release any held button; reenabling a held pinch is blocked', () => {
  for (const reset of [s => s.controller.reset(), s => s.feed('left',{}, {enabled:false})]) {
    const s = session(); s.hold('move'); s.hold('left');
    assert.deepEqual(reset(s).events,[{type:'release'}]);
    assert.deepEqual(events(s.hold('left')),[]);
  }
});

test('stale or duplicate timestamps cannot count toward activation or replay input', () => {
  const s = session(); s.hold('move');
  const common = {landmarks:hand('left'),enabled:true,width:1000,height:1000};
  for (const now of [180,150,180]) {
    const result = s.controller.update({...common,now});
    assert.equal(result.phase,'stale');
    assert.deepEqual(result.events,[]);
  }
  assert.equal(downs(s.hold('left')).length,1);
});

test('long gaps and handedness changes release dragging and discard the movement anchor', () => {
  for (const change of [{now:2000},{handedness:'Left'}]) {
    const s = session(); s.hold('move'); s.hold('left');
    const result = s.feed('left',{x:.05},change);
    assert.equal(result.phase,'tracking-reset');
    assert.ok(result.events.some(e=>e.type==='release'));
    assert.equal(result.events.some(e=>e.type==='move'),false);
  }
});

test('reacquired pointer starts from a fresh anchor instead of moving across the whole screen', () => {
  const s = session(); s.hold('move');
  s.feed('move',{}, {landmarks:null});
  assert.deepEqual(events(s.hold('move',{x:.12})),[]);
  const moved = s.feed('move',{x:.13});
  assert.ok(moved.events.every(e => e.type !== 'move' || Math.abs(e.dx) < 30));
});

test('a sudden large jump interrupts a drag instead of sending a teleport', () => {
  const s = session(); s.hold('move'); s.hold('left');
  const result = s.feed('left',{x:.18});
  assert.equal(result.phase,'tracking-reset');
  assert.ok(result.events.some(e=>e.type==='release'));
  assert.equal(result.events.some(e=>e.type==='move'),false);
});

test('slow subpixel movements accumulate while symmetric stationary jitter cancels', () => {
  const s = session(); s.hold('move');
  const slow = Array.from({length:20},(_,i)=>s.feed('move',{x:(i+1)*.0001}));
  assert.ok(events(slow).filter(e=>e.type==='move').reduce((total,e)=>total+e.dx,0) <= -3);
  const t = session(); t.hold('move');
  const jitter = Array.from({length:30},(_,i)=>t.feed('move',{x:(i%2 ? 1 : -1)*.00005}));
  assert.deepEqual(events(jitter),[]);
});

test('movement cap drops excess backlog instead of continuing to move after the hand stops', () => {
  const s = session({sensitivity:6000}); s.hold('move');
  const jump = s.feed('move',{x:.10});
  assert.ok(jump.events.every(e=>e.type!=='move'||Math.abs(e.dx)<=100));
  assert.deepEqual(events(s.hold('move',{x:.10})),[]);
});

test('mirror, scale and in-plane rotation preserve pose interpretation', () => {
  for (const transform of [{mirrored:true},{scale:.6},{rotate:.6},{mirrored:true,rotate:-.5}]) {
    const s = session();
    assert.equal(s.hold('move',transform).at(-1).mode,'move');
    assert.equal(s.hold('scroll',transform).at(-1).mode,'scroll');
    assert.equal(downs(s.hold('left',transform)).length,1);
  }
});

test('aspect correction gives the same pose at different input aspect ratios', () => {
  const s = session();
  for (let i=0;i<4;i++) s.feed('move',{aspect:1.3},{width:1300,height:1000});
  assert.equal(s.feed('move',{aspect:1.3},{width:1300,height:1000}).mode,'move');
});

test('three extended fingers are enough for the relaxed move pose; thumb position is not a requirement', () => {
  const s = session();
  const relaxed = hand('move'), folded = hand('fist');
  for (const i of [5,6,7,8]) relaxed[i] = folded[i];
  for (let i=0;i<4;i++) s.feed('move',{}, {landmarks:relaxed});
  assert.equal(s.feed('move',{}, {landmarks:relaxed}).mode,'move');
});

test('stored official-photo landmarks distinguish the open-palm move pose from old video gestures', () => {
  const reference = JSON.parse(readFileSync(new URL('./fixtures/personalization-reference.json',import.meta.url),'utf8'));
  for (const observation of reference.observations) {
    for (const row of observation.rows) {
      const s = session(); s.hold('move');
      let state;
      for (let i=0;i<5;i++) {
        state = s.feed('move',{}, {landmarks:row.landmarks,width:observation.width,height:observation.height});
        assert.equal(state.events.some(e=>e.type==='button'||e.type==='scroll'),false,observation.pose);
      }
      assert.equal(state.mode,observation.pose==='Open_Palm'?'move':'idle',observation.pose);
    }
  }
});

test('non-finite, incomplete, out-of-frame and degenerate landmarks cannot create inputs', () => {
  const bad = hand(); bad[8].x = NaN;
  const outside = hand(); outside[4].y = -0.1;
  for (const landmarks of [[],bad,outside,hand().slice(0,20),Array(21).fill({x:.5,y:.5,z:0})]) {
    const s = session(); s.hold('move');
    assert.equal(s.feed('left',{}, {landmarks}).phase,'invalid');
    assert.deepEqual(events(s.hold('left')),[]);
  }
  assert.throws(()=>new AirMouseController({sensitivity:Infinity}),RangeError);
});
