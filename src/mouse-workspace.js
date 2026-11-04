import {AirMouseController} from './air-mouse-controller.js';
import {buildOverlayFrame} from './overlay-frame.js';
import {IndexPointerController} from './index-pointer-controller.js';
import {POINTER_SETTINGS, DEFAULT_POINTER_SETTINGS, guideRows, guideNote, contactText} from './pointer-guide.js';
import {thumbContacts, thumbDistances} from './pointer-engine.js';

const LABELS = {idle:'쉬는 중', move:'커서 이동', left:'좌클릭 · 드래그', right:'우클릭', scroll:'스크롤'};
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
// Sensitivity is stored as the slider value; the Qt app shows it as a percentage of the default.
const percent = value => `${Math.round(value / 1800 * 100)}%`;

/** Practice actions are confined to the pad; only the desktop preload can control Windows. */
export class MouseWorkspace {
  constructor({root = document, bridge = globalThis.manuxDesktop, sensitivity = 1800, onSensitivityChange = null} = {}) {
    this.root = root; this.bridge = bridge; this.$ = selector => root.querySelector(selector);
    this.pointer = {...DEFAULT_POINTER_SETTINGS};
    this.controller = this.makeController(sensitivity);
    this.$('#mouse-sensitivity').value = String(sensitivity);
    this.$('#mouse-sensitivity-output').textContent = percent(sensitivity);
    this.active = false; this.ready = false; this.enabled = false; this.available = false;
    this.generation = 0; this.pending = false; this.lastFrame = null;
    this.position = {x:130, y:110}; this.pressed = false; this.drag = null; this.clickStart = null;
    this.$('#mouse-enabled').addEventListener('change', () => { void this.toggle(); });
    this.$('#mouse-stop').addEventListener('click', () => this.stop('마우스 제어를 껐습니다.'));
    this.$('#mouse-target').addEventListener('change', () => { this.stop(); this.renderTarget(); });
    this.$('#mouse-sensitivity').addEventListener('input', () => {
      this.stop('감도를 바꿨습니다. 제어를 다시 켜세요.');
      const sensitivity = Number(this.$('#mouse-sensitivity').value);
      this.controller = this.makeController(sensitivity);
      this.$('#mouse-sensitivity-output').textContent = percent(sensitivity);
    });
    // Save once the slider is released, not on every intermediate value.
    this.$('#mouse-sensitivity').addEventListener('change', () => onSensitivityChange?.(Number(this.$('#mouse-sensitivity').value)));
    this.$('#mouse-click-target').addEventListener('click', () => {
      const el = this.$('#mouse-click-count'); el.textContent = String(Number(el.textContent) + 1);
    });
    this.$('#mouse-scroll-area').addEventListener('scroll', () => this.renderScroll());
    this.root.querySelectorAll('.segmented[data-setting] button[data-value]').forEach(button => button.addEventListener('click', () => {
      this.setPointer(button.closest('.segmented').dataset.setting, button.dataset.value);
    }));
    this.renderPointerSettings();
    this.details = {fps:null, inferenceMs:null, contacts:[]};
    // Space toggles control like the Qt app, but never while typing or using a form control.
    root.addEventListener?.('keydown', event => {
      if (event.code !== 'Space' || event.repeat || !this.active || event.target?.closest?.('input, select, textarea, button, [contenteditable]')) return;
      event.preventDefault();
      const box = this.$('#mouse-enabled');
      if (box.disabled) return;
      box.checked = !box.checked; void this.toggle();
    });
    this.overlay = false;
    this.$('#mouse-overlay')?.addEventListener('change', () => { void this.setOverlay(this.$('#mouse-overlay').checked); });
    this.unsubscribe = bridge?.onStatus(status => {
      this.available = Boolean(status.available);
      if (this.enabled && !status.enabled && this.desktop) this.stop(status.error || 'Windows 마우스 제어가 중지되었습니다.');
      this.renderTarget(); this.refresh();
      if (status.error && !this.enabled) this.status(status.error, true);
    });
    if (bridge) bridge.getStatus().then(status => {
      this.available = Boolean(status.available); this.renderTarget();
      if (status.error) this.status(status.error, true);
    }).catch(error => this.status(`데스크톱 연결 실패: ${error.message}`, true));
    // A timer may stop stalled inference, but must never refresh the native input lease.
    this.watchdog = setInterval(() => {
      if (this.enabled && this.lastFrame !== null && performance.now() - this.lastFrame > 700) {
        this.stop('손 추적 응답이 멈춰 제어를 껐습니다. 카메라를 확인하고 다시 켜세요.');
      }
    }, 100);
    this.renderTarget(); this.renderCursor(); this.refresh();
  }

  /** Every engine takes the same update() input and returns {mode, phase, progress, events}. */
  makeController(sensitivity) {
    const {engine, click, anchor} = this.pointer;
    return engine === 'index' ? new IndexPointerController({sensitivity, clickStyle:click, anchor}) : new AirMouseController({sensitivity});
  }
  setPointer(setting, value) {
    if (!POINTER_SETTINGS[setting]?.includes(value) || this.pointer[setting] === value) return;
    this.stop('포인터 설정을 바꿨습니다. 제어를 다시 켜세요.');
    this.pointer = {...this.pointer, [setting]:value};
    this.controller = this.makeController(Number(this.$('#mouse-sensitivity').value));
    this.renderPointerSettings();
  }
  renderPointerSettings() {
    for (const group of this.root.querySelectorAll('.segmented[data-setting]')) {
      for (const button of group.querySelectorAll('button')) {
        const selected = button.dataset.value === this.pointer[group.dataset.setting];
        button.setAttribute('aria-checked', String(selected));
        button.classList.toggle('selected', selected);
      }
    }
    const index = this.pointer.engine === 'index';
    this.root.querySelectorAll('.index-only').forEach(el => { el.hidden = !index; });
    this.root.querySelectorAll('.palm-only').forEach(el => { el.hidden = index; });
    const list = this.$('#action-guide-list'), doc = list?.ownerDocument;
    if (doc?.createElement && list.replaceChildren) list.replaceChildren(...guideRows(this.pointer).flatMap(([gesture, result]) => {
      const dt = doc.createElement('dt'), dd = doc.createElement('dd');
      dt.textContent = gesture; dd.textContent = result;
      return [dt, dd];
    }));
    const note = this.$('#action-guide-note'); if (note) note.textContent = guideNote(this.pointer);
  }
  /** fps and inference time come from the app; contacts are measured here from the hand. */
  setFrameStats({fps, inferenceMs}) { this.details.fps = fps; this.details.inferenceMs = inferenceMs; }
  renderDetails(landmarks, width, height) {
    const box = this.$('#mouse-details');
    if (!box?.open) return;
    const points = Array.isArray(landmarks) && landmarks.length >= 21 ? landmarks : null;
    const contacts = points ? thumbContacts(thumbDistances(points, height / width)) : [];
    const {fps, inferenceMs} = this.details;
    // Trigger click tuning: show the live thumb-to-index distance against the press/release thresholds.
    const trigger = this.controller.engine?.trigger;
    const triggerText = trigger && this.pointer.click === 'trigger'
      ? `방아쇠 거리 ${Number.isFinite(trigger.ratio) ? trigger.ratio.toFixed(2) : '—'} (누름 ≤ ${trigger.settings.trigger_press_ratio} · 놓기 ≥ ${trigger.settings.trigger_release_ratio}) · ${{idle:'대기', approach:'접근', press:'누름', drag:'드래그'}[trigger.state] ?? trigger.state}`
      : null;
    this.$('#mouse-details-text').textContent = [
      fps ? `${fps.toFixed(1)} fps` : null, Number.isFinite(inferenceMs) ? `추론 ${inferenceMs.toFixed(1)} ms` : null,
      `손 ${points ? '감지됨' : '대기'}`, `엄지 접촉: ${contactText(contacts)}`, triggerText,
    ].filter(Boolean).join('  ·  ');
  }
  get desktop() { return this.$('#mouse-target').value === 'desktop'; }
  get backgroundAllowed() { return this.active && this.enabled && this.desktop && this.available; }
  status(text, error = false) { const el = this.$('#mouse-status'); el.textContent = text; el.classList.toggle('error', error); }
  refresh() {
    this.$('#mouse-enabled').disabled = !this.active || !this.ready || this.pending || (this.desktop && !this.available);
    this.$('#mouse-enabled').checked = this.enabled;
    this.$('#mouse-target').disabled = this.pending;
  }
  renderTarget() {
    const option = this.$('#mouse-target option[value="desktop"]');
    option.disabled = !this.available;
    option.textContent = this.available ? 'Windows 마우스' : 'Windows 마우스 · 데스크톱 앱 필요';
    this.$('#mouse-title').textContent = this.desktop ? 'Windows 마우스 제어' : '마우스 연습';
    this.$('#mouse-target-note').textContent = this.desktop
      ? '실제 Windows 커서를 제어합니다. Ctrl + Alt + M으로 어디서든 중지할 수 있습니다.'
      : '연습 모드에서는 아래 영역의 커서만 움직입니다.';
    this.$('#mouse-pad').hidden = this.desktop;
    const overlay = this.$('#mouse-overlay');
    if (overlay) overlay.disabled = !this.available || !this.bridge?.setOverlay;
  }
  /** Screen finger tracker (desktop only). Drawing only; it never changes mouse input. */
  async setOverlay(on) {
    try { this.overlay = Boolean(await this.bridge?.setOverlay?.(on)); }
    catch (error) { this.overlay = false; this.status(`화면 손가락 표시를 켜지 못했습니다. ${error.message}`, true); }
    const box = this.$('#mouse-overlay'); if (box) box.checked = this.overlay;
  }
  setReady(ready) {
    this.ready = ready;
    if (!ready) this.stop('카메라를 시작한 뒤 마우스 제어를 켜세요.');
    else if (!this.enabled) this.status('준비됐습니다. 제어를 켠 뒤 손바닥을 펴세요.');
    this.refresh();
  }
  setActive(active) {
    this.stop(); this.active = active; this.refresh();
    if (active) this.status(this.ready ? '제어를 켠 뒤 손바닥을 펴세요.' : '카메라를 시작한 뒤 마우스 제어를 켜세요.');
  }
  async toggle() {
    if (!this.$('#mouse-enabled').checked) { this.stop('마우스 제어를 껐습니다.'); return; }
    if (!this.ready || !this.active) { this.stop(); return; }
    const generation = ++this.generation;
    this.pending = true; this.refresh();
    try {
      if (this.desktop) {
        if (!this.available || !this.bridge) throw new Error('데스크톱 앱에서 실행하세요.');
        const status = await this.bridge.setEnabled(true);
        if (generation !== this.generation) { this.bridge.stop(); return; }
        if (!status.enabled) throw new Error(status.error || 'Windows 마우스 제어를 켜지 못했습니다.');
      }
      if (generation !== this.generation || !this.ready || !this.active) return;
      this.controller.reset(); this.enabled = true; this.lastFrame = performance.now();
      this.status('손바닥을 펴고 손 전체를 움직이세요. 손가락을 붙여 클릭·스크롤로 전환합니다.');
    } catch (error) { this.stop(error.message); }
    finally { this.pending = false; this.refresh(); }
  }
  stop(text) {
    this.generation++; this.enabled = false; this.lastFrame = null;
    this.controller.reset(); this.releasePractice(); this.bridge?.stop();
    this.$('#mouse-context-menu').hidden = true;
    this.$('#mouse-mode').textContent = '대기 중';
    this.root.querySelectorAll('[data-mouse-mode]').forEach(el => el.classList.remove('active'));
    this.refresh(); if (text) this.status(text);
  }
  update({landmarks, now, quality, handedness, width, height, latency = 0}) {
    if (!this.active) return null;
    if (this.enabled && latency > 450) this.stop('추적 결과가 늦어 제어를 껐습니다. 해상도를 낮추고 다시 켜보세요.');
    const state = this.controller.update({landmarks, now, quality, handedness, width, height, enabled:this.enabled});
    this.renderDetails(quality ? landmarks : null, width, height);
    if (this.overlay && state.phase !== 'stale') {
      const frame = quality ? buildOverlayFrame(landmarks, {mode:this.enabled ? state.mode : 'idle', width, height}) : null;
      if (frame) this.bridge?.sendOverlayFrame?.(frame);
    }
    // A repeated or older timestamp is not fresh inference. It must not renew the stall watchdog
    // or the native input lease, so a frozen frame source still stops control and releases buttons.
    if (this.enabled && state.phase !== 'stale') {
      this.lastFrame = performance.now();
      if (this.desktop) this.bridge.send(state.events);
      else this.applyPractice(state.events);
      const index = this.pointer.engine === 'index';
      const hints = {
        'release-required':'손가락을 떼고 손바닥을 잠깐 펴면 클릭을 다시 사용할 수 있습니다.',
        invalid:'손 전체가 카메라에 보이도록 해주세요.',
        holding:index ? '엄지를 확인하는 중입니다. 커서를 고정했습니다.' : '손 모양을 확인하고 있습니다.',
        latched:'움직이는 중에는 클릭하지 않습니다. 멈추면 클릭할 수 있어요.',
        'tracking-reset':'손 위치를 다시 잡았습니다. 천천히 움직여주세요.',
      };
      const idle = index ? '검지만 펴서 가리키면 커서가 따라옵니다.' : '쉬는 중입니다. 손바닥을 펴면 커서가 움직입니다.';
      this.status(hints[state.phase] || (state.mode === 'idle' ? idle : `${LABELS[state.mode]} 중${index ? '' : ' · 손 전체를 움직이세요.'}`));
    }
    this.$('#mouse-mode').textContent = this.enabled ? LABELS[state.mode] : '대기 중';
    this.$('#mouse-panel').dataset.mode = state.mode;
    this.$('#mouse-panel').dataset.phase = state.phase;
    this.root.querySelectorAll('[data-mouse-mode]').forEach(el => el.classList.toggle('active', this.enabled && el.dataset.mouseMode === state.mode));
    return state;
  }
  hit(selector) {
    const pad = this.$('#mouse-pad').getBoundingClientRect(), rect = this.$(selector).getBoundingClientRect();
    const x = pad.left + this.position.x, y = pad.top + this.position.y;
    return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
  }
  applyPractice(events) {
    const pad = this.$('#mouse-pad');
    for (const event of events) {
      if (event.type === 'release') { this.releasePractice(); continue; }
      if (event.type === 'move') {
        this.position.x = clamp(this.position.x + event.dx, 0, pad.clientWidth - 2);
        this.position.y = clamp(this.position.y + event.dy, 0, pad.clientHeight - 2);
        if (this.drag) {
          const target = this.$('#mouse-drag-target');
          target.style.left = `${clamp(this.position.x - this.drag.x, 0, pad.clientWidth - target.offsetWidth)}px`;
          target.style.top = `${clamp(this.position.y - this.drag.y, 0, pad.clientHeight - target.offsetHeight)}px`;
        }
        this.renderCursor();
      } else if (event.type === 'scroll') {
        this.$('#mouse-scroll-area').scrollTop += event.dy; this.renderScroll();
      } else if (event.type === 'button' && event.button === 'right' && event.down) {
        const menu = this.$('#mouse-context-menu'); menu.hidden = false;
        menu.style.left = `${clamp(this.position.x, 8, Math.max(8, pad.clientWidth - 250))}px`;
        menu.style.top = `${clamp(this.position.y, 8, pad.clientHeight - 90)}px`;
      } else if (event.type === 'button' && event.button === 'left') {
        if (event.down) {
          this.$('#mouse-context-menu').hidden = true; this.pressed = true;
          this.clickStart = this.hit('#mouse-click-target') ? {...this.position} : null;
          if (this.hit('#mouse-drag-target')) {
            const target = this.$('#mouse-drag-target');
            this.drag = {x:this.position.x - target.offsetLeft, y:this.position.y - target.offsetTop};
            target.classList.add('dragging');
          }
        } else {
          if (this.pressed && this.clickStart && this.hit('#mouse-click-target') && Math.hypot(this.position.x - this.clickStart.x, this.position.y - this.clickStart.y) < 12) this.$('#mouse-click-target').click();
          this.releasePractice();
        }
        this.renderCursor();
      }
    }
  }
  releasePractice() { this.pressed = false; this.drag = null; this.clickStart = null; this.$('#mouse-drag-target').classList.remove('dragging'); this.renderCursor(); }
  renderCursor() { const el = this.$('#mouse-cursor'); el.style.transform = `translate(${this.position.x}px, ${this.position.y}px)`; el.classList.toggle('pressed', this.pressed); }
  renderScroll() { this.$('#mouse-scroll-value').textContent = `${Math.round(this.$('#mouse-scroll-area').scrollTop)}px`; }
}
