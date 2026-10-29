import {POSES, FEATURE_COUNT} from './personalization.js';
import {VIEWS, VIEW_LABELS, validOrientation, orientationFeedback} from './orientation.js';

const finite = Number.isFinite;
const activePhases = new Set(['prepare','calibrate','capture','verify-prepare','verify']);
const validVector = value => Array.isArray(value) && value.length === FEATURE_COUNT && Array.from(value).every(x => finite(x) && x >= 0 && x <= 1);
const clone = value => JSON.parse(JSON.stringify(value));
const emptyGroups = () => Object.fromEntries(POSES.map(pose => [pose,Object.fromEntries(VIEWS.map(view => [view,[]]))]));
const emptyInfo = () => Object.fromEntries(POSES.map(pose => [pose,{}]));
const validHand = hand => hand === 'Left' || hand === 'Right';

/** Angle-aware registration, independent of rendering, storage and media.
 * Fresh observations carry features, quality, orientation and handedness.
 * Timer-only update({now}) calls never manufacture sample/angle coverage.
 * Each pose obtains a stable front reference, then all five measured views.
 * The caller trains at 'captured' and stores only after separate verification
 * of every pose/view reaches 'verified'. Verification never alters training.
 */
export class EnrollmentController {
  constructor({prepareMs = 3000, viewPrepareMs = 1200, calibrationMs = 1000, calibrationSamples = 6,
    calibrationSpreadDeg = 5, captureMs = 2000, sampleIntervalMs = 150, minSamplesPerView = 12,
    maxSamplesPerView = 32, captureTimeoutMs = 30000, maxGapMs = 350,
    verifySamples = 3, verifyHoldMs = 300, verifyMaxGapMs = 350, verifyTimeoutMs = 15000} = {}) {
    const positive = [calibrationMs,calibrationSpreadDeg,captureMs,sampleIntervalMs,captureTimeoutMs,maxGapMs,verifyHoldMs,verifyMaxGapMs,verifyTimeoutMs];
    if (![prepareMs,viewPrepareMs].every(value => finite(value) && value >= 0) || !positive.every(value => finite(value) && value > 0) ||
      !Number.isInteger(calibrationSamples) || calibrationSamples < 3 ||
      !Number.isInteger(minSamplesPerView) || minSamplesPerView < 12 ||
      !Number.isInteger(maxSamplesPerView) || maxSamplesPerView < minSamplesPerView || maxSamplesPerView > 120 ||
      !Number.isInteger(verifySamples) || verifySamples < 3 || sampleIntervalMs > maxGapMs ||
      captureTimeoutMs <= Math.max(calibrationMs,captureMs) || verifyTimeoutMs <= verifyHoldMs || calibrationSpreadDeg > 10) throw new TypeError('invalid angle enrollment timing or sample settings');
    this.options = {prepareMs,viewPrepareMs,calibrationMs,calibrationSamples,calibrationSpreadDeg,captureMs,sampleIntervalMs,minSamplesPerView,maxSamplesPerView,captureTimeoutMs,maxGapMs,verifySamples,verifyHoldMs,verifyMaxGapMs,verifyTimeoutMs};
    this.phase = 'idle'; this.mode = 'enroll'; this.environment = null;
    this.step = 0; this.phaseStarted = 0; this.lastNow = null; this.lastObservationAt = null;
    this.groups = emptyGroups(); this.captureInfo = emptyInfo(); this.baselines = {}; this.verification = {};
    this.calibrationRows = []; this.candidateHand = null; this.reason = null; this.failurePhase = null;
    this.feedback = orientationFeedback(null,null,'front'); this.clearStreak();
  }
  start({now,environment} = {}) {
    if (!['desk','sofa'].includes(environment)) throw new TypeError('environment must be desk or sofa');
    this.checkTime(now,true); this.environment = environment; this.mode = 'enroll'; this.step = 0;
    this.groups = emptyGroups(); this.captureInfo = emptyInfo(); this.baselines = {}; this.verification = {};
    this.lastObservationAt = null; this.calibrationRows = []; this.candidateHand = null; this.prepare(now,true);
    return this.snapshot();
  }
  startVerification({now} = {}) {
    if (this.phase !== 'captured') throw new Error('capture all enrollment poses and views before verification');
    this.checkTime(now); this.mode = 'verify'; this.step = 0; this.verification = {}; this.prepare(now,true);
    return this.snapshot();
  }
  retry({now} = {}) {
    if (this.phase !== 'failed') throw new Error('only a failed step can be retried');
    this.checkTime(now);
    if (this.mode === 'enroll') {
      this.groups[this.pose][this.targetView] = [];
      if (this.failurePhase === 'calibrate') {delete this.baselines[this.pose];this.calibrationRows = [];this.candidateHand = null;}
    }
    this.prepare(now, this.failurePhase === 'calibrate');
    return this.snapshot();
  }
  cancel() {
    this.phase = 'cancelled';this.groups = emptyGroups();this.captureInfo = emptyInfo();this.baselines = {};this.verification = {};
    this.calibrationRows = [];this.candidateHand = null;this.clearStreak();this.reason = 'cancelled';
    return this.snapshot();
  }
  checkTime(now,reset = false) {
    if (!finite(now) || now < 0 || (!reset && this.lastNow !== null && now < this.lastNow)) throw new TypeError('now must be a monotonic non-negative time');
    this.lastNow = now;
  }
  clearStreak() {this.streakStarted = null;this.lastSampleAt = null;this.streakCount = 0;this.validDurationMs = 0;}
  prepare(now,newPose = false) {
    this.phase = this.mode === 'verify' ? 'verify-prepare' : 'prepare';this.phaseStarted = now;
    this.prepareDuration = newPose ? this.options.prepareMs : this.options.viewPrepareMs;
    this.clearStreak();this.reason = null;this.failurePhase = null;
    if (newPose && this.mode === 'enroll') {this.calibrationRows = [];this.candidateHand = null;}
    this.feedback = orientationFeedback(null,this.baselines[this.pose],this.targetView);
  }
  fail(reason) {this.failurePhase = this.phase;this.phase = 'failed';this.reason = reason;return this.snapshot();}
  rejectObservation(reason) {
    this.reason = reason;this.clearStreak();
    if (this.phase === 'calibrate') {this.calibrationRows = [];this.candidateHand = null;}
    return this.snapshot();
  }
  update({now,features,quality = null,orientation,handedness,match} = {}) {
    this.checkTime(now);
    if (!activePhases.has(this.phase)) return this.snapshot();
    if (this.phase === 'prepare' || this.phase === 'verify-prepare') {
      if (now - this.phaseStarted < this.prepareDuration) return this.snapshot();
      this.phase = this.mode === 'verify' ? 'verify' : this.baselines[this.pose] ? 'capture' : 'calibrate';
      this.phaseStarted = now;this.clearStreak();
    }
    const timeout = this.mode === 'verify' ? this.options.verifyTimeoutMs : this.options.captureTimeoutMs;
    if (now - this.phaseStarted >= timeout) return this.fail(this.phase === 'calibrate' ? 'calibration-timeout' : this.mode === 'verify' ? 'verification-timeout' : 'angle-timeout');
    // A clock update and a fresh inference are deliberately different inputs.
    const fresh = features !== undefined || match !== undefined;
    if (!fresh || this.lastObservationAt === now) return this.snapshot();
    this.lastObservationAt = now;
    this.feedback = orientationFeedback(orientation,this.baselines[this.pose],this.targetView);
    if (quality?.ok !== true) return this.rejectObservation(quality?.reason || 'hand-not-ready');
    if (!validOrientation(orientation)) return this.rejectObservation(orientation?.reason || 'orientation-unavailable');
    if (!validHand(handedness)) return this.rejectObservation('handedness-unavailable');
    if (this.mode === 'enroll' && !validVector(features)) return this.rejectObservation('invalid-features');
    if (this.phase === 'calibrate') return this.calibrate(now,orientation,handedness);
    if (handedness !== this.baselines[this.pose].handedness) return this.rejectObservation('different-hand');
    if (!this.feedback.matched) return this.rejectObservation(this.feedback.reason);
    if (this.mode === 'verify') return this.verify(now,match);
    return this.capture(now,features);
  }
  calibrate(now,orientation,handedness) {
    // An edge-on starting reference can make the opposite requested view
    // unreachable. Ask for a more frontal palm before fixing the reference.
    if (Math.abs(orientation.yaw) > 45 || Math.abs(orientation.pitch) > 45) return this.rejectObservation('face-camera');
    const first = this.calibrationRows[0];
    const changed = first && (handedness !== this.candidateHand || now - this.lastSampleAt > this.options.maxGapMs ||
      Math.abs(orientation.yaw - first.yaw) > this.options.calibrationSpreadDeg || Math.abs(orientation.pitch - first.pitch) > this.options.calibrationSpreadDeg);
    if (changed || this.lastSampleAt === null) {this.calibrationRows = [];this.clearStreak();}
    if (this.lastSampleAt !== null && now - this.lastSampleAt < this.options.sampleIntervalMs) return this.snapshot();
    this.candidateHand = handedness;this.lastSampleAt = now;
    this.calibrationRows.push({yaw:orientation.yaw,pitch:orientation.pitch,now});
    this.streakStarted ??= now;this.streakCount = this.calibrationRows.length;this.validDurationMs = now - this.streakStarted;
    this.reason = changed ? 'front-moving' : null;
    if (this.streakCount >= this.options.calibrationSamples && this.validDurationMs >= this.options.calibrationMs) {
      this.baselines[this.pose] = {yaw:this.calibrationRows.reduce((sum,row) => sum+row.yaw,0)/this.calibrationRows.length,
        pitch:this.calibrationRows.reduce((sum,row) => sum+row.pitch,0)/this.calibrationRows.length,handedness};
      this.phase = 'capture';this.phaseStarted = now;this.clearStreak();this.calibrationRows = [];this.reason = null;
      this.feedback = orientationFeedback(orientation,this.baselines[this.pose],this.targetView);
    }
    return this.snapshot();
  }
  capture(now,features) {
    if (this.lastSampleAt !== null && now - this.lastSampleAt < this.options.sampleIntervalMs) return this.snapshot();
    if (this.lastSampleAt === null || now - this.lastSampleAt > this.options.maxGapMs) this.clearStreak();
    this.streakStarted ??= now;this.streakCount++;this.lastSampleAt = now;this.validDurationMs = now-this.streakStarted;
    const rows = this.groups[this.pose][this.targetView];rows.push([...features]);
    if (rows.length > this.options.maxSamplesPerView) rows.shift();
    this.reason = null;
    if (rows.length >= this.options.minSamplesPerView && this.validDurationMs >= this.options.captureMs) {
      this.captureInfo[this.pose][this.targetView] = {count:rows.length,durationMs:this.validDurationMs,complete:true};
      this.nextView(now);
    }
    return this.snapshot();
  }
  verify(now,match) {
    if (match?.accepted !== true || match.gesture !== this.pose) return this.rejectObservation('verification-mismatch');
    if (this.lastSampleAt !== null && now - this.lastSampleAt > this.options.verifyMaxGapMs) this.clearStreak();
    this.streakStarted ??= now;this.streakCount++;this.lastSampleAt = now;this.validDurationMs = now-this.streakStarted;this.reason = null;
    if (this.streakCount >= this.options.verifySamples && this.validDurationMs >= this.options.verifyHoldMs) {
      const summary = this.verification[this.pose] ??= {views:{},passed:false};
      summary.views[this.targetView] = {matched:this.streakCount,durationMs:this.validDurationMs,passed:true};
      summary.passed = VIEWS.every(view => summary.views[view]?.passed === true);
      summary.matched = Math.min(...Object.values(summary.views).map(view => view.matched));
      summary.durationMs = Math.min(...Object.values(summary.views).map(view => view.durationMs));
      this.nextView(now);
    }
    return this.snapshot();
  }
  nextView(now) {
    if (this.step + 1 >= this.totalSteps) {this.phase = this.mode === 'verify' ? 'verified' : 'captured';this.reason = null;this.clearStreak();return;}
    this.step++;this.prepare(now,this.viewIndex === 0);
  }
  get poseIndex() {return Math.floor(this.step / VIEWS.length);}
  get pose() {return POSES[this.poseIndex];}
  get viewIndex() {return this.step % VIEWS.length;}
  get targetView() {return VIEWS[this.viewIndex];}
  get totalSteps() {return POSES.length * VIEWS.length;}
  getSamples() {return Object.fromEntries(POSES.map(pose => [pose,VIEWS.flatMap(view => this.groups[pose][view].map(row => [...row]))]));}
  getSampleGroups() {return clone(this.groups);}
  snapshot() {
    const active = activePhases.has(this.phase),now = this.lastNow ?? 0;
    const preparation = this.phase === 'prepare' || this.phase === 'verify-prepare';
    const effectiveDuration = this.lastSampleAt !== null && now-this.lastSampleAt <= (this.mode === 'verify' ? this.options.verifyMaxGapMs : this.options.maxGapMs) ? this.validDurationMs : 0;
    const neededDuration = this.phase === 'calibrate' ? this.options.calibrationMs : this.mode === 'verify' ? this.options.verifyHoldMs : this.options.captureMs;
    const required = this.mode === 'verify' ? this.options.verifySamples : this.options.minSamplesPerView;
    const views = Object.fromEntries(VIEWS.map(view => {
      const done = this.mode === 'verify' ? this.verification[this.pose]?.views[view]?.passed === true : this.captureInfo[this.pose][view]?.complete === true;
      const current = view === this.targetView && ['capture','verify'].includes(this.phase);
      const count = this.mode === 'verify' ? this.verification[this.pose]?.views[view]?.matched ?? (current ? this.streakCount : 0) : this.groups[this.pose][view].length;
      const durationMs = this.mode === 'verify' ? this.verification[this.pose]?.views[view]?.durationMs ?? (current ? effectiveDuration : 0) : this.captureInfo[this.pose][view]?.durationMs ?? (current ? effectiveDuration : 0);
      return [view,{label:VIEW_LABELS[view],count,required,durationMs,requiredDurationMs:this.mode === 'verify' ? this.options.verifyHoldMs : this.options.captureMs,complete:done,
        progress:done ? 1 : current ? Math.min(1,count/required,effectiveDuration/neededDuration) : 0}];
    }));
    const completedViews = this.mode === 'verify'
      ? POSES.reduce((sum,pose) => sum+VIEWS.filter(view => this.verification[pose]?.views[view]?.passed).length,0)
      : POSES.reduce((sum,pose) => sum+VIEWS.filter(view => this.captureInfo[pose][view]?.complete).length,0);
    const calibrationProgress = this.phase === 'calibrate' ? Math.min(1,this.calibrationRows.length/this.options.calibrationSamples,effectiveDuration/this.options.calibrationMs) : this.baselines[this.pose] ? 1 : 0;
    const atBoundary = ['captured','verified'].includes(this.phase);
    return {
      phase:this.phase,mode:this.mode,environment:this.environment,active,pose:this.pose,poseIndex:this.poseIndex,targetView:this.targetView,viewIndex:this.viewIndex,
      round:1,rounds:1,step:this.step+1,totalSteps:this.totalSteps,views,
      progress:atBoundary ? 1 : this.phase === 'calibrate' ? calibrationProgress : preparation ? Math.min(1,(now-this.phaseStarted)/Math.max(1,this.prepareDuration)) : views[this.targetView].progress,
      totalProgress:Math.min(1,(completedViews+(views[this.targetView].complete ? 0 : views[this.targetView].progress))/this.totalSteps),
      completedViews,totalViews:this.totalSteps,
      remainingMs:preparation ? Math.max(0,this.prepareDuration-(now-this.phaseStarted)) : active ? Math.max(0,neededDuration-effectiveDuration) : 0,
      timeoutRemainingMs:active && !preparation ? Math.max(0,(this.mode === 'verify' ? this.options.verifyTimeoutMs : this.options.captureTimeoutMs)-(now-this.phaseStarted)) : null,
      sampleCount:this.phase === 'calibrate' ? this.calibrationRows.length : views[this.targetView].count,minSamples:this.phase === 'calibrate' ? this.options.calibrationSamples : required,
      calibration:{progress:calibrationProgress,count:this.calibrationRows.length,required:this.options.calibrationSamples,durationMs:effectiveDuration,requiredDurationMs:this.options.calibrationMs},
      baseline:this.baselines[this.pose] ? {...this.baselines[this.pose]} : null,orientationFeedback:{...this.feedback},reason:this.reason,
      samplesByPose:atBoundary ? this.getSamples() : undefined,sampleGroupsByPose:atBoundary ? this.getSampleGroups() : undefined,
      orientationBaselines:atBoundary ? clone(this.baselines) : undefined,verification:clone(this.verification),
    };
  }
}
