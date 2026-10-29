import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createServer} from '../server.js';

// Repeated static frames isolate inference/delegate cost. They do not measure
// physical camera capture, hand-motion accuracy or end-to-end human response.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = Object.fromEntries(process.argv.slice(2).map(arg => {
  const match = /^--([a-z]+)=(.+)$/.exec(arg);
  if (!match) throw new Error('Use --frames=10 --runs=1 --width=1920 --height=1080 --fixture=thumb_up.jpg --output=test-results/vision-benchmark.json');
  return [match[1], match[2]];
}));
const frames = Number(args.frames ?? 10), runs = Number(args.runs ?? 1);
const width = Number(args.width ?? 1920), height = Number(args.height ?? 1080);
if (!Number.isInteger(frames) || frames < 5 || frames > 100 || !Number.isInteger(runs) || runs < 1 || runs > 10 ||
  !Number.isInteger(width) || width < 320 || width > 3840 || !Number.isInteger(height) || height < 240 || height > 2160) throw new Error('Invalid benchmark dimensions or sample counts');
for (const key of Object.keys(args)) if (!['frames', 'runs', 'width', 'height', 'fixture', 'output'].includes(key)) throw new Error(`Unknown option: ${key}`);
const fixture = args.fixture ?? 'thumb_up.jpg';
if (!/^[a-z_]+\.jpg$/.test(fixture)) throw new Error('fixture must be a JPEG basename in tests/fixtures');
const fixtureBytes = await fs.readFile(path.join(root, 'tests/fixtures', fixture));
const output = path.resolve(root, args.output ?? 'test-results/vision-benchmark.json');
const playwrightPath = process.env.PLAYWRIGHT_MODULE || path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs');
const {chromium} = await import(pathToFileURL(playwrightPath));
const launchArgs = ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--enable-unsafe-swiftshader'];
const report = {
  recordedAt: new Date().toISOString(),
  purpose: 'Compare requested CPU and GPU delegates on identical fixed-size canvas frames in independent Web Workers.',
  limitations: [
    'Headless Chrome with SwiftShader permitted; inspect renderer strings. Software GPU results are not physical GPU performance.',
    'No physical webcam is used. A repeated photograph tests wiring and processing cost, not distance support or hand-motion accuracy.',
    'Steady samples exclude the first inference; p95 from 5–10 frames is descriptive only. Repeat on the presentation PC.',
    'Model and GPU shader/driver caches can persist between runs; neither initialization nor first-inference comparisons are controlled cold-start trials.',
    'Repeated identical frames can reuse hand tracking. Moving hands, reacquisition and other applications competing for resources are not represented.',
    'Inference time excludes bitmap creation, message transfer and rendering; roundTripMs includes transfer and inference, not the user gesture.',
  ],
  environment: {platform: os.platform(), release: os.release(), architecture: os.arch(), cpu: os.cpus()[0]?.model, node: process.version, headless: true, launchArgs},
  input: {fixture, width, height, composition: 'dark background; fixture aspect preserved and centered inside a 90% width/height box', steadyFramesPerRun: frames, runs},
  runs: [],
};
const quantile = (values, q) => {
  const sorted = [...values].sort((a, b) => a - b), index = (sorted.length - 1) * q;
  const low = Math.floor(index), fraction = index - low;
  return sorted[low] * (1 - fraction) + sorted[Math.min(low + 1, sorted.length - 1)] * fraction;
};
const summarize = rows => Object.fromEntries(['inferenceMs', 'bitmapMs', 'roundTripMs'].map(metric => {
  const values = rows.map(row => row[metric]);
  return [metric, {count: values.length, p50: quantile(values, .5), p95: quantile(values, .95), min: Math.min(...values), max: Math.max(...values)}];
}));

function benchmarkWorker() {
  let recognizer;
  self.onmessage = async ({data}) => {
    const {id, type, bitmap} = data;
    try {
      if (type === 'init') {
        const started = performance.now();
        const {GestureRecognizer, FilesetResolver} = await import(`${data.base}/vendor/vision_bundle.mjs`);
        const files = await FilesetResolver.forVisionTasks(`${data.base}/vendor/wasm`);
        recognizer = await GestureRecognizer.createFromOptions(files, {
          baseOptions: {modelAssetPath: `${data.base}/models/gesture_recognizer.task`, delegate: data.delegate},
          runningMode: 'VIDEO', numHands: 1, minHandDetectionConfidence: .5, minHandPresenceConfidence: .5, minTrackingConfidence: .5,
        });
        self.postMessage({id, ok: true, initializationMs: performance.now() - started});
      } else if (type === 'frame') {
        const started = performance.now();
        const result = recognizer.recognizeForVideo(bitmap, data.timestamp);
        const inferenceMs = performance.now() - started;
        self.postMessage({id, ok: true, inferenceMs, handCount: result.landmarks.length,
          gestures: result.gestures.map(list => ({name: list[0]?.categoryName, score: list[0]?.score}))});
      } else if (type === 'close') {
        recognizer?.close(); recognizer = null;
        // Probe after measured frames so creating a debug context does not add
        // work inside any inference timing. This is the worker WebGL backend.
        const canvas = new OffscreenCanvas(1, 1);
        const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
        let workerWebgl = {available: false};
        if (gl) {
          const ext = gl.getExtension('WEBGL_debug_renderer_info');
          workerWebgl = {available: true, vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
            renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)};
          gl.getExtension('WEBGL_lose_context')?.loseContext();
        }
        self.postMessage({id, ok: true, workerWebgl});
      }
    } catch (error) { self.postMessage({id, ok: false, error: error.message}); }
    finally { bitmap?.close(); }
  };
}

const server = createServer();
let browser;
try {
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true, args: launchArgs});
  report.environment.browser = browser.version();
  const context = await browser.newContext({viewport: {width: 1200, height: 800}});
  const page = await context.newPage();
  await page.route('**/benchmark-fixture.jpg', route => route.fulfill({body: fixtureBytes, contentType: 'image/jpeg'}));
  // Avoid loading app.js: no production worker or camera can compete for CPU.
  await page.goto(`${base}/health`);
  report.environment.webgl = await page.evaluate(() => {
    const gl = document.createElement('canvas').getContext('webgl2') || document.createElement('canvas').getContext('webgl');
    if (!gl) return {available: false};
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    return {available: true, vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR), renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)};
  });
  report.environment.userAgent = await page.evaluate(() => navigator.userAgent);
  for (let run = 1; run <= runs; run++) {
    // Alternate order when repeating to expose order/cache/thermal differences.
    for (const delegate of run % 2 ? ['CPU', 'GPU'] : ['GPU', 'CPU']) {
      console.log(`Benchmark run ${run}/${runs}: ${delegate}, ${width}x${height}, first + ${frames} steady frames`);
      const row = await page.evaluate(async ({base, delegate, width, height, frames, workerSource}) => {
        const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
        const ctx = canvas.getContext('2d'); ctx.fillStyle = '#202020'; ctx.fillRect(0, 0, width, height);
        const image = new Image(); image.src = `${base}/benchmark-fixture.jpg`; await image.decode();
        const scale = Math.min(width * .9 / image.width, height * .9 / image.height);
        ctx.drawImage(image, (width - image.width * scale) / 2, (height - image.height * scale) / 2, image.width * scale, image.height * scale);
        const blobUrl = URL.createObjectURL(new Blob([workerSource], {type: 'text/javascript'}));
        const worker = new Worker(blobUrl); let nextId = 0;
        const pending = new Map();
        const request = (type, data = {}, transfer = []) => new Promise((resolve, reject) => {
          const id = ++nextId;
          const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${type} timed out`)); }, 60000);
          pending.set(id, {resolve, reject, timer});
          worker.postMessage({id, type, ...data}, transfer);
        });
        worker.onmessage = ({data}) => {
          const task = pending.get(data.id); if (!task) return;
          clearTimeout(task.timer); pending.delete(data.id);
          data.ok ? task.resolve(data) : task.reject(new Error(data.error));
        };
        worker.onerror = error => {
          for (const task of pending.values()) { clearTimeout(task.timer); task.reject(new Error(error.message)); }
          pending.clear();
        };
        try {
          const init = await request('init', {base, delegate});
          const observations = [];
          for (let frame = 0; frame <= frames; frame++) {
            const bitmapStart = performance.now(), bitmap = await createImageBitmap(canvas);
            const bitmapMs = performance.now() - bitmapStart, sent = performance.now();
            const result = await request('frame', {bitmap, timestamp: frame * 33.333 + 1}, [bitmap]);
            observations.push({frame, inferenceMs: result.inferenceMs, bitmapMs, roundTripMs: performance.now() - sent, handCount: result.handCount, gestures: result.gestures});
          }
          const closed = await request('close');
          return {status: 'ok', initializationMs: init.initializationMs, workerWebgl: closed.workerWebgl, firstFrame: observations[0], steadyFrames: observations.slice(1)};
        } catch (error) { return {status: 'error', error: error.message}; }
        finally {
          for (const task of pending.values()) clearTimeout(task.timer);
          pending.clear(); worker.terminate(); URL.revokeObjectURL(blobUrl);
        }
      }, {base, delegate, width, height, frames, workerSource: `(${benchmarkWorker.toString()})();`});
      if (row.status === 'ok') row.steady = summarize(row.steadyFrames);
      report.runs.push({run, delegate, ...row});
      console.log(row.status === 'ok' ? `${delegate}: first ${row.firstFrame.inferenceMs.toFixed(1)}ms; steady p50 ${row.steady.inferenceMs.p50.toFixed(1)}ms, p95 ${row.steady.inferenceMs.p95.toFixed(1)}ms` : `${delegate}: ${row.error}`);
    }
  }
  report.status = report.runs.every(row => row.status === 'ok') ? 'completed' : 'partial';
  report.aggregate = Object.fromEntries(['CPU', 'GPU'].map(delegate => {
    const rows = report.runs.filter(row => row.delegate === delegate && row.status === 'ok');
    return [delegate, rows.length ? {successfulRuns: rows.length, firstFrameMs: rows.map(row => row.firstFrame.inferenceMs), steady: summarize(rows.flatMap(row => row.steadyFrames))} : null];
  }));
  await fs.mkdir(path.dirname(output), {recursive: true});
  await fs.writeFile(output, `${JSON.stringify(report, null, 2)}\n`);
  console.log(`Saved ${path.relative(root, output)}`);
  if (report.status !== 'completed') process.exitCode = 1;
} finally {
  await browser?.close();
  if (server.listening) await new Promise(resolve => server.close(resolve));
}
