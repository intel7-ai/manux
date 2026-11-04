import { realpath, stat } from 'node:fs/promises';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { assertPublicText, isSafeProjectPath, renderTaskReport } from './task-contract.mjs';
import { readTaskRecord } from './task-report.mjs';

const projectRoot = fileURLToPath(new URL('../', import.meta.url));
const guardedTools = new Set(['mcp__plugin_discord_discord__reply', 'mcp__plugin_discord_discord__edit_message']);
const headings = ['상태', '요약', '변경', '검증', '다음'];
const finalHeader = /^상태: (완료|검토 필요|차단) \| 작업: ([A-Za-z0-9][A-Za-z0-9_-]{0,63}) \| 통합: (예|아니오) \| 게시: (예|아니오)$/u;
const interimHeader = /^상태: (접수|진행|질문|정보)(?: \| 작업: [A-Za-z0-9][A-Za-z0-9_-]{0,63})?$/u;
const formatHelp = '응답은 상태/요약/변경/검증/다음 순서의 5개 항목을 사용하세요. 진행 상태는 접수·진행·질문·정보입니다. 작업 종료는 task.json/result.json을 저장하고 npm run task:report -- TASK.json RESULT.json의 본문을 그대로 보내세요.';

function contained(root, target) {
  const path = relative(root, target);
  return path !== '' && path !== '..' && !path.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`) && !isAbsolute(path);
}

async function readRecord(root, requestId, name) {
  const actualRoot = await realpath(root);
  const recordDir = join(actualRoot, '.local', 'harness', requestId);
  const actualDir = await realpath(recordDir);
  // Records may not redirect outside their request folder through junctions/symlinks.
  if (relative(recordDir, actualDir) !== '' || !contained(actualRoot, actualDir)) throw new Error('record path');
  const expected = join(actualDir, name);
  const actual = await realpath(expected);
  if (relative(expected, actual) !== '' || dirname(actual) !== actualDir) throw new Error('record file');
  return readTaskRecord(actual, { root: actualRoot });
}

async function validateAttachments(files, root) {
  if (files === undefined) return;
  const help = '첨부는 프로젝트 안의 공개 파일만 허용합니다. 숨김 경로·인증 파일은 제외하고 10개 이하, 각 25MiB 이하로 첨부하세요.';
  try {
    if (!Array.isArray(files) || files.length > 10) throw new Error('attachments');
    if (!files.length) return;
    const lexicalRoot = resolve(root);
    const actualRoot = await realpath(root);
    const safeRelativePath = (base, target) => {
      const path = relative(base, target).split(sep).join('/');
      return contained(base, target) && isSafeProjectPath(path)
        && path.split('/').every(part => !part.startsWith('.'));
    };
    for (const file of files) {
      if (typeof file !== 'string' || !file.isWellFormed() || !isAbsolute(file)) throw new Error('attachment path');
      // Windows may provide a short temp path while realpath returns its long name.
      // Check both root spellings, then independently confine the resolved target.
      if (!safeRelativePath(lexicalRoot, resolve(file)) && !safeRelativePath(actualRoot, resolve(file))) throw new Error('attachment path');
      const actual = await realpath(file);
      if (!safeRelativePath(actualRoot, actual)) throw new Error('attachment target');
      const info = await stat(actual);
      if (!info.isFile() || info.size > 25 * 1024 * 1024) throw new Error('attachment file');
    }
  } catch {
    // Never return the private path, filename or file contents to Discord.
    throw new Error(help);
  }
}

export async function validateDiscordOutput(input, { root = projectRoot } = {}) {
  if (!guardedTools.has(input?.tool_name)) return;
  const text = input?.tool_input?.text;
  if (typeof text !== 'string' || text.length > 1900 || text.includes('\r')) throw new Error(formatHelp);
  const lines = text.split('\n');
  const found = lines.filter(line => /^(상태|요약|변경|검증|다음):/u.test(line));
  if (found.length !== 5 || headings.some((heading, index) => !found[index].startsWith(`${heading}: `) || !found[index].slice(heading.length + 2).trim()) || lines[0] !== found[0]) throw new Error(formatHelp);
  assertPublicText(text);
  if (input.tool_name === 'mcp__plugin_discord_discord__reply') await validateAttachments(input.tool_input.files, root);
  const final = finalHeader.exec(lines[0]);
  if (!final) {
    if (!interimHeader.test(lines[0])) throw new Error(formatHelp);
    return;
  }
  try {
    const task = await readRecord(root, final[2], 'task.json');
    const result = await readRecord(root, final[2], 'result.json');
    if (task.requestId !== final[2] || text !== renderTaskReport(result, task)) throw new Error('report mismatch');
  } catch {
    throw new Error('작업 결과 기록이 없거나 유효하지 않거나 본문과 다릅니다. 공유 저장소 .local/harness/<작업ID>/task.json 및 result.json을 검증하고 task:report 출력 본문을 그대로 사용하세요. 검사를 실행하지 않았으면 완료로 표시하지 마세요.');
  }
}

export async function evaluateHook(input, options) {
  try {
    await validateDiscordOutput(input, options);
    // Do not grant permissions: the normal Claude permission policy still applies.
    return {};
  } catch (error) {
    return { hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: 'deny', permissionDecisionReason: error.message } };
  }
}

export async function readHookInput(source) {
  const chunks = [];
  let size = 0;
  for await (const chunk of source) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk, 'utf8');
    size += buffer.length;
    if (size > 131072) throw new Error('Hook 입력 크기를 확인하세요.');
    chunks.push(buffer);
  }
  // Decode once, so a pipe chunk boundary cannot split a Korean/emoji character.
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks, size)));
}

async function main() {
  const result = await evaluateHook(await readHookInput(process.stdin));
  process.stdout.write(JSON.stringify(result));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(() => {
    process.stderr.write('MANUX 응답 검사 실패: hook 입력과 프로젝트 설정을 확인하세요.\n');
    process.exitCode = 2;
  });
}
