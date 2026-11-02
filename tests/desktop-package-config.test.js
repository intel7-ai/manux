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
  for (const script of ['desktop:pack', 'desktop:dist', 'desktop:publish', 'test:packaged']) assert.ok(manifest.scripts[script], script);
  assert.equal(manifest.scripts.desktop, 'electron desktop/main.mjs', 'the development launch path is unchanged');
  assert.match(manifest.dependencies['electron-updater'], /^\d+\.\d+\.\d+$/);
  assert.match(manifest.devDependencies['electron-builder'], /^\d+\.\d+\.\d+$/);
  assert.equal(manifest.dependencies['electron-builder'], undefined, 'build tooling stays out of the shipped app');
});

test('release workflow is tag-triggered, least-privilege, pinned and secret-free', async () => {
  const workflow = await read('.github/workflows/release.yml');
  assert.match(workflow, /^on:\s*\n\s+push:\s*\n\s+tags:\s*\n\s+- 'v\*'/m);
  assert.doesNotMatch(workflow, /pull_request|workflow_dispatch/);
  assert.match(workflow, /^permissions: \{\}$/m, 'no default token permissions');
  assert.match(workflow, /^\s+permissions:\s*\n\s+contents: write$/m, 'only the release job can write contents');
  assert.match(workflow, /runs-on: windows-latest/);
  for (const step of ['npm ci --ignore-scripts', 'npm run verify', 'npm run desktop:pack', 'npm run test:packaged', 'npm run desktop:publish']) {
    assert.ok(workflow.includes(step), step);
  }
  assert.ok(workflow.indexOf('npm run verify') < workflow.indexOf('npm run desktop:publish'), 'verify before publish');
  for (const use of workflow.matchAll(/uses:\s*(\S+)/g)) assert.match(use[1], /@[0-9a-f]{40}$/, `unpinned action ${use[1]}`);
  const secrets = [...workflow.matchAll(/secrets\.([A-Za-z_]+)/g)].map(match => match[1]);
  assert.deepEqual([...new Set(secrets)], ['GITHUB_TOKEN']);
  assert.doesNotMatch(workflow, /(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}|CSC_LINK|CSC_KEY_PASSWORD/);
});
