// Pure checks for the screen overlay; no Electron import so they can be unit tested.
const MODES = ['idle', 'move', 'left', 'right', 'scroll', 'other'];
const TIPS = ['thumb', 'index', 'middle', 'ring', 'pinky'];
const FINGERS = ['index', 'middle', 'ring', 'pinky'];
const LIMIT = 4;
const exactKeys = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const offset = value => Array.isArray(value) && value.length === 2
  && value.every(n => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= LIMIT);

/** Copies only the overlay frame shape built by src/overlay-frame.js; anything else is dropped. */
export function validateOverlayFrame(frame) {
  if (!exactKeys(frame, ['tips', 'contacts', 'mode']) || !exactKeys(frame.tips, TIPS)) return null;
  if (!TIPS.every(name => offset(frame.tips[name])) || !MODES.includes(frame.mode)) return null;
  if (!Array.isArray(frame.contacts) || frame.contacts.length > FINGERS.length
    || !frame.contacts.every(name => FINGERS.includes(name)) || new Set(frame.contacts).size !== frame.contacts.length) return null;
  return {tips:Object.fromEntries(TIPS.map(name => [name, [...frame.tips[name]]])), contacts:[...frame.contacts], mode:frame.mode};
}

/** Pure check that the overlay page is the bundled one and nothing else. */
export function isOverlayDocument(url, appOrigin) {
  try {
    const parsed = new URL(url);
    return parsed.origin === appOrigin && parsed.pathname === '/overlay.html' && !parsed.search && !parsed.hash;
  } catch { return false; }
}
