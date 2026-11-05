import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {repositoryState} from '../scripts/verification-state.mjs';
import {validateResult} from '../scripts/task-contract.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
const read = name => fs.readFile(path.join(root, name), 'utf8');

test('the active root exposes MANUX and shared tooling without retired web/Electron entry points', async () => {
  for (const name of ['src', 'public', 'desktop', 'server.js', 'electron-builder.yml',
    'MANUX 실행.cmd', 'MANUX 마우스 실행.cmd', 'start-manux.ps1', '.github/workflows/source-handoff.yml']) {
    await assert.rejects(fs.access(path.join(root, name)), {code:'ENOENT'}, name);
  }
  for (const name of ['apps/manux/src/manux/desktop.py', 'hand-mouse/packaging/build.ps1',
    '.github/workflows/hand-mouse-release.yml', 'scripts/discord-output-guard.mjs']) {
    await fs.access(path.join(root, name));
  }
  assert.match(await read('archive/README.md'), /589cc9a8d338e40df8edc005825caf0c9a6329fc/);
});

test('root npm tooling has no application dependencies and retains the report hook', async () => {
  const manifest = JSON.parse(await read('package.json'));
  assert.equal(Object.keys(manifest.dependencies || {}).length, 0);
  assert.equal(Object.keys(manifest.devDependencies || {}).length, 0);
  assert.equal(manifest.main, undefined);
  assert.ok(manifest.scripts['task:report']);
  assert.ok(manifest.scripts['harness:check']);
  const settings = JSON.parse(await read('.claude/settings.json'));
  assert.match(JSON.stringify(settings), /scripts\/discord-output-guard\.mjs/);
});

test('current CI gates retain Python, tooling, and preview-only production admission', async () => {
  const workflow = await read('.github/workflows/ci.yml');
  assert.match(workflow, /needs: \[verify, apps-manux\]/);
  assert.match(workflow, /test "\$MANUX_RESULT" = success/);
  assert.match(workflow, /"\$HEAD_REF" != preview/);
  assert.doesNotMatch(workflow, /npm run (?:desktop|test:browser|test:native)|windows-package:/);
  for (const [, reference] of workflow.matchAll(/uses: (\S+)/g)) {
    assert.match(reference, /@[a-f0-9]{40}$/);
  }
});

test('default agent context stays bounded and legacy searches require an explicit target', async () => {
  const instructions = await read('AGENTS.md') + await read('apps/manux/AGENTS.md');
  assert.ok(instructions.length < 6000, `Default instructions grew to ${instructions.length} characters`);
  assert.match(await read('.gitignore'), /^\.worktrees\/$/m);
  assert.match(await read('.rgignore'), /^hand-mouse\/$/m);
});

test('retirement-sized result records enumerate all paths but remain bounded', async () => {
  const task = JSON.parse(await read('harness/task.example.json'));
  const result = JSON.parse(await read('harness/result.example.json'));
  task.scope = ['apps/manux/'];
  result.changedFiles = Array.from({length:256}, (_, i) => `apps/manux/file-${i}.py`);
  assert.doesNotThrow(() => validateResult(result, task));
  result.changedFiles.push('apps/manux/overflow.py');
  assert.throws(() => validateResult(result, task));
});

test('verification notices Python content edits even when Git porcelain status is unchanged', async t => {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'manux-digest-'));
  t.after(async () => {
    assert.equal(path.dirname(path.resolve(temporary)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(temporary).startsWith('manux-digest-'));
    await fs.rm(temporary, {recursive:true, force:true});
  });
  const git = (...args) => execFileSync('git', args, {cwd:temporary, windowsHide:true});
  git('init', '--quiet');
  git('config', 'user.name', 'MANUX test');
  git('config', 'user.email', 'test@example.invalid');
  git('config', 'commit.gpgsign', 'false');
  git('config', 'core.hooksPath', path.join(temporary, '.empty-hooks'));
  const source = path.join(temporary, 'app.py');
  await fs.writeFile(source, 'value = 0\n');
  git('add', 'app.py');
  git('commit', '--quiet', '-m', 'synthetic fixture');
  await fs.writeFile(source, 'value = 1\n');
  const first = repositoryState(temporary);
  await fs.writeFile(source, 'value = 2\n');
  const second = repositoryState(temporary);
  assert.equal(first.status, second.status);
  assert.notEqual(first.sourceDigest, second.sourceDigest);
});
