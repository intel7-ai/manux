import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const REPOSITORY = 'intel7-ai/manux';
const root = fileURLToPath(new URL('..', import.meta.url));
const stablePattern = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

function stableParts(version) {
  if (typeof version !== 'string' || !stablePattern.test(version)) throw new Error('정식 버전은 major.minor.patch 형식이어야 합니다.');
  const parts = version.split('.').map(Number);
  if (parts.some(value => !Number.isSafeInteger(value) || value > 65535)) throw new Error('Windows 버전 범위를 벗어났습니다.');
  return parts;
}

export function compareVersions(left, right) {
  const a = stableParts(left), b = stableParts(right);
  for (let index = 0; index < 3; index++) if (a[index] !== b[index]) return Math.sign(a[index] - b[index]);
  return 0;
}

export function buildVersion(baseVersion, runNumber, {channel = 'stable', pullRequest} = {}) {
  const [major, minor, patch] = stableParts(baseVersion);
  if (!/^[1-9]\d*$/.test(String(runNumber)) || !Number.isSafeInteger(Number(runNumber))) throw new Error('유효한 GitHub run number가 필요합니다.');
  if (channel === 'preview') {
    const request = pullRequest || runNumber;
    if (!/^[1-9]\d*$/.test(String(request))) throw new Error('유효한 PR 번호가 필요합니다.');
    return `${baseVersion}-preview.${request}.${runNumber}`;
  }
  if (channel !== 'stable' || patch + Number(runNumber) > 65535) throw new Error('지원하지 않는 채널 또는 Windows 버전 범위입니다.');
  return `${major}.${minor}.${patch + Number(runNumber)}`;
}

function releaseContext(env = process.env) {
  if (env.GITHUB_REPOSITORY !== REPOSITORY || env.GITHUB_REF !== 'refs/heads/production'
    || env.GITHUB_EVENT_NAME !== 'push'
    || !/^[0-9a-f]{40}$/.test(env.GITHUB_SHA ?? '')) throw new Error('production의 검증된 커밋에서만 정식 배포할 수 있습니다.');
  return {repository: REPOSITORY, sourceSha: env.GITHUB_SHA};
}

export function validateManifest(manifest, context) {
  if (manifest?.schemaVersion !== 1 || manifest.repository !== context.repository || manifest.sourceSha !== context.sourceSha
    || manifest.tag !== `v${manifest.version}`) throw new Error('릴리스 manifest와 workflow 커밋이 다릅니다.');
  stableParts(manifest.version);
  const names = [`MANUX-Setup-${manifest.version}.exe`, `MANUX-Setup-${manifest.version}.exe.blockmap`, 'latest.yml'];
  if (!Array.isArray(manifest.files) || manifest.files.length !== names.length
    || names.some(name => manifest.files.filter(file => file.name === name).length !== 1)
    || manifest.files.some(file => !/^[0-9a-f]{64}$/.test(file.sha256 ?? '') || !Number.isSafeInteger(file.bytes) || file.bytes < 1)) {
    throw new Error('릴리스 파일 목록 또는 체크섬이 올바르지 않습니다.');
  }
  return manifest;
}

export function validateUpdateMetadata(text, version, installerBytes) {
  const installer = `MANUX-Setup-${version}.exe`;
  const values = name => [...text.matchAll(new RegExp(`^\\s*(?:-\\s*)?${name}:\\s*(.*?)\\s*$`, 'gm'))].map(match => match[1]);
  const expectedHash = createHash('sha512').update(installerBytes).digest('base64');
  if (values('version').length !== 1 || values('version')[0] !== version
    || values('path').length !== 1 || values('path')[0] !== installer
    || values('url').length !== 1 || values('url')[0] !== installer
    || values('sha512').length !== 2 || values('sha512').some(hash => hash !== expectedHash)) {
    throw new Error('업데이트 메타데이터가 설치파일과 일치하지 않습니다.');
  }
}

export async function createManifest(directory, version, context) {
  stableParts(version);
  const names = [`MANUX-Setup-${version}.exe`, `MANUX-Setup-${version}.exe.blockmap`, 'latest.yml'];
  const contents = await Promise.all(names.map(name => fs.readFile(path.join(directory, name))));
  validateUpdateMetadata(contents[2].toString('utf8'), version, contents[0]);
  const manifest = {schemaVersion: 1, ...context, version, tag: `v${version}`,
    files: names.map((name, index) => ({name, bytes: contents[index].length, sha256: digest(contents[index])}))};
  validateManifest(manifest, context);
  await fs.writeFile(path.join(directory, 'release-manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
  return manifest;
}

export async function verifyArtifact(directory, context) {
  const manifest = validateManifest(JSON.parse(await fs.readFile(path.join(directory, 'release-manifest.json'), 'utf8')), context);
  for (const file of manifest.files) {
    const target = path.join(directory, file.name);
    const stat = await fs.lstat(target);
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('릴리스 artifact는 일반 파일이어야 합니다.');
    const bytes = await fs.readFile(target);
    if (bytes.length !== file.bytes || digest(bytes) !== file.sha256) throw new Error('빌드 후 릴리스 파일이 변경됐습니다.');
  }
  const installer = await fs.readFile(path.join(directory, `MANUX-Setup-${manifest.version}.exe`));
  validateUpdateMetadata(await fs.readFile(path.join(directory, 'latest.yml'), 'utf8'), manifest.version, installer);
  return manifest;
}

// Every GitHub operation uses argv (never shell interpolation). Only publish receives a write token.
function gh(args) {
  return execFileSync('gh', args, {encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true});
}
function api(endpoint, {optional = false} = {}) {
  try { return JSON.parse(gh(['api', endpoint])); }
  catch (error) {
    if (optional && /HTTP 404/.test(String(error.stderr))) return null;
    throw new Error('GitHub 릴리스 상태 조회에 실패했습니다.');
  }
}

async function tagCommit(tag) {
  const reference = api(`repos/${REPOSITORY}/git/ref/tags/${tag}`, {optional: true});
  if (!reference) return null;
  let object = reference.object;
  for (let depth = 0; object?.type === 'tag' && depth < 5; depth++) object = api(`repos/${REPOSITORY}/git/tags/${object.sha}`).object;
  if (object?.type !== 'commit') throw new Error('태그가 커밋을 가리키지 않습니다.');
  return object.sha;
}

export function validateExistingRelease(release, manifest, commit) {
  if (commit !== manifest.sourceSha || release.tag_name !== manifest.tag || release.draft || release.prerelease) {
    throw new Error('기존 태그/릴리스가 이번 완료된 정식 배포와 다릅니다. 덮어쓰지 않습니다.');
  }
  for (const file of manifest.files) {
    const assets = release.assets?.filter(asset => asset.name === file.name) ?? [];
    if (assets.length !== 1 || assets[0].size !== file.bytes || assets[0].digest !== `sha256:${file.sha256}`) {
      throw new Error('기존 릴리스의 파일이 이번 빌드와 다릅니다. 덮어쓰지 않습니다.');
    }
  }
}

async function publish(directory) {
  const context = releaseContext();
  const manifest = await verifyArtifact(directory, context);
  const existing = api(`repos/${REPOSITORY}/releases/tags/${manifest.tag}`, {optional: true});
  const commit = await tagCommit(manifest.tag);
  if (existing) {
    validateExistingRelease(existing, manifest, commit);
    console.log(`이미 게시된 동일 릴리스입니다: ${manifest.tag}`);
    return;
  }
  if (commit) throw new Error('이미 존재하는 태그를 재사용하지 않습니다. 새 production PR을 병합하여 새 버전을 만드세요.');
  const latest = api(`repos/${REPOSITORY}/releases/latest`, {optional: true});
  if (latest && compareVersions(manifest.version, latest.tag_name.replace(/^v/, '')) <= 0) {
    throw new Error('현재 정식 버전보다 오래된 빌드는 게시하지 않습니다.');
  }
  const notes = path.join(directory, 'release-notes.md');
  await fs.writeFile(notes, `MANUX Windows ${manifest.version}\n\nProduction commit: ${manifest.sourceSha}\n\nPR 검증과 Windows 패키지 시작 검사를 통과한 설치본입니다. 실제 웹캠·OS 입력 검증과 Windows 코드 서명은 별도입니다.\n`);
  gh(['api', '--method', 'POST', `repos/${REPOSITORY}/git/refs`, '-f', `ref=refs/tags/${manifest.tag}`, '-f', `sha=${manifest.sourceSha}`]);
  gh(['release', 'create', manifest.tag, '--repo', REPOSITORY, '--verify-tag',
    '--title', `MANUX ${manifest.version}`, '--notes-file', notes, '--draft']);
  // Do not use --clobber. A failed/partial draft stays visible for inspection and cannot become an update feed.
  gh(['release', 'upload', manifest.tag, '--repo', REPOSITORY,
    ...manifest.files.map(file => path.join(directory, file.name)), path.join(directory, 'release-manifest.json')]);
  if (await tagCommit(manifest.tag) !== manifest.sourceSha) throw new Error('게시 직전 태그 커밋 확인이 실패했습니다. draft를 보존합니다.');
  const uploaded = api(`repos/${REPOSITORY}/releases/tags/${manifest.tag}`);
  validateExistingRelease({...uploaded, draft: false}, manifest, manifest.sourceSha);
  gh(['release', 'edit', manifest.tag, '--repo', REPOSITORY, '--draft=false', '--latest']);
  console.log(`정식 릴리스 게시 완료: ${manifest.tag}`);
}

async function main() {
  const [command, argument] = process.argv.slice(2);
  const manifestPath = path.join(root, 'package.json');
  if (command === 'prepare' && ['stable', 'preview'].includes(argument)) {
    if (argument === 'stable') releaseContext();
    const pkg = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
    pkg.version = buildVersion(pkg.version, process.env.GITHUB_RUN_NUMBER, {channel: argument, pullRequest: process.env.MANUX_PR_NUMBER});
    const lockPath = path.join(root, 'package-lock.json');
    const lock = JSON.parse(await fs.readFile(lockPath, 'utf8'));
    lock.version = pkg.version;
    if (lock.packages?.['']) lock.packages[''].version = pkg.version;
    await fs.writeFile(manifestPath, `${JSON.stringify(pkg, null, 2)}\n`);
    await fs.writeFile(lockPath, `${JSON.stringify(lock, null, 2)}\n`);
    if (process.env.GITHUB_OUTPUT) await fs.appendFile(process.env.GITHUB_OUTPUT, `version=${pkg.version}\ntag=v${pkg.version}\n`);
    console.log(`빌드 버전: ${pkg.version}`);
  } else if (command === 'manifest') {
    const pkg = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
    console.log(JSON.stringify(await createManifest(path.resolve(root, argument ?? 'dist'), pkg.version, releaseContext()), null, 2));
  } else if (command === 'publish') await publish(path.resolve(root, argument ?? 'release-artifact'));
  else throw new Error('사용법: release-version.mjs prepare stable|preview / manifest dist / publish release-artifact');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(`릴리스 중단: ${error.message}`); process.exitCode = 1; });
}
