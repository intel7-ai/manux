import {validateProfile} from './personalization.js';

const KEY = 'manux.profiles.v1';
const MAX_PROFILES = 12;
const MAX_SERIALIZED_LENGTH = 4000000;
const validList = profiles => Array.isArray(profiles) && profiles.length <= MAX_PROFILES
  && Array.from(profiles).every(profile => validateProfile(profile).valid);

export function readProfiles(storage) {
  try {
    // Access can itself throw when browser storage is disabled.
    const target = storage ?? globalThis.localStorage;
    const raw = target.getItem(KEY);
    if (!raw) return {profiles: [], error: null};
    if (raw.length > MAX_SERIALIZED_LENGTH) throw new Error('저장 데이터가 너무 큽니다.');
    const profiles = JSON.parse(raw);
    if (!validList(profiles)) throw new Error('등록 데이터 형식이 올바르지 않습니다.');
    return {profiles, error: null};
  } catch (error) {
    return {profiles: [], error: `개인 등록을 불러오지 못했습니다. ${error.message}`};
  }
}

export function writeProfiles(profiles, storage) {
  if (!validList(profiles)) throw new Error('저장할 사용자 데이터가 올바르지 않습니다.');
  const serialized = JSON.stringify(profiles);
  if (serialized.length > MAX_SERIALIZED_LENGTH) throw new Error('사용자 등록 저장 용량을 초과했습니다.');
  const target = storage ?? globalThis.localStorage;
  target.setItem(KEY, serialized);
}
