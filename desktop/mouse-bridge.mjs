import {EventEmitter} from 'node:events';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import readline from 'node:readline';

const helperPath = fileURLToPath(new URL('./windows-mouse.ps1', import.meta.url));
const finiteWithin = (value, bound) => typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= bound;
const exactKeys = (value, keys) => Object.keys(value).every(key => keys.includes(key));

/** Copies only the mouse protocol. A rejected batch never partially executes. */
export function validateMouseEvents(events) {
  if (!Array.isArray(events) || events.length > 8) throw new TypeError('Invalid mouse batch.');
  return events.map(event => {
    if (!event || typeof event !== 'object' || Array.isArray(event)) throw new TypeError('Invalid mouse event.');
    if (event.type === 'move' && exactKeys(event, ['type', 'dx', 'dy']) && finiteWithin(event.dx, 100) && finiteWithin(event.dy, 100)) {
      return {type:'move', dx:event.dx, dy:event.dy};
    }
    if (event.type === 'button' && exactKeys(event, ['type', 'button', 'down']) && ['left', 'right'].includes(event.button) && typeof event.down === 'boolean') {
      return {type:'button', button:event.button, down:event.down};
    }
    if (event.type === 'scroll' && exactKeys(event, ['type', 'dy']) && finiteWithin(event.dy, 240)) return {type:'scroll', dy:event.dy};
    if (event.type === 'release' && exactKeys(event, ['type'])) return {type:'release'};
    throw new TypeError('Unsupported mouse event.');
  });
}

/** Narrow bridge to one fixed native helper, with an expiring renderer lease. */
export class MouseBridge extends EventEmitter {
  constructor({platform = process.platform, spawnHelper = spawn, now = Date.now, watchdogMs = 1000,
    startupTimeoutMs = 15000, dryRun = process.env.MANUX_MOUSE_DRY_RUN === '1'} = {}) {
    super();
    Object.assign(this, {platform, spawnHelper, now, watchdogMs, startupTimeoutMs, dryRun});
    this.enabled = false;
    this.ready = false;
    this.generation = 0;
    this.error = platform === 'win32' ? null : '실제 마우스 제어는 Windows에서 사용할 수 있습니다.';
    this.lastInputAt = 0;
    this.child = null;
    this.closed = false;
  }

  status() {
    return {available:this.platform === 'win32' && this.ready && !this.closed,
      enabled:this.enabled, platform:this.platform, generation:this.generation, dryRun:this.dryRun, error:this.error};
  }

  publish() { this.emit('status', this.status()); }

  start() {
    if (this.closed) return Promise.reject(new Error('Mouse bridge is closed.'));
    if (this.startPromise) return this.startPromise;
    if (this.platform !== 'win32') return Promise.resolve(this.status());
    this.startPromise = new Promise((resolve, reject) => {
      const executable = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
      const args = ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', helperPath];
      if (this.dryRun) args.push('-DryRun');
      let settled = false;
      const complete = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        error ? reject(error) : resolve(this.status());
      };
      const timer = setTimeout(() => {
        this.fail('Windows 마우스 연결 시간이 초과됐습니다. 앱을 다시 실행하세요.');
        this.child?.kill();
        complete(new Error(this.error));
      }, this.startupTimeoutMs);
      try {
        this.child = this.spawnHelper(executable, args, {windowsHide:true, stdio:['pipe', 'pipe', 'pipe']});
      } catch (error) {
        this.fail(error.message); complete(error); return;
      }
      this.child.stdin.on('error', () => this.fail('Windows 마우스 연결이 끊어졌습니다.'));
      this.child.on('error', error => { this.fail(error.message); complete(error); });
      this.child.on('exit', () => {
        this.ready = false;
        if (!this.closed) this.fail('Windows 마우스 연결이 종료됐습니다. 앱을 다시 실행하세요.');
        complete(new Error('Mouse helper exited.'));
      });
      const lines = readline.createInterface({input:this.child.stdout});
      lines.on('line', line => {
        let message;
        try { message = JSON.parse(line); } catch { return; }
        if (message.kind === 'ready') {
          this.ready = true; this.error = null; this.publish(); complete();
        } else if (message.kind === 'stopped' || message.kind === 'error') {
          // Ignore acknowledgements for an older lease after an explicit re-enable.
          if (message.generation >= this.generation) this.disable(message.reason || 'Windows 입력이 중단됐습니다.');
        } else if (message.kind === 'applied') this.emit('applied', message);
      });
      let stderr = '';
      this.child.stderr.on('data', chunk => {
        stderr = (stderr + String(chunk)).slice(-1600);
        if (!settled) this.error = `Windows 마우스 초기화 오류: ${stderr.trim()}`;
      });
      this.timer = setInterval(() => this.tick(), 100);
      this.timer.unref?.();
    });
    return this.startPromise;
  }

  write(command) {
    if (!this.child?.stdin?.writable || this.closed) return false;
    if (this.child.stdin.writableLength > 16384) return false;
    try { this.child.stdin.write(`${JSON.stringify(command)}\n`); return true; }
    catch { return false; }
  }

  async setEnabled(enabled) {
    if (typeof enabled !== 'boolean') throw new TypeError('enabled must be boolean');
    if (!enabled) { this.disable(); return this.status(); }
    const before = this.generation;
    await this.start();
    if (this.closed || !this.ready || before !== this.generation) return this.status();
    this.generation += 1;
    this.error = null;
    this.lastInputAt = this.now();
    this.enabled = this.write({kind:'enable', generation:this.generation, sentAt:this.now()});
    if (!this.enabled) this.error = 'Windows 마우스 연결에 쓸 수 없습니다.';
    this.publish();
    return this.status();
  }

  send(events, {generation = this.generation, sentAt = this.now()} = {}) {
    if (!this.enabled || generation !== this.generation) return false;
    const age = this.now() - sentAt;
    if (!Number.isFinite(age) || age < -50 || age > 300) { this.disable('오래된 카메라 입력으로 제어를 멈췄습니다.'); return false; }
    let batch;
    try { batch = validateMouseEvents(events); }
    catch { this.disable('지원하지 않는 마우스 입력으로 제어를 멈췄습니다.'); return false; }
    this.lastInputAt = this.now();
    if (!this.write({kind:'batch', generation:this.generation, sentAt, events:batch})) {
      this.disable('마우스 연결이 지연되어 제어를 멈췄습니다.'); return false;
    }
    return true;
  }

  tick() {
    if (this.enabled && this.now() - this.lastInputAt > this.watchdogMs) this.disable('카메라 입력이 멈춰 마우스 제어를 해제했습니다.');
  }

  disable(reason = null) {
    this.enabled = false;
    this.generation += 1;
    this.error = reason;
    this.write({kind:'disable', generation:this.generation});
    this.publish();
  }

  fail(reason) { this.ready = false; this.disable(reason); }

  close() {
    if (this.closed) return;
    this.disable();
    this.write({kind:'shutdown', generation:this.generation});
    this.child?.stdin.end();
    this.closed = true;
    clearInterval(this.timer);
    this.publish();
    // EOF and the helper's independent watchdog release buttons even if Electron exits.
  }
}
