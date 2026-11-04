import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createHash} from 'node:crypto';
import {buildVersion, compareVersions, createManifest, verifyArtifact, validateManifest, validateUpdateMetadata, validateExistingRelease} from '../scripts/release-version.mjs';

const context = {repository: 'intel7-ai/manux', sourceSha: 'a'.repeat(40)};
const installer = Buffer.from('synthetic installer bytes, never executed');
const metadata = (version = '0.2.7') => {
  const sha512 = createHash('sha512').update(installer).digest('base64');
  return `version: ${version}\nfiles:\n  - url: MANUX-Setup-${version}.exe\n    sha512: ${sha512}\n    size: ${installer.length}\npath: MANUX-Setup-${version}.exe\nsha512: ${sha512}\nreleaseDate: '2026-11-02T00:00:00.000Z'\n`;
};

async function fixture(t) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'manux-release-test-'));
  t.after(() => fs.rm(dir, {recursive: true, force: true}));
  await fs.writeFile(path.join(dir, 'MANUX-Setup-0.2.7.exe'), installer);
  await fs.writeFile(path.join(dir, 'MANUX-Setup-0.2.7.exe.blockmap'), 'synthetic blockmap');
  await fs.writeFile(path.join(dir, 'latest.yml'), metadata());
  return {dir, manifest: await createManifest(dir, '0.2.7', context)};
}

test('production runs get increasing Windows-compatible versions; retries keep the same version', () => {
  assert.equal(buildVersion('0.2.0', '7'), '0.2.7');
  assert.equal(buildVersion('0.2.0', '8'), '0.2.8');
  assert.equal(buildVersion('0.2.0', '7'), '0.2.7');
  assert.equal(compareVersions('0.2.10', '0.2.9'), 1);
  assert.equal(compareVersions('0.2.7', '0.2.7'), 0);
  assert.equal(compareVersions('0.2.8', '0.3.0'), -1);
  for (const run of ['0', '-1', '1;echo bad', undefined, '0007']) assert.throws(() => buildVersion('0.2.0', run));
  for (const base of ['v0.2.0', '0.2.0-beta.1', '0.2', '0.02.0', '1.2.65535']) assert.throws(() => buildVersion(base, 1));
});

test('preview versions identify PR and run and cannot pass the stable comparison', () => {
  const version = buildVersion('0.2.0', '99', {channel: 'preview', pullRequest: '23'});
  assert.equal(version, '0.2.0-preview.23.99');
  assert.throws(() => compareVersions(version, '0.2.0'));
  assert.throws(() => buildVersion('0.2.0', 1, {channel: 'preview', pullRequest: '../../'}));
  assert.throws(() => buildVersion('0.2.0', 1, {channel: 'untrusted'}));
});

test('exact verified installer, blockmap and update metadata survive artifact transfer', async t => {
  const {dir, manifest} = await fixture(t);
  assert.deepEqual(await verifyArtifact(dir, context), manifest);
  await fs.appendFile(path.join(dir, 'MANUX-Setup-0.2.7.exe'), 'tampered');
  await assert.rejects(verifyArtifact(dir, context), /변경/);
});

test('artifact commit, repository, paths and duplicate entries cannot be substituted', async t => {
  const {manifest} = await fixture(t);
  for (const changed of [
    {...manifest, sourceSha: 'b'.repeat(40)},
    {...manifest, repository: 'somewhere/else'},
    {...manifest, tag: 'v9.9.9'},
    {...manifest, files: [{...manifest.files[0], name: '../installer.exe'}, ...manifest.files.slice(1)]},
    {...manifest, files: [manifest.files[0], manifest.files[0], manifest.files[2]]},
    {...manifest, files: manifest.files.map(file => ({...file, sha256: 'invalid'}))},
  ]) assert.throws(() => validateManifest(changed, context));
});

test('update feed must name and hash precisely the verified installer', () => {
  validateUpdateMetadata(metadata(), '0.2.7', installer);
  for (const altered of [
    metadata('0.2.8'),
    metadata().replace('url: MANUX-', 'url: https://example.com/MANUX-'),
    metadata().replace('path: MANUX-', 'path: ../MANUX-'),
    metadata().replace(/sha512: \S+/, 'sha512: bad'),
    metadata() + 'version: 9.9.9\n',
  ]) assert.throws(() => validateUpdateMetadata(altered, '0.2.7', installer));
});

test('reruns accept only identical published assets and never overwrite a draft or changed tag', async t => {
  const {manifest} = await fixture(t);
  const release = {tag_name: manifest.tag, draft: false, prerelease: false,
    assets: manifest.files.map(file => ({name: file.name, size: file.bytes, digest: `sha256:${file.sha256}`}))};
  validateExistingRelease(release, manifest, context.sourceSha);
  assert.throws(() => validateExistingRelease(release, manifest, 'b'.repeat(40)));
  for (const altered of [
    {...release, draft: true}, {...release, prerelease: true}, {...release, assets: []},
    {...release, assets: release.assets.map(asset => ({...asset, digest: 'sha256:bad'}))},
  ]) assert.throws(() => validateExistingRelease(altered, manifest, context.sourceSha));
});

test('PR checks require the isolated preview package and cannot access a release write token', async () => {
  const workflow = (await fs.readFile(new URL('../.github/workflows/ci.yml', import.meta.url), 'utf8')).replaceAll('\r\n', '\n');
  assert.match(workflow, /needs: \[verify, browser, windows-package\]/);
  assert.match(workflow, /PACKAGE_RESULT.*needs\.windows-package\.result/);
  assert.match(workflow, /test "\$PACKAGE_RESULT" = success/);
  assert.match(workflow, /MANUX_BUILD_CHANNEL: preview/);
  assert.match(workflow, /release-version\.mjs prepare preview/);
  assert.ok(workflow.indexOf('npm run desktop:dist') < workflow.indexOf('npm run test:packaged'));
  assert.ok(workflow.indexOf('npm run test:packaged') < workflow.indexOf('npm run test:update-download'));
  assert.doesNotMatch(workflow, /contents: write|GH_TOKEN|secrets\.|pull_request_target|desktop:publish/);
});

test('production only deploys successful immutable artifacts with a separate publishing job', async () => {
  const workflow = (await fs.readFile(new URL('../.github/workflows/release.yml', import.meta.url), 'utf8')).replaceAll('\r\n', '\n');
  assert.match(workflow, /branches: \[production\]/);
  assert.doesNotMatch(workflow, /tags:|workflow_dispatch:|pull_request_target|desktop:publish/);
  assert.match(workflow, /^permissions: \{\}$/m);
  assert.match(workflow, /cancel-in-progress: false\n  queue: max/);
  const [build, publish] = workflow.split('\n  publish:');
  assert.match(build, /contents: read/);
  assert.doesNotMatch(build, /contents: write|GH_TOKEN|secrets\./);
  for (const command of ['npm run verify', 'npm run test:browser', 'npm run test:native', 'npm run test:packaged', 'npm run test:update-download', 'release-version.mjs manifest dist']) assert.ok(build.includes(command));
  assert.match(publish, /needs: build/);
  assert.match(publish, /contents: write/);
  assert.doesNotMatch(publish, /npm ci|desktop:dist|desktop:pack/);
  assert.match(publish, /release-version\.mjs publish release-artifact/);
  assert.match(build, /artifact_id: \$\{\{ steps\.release_artifact\.outputs\.artifact-id \}\}/);
  assert.match(build, /id: release_artifact/);
  assert.match(publish, /artifact-ids: \$\{\{ needs\.build\.outputs\.artifact_id \}\}/);
  assert.match(publish, /merge-multiple: true/, 'publisher expects artifact contents directly in release-artifact');
  assert.doesNotMatch(publish, /github\.run_attempt/, 'a publish-only rerun must reuse the successful build artifact, not its new attempt number');
  assert.equal((workflow.match(/refs\/heads\/production/g) ?? []).length, 2);
  for (const action of workflow.matchAll(/uses:\s*(\S+)/g)) assert.match(action[1], /@[0-9a-f]{40}$/);
});

test('the MANUX app (hand-mouse) release builds read-only and publishes only that artifact from production', async () => {
  const workflow = (await fs.readFile(new URL('../.github/workflows/hand-mouse-release.yml', import.meta.url), 'utf8')).replaceAll('\r\n', '\n');
  assert.match(workflow, /branches: \[production\]/);
  assert.doesNotMatch(workflow, /tags:|workflow_dispatch:|pull_request_target/);
  assert.match(workflow, /^permissions: \{\}$/m);
  const [build, publish] = workflow.split('\n  publish:');
  assert.match(build, /contents: read/);
  assert.doesNotMatch(build, /contents: write|GH_TOKEN|secrets\.|vpk upload/);
  assert.ok(build.indexOf('pytest') < build.indexOf('packaging/build.ps1'));
  assert.match(publish, /needs: build/);
  assert.match(publish, /if: github\.ref == 'refs\/heads\/production' && github\.repository == 'intel7-ai\/manux'/);
  assert.match(publish, /contents: write/);
  assert.match(publish, /artifact-ids: \$\{\{ needs\.build\.outputs\.artifact_id \}\}/);
  assert.doesNotMatch(publish, /checkout|uv sync|pyinstaller|build\.ps1/, 'publish uploads the built artifact, it never rebuilds');
  for (const action of workflow.matchAll(/uses:\s*(\S+)/g)) assert.match(action[1], /@[0-9a-f]{40}$/);
});
