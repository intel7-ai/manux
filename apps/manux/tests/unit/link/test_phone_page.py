"""Execute the shipped inline script with fake browser APIs, not a real phone or getUserMedia."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[3] / "src/manux/link/static/phone.html"


def test_page_auth_backpressure_switch_and_cleanup():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the simulated browser check")
    script = re.search(r"<script>(.*?)</script>", PAGE.read_text(encoding="utf-8"), re.S).group(1)
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
let source = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', part => source += part);
process.stdin.on('end', async () => {
 try {
  const elements = new Map();
  let clock = 100, requests = [], blobs = [], sockets = [], tracks = [], released = 0;
  let screenRequests = [], revoked = [], objectUrls = 0, docListeners = {};
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: id === 'camera' ? 'environment' : '123456', disabled: false,
      classList: {values: new Set(), add(v) {this.values.add(v);}, remove(v) {this.values.delete(v);}}, listeners: {},
      removeAttribute(name) { delete this[name]; },
      addEventListener(name, fn) { this.listeners[name] = fn; },
      getContext() { return {drawImage() {}}; },
      toBlob(fn, type, quality) { assert.equal(type, 'image/jpeg'); assert.equal(quality, .55); blobs.push(fn); },
      readyState: 2, currentTime: 1, videoWidth: 480, videoHeight: 360,
      play: async () => {}, requestVideoFrameCallback: () => 1, cancelVideoFrameCallback() {}
    });
    return elements.get(id);
  }
  class Socket {
    static OPEN = 1;
    constructor(url) { assert.equal(url, 'wss://192.168.0.1:8443/ws'); this.readyState = 0; this.bufferedAmount = 0; this.sent = []; sockets.push(this); }
    send(value) { this.sent.push(value); }
    close() { this.readyState = 3; }
  }
  const context = vm.createContext({
    document: {getElementById: element, visibilityState: 'visible', hidden: false,
      addEventListener(name, fn) {docListeners[name] = fn;}, fullscreenElement: null,
      exitFullscreen: async () => {context.document.fullscreenElement = null;}},
    navigator: {userAgent: 'Synthetic browser',
      mediaDevices: {getUserMedia: async config => {
        requests.push(config);
        const track = {stopped: false, stop() { this.stopped = true; }, getSettings() { return {facingMode: config.video.facingMode.ideal}; }};
        tracks.push(track);
        return {getTracks: () => [track], getVideoTracks: () => [track]};
      }},
      wakeLock: {request: async () => ({addEventListener() {}, release: async () => { ++released; }})}},
    window: {isSecureContext: true, addEventListener() {}},
    location: {host: '192.168.0.1:8443'}, WebSocket: Socket,
    performance: {now: () => clock}, TextEncoder, Uint8Array, DataView,
    fetch: async (url, options) => {
      if (url === '/pair') return {ok: true, json: async () => ({token: 'synthetic-token'})};
      assert.equal(url, '/screen');
      assert.equal(options.headers.Authorization, 'Bearer synthetic-screen');
      assert.equal(options.cache, 'no-store');
      return await new Promise(resolve => screenRequests.push({resolve, options}));
    },
    AbortController, Image: class {set src(value) {queueMicrotask(() => this.onload?.());}},
    URL: {createObjectURL: () => 'blob:synthetic-' + (++objectUrls), revokeObjectURL: url => revoked.push(url)},
    setTimeout: () => 1, clearTimeout() {},
    setInterval: () => 1, clearInterval() {}, requestAnimationFrame: () => 1, cancelAnimationFrame() {},
    console
  });
  vm.runInContext(source, context);
  element('viewer').requestFullscreen = async () => {context.document.fullscreenElement = element('viewer');};
  assert.equal(requests.length, 0); // no camera before the user pairs
  await element('pairForm').listeners.submit({preventDefault() {}});
  const socket = sockets[0];
  socket.readyState = 1; socket.onopen();
  assert.deepEqual(JSON.parse(socket.sent[0]), {kind:'auth', token:'synthetic-token'});
  assert.equal(requests.length, 0); // still no camera until server authenticates
  await socket.onmessage({data: JSON.stringify({kind:'ready', screen_token:'synthetic-screen'})});
  assert.equal(requests.length, 1);
  assert.equal(requests[0].audio, false);
  assert.equal(requests[0].video.width.ideal, 480);
  assert.equal(requests[0].video.facingMode.ideal, 'environment');
  assert.equal(screenRequests.length, 0); // rear camera does not request desktop pixels
  await socket.onmessage({data: JSON.stringify({kind:'sync', sent:Date.now(), server:Date.now()})});
  vm.runInContext('sendCurrentVideoFrame()', context);
  await blobs.shift()({arrayBuffer: async () => new Uint8Array([255,216,255,217]).buffer});
  const firstPacket = socket.sent.find(v => typeof v !== 'string');
  assert(firstPacket);
  const length = new DataView(firstPacket.buffer).getUint32(0);
  const meta = JSON.parse(new TextDecoder().decode(firstPacket.slice(4, 4 + length)));
  assert.equal(meta.kind, 'frame'); assert.equal(meta.mirrored, false); assert.equal(meta.rotation, 0);
  assert.equal(meta.width, 480); assert.equal(meta.height, 360);
  const count = socket.sent.length;
  clock += 100; element('video').currentTime += 1;
  vm.runInContext('sendCurrentVideoFrame()', context);
  assert.equal(blobs.length, 0); assert.equal(socket.sent.length, count); // await ack, skip instead of queue
  await socket.onmessage({data: JSON.stringify({kind:'ack', accepted:true})});
  socket.bufferedAmount = 100;
  vm.runInContext('sendCurrentVideoFrame()', context);
  assert.equal(blobs.length, 0); // socket busy, skip
  socket.bufferedAmount = 0;
  await vm.runInContext("(async () => { facingMode = 'user'; await startCamera(); })()", context);
  assert(tracks[0].stopped);
  assert.equal(requests[1].video.facingMode.ideal, 'user');
  assert(element('viewer').classList.values.has('pc-screen'));
  assert.equal(screenRequests.length, 1);
  screenRequests[0].resolve({ok:true, blob:async () => ({type:'image/jpeg'})});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(element('desktopScreen').src, 'blob:synthetic-1');
  await element('fullscreenBtn').listeners.click();
  assert.equal(context.document.fullscreenElement, element('viewer'));
  element('exitFullscreen').listeners.click();
  assert.equal(context.document.fullscreenElement, null);
  element('viewer').requestFullscreen = async () => {throw new Error('denied');};
  await element('fullscreenBtn').listeners.click();
  assert.equal(socket.readyState, 1); // fullscreen refusal must not stop the camera
  context.document.hidden = true; docListeners.visibilitychange();
  assert.equal(element('desktopScreen').src, undefined);
  assert(revoked.includes('blob:synthetic-1'));
  context.document.hidden = false; docListeners.visibilitychange();
  assert.equal(screenRequests.length, 2);
  clock += 100; element('video').currentTime += 1;
  vm.runInContext('sendCurrentVideoFrame()', context);
  const delayedBlob = blobs.shift();
  assert(delayedBlob);
  element('disconnectBtn').listeners.click();
  assert(tracks.every(t => t.stopped)); assert.equal(socket.readyState, 3); assert(released > 0);
  await delayedBlob({arrayBuffer: async () => new Uint8Array([255,216,255,217]).buffer});
  assert(screenRequests[1].options.signal.aborted);
  screenRequests[1].resolve({ok:true, blob:async () => ({type:'image/jpeg'})});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(element('desktopScreen').src, undefined); // delayed response after disconnect discarded
  assert(!element('viewer').classList.values.has('pc-screen'));
  assert.equal(socket.sent.length, count); // no async stale toBlob result after disconnect
  console.log('PASS simulated phone auth, camera ack gating, screen backpressure/cleanup and fullscreen');
 } catch(error) { console.error(error); process.exitCode = 1; }
});
"""
    result = subprocess.run([node, "-e", harness], input=script, text=True, encoding="utf-8",
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
