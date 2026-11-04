import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';

const root = fileURLToPath(new URL('..', import.meta.url));
let failures = 0;
const result = (ok, name, fix) => {
  console.log(`${ok ? 'OK' : 'FAIL'} ${name}${ok ? '' : ` — ${fix}`}`);
  if (!ok) failures++;
};
result(Number(process.versions.node.split('.')[0]) === 24, `Node ${process.version}`,
  'Node.js 24 LTS를 설치한 뒤 터미널을 다시 여세요. 기준 버전: .node-version');
const git = spawnSync('git', ['--version'], {encoding:'utf8', windowsHide:true});
result(git.status === 0, 'Git', 'Git for Windows를 설치한 뒤 터미널을 다시 여세요.');
const manifest = JSON.parse(await fs.readFile(path.join(root, 'package.json'), 'utf8'));
const lock = JSON.parse(await fs.readFile(path.join(root, 'package-lock.json'), 'utf8'));
for (const name of Object.keys({...manifest.dependencies, ...manifest.devDependencies})) {
  const expected = lock.packages[`node_modules/${name}`]?.version;
  const installed = await fs.readFile(path.join(root, 'node_modules', name, 'package.json'), 'utf8')
    .then(JSON.parse).catch(() => null);
  result(Boolean(expected && installed?.version === expected), `${name}: ${installed?.version || '미설치'}`,
    '이 프로젝트 폴더에서 npm ci --ignore-scripts 를 실행하세요.');
}
const optional = async (name, filename, install) => {
  const available = await fs.access(filename).then(() => true, () => false);
  console.log(`INFO ${name}: ${available ? '준비됨' : `선택 설치 필요 (${install})`}`);
};
await optional('MediaPipe 모델 / 실제 카메라 실행', path.join(root, 'public/models/gesture_recognizer.task'), 'npm run setup');
await optional('Electron 실행 파일', path.join(root, 'node_modules/electron/path.txt'), 'npm run desktop:setup');
try {
  const {chromium} = await import('playwright');
  await optional('합성 브라우저 검사', chromium.executablePath(), 'npm run browser:install');
} catch {
  console.log('INFO 합성 브라우저 검사: npm ci --ignore-scripts → npm run browser:install');
}
console.log(failures ? `환경 확인 ${failures}건 실패. 위 안내대로 처리하고 npm run doctor 를 다시 실행하세요.`
  : '기본 개발 환경이 준비되었습니다. 다음 명령: npm run verify');
process.exitCode = failures ? 1 : 0;
