// One Euro smoothing of the 21 hand landmarks (x, y, z) before any judgement or drawing.
// The Qt app only smoothed the cursor point, so pinch/trigger distances still jittered; filtering
// every joint keeps those distances steady while fast motion stays responsive (beta).
function alpha(cutoff, dt) { const tau = 1 / (2 * Math.PI * Math.max(1e-6, cutoff)); return 1 / (1 + tau / Math.max(1e-6, dt)); }

export const LANDMARK_SMOOTHING = Object.freeze({minCutoff:1.5, beta:8, dCutoff:1, resetAfter:.25});

class HandFilter {
  constructor(settings) { this.settings = settings; this.values = null; this.speeds = null; this.t = null; }
  filter(points, t) {
    const flat = points.flatMap(p => [p.x, p.y, p.z ?? 0]);
    if (this.values === null || t <= this.t || t - this.t > this.settings.resetAfter) {
      this.values = flat; this.speeds = flat.map(() => 0); this.t = t;
      return points.map(p => ({x:p.x, y:p.y, z:p.z ?? 0}));
    }
    const dt = Math.min(.2, t - this.t); this.t = t;
    const aD = alpha(this.settings.dCutoff, dt);
    for (let i = 0; i < flat.length; i++) {
      this.speeds[i] += aD * ((flat[i] - this.values[i]) / dt - this.speeds[i]);
      this.values[i] += alpha(this.settings.minCutoff + this.settings.beta * Math.abs(this.speeds[i]), dt) * (flat[i] - this.values[i]);
    }
    return points.map((_, j) => ({x:this.values[j * 3], y:this.values[j * 3 + 1], z:this.values[j * 3 + 2]}));
  }
}

/** Keeps one filter per key (e.g. 'left' / 'right'), so two tracked hands never share history. */
export class LandmarkSmoother {
  constructor(settings = {}) { this.settings = {...LANDMARK_SMOOTHING, ...settings}; this.hands = new Map(); }
  reset(key) { if (key === undefined) this.hands.clear(); else this.hands.delete(key); }
  /** t in seconds. Malformed input passes through unchanged and resets that hand. */
  smooth(key, points, t) {
    if (!Array.isArray(points) || points.length < 21 || !points.every(p => Number.isFinite(p?.x) && Number.isFinite(p?.y)) || !Number.isFinite(t)) {
      this.reset(key); return points;
    }
    if (!this.hands.has(key)) this.hands.set(key, new HandFilter(this.settings));
    return this.hands.get(key).filter(points, t);
  }
}
