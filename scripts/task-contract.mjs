import { readFileSync } from 'node:fs';

const taskSchema = JSON.parse(readFileSync(new URL('../harness/task.schema.json', import.meta.url), 'utf8'));
const resultSchema = JSON.parse(readFileSync(new URL('../harness/result.schema.json', import.meta.url), 'utf8'));
export const REQUEST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
export const RESULT_LABELS = Object.freeze({ blocked: '차단', needs_review: '검토 필요', complete: '완료' });
const checkLabels = Object.freeze({ passed: '통과', failed: '실패', not_run: '미실행' });

export class ContractValidationError extends Error {
  constructor(errors) {
    super(`작업 기록 형식 오류: ${errors.slice(0, 12).join('; ')}`);
    this.name = 'ContractValidationError';
    this.code = 'TASK_CONTRACT';
    this.errors = errors.slice(0, 12);
  }
}

const plainObject = value => value !== null && typeof value === 'object' && !Array.isArray(value)
  && [Object.prototype, null].includes(Object.getPrototypeOf(value));
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// This is deliberately a validator for the small schema subset used by these
// versioned contracts, not a general-purpose JSON Schema implementation.
function schemaErrors(value, schema, path = '$') {
  const errors = [];
  const fail = label => errors.push(`${path}: ${label}`);
  if (Object.hasOwn(schema, 'const') && !equal(value, schema.const)) fail('고정 값 불일치');
  if (schema.enum && !schema.enum.some(option => equal(value, option))) fail('허용 값 아님');
  const type = schema.type;
  if (type && !({ object: plainObject(value), array: Array.isArray(value), string: typeof value === 'string', boolean: typeof value === 'boolean' }[type])) {
    fail('자료형 불일치');
    return errors;
  }
  if (plainObject(value)) {
    for (const key of schema.required ?? []) if (!Object.hasOwn(value, key)) errors.push(`${path}.${key}: 필수 항목 누락`);
    if (schema.additionalProperties === false && Object.keys(value).some(key => !Object.hasOwn(schema.properties ?? {}, key))) fail('알 수 없는 항목');
    for (const [key, rule] of Object.entries(schema.properties ?? {})) {
      if (Object.hasOwn(value, key)) errors.push(...schemaErrors(value[key], rule, `${path}.${key}`));
    }
  }
  if (typeof value === 'string') {
    const length = Array.from(value).length;
    if (schema.minLength !== undefined && length < schema.minLength) fail('내용이 너무 짧음');
    if (schema.maxLength !== undefined && length > schema.maxLength) fail('내용이 너무 김');
    if (schema.pattern && !(new RegExp(schema.pattern, 'u')).test(value)) fail('문자 형식 불일치');
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) fail('항목 부족');
    if (schema.maxItems !== undefined && value.length > schema.maxItems) fail('항목 초과');
    if (schema.uniqueItems && new Set(value.map(item => JSON.stringify(item))).size !== value.length) fail('중복 항목');
    if (schema.items) value.forEach((item, index) => errors.push(...schemaErrors(item, schema.items, `${path}[${index}]`)));
  }
  for (const rule of schema.allOf ?? []) errors.push(...schemaErrors(value, rule, path));
  if (schema.if) {
    const branch = schemaErrors(value, schema.if, path).length ? schema.else : schema.then;
    if (branch) errors.push(...schemaErrors(value, branch, path));
  }
  return errors;
}

export function assertPublicText(value, path = '$') {
  if (typeof value === 'string') {
    if (!value.isWellFormed()) throw new ContractValidationError([`${path}: 잘못된 Unicode`]);
    const sensitive = [
      /\b[A-Z_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY)[A-Z_]*\s*[:=]\s*\S+/i,
      /\bAuthorization\s*[:=]\s*\S+/i,
      /\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:ant-)?[A-Za-z0-9_-]{20,}|mfa\.[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b/,
      /\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{20,}\b/,
      /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/,
      /https?:\/\/[^\s/@]+:[^\s/@]+@/i,
      /https?:\/\/(?:[a-z0-9-]+\.)?discord(?:app)?\.com\/api(?:\/v\d+)?\/webhooks\//i,
      /[?&](?:access_token|token|api_key|secret|password|signature)=/i,
      /(?:^|[\s("'`=:\[])[a-z]:[\\/]/i,
      /(?:^|[\s("'`=:\[])(?:\\\\|~[\\/]|\/(?:[A-Za-z0-9._-]+\/|home\b|Users\b|etc\b|var\b|tmp\b|root\b))/,
    ];
    if (sensitive.some(pattern => pattern.test(value))) throw new ContractValidationError([`${path}: 비밀 값 또는 개인 절대 경로를 제거하세요`]);
  } else if (Array.isArray(value)) {
    value.forEach((item, index) => assertPublicText(item, `${path}[${index}]`));
  } else if (plainObject(value)) {
    // Field names have already been checked against the fixed schema.
    for (const [key, item] of Object.entries(value)) assertPublicText(item, `${path}.${key}`);
  }
}

export function isSafeProjectPath(value, { directory = false } = {}) {
  if (typeof value !== 'string' || !value || value.length > 240 || /[\\<>:"|?*\x00-\x1f\x7f]/.test(value) || value.startsWith('/')) return false;
  const candidate = directory && value.endsWith('/') ? value.slice(0, -1) : value;
  const parts = candidate.split('/');
  if (parts.some(part => !part || part === '.' || part === '..' || /[. ]$/.test(part))) return false;
  if (parts.some(part => ['.git', 'node_modules', '.aws', '.ssh'].includes(part.toLowerCase()))) return false;
  if (parts.some(part => /^\.env(?:\.|$)/i.test(part))) return false;
  if (value.toLowerCase() === '.claude/settings.local.json') return false;
  const leaf = parts.at(-1);
  return !/(?:^|[._-])(?:credentials?|secrets?|tokens?|private[._-]?key)(?:[._-]|$)/i.test(leaf)
    && !/\.(?:pem|key|p12|pfx)$/i.test(leaf)
    && !/^(?:id_rsa|id_ed25519|\.npmrc|\.netrc|access\.json)$/i.test(leaf);
}

function checked(value, schema) {
  const errors = schemaErrors(value, schema);
  if (errors.length) throw new ContractValidationError(errors);
  assertPublicText(value);
  return value;
}

export function validateTask(task) {
  checked(task, taskSchema);
  if (task.scope.some(path => !isSafeProjectPath(path, { directory: true }))) throw new ContractValidationError(['$.scope: 저장소 안의 안전한 상대 경로만 허용']);
  return task;
}

/** Validate the recorded claims. This does not run checks or prove their truth. */
export function validateResult(result, task) {
  validateTask(task);
  checked(result, resultSchema);
  const errors = [];
  if (result.requestId !== task.requestId) errors.push('$.requestId: 요청 기록과 불일치');
  if (result.baselineCommit !== task.baselineCommit) errors.push('$.baselineCommit: 기준 커밋 불일치');
  if (result.changedFiles.some(path => !isSafeProjectPath(path))) errors.push('$.changedFiles: 저장소 안의 안전한 파일 경로만 허용');
  if (result.changedFiles.some(path => !task.scope.some(scope => scope.endsWith('/') ? path.startsWith(scope) : path === scope))) errors.push('$.changedFiles: 담당 범위 밖의 변경');
  const criteria = result.acceptance.map(item => item.criterion);
  if (new Set(criteria).size !== criteria.length || criteria.length !== task.acceptanceCriteria.length
    || task.acceptanceCriteria.some(criterion => !criteria.includes(criterion))) errors.push('$.acceptance: 완료 기준을 빠짐없이 한 번씩 기록하세요');
  if (new Set(result.checks.map(check => check.name)).size !== result.checks.length) errors.push('$.checks: 검사 이름 중복');
  if (result.checks.some(check => check.status === 'failed') && result.status !== 'blocked') errors.push('$.status: 실패한 검사가 있으면 차단으로 기록하세요');
  const notVerified = /미실행|미검증|(?:실행|수행|검증|확인)\s*(?:안\s*(?:함|했|됨)|하지\s*않)|\bnot (?:run|tested|executed|checked|verified)\b|^(?:pending|todo)[.! ]*$/i;
  if (result.checks.some(check => check.status === 'passed' && notVerified.test(check.evidence))) errors.push('$.checks: 통과와 미실행 근거가 충돌합니다');
  if (result.acceptance.some(item => item.status === 'met' && notVerified.test(item.evidence))) errors.push('$.acceptance: 충족과 미검증 근거가 충돌합니다');
  if (errors.length) throw new ContractValidationError(errors);
  return result;
}

export const DISCORD_LIMIT = 1900;
const criterionLabels = Object.freeze({ met: '충족', not_met: '미충족', not_checked: '미확인' });

function clip(value, limit) {
  if (value.length <= limit) return value;
  let end = value.slice(0, limit - 1);
  if (/[\uD800-\uDBFF]$/.test(end)) end = end.slice(0, -1);
  return `${end}…`;
}
const display = value => String(value).replace(/\s+/g, ' ').trim().replaceAll('@', '@​');

// Progressively smaller layouts. The first layout whose text fits Discord's limit is used,
// so a long report loses whole list items (and says how many) before items are cut short.
const LAYOUTS = Object.freeze([
  { summary: 360, files: 8, file: 90, checks: 6, check: 220, criteria: 3, criterion: 160, steps: 4, step: 140, limits: 4, limit: 140 },
  { summary: 300, files: 6, file: 80, checks: 5, check: 170, criteria: 3, criterion: 130, steps: 3, step: 110, limits: 3, limit: 110 },
  { summary: 240, files: 5, file: 70, checks: 4, check: 130, criteria: 2, criterion: 100, steps: 3, step: 90, limits: 3, limit: 90 },
  { summary: 200, files: 4, file: 60, checks: 3, check: 100, criteria: 2, criterion: 80, steps: 2, step: 70, limits: 2, limit: 70 },
  { summary: 140, files: 3, file: 50, checks: 3, check: 70, criteria: 1, criterion: 60, steps: 2, step: 50, limits: 2, limit: 50 },
  { summary: 100, files: 2, file: 40, checks: 2, check: 50, criteria: 1, criterion: 40, steps: 1, step: 40, limits: 1, limit: 40 },
]);

function bullets(items, count, size) {
  const lines = items.slice(0, count).map(item => `- ${clip(item, size)}`);
  if (items.length > count) lines.push(`- 외 ${items.length - count}건 생략`);
  return lines;
}

/**
 * Stable report used verbatim by the Discord output guard: five headings
 * (상태, 요약, 변경, 검증, 다음), each heading line followed by "- " list lines.
 * Values are collapsed to one line, so a recorded value cannot forge a heading.
 */
export function renderTaskReport(result, task) {
  validateResult(result, task);
  const counts = Object.fromEntries(['passed', 'failed', 'not_run'].map(status => [status, result.checks.filter(check => check.status === status).length]));
  const checkSummary = `통과 ${counts.passed} / 실패 ${counts.failed} / 미실행 ${counts.not_run}`;
  const orderedChecks = [...result.checks].sort((a, b) => ['failed', 'not_run', 'passed'].indexOf(a.status) - ['failed', 'not_run', 'passed'].indexOf(b.status));
  const checkItems = orderedChecks.map(check => `${checkLabels[check.status]} · ${display(check.name)} (${display(check.command)}): ${display(check.evidence)}`);
  const criteriaMet = result.acceptance.filter(item => item.status === 'met').length;
  const openCriteria = result.acceptance.filter(item => item.status !== 'met')
    .map(item => `${criterionLabels[item.status]} 기준 · ${display(item.criterion)}`);
  const files = result.changedFiles;
  const header = `상태: ${RESULT_LABELS[result.status]} | 작업: ${result.requestId} | 통합: ${result.delivery.integrated ? '예' : '아니오'} | 게시: ${result.delivery.published ? '예' : '아니오'}`;
  const limitItems = result.limitations.map(item => `한계: ${display(item)}`);
  for (const layout of LAYOUTS) {
    const text = [
      header,
      clip(`요약: ${display(result.summary)}`, layout.summary),
      files.length ? `변경: 파일 ${files.length}개` : '변경: 파일 변경 없음',
      ...bullets(files.map(display), layout.files, layout.file),
      `검증: ${checkSummary} · 완료 기준 ${criteriaMet}/${task.acceptanceCriteria.length}`,
      ...bullets(checkItems, layout.checks, layout.check),
      ...bullets(openCriteria, layout.criteria, layout.criterion),
      `다음: ${result.nextSteps.length}단계${limitItems.length ? ` · 한계 ${limitItems.length}건` : ''}`,
      ...bullets(result.nextSteps.map(display), layout.steps, layout.step),
      ...bullets(limitItems, layout.limits, layout.limit),
    ].join('\n');
    if (text.length <= DISCORD_LIMIT) return text;
  }
  throw new ContractValidationError(['보고서가 Discord 길이 제한을 넘습니다']);
}

const HTML_ESCAPES = Object.freeze({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' });
const escapeHtml = value => String(value).replace(/[&<>"']/g, character => HTML_ESCAPES[character]);
const paragraph = value => escapeHtml(value);
const listItems = items => items.length ? `<ul>${items.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '<p class="muted">없음</p>';

const REPORT_STYLE = `
:root{color-scheme:light dark;--bg:#f7f7f5;--panel:#fff;--text:#1d1d1b;--muted:#5f5f58;--line:#d9d9d2;--ok:#17653a;--ok-bg:#e3f4ea;--warn:#8a5a00;--warn-bg:#fdf1d6;--bad:#a12626;--bad-bg:#fbe4e4;--neutral-bg:#ecece6}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--panel:#21211f;--text:#ecece6;--muted:#a9a9a0;--line:#3a3a36;--ok:#7fd6a0;--ok-bg:#1d3a2a;--warn:#f0c872;--warn-bg:#3d3216;--bad:#f2a0a0;--bad-bg:#432121;--neutral-bg:#2d2d2a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.6 system-ui,-apple-system,"Segoe UI","Malgun Gothic",sans-serif}
main{max-width:960px;margin:0 auto;padding:24px 16px 56px}
h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.1rem;margin:16px 0 8px}
section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:4px 16px 12px;margin-top:16px}
.meta{color:var(--muted);font-size:.9rem;overflow-wrap:anywhere}.muted{color:var(--muted)}
.badges{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.9rem;font-weight:600;background:var(--neutral-bg)}
.badge.ok{background:var(--ok-bg);color:var(--ok)}.badge.warn{background:var(--warn-bg);color:var(--warn)}.badge.bad{background:var(--bad-bg);color:var(--bad)}
.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;min-width:560px}
th,td{border-top:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{font-size:.85rem;color:var(--muted)}
code{font-family:ui-monospace,Consolas,monospace;font-size:.9em;overflow-wrap:anywhere}
ul{margin:4px 0;padding-left:1.25rem}
`;

const statusTone = Object.freeze({ complete: 'ok', needs_review: 'warn', blocked: 'bad', passed: 'ok', failed: 'bad', not_run: 'warn', met: 'ok', not_met: 'bad', not_checked: 'warn' });

/**
 * Self-contained HTML view of the same recorded claims (no scripts, no network).
 * Every dynamic value is escaped. Unlike the Discord text, nothing is shortened.
 */
export function renderTaskReportHtml(result, task) {
  validateResult(result, task);
  const badge = (label, tone = '') => `<span class="badge ${tone}">${escapeHtml(label)}</span>`;
  const checkRows = result.checks.map(check => `<tr><td>${escapeHtml(check.name)}</td><td><code>${escapeHtml(check.command)}</code></td><td>${badge(checkLabels[check.status], statusTone[check.status])}</td><td>${paragraph(check.evidence)}</td></tr>`).join('');
  const criterionRows = result.acceptance.map(item => `<tr><td>${escapeHtml(item.criterion)}</td><td>${badge(criterionLabels[item.status], statusTone[item.status])}</td><td>${paragraph(item.evidence)}</td></tr>`).join('');
  const met = result.acceptance.filter(item => item.status === 'met').length;
  const files = result.changedFiles.length ? `<ul>${result.changedFiles.map(file => `<li><code>${escapeHtml(file)}</code></li>`).join('')}</ul>` : '<p class="muted">파일 변경 없음</p>';
  const delivery = result.delivery;
  return `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>MANUX 작업 보고 ${escapeHtml(result.requestId)}</title>
<style>${REPORT_STYLE}</style>
</head>
<body>
<main>
<h1>MANUX 작업 보고</h1>
<p class="meta">작업 <code>${escapeHtml(result.requestId)}</code> · 기준 커밋 <code>${escapeHtml(result.baselineCommit)}</code></p>
<div class="badges">${badge(RESULT_LABELS[result.status], statusTone[result.status])}${badge(`공유 checkout 통합: ${delivery.integrated ? '예' : '아니오'}`, delivery.integrated ? 'ok' : '')}${badge(`GitHub 게시: ${delivery.published ? '예' : '아니오'}`, delivery.published ? 'ok' : '')}</div>
<section><h2>목표</h2><p>${paragraph(task.objective)}</p></section>
<section><h2>요약</h2><p>${paragraph(result.summary)}</p></section>
<section><h2>변경 파일 (${result.changedFiles.length}개)</h2>${files}</section>
<section><h2>완료 기준 (${met}/${task.acceptanceCriteria.length} 충족)</h2><div class="table-wrap"><table><thead><tr><th>기준</th><th>판정</th><th>증거</th></tr></thead><tbody>${criterionRows}</tbody></table></div></section>
<section><h2>실행한 검사</h2><div class="table-wrap"><table><thead><tr><th>이름</th><th>명령</th><th>결과</th><th>증거</th></tr></thead><tbody>${checkRows}</tbody></table></div></section>
<section><h2>통합·게시 근거</h2>${delivery.evidence ? `<p>${paragraph(delivery.evidence)}</p>` : '<p class="muted">기록된 근거 없음</p>'}</section>
<section><h2>한계</h2>${listItems(result.limitations)}</section>
<section><h2>다음 단계</h2>${listItems(result.nextSteps)}</section>
<p class="meta">이 보고서는 기록된 결과의 형식, 범위, 증거 유무와 상태 모순을 검사한 것입니다. 증거 내용이 사실인지는 증명하지 않으므로 실행 로그, 독립 리뷰, CI와 함께 확인하세요. 실제 웹캠과 실제 운영체제 동작은 별도로 확인해야 합니다.</p>
</main>
</body>
</html>
`;
}
