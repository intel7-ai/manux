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
  assert.equal(lock.lockfileVersion, 3);
  for (const key of ['name', 'version', 'dependencies', 'devDependencies', 'engines']) {
    assert.deepEqual(lock.packages[''][key], manifest[key], `package-lock.json 불일치: ${key}`);
  }
  const sources = [];
  async function collect(directory) {
    for (const entry of await fs.readdir(path.join(root, directory), {withFileTypes:true})) {
      const relative = `${directory}/${entry.name}`;
      if (entry.isDirectory()) await collect(relative);
      else if (entry.isFile() && /\.(?:js|mjs|cjs)$/.test(entry.name)) sources.push(relative);
    }
  }
  for (const directory of ['scripts', 'tests']) await collect(directory);
  for (const source of sources.sort()) {
    const checked = spawnSync(process.execPath, ['--check', source], {cwd:root, encoding:'utf8', windowsHide:true});
    assert.equal(checked.status, 0, `${source}: ${checked.stderr || checked.error || '구문 검사 실패'}`);
  }
  console.log(`PASS Node 24 / tooling lock / JavaScript ${sources.length}개 구문 검사`);
} catch (error) {
  console.error(`FAIL ${error.message}`);
  process.exitCode = 1;
}
