import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {desktopChannel, desktopVersion} from './desktop-channel.mjs';

// Usage: node scripts/desktop-build.mjs dir|dist
//   dir     unpacked Windows app in dist/win-unpacked (fast; used by test:packaged)
//   dist    NSIS installer in dist/, nothing uploaded
// Releases publish these exact artifacts in a separate protected CI job.
const root = fileURLToPath(new URL('..', import.meta.url));
const mode = process.argv[2];
if (!['dir', 'dist'].includes(mode)) {
  console.error('사용법: node scripts/desktop-build.mjs dir|dist');
  process.exit(2);
}
if (process.platform !== 'win32') {
  console.error('FAIL 데스크톱 패키지는 Windows에서만 만듭니다.');
  process.exit(1);
}
const channel = desktopChannel();
const packageManifest = JSON.parse(await fs.readFile(path.join(root, 'package.json'), 'utf8'));
const version = desktopVersion(packageManifest.version, channel.channel);

// The model is not committed. Fetch it and refuse to package anything but the pinned file.
const modelDirectory = path.join(root, 'public/models');
const expected = JSON.parse(await fs.readFile(path.join(modelDirectory, 'model-source.json'), 'utf8'));
const setup = spawnSync(process.execPath, ['scripts/setup.mjs'], {cwd: root, stdio: 'inherit', windowsHide: true});
if (setup.status !== 0) { console.error('FAIL 모델 준비(npm run setup)에 실패했습니다.'); process.exit(1); }
const model = await fs.readFile(path.join(modelDirectory, 'gesture_recognizer.task'));
const actual = createHash('sha256').update(model).digest('hex');
if (actual !== expected.sha256 || model.length !== expected.bytes) {
  console.error(`FAIL 모델 파일이 model-source.json의 해시와 다릅니다. 기대 ${expected.sha256}, 실제 ${actual}`);
  process.exit(1);
}

const builder = path.join(root, 'node_modules/electron-builder/cli.js');
const args = [builder, '--win', '--x64', '--config', 'electron-builder.yml', '--publish', 'never',
  `--config.appId=${channel.appId}`, `--config.productName=${channel.productName}`,
  `--config.win.executableName=${channel.executableName}`, `--config.nsis.artifactName=${channel.artifactName}`,
  `--config.extraMetadata.name=${channel.packageName}`, `--config.extraMetadata.manuxBuildChannel=${channel.channel}`,
  `--config.extraMetadata.version=${version}`];
if (mode === 'dir') args.push('--dir');
const result = spawnSync(process.execPath, args, {cwd: root, stdio: 'inherit', windowsHide: true,
  env: {...process.env, CSC_IDENTITY_AUTO_DISCOVERY: 'false'}});
if (result.status !== 0) { console.error('FAIL electron-builder가 실패했습니다.'); process.exit(result.status ?? 1); }
console.log(`PASS ${mode} 빌드 완료 → dist/ (서명되지 않은 빌드입니다)`);
