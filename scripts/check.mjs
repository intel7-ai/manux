import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import assert from 'node:assert/strict';

const root = fileURLToPath(new URL('..', import.meta.url));
try {
  assert.equal(Number(process.versions.node.split('.')[0]), 24, 'Node.js 24 LTS가 필요합니다.');
  const manifest = JSON.parse(await fs.readFile(path.join(root, 'package.json'), 'utf8'));
  const lock = JSON.parse(await fs.readFile(path.join(root, 'package-lock.json'), 'utf8'));
  assert.equal(lock.lockfileVersion, 3, '지원되는 package-lock.json 버전은 3입니다.');
  for (const key of ['name', 'version', 'dependencies', 'devDependencies', 'engines']) {
    assert.deepEqual(lock.packages[''][key], manifest[key], `package-lock.json 불일치: ${key}. npm install --package-lock-only --ignore-scripts 로 갱신하세요.`);
  }
  const sources = ['server.js'];
  async function collect(relative) {
    for (const entry of await fs.readdir(path.join(root, relative), {withFileTypes:true})) {
      const name = path.join(relative, entry.name);
      if (entry.isDirectory()) await collect(name);
      else if (entry.isFile() && /\.(?:js|mjs|cjs)$/.test(name)) sources.push(name);
    }
  }
  for (const directory of ['src', 'desktop', 'scripts', 'tests']) await collect(directory);
  for (const source of sources.sort()) {
    const checked = spawnSync(process.execPath, ['--check', source], {cwd:root, encoding:'utf8', windowsHide:true});
    if (checked.status !== 0) throw new Error(`${source}\n${checked.stderr || checked.error || '구문 검사 실패'}`);
  }
  console.log(`PASS Node 24 / package-lock 일치 / JavaScript ${sources.length}개 구문 검사`);
} catch (error) {
  console.error(`FAIL ${error.message}`);
  process.exitCode = 1;
}
