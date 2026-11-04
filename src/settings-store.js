// A2 (SCRUM-30): mouse settings, registered gestures and action bindings in one versioned record.
// Every change is a whole-record transaction: validate, write once, read back, then commit in memory.
export const SETTINGS_KEY = 'manux.settings.v1';
export const SETTINGS_BACKUP_KEY = 'manux.settings.v1.backup';
export const SETTINGS_VERSION = 1;
export const ENVIRONMENTS = Object.freeze(['desk']);
export const SENSITIVITY = Object.freeze({min: 600, max: 3600, step: 100, default: 1800});
export const MIN_AREA_SPAN = 0.2;
const MAX_SERIALIZED_LENGTH = 2000000;
const ACTION_ID = /^[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+$/;
const SESSION_ID = /^[A-Za-z0-9_.:-]{1,64}$/;

const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const exactKeys = (value, keys) => isRecord(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const fraction = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
const isoDate = value => typeof value === 'string' && value.length <= 40 && !Number.isNaN(Date.parse(value));
// Fail closed until the app passes G1's validateRegistrations: no model is stored unchecked.
const noValidator = registrations => Object.keys(registrations).length === 0
  ? {valid: true, errors: []} : {valid: false, errors: ['등록 검증기가 연결되지 않았습니다.']};

export function defaultSettings() {
  return {
    version: SETTINGS_VERSION, revision: 0, savedAt: null,
    mouse: {sensitivity: SENSITIVITY.default, mainHand: 'right', area: {left: 0, top: 0, right: 1, bottom: 1}},
    environments: Object.fromEntries(ENVIRONMENTS.map(env => [env, {registrations: {}, verifications: {}, bindings: {}}])),
  };
}

function mouseErrors(mouse) {
  if (!exactKeys(mouse, ['sensitivity', 'mainHand', 'area'])) return ['mouse: 형식이 올바르지 않습니다.'];
  const errors = [];
  const {sensitivity, mainHand, area} = mouse;
  if (!Number.isInteger(sensitivity) || sensitivity < SENSITIVITY.min || sensitivity > SENSITIVITY.max || sensitivity % SENSITIVITY.step !== 0) {
    errors.push(`mouse.sensitivity: ${SENSITIVITY.min}~${SENSITIVITY.max}, ${SENSITIVITY.step} 단위여야 합니다.`);
  }
  if (mainHand !== 'right' && mainHand !== 'left') errors.push('mouse.mainHand: right 또는 left여야 합니다.');
  if (!exactKeys(area, ['left', 'top', 'right', 'bottom']) || ![area.left, area.top, area.right, area.bottom].every(fraction)
    || area.right - area.left < MIN_AREA_SPAN || area.bottom - area.top < MIN_AREA_SPAN) {
    errors.push(`mouse.area: 0~1 비율이며 가로·세로가 ${MIN_AREA_SPAN} 이상이어야 합니다.`);
  }
  return errors;
}

function environmentErrors(name, env, validateRegistrations) {
  if (!exactKeys(env, ['registrations', 'verifications', 'bindings'])) return [`${name}: 형식이 올바르지 않습니다.`];
  const {registrations, verifications, bindings} = env;
  if (!isRecord(registrations) || !isRecord(verifications) || !isRecord(bindings)) return [`${name}: 형식이 올바르지 않습니다.`];
  const errors = [];
  const checked = validateRegistrations(registrations);
  if (!checked?.valid) errors.push(...(checked?.errors ?? ['invalid registrations']).map(error => `${name}.registrations: ${error}`));
  const registered = new Set(Object.keys(registrations));
  // Only verified candidates may be stored, so every registration carries its verification record.
  for (const id of registered) if (!Object.hasOwn(verifications, id)) errors.push(`${name}.verifications: ${id} 확인 기록이 없습니다.`);
  for (const [id, record] of Object.entries(verifications)) {
    if (!registered.has(id)) errors.push(`${name}.verifications: 등록되지 않은 ${id}`);
    else if (!exactKeys(record, ['verifiedAt', 'sessionId']) || !isoDate(record.verifiedAt) || !SESSION_ID.test(record.sessionId)) {
      errors.push(`${name}.verifications: ${id} 형식이 올바르지 않습니다.`);
    }
  }
  for (const [id, binding] of Object.entries(bindings)) {
    if (!registered.has(id)) errors.push(`${name}.bindings: 등록되지 않은 ${id}`);
    else if (!exactKeys(binding, ['gestureId', 'actionId', 'enabled']) || binding.gestureId !== id
      || typeof binding.actionId !== 'string' || !ACTION_ID.test(binding.actionId) || typeof binding.enabled !== 'boolean') {
      errors.push(`${name}.bindings: ${id} 형식이 올바르지 않습니다.`);
    }
  }
  return errors;
}

/** Structure check for a whole settings record. Unknown action IDs pass here and run as no command. */
export function validateSettings(settings, {validateRegistrations = noValidator} = {}) {
  if (!exactKeys(settings, ['version', 'revision', 'savedAt', 'mouse', 'environments'])) return {valid: false, errors: ['설정 형식이 올바르지 않습니다.']};
  const errors = [];
  if (settings.version !== SETTINGS_VERSION) errors.push(`version: ${SETTINGS_VERSION}이어야 합니다.`);
  if (!Number.isInteger(settings.revision) || settings.revision < 0) errors.push('revision: 0 이상의 정수여야 합니다.');
  if (settings.savedAt !== null && !isoDate(settings.savedAt)) errors.push('savedAt: 날짜 형식이 아닙니다.');
  errors.push(...mouseErrors(settings.mouse));
  if (!exactKeys(settings.environments, [...ENVIRONMENTS])) errors.push('environments: 형식이 올바르지 않습니다.');
  else for (const env of ENVIRONMENTS) errors.push(...environmentErrors(env, settings.environments[env], validateRegistrations));
  return {valid: errors.length === 0, errors};
}

/**
 * Reads the stored record. Corrupt data is never deleted here: the app runs on defaults and the
 * first successful save copies the original to SETTINGS_BACKUP_KEY. A record from a newer app
 * version is opened read-only so an older install cannot overwrite it.
 */
export function readSettings({storage, validateRegistrations} = {}) {
  let raw;
  try {
    raw = (storage ?? globalThis.localStorage).getItem(SETTINGS_KEY);
  } catch (error) {
    return {settings: defaultSettings(), status: 'unavailable', readOnly: true, raw: null, error: `설정 저장소를 사용할 수 없습니다. ${error.message}`};
  }
  if (raw === null) return {settings: defaultSettings(), status: 'empty', readOnly: false, raw, error: null};
  try {
    if (raw.length > MAX_SERIALIZED_LENGTH) throw new Error('저장 데이터가 너무 큽니다.');
    const parsed = JSON.parse(raw);
    if (Number.isInteger(parsed?.version) && parsed.version > SETTINGS_VERSION) {
      return {settings: defaultSettings(), status: 'newer', readOnly: true, raw,
        error: '새 버전 MANUX에서 저장한 설정입니다. 이 버전에서는 바꾸지 않고 기본값으로 실행합니다.'};
    }
    const checked = validateSettings(parsed, {validateRegistrations});
    if (!checked.valid) throw new Error(checked.errors.slice(0, 3).join(' '));
    return {settings: parsed, status: 'loaded', readOnly: false, raw, error: null};
  } catch (error) {
    return {settings: defaultSettings(), status: 'corrupt', readOnly: false, raw,
      error: `저장된 설정을 불러오지 못해 기본값으로 실행합니다. 원본은 지우지 않았습니다. ${error.message}`};
  }
}

/** Holds the last committed record. A failed change throws and leaves memory and storage as they were. */
export class SettingsStore {
  constructor({storage, validateRegistrations = noValidator, allowedActions = [], now = () => new Date()} = {}) {
    Object.assign(this, {storage, validateRegistrations, now});
    this.allowedActions = new Set(allowedActions);
    this.load();
  }

  load() {
    const result = readSettings({storage: this.storage, validateRegistrations: this.validateRegistrations});
    Object.assign(this, {current: result.settings, status: result.status, readOnly: result.readOnly, raw: result.raw, error: result.error});
    return {settings: this.settings, status: this.status, error: this.error};
  }

  get settings() { return structuredClone(this.current); }

  update(change) {
    if (this.readOnly) throw new Error(this.error ?? '설정을 저장할 수 없습니다.');
    let stored;
    try {
      this.storage ??= globalThis.localStorage;
      stored = this.storage.getItem(SETTINGS_KEY);
    } catch (error) { throw new Error(`설정 저장소를 사용할 수 없습니다. ${error.message}`); }
    // Another window saved since this one loaded; refuse instead of overwriting its changes.
    if (stored !== this.raw) throw new Error('다른 창에서 설정이 바뀌었습니다. 앱을 다시 열어 주세요.');
    const next = structuredClone(this.current);
    change(next);
    next.revision = this.current.revision + 1;
    next.savedAt = this.now().toISOString();
    const checked = validateSettings(next, {validateRegistrations: this.validateRegistrations});
    if (!checked.valid) throw new Error(`저장할 설정이 올바르지 않습니다. ${checked.errors.slice(0, 3).join(' ')}`);
    const serialized = JSON.stringify(next);
    if (serialized.length > MAX_SERIALIZED_LENGTH) throw new Error('설정 저장 용량을 초과했습니다.');
    try {
      // Keep one copy of unreadable data before the first overwrite, so it can still be recovered.
      if (this.status === 'corrupt' && this.storage.getItem(SETTINGS_BACKUP_KEY) === null) this.storage.setItem(SETTINGS_BACKUP_KEY, this.raw);
      this.storage.setItem(SETTINGS_KEY, serialized);
    } catch (error) {
      throw new Error(`설정을 저장하지 못했습니다. 기존 설정은 그대로입니다. ${error.message}`);
    }
    try {
      if (this.storage.getItem(SETTINGS_KEY) !== serialized) throw new Error('저장값이 일치하지 않습니다.');
    } catch (error) {
      if (!this.restoreRaw()) {
        this.readOnly = true;
        this.error = `설정 저장과 이전 값 복원을 확인하지 못했습니다. 앱을 다시 열어 저장소를 확인하세요. ${error.message}`;
        throw new Error(this.error);
      }
      throw new Error(`설정 저장을 확인하지 못했습니다. 기존 설정은 그대로입니다. ${error.message}`);
    }
    Object.assign(this, {current: next, status: 'loaded', raw: serialized, error: null});
    return this.settings;
  }

  restoreRaw() {
    try {
      if (this.raw === null) this.storage.removeItem(SETTINGS_KEY);
      else this.storage.setItem(SETTINGS_KEY, this.raw);
      return this.storage.getItem(SETTINGS_KEY) === this.raw;
    } catch { return false; }
  }

  environment(next, name) {
    if (!ENVIRONMENTS.includes(name)) throw new RangeError(`알 수 없는 환경입니다: ${name}`);
    return next.environments[name];
  }

  setMouse(patch) {
    return this.update(next => {
      const {area, ...rest} = patch;
      Object.assign(next.mouse, rest);
      if (area) next.mouse.area = {...next.mouse.area, ...area};
    });
  }

  resetMouse() { return this.update(next => { next.mouse = defaultSettings().mouse; }); }

  /** Adds or replaces one verified gesture. Its binding and every other gesture stay as they are. */
  putGesture({registration, verifiedAt, sessionId, environment = 'desk'}) {
    if (!isRecord(registration)) throw new TypeError('등록 데이터가 없습니다.');
    return this.update(next => {
      const env = this.environment(next, environment);
      env.registrations[registration.gestureId] = registration;
      env.verifications[registration.gestureId] = {verifiedAt, sessionId};
    });
  }

  /** Removes one gesture together with its verification record and binding. */
  removeGesture(gestureId, environment = 'desk') {
    return this.update(next => {
      const env = this.environment(next, environment);
      delete env.registrations[gestureId]; delete env.verifications[gestureId]; delete env.bindings[gestureId];
    });
  }

  setBinding({gestureId, actionId, enabled = true}, environment = 'desk') {
    if (!this.allowedActions.has(actionId)) throw new RangeError(`허용되지 않은 액션입니다: ${actionId}`);
    return this.update(next => {
      const env = this.environment(next, environment);
      if (!Object.hasOwn(env.registrations, gestureId)) throw new RangeError(`등록되지 않은 동작입니다: ${gestureId}`);
      env.bindings[gestureId] = {gestureId, actionId, enabled};
    });
  }

  removeBinding(gestureId, environment = 'desk') {
    return this.update(next => { delete this.environment(next, environment).bindings[gestureId]; });
  }
}
