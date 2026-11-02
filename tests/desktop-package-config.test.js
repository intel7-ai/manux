import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';

const read = path => fs.readFile(new URL(`../${path}`, import.meta.url), 'utf8');

test('electron-builder config keeps the helper readable, publishes to this repo as a draft, and is unsigned', async () => {
  const config = await read('electron-builder.yml');
  assert.match(config, /^asar: false$/m, 'the PowerShell helper must be a real file');
  assert.match(config, /^\s+provider: github$/m);
  assert.match(config, /^\s+owner: intel7-ai$/m);
  assert.match(config, /^\s+repo: manux$/m);
  assert.match(config, /^\s+releaseType: draft$/m);
  assert.match(config, /signAndEditExecutable: false/);
  for (const included of ['server.js', 'desktop/**', 'src/**', 'public/**']) assert.ok(config.includes(`- ${included}`), included);
  for (const excluded of ['tests', 'docs', 'scripts', '.local', 'harness']) assert.doesNotMatch(config, new RegExp(`^\\s+- ${excluded}`, 'm'), excluded);
  assert.doesNotMatch(config, /(?:password|secret|token|certificate)\w*\s*:/i, 'no signing or credential settings');
});

test('package scripts, entry point and pinned packaging dependencies', async () => {
  const manifest = JSON.parse(await read('package.json'));
  assert.equal(manifest.main, 'desktop/main.mjs');
  for (const script of ['desktop:pack', 'desktop:dist', 'test:packaged']) assert.ok(manifest.scripts[script], script);
  assert.equal(manifest.scripts.desktop, 'electron desktop/main.mjs', 'the development launch path is unchanged');
  assert.match(manifest.dependencies['electron-updater'], /^\d+\.\d+\.\d+$/);
  assert.match(manifest.devDependencies['electron-builder'], /^\d+\.\d+\.\d+$/);
  assert.equal(manifest.dependencies['electron-builder'], undefined, 'build tooling stays out of the shipped app');
});

test('preview identities cannot replace the stable installer or share its package name', async () => {
  const {desktopChannel, desktopVersion} = await import('../scripts/desktop-channel.mjs');
  const stable=desktopChannel({}), preview=desktopChannel({MANUX_BUILD_CHANNEL:'preview'});
  for(const key of ['appId','productName','executableName','packageName','artifactName']) assert.notEqual(preview[key],stable[key],key);
  assert.equal(desktopChannel({MANUX_BUILD_CHANNEL:'stable'}).channel,'stable');
  assert.throws(()=>desktopChannel({MANUX_BUILD_CHANNEL:'prod-typo'}));
  assert.equal(desktopVersion('0.2.0', 'stable'), '0.2.0');
  assert.equal(desktopVersion('0.2.0', 'preview'), '0.2.0-preview.local');
  assert.equal(desktopVersion('0.2.0-preview.3.20', 'preview'), '0.2.0-preview.3.20');
});

test('local build commands never publish a release', async () => {
  const manifest=JSON.parse(await read('package.json'));
  assert.equal(manifest.scripts['desktop:publish'],undefined);
  const source=await read('scripts/desktop-build.mjs');
  assert.match(source,/'--publish', 'never'/);
});
