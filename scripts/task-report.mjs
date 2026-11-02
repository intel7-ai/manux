import { lstat, mkdir, open, realpath, writeFile } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ContractValidationError, renderTaskReport, renderTaskReportHtml, validateResult } from './task-contract.mjs';

function inside(root, target) {
  const child = relative(root, target);
  return child !== '' && child !== '..' && !child.startsWith(`..${sep}`) && !isAbsolute(child);
}

export async function readTaskRecord(path, { root = process.cwd() } = {}) {
  let handle;
  try {
    const base = await realpath(root);
    const target = await realpath(resolve(base, path));
    if (!inside(base, target) || !target.toLowerCase().endsWith('.json')) throw new Error('Invalid record path');
    handle = await open(target, 'r');
    const info = await handle.stat();
    if (!info.isFile() || info.size > 65_536) throw new Error('Invalid record');
    const buffer = Buffer.alloc(65_537);
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
    if (bytesRead > 65_536) throw new Error('Record too large');
    return JSON.parse(buffer.subarray(0, bytesRead).toString('utf8'));
  } catch {
    throw new ContractValidationError(['입력 파일: 저장소 안의 64KB 이하 JSON 파일 두 개를 지정하세요']);
  } finally { await handle?.close().catch(() => {}); }
}

const USAGE = '사용법: node scripts/task-report.mjs TASK.json RESULT.json [--check | --html REPORT.html]';

/** Write the standalone HTML report inside the workspace, never through a link that leaves it. */
async function writeHtmlReport(path, html, root) {
  const fail = () => new ContractValidationError(['HTML 출력: 저장소 안의 .html 파일 경로를 지정하세요']);
  const base = await realpath(root);
  const target = resolve(base, path);
  if (!inside(base, target) || !target.toLowerCase().endsWith('.html')) throw fail();
  await mkdir(dirname(target), { recursive: true });
  const parent = await realpath(dirname(target));
  if (parent !== base && !inside(base, parent)) throw fail();
  const file = join(parent, basename(target));
  const existing = await lstat(file).catch(() => null);
  if (existing && !existing.isFile()) throw fail();
  await writeFile(file, html, 'utf8');
  return relative(base, file).split(sep).join('/');
}

export async function runTaskReport(args = process.argv.slice(2), { root = process.cwd(), output = console.log, error = console.error, notice = console.error } = {}) {
  try {
    const check = args.length === 3 && args[2] === '--check';
    const html = args.length === 4 && args[2] === '--html';
    if (args.length < 2 || args.length > 4 || (args.length === 3 && !check) || (args.length === 4 && !html)) throw new ContractValidationError([USAGE]);
    const [task, result] = await Promise.all(args.slice(0, 2).map(path => readTaskRecord(path, { root })));
    validateResult(result, task);
    if (html) notice(`HTML 보고서를 저장했습니다: ${await writeHtmlReport(args[3], renderTaskReportHtml(result, task), root)}`);
    output(check ? `작업 기록 검증 통과: ${result.requestId}` : renderTaskReport(result, task));
    return 0;
  } catch (failure) {
    error(failure instanceof ContractValidationError ? failure.message : '작업 기록을 처리하지 못했습니다.');
    return 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) process.exitCode = await runTaskReport();
