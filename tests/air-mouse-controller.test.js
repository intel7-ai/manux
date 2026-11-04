import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {AirMouseController} from '../src/air-mouse-controller.js';

import {hand} from './helpers/mouse-hand.js';
import {MouseWorkspace} from '../src/mouse-workspace.js';

// Minimal DOM and desktop-bridge doubles for MouseWorkspace. They record calls; no OS input is possible.
function element(selector, values) {
  const classes = new Set();
  return {
    selector, textContent:'', hidden:false, disabled:false, checked:false, dataset:{}, style:{},
    value:values[selector] ?? '', clientWidth:400, clientHeight:240, offsetWidth:40, offsetHeight:40,
    offsetLeft:0, offsetTop:0, scrollTop:0,
    classList:{add:name => classes.add(name), remove:name => classes.delete(name),
      toggle:(name, on) => { (on ?? !classes.has(name)) ? classes.add(name) : classes.delete(name); }},
    addEventListener() {},
    getBoundingClientRect:() => ({left:0, top:0, right:400, bottom:240}),
    click() {},
  };
}
function workspaceRoot({target = 'practice'} = {}) {
  const values = {'#mouse-target':target, '#mouse-sensitivity':'1800'};
  const cache = new Map();
  return {
    querySelector(selector) {
      if (!cache.has(selector)) cache.set(selector, element(selector, values));
      return cache.get(selector);
    },
    querySelectorAll:() => [],
  };
}
function fakeBridge() {
  const calls = {send:[], stop:0, setEnabled:[]};
  return {
    calls,
    onStatus() { return () => {}; },
    getStatus:async () => ({available:true, enabled:false}),
    setEnabled:async value => { calls.setEnabled.push(value); return {available:true, enabled:value}; },
    send:events => { calls.send.push(events); return true; },
    stop:() => { calls.stop++; },
  };
}

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

test('a non-finite timestamp releases a held button and the next valid frame must settle again', () => {
  for (const bad of [NaN, Infinity, undefined]) {
    const s = session(); s.hold('move'); s.hold('left');
    const result = s.controller.update({landmarks:hand('left'), now:bad, enabled:true, width:1000, height:1000});
    assert.equal(result.phase, 'invalid');
    assert.deepEqual(result.events, [{type:'button',button:'left',down:false},{type:'release'}]);
    assert.deepEqual(events(s.hold('left')), []);
    assert.equal(s.feed('move').phase, 'holding');
  }
});

test('after an explicit pause the same hand must dwell again and cannot move or click on re-enable', () => {
  for (const pose of ['move', 'left']) {
    const s = session(); s.hold('move'); s.hold(pose);
    assert.deepEqual(s.feed(pose, {}, {enabled:false}).events, [{type:'release'}], pose);
    assert.equal(s.feed(pose, {}, {enabled:false}).phase, 'disabled', pose);
    const first = s.feed(pose, {x:.1});
    assert.equal(first.phase, pose === 'move' ? 'holding' : 'release-required', pose);
    assert.deepEqual(first.events, [], pose);
    const rest = events(s.hold(pose, {x:.1}));
    assert.equal(rest.some(e => e.type === 'move' || (e.type === 'button' && e.down)), false, pose);
  }
});

function enabledWorkspace(t, target = 'desktop') {
  const bridge = fakeBridge();
  const workspace = new MouseWorkspace({root:workspaceRoot({target}), bridge});
  t.after(() => clearInterval(workspace.watchdog));
  Object.assign(workspace, {active:true, ready:true, enabled:true, available:true});
  workspace.lastFrame = performance.now();
  return {workspace, bridge};
}
const frame = (now, overrides = {}) => ({landmarks:hand('move'), now, quality:true, handedness:'Right', width:1000, height:1000, ...overrides});

test('a repeated or older timestamp is not a fresh inference: it neither feeds the native lease nor the stall watchdog', t => {
  const {workspace, bridge} = enabledWorkspace(t);
  workspace.update(frame(100));
  const sent = bridge.calls.send.length;
  workspace.lastFrame = performance.now() - 600; // Camera is stalling; only a genuinely new frame may renew this.
  const before = workspace.lastFrame;
  for (const now of [100, 90, 100]) assert.equal(workspace.update(frame(now)).phase, 'stale');
  assert.equal(bridge.calls.send.length, sent);
  assert.equal(workspace.lastFrame, before);
  workspace.update(frame(160));
  assert.equal(bridge.calls.send.length, sent + 1);
  assert.ok(workspace.lastFrame > before);
});

test('the stall watchdog turns control off and releases practice buttons and the native lease', t => {
  t.mock.timers.enable({apis:['setInterval']});
  const {workspace, bridge} = enabledWorkspace(t, 'practice');
  workspace.pressed = true; workspace.drag = {x:1, y:1};
  workspace.lastFrame = performance.now() - 800;
  t.mock.timers.tick(100);
  assert.equal(workspace.enabled, false);
  assert.equal(workspace.pressed, false);
  assert.equal(workspace.drag, null);
  assert.ok(bridge.calls.stop >= 1);
  assert.equal(workspace.update(frame(500)).phase, 'disabled'); // Never re-enables on its own.
  assert.equal(workspace.enabled, false);
});

test('very late tracking results turn control off before any event is forwarded', t => {
  const {workspace, bridge} = enabledWorkspace(t);
  const state = workspace.update(frame(100, {latency:451}));
  assert.equal(workspace.enabled, false);
  assert.equal(state.phase, 'disabled');
  assert.deepEqual(bridge.calls.send, []);
  assert.ok(bridge.calls.stop >= 1);
});

test('a lost hand during a held right pinch or scroll leaves nothing pressed and the cursor still', () => {
  for (const pose of ['right', 'scroll']) {
    const s = session(); s.hold('move'); s.hold(pose);
    const lost = s.feed(pose, {x:.03}, {landmarks:null});
    assert.equal(lost.phase, 'invalid', pose);
    assert.equal(lost.events.some(e => e.type === 'move' || e.type === 'scroll' || (e.type === 'button' && e.down)), false, pose);
    assert.equal(lost.mode, 'idle', pose);
  }
});

// Two-hand PoC (SCRUM-21). Constructed geometry exercises state transitions only; it is not a tracker accuracy or ergonomics result.
function pair(options = {}) {
  const controller = new AirMouseController({smoothing:1, twoHand:true, ...options});
  let now = -60;
  const feed = (primaryPose = 'move', auxPose = 'move', {primary = {x:.2}, aux = {x:-.2}, step = 60, hands = null, at = null} = {}) => {
    now = at ?? now + step;
    return controller.update({
      hands:hands ?? [{landmarks:hand(primaryPose, primary), handedness:'Right'}, {landmarks:hand(auxPose, aux), handedness:'Left'}],
      now, enabled:true, width:1000, height:1000,
    });
  };
  const hold = (...args) => [feed(...args), feed(...args), feed(...args), feed(...args)];
  // Auxiliary dwell, then the existing primary-hand movement dwell.
  const ready = () => { hold(); return hold(); };
  return {controller, feed, hold, ready};
}
const released = states => events(states).some(e => e.type === 'button' && e.button === 'left' && !e.down);

test('two-hand mode is off by default, validated, and leaves single-hand output unchanged', () => {
  const controller = new AirMouseController({smoothing:1});
  assert.equal(controller.twoHand, false);
  const result = controller.update({hands:[], landmarks:hand('move'), now:0, enabled:true, width:1000, height:1000});
  assert.deepEqual(Object.keys(result), ['mode', 'phase', 'progress', 'events']);
  assert.equal(result.phase, 'holding');
  assert.throws(() => new AirMouseController({twoHand:'yes'}), RangeError);
  assert.throws(() => new AirMouseController({twoHand:true, primaryHand:'Both'}), RangeError);
  assert.equal(new AirMouseController({twoHand:true}).update({now:0, enabled:true}).auxActive, false);
});

test('primary hand cannot move until the auxiliary open palm is held, and the result reports it', () => {
  const s = pair();
  const first = s.feed();
  assert.equal(first.phase, 'holding-aux');
  assert.equal(first.auxActive, false);
  assert.deepEqual(first.events, []);
  const rest = [s.feed(), s.feed(), s.feed()];
  assert.equal(rest.at(-1).auxActive, true);
  assert.equal(events([first, ...rest]).length, 0);
  s.hold();
  const moved = s.feed('move', 'move', {primary:{x:.22, y:.01}});
  assert.equal(moved.auxActive, true);
  assert.ok(moved.events.some(e => e.type === 'move' && e.dx < 0 && e.dy > 0));
});

test('an auxiliary hand that is not an open palm never enables the primary hand', () => {
  for (const pose of ['index', 'left', 'right', 'scroll']) {
    const s = pair();
    const states = [...s.hold('move', pose), ...s.hold('move', pose), s.feed('move', pose, {primary:{x:.25}})];
    assert.equal(states.every(state => state.auxActive === false), true, pose);
    assert.equal(states.at(-1).phase, 'aux-required', pose);
    assert.equal(events(states).length, 0, pose);
  }
});

test('releasing the auxiliary pose stops movement at once, releases the button, and requires holding again', () => {
  const s = pair(); s.ready();
  const entry = s.hold('left', 'move');
  assert.equal(downs(entry).length, 1);
  const release = s.feed('left', 'index');
  assert.equal(release.phase, 'aux-released');
  assert.equal(release.auxActive, false);
  assert.equal(release.mode, 'idle');
  assert.ok(released([release]));
  assert.deepEqual(release.events.at(-1), {type:'release'});
  const again = s.feed('move', 'move');
  assert.equal(again.phase, 'holding-aux');
  assert.equal(events([again]).length, 0);
  assert.equal(events(s.hold('move', 'move')).some(e => e.type === 'move' || e.type === 'button'), false);
});

test('losing, degrading, or invalidating either hand interrupts, releases, and requires reactivation', () => {
  const cases = {
    'primary lost':s => s.feed('left', 'move', {hands:[{landmarks:hand('move', {x:-.2}), handedness:'Left'}]}),
    'auxiliary lost':s => s.feed('left', 'move', {hands:[{landmarks:hand('left', {x:.2}), handedness:'Right'}]}),
    'no hands':s => s.feed('left', 'move', {hands:[]}),
    'primary quality':s => s.feed('left', 'move', {hands:[{landmarks:hand('left', {x:.2}), handedness:'Right', quality:false},
      {landmarks:hand('move', {x:-.2}), handedness:'Left'}]}),
    'auxiliary landmarks':s => s.feed('left', 'move', {hands:[{landmarks:hand('left', {x:.2}), handedness:'Right'},
      {landmarks:null, handedness:'Left'}]}),
  };
  for (const [name, lose] of Object.entries(cases)) {
    const s = pair(); s.ready(); s.hold('left', 'move');
    const result = lose(s);
    assert.equal(result.phase, 'invalid', name);
    assert.equal(result.auxActive, false, name);
    assert.ok(released([result]), name);
    const back = s.feed('left', 'move');
    assert.equal(back.phase, 'holding-aux', name);
    assert.equal(downs([back]).length, 0, name);
  }
});

test('crossed or overlapping hands interrupt and do not resume while still too close', () => {
  const s = pair(); s.ready(); s.hold('left', 'move');
  const close = s.feed('left', 'move', {primary:{x:.05}, aux:{x:-.05}});
  assert.equal(close.phase, 'hands-crossed');
  assert.equal(close.auxActive, false);
  assert.ok(released([close]));
  const still = [s.feed('move', 'move', {primary:{x:.05}, aux:{x:-.05}}), s.feed('move', 'move', {primary:{x:.05}, aux:{x:-.05}})];
  assert.equal(still.every(state => state.phase === 'hands-crossed'), true);
  assert.equal(events(still).length, 0);
  assert.equal(s.feed('move', 'move').phase, 'holding-aux');
});

test('swapped roles, duplicate labels, and extra hands are treated as role confusion', () => {
  const swapped = pair(); swapped.ready(); swapped.hold('left', 'move');
  const crossedOver = swapped.feed('left', 'move', {primary:{x:-.2}, aux:{x:.2}});
  assert.equal(crossedOver.phase, 'role-confusion');
  assert.ok(released([crossedOver]));

  for (const [name, hands] of Object.entries({
    'both Right':[{landmarks:hand('move', {x:.2}), handedness:'Right'}, {landmarks:hand('move', {x:-.2}), handedness:'Right'}],
    'missing label':[{landmarks:hand('move', {x:.2}), handedness:'Right'}, {landmarks:hand('move', {x:-.2}), handedness:null}],
    'three hands':[{landmarks:hand('move', {x:.2}), handedness:'Right'}, {landmarks:hand('move', {x:-.2}), handedness:'Left'},
      {landmarks:hand('move', {x:0}), handedness:'Left'}],
  })) {
    const s = pair(); s.ready(); s.hold('left', 'move');
    const result = s.feed('left', 'move', {hands});
    assert.equal(result.phase, 'role-confusion', name);
    assert.equal(result.auxActive, false, name);
    assert.ok(released([result]), name);
  }
});

test('the primary label can be Left and then the Right hand is the auxiliary hand', () => {
  const controller = new AirMouseController({smoothing:1, twoHand:true, primaryHand:'Left'});
  let now = -60;
  const feed = () => controller.update({
    hands:[{landmarks:hand('move', {x:-.2}), handedness:'Left'}, {landmarks:hand('move', {x:.2}), handedness:'Right'}],
    now:(now += 60), enabled:true, width:1000, height:1000,
  });
  const states = Array.from({length:8}, feed);
  assert.equal(states.at(-1).auxActive, true);
  assert.equal(states.at(-1).mode, 'move');
});

test('stale frames, long gaps, disabling, and bad timestamps keep two-hand safety', () => {
  const stale = pair(); stale.ready(); stale.hold('left', 'move');
  const repeated = stale.feed('left', 'move', {at:stale.controller.lastTime});
  assert.equal(repeated.phase, 'stale');
  assert.deepEqual(repeated.events, []);
  assert.equal(repeated.auxActive, true);
  assert.equal(stale.feed('left', 'move').auxActive, true);

  const gap = pair(); gap.ready(); gap.hold('left', 'move');
  const late = gap.feed('left', 'move', {step:400});
  assert.equal(late.phase, 'tracking-reset');
  assert.equal(late.auxActive, false);
  assert.ok(released([late]));

  const off = pair(); off.ready(); off.hold('left', 'move');
  const disabled = off.controller.update({hands:[], now:1e6, enabled:false});
  assert.deepEqual(disabled.events, [{type:'release'}]);
  assert.equal(disabled.auxActive, false);
  assert.equal(off.controller.update({hands:[], now:1e6 + 1, enabled:false}).phase, 'disabled');
  assert.equal(off.feed('move', 'move').phase, 'holding-aux');

  const bad = pair(); bad.ready(); bad.hold('left', 'move');
  const invalid = bad.controller.update({hands:[], now:Number.NaN, enabled:true});
  assert.equal(invalid.phase, 'invalid');
  assert.ok(released([invalid]));
});

test('legacy single-hand fields never move the cursor while two-hand mode is on', () => {
  const controller = new AirMouseController({smoothing:1, twoHand:true});
  for (let now = 0; now < 600; now += 60) {
    const result = controller.update({landmarks:hand('move'), now, enabled:true, width:1000, height:1000, handedness:'Right'});
    assert.equal(result.phase, 'invalid');
    assert.deepEqual(result.events, []);
  }
});
