// Index-pointer engine ported from hand-mouse/src/pointer_engine.py + pointer_workspace.py
// (Python Qt app, 11/03-11/04). Contract: hand-mouse/docs/engine-contract.md.
// Ported click styles: "trigger" (option A, thumb pressed on the index side) and "off"
// (tracking only). The "pinch" style needs the Python gesture engine and is not ported yet.
const INDEX_TIP = 8;
const FINGER_JOINTS = [[5, 8], [9, 12], [13, 16], [17, 20]];
const dist = (a, b) => Math.hypot(...a.map((v, i) => v - b[i]));
const finite = (...values) => values.every(Number.isFinite);
const finiteSetting = (config, key, fallback) => {
  const value = Number(config?.[key]);
  return Number.isFinite(value) && value >= 0 ? value : fallback;
};

export const POINTER_STYLES = Object.freeze(['trigger', 'off']);

export function fingerCurlRatios(points) {
  const wrist = points[0];
  const d3 = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, (a.z ?? 0) - (b.z ?? 0));
  return FINGER_JOINTS.map(([mcp, tip]) => {
    const base = d3(points[mcp], wrist);
    return base > 1e-6 ? d3(points[tip], wrist) / base : 9.9;
  });
}

export function palmAnchorPoint(points, ratio = .5) {
  const r = Math.max(0, Math.min(1, ratio)), wrist = points[0], palm = points[9];
  return [wrist.x + (palm.x - wrist.x) * r, wrist.y + (palm.y - wrist.y) * r];
}

export function thumbDistances(points, aspect) {
  const d = (i, j) => Math.hypot(points[i].x - points[j].x, (points[i].y - points[j].y) * aspect, (points[i].z ?? 0) - (points[j].z ?? 0));
  const width = d(5, 17);
  if (!Number.isFinite(width) || width < .025) return [];
  const ratios = [8, 12, 16, 20].map(tip => d(4, tip) / width);
  return ratios.every(Number.isFinite) ? ratios : [];
}

/** Unambiguous thumb contacts by fingertip index (1 = index ... 4 = pinky), 0 = thumb. */
export function thumbContacts(ratios) {
  if (ratios.some(r => r > .23 && r < .36)) return [];
  const contacts = ratios.flatMap((r, i) => r <= .23 ? [i + 1] : []);
  return contacts.length ? [0, ...contacts] : [];
}

export function pointerAxes(center, right, up, aspect) {
  const rx = right[0] - center[0], ry = (right[1] - center[1]) * aspect;
  const dx = center[0] - up[0], dy = (center[1] - up[1]) * aspect;
  const width = Math.hypot(rx, ry), height = Math.hypot(dx, dy), det = rx * dy - ry * dx;
  if (Math.min(width, height) < .025 || det <= .5 * width * height || rx <= 0 || dy <= 0) return null;
  const scale = .08 / det;
  return [dy * scale, -dx * scale, -ry * scale, rx * scale];
}

export function calibratedPointer(point, axes, aspect) {
  if (!axes) return point;
  const x = point[0] - .5, y = (point[1] - .5) * aspect, [xx, xy, yx, yy] = axes;
  return [.5 + (xx * x + xy * y) / Math.hypot(xx, xy), .5 + (yx * x + yy * y) / (Math.hypot(yx, yy) * aspect)];
}

export class IndexTipGuard {
  constructor() { this.reset(); }
  reset() { this.offset = null; this.pending = null; }
  accept(points, aspect) {
    const tip = points[8], joint = points[6], indexBase = points[5], pinkyBase = points[17];
    const offset = [tip.x - joint.x, (tip.y - joint.y) * aspect];
    const palmWidth = Math.hypot(indexBase.x - pinkyBase.x, (indexBase.y - pinkyBase.y) * aspect);
    if (!finite(...offset, palmWidth)) { this.reset(); return false; }
    const limit = Math.max(.012, .22 * palmWidth);
    if (this.offset !== null && dist(offset, this.offset) > limit) {
      if (this.pending === null || dist(offset, this.pending) > limit / 2) { this.pending = offset; return false; }
    }
    this.offset = offset; this.pending = null;
    return true;
  }
}

export function isPointingPose(points, curls, aspect) {
  const [index, ...others] = curls;
  if (index >= 1.4 && Math.max(...others) <= 1.3) return true;
  const straightness = base => {
    const chain = points.slice(base, base + 4).map(p => [p.x, p.y * aspect, p.z ?? 0]);
    let length = 0;
    for (let i = 1; i < chain.length; i++) length += dist(chain[i - 1], chain[i]);
    return length > .001 ? dist(chain[0], chain.at(-1)) / length : NaN;
  };
  return straightness(5) >= .90 && others.every((curl, i) => curl <= 1.3 || straightness([9, 13, 17][i]) < .88);
}

/** Thumb tip distance to the index finger's first segment (#5-#6), in palm widths. */
export function triggerRatio(points, aspect) {
  const p = i => [points[i].x, points[i].y * aspect, points[i].z ?? 0];
  const a = p(5), b = p(6), thumb = p(4), width = dist(p(5), p(17));
  const seg = b.map((v, i) => v - a[i]), length2 = seg.reduce((s, v) => s + v * v, 0);
  if (!Number.isFinite(width) || width < .025 || !Number.isFinite(length2) || length2 < 1e-8) return NaN;
  const t = Math.max(0, Math.min(1, seg.reduce((s, v, i) => s + (thumb[i] - a[i]) * v, 0) / length2));
  const ratio = dist(thumb, a.map((v, i) => v + t * seg[i])) / width;
  return Number.isFinite(ratio) ? ratio : NaN;
}

/** Option A click state machine. Every threshold is a starting guess to tune on a real webcam. */
export class ThumbTrigger {
  static DEFAULTS = Object.freeze({trigger_press_ratio:.28, trigger_release_ratio:.40, trigger_approach_ratio:.50,
    trigger_long_press:.55, trigger_drag_move:.35, trigger_rewind:.10});
  constructor(config = {}) { this.settings = {...ThumbTrigger.DEFAULTS}; this.configure(config); this.history = []; this.reset(); }
  configure(config) { for (const key of Object.keys(ThumbTrigger.DEFAULTS)) this.settings[key] = finiteSetting(config, key, this.settings[key]); }
  reset() { this.state = 'idle'; this.pressAt = null; this.pressTip = null; this.fired = false; this.warp = null; this.ratio = NaN; }
  record(now, screenPos) {
    this.history.push([now, screenPos]);
    const keep = now - Math.max(.3, 2 * this.settings.trigger_rewind);
    while (this.history.length > 2 && this.history[1][0] < keep) this.history.shift();
  }
  rewound(now) {
    const target = now - this.settings.trigger_rewind;
    let best = null;
    for (const [when, pos] of this.history) if (when <= target) best = pos;
    return best ?? (this.history.length ? this.history[0][1] : null);
  }
  update(now, points, aspect, {pointing}) {
    const s = this.settings;
    this.warp = null;
    const ratio = this.ratio = triggerRatio(points, aspect);
    const tip = points[8];
    const width = Math.hypot(points[5].x - points[17].x, (points[5].y - points[17].y) * aspect);
    if (!Number.isFinite(ratio) || width < .025) {
      const dragging = this.state === 'drag';
      this.reset();
      return dragging ? 'pointer_wait' : (pointing ? 'move_cursor' : 'pointer_wait');
    }
    if (this.state === 'press' || this.state === 'drag') {
      if (ratio >= s.trigger_release_ratio) {
        const action = this.state === 'press' && !this.fired ? 'left_click' : 'pointer_wait';
        this.reset();
        return action;
      }
      if (this.state === 'drag') return 'drag_hold';
      const moved = dist([tip.x, tip.y * aspect], this.pressTip) / width;
      if (!this.fired && moved >= s.trigger_drag_move) { this.state = 'drag'; return 'drag_hold'; }
      if (!this.fired && now - this.pressAt >= s.trigger_long_press) { this.fired = true; return 'right_click'; }
      return 'pointer_wait';
    }
    if (ratio <= s.trigger_press_ratio) {
      this.state = 'press'; this.pressAt = now; this.pressTip = [tip.x, tip.y * aspect]; this.fired = false;
      this.warp = this.rewound(now);
      return 'pointer_wait';
    }
    if (ratio <= s.trigger_approach_ratio) { this.state = 'approach'; return 'pointer_wait'; }
    this.state = 'idle';
    return pointing ? 'move_cursor' : 'pointer_wait';
  }
}

/** Keep "move cursor" while the anchor moves, until it has been still `pointer_move_latch` s. */
export class MoveLatch {
  static DEFAULTS = Object.freeze({pointer_move_latch:.75, pointer_move_speed:.20, pointer_still_speed:.08});
  constructor(window = .10, config = {}) { this.window = window; this.settings = {...MoveLatch.DEFAULTS}; this.configure(config); this.reset(); }
  configure(config) { for (const key of Object.keys(MoveLatch.DEFAULTS)) this.settings[key] = finiteSetting(config, key, this.settings[key]); }
  reset() { this.latched = false; this.lastMotion = null; this.samples = []; this.speed = 0; }
  update(now, tip, aspect) {
    const s = this.settings, point = [Number(tip[0]), Number(tip[1]) * aspect];
    if (!finite(...point)) return this.expire(now);
    this.samples.push([now, point]);
    while (this.samples.length > 2 && this.samples[1][0] <= now - this.window) this.samples.shift();
    const [then, old] = this.samples[0];
    this.speed = now - then >= this.window / 2 ? dist(point, old) / (now - then) : 0;
    if (this.speed >= s.pointer_move_speed) { this.latched = true; this.lastMotion = now; }
    else if (this.latched && this.speed >= s.pointer_still_speed) this.lastMotion = now;
    return this.expire(now);
  }
  expire(now) {
    if (this.latched && now - this.lastMotion >= this.settings.pointer_move_latch) this.latched = false;
    return this.latched;
  }
}

/**
 * One hand's landmarks in, one pointer intent out. Times are seconds; landmarks are 21
 * {x, y, z} in camera space after mirroring. Settings: {clickStyle, anchor, trackingOnly, axes, config}.
 */
export class PointerEngine {
  constructor() {
    this.guard = new IndexTipGuard(); this.trigger = new ThumbTrigger(); this.latch = new MoveLatch();
    this.clickArmed = false;
  }
  reset() { this.resetClicks(); this.clickArmed = false; this.latch.reset(); }
  frameMissing() { this.guard.reset(); this.trigger.reset(); }
  resetClicks() { this.guard.reset(); this.trigger.reset(); }
  recordCursor(t, screenPos) { this.trigger.record(t, screenPos); }

  step(t, points, aspect, settings = {}, {active = true} = {}) {
    const clickStyle = POINTER_STYLES.includes(settings.clickStyle) ? settings.clickStyle : 'trigger';
    const clicksOff = Boolean(settings.trackingOnly) || clickStyle === 'off';
    const config = settings.config ?? {};
    if (!points || !active) { this.resetClicks(); this.clickArmed = false; }
    if (!active) this.latch.reset();
    else if (!points) this.latch.expire(t);
    if (!points) return null;

    const anchorPalm = settings.anchor === 'palm';
    const tip = points[INDEX_TIP];
    const preview = anchorPalm ? palmAnchorPoint(points, finiteSetting(config, 'cursor_anchor_ratio', .5)) : [tip.x, tip.y];
    const frame = {preview, contacts:thumbContacts(thumbDistances(points, aspect)), action:null, cursor:null, pointer:null,
      source:anchorPalm ? 'palm' : 'index', warp:null, latched:false, speed:0, armed:false, ratio:NaN};
    if (!active) return frame;

    const triggerMode = clickStyle === 'trigger' && !clicksOff;
    const pointing = isPointingPose(points, fingerCurlRatios(points), aspect);
    const idleMove = pointing ? 'move_cursor' : 'pointer_wait';
    this.latch.configure(config);
    const pressing = triggerMode && (this.trigger.state === 'press' || this.trigger.state === 'drag');
    const latched = this.latch.update(t, preview, aspect);

    let action;
    if (clicksOff) {
      this.trigger.reset(); this.clickArmed = true; action = 'move_cursor';
    } else if (latched && !pressing) {
      this.trigger.reset(); this.clickArmed = false; action = 'move_cursor';
    } else {
      this.trigger.configure(config);
      action = this.trigger.update(t, points, aspect, {pointing});
      if (!this.clickArmed) {
        // A thumb already at the index side when the latch ends is a rest, not a press.
        if (this.trigger.state === 'idle') this.clickArmed = true;
        else { this.trigger.reset(); action = idleMove; }
      }
    }
    if (action === 'move_cursor' && !anchorPalm) { if (!this.guard.accept(points, aspect)) action = 'pointer_wait'; }
    else this.guard.reset();

    const pointer = calibratedPointer(preview, settings.axes ?? null, aspect);
    Object.assign(frame, {action, pointer, cursor:action === 'move_cursor' || action === 'drag_hold' ? pointer : null,
      warp:triggerMode ? this.trigger.warp : null, latched, speed:this.latch.speed, armed:this.clickArmed, ratio:this.trigger.ratio});
    return frame;
  }
}
