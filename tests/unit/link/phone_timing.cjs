// Run the shipped sender on a jittery 30fps camera with controlled network delay.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const rtt = Number(process.argv[3]);
const duration = rtt < 0 ? 1000 : 5000;
let now = 100, pending = 0, maxPending = 0, serial = 0;
const events = [], packets = [], elements = new Map();
function later(at, fn) { events.push({at, fn, serial: serial++}); }
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: id === 'camera' ? 'environment' : '123456', listeners: {},
    classList: {add() {}, remove() {}},
    addEventListener(type, fn) { this.listeners[type] = fn; },
    removeAttribute() {}, getContext: () => ({drawImage() {}, clearRect() {}}),
    toBlob(fn) { later(now + 5, () => fn({arrayBuffer: async () => new Uint8Array([255,216,255,217]).buffer})); },
    videoWidth: 480, videoHeight: 360, readyState: 2, currentTime: 0,
    play: async () => {}, requestVideoFrameCallback: () => 1, cancelVideoFrameCallback() {}
  });
  return elements.get(id);
}
let socket;
class Socket {
  static OPEN = 1;
  constructor() { socket = this; this.readyState = 1; this.bufferedAmount = 0; }
  send(data) {
    if (typeof data === 'string') return;
    const length = new DataView(data.buffer).getUint32(0);
    const meta = JSON.parse(new TextDecoder().decode(data.slice(4, 4 + length)));
    packets.push({at: now, seq: meta.seq});
    maxPending = Math.max(maxPending, ++pending);
    if (rtt >= 0) later(now + rtt, async () => {
      --pending;
      await this.onmessage({data: JSON.stringify({kind: 'ack', seq: meta.seq, accepted: true})});
    });
  }
  close() { this.readyState = 3; }
}
const context = vm.createContext({
  document: {getElementById: element, hidden: false, visibilityState: 'visible', addEventListener() {}},
  window: {isSecureContext: true, addEventListener() {}},
  navigator: {userAgent: 'Synthetic', mediaDevices: {getUserMedia: async () => ({
    getTracks: () => [{stop() {}}], getVideoTracks: () => [{getSettings: () => ({facingMode: 'environment'})}]
  })}},
  location: {host: '127.0.0.1'}, WebSocket: Socket, performance: {now: () => now},
  Date: class extends Date { static now() { return 1000000 + now; } },
  fetch: async () => ({ok: true, json: async () => ({token: 'synthetic'})}),
  TextEncoder, Uint8Array, DataView,
  setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
  requestAnimationFrame: () => 1, cancelAnimationFrame() {}, console
});
(async () => {
  vm.runInContext(source, context);
  await element('pairForm').listeners.submit({preventDefault() {}});
  socket.onopen();
  await socket.onmessage({data: JSON.stringify({kind: 'ready'})});
  await socket.onmessage({data: JSON.stringify({kind: 'sync', sent: 1000100, server: 1000100})});
  for (let i = 0; i < duration * 30 / 1000; ++i) {
    later(100 + i * 1000 / 30 + (i % 2 ? .3 : 0), () => {
      element('video').currentTime = (i + 1) / 30;
      vm.runInContext('sendCurrentVideoFrame()', context);
    });
  }
  while (events.length) {
    events.sort((a, b) => a.at - b.at || a.serial - b.serial);
    const event = events.shift();
    now = event.at;
    await event.fn();
  }
  const gaps = packets.slice(1).map((p, i) => p.at - packets[i].at).sort((a, b) => a - b);
  const report = {rtt_ms: rtt, frames: packets.length, fps: packets.length * 1000 / duration,
    max_pending: maxPending, max_gap_ms: gaps.length ? Math.max(...gaps) : null};
  console.log(JSON.stringify(report));
  if (process.argv[4] !== '--report') {
    assert(maxPending <= 2, 'network delay must never create an upload backlog');
    if (rtt < 0) assert(packets.length <= 2, 'stalled connection must stop sending');
    else {
      assert(report.fps >= 28, 'timing jitter / ACK wait discarded too many fresh frames');
      assert(report.fps <= 31, 'camera pacing exceeded its target');
      assert(report.max_gap_ms < 40, 'ordinary callback jitter must not skip a camera frame');
    }
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
