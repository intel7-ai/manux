// Index-pointer preset for the Electron app: PointerEngine (ported from the Python Qt app) plus
// the Qt cursor layer (One Euro tremor filter, speed-based gain), emitting the same events and
// state shape as AirMouseController so MouseWorkspace, the native bridge and the overlay accept it.
import {PointerEngine, POINTER_STYLES} from './pointer-engine.js';

const MOVE_LIMIT = 100;      // px per move event (desktop/mouse-bridge.mjs)
const MAX_MOVE_EVENTS = 3;   // a batch also carries a rewind and button events; the bridge allows 8

function alpha(cutoff, dt) { const tau = 1 / (2 * Math.PI * Math.max(1e-6, cutoff)); return 1 / (1 + tau / Math.max(1e-6, dt)); }

class OneEuro {
  constructor(minCutoff, beta, dCutoff = 1) { Object.assign(this, {minCutoff, beta, dCutoff}); this.reset(); }
  reset() { this.x = null; this.dx = 0; this.t = null; }
  filter(x, t) {
    if (this.x === null || this.t === null || t <= this.t) { if (this.x === null) { this.x = x; this.t = t; } return this.x; }
    const dt = Math.min(.2, t - this.t); this.t = t;
    this.dx += alpha(this.dCutoff, dt) * ((x - this.x) / dt - this.dx);
    this.x += alpha(this.minCutoff + this.beta * Math.abs(this.dx), dt) * (x - this.x);
    return this.x;
  }
}

/** 2D One Euro filter followed by a deadband (hand-mouse/src/filters.py TremorStabilizer). */
class TremorStabilizer {
  constructor({minCutoff = 2.0, beta = 7.0, deadzone = .002} = {}) {
    this.fx = new OneEuro(minCutoff, beta); this.fy = new OneEuro(minCutoff, beta); this.deadzone = deadzone; this.out = null;
  }
  reset() { this.fx.reset(); this.fy.reset(); this.out = null; }
  filter([px, py], t) {
    const x = this.fx.filter(px, t), y = this.fy.filter(py, t);
    if (!this.out) return (this.out = [x, y]);
    const [ox, oy] = this.out, d = Math.hypot(x - ox, y - oy);
    if (d > this.deadzone) { const k = (d - this.deadzone) / d; this.out = [ox + (x - ox) * k, oy + (y - oy) * k]; }
    return this.out;
  }
}

const MODES = {move_cursor:'move', drag_hold:'left', left_click:'left', right_click:'right'};

export const POINTER_PRESETS = Object.freeze({'pointer-trigger':'trigger', 'pointer-off':'off'});

export class IndexPointerController {
  constructor({sensitivity = 1800, clickStyle = 'trigger', anchor = 'index', screenWidth = globalThis.screen?.width ?? 1920} = {}) {
    if (!Number.isFinite(sensitivity) || sensitivity < 100 || sensitivity > 6000) throw new RangeError('Invalid pointer sensitivity.');
    if (!POINTER_STYLES.includes(clickStyle) || !['index', 'palm'].includes(anchor)) throw new RangeError('Invalid pointer preset.');
    if (!Number.isFinite(screenWidth) || screenWidth <= 0) throw new RangeError('Invalid screen width.');
    Object.assign(this, {gainScale:sensitivity / 1800, clickStyle, anchor, screenWidth});
    this.engine = new PointerEngine();
    this.tremor = new TremorStabilizer();
    this.reset();
  }

  state(phase, events = [], progress = 0, mode = this.mode) {
    return {mode, phase, progress:Math.max(0, Math.min(1, progress)), events};
  }

  reset() {
    this.engine.reset(); this.tremor.reset();
    this.lastTime = null; this.enabled = false; this.mode = 'idle';
    this.pos = [0, 0]; this.remainder = [0, 0]; this.anchorPoint = null; this.source = null; this.relTime = null;
    this.leftDown = false;
    return this.state('disabled', [{type:'release'}], 0, 'idle');
  }

  update({landmarks, now, enabled, quality = true, width = 1280, height = 720} = {}) {
    // A repeated or older timestamp is not fresh inference: change nothing, emit nothing.
    if (this.lastTime !== null && !(now > this.lastTime)) return this.state('stale');
    if (!enabled) return this.enabled ? this.reset() : this.state('disabled', [], 0, 'idle');
    this.enabled = true;
    this.lastTime = now;
    const t = now / 1000, aspect = height / width;
    const points = quality && Array.isArray(landmarks) && landmarks.length >= 21
      ? landmarks.map(p => ({x:1 - p.x, y:p.y, z:p.z ?? 0}))   // mirror: the user's right is screen right
      : null;
    const frame = this.engine.step(t, points, aspect, {clickStyle:this.clickStyle, anchor:this.anchor}, {active:true});
    const events = [];
    if (!frame) {
      if (this.leftDown) { events.push({type:'button', button:'left', down:false}); this.leftDown = false; }
      this.stopFollowing(); this.mode = 'idle';
      return this.state('invalid', events);
    }
    if (frame.warp) this.moveBy(frame.warp[0] - this.pos[0], frame.warp[1] - this.pos[1], events, true);
    const action = frame.action;
    if (this.leftDown && action !== 'drag_hold') { events.push({type:'button', button:'left', down:false}); this.leftDown = false; }
    if (frame.cursor && (action === 'move_cursor' || action === 'drag_hold')) {
      if (action === 'drag_hold' && !this.leftDown) { events.push({type:'button', button:'left', down:true}); this.leftDown = true; }
      this.follow(frame.cursor, frame.source, t, aspect, events);
    } else this.stopFollowing();
    if (action === 'left_click') events.push({type:'button', button:'left', down:true}, {type:'button', button:'left', down:false});
    if (action === 'right_click') events.push({type:'button', button:'right', down:true}, {type:'button', button:'right', down:false});
    this.engine.recordCursor(t, [Math.round(this.pos[0]), Math.round(this.pos[1])]);
    // The native bridge rejects batches over 8 events; drop surplus moves, never button events.
    while (events.length > 8) events.splice(events.findIndex(e => e.type === 'move'), 1);
    this.mode = MODES[action] ?? 'idle';
    const s = this.engine.trigger.settings;
    const progress = Number.isFinite(frame.ratio) ? (s.trigger_approach_ratio - frame.ratio) / (s.trigger_approach_ratio - s.trigger_press_ratio) : 0;
    const phase = frame.latched ? 'latched' : action === 'pointer_wait' ? 'holding' : 'tracking';
    return this.state(phase, events, this.clickStyle === 'trigger' ? progress : 0);
  }

  stopFollowing() { this.anchorPoint = null; this.tremor.reset(); }

  /** hand-mouse/src/mouse_controller.py _relative_step with the index-pointer gains. */
  follow(point, source, t, aspect, events) {
    if (source !== this.source) { this.stopFollowing(); this.source = source; }
    const [sx, sy] = this.tremor.filter(point, t);
    if (!this.anchorPoint) { this.anchorPoint = [sx, sy]; this.relTime = t; return; }
    const [ax, ay] = this.anchorPoint;
    this.anchorPoint = [sx, sy];
    const dt = Math.max(1e-3, Math.min(.1, t - this.relTime)); this.relTime = t;
    const dx = sx - ax, dy = (sy - ay) * aspect;
    const [gMin, gMax] = source === 'palm' ? [.85, 2.5] : [1.5, 5.0];
    const k = Math.max(0, Math.min(1, (Math.hypot(dx, dy) / dt - .08) / (.40 - .08)));
    const gain = (gMin + (gMax - gMin) * k) * this.gainScale;
    this.moveBy(dx * this.screenWidth * gain, dy * this.screenWidth * gain, events);
  }

  moveBy(dx, dy, events, exact = false) {
    let x = dx + (exact ? 0 : this.remainder[0]), y = dy + (exact ? 0 : this.remainder[1]);
    const limit = MOVE_LIMIT * MAX_MOVE_EVENTS;
    const scale = Math.max(1, Math.abs(x) / limit, Math.abs(y) / limit);
    x /= scale; y /= scale;
    let ix = Math.trunc(x), iy = Math.trunc(y);
    if (!exact) this.remainder = [x - ix, y - iy];
    this.pos = [this.pos[0] + ix, this.pos[1] + iy];
    while (ix !== 0 || iy !== 0) {
      const stepX = Math.max(-MOVE_LIMIT, Math.min(MOVE_LIMIT, ix)), stepY = Math.max(-MOVE_LIMIT, Math.min(MOVE_LIMIT, iy));
      events.push({type:'move', dx:stepX, dy:stepY});
      ix -= stepX; iy -= stepY;
    }
  }
}
