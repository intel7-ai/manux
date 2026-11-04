import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {repositoryState} from './verification-state.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
const output = path.join(root, '.local/verification');
await fs.mkdir(output, {recursive:true});
const started = repositoryState(root);
const report = {schemaVersion:1, startedAt:new Date().toISOString(), node:process.version,
  platform:process.platform, git:started, status:'running', stages:[]};
const log = [];
// Invalidate old success evidence before starting, including interrupted runs.
await fs.writeFile(path.join(output, 'latest.json'), `${JSON.stringify(report, null, 2)}\n`);
async function run(name, args) {
  const command = `node ${args.join(' ')}`;
  const prefix = `\n[${name}] ${command}\n`;
  process.stdout.write(prefix); log.push(prefix);
  const began = Date.now();
  const child = spawn(process.execPath, args, {cwd:root, windowsHide:true, timeout:300_000});
  for (const stream of [child.stdout, child.stderr]) stream.on('data', bytes => {
    const text = bytes.toString(); process.stdout.write(text); log.push(text);
  });
  const result = await new Promise(resolve => {
    child.once('error', error => resolve({exitCode:1, error:error.message}));
    child.once('close', (code, signal) => resolve({exitCode:code ?? 1, ...(signal ? {signal} : {})}));
  });
  const stage = {name, command, ...result, durationMs:Date.now() - began};
  report.stages.push(stage);
  return result.exitCode === 0;
}
const tests = (await fs.readdir(path.join(root, 'tests'))).filter(name => name.endsWith('.test.js')).sort()
  .map(name => `tests/${name}`);
let ok = await run('check', ['scripts/check.mjs']);
if (ok) ok = await run('harness', ['scripts/task-report.mjs', 'harness/task.example.json', 'harness/result.example.json', '--check']);
if (ok) ok = await run('unit', ['--test', ...tests]);
const ended = repositoryState(root);
if (started.head !== ended.head || started.status !== ended.status || started.sourceDigest !== ended.sourceDigest) {
  ok = false;
  log.push('\nFAIL 검사 중 Git 상태 또는 소스 내용이 바뀌었습니다. 작업을 멈추고 npm run verify 를 다시 실행하세요.\n');
  console.error(log.at(-1));
}
report.status = ok ? 'passed' : 'failed';
report.finishedAt = new Date().toISOString();
report.git = ended;
await fs.writeFile(path.join(output, 'verify.log'), log.join(''));
await fs.writeFile(path.join(output, 'latest.json'), `${JSON.stringify(report, null, 2)}\n`);
console.log(`\n${ok ? 'PASS' : 'FAIL'} MANUX verify — .local/verification/latest.json / verify.log`);
console.log('이 검사는 실제 웹캠 인식률·실제 OS 마우스 조작·클라우드 배포를 검증하지 않습니다.');
process.exitCode = ok ? 0 : 1;
