// Two-hand mode: one hand moves the cursor, the other only acts (2026-11-04, team choice: right moves,
// left acts). Because the acting hand is separate, the cursor stays still while clicking.
// Same update()/reset() contract and event shape as AirMouseController.
import {IndexPointerController} from './index-pointer-controller.js';
import {thumbDistances} from './pointer-engine.js';

export const TWO_HAND_DEFAULTS = Object.freeze({press:.25, release:.40, confirmFrames:2});

/** Thumb-to-finger contact with press/release hysteresis, confirmed over a few fresh frames. */
class Contact {
  constructor(settings) { this.settings = settings; this.reset(); }
  reset() { this.down = false; this.streak = 0; }
  update(ratio) {
    const {press, release, confirmFrames} = this.settings;
    if (!Number.isFinite(ratio)) { this.streak = 0; return this.down; }
    if (!this.down) {
      this.streak = ratio <= press ? this.streak + 1 : 0;
      if (this.streak >= confirmFrames) { this.down = true; this.streak = 0; }
    } else {
      this.streak = ratio >= release ? this.streak + 1 : 0;
      if (this.streak >= confirmFrames) { this.down = false; this.streak = 0; }
    }
    return this.down;
  }
}

export class TwoHandController {
  constructor({sensitivity = 1800, anchor = 'index', screenWidth, contact = {}} = {}) {
    this.mover = new IndexPointerController({sensitivity, clickStyle:'off', anchor, ...(screenWidth ? {screenWidth} : {})});
    this.settings = {...TWO_HAND_DEFAULTS, ...contact};
    this.left = new Contact(this.settings);
    this.right = new Contact(this.settings);
    this.reset();
  }

  state(phase, events = [], mode = 'idle') { return {mode, phase, progress:0, events}; }

  reset() {
    this.mover.reset(); this.left.reset(); this.right.reset();
    this.lastTime = null; this.enabled = false; this.leftDown = false; this.contacts = [];
    return this.state('disabled', [{type:'release'}]);
  }

  /** moveHand / actionHand: 21 landmarks each, or null when that hand is not seen. */
  update({moveHand = null, actionHand = null, now, enabled, width = 1280, height = 720} = {}) {
    if (this.lastTime !== null && !(now > this.lastTime)) return this.state('stale', [], this.leftDown ? 'left' : 'idle');
    if (!enabled) return this.enabled ? this.reset() : this.state('disabled');
    this.enabled = true; this.lastTime = now;
    const moved = this.mover.update({landmarks:moveHand, now, enabled:true, quality:Boolean(moveHand), width, height});
    const events = [...moved.events];
    const release = () => { if (this.leftDown) { events.push({type:'button', button:'left', down:false}); this.leftDown = false; } };
    // Losing either hand ends any press: the cursor must never drag on without both hands seen.
    if (!moveHand || !actionHand) {
      release(); this.left.reset(); this.right.reset(); this.contacts = [];
      return this.state(moveHand ? 'action-hand-missing' : 'invalid', events, moveHand ? 'move' : 'idle');
    }
    const [index, middle] = thumbDistances(actionHand, height / width);
    this.contacts = [index, middle];
    const leftHeld = this.left.update(index);
    const wasRight = this.right.down, rightHeld = this.right.update(middle);
    if (leftHeld && !this.leftDown) { events.push({type:'button', button:'left', down:true}); this.leftDown = true; }
    if (!leftHeld) release();
    let mode = moveHand && moved.phase !== 'invalid' ? 'move' : 'idle';
    if (rightHeld && !wasRight && !this.leftDown) {
      events.push({type:'button', button:'right', down:true}, {type:'button', button:'right', down:false});
      mode = 'right';
    }
    if (this.leftDown) mode = 'left';
    while (events.length > 8) events.splice(events.findIndex(e => e.type === 'move'), 1);
    return this.state('tracking', events, mode);
  }
}
