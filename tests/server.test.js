import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createServer, readServerConfig } from '../server.js';

test('launch settings stay local by default and allow explicit LAN host and HTTPS files', () => {
  assert.deepEqual(readServerConfig({}), { host: '127.0.0.1', port: 4173, keyFile: undefined, certFile: undefined });
  assert.deepEqual(readServerConfig({ HOST: '0.0.0.0', PORT: '4174', TLS_KEY_FILE: 'private/key.pem', TLS_CERT_FILE: 'private/cert.pem' }), {
    host: '0.0.0.0', port: 4174, keyFile: 'private/key.pem', certFile: 'private/cert.pem',
  });
  assert.equal(readServerConfig({ HOST: '::1' }).host, '::1');
  for (const PORT of ['0', '65536', '-1', '1.5', '', 'not-a-port']) {
    assert.throws(() => readServerConfig({ PORT }), /PORT must be/);
  }
  for (const HOST of ['', ' ', ' 0.0.0.0', 'http://localhost']) {
    assert.throws(() => readServerConfig({ HOST }), /HOST must be/);
  }
});

test('incomplete or invalid TLS settings fail instead of falling back to HTTP', () => {
  for (const env of [
    { TLS_KEY_FILE: 'private/key.pem' },
    { TLS_CERT_FILE: 'private/cert.pem' },
    { TLS_KEY_FILE: '', TLS_CERT_FILE: 'private/cert.pem' },
    { TLS_KEY_FILE: 'private/key.pem', TLS_CERT_FILE: ' ' },
  ]) assert.throws(() => readServerConfig(env), /TLS_KEY_FILE and TLS_CERT_FILE must both be set/);
  assert.throws(() => createServer({ tls: {} }), /TLS requires both/);
  assert.throws(() => createServer({ tls: { key: 'invalid-key', cert: 'invalid-certificate' } }));
  const server = createServer();
  assert.ok(server instanceof http.Server);
  assert.equal(server.listening, false);
});

async function fixture(t) {
  const rootDir = await mkdtemp(path.join(os.tmpdir(), 'manux-server-'));
  for (const name of ['public', 'src', 'node_modules/@mediapipe/tasks-vision/wasm']) await mkdir(path.join(rootDir, name), { recursive: true });
  await Promise.all([
    writeFile(path.join(rootDir, 'public/index.html'), '<h1>MANUX</h1>'),
    writeFile(path.join(rootDir, 'public/demo.mp4'), '0123456789'),
    writeFile(path.join(rootDir, 'public/private.env'), 'secret'),
    writeFile(path.join(rootDir, 'secret.js'), 'secret'),
    writeFile(path.join(rootDir, 'src/app.js'), 'export const ready = true;'),
    writeFile(path.join(rootDir, 'node_modules/@mediapipe/tasks-vision/wasm/test.wasm'), 'wasm'),
  ]);
  const server = createServer({ rootDir });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    await new Promise(resolve => server.close(resolve));
    const relative = path.relative(path.resolve(os.tmpdir()), path.resolve(rootDir));
    assert.ok(relative.startsWith('manux-server-') && !relative.includes(path.sep), 'Only remove this test fixture inside the temporary directory');
    await rm(rootDir, { recursive: true, force: true });
  });
  const port = server.address().port;
  // Raw HTTP paths avoid the fetch URL normalizer hiding traversal cases.
  return (url, { method = 'GET', headers = {} } = {}) => new Promise((resolve, reject) => {
    const req = http.request({ hostname: '127.0.0.1', port, path: url, method, headers }, res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString() }));
    });
    req.on('error', reject);
    req.end();
  });
}

test('serves app files, MIME types, health and HEAD without listening on import', async t => {
  const request = await fixture(t);
  const index = await request('/');
  assert.equal(index.status, 200);
  assert.equal(index.body, '<h1>MANUX</h1>');
  assert.match(index.headers['content-type'], /text\/html/);
  assert.equal(index.headers['cache-control'], 'no-store');
  const js = await request('/src/app.js?v=1');
  assert.equal(js.status, 200);
  assert.match(js.headers['content-type'], /text\/javascript/);
  assert.equal(js.headers['access-control-allow-origin'], undefined);
  const wasm = await request('/vendor/wasm/test.wasm');
  assert.equal(wasm.headers['content-type'], 'application/wasm');
  assert.match(wasm.headers['cache-control'], /max-age/);
  const head = await request('/demo.mp4', { method: 'HEAD' });
  assert.equal(head.body, '');
  assert.equal(head.headers['content-length'], '10');
  const health = await request('/health');
  assert.deepEqual(JSON.parse(health.body), { ok: true, app: 'MANUX', version: '0.2.0' });
});

test('supports inclusive, open-ended, suffix and clipped byte ranges', async t => {
  const request = await fixture(t);
  for (const [range, body, contentRange] of [
    ['bytes=2-5', '2345', 'bytes 2-5/10'],
    ['bytes=7-', '789', 'bytes 7-9/10'],
    ['bytes=-3', '789', 'bytes 7-9/10'],
    ['bytes=8-99', '89', 'bytes 8-9/10'],
    ['bytes=-99', '0123456789', 'bytes 0-9/10'],
  ]) {
    const response = await request('/demo.mp4', { headers: { Range: range } });
    assert.equal(response.status, 206, range);
    assert.equal(response.body, body, range);
    assert.equal(response.headers['content-range'], contentRange, range);
    assert.equal(Number(response.headers['content-length']), body.length, range);
  }
  const head = await request('/demo.mp4', { method: 'HEAD', headers: { Range: 'bytes=2-5' } });
  assert.equal(head.status, 206);
  assert.equal(head.body, '');
  assert.equal(head.headers['content-length'], '4');
});

test('rejects invalid or unsupported ranges with the total size', async t => {
  const request = await fixture(t);
  for (const range of ['bytes=10-', 'bytes=8-2', 'bytes=-0', 'bytes=-', 'bytes=0-1,3-4', 'items=0-3', 'bytes=999999999999999999999999-']) {
    const response = await request('/demo.mp4', { headers: { Range: range } });
    assert.equal(response.status, 416, range);
    assert.equal(response.headers['content-range'], 'bytes */10', range);
  }
});

test('blocks traversal, hidden files, unsupported methods and unknown files', async t => {
  const request = await fixture(t);
  for (const url of ['/../secret.js', '/src/../secret.js', '/%2e%2e/secret.js', '/%252e%252e/secret.js', '/src/%2e%2e/secret.js', '/src/..%5csecret.js', '/.env', '/%00.js']) {
    assert.equal((await request(url)).status, 403, url);
  }
  assert.equal((await request('/%invalid')).status, 400);
  assert.equal((await request('/private.env')).status, 404);
  assert.equal((await request('/does-not-exist.js')).status, 404);
  assert.equal((await request('/vendor/wasm/')).status, 404);
  const post = await request('/health', { method: 'POST' });
  assert.equal(post.status, 405);
  assert.equal(post.headers.allow, 'GET, HEAD');
});
