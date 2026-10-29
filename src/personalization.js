/**
 * Local, pose-based personalization. This model does not identify a person.
 *
 * const f = extractFeatures(result.landmarks[0], {width: video.videoWidth,
 *   height: video.videoHeight, handedness: 'Right'});
 * const model = trainEnvironment({Open_Palm: [...], Thumb_Up: [...],
 *   Victory: [...], Closed_Fist: [...], None: [...]});
 * const match = classifyPersonal(f, model);
 *
 * Enroll desk and sofa separately. Every label needs at least 8 feature vectors.
 * Save samples and the serializable model in profile.environments[desk|sofa].
 * `accepted` is a distance/margin decision, NOT a calibrated probability.
 * A matching `None` is an explicit neutral pose. Unknown input is rejected.
 */
export const POSES = Object.freeze(['Open_Palm', 'Thumb_Up', 'Victory', 'Closed_Fist', 'None']);
export const FEATURE_COUNT = 37;
export const MIN_SAMPLES_PER_POSE = 8;
export const MAX_SAMPLES_PER_POSE = 600;
export const ENROLLMENT_VIEWS = Object.freeze(['front', 'left', 'right', 'up', 'down']);
export const MIN_SAMPLES_PER_VIEW = 12;
const MAX_TEMPLATES = 24;
const MAX_VIEW_TEMPLATES = 8;
const MAX_RADIUS = 0.14;
const MIN_SEPARATION = 0.035;
const ENVIRONMENTS = ['desk', 'sofa'];
const FINGERS = [[0, 1, 2, 3, 4], [0, 5, 6, 7, 8], [0, 9, 10, 11, 12], [0, 13, 14, 15, 16], [0, 17, 18, 19, 20]];
const TIPS = [4, 8, 12, 16, 20];

const finite = Number.isFinite;
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const featureDistance = (a, b) => Math.sqrt(a.reduce((sum, x, i) => sum + (x - b[i]) ** 2, 0) / a.length);
const quantile = (values, q) => {
  const sorted = [...values].sort((a, b) => a - b);
  const pos = (sorted.length - 1) * q;
  const lower = Math.floor(pos), fraction = pos - lower;
  return sorted[lower] * (1 - fraction) + sorted[Math.min(lower + 1, sorted.length - 1)] * fraction;
};
const recordValid = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const vectorValid = vector => Array.isArray(vector) && vector.length === FEATURE_COUNT && Array.from(vector).every(x => finite(x) && x >= 0 && x <= 1);
const dimensionsValid = (width, height) => finite(width) && finite(height) && width > 0 && height > 0 && width <= 32768 && height <= 32768;
const landmarksValid = landmarks => Array.isArray(landmarks) && landmarks.length === 21 && Array.from(landmarks).every(p => p && finite(p.x) && finite(p.y) && finite(p.z ?? 0) && Math.abs(p.x) < 100000 && Math.abs(p.y) < 100000 && Math.abs(p.z ?? 0) < 100000);

function jointAngle(a, b, c) {
  const u = a.map((x, i) => x - b[i]), v = c.map((x, i) => x - b[i]);
  const denominator = Math.hypot(...u) * Math.hypot(...v);
  if (denominator < 1e-12) return null;
  return Math.acos(clamp(u.reduce((sum, x, i) => sum + x * v[i], 0) / denominator, -1, 1)) / Math.PI;
}

/** Intrinsic angles and distance ratios are invariant to translation, scale,
 * reflection (left/right), and rotation. Correct image aspect before measuring.
 * Perspective/occlusion still affects estimated landmarks and is not recovered.
 */
export function extractFeatures(landmarks, {width = 1, height = 1, handedness = 'Right'} = {}) {
  void handedness; // Reflection-invariant features intentionally need no hand flip.
  if (!landmarksValid(landmarks) || !dimensionsValid(width, height)) return null;
  const p = landmarks.map(v => [v.x * width, v.y * height, (v.z ?? 0) * width]);
  const scale = (distance(p[0], p[5]) + distance(p[0], p[9]) + distance(p[0], p[17])) / 3;
  if (!finite(scale) || scale < Math.max(width, height) * 1e-8) return null;
  const features = [];
  for (const finger of FINGERS) {
    for (let i = 1; i <= 3; i++) {
      const angle = jointAngle(p[finger[i - 1]], p[finger[i]], p[finger[i + 1]]);
      if (angle === null) return null;
      features.push(angle);
    }
  }
  // Wrist-to-tip reach, normalized to palm size and bounded.
  for (const tip of TIPS) features.push(clamp(distance(p[0], p[tip]) / scale / 4, 0, 1));
  // Finger extension relative to its own bone-chain length.
  for (const finger of FINGERS) {
    let length = 0;
    for (let i = 1; i < 4; i++) length += distance(p[finger[i]], p[finger[i + 1]]);
    if (length < scale * 1e-8) return null;
    features.push(clamp(distance(p[finger[1]], p[finger[4]]) / length, 0, 1));
  }
  for (let a = 0; a < TIPS.length; a++) {
    for (let b = a + 1; b < TIPS.length; b++) features.push(clamp(distance(p[TIPS[a]], p[TIPS[b]]) / scale / 3, 0, 1));
  }
  features.push(clamp(distance(p[4], p[5]) / scale / 3, 0, 1));
  features.push(clamp(distance(p[4], p[17]) / scale / 3, 0, 1));
  return vectorValid(features) ? features : null;
}

/** Pixel-size checks are conservative usability heuristics, not distance meters
 * or a measured camera accuracy guarantee. Blurred images can still pass.
 */
export function getHandQuality(landmarks, {width = 1, height = 1} = {}) {
  if (!landmarksValid(landmarks) || !dimensionsValid(width, height)) return {ok: false, reason: 'invalid-landmarks', spanPx: 0, shortSidePx: 0, boundaryCutoff: false};
  const xs = landmarks.map(p => p.x), ys = landmarks.map(p => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const boxWidth = (maxX - minX) * width, boxHeight = (maxY - minY) * height;
  const spanPx = Math.max(boxWidth, boxHeight), shortSidePx = Math.min(boxWidth, boxHeight);
  const boundaryCutoff = minX < 0.005 || minY < 0.005 || maxX > 0.995 || maxY > 0.995;
  const reason = boundaryCutoff ? 'near-frame-edge' : spanPx < 45 || shortSidePx < 20 ? 'hand-too-small' : extractFeatures(landmarks, {width, height}) === null ? 'invalid-landmarks' : 'ok';
  return {ok: reason === 'ok', reason, spanPx, shortSidePx, boundaryCutoff};
}

function sampleErrors(samples) {
  if (!samples || typeof samples !== 'object' || Array.isArray(samples)) return ['samples must be an object'];
  const errors = [];
  for (const pose of POSES) {
    const rows = samples[pose];
    if (!Array.isArray(rows) || rows.length < MIN_SAMPLES_PER_POSE || rows.length > MAX_SAMPLES_PER_POSE) errors.push(`${pose}: require ${MIN_SAMPLES_PER_POSE}–${MAX_SAMPLES_PER_POSE} samples`);
    else if (!Array.from(rows).every(vectorValid)) errors.push(`${pose}: invalid feature vector`);
  }
  return errors;
}

function chooseTemplates(rows, limit = MAX_TEMPLATES) {
  if (rows.length <= limit) return rows.map(row => [...row]);
  // Farthest-point sampling preserves observed subposes rather than only a mean.
  const selected = [rows[0]];
  while (selected.length < limit) {
    let best = null, bestDistance = -1;
    for (const row of rows) {
      const d = Math.min(...selected.map(template => featureDistance(row, template)));
      if (d > bestDistance) { best = row; bestDistance = d; }
    }
    if (bestDistance < 1e-9) break;
    selected.push(best);
  }
  return selected.map(row => [...row]);
}

/** Train desk or sofa independently. Malformed input throws; ambiguous/noisy
 * enrollment returns usable:false with warnings and will not issue commands.
 */
export function trainEnvironment(samplesByPose, {viewSamples} = {}) {
  const errors = sampleErrors(samplesByPose);
  if (errors.length) throw new TypeError(errors.join('; '));
  if (viewSamples !== undefined) {
    const viewErrors = viewSampleErrors(viewSamples, samplesByPose);
    if (viewErrors.length) throw new TypeError(viewErrors.join('; '));
    return trainViews(viewSamples);
  }
  const warnings = [];
  const classes = {};
  for (const pose of POSES) {
    const rows = samplesByPose[pose];
    const center = Array.from({length: FEATURE_COUNT}, (_, i) => quantile(rows.map(row => row[i]), 0.5));
    const radii = rows.map(row => featureDistance(row, center));
    const median = quantile(radii, 0.5), mad = quantile(radii.map(d => Math.abs(d - median)), 0.5);
    const cutoff = Math.min(MAX_RADIUS, Math.max(0.035, median + 3 * mad));
    const inliers = rows.filter((_, i) => radii[i] <= cutoff);
    const enabled = inliers.length >= MIN_SAMPLES_PER_POSE && inliers.length / rows.length >= 0.75 && quantile(radii, 0.9) <= MAX_RADIUS;
    const templates = chooseTemplates(inliers.length ? inliers : [center]);
    const radius = Math.min(MAX_RADIUS, Math.max(0.035, median + 3 * mad + 0.025));
    classes[pose] = {center, templates, radius, enabled, sampleCount: rows.length, inlierCount: inliers.length, spread: quantile(radii, 0.9)};
    if (!enabled) warnings.push(`${pose}: 등록 중 손 모양이 많이 달라졌습니다. 같은 동작을 유지해 다시 등록하세요.`);
  }
  for (let i = 0; i < POSES.length; i++) {
    for (let j = i + 1; j < POSES.length; j++) {
      const a = classes[POSES[i]], b = classes[POSES[j]];
      const nearest = Math.min(...a.templates.flatMap(x => b.templates.map(y => featureDistance(x, y))));
      if (nearest < MIN_SEPARATION) {
        a.enabled = false; b.enabled = false;
        warnings.push(`${POSES[i]} / ${POSES[j]}: 두 동작이 너무 비슷합니다. 구분되는 손 모양으로 다시 등록하세요.`);
      }
      a.radius = Math.min(a.radius, nearest * 0.44);
      b.radius = Math.min(b.radius, nearest * 0.44);
    }
  }
  const usable = POSES.every(pose => classes[pose].enabled && classes[pose].radius >= 0.012);
  if (!usable && !warnings.length) warnings.push('동작 사이의 차이가 작습니다. 손 모양을 더 구분해서 다시 등록하세요.');
  return {version: 1, featureCount: FEATURE_COUNT, usable, classes, warnings};
}

function viewSampleErrors(viewSamples, flatSamples) {
  if (!recordValid(viewSamples)) return ['viewSamples must be an object'];
  const errors = [];
  for (const pose of POSES) {
    const groups = viewSamples[pose];
    if (!recordValid(groups)) { errors.push(`${pose}: missing view samples`); continue; }
    let valid = true;
    for (const view of ENROLLMENT_VIEWS) {
      const rows = groups[view];
      if (!Array.isArray(rows) || rows.length < MIN_SAMPLES_PER_VIEW || rows.length > MAX_SAMPLES_PER_POSE) {
        errors.push(`${pose}/${view}: require ${MIN_SAMPLES_PER_VIEW}–${MAX_SAMPLES_PER_POSE} samples`); valid = false;
      } else if (!Array.from(rows).every(vectorValid)) {
        errors.push(`${pose}/${view}: invalid feature vector`); valid = false;
      }
    }
    if (!valid) continue;
    const all = ENROLLMENT_VIEWS.flatMap(view => groups[view]);
    if (all.length > MAX_SAMPLES_PER_POSE) { errors.push(`${pose}: too many combined view samples`); continue; }
    // Grouping must preserve the captured examples. Comparing a multiset accepts
    // either view order and retains duplicate observations without inventing data.
    const grouped = all.map(row => JSON.stringify(row)).sort();
    const flatRows = flatSamples?.[pose];
    const flat = Array.isArray(flatRows) && Array.from(flatRows).every(vectorValid)
      ? flatRows.map(row => JSON.stringify(row)).sort() : null;
    if (!flat || grouped.length !== flat.length || grouped.some((row, i) => row !== flat[i])) {
      errors.push(`${pose}: view samples do not match flat samples`);
    }
  }
  return errors;
}

function trainView(rows) {
  const center = Array.from({length: FEATURE_COUNT}, (_, i) => quantile(rows.map(row => row[i]), 0.5));
  const radii = rows.map(row => featureDistance(row, center));
  const median = quantile(radii, 0.5), mad = quantile(radii.map(d => Math.abs(d - median)), 0.5);
  const cutoff = Math.min(MAX_RADIUS, Math.max(0.035, median + 3 * mad));
  const inliers = rows.filter((_, i) => radii[i] <= cutoff);
  const enabled = inliers.length >= MIN_SAMPLES_PER_VIEW && inliers.length / rows.length >= 0.75 && quantile(radii, 0.9) <= MAX_RADIUS;
  return {
    center, templates: chooseTemplates(inliers.length ? inliers : [center], MAX_VIEW_TEMPLATES),
    radius: Math.min(MAX_RADIUS, Math.max(0.035, median + 3 * mad + 0.025)), enabled,
    sampleCount: rows.length, inlierCount: inliers.length, spread: quantile(radii, 0.9),
  };
}

/** Every view is a separate compact local model. A legitimate side view is not
 * an outlier from the front view, and empty space between views is not accepted.
 * This fits examples and thresholds; it does not change pretrained weights.
 */
function trainViews(viewSamples) {
  const classes = {}, warnings = [];
  for (const pose of POSES) {
    const views = Object.fromEntries(ENROLLMENT_VIEWS.map(view => [view, trainView(viewSamples[pose][view])]));
    for (const view of ENROLLMENT_VIEWS) if (!views[view].enabled) {
      warnings.push(`${pose}/${view}: 이 각도에서 손 모양이 많이 달라졌습니다. 손 모양을 유지해 다시 수집하세요.`);
    }
    classes[pose] = {
      views, enabled: true,
      sampleCount: ENROLLMENT_VIEWS.reduce((sum, view) => sum + views[view].sampleCount, 0),
      inlierCount: ENROLLMENT_VIEWS.reduce((sum, view) => sum + views[view].inlierCount, 0),
    };
  }
  for (let i = 0; i < POSES.length; i++) for (let j = i + 1; j < POSES.length; j++) {
    let overlap = null;
    for (const viewA of ENROLLMENT_VIEWS) for (const viewB of ENROLLMENT_VIEWS) {
      const a = classes[POSES[i]].views[viewA], b = classes[POSES[j]].views[viewB];
      const nearest = Math.min(...a.templates.flatMap(x => b.templates.map(y => featureDistance(x, y))));
      a.radius = Math.min(a.radius, nearest * 0.44);
      b.radius = Math.min(b.radius, nearest * 0.44);
      if (nearest < MIN_SEPARATION) {
        a.enabled = false; b.enabled = false; overlap ??= [viewA, viewB];
      }
    }
    // One summary per command pair bounds warnings even if all views overlap.
    if (overlap) warnings.push(`${POSES[i]}/${overlap[0]} / ${POSES[j]}/${overlap[1]}: 서로 다른 명령이 너무 비슷합니다. 구분되는 손 모양으로 다시 등록하세요.`);
  }
  for (const pose of POSES) classes[pose].enabled = ENROLLMENT_VIEWS.every(view => {
    const group = classes[pose].views[view];
    return group.enabled && group.radius >= 0.012;
  });
  const usable = POSES.every(pose => classes[pose].enabled);
  if (!usable && !warnings.length) warnings.push('각도별 동작의 차이가 작습니다. 손 모양을 더 구분해서 다시 등록하세요.');
  return {version: 2, featureCount: FEATURE_COUNT, usable, classes, warnings};
}

function groupValid(group, {usable, minSamples, maxTemplates}) {
  return recordValid(group) && vectorValid(group.center) && finite(group.radius) && group.radius >= 0 && group.radius <= MAX_RADIUS
    && typeof group.enabled === 'boolean' && (!usable || (group.enabled && group.radius >= 0.012 && group.inlierCount >= minSamples))
    && Number.isInteger(group.sampleCount) && group.sampleCount >= minSamples && group.sampleCount <= MAX_SAMPLES_PER_POSE
    && Number.isInteger(group.inlierCount) && group.inlierCount >= 0 && group.inlierCount <= group.sampleCount
    && finite(group.spread) && group.spread >= 0 && group.spread <= 1
    && Array.isArray(group.templates) && group.templates.length >= 1 && group.templates.length <= maxTemplates && Array.from(group.templates).every(vectorValid);
}

function modelValid(model) {
  const common = recordValid(model) && [1, 2].includes(model.version) && model.featureCount === FEATURE_COUNT && typeof model.usable === 'boolean'
    && Array.isArray(model.warnings) && model.warnings.length <= 50 && Array.from(model.warnings).every(w => typeof w === 'string' && w.length <= 400)
    && recordValid(model.classes);
  if (!common) return false;
  if (model.version === 1) return POSES.every(pose => groupValid(model.classes[pose], {
    usable: model.usable, minSamples: MIN_SAMPLES_PER_POSE, maxTemplates: MAX_TEMPLATES,
  }));
  return POSES.every(pose => {
    const c = model.classes[pose];
    return recordValid(c) && typeof c.enabled === 'boolean' && (!model.usable || c.enabled)
      && recordValid(c.views) && ENROLLMENT_VIEWS.every(view => groupValid(c.views[view], {
        usable: model.usable, minSamples: MIN_SAMPLES_PER_VIEW, maxTemplates: MAX_VIEW_TEMPLATES,
      }))
      && c.sampleCount === ENROLLMENT_VIEWS.reduce((sum, view) => sum + c.views[view].sampleCount, 0)
      && c.sampleCount <= MAX_SAMPLES_PER_POSE
      && c.inlierCount === ENROLLMENT_VIEWS.reduce((sum, view) => sum + c.views[view].inlierCount, 0);
  });
}

export function classifyPersonal(features, model) {
  const rejected = (reason, distance = null, margin = null) => ({gesture: null, accepted: false, distance, margin, reason});
  if (!vectorValid(features)) return rejected('invalid-features');
  if (!modelValid(model)) return rejected('invalid-model');
  if (!model.usable) return rejected('ambiguous-enrollment');
  const scores = POSES.map(pose => {
    const groups = model.version === 1 ? [['legacy', model.classes[pose]]] : ENROLLMENT_VIEWS.map(view => [view, model.classes[pose].views[view]]);
    const nearest = groups.map(([view, group]) => ({view, group, distance: Math.min(...group.templates.map(template => featureDistance(features, template)))})).sort((a, b) => a.distance - b.distance)[0];
    return {pose, ...nearest};
  }).sort((a, b) => a.distance - b.distance);
  const first = scores[0], second = scores[1], margin = second.distance - first.distance;
  const c = first.group;
  if (!c.enabled || first.distance > c.radius) return rejected('outside-enrollment', first.distance, margin);
  if (margin < 0.012 || (second.distance > 0 && first.distance / second.distance > 0.72)) return rejected('ambiguous-pose', first.distance, margin);
  return {gesture: first.pose, accepted: true, distance: first.distance, margin, reason: first.pose === 'None' ? 'neutral' : 'matched', ...(model.version === 2 ? {view: first.view} : {})};
}

/** Validate parsed local storage/import data before using it. Extra metadata
 * is ignored. Profiles may be empty while onboarding, but existing environments
 * must contain complete samples; models, when present, must pass structure checks.
 */
export function validateProfile(profile) {
  const errors = [];
  if (!profile || typeof profile !== 'object' || Array.isArray(profile)) return {valid: false, errors: ['profile must be an object']};
  if (profile.version !== 1) errors.push('unsupported profile version');
  if (typeof profile.id !== 'string' || !profile.id.trim() || profile.id.length > 80) errors.push('invalid profile id');
  if (typeof profile.name !== 'string' || !profile.name.trim() || profile.name.length > 80) errors.push('invalid profile name');
  if (!profile.environments || typeof profile.environments !== 'object' || Array.isArray(profile.environments)) errors.push('invalid environments');
  else for (const [environment, data] of Object.entries(profile.environments)) {
    if (!ENVIRONMENTS.includes(environment)) { errors.push(`unknown environment: ${environment}`); continue; }
    if (!data || typeof data !== 'object' || Array.isArray(data)) { errors.push(`${environment}: invalid environment`); continue; }
    errors.push(...sampleErrors(data.samples).map(message => `${environment}: ${message}`));
    if (data.model !== undefined && !modelValid(data.model)) errors.push(`${environment}: invalid model`);
    if (data.viewSamples !== undefined) errors.push(...viewSampleErrors(data.viewSamples, data.samples).map(message => `${environment}: ${message}`));
  }
  return {valid: errors.length === 0, errors};
}
