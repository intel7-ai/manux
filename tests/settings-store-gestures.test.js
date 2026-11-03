import test from 'node:test';
import assert from 'node:assert/strict';
import {SettingsStore, SETTINGS_KEY} from '../src/settings-store.js';
import {FEATURE_COUNT, ENROLLMENT_VIEWS, trainGesture, registerGesture, validateRegistrations, classifyRegisteredGesture} from '../src/personalization.js';

// A2 x G1: real G1 records through the settings store. Synthetic feature vectors, no webcam.
const vector = (label, n = 0) => Array.from({length: FEATURE_COUNT}, (_, i) => 0.10 + label * 0.18 + Math.sin(i + n) * 0.003);
const views = label => Object.fromEntries(ENROLLMENT_VIEWS.map((view, v) => [view, Array.from({length: 12}, (_, n) => vector(label, n + v * 3))]));
const verified = {verifiedAt: '2026-11-03T01:00:00.000Z', sessionId: 'enroll-1'};
function memoryStorage() {
  const entries = new Map();
  return {getItem: key => entries.get(key) ?? null, setItem: (key, value) => { entries.set(key, String(value)); }, removeItem: key => { entries.delete(key); }, entries};
}
const open = storage => new SettingsStore({storage, validateRegistrations, allowedActions: ['mouse.left-click']});

// The U2 save path: train one candidate, re-check it against stored gestures, then store only that gesture.
function enroll(store, gestureId, label) {
  const existing = store.settings.environments.desk.registrations;
  const {candidate, usable, reason} = trainGesture({gestureId, viewSamples: views(label), existingRegistrations: existing});
  assert.equal(usable, true, reason);
  const next = registerGesture(existing, candidate);
  return store.putGesture({registration: next[gestureId], ...verified});
}

test('a real G1 gesture bound to left click is restored after restart and still classifies', () => {
  const storage = memoryStorage();
  const store = open(storage);
  enroll(store, 'custom:ok-sign', 1);
  store.setBinding({gestureId: 'custom:ok-sign', actionId: 'mouse.left-click'});
  const restarted = open(storage);
  assert.equal(restarted.status, 'loaded');
  const desk = restarted.settings.environments.desk;
  assert.equal(desk.bindings['custom:ok-sign'].actionId, 'mouse.left-click');
  const match = classifyRegisteredGesture({features: vector(1, 100), registrations: desk.registrations});
  assert.equal(match.accepted, true);
  assert.equal(match.gestureId, 'custom:ok-sign');
  assert.equal(classifyRegisteredGesture({features: vector(4, 100), registrations: desk.registrations}).accepted, false);
});

test('adding a second real gesture keeps the first one and its binding', () => {
  const store = open(memoryStorage());
  enroll(store, 'custom:a', 1);
  store.setBinding({gestureId: 'custom:a', actionId: 'mouse.left-click'});
  enroll(store, 'custom:b', 3);
  const desk = store.settings.environments.desk;
  assert.deepEqual(Object.keys(desk.registrations).sort(), ['custom:a', 'custom:b']);
  assert.equal(desk.bindings['custom:a'].actionId, 'mouse.left-click');
});

test('a stored G1 record from an older model version is treated as corrupt, not used', () => {
  const storage = memoryStorage();
  enroll(open(storage), 'custom:a', 1);
  const saved = JSON.parse(storage.entries.get(SETTINGS_KEY));
  saved.environments.desk.registrations['custom:a'].modelVersion = 2;
  storage.entries.set(SETTINGS_KEY, JSON.stringify(saved));
  const store = open(storage);
  assert.equal(store.status, 'corrupt');
  assert.deepEqual(store.settings.environments.desk.registrations, {});
});
