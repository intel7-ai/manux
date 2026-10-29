/** Commands require recent agreeing observations; held seek repetition is opt-in. */
export const GESTURE_ACTIONS = Object.freeze({
  Open_Palm: 'pause', Thumb_Up: 'play', Victory: 'forward', Closed_Fist: 'backward',
});

export const GESTURE_LABELS = Object.freeze({
  Open_Palm: '손바닥 · 일시정지', Thumb_Up: '엄지척 · 재생',
  Victory: 'V · 10초 앞으로', Closed_Fist: '주먹 · 10초 뒤로',
});

function duration(value, name) {
  if (!Number.isFinite(value) || value < 0) {
    throw new RangeError(`${name} must be a finite, non-negative number.`);
  }
  return value;
}

function confidence(value, name) {
  if (!Number.isFinite(value) || value < 0 || value > 1) {
    throw new RangeError(`${name} must be between 0 and 1.`);
  }
  return value;
}

export class GestureController {
  constructor({
    holdMs = 300, releaseMs = 350, cooldownMs = 350, minConfidence = 0.70,
    sustainConfidence = Math.max(0, minConfidence - 0.15),
    uncertaintyMs = 150, maxSampleGapMs = 250, missingReleaseMs = 500,
    windowMs = Math.max(450, holdMs + 150), minSamples = 3, supportRatio = 0.65,
    repeatEnabled = false, repeatIntervalMs = 1000,
  } = {}) {
    this.holdMs = duration(holdMs, 'holdMs');
    this.releaseMs = duration(releaseMs, 'releaseMs');
    this.cooldownMs = duration(cooldownMs, 'cooldownMs');
    this.minConfidence = confidence(minConfidence, 'minConfidence');
    this.sustainConfidence = confidence(sustainConfidence, 'sustainConfidence');
    this.uncertaintyMs = duration(uncertaintyMs, 'uncertaintyMs');
    this.maxSampleGapMs = duration(maxSampleGapMs, 'maxSampleGapMs');
    this.missingReleaseMs = duration(missingReleaseMs, 'missingReleaseMs');
    this.windowMs = duration(windowMs, 'windowMs');
    this.supportRatio = confidence(supportRatio, 'supportRatio');
    this.repeatEnabled = repeatEnabled === true;
    this.repeatIntervalMs = duration(repeatIntervalMs, 'repeatIntervalMs');
    if (sustainConfidence > minConfidence || windowMs < holdMs
      || missingReleaseMs < 500 || maxSampleGapMs === 0
      || repeatIntervalMs === 0
      || !Number.isInteger(minSamples) || minSamples < 3) {
      throw new RangeError('Require sustain <= entry, window >= hold, missing release >= 500 ms, positive sample/repeat intervals, and at least 3 samples.');
    }
    this.minSamples = minSamples;
    this.reset();
  }

  /** Camera stop, disable, visibility loss and clock errors discard all state. */
  reset() {
    this.armed = true;
    this.lastGesture = null;
    this.lastFireAt = null;
    this.lastNow = null;
    this.neutralSince = null;
    this.missingSince = null;
    this.clearRepeat();
    this.clearEvidence();
  }

  clearRepeat() {
    this.repeatSince = null;
    this.repeatSamples = 0;
    this.repeatLastSampleAt = null;
  }

  startRepeat(now) {
    this.repeatSince = now;
    this.repeatSamples = 1;
    this.repeatLastSampleAt = now;
  }

  clearEvidence() {
    this.observations = [];
    this.candidate = null;
    this.candidateSince = null;
  }

  rearm() {
    this.lastGesture = null;
    this.armed = true;
    this.clearRepeat();
  }

  snapshot(phase, { action = null, candidate = this.candidate, progress = 0 } = {}) {
    return { action, candidate, progress, armed: this.armed, phase };
  }

  /** Recent support may bridge a short uncertain interval, never a long one. */
  evidenceFor(gesture) {
    let agreeing = [];
    let uncertainSince = null;
    for (const observation of this.observations) {
      if (observation.gesture === gesture) {
        if (uncertainSince !== null && observation.now - uncertainSince >= this.uncertaintyMs) {
          agreeing = [];
        }
        uncertainSince = null;
        // A weak first observation cannot start a hold. Once entered, the
        // lower sustain threshold absorbs normal confidence jitter.
        if (agreeing.length || observation.score >= this.minConfidence) agreeing.push(observation);
      } else if (agreeing.length) {
        uncertainSince ??= observation.now;
        if (observation.now - uncertainSince >= this.uncertaintyMs) agreeing = [];
      }
    }
    if (!agreeing.length) return null;
    const since = agreeing[0].now;
    const total = this.observations.filter((observation) => observation.now >= since).length;
    return { gesture, since, count: agreeing.length, support: agreeing.length / total };
  }

  /**
   * now: monotonic milliseconds (performance.now()). quality: usable landmarks.
   * neutral: an explicitly recognized resting pose; low confidence is NOT rest.
   * A fired pose remains latched through uncertainty. A different confirmed pose
   * may fire directly. Repeating the same pose needs sustained neutral/no-hand,
   * unless seek repetition was explicitly enabled. Seek repetition retains its
   * timer at sustain confidence, but only fires on an entry-confident frame.
   * Below-sustain confidence or any other interruption restarts both the hold
   * and repeat interval; play/pause are always one-shot.
   * armed means the same pose may be used again; it does not block other poses.
   * Phases: ready, holding, uncertain, cooldown, fired, latched, releasing,
   * repeating, disabled, invalid-time, clock-reset. Each update emits at most one action.
   */
  update({
    gesture = null, score = 0, now, enabled = true, handPresent = true,
    neutral = false, quality = true,
  } = {}) {
    if (!Number.isFinite(now)) {
      this.reset();
      return this.snapshot('invalid-time');
    }
    if (!enabled) {
      this.reset();
      this.lastNow = now;
      return this.snapshot('disabled');
    }
    if (this.lastNow !== null && now < this.lastNow) {
      this.reset();
      this.lastNow = now;
      return this.snapshot('clock-reset');
    }
    if (this.lastNow !== null && now - this.lastNow > this.maxSampleGapMs) {
      // Unobserved wall time must not count as a hold or a release. Keep the
      // fired-pose latch: a stalled camera must never cause seek auto-repeat.
      this.clearEvidence();
      this.clearRepeat();
      this.neutralSince = null;
      this.missingSince = null;
    }
    this.lastNow = now;
    const validScore = Number.isFinite(score) && score >= 0 && score <= 1;
    const coolingDown = this.lastFireAt !== null && now - this.lastFireAt < this.cooldownMs;
    const repeatAgrees = this.repeatEnabled
      && ['forward', 'backward'].includes(GESTURE_ACTIONS[this.lastGesture])
      && handPresent && quality && !neutral && validScore
      && gesture === this.lastGesture && score >= this.sustainConfidence;
    // This latch is separate from command rearming. A single uncertain frame
    // cancels repetition without permitting another ordinary same-pose action.
    if (!repeatAgrees) this.clearRepeat();

    if (!handPresent) {
      this.clearEvidence();
      this.neutralSince = null;
      this.missingSince ??= now;
      if (now - this.missingSince >= this.missingReleaseMs) this.rearm();
      return this.snapshot(!this.armed ? 'releasing' : coolingDown ? 'cooldown' : 'ready');
    }
    this.missingSince = null;

    const recognizedNeutral = quality && (neutral
      || (gesture === 'None' && validScore && score >= this.minConfidence));
    if (recognizedNeutral) {
      this.clearEvidence();
      this.neutralSince ??= now;
      if (now - this.neutralSince >= this.releaseMs) this.rearm();
      return this.snapshot(!this.armed ? 'releasing' : coolingDown ? 'cooldown' : 'ready');
    }
    this.neutralSince = null;

    const supported = quality && validScore && Object.hasOwn(GESTURE_ACTIONS, gesture)
      && score >= this.sustainConfidence;
    const observation = { now, gesture: supported ? gesture : null, score };
    // Re-reading the same timestamp is not another independent camera sample.
    if (this.observations.at(-1)?.now === now) this.observations.pop();
    this.observations.push(observation);
    this.observations = this.observations.filter((item) => now - item.now <= this.windowMs);

    if (repeatAgrees) {
      if (this.repeatSince === null) this.startRepeat(now);
      else if (now !== this.repeatLastSampleAt) {
        this.repeatSamples = Math.min(this.minSamples, this.repeatSamples + 1);
        this.repeatLastSampleAt = now;
      }
      const requiredMs = Math.max(this.holdMs, this.repeatIntervalMs);
      const elapsed = now - this.repeatSince;
      this.candidate = gesture;
      this.candidateSince = this.repeatSince;
      if (elapsed >= requiredMs && this.repeatSamples >= this.minSamples
        && score >= this.minConfidence && !coolingDown) {
        this.lastFireAt = now;
        this.clearEvidence();
        this.startRepeat(now);
        return this.snapshot('fired', { action: GESTURE_ACTIONS[gesture], candidate: gesture, progress: 1 });
      }
      return this.snapshot('repeating', { progress: Math.min(1, elapsed / requiredMs) });
    }

    const candidates = Object.keys(GESTURE_ACTIONS)
      .filter((pose) => pose !== this.lastGesture)
      .map((pose) => this.evidenceFor(pose))
      .filter(Boolean)
      .sort((a, b) => b.count - a.count || Number(b.gesture === gesture) - Number(a.gesture === gesture));
    const evidence = candidates[0];
    this.candidate = evidence?.gesture ?? null;
    this.candidateSince = evidence?.since ?? null;
    if (!evidence) {
      return this.snapshot(supported && gesture === this.lastGesture ? 'latched' : 'uncertain');
    }

    const heldFor = now - evidence.since;
    const progress = this.holdMs === 0 ? 1 : Math.min(1, heldFor / this.holdMs);
    const freshAgreement = supported && gesture === evidence.gesture && score >= this.minConfidence;
    if (!freshAgreement) return this.snapshot('uncertain', { progress });
    if (coolingDown) return this.snapshot('cooldown', { progress });
    if (heldFor < this.holdMs || evidence.count < this.minSamples || evidence.support < this.supportRatio) {
      return this.snapshot('holding', { progress });
    }

    this.lastGesture = evidence.gesture;
    this.armed = false;
    this.lastFireAt = now;
    this.clearEvidence();
    if (this.repeatEnabled && ['forward', 'backward'].includes(GESTURE_ACTIONS[this.lastGesture])) {
      this.startRepeat(now);
    }
    return this.snapshot('fired', {
      action: GESTURE_ACTIONS[this.lastGesture], candidate: this.lastGesture, progress: 1,
    });
  }
}
