// Port of hand-mouse/src/hand_selection.py: pick one physical hand for control.
// Frames here are not mirrored before MediaPipe, which matches the Python call with mirrored=False:
// the reported label is used as the physical side. Confirm with a real hand during onboarding.
export const HAND_LABELS = Object.freeze({right:'오른손', left:'왼손'});

/** 'right' | 'left' | '' for one detection {handedness:'Left'|'Right', score}. */
export function physicalSide(detection, mirrored = false) {
  const side = detection?.handedness;
  if ((side !== 'Left' && side !== 'Right') || !(detection.score >= .75)) return '';
  const physical = mirrored ? (side === 'Left' ? 'Right' : 'Left') : side;
  return physical.toLowerCase();
}

/** The one detection on the chosen side, or null (none, or more than one). */
export function selectedDetection(detections, side, mirrored = false) {
  const matches = (detections ?? []).filter(hand => side && physicalSide(hand, mirrored) === side);
  return matches.length === 1 ? matches[0] : null;
}

/** One hand held steadily for `holdSeconds` (and at least 5 frames) chooses that side. */
export class HandSelection {
  constructor(holdSeconds = 5) { this.holdSeconds = holdSeconds; this.reset(); }
  reset() { this.side = ''; this.since = null; this.lastAt = null; this.frames = 0; }
  secondsRemaining(now) { return this.since === null ? this.holdSeconds : Math.max(0, this.holdSeconds - (now - this.since)); }
  observe(detections, now, mirrored = false) {
    const side = detections?.length === 1 ? physicalSide(detections[0], mirrored) : '';
    if (!side) { this.reset(); return ''; }
    if (side !== this.side || this.lastAt === null || now - this.lastAt > .25) {
      this.reset(); this.side = side; this.since = now;
    }
    if (this.lastAt === null || now > this.lastAt) { this.lastAt = now; this.frames += 1; }
    return this.frames >= 5 && this.secondsRemaining(now) === 0 ? side : '';
  }
}
