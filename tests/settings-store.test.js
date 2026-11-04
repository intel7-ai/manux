import test from 'node:test';
import assert from 'node:assert/strict';
import {SettingsStore, readSettings, defaultSettings, validateSettings, SETTINGS_KEY, SETTINGS_BACKUP_KEY, SETTINGS_VERSION} from '../src/settings-store.js';

// Stand-in for G1's validateRegistrations: key must match gestureId and the model version must be 3.
const validateRegistrations = registrations => {
  const errors = Object.entries(registrations)
    .filter(([key, value]) => value?.gestureId !== key || value?.modelVersion !== 3)
    .map(([key]) => `${key}: invalid registration`);
  return {valid: errors.length === 0, errors};
};
const registration = (gestureId, marker = 1) => ({gestureId, modelVersion: 3, model: {views: {front: [marker]}}});
const verified = {verifiedAt: '2026-11-03T01:00:00.000Z', sessionId: 'enroll-1'};
const actions = ['mouse.left-click', 'mouse.right-click'];
let tick = 0;
const now = () => new Date(Date.UTC(2026, 10, 3, 0, 0, tick++));

function memoryStorage(initial = {}) {
  const entries = new Map(Object.entries(initial));
  return {
    getItem: key => entries.get(key) ?? null,
    setItem: (key, value) => { entries.set(key, String(value)); },
    removeItem: key => { entries.delete(key); },
    entries,
  };
}
const open = (storage, options = {}) => new SettingsStore({storage, validateRegistrations, allowedActions: actions, now, ...options});

test('empty storage starts on defaults and a mouse change survives an app restart', () => {
  const storage = memoryStorage();
  const store = open(storage);
  assert.equal(store.status, 'empty');
  assert.deepEqual(store.settings, defaultSettings());
  store.setMouse({sensitivity: 2400, mainHand: 'left', area: {left: 0.1, right: 0.9}});
  const restarted = open(storage);
  assert.equal(restarted.status, 'loaded');
  assert.deepEqual(restarted.settings.mouse, {sensitivity: 2400, mainHand: 'left', area: {left: 0.1, top: 0, right: 0.9, bottom: 1},
    pointer: {engine: 'index', click: 'trigger', anchor: 'index'}});
  assert.equal(restarted.settings.revision, 1);
});

test('one registered gesture bound to left click is restored after restart', () => {
  const storage = memoryStorage();
  const store = open(storage);
  store.putGesture({registration: registration('custom:ok-sign'), ...verified});
  store.setBinding({gestureId: 'custom:ok-sign', actionId: 'mouse.left-click'});
  const desk = open(storage).settings.environments.desk;
  assert.deepEqual(desk.registrations['custom:ok-sign'], registration('custom:ok-sign'));
  assert.deepEqual(desk.verifications['custom:ok-sign'], verified);
  assert.deepEqual(desk.bindings['custom:ok-sign'], {gestureId: 'custom:ok-sign', actionId: 'mouse.left-click', enabled: true});
});

test('re-registering one gesture keeps its binding and every other gesture', () => {
  const store = open(memoryStorage());
  store.putGesture({registration: registration('custom:a'), ...verified});
  store.putGesture({registration: registration('builtin.thumb-up'), ...verified});
  store.setBinding({gestureId: 'custom:a', actionId: 'mouse.right-click'});
  store.putGesture({registration: registration('custom:a', 2), verifiedAt: '2026-11-03T02:00:00.000Z', sessionId: 'enroll-2'});
  const desk = store.settings.environments.desk;
  assert.deepEqual(desk.registrations['custom:a'], registration('custom:a', 2));
  assert.deepEqual(desk.registrations['builtin.thumb-up'], registration('builtin.thumb-up'));
  assert.equal(desk.bindings['custom:a'].actionId, 'mouse.right-click');
  assert.equal(desk.verifications['custom:a'].sessionId, 'enroll-2');
});

test('removing a gesture removes its verification and binding only', () => {
  const store = open(memoryStorage());
  for (const id of ['custom:a', 'custom:b']) {
    store.putGesture({registration: registration(id), ...verified});
    store.setBinding({gestureId: id, actionId: 'mouse.left-click'});
  }
  store.removeGesture('custom:a');
  const desk = store.settings.environments.desk;
  assert.deepEqual(Object.keys(desk.registrations), ['custom:b']);
  assert.deepEqual(Object.keys(desk.verifications), ['custom:b']);
  assert.deepEqual(Object.keys(desk.bindings), ['custom:b']);
});

test('a failed write leaves memory and storage exactly as they were', () => {
  const storage = memoryStorage();
  const store = open(storage);
  store.setMouse({sensitivity: 2000});
  const before = {settings: store.settings, raw: storage.entries.get(SETTINGS_KEY)};
  storage.setItem = () => { throw new Error('QuotaExceededError'); };
  assert.throws(() => store.setMouse({sensitivity: 3000}), /기존 설정은 그대로입니다/);
  assert.deepEqual(store.settings, before.settings);
  assert.equal(storage.entries.get(SETTINGS_KEY), before.raw);
});

test('a write that does not read back is rolled back', () => {
  const storage = memoryStorage();
  const store = open(storage);
  store.setMouse({sensitivity: 2000});
  const raw = storage.entries.get(SETTINGS_KEY);
  const setItem = storage.setItem;
  let calls = 0;
  storage.setItem = (key, value) => setItem(key, ++calls === 1 ? `${value} ` : value);
  assert.throws(() => store.setMouse({sensitivity: 3000}), /확인하지 못했습니다/);
  assert.equal(storage.entries.get(SETTINGS_KEY), raw);
  assert.equal(store.settings.mouse.sensitivity, 2000);
});

test('invalid changes are rejected before anything is written', () => {
  const storage = memoryStorage();
  const store = open(storage);
  for (const patch of [{sensitivity: 650}, {sensitivity: 9000}, {mainHand: 'both'}, {area: {left: 0.5, right: 0.6}}, {speed: 1}]) {
    assert.throws(() => store.setMouse(patch), /올바르지 않습니다/, JSON.stringify(patch));
  }
  assert.throws(() => store.putGesture({registration: {gestureId: 'custom:a', modelVersion: 2}, ...verified}), /올바르지 않습니다/);
  assert.throws(() => store.putGesture({registration: registration('custom:a'), verifiedAt: 'yesterday', sessionId: 'x'}), /올바르지 않습니다/);
  assert.throws(() => store.setBinding({gestureId: 'custom:missing', actionId: 'mouse.left-click'}), /등록되지 않은 동작/);
  store.putGesture({registration: registration('custom:a'), ...verified});
  assert.throws(() => store.setBinding({gestureId: 'custom:a', actionId: 'shell.run'}), /허용되지 않은 액션/);
  assert.equal(store.settings.revision, 1);
  assert.equal(JSON.parse(storage.entries.get(SETTINGS_KEY)).revision, 1);
});

test('without a registration validator no gesture model can be stored', () => {
  const store = new SettingsStore({storage: memoryStorage(), allowedActions: actions});
  assert.throws(() => store.putGesture({registration: registration('custom:a'), ...verified}), /검증기가 연결되지 않았습니다/);
  assert.doesNotThrow(() => store.setMouse({sensitivity: 2000}));
});

test('corrupt data runs on defaults, is never deleted, and is backed up before the first save', () => {
  for (const raw of ['{', 'null', '[]', JSON.stringify({...defaultSettings(), mouse: {sensitivity: 1}}),
    JSON.stringify({...defaultSettings(), environments: {desk: {registrations: {'custom:a': registration('custom:a')}, verifications: {}, bindings: {}}}})]) {
    const storage = memoryStorage({[SETTINGS_KEY]: raw});
    const store = open(storage);
    assert.equal(store.status, 'corrupt', raw);
    assert.match(store.error, /원본은 지우지 않았습니다/);
    assert.deepEqual(store.settings, defaultSettings());
    assert.equal(storage.entries.get(SETTINGS_KEY), raw);
    store.setMouse({sensitivity: 2000});
    assert.equal(storage.entries.get(SETTINGS_BACKUP_KEY), raw);
    assert.equal(open(storage).settings.mouse.sensitivity, 2000);
  }
});

test('settings from a newer app version are read-only', () => {
  const raw = JSON.stringify({...defaultSettings(), version: SETTINGS_VERSION + 1});
  const storage = memoryStorage({[SETTINGS_KEY]: raw});
  const store = open(storage);
  assert.equal(store.status, 'newer');
  assert.throws(() => store.setMouse({sensitivity: 2000}), /새 버전/);
  assert.equal(storage.entries.get(SETTINGS_KEY), raw);
});

test('a save from another window is not overwritten', () => {
  const storage = memoryStorage();
  const first = open(storage), second = open(storage);
  second.setMouse({sensitivity: 3000});
  assert.throws(() => first.setMouse({sensitivity: 1000}), /다른 창/);
  assert.equal(open(storage).settings.mouse.sensitivity, 3000);
});

test('unavailable storage opens read-only on defaults', () => {
  const storage = {getItem: () => { throw new Error('denied'); }, setItem: () => {}};
  const store = open(storage);
  assert.equal(store.status, 'unavailable');
  assert.deepEqual(store.settings, defaultSettings());
  assert.throws(() => store.setMouse({sensitivity: 2000}), /사용할 수 없습니다/);
});

test('stored bindings to an action this build does not know still load', () => {
  const settings = defaultSettings();
  settings.environments.desk = {registrations: {'custom:a': registration('custom:a')}, verifications: {'custom:a': verified},
    bindings: {'custom:a': {gestureId: 'custom:a', actionId: 'media.future-action', enabled: true}}};
  const {status, settings: loaded} = readSettings({storage: memoryStorage({[SETTINGS_KEY]: JSON.stringify(settings)}), validateRegistrations});
  assert.equal(status, 'loaded');
  assert.equal(loaded.environments.desk.bindings['custom:a'].actionId, 'media.future-action');
  assert.equal(validateSettings(settings, {validateRegistrations}).valid, true);
});

test('settings handed out are copies, so callers cannot change committed state', () => {
  const store = open(memoryStorage());
  const copy = store.settings;
  copy.mouse.sensitivity = 100;
  assert.equal(store.settings.mouse.sensitivity, 1800);
});

test('v1 records migrate to v2: an explicit left hand is kept, the old default right means not chosen', () => {
  for (const [stored, expected] of [['right', null], ['left', 'left']]) {
    const {pointer: _pointer, ...mouseV1} = defaultSettings().mouse;
    const v1 = {...defaultSettings(), version: 1, mouse: {...mouseV1, mainHand: stored}};
    const storage = memoryStorage();
    storage.setItem(SETTINGS_KEY, JSON.stringify(v1));
    const store = open(storage);
    assert.equal(store.status, 'loaded');
    assert.equal(store.settings.version, SETTINGS_VERSION);
    assert.equal(store.settings.mouse.mainHand, expected);
    store.setMouse({mainHand: 'right'});
    assert.equal(JSON.parse(storage.getItem(SETTINGS_KEY)).version, 2, 'the first save writes v2');
  }
});

test('the hand starts as not chosen and accepts only right, left or null', () => {
  const store = open(memoryStorage());
  assert.equal(store.settings.mouse.mainHand, null);
  store.setMouse({mainHand: 'left'});
  assert.equal(store.settings.mouse.mainHand, 'left');
  store.setMouse({mainHand: null});
  assert.equal(store.settings.mouse.mainHand, null);
  assert.throws(() => store.setMouse({mainHand: 'both'}), /올바르지 않습니다/);
});

test('pointer settings default to the index trigger, persist, and reject unknown choices', () => {
  const storage = memoryStorage();
  const store = open(storage);
  assert.deepEqual(store.settings.mouse.pointer, {engine: 'index', click: 'trigger', anchor: 'index'});
  store.setMouse({pointer: {click: 'off'}});
  assert.deepEqual(open(storage).settings.mouse.pointer, {engine: 'index', click: 'off', anchor: 'index'});
  for (const pointer of [{click: 'pinch'}, {engine: 'both'}, {anchor: 'wrist'}]) assert.throws(() => store.setMouse({pointer}), /포인터 설정/);
  const v1 = {...defaultSettings(), version: 1, mouse: {sensitivity: 1800, mainHand: 'right', area: defaultSettings().mouse.area}};
  const old = memoryStorage(); old.setItem(SETTINGS_KEY, JSON.stringify(v1));
  assert.deepEqual(open(old).settings.mouse.pointer, {engine: 'index', click: 'trigger', anchor: 'index'});
});
