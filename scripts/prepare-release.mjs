import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {repositoryState} from './verification-state.mjs';

const defaultRoot = fileURLToPath(new URL('..', import.meta.url));
const roots = new Set(['README.md', 'AGENTS.md', 'CLAUDE.md', 'CONTRIBUTING.md', 'package.json',
  'package-lock.json', 'server.js', '.gitignore', '.editorconfig', '.node-version', '.nvmrc']);
const extensions = /\.(?:md|json|js|mjs|cjs|html|css|svg|yml|yaml|ps1|cmd)$/i;
export function isReleasePath(name) {
  if (typeof name !== 'string' || name.includes('\\') || name.startsWith('/') || name.includes('\0')) return false;
  const parts = name.split('/');
  if (parts.some(part => !part || part === '.' || part === '..' || /^\.env(?:\.|$)/i.test(part)
    || /^(?:node_modules|\.git|\.local|\.tools|test-results|worktrees)$/i.test(part))) return false;
  if (roots.has(name)) return true;
  if (parts.length === 1) return /^(?:MANUX .+\.cmd|(?:start|stop)-manux(?:-lan)?\.ps1)$/.test(name);
  const allowedDirectory = /^(?:src|desktop|scripts|tests|docs|public|harness|\.github)\//.test(name)
    || /^\.claude\/(?:commands|agents)\/.+\.md$/.test(name) || name === '.claude/settings.json';
  return allowedDirectory && extensions.test(name)
    && !/(?:^|\/)(?:credentials|secrets?|settings\.local)(?:\.|\/|$)/i.test(name);
}

function git(root, args, encoding = 'utf8') {
  return execFileSync('git', args, {cwd:root, encoding, windowsHide:true, maxBuffer:4 * 1024 * 1024});
}
function cleanSnapshot(root) {
  const head = git(root, ['rev-parse', '--verify', 'HEAD']).trim();
  if (git(root, ['status', '--porcelain=v1', '--untracked-files=all']).trim()) {
    throw new Error('미커밋 변경이 있습니다. 검토한 작업을 먼저 커밋하고 npm run verify 를 다시 실행하세요. 자동 커밋하지 않습니다.');
  }
  return head;
}
export function assertVerification(report, head, sourceDigest = report?.git?.sourceDigest) {
  if (report?.schemaVersion !== 1 || report.status !== 'passed' || report.git?.head !== head || report.git.dirty !== false
    || !/^[a-f0-9]{64}$/.test(sourceDigest || '') || report.git.sourceDigest !== sourceDigest
    || !['check', 'harness', 'unit'].every(name => report.stages?.some(stage => stage.name === name && stage.exitCode === 0))) {
    throw new Error('현재 커밋의 통과 기록이 없습니다. npm run verify 를 먼저 실행하세요.');
  }
}
export async function prepareRelease({root = defaultRoot} = {}) {
  root = await fs.realpath(root);
  const head = cleanSnapshot(root);
  const evidence = JSON.parse(await fs.readFile(path.join(root, '.local/verification/latest.json'), 'utf8'));
  assertVerification(evidence, head, repositoryState(root).sourceDigest);
  const names = git(root, ['ls-files', '-z']).split('\0').filter(Boolean);
  const selected = names.filter(isReleasePath).sort();
  if (!selected.includes('package.json') || !selected.includes('README.md')) throw new Error('MANUX 소스 루트가 아닙니다.');
  const entries = [];
  let total = 0;
  for (const name of selected) {
    const filename = path.resolve(root, name);
    const stat = await fs.lstat(filename);
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`일반 파일만 포함할 수 있습니다: ${name}`);
    const resolved = await fs.realpath(filename);
    const relative = path.relative(root, resolved);
    if (relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) throw new Error(`프로젝트 밖의 파일입니다: ${name}`);
    if (stat.size > 2 * 1024 * 1024 || (total += stat.size) > 20 * 1024 * 1024) throw new Error(`소스 패키지 용량 제한 초과: ${name}`);
    const bytes = await fs.readFile(filename);
    // Text-only allowlist, plus common high-confidence credential formats.
    if (/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-ant-[A-Za-z0-9_-]{30,}|mfa\.[A-Za-z0-9_-]{40,})/.test(bytes.toString('utf8'))) {
      throw new Error(`자격 증명처럼 보이는 내용이 있어 중단했습니다: ${name}`);
    }
    entries.push({name, bytes, sha256:createHash('sha256').update(bytes).digest('hex')});
  }
  if (cleanSnapshot(root) !== head) throw new Error('패키지 준비 중 Git 커밋이 바뀌었습니다.');
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const destination = path.join(root, '.local/releases', `${stamp}-${head.slice(0, 12)}`);
  await fs.mkdir(path.dirname(destination), {recursive:true});
  await fs.mkdir(destination); // Never overwrite a previous handoff.
  const source = path.join(destination, 'source');
  for (const entry of entries) {
    const target = path.join(source, entry.name);
    await fs.mkdir(path.dirname(target), {recursive:true});
    await fs.writeFile(target, entry.bytes, {flag:'wx'});
  }
  const manifest = {schemaVersion:1, kind:'verified-source-handoff', createdAt:new Date().toISOString(),
    commit:head, node:process.version, verification:evidence,
    limits:['Source only; not an installer or deployment.',
      'No node_modules, secrets, downloaded models, videos, presentation binaries, or webcam captures.',
      'Automated checks do not measure real webcam recognition or actual OS input.'],
    files:entries.map(({name, bytes, sha256}) => ({path:`source/${name}`, bytes:bytes.length, sha256})),
    excluded:names.filter(name => !isReleasePath(name))};
  await fs.writeFile(path.join(destination, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, {flag:'wx'});
  return {destination, manifest};
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const {destination, manifest} = await prepareRelease();
    console.log(`PASS 소스 전달 패키지 ${manifest.files.length}개 파일: ${destination}`);
    console.log('설치 파일 또는 실제 배포가 아닙니다. source/README.md의 준비 절차를 따르세요.');
  } catch (error) {
    console.error(`FAIL ${error.message}`);
    process.exitCode = 1;
  }
}
