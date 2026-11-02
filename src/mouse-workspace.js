import {AirMouseController} from './air-mouse-controller.js';

const LABELS = {idle:'쉬는 중', move:'커서 이동', left:'좌클릭 · 드래그', right:'우클릭', scroll:'스크롤'};
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

/** Practice actions are confined to the pad; only the desktop preload can control Windows. */
export class MouseWorkspace {
  constructor({root = document, bridge = globalThis.manuxDesktop} = {}) {
    this.root = root; this.bridge = bridge; this.$ = selector => root.querySelector(selector);
    this.controller = new AirMouseController();
    this.active = false; this.ready = false; this.enabled = false; this.available = false;
    this.generation = 0; this.pending = false; this.lastFrame = null;
    this.position = {x:130, y:110}; this.pressed = false; this.drag = null; this.clickStart = null;
    this.$('#mouse-enabled').addEventListener('change', () => { void this.toggle(); });
    this.$('#mouse-stop').addEventListener('click', () => this.stop('마우스 제어를 껐습니다.'));
    this.$('#mouse-target').addEventListener('change', () => { this.stop(); this.renderTarget(); });
    this.$('#mouse-sensitivity').addEventListener('input', () => {
      this.stop('감도를 바꿨습니다. 제어를 다시 켜세요.');
      const sensitivity = Number(this.$('#mouse-sensitivity').value);
      this.controller = new AirMouseController({sensitivity});
      this.$('#mouse-sensitivity-output').textContent = String(sensitivity);
    });
    this.$('#mouse-click-target').addEventListener('click', () => {
      const el = this.$('#mouse-click-count'); el.textContent = String(Number(el.textContent) + 1);
    });
    this.$('#mouse-scroll-area').addEventListener('scroll', () => this.renderScroll());
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
    if (this.enabled) {
      this.lastFrame = performance.now();
      if (this.desktop) this.bridge.send(state.events);
      else this.applyPractice(state.events);
      const hints = {
        'release-required':'손가락을 떼고 손바닥을 잠깐 펴면 클릭을 다시 사용할 수 있습니다.',
        invalid:'손 전체가 카메라에 보이도록 해주세요.',
        holding:'손 모양을 확인하고 있습니다.',
        'tracking-reset':'손 위치를 다시 잡았습니다. 천천히 움직여주세요.',
      };
      this.status(hints[state.phase] || (state.mode === 'idle' ? '쉬는 중입니다. 손바닥을 펴면 커서가 움직입니다.' : `${LABELS[state.mode]} 중 · 손 전체를 움직이세요.`));
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
