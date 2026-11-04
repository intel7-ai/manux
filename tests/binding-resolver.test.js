import test from 'node:test';
import assert from 'node:assert/strict';
import {resolveBinding, eventsForAction, findSharedActions, MOUSE_ACTIONS, ALLOWED_ACTION_IDS, BINDING_REASONS} from '../src/binding-resolver.js';
import {validGestureId} from '../src/personalization.js';
import {SettingsStore} from '../src/settings-store.js';

// Synthetic decisions in the shape classifyRegisteredGesture returns. No camera, no model.
const match = gestureId => ({gestureId, accepted: true, kind: 'match', reason: 'matched', distance: 0.01, margin: 0.2, view: 'front'});
const unknown = reason => ({gestureId: null, accepted: false, kind: 'unknown', reason, distance: 0.5, margin: null});
const invalid = reason => ({gestureId: null, accepted: false, kind: 'invalid', reason, distance: null, margin: null});
const binding = (gestureId, actionId, enabled = true) => ({gestureId, actionId, enabled});
const allowed = ['mouse.left-click', 'mouse.right-click'];
const wave = 'custom:wave';
function memoryStorage() {
  const entries = new Map();
  return {getItem: key => entries.get(key) ?? null, setItem: (key, value) => { entries.set(key, String(value)); }, removeItem: key => { entries.delete(key); }};
}
const bindings = {[wave]: binding(wave, 'mouse.left-click')};

test('an accepted gesture with an enabled, allowed binding resolves to that action id', () => {
  assert.deepEqual(resolveBinding({decision: match(wave), bindings, allowedActions: allowed}), {actionId: 'mouse.left-click', reason: 'bound'});
  assert.deepEqual(resolveBinding({decision: match(wave), bindings, allowedActions: new Set(allowed)}), {actionId: 'mouse.left-click', reason: 'bound'});
});

test('not-accepted decisions never produce a command and are not replaced by another path', () => {
  for (const decision of [unknown('outside-enrollment'), unknown('ambiguous-gesture'), unknown('no-registrations'), invalid('invalid-features'), invalid('invalid-model')]) {
    assert.deepEqual(resolveBinding({decision, bindings, allowedActions: allowed}), {actionId: null, reason: 'not-accepted'}, decision.reason);
  }
  // A forged or inconsistent decision is not accepted either: accepted flag, kind and gesture id must all agree.
  for (const decision of [{...match(wave), accepted: false}, {...match(wave), kind: 'unknown'}, {...match(wave), gestureId: null}, {...match(wave), gestureId: 'wave'}, {...match(wave), gestureId: 'custom:bad id'}, {...match(wave), accepted: 'true'}]) {
    assert.deepEqual(resolveBinding({decision, bindings, allowedActions: allowed}), {actionId: null, reason: 'not-accepted'});
  }
});

test('no binding, disabled binding and unknown action each end as no command with their own reason', () => {
  assert.deepEqual(resolveBinding({decision: match('custom:other'), bindings, allowedActions: allowed}), {actionId: null, reason: 'no-binding'});
  assert.deepEqual(resolveBinding({decision: match(wave), bindings: {}, allowedActions: allowed}), {actionId: null, reason: 'no-binding'});
  assert.deepEqual(resolveBinding({decision: match(wave), bindings: {[wave]: binding(wave, 'mouse.left-click', false)}, allowedActions: allowed}), {actionId: null, reason: 'disabled'});
  // Action not offered by this caller, or not a mouse action at all.
  assert.deepEqual(resolveBinding({decision: match(wave), bindings, allowedActions: ['mouse.right-click']}), {actionId: null, reason: 'unknown-action'});
  assert.deepEqual(resolveBinding({decision: match(wave), bindings: {[wave]: binding(wave, 'system.shutdown')}, allowedActions: ['system.shutdown']}), {actionId: null, reason: 'unknown-action'});
  assert.deepEqual(resolveBinding({decision: match(wave), bindings: {[wave]: binding(wave, 'mouse.left-click')}, allowedActions: []}), {actionId: null, reason: 'unknown-action'});
});

test('a disabled binding wins over an unknown action, so a disabled row never looks like a configuration error', () => {
  assert.deepEqual(resolveBinding({decision: match(wave), bindings: {[wave]: binding(wave, 'system.shutdown', false)}, allowedActions: allowed}), {actionId: null, reason: 'disabled'});
});

test('malformed input never throws and never yields an action', () => {
  const junk = [undefined, null, 0, 1, 'x', true, [], () => {}, {}, Symbol('s'), new Proxy({}, {get() { throw new Error('boom'); }, has() { throw new Error('boom'); }, ownKeys() { throw new Error('boom'); }, getOwnPropertyDescriptor() { throw new Error('boom'); }})];
  assert.deepEqual(resolveBinding(), {actionId: null, reason: 'not-accepted'});
  assert.deepEqual(resolveBinding(null), {actionId: null, reason: 'not-accepted'});
  for (const decision of junk) assert.equal(resolveBinding({decision, bindings, allowedActions: allowed}).actionId, null);
  for (const b of junk) assert.equal(resolveBinding({decision: match(wave), bindings: b, allowedActions: allowed}).actionId, null);
  for (const a of junk) assert.equal(resolveBinding({decision: match(wave), bindings, allowedActions: a}).actionId, null);
  for (const entry of junk) assert.equal(resolveBinding({decision: match(wave), bindings: {[wave]: entry}, allowedActions: allowed}).actionId, null);
  for (const actionId of junk) assert.equal(resolveBinding({decision: match(wave), bindings: {[wave]: {gestureId: wave, actionId, enabled: true}}, allowedActions: allowed}).actionId, null);
  for (const enabled of [undefined, null, 'true', 1, 0, {}]) assert.equal(resolveBinding({decision: match(wave), bindings: {[wave]: {gestureId: wave, actionId: 'mouse.left-click', enabled}}, allowedActions: allowed}).actionId, null);
  const proxied = resolveBinding({decision: junk.at(-1), bindings, allowedActions: allowed});
  assert.equal(BINDING_REASONS.includes(proxied.reason), true);
});

test('every result uses one of the five documented reasons and the action id only appears with bound', () => {
  const cases = [
    resolveBinding({decision: match(wave), bindings, allowedActions: allowed}),
    resolveBinding({decision: unknown('ambiguous-gesture'), bindings, allowedActions: allowed}),
    resolveBinding({decision: match('custom:other'), bindings, allowedActions: allowed}),
    resolveBinding({decision: match(wave), bindings: {[wave]: binding(wave, 'mouse.left-click', false)}, allowedActions: allowed}),
    resolveBinding({decision: match(wave), bindings, allowedActions: []})
  ];
  assert.deepEqual(cases.map(c => c.reason), ['bound', 'not-accepted', 'no-binding', 'disabled', 'unknown-action']);
  assert.deepEqual([...BINDING_REASONS], ['bound', 'not-accepted', 'no-binding', 'disabled', 'unknown-action']);
  for (const c of cases) assert.equal(c.actionId !== null, c.reason === 'bound');
});

test('a binding only applies to its own gesture: gestureId inside the row must equal the key', () => {
  const forged = {[wave]: binding('custom:other', 'mouse.left-click')};
  assert.deepEqual(resolveBinding({decision: match(wave), bindings: forged, allowedActions: allowed}), {actionId: null, reason: 'no-binding'});
});

test('prototype keys cannot be used to smuggle a binding', () => {
  const poisoned = Object.create({[wave]: binding(wave, 'mouse.left-click')});
  assert.deepEqual(resolveBinding({decision: match(wave), bindings: poisoned, allowedActions: allowed}), {actionId: null, reason: 'no-binding'});
  assert.deepEqual(resolveBinding({decision: match(wave), bindings: {}, allowedActions: Object.create({has: () => true})}), {actionId: null, reason: 'no-binding'});
});

test('gesture ids and action ids stay separate: a gesture id is never an action and vice versa', () => {
  assert.deepEqual(resolveBinding({decision: match('custom:rock'), bindings: {'custom:rock': binding('custom:rock', 'custom:rock')}, allowedActions: ['custom:rock']}), {actionId: null, reason: 'unknown-action'});
  assert.deepEqual(resolveBinding({decision: match('builtin.fist'), bindings: {}, allowedActions: allowed}), {actionId: null, reason: 'no-binding'});
  // A gesture named like an action does not execute anything by itself.
  assert.deepEqual(resolveBinding({decision: {...match(wave), gestureId: 'mouse.left-click'}, bindings: {'mouse.left-click': binding('mouse.left-click', 'mouse.left-click')}, allowedActions: allowed}), {actionId: null, reason: 'not-accepted'});
  for (const id of ALLOWED_ACTION_IDS) assert.equal(validGestureId(id), false, id);
});

test('the allowed action set is defined once, frozen, and matches what SettingsStore accepts', () => {
  assert.deepEqual(ALLOWED_ACTION_IDS, ['mouse.left-click', 'mouse.right-click', 'mouse.pause']);
  assert.equal(Object.isFrozen(MOUSE_ACTIONS), true);
  assert.equal(Object.isFrozen(ALLOWED_ACTION_IDS), true);
  assert.throws(() => { 'use strict'; ALLOWED_ACTION_IDS.push('mouse.drag'); });
  const store = new SettingsStore({storage: memoryStorage(), allowedActions: ALLOWED_ACTION_IDS, validateRegistrations: () => ({valid: true, errors: []})});
  store.putGesture({registration: {gestureId: wave, modelVersion: 3, model: {}}, verifiedAt: '2026-11-03T01:00:00.000Z', sessionId: 's-1'});
  for (const actionId of ALLOWED_ACTION_IDS) assert.equal(store.setBinding({gestureId: wave, actionId}).environments.desk.bindings[wave].actionId, actionId);
  assert.throws(() => store.setBinding({gestureId: wave, actionId: 'mouse.drag'}), RangeError);
});

test('the gesture id pattern agrees with personalization.validGestureId', () => {
  const ids = ['custom:wave', 'custom:Wave_1-x', 'custom:', 'custom:-a', 'custom:' + 'a'.repeat(48), 'custom:' + 'a'.repeat(49), 'builtin.fist', 'builtin.open-palm', 'builtin.', 'builtin.Fist', 'mouse.left-click', 'rock', '', 'custom:a b'];
  for (const id of ids) {
    const decision = {...match(wave), gestureId: id};
    const accepted = resolveBinding({decision, bindings: {[id]: binding(id, 'mouse.left-click')}, allowedActions: allowed}).actionId !== null;
    assert.equal(accepted, validGestureId(id), id);
  }
});

test('mouse events for event actions use the format the desktop bridge validates; pause has none; unknown is null', () => {
  assert.deepEqual(eventsForAction('mouse.left-click'), [{type: 'button', button: 'left', down: true}, {type: 'button', button: 'left', down: false}]);
  assert.deepEqual(eventsForAction('mouse.right-click'), [{type: 'button', button: 'right', down: true}, {type: 'button', button: 'right', down: false}]);
  assert.deepEqual(eventsForAction('mouse.pause'), []);
  for (const id of ['mouse.drag', 'custom:wave', '', undefined, null, 7, '__proto__', 'constructor', 'toString']) assert.equal(eventsForAction(id), null, String(id));
  const first = eventsForAction('mouse.left-click'); first.push({type: 'move', dx: 1, dy: 1});
  assert.equal(eventsForAction('mouse.left-click').length, 2, 'callers get a fresh array each time');
  for (const id of ALLOWED_ACTION_IDS) for (const e of eventsForAction(id)) assert.equal(['button'].includes(e.type), true);
});

test('policy: several gestures may share one action and are reported, never rejected; one gesture has exactly one binding', () => {
  const a = 'custom:a', b = 'custom:b', c = 'custom:c';
  const shared = {[a]: binding(a, 'mouse.left-click'), [b]: binding(b, 'mouse.left-click'), [c]: binding(c, 'mouse.right-click')};
  assert.deepEqual(findSharedActions(shared), [{actionId: 'mouse.left-click', gestureIds: [a, b]}]);
  // Sharing never breaks resolving: each gesture resolves on its own, and one frame is one decision.
  assert.equal(resolveBinding({decision: match(a), bindings: shared, allowedActions: allowed}).actionId, 'mouse.left-click');
  assert.equal(resolveBinding({decision: match(b), bindings: shared, allowedActions: allowed}).actionId, 'mouse.left-click');
  assert.equal(resolveBinding({decision: match(c), bindings: shared, allowedActions: allowed}).actionId, 'mouse.right-click');
  // Disabled rows are not counted as shared; junk input reports nothing.
  assert.deepEqual(findSharedActions({[a]: binding(a, 'mouse.left-click'), [b]: binding(b, 'mouse.left-click', false)}), []);
  for (const junk of [undefined, null, 5, 'x', [], {x: null}, {x: 4}]) assert.deepEqual(findSharedActions(junk), []);
  // The settings record holds one binding per gesture id, so a second binding replaces the first instead of adding a second action.
  const store = new SettingsStore({storage: memoryStorage(), allowedActions: ALLOWED_ACTION_IDS, validateRegistrations: () => ({valid: true, errors: []})});
  store.putGesture({registration: {gestureId: a, modelVersion: 3, model: {}}, verifiedAt: '2026-11-03T01:00:00.000Z', sessionId: 's-1'});
  store.setBinding({gestureId: a, actionId: 'mouse.left-click'});
  const next = store.setBinding({gestureId: a, actionId: 'mouse.right-click'});
  assert.deepEqual(Object.keys(next.environments.desk.bindings), [a]);
  assert.equal(next.environments.desk.bindings[a].actionId, 'mouse.right-click');
});

test('resolving does not modify its inputs', () => {
  const decision = Object.freeze(match(wave));
  const b = Object.freeze({[wave]: Object.freeze(binding(wave, 'mouse.left-click'))});
  const list = Object.freeze([...allowed]);
  assert.equal(resolveBinding({decision, bindings: b, allowedActions: list}).actionId, 'mouse.left-click');
  assert.equal(Object.isFrozen(findSharedActions(b)), false);
});
