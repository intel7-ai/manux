import test from 'node:test';
import assert from 'node:assert/strict';
import {readProfiles, writeProfiles} from '../src/profile-store.js';
import {POSES, FEATURE_COUNT, trainEnvironment} from '../src/personalization.js';

const KEY = 'manux.profiles.v1';
const profile = (id = 'user-1') => ({version: 1, id, name: '테스트 사용자', environments: {}});
function memoryStorage(initial = null) {
  const entries = new Map(initial === null ? [] : [[KEY, initial]]);
  return {
    getItem: key => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, value),
    entries
  };
}

test('empty storage and empty profile lists are supported during onboarding', () => {
  const storage = memoryStorage();
  assert.deepEqual(readProfiles(storage), {profiles: [], error: null});
  writeProfiles([], storage);
  assert.equal(storage.entries.get(KEY), '[]');
  writeProfiles([profile()], storage);
  assert.deepEqual(readProfiles(storage), {profiles: [profile()], error: null});
});

test('verified per-environment models and metadata survive JSON storage', () => {
  const samples = Object.fromEntries(POSES.map((pose, index) => [pose,
    Array.from({length: 8}, () => Array(FEATURE_COUNT).fill(.1 + index * .18))]));
  const p = profile();
  p.environments.desk = {samples, model: trainEnvironment(samples), verified: true,
    verifiedAt: 123, verification: {passes: 5}, resolution: {width: 1920, height: 1080}};
  const storage = memoryStorage();
  writeProfiles([p], storage);
  assert.deepEqual(readProfiles(storage), {profiles: [p], error: null});
});

test('corrupted JSON or invalid profiles fail closed without deleting saved data', () => {
  for (const raw of ['{', 'null', '{}', '[null]', JSON.stringify([{...profile(), version: 2}])]) {
    const storage = memoryStorage(raw);
    const result = readProfiles(storage);
    assert.deepEqual(result.profiles, []);
    assert.match(result.error, /불러오지 못했습니다/);
    assert.equal(storage.entries.get(KEY), raw);
  }
});

test('storage access failure is reported instead of breaking startup', () => {
  assert.match(readProfiles({getItem() {throw new Error('access denied');}}).error, /access denied/);
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', {configurable: true, get() {throw new Error('blocked storage');}});
  try {assert.match(readProfiles().error, /blocked storage/);}
  finally {
    if (original) Object.defineProperty(globalThis, 'localStorage', original);
    else delete globalThis.localStorage;
  }
});

test('quota failures propagate to the UI without reporting a successful save', () => {
  assert.throws(() => writeProfiles([profile()], {setItem() {throw new Error('quota exceeded');}}), /quota exceeded/);
});

test('invalid or sparse profile arrays never reach storage', () => {
  const storage = memoryStorage('previous value');
  for (const value of [{}, [null], Array(2), [{...profile(), id: ''}]]) {
    assert.throws(() => writeProfiles(value, storage), /올바르지 않습니다/);
    assert.equal(storage.entries.get(KEY), 'previous value');
  }
});

test('profile count and serialized size are bounded on reading and writing', () => {
  const tooMany = Array.from({length: 13}, (_, i) => profile(`user-${i}`));
  const storage = memoryStorage();
  assert.throws(() => writeProfiles(tooMany, storage));
  assert.ok(readProfiles(memoryStorage(JSON.stringify(tooMany))).error);
  const huge = {...profile(), metadata: 'x'.repeat(4000001)};
  assert.throws(() => writeProfiles([huge], storage), /용량/);
  assert.match(readProfiles(memoryStorage(' '.repeat(4000001))).error, /너무 큽니다/);
  assert.equal(storage.entries.size, 0);
});

test('cyclic extra metadata fails before replacing a valid stored profile', () => {
  const storage = memoryStorage();
  writeProfiles([profile()], storage);
  const p = profile(); p.metadata = p;
  assert.throws(() => writeProfiles([p], storage), /circular/i);
  assert.deepEqual(readProfiles(storage).profiles, [profile()]);
});
