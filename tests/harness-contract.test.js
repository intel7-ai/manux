import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { ContractValidationError, isSafeProjectPath, renderTaskReport, renderTaskReportHtml, validateResult, validateTask } from '../scripts/task-contract.mjs';
import { readTaskRecord, runTaskReport } from '../scripts/task-report.mjs';

const clone = value => JSON.parse(JSON.stringify(value));
const task = {
  schemaVersion: 1, requestId: 'MANUX-42', objective: '손 추적이 사라지면 입력을 멈춘다.',
  acceptanceCriteria: ['손 추적이 사라지면 눌린 버튼을 해제한다.'],
  scope: ['src/air-mouse-controller.js', 'tests/'], baselineCommit: 'a'.repeat(40),
};
const result = {
  schemaVersion: 1, requestId: task.requestId, baselineCommit: task.baselineCommit,
  status: 'complete', summary: '손 추적 중단 시 해제 동작을 검증했다.', changedFiles: ['src/air-mouse-controller.js'],
  checks: [{ name: '손 추적 중단', command: 'node --test tests/air-mouse-controller.test.js', status: 'passed', evidence: '누름 후 추적 중단 입력에서 release 사건을 확인했다.' }],
  acceptance: [{ criterion: task.acceptanceCriteria[0], status: 'met', evidence: '해당 회귀 시나리오가 통과했다.' }],
  delivery: { integrated: false, published: false, evidence: '' },
  limitations: ['실물 웹캠 검사는 이 코드 시험에 포함되지 않았다.'], nextSteps: ['담당 리뷰어가 변경 내용과 시험 결과를 확인한다.'],
};

async function fixture(t) {
  const base = await mkdtemp(join(tmpdir(), 'manux-contract-'));
  t.after(async () => {
    const target = resolve(base);
    assert.equal(dirname(target), resolve(tmpdir()));
    assert.ok(basename(target).startsWith('manux-contract-'));
    await rm(target, { recursive: true, force: true });
  });
  const root = join(base, 'repo');
  await mkdir(root);
  await writeFile(join(root, 'task.json'), JSON.stringify(task));
  await writeFile(join(root, 'result.json'), JSON.stringify(result));
  return { base, root };
}

test('versioned examples validate while explicitly recording that no test ran', async () => {
  const exampleTask = JSON.parse(await readFile(new URL('../harness/task.example.json', import.meta.url), 'utf8'));
  const exampleResult = JSON.parse(await readFile(new URL('../harness/result.example.json', import.meta.url), 'utf8'));
  assert.equal(validateTask(exampleTask), exampleTask);
  assert.equal(validateResult(exampleResult, exampleTask), exampleResult);
  const report = renderTaskReport(exampleResult, exampleTask);
  assert.match(report, /^상태: 검토 필요/);
  assert.match(report, /미실행 1/);
});

const headingLines = report => report.split('\n').filter(line => /^(상태|요약|변경|검증|다음):/u.test(line));
const listLines = report => report.split('\n').filter(line => !/^(상태|요약|변경|검증|다음):/u.test(line));

// Format change (this request): the former single `;`-joined line per section became a heading
// line followed by "- " list lines. The five headings and their order are unchanged.
test('renderer produces exactly the same five-heading result across property ordering and calls', () => {
  const first = renderTaskReport(result, task);
  const reordered = Object.fromEntries(Object.entries(result).reverse());
  assert.equal(renderTaskReport(reordered, task), first);
  assert.equal(renderTaskReport(clone(result), clone(task)), first);
  assert.deepEqual(headingLines(first).map(line => line.split(':')[0]), ['상태', '요약', '변경', '검증', '다음']);
  assert.equal(first.split('\n')[0], '상태: 완료 | 작업: MANUX-42 | 통합: 아니오 | 게시: 아니오');
});

test('details are split into "- " list lines under their heading instead of one long line', () => {
  const report = renderTaskReport(result, task);
  assert.deepEqual(report.split('\n'), [
    '상태: 완료 | 작업: MANUX-42 | 통합: 아니오 | 게시: 아니오',
    '요약: 손 추적 중단 시 해제 동작을 검증했다.',
    '변경: 파일 1개',
    '- src/air-mouse-controller.js',
    '검증: 통과 1 / 실패 0 / 미실행 0 · 완료 기준 1/1',
    '- 통과 · 손 추적 중단 (node --test tests/air-mouse-controller.test.js): 누름 후 추적 중단 입력에서 release 사건을 확인했다.',
    '다음: 1단계 · 한계 1건',
    '- 담당 리뷰어가 변경 내용과 시험 결과를 확인한다.',
    '- 한계: 실물 웹캠 검사는 이 코드 시험에 포함되지 않았다.',
  ]);
  assert.ok(!report.includes('; '));
  const empty = clone(result); empty.changedFiles = []; empty.limitations = [];
  const emptyReport = renderTaskReport(empty, task);
  assert.match(emptyReport, /^변경: 파일 변경 없음$/m);
  assert.match(emptyReport, /^다음: 1단계$/m);
});

test('unmet or unchecked criteria are listed by name while the status values stay distinct', () => {
  const unchecked = clone(result); unchecked.status = 'needs_review'; unchecked.acceptance[0].status = 'not_checked'; unchecked.acceptance[0].evidence = '실물 웹캠에서는 아직 확인하지 않았다.';
  const review = renderTaskReport(unchecked, task);
  assert.match(review, /^상태: 검토 필요 \|/);
  assert.match(review, /^- 미확인 기준 · 손 추적이 사라지면 눌린 버튼을 해제한다\.$/m);
  assert.match(review, /완료 기준 0\/1/);
  const unmet = clone(unchecked); unmet.status = 'blocked'; unmet.acceptance[0].status = 'not_met';
  const unmetReport = renderTaskReport(unmet, task);
  assert.match(unmetReport, /^상태: 차단 \|/);
  assert.match(unmetReport, /^- 미충족 기준 · /m);
  assert.match(renderTaskReport(result, task), /^상태: 완료 \|/);
});

test('result must identify the exact task, baseline, acceptance criteria and owned paths', () => {
  for (const mutation of [
    value => { value.requestId = 'OTHER'; },
    value => { value.baselineCommit = 'b'.repeat(40); },
    value => { value.acceptance[0].criterion = '다른 기준'; },
    value => { value.changedFiles = ['src/app.js']; },
    value => { value.changedFiles = ['tests-other/file.js']; },
    value => { value.acceptance.push(clone(value.acceptance[0])); },
  ]) {
    const invalid = clone(result); mutation(invalid);
    assert.throws(() => validateResult(invalid, task), ContractValidationError);
  }
  const nested = clone(result); nested.changedFiles = ['tests/nested/camera.test.js'];
  assert.doesNotThrow(() => validateResult(nested, task));
});

test('failure, missing verification and unmet acceptance cannot become complete', () => {
  for (const mutation of [
    value => { value.checks[0].status = 'failed'; },
    value => { value.checks[0].status = 'not_run'; },
    value => { value.checks = []; },
    value => { value.checks[0].evidence = ''; },
    value => { value.checks[0].evidence = '  '; },
    value => { value.checks[0].evidence = 'not run'; },
    value => { value.checks[0].evidence = 'Tests were not run because the runtime is missing.'; },
    value => { value.acceptance[0].evidence = '아직 확인하지 않음'; },
    value => { value.acceptance[0].status = 'not_checked'; },
    value => { value.acceptance[0].status = 'not_met'; },
  ]) {
    const invalid = clone(result); mutation(invalid);
    assert.throws(() => validateResult(invalid, task), ContractValidationError);
  }
  const failed = clone(result); failed.status = 'needs_review'; failed.checks[0].status = 'failed';
  assert.throws(() => validateResult(failed, task), /차단/);
  failed.status = 'blocked';
  assert.doesNotThrow(() => validateResult(failed, task));
});

test('task completion, integration and publication remain separate recorded claims', () => {
  const published = clone(result); published.delivery.published = true;
  assert.throws(() => validateResult(published, task), ContractValidationError);
  published.delivery.evidence = 'https://github.com/example/manux/pull/42 초안 PR에 변경을 게시했다.';
  assert.doesNotThrow(() => validateResult(published, task));
  assert.match(renderTaskReport(published, task), /통합: 아니오 \| 게시: 예/);
  published.delivery.integrated = true;
  assert.match(renderTaskReport(published, task), /통합: 예 \| 게시: 예/);

  for (const status of ['needs_review', 'blocked']) {
    const draft = clone(published);
    draft.status = status;
    draft.delivery.integrated = false;
    draft.checks[0].status = status === 'blocked' ? 'failed' : 'not_run';
    draft.checks[0].evidence = status === 'blocked' ? '초안 PR의 CI 회귀 검사가 실패했다.' : '초안 PR을 게시했으나 CI 검사를 아직 실행하지 않음';
    draft.acceptance[0].status = status === 'blocked' ? 'not_met' : 'not_checked';
    draft.acceptance[0].evidence = '초안 PR은 게시되었으며 완료 기준은 아직 충족하지 못했다.';
    draft.limitations = ['초안 PR의 검증이 완료되지 않았다.'];
    assert.doesNotThrow(() => validateResult(draft, task));
    assert.match(renderTaskReport(draft, task), /통합: 아니오 \| 게시: 예/);
    draft.status = 'complete';
    assert.throws(() => validateResult(draft, task), ContractValidationError);
  }

  const integrated = clone(result);
  integrated.delivery.integrated = true;
  assert.throws(() => validateResult(integrated, task), ContractValidationError);
  integrated.delivery.evidence = '공유 checkout 기준 커밋에 검토한 변경을 통합했다.';
  assert.doesNotThrow(() => validateResult(integrated, task));
});

test('paths reject traversal, absolute paths, secrets and directory prefix confusion', () => {
  for (const path of ['../private.txt', 'src/../private.txt', '/etc/passwd', 'C:/Users/private.txt', 'src\\file.js', '.env', 'config/.env.local', 'secrets.json', '.git/config', '.claude/settings.local.json', '.CLAUDE/SETTINGS.LOCAL.JSON', 'tests//bad.js', 'tests/', 'tests/./bad.js']) {
    assert.equal(isSafeProjectPath(path), false, path);
    const invalid = clone(result); invalid.changedFiles = [path];
    assert.throws(() => validateResult(invalid, task), ContractValidationError);
  }
  assert.equal(isSafeProjectPath('tests/', { directory: true }), true);
});

test('credentials and private local paths are rejected without reflecting values in errors', () => {
  const privateValues = [
    'DISCORD_TOKEN=synthetic-private-value', 'sk-ant-' + 'x'.repeat(30), 'https://user:secret@example.com/path',
    'https://example.com?token=private', 'C:\\Users\\Private\\file.txt', '/home/private/file.txt',
    '`C:/Users/Private/file.txt`', '/arbitrary/private/file.txt', 'https://discord.com/api/webhooks/123/private',
  ];
  for (const value of privateValues) {
    const invalid = clone(result); invalid.summary = value;
    assert.throws(() => validateResult(invalid, task), error => error instanceof ContractValidationError && !error.message.includes(value));
  }
  const unknown = clone(result); unknown.privateToken = 'private-contents';
  assert.throws(() => validateResult(unknown, task), error => !error.message.includes('private-contents'));
});

test('long reports stay within Discord limits and preserve failure counts and safe Unicode', () => {
  const longTask = clone(task); longTask.scope = ['src/'];
  const long = clone(result); long.status = 'blocked'; long.summary = '😀'.repeat(800);
  long.changedFiles = Array.from({ length: 60 }, (_, i) => `src/${'a'.repeat(160)}${i}.js`);
  long.checks = Array.from({ length: 20 }, (_, i) => ({ name: `검사 ${i}`, command: 'npm test', status: i === 19 ? 'failed' : 'passed', evidence: '결과'.repeat(300) }));
  long.limitations = ['@everyone ' + '한계'.repeat(100)];
  long.nextSteps = ['@here ' + '확인'.repeat(100)];
  const report = renderTaskReport(long, longTask);
  assert.ok(report.length <= 1900, report.length);
  assert.ok(report.isWellFormed());
  assert.equal(headingLines(report).length, 5);
  assert.ok(listLines(report).every(line => line.startsWith('- ')));
  assert.match(report, /실패 1/);
  assert.match(report, /실패 · 검사 19/);
  assert.doesNotMatch(report, /@everyone|@here/);
  assert.match(report, /^- 외 \d+건 생략$/m);
});

test('the largest valid records still fit in one Discord message, dropping whole list items first', () => {
  const longTask = clone(task); longTask.scope = ['src/']; longTask.acceptanceCriteria = Array.from({ length: 12 }, (_, i) => `완료 기준 ${i} ${'기'.repeat(200)}`);
  const big = clone(result); big.status = 'blocked'; big.requestId = longTask.requestId = 'R'.repeat(64);
  big.changedFiles = Array.from({ length: 60 }, (_, i) => `src/${'a'.repeat(200)}${i}.js`);
  big.checks = Array.from({ length: 12 }, (_, i) => ({ name: `검사${i}${'이름'.repeat(50)}`, command: `npm run ${'c'.repeat(250)}`, status: ['failed', 'not_run', 'passed'][i % 3], evidence: i % 3 === 1 ? '이 검사는 아직 실행하지 않음' : '결과'.repeat(300) }));
  big.acceptance = longTask.acceptanceCriteria.map(criterion => ({ criterion, status: 'not_checked', evidence: '확인하지 않았다.' }));
  big.limitations = Array.from({ length: 10 }, (_, i) => `한계 ${i} ${'제한'.repeat(140)}`);
  big.nextSteps = Array.from({ length: 10 }, (_, i) => `다음 ${i} ${'단계'.repeat(140)}`);
  const report = renderTaskReport(big, longTask);
  assert.ok(report.length <= 1900, report.length);
  assert.equal(headingLines(report).length, 5);
  assert.match(report, /^다음: 10단계 · 한계 10건$/m);
  assert.match(report, /실패 4 \/ 미실행 4/);
  // Failures are listed before passing checks even when only a few items fit.
  assert.ok(report.indexOf('- 실패 ·') < report.indexOf('- 통과 ·') || !report.includes('- 통과 ·'));
});

test('each recorded status keeps its own label and the integration/publication flags stay separate', () => {
  for (const [status, label] of [['complete', '완료'], ['needs_review', '검토 필요'], ['blocked', '차단']]) {
    const value = clone(result); value.status = status;
    if (status !== 'complete') { value.acceptance[0].status = 'not_checked'; value.acceptance[0].evidence = '실물에서 확인하지 않았다.'; }
    if (status === 'blocked') { value.checks[0].status = 'failed'; value.checks[0].evidence = '회귀 시험이 실패했다.'; }
    assert.match(renderTaskReport(value, task), new RegExp(`^상태: ${label} \\| 작업: MANUX-42 \\| 통합: 아니오 \\| 게시: 아니오$`, 'm'));
  }
});

test('record paths are confined to the workspace', async t => {
  const { base, root } = await fixture(t);
  const outside = join(base, 'outside');
  await mkdir(outside);
  await writeFile(join(outside, 'record.json'), JSON.stringify(task));
  await assert.rejects(readTaskRecord('../outside/record.json', { root }), ContractValidationError);
  assert.deepEqual(await readTaskRecord('task.json', { root }), task);
});

test('symlink/junction escapes are confined to the workspace', async t => {
  const { base, root } = await fixture(t);
  const outside = join(base, 'outside');
  await mkdir(outside);
  await writeFile(join(outside, 'record.json'), JSON.stringify(task));
  try {
    await symlink(outside, join(root, 'escape'), process.platform === 'win32' ? 'junction' : 'dir');
  } catch (error) {
    if (!['EPERM', 'EACCES', 'ENOSYS'].includes(error.code)) throw error;
    t.skip('This environment does not permit creating symbolic links/junctions.');
    return;
  }
  await assert.rejects(readTaskRecord('escape/record.json', { root }), ContractValidationError);
});

test('CLI validates and renders data without executing the recorded command', async t => {
  const { root } = await fixture(t);
  const value = clone(result);
  value.checks[0].command = 'node -e "require(\'fs\').writeFileSync(\'should-not-exist\',\'x\')"';
  await writeFile(join(root, 'result.json'), JSON.stringify(value));
  const cli = fileURLToPath(new URL('../scripts/task-report.mjs', import.meta.url));
  const output = execFileSync(process.execPath, [cli, 'task.json', 'result.json'], { cwd: root, encoding: 'utf8', windowsHide: true });
  assert.equal(output.trimEnd(), renderTaskReport(value, task));
  await assert.rejects(access(join(root, 'should-not-exist')));
  const messages = [];
  assert.equal(await runTaskReport(['task.json', 'result.json', '--check'], { root, output: text => messages.push(text) }), 0);
  assert.deepEqual(messages, ['작업 기록 검증 통과: MANUX-42']);
});

test('invalid JSON, too-large inputs and unsupported CLI flags fail without leaking source content', async t => {
  const { root } = await fixture(t);
  const errors = [];
  await writeFile(join(root, 'result.json'), 'private-token-invalid-json');
  assert.equal(await runTaskReport(['task.json', 'result.json'], { root, error: text => errors.push(text) }), 1);
  await writeFile(join(root, 'result.json'), ' '.repeat(65_537));
  assert.equal(await runTaskReport(['task.json', 'result.json'], { root, error: text => errors.push(text) }), 1);
  assert.equal(await runTaskReport(['task.json', 'result.json', '--execute'], { root, error: text => errors.push(text) }), 1);
  assert.ok(errors.every(text => !text.includes('private-token-invalid-json')));
});

test('HTML report is one self-contained file, escapes every recorded value and keeps statuses and flags', () => {
  const hostile = clone(result);
  hostile.summary = '<script>alert(1)</script> & "인용" \'따옴표\'';
  hostile.checks[0].name = '<b>이름</b>';
  hostile.checks[0].command = 'node --test "tests/a&b.js"';
  hostile.checks[0].evidence = '<iframe src="https://example.com"></iframe>';
  hostile.acceptance[0].evidence = '</td><td onclick="x">';
  hostile.limitations = ['한계 <u>밑줄</u>'];
  const hostileTask = clone(task); hostileTask.objective = '목표 <svg onload=alert(1)>';
  const html = renderTaskReportHtml(hostile, hostileTask);
  for (const raw of ['<script>', '<img src=x', '<b>이름', '<iframe', '</td><td onclick', '<u>', '<svg onload']) assert.ok(!html.includes(raw), raw);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt; &amp; &quot;인용&quot; &#39;따옴표&#39;/);
  // No script, external stylesheet/script/image reference, form or link; a CSP forbids network use.
  assert.doesNotMatch(html, /<script|<link|<img|<form|<a\s|href=|@import|url\(/i);
  assert.match(html, /Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"/);
  assert.match(html, /prefers-color-scheme:dark/);
  assert.match(html, /<span class="badge ok">완료<\/span>/);
  assert.match(html, /공유 checkout 통합: 아니오/);
  assert.match(html, /GitHub 게시: 아니오/);
  assert.match(html, /완료 기준 \(1\/1 충족\)/);
  const review = clone(result); review.status = 'needs_review'; review.acceptance[0].status = 'not_checked'; review.acceptance[0].evidence = '실물에서 확인하지 않았다.';
  review.delivery.published = true; review.delivery.evidence = '초안 PR을 게시했다.';
  const reviewHtml = renderTaskReportHtml(review, task);
  assert.match(reviewHtml, /<span class="badge warn">검토 필요<\/span>/);
  assert.match(reviewHtml, /<span class="badge warn">미확인<\/span>/);
  assert.match(reviewHtml, /GitHub 게시: 예/);
  assert.match(reviewHtml, /공유 checkout 통합: 아니오/);
});

test('HTML report keeps full evidence that the Discord text shortens and does not embed local paths', () => {
  const longEvidence = clone(result); longEvidence.checks[0].evidence = '자세한 근거'.repeat(90);
  const html = renderTaskReportHtml(longEvidence, task);
  assert.ok(html.includes('자세한 근거'.repeat(90)));
  assert.doesNotMatch(html, /[A-Za-z]:[\\/]|\/Users\/|\/home\//);
  assert.ok(html.isWellFormed());
});

test('CLI --html writes inside the workspace only, prints the same Discord text and rejects other targets', async t => {
  const { root } = await fixture(t);
  const messages = []; const notices = []; const errors = [];
  assert.equal(await runTaskReport(['task.json', 'result.json', '--html', 'out/report.html'], { root, output: text => messages.push(text), notice: text => notices.push(text), error: text => errors.push(text) }), 0);
  assert.deepEqual(messages, [renderTaskReport(result, task)]);
  assert.deepEqual(notices, ['HTML 보고서를 저장했습니다: out/report.html']);
  assert.equal(await readFile(join(root, 'out', 'report.html'), 'utf8'), renderTaskReportHtml(result, task));
  for (const target of ['../escape.html', 'out/report.txt', 'out', join(tmpdir(), 'manux-outside.html')]) {
    assert.equal(await runTaskReport(['task.json', 'result.json', '--html', target], { root, output: () => {}, notice: () => {}, error: text => errors.push(text) }), 1, target);
  }
  assert.equal(await runTaskReport(['task.json', 'result.json', '--html'], { root, output: () => {}, error: text => errors.push(text) }), 1);
  assert.equal(await runTaskReport(['task.json', 'result.json', '--check', 'x'], { root, output: () => {}, error: text => errors.push(text) }), 1);
  await assert.rejects(access(join(root, '..', 'escape.html')));
  assert.ok(errors.every(text => !text.includes(root)));
});

test('CLI --html does not write through a link that leaves the workspace', async t => {
  const { base, root } = await fixture(t);
  const outside = join(base, 'outside');
  await mkdir(outside);
  try {
    await symlink(outside, join(root, 'escape'), process.platform === 'win32' ? 'junction' : 'dir');
  } catch (error) {
    if (!['EPERM', 'EACCES', 'ENOSYS'].includes(error.code)) throw error;
    t.skip('This environment does not permit creating symbolic links/junctions.');
    return;
  }
  assert.equal(await runTaskReport(['task.json', 'result.json', '--html', 'escape/report.html'], { root, output: () => {}, notice: () => {}, error: () => {} }), 1);
  await assert.rejects(access(join(outside, 'report.html')));
});
