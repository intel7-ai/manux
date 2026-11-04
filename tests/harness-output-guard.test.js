import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, open, readFile, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { Readable } from 'node:stream';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { evaluateHook, readHookInput, validateDiscordOutput } from '../scripts/discord-output-guard.mjs';
import { renderTaskReport } from '../scripts/task-contract.mjs';

const taskExample = JSON.parse(await readFile(new URL('../harness/task.example.json', import.meta.url), 'utf8'));
const resultExample = JSON.parse(await readFile(new URL('../harness/result.example.json', import.meta.url), 'utf8'));
const interim = '상태: 접수 | 작업: TEST-1\n요약: 취소 동작을 확인합니다.\n변경: 아직 없음\n검증: 재현 예정\n다음: 원인을 조사합니다.';
const input = (text, name = 'reply') => ({ tool_name: `mcp__plugin_discord_discord__${name}`, tool_input: { text } });

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'manux-report-'));
  t.after(async () => {
    assert.equal(dirname(resolve(root)), resolve(tmpdir()));
    assert.ok(basename(root).startsWith('manux-report-'));
    await rm(root, { recursive: true, force: true });
  });
  const directory = join(root, '.local', 'harness', taskExample.requestId);
  await mkdir(directory, { recursive: true });
  await writeFile(join(directory, 'task.json'), JSON.stringify(taskExample));
  await writeFile(join(directory, 'result.json'), JSON.stringify(resultExample));
  return { root, directory };
}

test('status replies and edits use the same five sections without granting tool permissions', async () => {
  for (const tool of ['reply', 'edit_message']) assert.deepEqual(await evaluateHook(input(interim, tool)), {});
  assert.deepEqual(await evaluateHook(input(interim.replace('변경:', '추가 설명도 같은 항목에 둘 수 있습니다.\n변경:'))), {});
  assert.deepEqual(await evaluateHook({ tool_name: 'Read', tool_input: {} }), {});
});

test('public-text screening covers interim replies and edits without leaking the rejected value', async () => {
  for (const value of ['sk-ant-' + 'x'.repeat(30), 'Authorization: Bearer synthetic', '`C:/Users/Private/settings.json`', '/home/private/settings.json', 'https://discord.com/api/webhooks/123/private', '\ud800']) {
    for (const tool of ['reply', 'edit_message']) {
      const decision = await evaluateHook(input(interim.replace('아직 없음', value), tool));
      assert.equal(decision.hookSpecificOutput.permissionDecision, 'deny');
      assert.ok(!decision.hookSpecificOutput.permissionDecisionReason.includes(value));
    }
  }
});

test('hook input preserves Korean and emoji across individual UTF-8 byte chunks', async () => {
  const original = input(interim.replace('취소 동작', '취소 동작 😀'));
  const payload = Buffer.from(JSON.stringify(original));
  const parsed = await readHookInput(Readable.from(Array.from(payload, byte => Buffer.from([byte]))));
  assert.deepEqual(parsed, original);
  assert.deepEqual(await evaluateHook(parsed), {});
  await assert.rejects(readHookInput(Readable.from([Buffer.alloc(131073, 32)])), /입력 크기/);
  await assert.rejects(readHookInput(Readable.from([Buffer.from([0xc3])])));
});

test('reply attachments allow project screenshots and docs within file-count and size limits', async t => {
  const { root } = await fixture(t);
  await mkdir(join(root, 'test-results'));
  await mkdir(join(root, 'docs'));
  const screenshot = join(root, 'test-results', 'mouse.png');
  const document = join(root, 'docs', 'onboarding.md');
  await writeFile(screenshot, 'synthetic screenshot fixture');
  await writeFile(document, 'synthetic documentation fixture');
  const message = input(interim);
  message.tool_input.files = [screenshot, document];
  assert.deepEqual(await evaluateHook(message, { root }), {});
  message.tool_input.files = Array(10).fill(screenshot);
  assert.deepEqual(await evaluateHook(message, { root }), {});
  message.tool_input.files.push(document);
  assert.equal((await evaluateHook(message, { root })).hookSpecificOutput.permissionDecision, 'deny');

  const sized = await open(screenshot, 'w');
  try {
    await sized.truncate(25 * 1024 * 1024);
    message.tool_input.files = [screenshot];
    assert.deepEqual(await evaluateHook(message, { root }), {});
    await sized.truncate(25 * 1024 * 1024 + 1);
    assert.equal((await evaluateHook(message, { root })).hookSpecificOutput.permissionDecision, 'deny');
  } finally { await sized.close(); }
});

test('reply attachments reject external, hidden, sensitive and invalid paths without disclosure', async t => {
  const { root } = await fixture(t);
  const outside = join(dirname(root), `${basename(root)}-personal-settings.json`);
  await writeFile(outside, 'synthetic private fixture');
  t.after(() => rm(outside, { force: true }));
  for (const files of [
    [outside], [join(root, '.local', 'report.png')], [join(root, '.claude', 'settings.json')],
    [join(root, '.cache', 'image.png')], [join(root, '.env')], [join(root, 'docs', 'tokens.json')],
    [root], ['docs/onboarding.md'], [42], 'file.png', null,
  ]) {
    const message = input(interim); message.tool_input.files = files;
    const decision = await evaluateHook(message, { root });
    assert.equal(decision.hookSpecificOutput.permissionDecision, 'deny');
    assert.ok(!decision.hookSpecificOutput.permissionDecisionReason.includes(root));
    assert.ok(!decision.hookSpecificOutput.permissionDecisionReason.includes(outside));
  }
});

test('project root aliases accept the same public file without allowing hidden targets', async t => {
  const { root } = await fixture(t);
  const alias = `${root}-alias`;
  await mkdir(join(root, 'docs'));
  await writeFile(join(root, 'docs', 'guide.md'), 'synthetic documentation fixture');
  try {
    await symlink(root, alias, process.platform === 'win32' ? 'junction' : 'dir');
  } catch (error) {
    if (!['EPERM', 'EACCES', 'ENOSYS'].includes(error.code)) throw error;
    t.skip('This environment does not permit creating symbolic links/junctions.');
    return;
  }
  t.after(async () => {
    assert.equal(dirname(resolve(alias)), resolve(tmpdir()));
    assert.equal(basename(alias), `${basename(root)}-alias`);
    await rm(alias, { recursive: true, force: true });
  });
  const message = input(interim);
  message.tool_input.files = [join(alias, 'docs', 'guide.md')];
  assert.deepEqual(await evaluateHook(message, { root: alias }), {});
  message.tool_input.files = [join(await realpath(root), 'docs', 'guide.md')];
  assert.deepEqual(await evaluateHook(message, { root: alias }), {});
  message.tool_input.files = [join(alias, '.local', 'harness', taskExample.requestId, 'task.json')];
  assert.equal((await evaluateHook(message, { root: alias })).hookSpecificOutput.permissionDecision, 'deny');
});

test('attachment directory links cannot redirect into private project storage', async t => {
  const { root } = await fixture(t);
  const hidden = join(root, '.local', 'private');
  await mkdir(hidden);
  await writeFile(join(hidden, 'report.png'), 'synthetic private fixture');
  try {
    await symlink(hidden, join(root, 'public-alias'), process.platform === 'win32' ? 'junction' : 'dir');
  } catch (error) {
    if (!['EPERM', 'EACCES', 'ENOSYS'].includes(error.code)) throw error;
    t.skip('This environment does not permit creating symbolic links/junctions.');
    return;
  }
  const message = input(interim); message.tool_input.files = [join(root, 'public-alias', 'report.png')];
  assert.equal((await evaluateHook(message, { root })).hookSpecificOutput.permissionDecision, 'deny');
});

test('malformed, oversized, duplicate and out of order sections fail closed', async () => {
  for (const text of ['done', interim.replace('변경:', '다음:'), interim.replace('요약:', '메모:'), `${interim}\n요약: 중복`, `${interim}${'x'.repeat(1900)}`, interim.replace('상태: 접수', '상태: 완료'), interim.replace('아직 없음', 'DISCORD_TOKEN=not-a-real-token')]) {
    const decision = await evaluateHook(input(text));
    assert.equal(decision.hookSpecificOutput.permissionDecision, 'deny');
  }
});

test('final report must match validated records in the same request folder', async t => {
  const { root } = await fixture(t);
  const report = renderTaskReport(resultExample, taskExample);
  await validateDiscordOutput(input(report), { root });
  await assert.rejects(validateDiscordOutput(input(report.replace('실제 작업 결과가 아니다.', '완료했다.')), { root }));
  await assert.rejects(validateDiscordOutput(input(report.replace('MANUX-EXAMPLE', 'OTHER-ID')), { root }));
});

test('list-style final reports pass only when every line equals the rendered record', async t => {
  const { root } = await fixture(t);
  const report = renderTaskReport(resultExample, taskExample);
  const lines = report.split('\n');
  assert.ok(lines.some(line => line.startsWith('- ')), 'the example report uses list lines');
  assert.deepEqual(await evaluateHook(input(report), { root }), {});
  assert.deepEqual(await evaluateHook(input(report, 'edit_message'), { root }), {});
  const bulletIndex = lines.findIndex(line => line.startsWith('- '));
  const tampered = [
    lines.filter((_, index) => index !== bulletIndex).join('\n'),
    [...lines.slice(0, bulletIndex + 1), '- 통과 · 지어낸 검사: 모두 통과', ...lines.slice(bulletIndex + 1)].join('\n'),
    [...lines].reverse().join('\n'),
    report.replace('미실행 1', '통과 1'),
    report.replace('상태: 검토 필요', '상태: 완료'),
    report.replace('통합: 아니오', '통합: 예'),
    report.replace(/^- /m, '* '),
    `${report}\n- 한계: 없음`,
    report.replace('- 한계:', '다음: 숨겨진 항목\n- 한계:'),
  ];
  for (const text of tampered) assert.equal((await evaluateHook(input(text), { root })).hookSpecificOutput?.permissionDecision, 'deny', text);
});

test('a list line cannot start a sixth heading and a final report over the Discord limit is denied', async t => {
  const { root } = await fixture(t);
  const report = renderTaskReport(resultExample, taskExample);
  const forged = report.replace(/^- /m, '검증: ');
  assert.equal((await evaluateHook(input(forged), { root })).hookSpecificOutput.permissionDecision, 'deny');
  const padded = `${report}\n- ${'가'.repeat(1900 - report.length)}`;
  assert.ok(padded.length > 1900);
  assert.equal((await evaluateHook(input(padded), { root })).hookSpecificOutput.permissionDecision, 'deny');
  assert.ok(report.length <= 1900);
});

test('missing, oversized or contradictory records cannot authorize a final report', async t => {
  const { root, directory } = await fixture(t);
  const report = renderTaskReport(resultExample, taskExample);
  const resultPath = join(directory, 'result.json');
  await writeFile(resultPath, JSON.stringify({ ...resultExample, status: 'complete' }));
  await assert.rejects(validateDiscordOutput(input(report), { root }));
  await writeFile(resultPath, ' '.repeat(65537));
  await assert.rejects(validateDiscordOutput(input(report), { root }));
  await rm(resultPath);
  await assert.rejects(validateDiscordOutput(input(report), { root }));
});

test('path traversal and nonexistent final records are denied without leaking paths or input', async t => {
  const { root } = await fixture(t);
  const report = renderTaskReport(resultExample, taskExample);
  for (const id of ['../secret', 'a/b', 'a\\b', 'missing']) {
    const decision = await evaluateHook(input(report.replace('MANUX-EXAMPLE', id)), { root });
    assert.equal(decision.hookSpecificOutput.permissionDecision, 'deny');
    assert.ok(!decision.hookSpecificOutput.permissionDecisionReason.includes(root));
  }
});

test('CLI hook returns a deny decision for invalid output and exit 2 for invalid input JSON', () => {
  const script = fileURLToPath(new URL('../scripts/discord-output-guard.mjs', import.meta.url));
  const response = spawnSync(process.execPath, [script], { input: JSON.stringify(input('not formatted')), encoding: 'utf8' });
  assert.equal(response.status, 0);
  assert.equal(JSON.parse(response.stdout).hookSpecificOutput.permissionDecision, 'deny');
  const malformed = spawnSync(process.execPath, [script], { input: '{', encoding: 'utf8' });
  assert.equal(malformed.status, 2);
  assert.equal(malformed.stdout, '');
});
