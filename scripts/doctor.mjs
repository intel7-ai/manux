import {spawnSync} from 'node:child_process';
let failures = 0;
function result(ok, name) {
  console.log(`${ok ? 'OK' : 'FAIL'} ${name}`);
  if (!ok) failures++;
}
result(Number(process.versions.node.split('.')[0]) === 24, 'Node.js 24 (공유 하네스)');
result(spawnSync('git', ['--version'], {windowsHide:true}).status === 0, 'Git');
console.log('INFO 앱 실행: cd apps/manux → uv run --locked --python 3.12 --extra ui --extra vision python -m manux');
console.log('INFO 앱 시험: cd apps/manux → uv run --locked --python 3.12 pytest -q');
console.log('INFO 공유 하네스: npm run verify (외부 npm 의존성 없음)');
process.exitCode = failures ? 1 : 0;
