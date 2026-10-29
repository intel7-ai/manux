import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL, fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {createServer} from '../server.js';
import {POSES, trainEnvironment, classifyPersonal} from '../src/personalization.js';

// Reference-image integration probe only. Variants share the same source image;
// passing this probe does not measure human, webcam, or desk/sofa accuracy.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const playwrightPath = process.env.PLAYWRIGHT_MODULE || path.join(process.env.USERPROFILE,
  '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs');
const {chromium} = await import(pathToFileURL(playwrightPath));
const manifest = JSON.parse(await fs.readFile(path.join(root, 'tests/fixtures/sources.json'), 'utf8'));
const fixtures = POSES.map(pose => {
  const item = manifest.files.find(file => (file.personalPose ?? file.expectedGesture) === pose);
  assert.ok(item, `Missing fixture for ${pose}`);
  return {...item, pose};
});
for (const fixture of fixtures) {
  const bytes = await fs.readFile(path.join(root, 'tests/fixtures', fixture.file));
  assert.equal(createHash('sha256').update(bytes).digest('hex'), fixture.sha256, fixture.file);
}

const server = createServer();
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
let browser;
try {
  browser = await chromium.launch({channel: 'chrome', headless: true, args: ['--enable-unsafe-swiftshader']});
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {if (response.status() >= 400) console.error(`HTTP ${response.status()}: ${response.url()}`);});
  page.on('requestfailed', request => console.error(`Request failed: ${request.url()} ${request.failure()?.errorText}`));
  await page.route('**/probe', route => route.fulfill({body: '<!doctype html><title>Reference probe</title>', contentType: 'text/html'}));
  await page.route('**/test-assets/*', async route => {
    const name = new URL(route.request().url()).pathname.split('/').pop();
    assert.ok(fixtures.some(fixture => fixture.file === name), `Unexpected fixture ${name}`);
    await route.fulfill({body: await fs.readFile(path.join(root, 'tests/fixtures', name)), contentType: 'image/jpeg'});
  });
  await page.goto(`${base}/probe`);
  const observations = await page.evaluate(async fixtures => {
    const {GestureRecognizer, FilesetResolver} = await import('/vendor/vision_bundle.mjs');
    const {extractFeatures, getHandQuality} = await import('/src/personalization.js');
    const files = await FilesetResolver.forVisionTasks('/vendor/wasm');
    const recognizer = await GestureRecognizer.createFromOptions(files, {
      baseOptions: {modelAssetPath: '/models/gesture_recognizer.task', delegate: 'CPU'},
      runningMode: 'IMAGE', numHands: 1
    });
    const results = [];
    try {
      for (const fixture of fixtures) {
        const image = new Image(); image.src = '/test-assets/' + fixture.file;
        await image.decode();
        // Re-run landmark inference on eight training and two verification
        // variants. Verification variants are withheld, but remain the SAME
        // source photograph and cannot establish real-world generalization.
        const variants = [
          ...[-4, -3, -2, -1, 0, 1, 2, 3].map(angle => ({split: 'training', angle, scale: 1})),
          {split: 'verification', angle: -1.5, scale: .97},
          {split: 'verification', angle: 1.5, scale: 1.03}
        ];
        const rows = [];
        for (const variant of variants) {
          const canvas = document.createElement('canvas');
          canvas.width = image.width; canvas.height = image.height;
          const ctx = canvas.getContext('2d');
          ctx.fillStyle = '#ddd'; ctx.fillRect(0, 0, canvas.width, canvas.height);
          ctx.translate(canvas.width / 2, canvas.height / 2);
          ctx.rotate(variant.angle * Math.PI / 180); ctx.scale(variant.scale, variant.scale);
          ctx.drawImage(image, -image.width / 2, -image.height / 2);
          const result = recognizer.recognize(canvas);
          const landmarks = result.landmarks[0] ?? null;
          const dimensions = {width: canvas.width, height: canvas.height};
          rows.push({...variant, canned: result.gestures[0]?.[0] ?? null, landmarks,
            features: extractFeatures(landmarks, dimensions), quality: getHandQuality(landmarks, dimensions)});
        }
        results.push({file: fixture.file, pose: fixture.pose, expectedGesture: fixture.expectedGesture,
          width: image.width, height: image.height, rows});
      }
    } finally {recognizer.close();}
    return results;
  }, fixtures);

  const samples = Object.fromEntries(observations.map(observation => [observation.pose,
    observation.rows.filter(row => row.split === 'training').map(row => row.features)]));
  const model = trainEnvironment(samples);
  const summary = observations.map(observation => ({
    file: observation.file, pose: observation.pose,
    training: observation.rows.filter(row => row.split === 'training').length,
    qualityPassed: observation.rows.filter(row => row.quality.ok).length,
    cannedGestures: [...new Set(observation.rows.map(row => row.canned?.categoryName))],
    verification: observation.rows.filter(row => row.split === 'verification').map(row => classifyPersonal(row.features, model))
  }));
  const report = {
    purpose: 'Actual MediaPipe landmark integration on five official reference photographs, with derived variants of each source. Not webcam accuracy or independent-subject accuracy.',
    recordedAt: new Date().toISOString(), modelUsable: model.usable, warnings: model.warnings,
    modelDelegate: 'CPU', runningMode: 'IMAGE', hashesVerified: true, summary, pageErrors: errors
  };
  await fs.mkdir(path.join(root, 'test-results'), {recursive: true});
  await fs.writeFile(path.join(root, 'test-results/personalization-probe.json'), JSON.stringify(report, null, 2));
  await fs.writeFile(path.join(root, 'tests/fixtures/personalization-reference.json'), JSON.stringify({
    ...report, fixtures: fixtures.map(({file, url, sha256, pose}) => ({file, url, sha256, pose})), observations, model
  }, null, 2));
  console.log(JSON.stringify(report, null, 2));
  assert.deepEqual(errors, []);
  assert.equal(model.usable, true, JSON.stringify(model.warnings));
  for (const row of summary) {
    assert.equal(row.qualityPassed, 10, `Quality check failed: ${row.file}`);
    for (const result of row.verification) assert.equal(result.gesture, row.pose, `${row.file}: ${JSON.stringify(result)}`);
  }
} finally {
  if (browser) await browser.close();
  await new Promise(resolve => server.close(resolve));
}
