import test from 'node:test';
import assert from 'node:assert/strict';
import { VisionClient } from '../src/vision-client.js';

class WorkerMock {
  messages = [];
  terminated = 0;
  postError = null;
  postMessage(message, transfer) {
    if (this.postError) throw this.postError;
    this.messages.push({ message, transfer });
  }
  reply(index, data = {}) {
    this.onmessage?.({ data: { id: this.messages[index].message.id, ok: true, ...data } });
  }
  terminate() { this.terminated++; }
}

function bitmap() {
  return { closed: 0, close() { this.closed++; } };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function setup(t, options = {}) {
  const worker = new WorkerMock();
  const bitmaps = [];
  let factories = 0;
  const client = new VisionClient({
    workerFactory: () => { factories++; return worker; },
    createBitmap: async () => { const image = bitmap(); bitmaps.push(image); return image; },
    ...options,
  });
  t.after(() => client.close());
  return { client, worker, bitmaps, factories: () => factories };
}

async function ready(client, worker) {
  const initialization = client.init();
  worker.reply(0, { delegate: 'CPU' });
  await initialization;
}

test('concurrent init calls share one worker, one request and one promise', async (t) => {
  const { client, worker, factories } = setup(t);
  const first = client.init();
  const second = client.init();
  assert.equal(first, second);
  assert.equal(factories(), 1);
  assert.equal(worker.messages.length, 1);
  worker.reply(0, { delegate: 'GPU' });
  assert.equal((await first).delegate, 'GPU');
  assert.equal(client.ready, true);
  assert.equal(await client.init(), await first);
  assert.equal(factories(), 1);
});

test('uninitialized recognition rejects without allocating an ImageBitmap', async (t) => {
  const { client, bitmaps } = setup(t);
  await assert.rejects(client.recognize({}, 100), /아직 준비/);
  assert.equal(bitmaps.length, 0);
  assert.equal(client.busy, false);
});

test('only one frame is allowed during bitmap creation and worker inference', async (t) => {
  const conversion = deferred();
  let conversions = 0;
  const { client, worker } = setup(t, { createBitmap: () => { conversions++; return conversion.promise; } });
  await ready(client, worker);
  const first = client.recognize({}, 100);
  assert.equal(await client.recognize({}, 101), null);
  assert.equal(conversions, 1);
  assert.equal(worker.messages.length, 1);
  const image = bitmap();
  conversion.resolve(image);
  await Promise.resolve();
  assert.equal(worker.messages.length, 2);
  assert.equal(worker.messages[1].message.timestamp, 100);
  assert.deepEqual(worker.messages[1].transfer, [image]);
  assert.equal(await client.recognize({}, 102), null);
  assert.equal(conversions, 1);
  worker.reply(1, { result: { landmarks: [] }, inferenceMs: 12 });
  assert.equal((await first).inferenceMs, 12);
  assert.equal(client.busy, false);
  assert.equal(client.pending.size, 0);
  assert.equal(image.closed, 0, 'successfully transferred images belong to the worker');
});

test('createImageBitmap rejection clears busy and permits a later frame', async (t) => {
  let attempts = 0;
  const { client, worker } = setup(t, {
    createBitmap: async () => {
      if (++attempts === 1) throw new Error('source video unavailable');
      return bitmap();
    },
  });
  await ready(client, worker);
  await assert.rejects(client.recognize({}, 100), /source video unavailable/);
  assert.equal(client.busy, false);
  assert.equal(client.pending.size, 0);
  const second = client.recognize({}, 200);
  await Promise.resolve();
  worker.reply(1, { result: {} });
  await second;
  assert.equal(attempts, 2);
});

test('close during asynchronous bitmap creation disposes it without posting a frame', async (t) => {
  const conversion = deferred();
  const { client, worker } = setup(t, { createBitmap: () => conversion.promise });
  await ready(client, worker);
  const recognition = client.recognize({}, 100);
  client.close();
  const image = bitmap();
  conversion.resolve(image);
  assert.equal(await recognition, null);
  assert.equal(image.closed, 1);
  assert.equal(worker.messages.length, 1);
  assert.equal(worker.terminated, 1);
  assert.equal(client.pending.size, 0);
});

test('postMessage failure closes the untransferred bitmap and clears pending state', async (t) => {
  const { client, worker, bitmaps } = setup(t);
  await ready(client, worker);
  worker.postError = new Error('DataCloneError');
  await assert.rejects(client.recognize({}, 100), /DataCloneError/);
  assert.equal(bitmaps[0].closed, 1);
  assert.equal(client.pending.size, 0);
  assert.equal(client.busy, false);
  assert.equal(client.closed, false);
  worker.postError = null;
  const retry = client.recognize({}, 200);
  await Promise.resolve();
  worker.reply(1);
  await retry;
});

test('frame timeout terminates the worker instead of allowing a hidden processing backlog', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { client, worker } = setup(t, { frameTimeoutMs: 100 });
  await ready(client, worker);
  const recognition = client.recognize({}, 100);
  const rejection = assert.rejects(recognition, /영상 처리 시간이 초과/);
  await Promise.resolve();
  assert.equal(client.pending.size, 1);
  t.mock.timers.tick(100);
  await rejection;
  assert.equal(client.closed, true);
  assert.equal(client.busy, false);
  assert.equal(client.pending.size, 0);
  assert.equal(worker.terminated, 1);
  assert.equal(await client.recognize({}, 200), null);
  assert.equal(worker.messages.length, 2);
});

test('init timeout clears the pending request and releases the worker', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { client, worker } = setup(t, { initTimeoutMs: 50 });
  const initialization = client.init();
  const rejection = assert.rejects(initialization, /모델 준비 시간이 초과/);
  t.mock.timers.tick(50);
  await rejection;
  assert.equal(client.closed, true);
  assert.equal(client.ready, false);
  assert.equal(client.pending.size, 0);
  assert.equal(worker.terminated, 1);
});

test('closing an in-flight request rejects once and ignores a late worker response', async (t) => {
  const { client, worker, factories } = setup(t);
  await ready(client, worker);
  const recognition = client.recognize({}, 100);
  const rejection = assert.rejects(recognition, /종료/);
  await Promise.resolve();
  const lateHandler = worker.onmessage;
  const frameId = worker.messages[1].message.id;
  client.close();
  client.close();
  lateHandler({ data: { id: frameId, ok: true, result: {} } });
  await rejection;
  assert.equal(client.pending.size, 0);
  assert.equal(worker.terminated, 1);
  await assert.rejects(client.init(), /종료/);
  assert.equal(factories(), 1, 'a closed client must never allocate another worker');
});

test('worker fatal errors terminate the session and reject current inference promptly', async (t) => {
  for (const eventType of ['onerror', 'onmessageerror']) {
    const { client, worker } = setup(t);
    await ready(client, worker);
    const recognition = client.recognize({}, 100);
    const rejection = assert.rejects(recognition, /worker exploded|읽을 수 없습니다/);
    await Promise.resolve();
    worker[eventType]({ message: 'worker exploded' });
    await rejection;
    assert.equal(client.closed, true);
    assert.equal(client.pending.size, 0);
    assert.equal(worker.terminated, 1);
  }
});

test('model initialization failure releases the failed worker', async (t) => {
  const { client, worker } = setup(t);
  const initialization = client.init();
  const rejection = assert.rejects(initialization, /model invalid/);
  worker.reply(0, { ok: false, error: 'model invalid' });
  await rejection;
  assert.equal(client.ready, false);
  assert.equal(client.closed, true);
  assert.equal(worker.terminated, 1);
});

test('closing immediately after an init response prevents a ready-state resurrection', async (t) => {
  const { client, worker } = setup(t);
  const initialization = client.init();
  const rejection = assert.rejects(initialization, /종료/);
  worker.reply(0);
  client.close();
  await rejection;
  assert.equal(client.ready, false);
  assert.equal(worker.terminated, 1);
});

test('frame-level inference errors release busy while a healthy worker remains usable', async (t) => {
  const { client, worker } = setup(t);
  await ready(client, worker);
  const first = client.recognize({}, 100);
  const rejection = assert.rejects(first, /bad frame/);
  await Promise.resolve();
  worker.reply(1, { ok: false, error: 'bad frame' });
  await rejection;
  assert.equal(client.busy, false);
  assert.equal(client.closed, false);
  const retry = client.recognize({}, 200);
  await Promise.resolve();
  worker.reply(2);
  await retry;
});

test('unrelated or duplicate worker replies cannot settle another pending frame', async (t) => {
  const { client, worker } = setup(t);
  await ready(client, worker);
  const recognition = client.recognize({}, 100);
  await Promise.resolve();
  worker.onmessage({ data: null });
  worker.onmessage({ data: { id: 9999, ok: true } });
  worker.reply(0);
  assert.equal(client.pending.size, 1);
  assert.equal(client.busy, true);
  worker.reply(1);
  await recognition;
  assert.equal(client.pending.size, 0);
});
