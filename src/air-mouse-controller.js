const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
const MAX_GAP_MS = 250;
const RELEASE_MS = 120;
const MIN_OBSERVATIONS = 3;

function straight(a, b, c) {
  const u = {x:a.x - b.x, y:a.y - b.y, z:a.z - b.z};
  const v = {x:c.x - b.x, y:c.y - b.y, z:c.z - b.z};
  const length = Math.hypot(u.x, u.y, u.z) * Math.hypot(v.x, v.y, v.z);
  return length > 1e-8 && (u.x * v.x + u.y * v.y + u.z * v.z) / length < -0.82;
}

function observation(landmarks, width, height, previousPinch) {
  if (!Array.isArray(landmarks) || landmarks.length !== 21 || !Number.isFinite(width) ||
      !Number.isFinite(height) || width <= 0 || height <= 0) return null;
  if (!landmarks.every(p => p && Number.isFinite(p.x) && Number.isFinite(p.y) &&
      p.x >= 0 && p.x <= 1 && p.y >= 0 && p.y <= 1 &&
      (p.z === undefined || (Number.isFinite(p.z) && Math.abs(p.z) <= 3)))) return null;
  // MediaPipe's z uses approximately the same scale as x. All axes below use video-width units.
  const points = landmarks.map(p => ({x:p.x, y:p.y * height / width, z:p.z ?? 0}));
  const palm = Math.max(distance(points[0], points[9]), distance(points[5], points[17]));
  if (palm < 0.025 || palm > 0.8) return null;
  const extended = base => straight(points[base], points[base + 1], points[base + 3]) &&
    distance(points[base + 3], points[0]) > distance(points[base + 1], points[0]) * 1.12;
  const index = extended(5), middle = extended(9), ring = extended(13), pinky = extended(17);
  const fingerExtensions = new Map([[5,index],[9,middle],[13,ring],[17,pinky]]);
  const ratio = tip => distance(points[4], points[tip]) / palm;
  // Reject a thumb resting against a folded knuckle: pinch tips must extend beyond the palm.
  const reachable = (tip, base) => distance(points[tip], points[0]) > palm * 0.9 &&
    distance(points[tip], points[base]) > palm * 0.3;
  const fingers = [
    {mode:'left', tip:8, base:5},
    {mode:'right', tip:12, base:9},
    {mode:'scroll', tip:16, base:13},
  ].map(f => ({...f, ratio:ratio(f.tip), reachable:reachable(f.tip, f.base) &&
    [...fingerExtensions].filter(([base, open]) => base !== f.base && open).length >= 2}));
  const pinches = fingers.filter(f => f.reachable && f.ratio < (previousPinch === f.mode ? 0.4 : 0.27));
  const ambiguous = pinches.length > 1 || (pinches.length === 1 && fingers.some(f =>
    f.mode !== pinches[0].mode && f.reachable && f.ratio < 0.4));
  const pinch = ambiguous ? null : pinches[0]?.mode ?? null;
  const mode = ambiguous ? 'idle' : pinch || ([index,middle,ring,pinky].filter(Boolean).length >= 3 ? 'move' : 'idle');
  const anchor = [0,5,9,13,17].reduce((p, i) => ({x:p.x + points[i].x / 5, y:p.y + points[i].y / 5}), {x:0,y:0});
  return {mode, pinch, anchor, unpinched:!ambiguous && fingers.every(f => !f.reachable || f.ratio >= 0.42)};
}

/** Pure landmark-to-input controller. It never sends OS events or stores camera data. */
export class AirMouseController {
  constructor({sensitivity = 1800, smoothing = 0.45, holdMs = 160} = {}) {
    if (!Number.isFinite(sensitivity) || sensitivity < 100 || sensitivity > 6000 ||
        !Number.isFinite(smoothing) || smoothing <= 0 || smoothing > 1 ||
        !Number.isFinite(holdMs) || holdMs < 0 || holdMs > 2000) {
      throw new RangeError('Invalid air mouse sensitivity, smoothing, or hold time.');
    }
    this.sensitivity = sensitivity;
    this.smoothing = smoothing;
    this.holdMs = holdMs;
    this.reset();
  }

  state(phase, events = [], progress = 0) {
    return {mode:this.mode, phase, progress:clamp(progress, 0, 1), events};
  }

  reset() {
    this.enabled = false;
    this.lastTime = null;
    this.lastHand = null;
    this.mode = 'idle';
    this.candidate = null;
    this.candidateSince = null;
    this.observations = 0;
    this.pinch = null;
    this.anchor = null;
    this.filtered = null;
    this.remaining = {x:0,y:0};
    this.leftDown = false;
    this.armed = false;
    this.releaseSince = null;
    return this.state('disabled', [{type:'release'}]);
  }

  interrupt(phase, disarm = true) {
    const events = this.leftDown ? [{type:'button', button:'left', down:false}, {type:'release'}] : [];
    this.leftDown = false;
    this.mode = 'idle';
    this.candidate = null;
    this.candidateSince = null;
    this.observations = 0;
    this.anchor = this.filtered = null;
    this.remaining = {x:0,y:0};
    this.pinch = null;
    this.releaseSince = null;
    if (disarm) this.armed = false;
    return this.state(phase, events);
  }

  update({landmarks, now, enabled, quality = true, handedness = null, width = 1280, height = 720} = {}) {
    if (!enabled) {
      if (this.enabled) return this.reset();
      return this.state('disabled');
    }
    this.enabled = true;
    if (!Number.isFinite(now)) return this.interrupt('invalid');
    if (this.lastTime !== null && now <= this.lastTime) return this.state('stale');
    const gap = this.lastTime !== null && now - this.lastTime > MAX_GAP_MS;
    this.lastTime = now;
    const observed = quality === true ? observation(landmarks, width, height, this.pinch) : null;
    if (!observed) {
      this.lastHand = null;
      return this.interrupt('invalid');
    }
    const changedHand = handedness && this.lastHand && handedness !== this.lastHand;
    this.lastHand = handedness;
    if (gap || changedHand) return this.interrupt('tracking-reset');

    this.pinch = observed.pinch;
    // Neither absent tracking nor an ambiguous pinch is a release gesture.
    if (observed.unpinched) {
      this.releaseSince ??= now;
      if (now - this.releaseSince >= RELEASE_MS) this.armed = true;
    } else this.releaseSince = null;

    const events = [];
    if (this.leftDown && observed.mode !== 'left') {
      events.push({type:'button',button:'left',down:false});
      this.leftDown = false;
      this.armed = false;
      this.releaseSince = observed.unpinched ? now : null;
    }
    if (observed.mode === 'idle') {
      const releaseSince = this.releaseSince;
      const result = this.interrupt('ready', false);
      // Keep the confirmed release timer while a valid resting hand is visible.
      this.releaseSince = releaseSince;
      result.events.unshift(...events);
      return result;
    }

    if (observed.pinch && !this.armed && this.mode !== observed.mode) {
      const result = this.interrupt('release-required', false);
      result.events.unshift(...events);
      return result;
    }
    if (this.candidate !== observed.mode) {
      // A deliberate action already confirmed this hand. Release back to the base
      // pose without another dwell, but discard motion during the pose transition.
      const recoveringMove = observed.mode === 'move' && ['left','right','scroll'].includes(this.mode);
      this.mode = recoveringMove ? 'move' : 'idle';
      this.candidate = observed.mode;
      this.candidateSince = now;
      this.observations = recoveringMove ? MIN_OBSERVATIONS : 1;
      this.anchor = this.filtered = observed.anchor;
      this.remaining = {x:0,y:0};
      return this.state(recoveringMove ? 'active' : 'holding', events, recoveringMove ? 1 : 0);
    }
    this.observations++;
    if (this.mode !== observed.mode) {
      const progress = this.holdMs === 0 ? 1 : (now - this.candidateSince) / this.holdMs;
      this.anchor = this.filtered = observed.anchor;
      if (progress < 1 || this.observations < MIN_OBSERVATIONS) return this.state('holding', events, progress);
      this.mode = observed.mode;
      if (this.mode === 'left') {
        events.push({type:'button', button:'left', down:true});
        this.leftDown = true;
        this.armed = false;
      } else if (this.mode === 'right') {
        events.push({type:'button', button:'right', down:true}, {type:'button', button:'right', down:false});
        this.armed = false;
      }
      return this.state('active', events, 1);
    }
    // Right pinch sends its one click on entry, then keeps ordinary palm movement.
    const delta = {x:observed.anchor.x - this.anchor.x, y:observed.anchor.y - this.anchor.y};
    this.anchor = observed.anchor;
    if (Math.hypot(delta.x, delta.y) > 0.14) {
      const result = this.interrupt('tracking-reset');
      result.events.unshift(...events);
      return result;
    }
    const next = {
      x:this.filtered.x + (observed.anchor.x - this.filtered.x) * this.smoothing,
      y:this.filtered.y + (observed.anchor.y - this.filtered.y) * this.smoothing,
    };
    const dx = -(next.x - this.filtered.x) * this.sensitivity;
    const dy = (next.y - this.filtered.y) * this.sensitivity;
    this.filtered = next;
    if (this.mode === 'scroll') {
      this.remaining.y += dy * 2;
      const wheel = clamp(Math.trunc(this.remaining.y / 6) * 6, -120, 120);
      if (wheel) {
        events.push({type:'scroll',dy:wheel});
        this.remaining.y = clamp(this.remaining.y - wheel, -5.999, 5.999);
      }
    } else {
      this.remaining.x += dx;
      this.remaining.y += dy;
      const px = clamp(Math.trunc(this.remaining.x), -100, 100);
      const py = clamp(Math.trunc(this.remaining.y), -100, 100);
      if (px || py) {
        events.push({type:'move',dx:px,dy:py});
        this.remaining.x = clamp(this.remaining.x - px, -0.999, 0.999);
        this.remaining.y = clamp(this.remaining.y - py, -0.999, 0.999);
      }
    }
    return this.state('active', events, 1);
  }
}
