// DOM-free mouse control session for the desktop studio. Same safety rules as MouseWorkspace:
// only fresh inference feeds the native lease, a stall or a late result stops control, and every
// stop releases buttons. Without the desktop bridge it runs as a preview and sends nothing.
import {buildOverlayFrame} from './overlay-frame.js';

export const STALL_MS = 700;
export const MAX_LATENCY_MS = 450;

export class MouseSession {
  constructor({bridge = globalThis.manuxDesktop, controller, clock = () => performance.now(), onChange = () => {}, timers = globalThis} = {}) {
    Object.assign(this, {bridge, controller, clock, onChange});
    this.enabled = false; this.pending = false; this.available = false; this.generation = 0;
    this.lastFrame = null; this.message = ''; this.error = false; this.overlay = false; this.state = null;
    this.unsubscribe = bridge?.onStatus?.(status => {
      this.available = Boolean(status.available);
      if (this.enabled && !status.enabled) this.stop(status.error || 'Windows 마우스 제어가 중지되었습니다.', true);
      else this.onChange();
    });
    bridge?.getStatus?.().then(status => { this.available = Boolean(status.available); this.onChange(); })
      .catch(error => this.say(`데스크톱 연결 실패: ${error.message}`, true));
    // A timer may stop stalled inference, but never refreshes the native input lease.
    this.watchdog = timers.setInterval?.(() => {
      if (this.enabled && this.lastFrame !== null && this.clock() - this.lastFrame > STALL_MS) {
        this.stop('손 추적 응답이 멈춰 제어를 껐습니다. 카메라를 확인하고 다시 켜세요.', true);
      }
    }, 100);
    this.timers = timers;
  }

  get desktop() { return Boolean(this.bridge && this.available); }
  say(text, error = false) { this.message = text; this.error = error; this.onChange(); }

  setController(controller) { this.stop(); this.controller = controller; }

  async start() {
    if (this.enabled || this.pending) return;
    const generation = ++this.generation;
    this.pending = true; this.onChange();
    try {
      if (this.desktop) {
        const status = await this.bridge.setEnabled(true);
        if (generation !== this.generation) { this.bridge.stop(); return; }
        if (!status.enabled) throw new Error(status.error || 'Windows 마우스 제어를 켜지 못했습니다.');
      }
      if (generation !== this.generation) return;
      this.controller.reset(); this.enabled = true; this.lastFrame = this.clock();
      this.say(this.desktop ? '제어 중입니다.' : '미리보기 중입니다. 실제 커서는 데스크톱 앱에서 움직입니다.');
    } catch (error) { this.stop(error.message, true); }
    finally { this.pending = false; this.onChange(); }
  }

  stop(text = '', error = false) {
    this.generation++; this.enabled = false; this.lastFrame = null;
    this.controller?.reset(); this.bridge?.stop?.();
    this.message = text; this.error = error; this.onChange();
  }

  toggle() { return this.enabled ? this.stop('제어를 껐습니다.') : this.start(); }

  async setOverlay(on) {
    try { this.overlay = Boolean(await this.bridge?.setOverlay?.(on)); }
    catch (error) { this.overlay = false; this.say(`화면 손가락 표시를 켜지 못했습니다. ${error.message}`, true); }
    this.onChange();
    return this.overlay;
  }

  /** input: {landmarks, quality, handedness} for one hand, or {moveHand, actionHand} for two; plus now, width, height, latency. */
  update(input) {
    const {width, height, latency = 0} = input;
    const landmarks = input.landmarks ?? input.moveHand ?? null, quality = input.quality ?? Boolean(landmarks);
    if (this.enabled && latency > MAX_LATENCY_MS) this.stop('추적 결과가 늦어 제어를 껐습니다. 해상도를 낮추고 다시 켜보세요.', true);
    const state = this.controller.update({...input, enabled:this.enabled});
    if (this.overlay && state.phase !== 'stale') {
      const frame = quality ? buildOverlayFrame(landmarks, {mode:this.enabled ? state.mode : 'idle', width, height}) : null;
      if (frame) this.bridge?.sendOverlayFrame?.(frame);
    }
    // A repeated or older timestamp is not fresh inference: it neither feeds the lease nor the watchdog.
    if (this.enabled && state.phase !== 'stale') {
      this.lastFrame = this.clock();
      if (this.desktop) this.bridge.send(state.events);
    }
    this.state = state;
    return state;
  }

  close() { this.stop(); this.timers.clearInterval?.(this.watchdog); this.unsubscribe?.(); }
}
