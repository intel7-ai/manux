/** Approximate palm view from monocular landmarks, not calibrated camera angles.
 * Translation, uniform scale and image-plane roll do not create new views.
 * Angles are in degrees. Positive yaw points right in the mirrored preview;
 * positive pitch points upward. These labels describe palm tilt, not moving a
 * hand across the frame or rotating an image like a clock face.
 */
export const VIEWS = Object.freeze(['front', 'left', 'right', 'up', 'down']);
export const VIEW_LABELS = Object.freeze({front:'정면', left:'왼쪽', right:'오른쪽', up:'위쪽', down:'아래쪽'});
const finite = Number.isFinite;
const degrees = radians => radians * 180 / Math.PI;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const subtract = (a, b) => a.map((value, i) => value - b[i]);
const dot = (a, b) => a.reduce((sum, value, i) => sum + value * b[i], 0);
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
function unit(vector) { const length = Math.hypot(...vector); return finite(length) && length > 1e-9 ? vector.map(value => value / length) : null; }
export const validOrientation = value => value?.valid === true && [value.yaw, value.pitch].every(number => finite(number) && Math.abs(number) <= 90);

export function extractOrientation(landmarks, {width = 1, height = 1, mirrored = true} = {}) {
  const invalid = reason => ({valid:false, yaw:null, pitch:null, roll:null, normal:null, reason});
  if (!finite(width) || !finite(height) || width <= 0 || height <= 0 || width > 32768 || height > 32768 ||
    !Array.isArray(landmarks) || landmarks.length !== 21 || !Array.from(landmarks).every(point => point && [point.x,point.y,point.z ?? 0].every(finite))) return invalid('invalid-landmarks');
  const point = index => [landmarks[index].x * width, landmarks[index].y * height, (landmarks[index].z ?? 0) * width];
  const distal = unit(subtract(point(9), point(0)));
  const across = subtract(point(5), point(17));
  if (!distal) return invalid('degenerate-palm');
  const lateral = unit(across.map((value, i) => value - dot(across, distal) * distal[i]));
  if (!lateral) return invalid('degenerate-palm');
  let normal = unit(cross(lateral, distal));
  if (!normal || Math.hypot(distal[0], distal[1]) < 0.15) return invalid('palm-edge-on');
  // Use the visible hemisphere consistently for either handedness. Crossing an
  // edge-on palm is rejected rather than treated as useful enrollment coverage.
  if (normal[2] < 0) normal = normal.map(value => -value);
  if (normal[2] < 0.15) return invalid('palm-edge-on');
  const roll = Math.atan2(distal[0], -distal[1]);
  const cos = Math.cos(roll), sin = Math.sin(roll);
  const canonical = [cos * normal[0] + sin * normal[1], -sin * normal[0] + cos * normal[1], normal[2]];
  const yaw = degrees(Math.atan2(canonical[0], canonical[2])) * (mirrored ? -1 : 1);
  const pitch = degrees(Math.atan2(-canonical[1], canonical[2]));
  return {valid:true, yaw, pitch, roll:degrees(roll), normal:canonical, reason:'ok'};
}

/** Relative to the stable front reference for THIS pose and hand.
 * A diagonal/overshoot is deliberately unassigned, so one frame cannot satisfy
 * multiple requested directions. The bands are heuristics, not measured degrees.
 */
export function orientationFeedback(orientation, baseline, targetView) {
  const empty = reason => ({x:0,y:0,deltaYaw:null,deltaPitch:null,label:null,matched:false,reason});
  if (!validOrientation(orientation)) return empty(orientation?.reason || 'orientation-unavailable');
  if (!baseline || ![baseline.yaw,baseline.pitch].every(finite)) return empty('need-front-reference');
  const yaw = orientation.yaw - baseline.yaw, pitch = orientation.pitch - baseline.pitch;
  let label = null;
  if (Math.abs(yaw) <= 10 && Math.abs(pitch) <= 10) label = 'front';
  else if (yaw >= 15 && yaw <= 45 && Math.abs(pitch) <= 12) label = 'right';
  else if (yaw <= -15 && yaw >= -45 && Math.abs(pitch) <= 12) label = 'left';
  else if (pitch >= 15 && pitch <= 45 && Math.abs(yaw) <= 12) label = 'up';
  else if (pitch <= -15 && pitch >= -45 && Math.abs(yaw) <= 12) label = 'down';
  const matched = label === targetView && VIEWS.includes(targetView);
  return {x:clamp(yaw / 30,-1,1),y:clamp(-pitch / 30,-1,1),deltaYaw:yaw,deltaPitch:pitch,label,matched,
    reason:matched ? 'ok' : Math.abs(yaw) > 45 || Math.abs(pitch) > 45 ? 'tilt-too-far' : label ? 'wrong-view' : 'between-views'};
}
