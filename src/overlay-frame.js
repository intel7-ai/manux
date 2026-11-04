// Screen overlay "finger tracker": a compact, engine-independent description of the hand for the
// desktop overlay window. Adapted from park's transparent HUD (park branch, 11/03).
export const OVERLAY_TIPS = Object.freeze({thumb:4, index:8, middle:12, ring:16, pinky:20});
export const OVERLAY_MODES = Object.freeze(['idle', 'move', 'left', 'right', 'scroll', 'other']);
export const OVERLAY_TIP_LIMIT = 4;
const CONTACT_RATIO = 0.35;

const finitePoint = p => p && Number.isFinite(p.x) && Number.isFinite(p.y);
const clamp = (value, limit) => Math.max(-limit, Math.min(limit, value));

/**
 * Fingertip offsets from the palm center in palm-size units, mirrored so the user's right is the
 * screen's right. Returns null when the hand is missing or degenerate; the overlay then fades.
 * `mode` is the active engine's mode; unknown modes are reported as 'other'.
 */
export function buildOverlayFrame(landmarks, {mode = 'idle', width = 1, height = 1} = {}) {
  if (!Array.isArray(landmarks) || landmarks.length < 21 || !landmarks.every(finitePoint)) return null;
  if (!(width > 0 && height > 0)) return null;
  const point = i => ({x:landmarks[i].x * width, y:landmarks[i].y * height});
  const anchor = [0, 5, 9, 13, 17].map(point).reduce((sum, p) => ({x:sum.x + p.x / 5, y:sum.y + p.y / 5}), {x:0, y:0});
  const palm = Math.hypot(point(9).x - point(0).x, point(9).y - point(0).y);
  if (!(palm > 1e-6)) return null;
  const tips = {};
  for (const [name, index] of Object.entries(OVERLAY_TIPS)) {
    const p = point(index);
    tips[name] = [clamp(-(p.x - anchor.x) / palm, OVERLAY_TIP_LIMIT), clamp((p.y - anchor.y) / palm, OVERLAY_TIP_LIMIT)];
  }
  // Only the finger nearest the thumb counts: a curled finger resting beside it is not a touch.
  const thumb = point(OVERLAY_TIPS.thumb);
  const [nearest] = ['index', 'middle', 'ring', 'pinky']
    .map(name => { const p = point(OVERLAY_TIPS[name]); return {name, ratio:Math.hypot(p.x - thumb.x, p.y - thumb.y) / palm}; })
    .sort((a, b) => a.ratio - b.ratio);
  const contacts = nearest.ratio < CONTACT_RATIO ? [nearest.name] : [];
  return {tips, contacts, mode:OVERLAY_MODES.includes(mode) ? mode : 'other'};
}
