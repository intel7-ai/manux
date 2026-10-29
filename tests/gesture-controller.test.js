import test from 'node:test';
import assert from 'node:assert/strict';
import { GestureController, GESTURE_ACTIONS } from '../src/gesture-controller.js';

function sample(controller, now, gesture = 'Thumb_Up', extra = {}) {
  return controller.update({ gesture, score: 0.9, now, ...extra });
}

function hold(controller, start = 0, gesture = 'Thumb_Up', extra = {}) {
  let state;
  for (const now of [start, start + 100, start + 200, start + 300]) {
    state = sample(controller, now, gesture, extra);
  }
  return state;
}

function release(controller, start, gesture = 'None', extra = {}) {
  let state;
  for (const elapsed of [0, 100, 200, 300, 349, 350]) {
    state = sample(controller, start + elapsed, gesture, extra);
    if (elapsed < 350) assert.equal(state.armed, false);
  }
  return state;
}

test('the four poses map to distinct commands after a deliberate hold', () => {
  assert.deepEqual(GESTURE_ACTIONS, {
    Open_Palm: 'pause', Thumb_Up: 'play', Victory: 'forward', Closed_Fist: 'backward',
  });
  for (const [gesture, action] of Object.entries(GESTURE_ACTIONS)) {
    assert.equal(hold(new GestureController(), 0, gesture).action, action);
  }
});

test('time zero is valid and firing requires the exact 300 ms hold threshold', () => {
  const controller = new GestureController();
  assert.deepEqual(sample(controller, 0), {
    action: null, candidate: 'Thumb_Up', progress: 0, armed: true, phase: 'holding',
  });
  assert.equal(sample(controller, 150).progress, 0.5);
  assert.equal(sample(controller, 299).action, null);
  assert.equal(sample(controller, 300).action, 'play');
});

test('normal confidence jitter .78 .73 .68 .79 still fires in 300 ms', () => {
  const controller = new GestureController();
  const scores = [0.78, 0.73, 0.68, 0.79];
  const states = scores.map((score, index) => sample(controller, index * 100, 'Thumb_Up', { score }));
  assert.deepEqual(states.map((state) => state.action), [null, null, null, 'play']);
});

test('a brief uncertain interval preserves evidence but cannot itself fire', () => {
  const controller = new GestureController();
  sample(controller, 0);
  sample(controller, 100);
  sample(controller, 200);
  assert.equal(sample(controller, 300, 'Thumb_Up', { score: 0.3 }).action, null);
  assert.equal(sample(controller, 350).action, 'play');
});

test('a sustained uncertain interval discards pending hold evidence', () => {
  const controller = new GestureController();
  sample(controller, 0);
  sample(controller, 100);
  sample(controller, 150, null, { score: 0.2 });
  assert.equal(sample(controller, 300, null, { score: 0.2 }).candidate, null);
  assert.equal(sample(controller, 350).action, null);
  sample(controller, 450);
  sample(controller, 550);
  assert.equal(sample(controller, 650).action, 'play');
});

test('the current sample must reach entry confidence even after a supported hold', () => {
  const controller = new GestureController();
  sample(controller, 0);
  sample(controller, 100, 'Thumb_Up', { score: 0.6 });
  sample(controller, 200, 'Thumb_Up', { score: 0.6 });
  assert.equal(sample(controller, 300, 'Thumb_Up', { score: 0.6 }).action, null);
  assert.equal(sample(controller, 350, 'Thumb_Up', { score: 0.7 }).action, 'play');
});

test('weak observations cannot start a hold before a confident entry sample', () => {
  const controller = new GestureController();
  for (const now of [0, 100, 200, 300]) {
    assert.equal(sample(controller, now, 'Thumb_Up', { score: 0.6 }).candidate, null);
  }
  assert.equal(sample(controller, 400).progress, 0);
  sample(controller, 500);
  sample(controller, 600);
  assert.equal(sample(controller, 700).action, 'play');
});

test('one false command frame never triggers and a brief competing frame is tolerated', () => {
  const controller = new GestureController();
  sample(controller, 0);
  sample(controller, 100);
  assert.equal(sample(controller, 200, 'Open_Palm').action, null);
  assert.equal(sample(controller, 300).action, 'play');
  for (const now of [400, 500, 600]) assert.equal(sample(controller, now).action, null);
});

test('at least three distinct samples are required even with a zero hold', () => {
  const controller = new GestureController({ holdMs: 0 });
  assert.equal(sample(controller, 0).action, null);
  for (let i = 0; i < 10; i++) assert.equal(sample(controller, 0).action, null);
  assert.equal(sample(controller, 1).action, null);
  assert.equal(sample(controller, 2).action, 'play');
});

test('a long held seek never auto-repeats, including prolonged visible uncertainty', () => {
  const controller = new GestureController();
  assert.equal(hold(controller, 0, 'Victory').action, 'forward');
  for (let now = 400; now <= 1400; now += 100) {
    const state = sample(controller, now, 'Victory', { score: 0.2 });
    assert.equal(state.action, null);
    assert.equal(state.armed, false);
  }
  for (let now = 1500; now <= 3000; now += 100) {
    assert.equal(sample(controller, now, 'Victory').action, null);
  }
});

test('low-confidence None and unknown visible hand labels never rearm', () => {
  const controller = new GestureController();
  hold(controller);
  for (let now = 400; now <= 1000; now += 100) {
    assert.equal(sample(controller, now, 'None', { score: 0.2 }).armed, false);
  }
  for (let now = 1100; now <= 1700; now += 100) {
    assert.equal(sample(controller, now, 'Unknown').armed, false);
  }
  assert.equal(hold(controller, 1800).action, null);
});

test('play can transition directly to pause after confirmation and cooldown', () => {
  const controller = new GestureController();
  assert.equal(hold(controller).action, 'play');
  assert.equal(sample(controller, 350, 'Open_Palm').action, null);
  assert.equal(sample(controller, 450, 'Open_Palm').action, null);
  assert.equal(sample(controller, 550, 'Open_Palm').action, null);
  assert.equal(sample(controller, 650, 'Open_Palm').action, 'pause');
  assert.equal(hold(controller, 750).action, 'play');
});

test('each seek requires a deliberate new pose transition or neutral release', () => {
  const controller = new GestureController();
  assert.equal(hold(controller, 0, 'Victory').action, 'forward');
  assert.equal(hold(controller, 400, 'Victory').action, null);
  assert.equal(hold(controller, 800, 'Closed_Fist').action, 'backward');
  assert.equal(hold(controller, 1200, 'Victory').action, 'forward');
});

test('continuous recognized neutral rearms, then the same gesture can trigger again', () => {
  const controller = new GestureController();
  hold(controller);
  assert.equal(release(controller, 400).armed, true);
  assert.equal(hold(controller, 800).action, 'play');
});

test('explicit neutral supports resting poses without a model confidence score', () => {
  const controller = new GestureController();
  hold(controller);
  assert.equal(release(controller, 400, null, { neutral: true, score: 0 }).armed, true);
  assert.equal(hold(controller, 800).action, 'play');
});

test('visible uncertainty interrupts a neutral release timer', () => {
  const controller = new GestureController();
  hold(controller);
  sample(controller, 400, 'None');
  sample(controller, 500, 'None');
  sample(controller, 600, 'None', { score: 0.2 });
  assert.equal(release(controller, 700).armed, true);
});

test('transient hand loss does not release and continuous 500 ms loss does', () => {
  const controller = new GestureController();
  hold(controller);
  for (const now of [400, 500, 600]) assert.equal(sample(controller, now, null, { handPresent: false }).armed, false);
  assert.equal(sample(controller, 700).armed, false);
  for (const now of [800, 900, 1000, 1100, 1200, 1299]) {
    assert.equal(sample(controller, now, null, { handPresent: false }).armed, false);
  }
  assert.equal(sample(controller, 1300, null, { handPresent: false }).armed, true);
  assert.equal(hold(controller, 1400).action, 'play');
});

test('a large frame gap cannot accrue hidden hold or release time', () => {
  const controller = new GestureController();
  sample(controller, 0);
  sample(controller, 100);
  assert.equal(sample(controller, 1000).progress, 0);
  sample(controller, 1100);
  sample(controller, 1200);
  assert.equal(sample(controller, 1300).action, 'play');
  sample(controller, 1400, 'None');
  assert.equal(sample(controller, 2400, 'None').armed, false);
  sample(controller, 2500, null, { handPresent: false });
  assert.equal(sample(controller, 3500, null, { handPresent: false }).armed, false);
  assert.equal(hold(controller, 3600).action, null);
});

test('variable frame intervals use elapsed time rather than frame count', () => {
  const controller = new GestureController();
  for (const now of [0, 33, 90, 200, 299]) assert.equal(sample(controller, now).action, null);
  assert.equal(sample(controller, 325).action, 'play');
});

test('switching pose before confirmation requires a full hold of the new pose', () => {
  const controller = new GestureController();
  sample(controller, 0, 'Open_Palm');
  sample(controller, 100, 'Open_Palm');
  assert.equal(sample(controller, 200, 'Victory').action, null);
  assert.equal(sample(controller, 300, 'Victory').action, null);
  assert.equal(sample(controller, 400, 'Victory').action, null);
  assert.equal(sample(controller, 500, 'Victory').action, 'forward');
});

test('quality gating forbids firing or neutral rearming from unusable landmarks', () => {
  const controller = new GestureController();
  sample(controller, 0);
  sample(controller, 100);
  sample(controller, 200);
  assert.equal(sample(controller, 300, 'Thumb_Up', { quality: false }).action, null);
  assert.equal(sample(controller, 350).action, 'play');
  for (let now = 400; now <= 1000; now += 100) {
    assert.equal(sample(controller, now, 'None', { quality: false, neutral: true }).armed, false);
  }
});

test('disabling mid-hold cancels the pending action and requires a new full hold', () => {
  const controller = new GestureController();
  sample(controller, 0);
  sample(controller, 100);
  const disabled = sample(controller, 200, 'Thumb_Up', { enabled: false });
  assert.equal(disabled.phase, 'disabled');
  assert.equal(disabled.candidate, null);
  assert.equal(sample(controller, 300).action, null);
  sample(controller, 400);
  sample(controller, 500);
  assert.equal(sample(controller, 600).action, 'play');
});

test('cooldown blocks a second confirmed command until its exact deadline', () => {
  const controller = new GestureController({ holdMs: 100, releaseMs: 50, cooldownMs: 500 });
  sample(controller, 0);
  sample(controller, 50);
  assert.equal(sample(controller, 100).action, 'play');
  sample(controller, 120, 'None');
  assert.equal(sample(controller, 170, 'None').armed, true);
  for (const now of [180, 230, 280, 380, 480, 599]) {
    assert.equal(sample(controller, now, 'Closed_Fist').phase, 'cooldown');
  }
  assert.equal(sample(controller, 600, 'Closed_Fist').action, 'backward');
});

test('reset clears pending evidence, the old latch and cooldown', () => {
  const controller = new GestureController();
  sample(controller, 0);
  controller.reset();
  assert.equal(hold(controller, 1000).action, 'play');
  controller.reset();
  assert.equal(hold(controller, 1350).action, 'play');
});

test('clock rollback safely discards any pending hold', () => {
  const controller = new GestureController();
  sample(controller, 1000);
  const rollback = sample(controller, 900);
  assert.equal(rollback.phase, 'clock-reset');
  assert.equal(rollback.action, null);
  assert.equal(rollback.candidate, null);
  assert.equal(hold(controller, 950).action, 'play');
});

test('invalid timestamps and scores fail closed', () => {
  const controller = new GestureController();
  sample(controller, 0);
  assert.equal(sample(controller, Number.NaN).phase, 'invalid-time');
  for (const score of [NaN, Infinity, -1, 2]) {
    controller.reset();
    assert.equal(hold(controller, 0, 'Thumb_Up', { score }).action, null);
    assert.equal(controller.candidate, null);
  }
  assert.equal(sample(controller, 400, 'toString').action, null);
});

test('entry threshold is inclusive and absent hands never issue commands', () => {
  const controller = new GestureController();
  assert.equal(hold(controller, 0, 'Thumb_Up', { score: 0.7 }).action, 'play');
  controller.reset();
  assert.equal(hold(controller, 0, 'Thumb_Up', { handPresent: false }).action, null);
});

test('invalid configuration is rejected', () => {
  for (const options of [
    { holdMs: -1 }, { releaseMs: NaN }, { cooldownMs: Infinity }, { minConfidence: 1.1 },
    { sustainConfidence: 0.8 }, { minSamples: 2 }, { minSamples: 3.5 },
    { windowMs: 299 }, { maxSampleGapMs: 0 }, { missingReleaseMs: 499 },
  ]) assert.throws(() => new GestureController(options), RangeError);
});

test('seek repetition is off by default', () => {
  const controller = new GestureController();
  assert.equal(controller.repeatEnabled, false);
  assert.equal(hold(controller, 0, 'Victory').action, 'forward');
  for (let now = 400; now <= 3300; now += 100) {
    assert.equal(sample(controller, now, 'Victory').action, null);
  }
});

test('opted-in seek actions repeat at exactly 1000 ms after each firing', () => {
  for (const [gesture, action] of [['Victory', 'forward'], ['Closed_Fist', 'backward']]) {
    const controller = new GestureController({ repeatEnabled: true });
    assert.equal(hold(controller, 0, gesture).action, action);
    for (let now = 400; now <= 1200; now += 100) {
      const state = sample(controller, now, gesture);
      assert.equal(state.action, null);
      assert.equal(state.phase, 'repeating');
      assert.equal(state.armed, false);
    }
    assert.equal(sample(controller, 1299, gesture).action, null);
    assert.equal(sample(controller, 1300, gesture).action, action);
    for (let now = 1400; now <= 2200; now += 100) {
      assert.equal(sample(controller, now, gesture).action, null);
    }
    assert.equal(sample(controller, 2299, gesture).action, null);
    assert.equal(sample(controller, 2300, gesture).action, action);
  }
});

test('play and pause never repeat even when continuous seek is enabled', () => {
  for (const gesture of ['Thumb_Up', 'Open_Palm']) {
    const controller = new GestureController({ repeatEnabled: true });
    assert.equal(hold(controller, 0, gesture).action, GESTURE_ACTIONS[gesture]);
    for (let now = 400; now <= 3300; now += 100) {
      assert.equal(sample(controller, now, gesture).action, null);
    }
  }
});

test('any uncertain frame stops repetition and recovery waits a fresh interval', () => {
  for (const interruption of [
    { gesture: 'Victory', score: 0.54 },
    { gesture: 'Victory', score: 0.9, quality: false },
    { gesture: null, handPresent: false },
    { gesture: 'Open_Palm' },
    { gesture: 'None', score: 0.2 },
    { gesture: null, neutral: true },
  ]) {
    const controller = new GestureController({ repeatEnabled: true });
    hold(controller, 0, 'Victory');
    for (let now = 400; now <= 1200; now += 100) sample(controller, now, 'Victory');
    const interrupted = sample(controller, 1299, interruption.gesture, interruption);
    assert.equal(interrupted.action, null);
    assert.equal(interrupted.armed, false, 'an interruption must not rearm the ordinary pose latch');
    assert.equal(controller.repeatSince, null);
    assert.equal(sample(controller, 1300, 'Victory').action, null, 'old repeat deadline must be discarded');
    for (let now = 1400; now <= 2200; now += 100) {
      assert.equal(sample(controller, now, 'Victory').action, null);
    }
    assert.equal(sample(controller, 2299, 'Victory').action, null);
    assert.equal(sample(controller, 2300, 'Victory').action, 'forward');
  }
});

test('continuously weak seek scores do not repeat or falsely rearm', () => {
  const controller = new GestureController({ repeatEnabled: true });
  hold(controller, 0, 'Victory');
  for (let now = 400; now <= 2400; now += 100) {
    const state = sample(controller, now, 'Victory', { score: 0.54 });
    assert.equal(state.action, null);
    assert.equal(state.armed, false);
  }
  for (let now = 2500; now <= 3400; now += 100) {
    assert.equal(sample(controller, now, 'Victory').action, null);
  }
  assert.equal(sample(controller, 3500, 'Victory').action, 'forward');
});

test('a deliberate different pose still fires promptly while seek repeat was active', () => {
  const controller = new GestureController({ repeatEnabled: true });
  hold(controller, 0, 'Victory');
  sample(controller, 400, 'Victory');
  sample(controller, 500, 'Victory');
  assert.equal(hold(controller, 600, 'Open_Palm').action, 'pause');
  for (let now = 1000; now <= 2000; now += 100) assert.equal(sample(controller, now, 'Open_Palm').action, null);
});

test('repeat observations still interrupt evidence for a competing command', () => {
  const controller = new GestureController({ repeatEnabled: true });
  hold(controller, 0, 'Victory');
  sample(controller, 400, 'Open_Palm');
  sample(controller, 500, 'Victory');
  sample(controller, 600, 'Victory');
  assert.equal(sample(controller, 700, 'Open_Palm').action, null);
  assert.equal(sample(controller, 800, 'Open_Palm').action, null);
  assert.equal(sample(controller, 900, 'Open_Palm').action, null);
  assert.equal(sample(controller, 1000, 'Open_Palm').action, 'pause');
});

test('a stalled camera cannot accrue an unseen repeat interval', () => {
  const controller = new GestureController({ repeatEnabled: true });
  hold(controller, 0, 'Closed_Fist');
  for (let now = 400; now <= 1200; now += 100) sample(controller, now, 'Closed_Fist');
  assert.equal(sample(controller, 2200, 'Closed_Fist').action, null);
  for (let now = 2300; now <= 3100; now += 100) {
    assert.equal(sample(controller, now, 'Closed_Fist').action, null);
  }
  assert.equal(sample(controller, 3200, 'Closed_Fist').action, 'backward');
});

test('repeat recovery must satisfy hold duration even with a shorter configured interval', () => {
  const controller = new GestureController({ repeatEnabled: true, repeatIntervalMs: 100 });
  hold(controller, 0, 'Victory');
  sample(controller, 400, 'Victory', { score: 0.2 });
  for (const now of [500, 600, 700, 799]) {
    assert.equal(sample(controller, now, 'Victory').action, null);
  }
  assert.equal(sample(controller, 800, 'Victory').action, 'forward');
});

test('repeat requires independent samples and observes the configured cooldown', () => {
  const controller = new GestureController({
    repeatEnabled: true, holdMs: 0, cooldownMs: 0, maxSampleGapMs: 2000,
  });
  sample(controller, 0, 'Victory');
  sample(controller, 1, 'Victory');
  assert.equal(sample(controller, 2, 'Victory').action, 'forward');
  for (let i = 0; i < 5; i++) assert.equal(sample(controller, 1002, 'Victory').action, null);
  assert.equal(sample(controller, 1003, 'Victory').action, 'forward');

  const cooling = new GestureController({ repeatEnabled: true, cooldownMs: 2000 });
  hold(cooling, 0, 'Victory');
  for (let now = 400; now <= 2200; now += 100) assert.equal(sample(cooling, now, 'Victory').action, null);
  assert.equal(sample(cooling, 2300, 'Victory').action, 'forward');
});

test('reset and disable discard any repeat timer', () => {
  const controller = new GestureController({ repeatEnabled: true });
  hold(controller, 0, 'Victory');
  sample(controller, 400, 'Victory');
  controller.reset();
  assert.equal(controller.repeatSince, null);
  assert.equal(hold(controller, 500, 'Victory').action, 'forward');
  assert.equal(sample(controller, 900, 'Victory', { enabled: false }).action, null);
  assert.equal(controller.repeatSince, null);
  assert.equal(hold(controller, 1000, 'Victory').action, 'forward');
});

test('repeat interval rejects zero and invalid durations', () => {
  for (const repeatIntervalMs of [0, -1, NaN, Infinity]) {
    assert.throws(() => new GestureController({ repeatIntervalMs }), RangeError);
  }
});

test('repeat hysteresis retains its timer through .68 scores and fires on .70 at the deadline', () => {
  const controller = new GestureController({ repeatEnabled: true });
  assert.equal(hold(controller, 0, 'Victory').action, 'forward');
  for (let now = 400; now <= 1200; now += 100) {
    const state = sample(controller, now, 'Victory', { score: 0.68 });
    assert.equal(state.action, null);
    assert.equal(state.phase, 'repeating');
    assert.equal(controller.repeatSince, 300);
  }
  assert.equal(sample(controller, 1299, 'Victory', { score: 0.68 }).action, null);
  assert.equal(sample(controller, 1300, 'Victory', { score: 0.7 }).action, 'forward');
});

test('repeat at its deadline waits for a fresh entry-confident frame without restarting the timer', () => {
  const controller = new GestureController({ repeatEnabled: true });
  assert.equal(hold(controller, 0, 'Closed_Fist').action, 'backward');
  for (let now = 400; now <= 1200; now += 100) {
    assert.equal(sample(controller, now, 'Closed_Fist', { score: 0.68 }).action, null);
  }
  const pending = sample(controller, 1300, 'Closed_Fist', { score: 0.68 });
  assert.equal(pending.action, null);
  assert.equal(pending.progress, 1);
  assert.equal(controller.repeatSince, 300);
  assert.equal(sample(controller, 1350, 'Closed_Fist', { score: 0.7 }).action, 'backward');
  assert.equal(controller.repeatSince, 1350);
});
