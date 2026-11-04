import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {isReleasePath, assertVerification, prepareRelease} from '../scripts/prepare-release.mjs';
import {repositoryState} from '../scripts/verification-state.mjs';

test('source handoff excludes secrets, traversal, local state, binaries and large fixtures by path', () => {
  for (const name of ['.env', 'src/.env.local', 'secrets.json', 'src/secrets.json', '.git/config', '.local/report.json',
    'node_modules/tool/index.js', 'public/models/model.task', 'tests/fixtures/camera.jpg', 'public/media/demo.webm',
    '../package.json', '/package.json', 'src/../package.json', 'src\\app.js', '.claude/settings.local.json',
    '.claude/worktrees/task/src/app.js', 'test-results/result.json']) assert.equal(isReleasePath(name), false, name);
  for (const name of ['package-lock.json', 'src/app.js', 'desktop/windows-mouse.ps1', '.github/workflows/ci.yml',
    'harness/task.example.json', '.claude/commands/manux.md', '.claude/agents/manux-reviewer.md',
    '.claude/settings.json', 'MANUX 검사.cmd']) assert.equal(isReleasePath(name), true, name);
});

function evidence(head, sourceDigest = 'a'.repeat(64)) {
  return {schemaVersion:1, status:'passed', git:{head, dirty:false, sourceDigest},
    stages:['check', 'harness', 'unit'].map(name => ({name, exitCode:0}))};
}
test('release requires complete successful verification of the same clean commit', () => {
  const passed = evidence('abc');
  assert.doesNotThrow(() => assertVerification(passed, 'abc'));
  assert.throws(() => assertVerification(passed, 'abc', 'b'.repeat(64)));
  for (const value of [null, {...passed, status:'running'}, {...passed, status:'failed'},
    {...passed, git:{head:'old', dirty:false}}, {...passed, git:{head:'abc', dirty:true}},
    {...passed, stages:[{name:'unit', exitCode:0}]}]) assert.throws(() => assertVerification(value, 'abc'));
});

test('handoff packages committed source with hash evidence and rejects dirty or credential-bearing source', async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'manux-handoff-'));
  t.after(() => fs.rm(root, {recursive:true, force:true}));
  const git = (...args) => execFileSync('git', args, {cwd:root, encoding:'utf8', windowsHide:true});
  git('init', '--quiet');
  git('config', 'user.name', 'MANUX test');
  git('config', 'user.email', 'manux-test@example.invalid');
  git('config', 'commit.gpgsign', 'false');
  git('config', 'core.autocrlf', 'false');
  git('config', 'core.hooksPath', path.join(root, '.local/empty-hooks'));
  await fs.writeFile(path.join(root, '.gitignore'), '.local/\n.env\n');
  await fs.writeFile(path.join(root, 'README.md'), 'Test source handoff\n');
  await fs.writeFile(path.join(root, 'package.json'), '{"private":true}\n');
  await fs.writeFile(path.join(root, '.env'), 'TEST_SECRET=excluded\n');
  git('add', '.'); git('commit', '--quiet', '-m', 'test fixture');
  const refresh = async () => {
    await fs.mkdir(path.join(root, '.local/verification'), {recursive:true});
    const snapshot = repositoryState(root);
    await fs.writeFile(path.join(root, '.local/verification/latest.json'), JSON.stringify(evidence(snapshot.head, snapshot.sourceDigest)));
  };
  await refresh();
  const {destination, manifest} = await prepareRelease({root});
  assert.equal(manifest.kind, 'verified-source-handoff');
  assert.equal(manifest.files.some(file => file.path.endsWith('.env')), false);
  const readme = manifest.files.find(file => file.path === 'source/README.md');
  assert.equal(readme.sha256, createHash('sha256').update(await fs.readFile(path.join(destination, readme.path))).digest('hex'));
  await fs.writeFile(path.join(root, 'README.md'), 'uncommitted change');
  const firstEdit = repositoryState(root);
  await fs.writeFile(path.join(root, 'README.md'), 'second edit to the same dirty file');
  const secondEdit = repositoryState(root);
  assert.equal(firstEdit.status, secondEdit.status);
  assert.notEqual(firstEdit.sourceDigest, secondEdit.sourceDigest);
  await fs.mkdir(path.join(root, 'src'));
  await fs.writeFile(path.join(root, 'src/new.js'), 'export const value = 1;');
  const newFile = repositoryState(root);
  await fs.writeFile(path.join(root, 'src/new.js'), 'export const value = 2;');
  const editedNewFile = repositoryState(root);
  assert.equal(newFile.status, editedNewFile.status);
  assert.notEqual(newFile.sourceDigest, editedNewFile.sourceDigest);
  await fs.writeFile(path.join(root, '.local/ignored.json'), '{"local":1}');
  assert.equal(repositoryState(root).sourceDigest, editedNewFile.sourceDigest);
  await assert.rejects(prepareRelease({root}), /미커밋 변경/);
  await fs.writeFile(path.join(root, 'README.md'), `do not ship: ${'ghp_'}${'a'.repeat(36)}`);
  git('add', 'README.md', 'src/new.js'); git('commit', '--quiet', '-m', 'credential fixture');
  await refresh();
  await assert.rejects(prepareRelease({root}), /자격 증명처럼/);
});
