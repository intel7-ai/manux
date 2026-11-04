import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';

// Fingerprint source bytes, including unstaged/new files. Porcelain alone misses
// a second edit to an already modified file while another teammate is verifying.
export function repositoryState(root) {
  const command = args => spawnSync('git', args, {cwd:root, encoding:'utf8', windowsHide:true, maxBuffer:8 * 1024 * 1024});
  const head = command(['rev-parse', '--verify', 'HEAD']);
  const status = command(['status', '--porcelain=v1', '--untracked-files=all']);
  const paths = command(['ls-files', '--cached', '--others', '--exclude-standard', '-z']);
  if ([head, status, paths].some(result => result.status !== 0)) {
    return {head:null, dirty:true, status:'Git metadata unavailable', sourceDigest:null};
  }
  const hash = createHash('sha256');
  const names = [...new Set(paths.stdout.split('\0').filter(Boolean))].sort();
  for (const name of names) {
    if (/(?:^|\/)(?:node_modules|\.git|\.local|\.tools|\.npm-cache|test-results|worktrees|\.env(?:\.[^/]*)?)(?:\/|$)/i.test(name)) continue;
    if (!/\.(?:md|json|js|mjs|cjs|html|css|svg|yml|yaml|ps1|cmd)$|^\.(?:gitignore|editorconfig|node-version|nvmrc)$/i.test(name)) continue;
    const filename = path.join(root, name);
    let digest;
    try {
      const info = fs.lstatSync(filename);
      digest = info.isSymbolicLink() ? `link:${fs.readlinkSync(filename)}`
        : info.isFile() ? createHash('sha256').update(fs.readFileSync(filename)).digest('hex') : 'not-file';
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      digest = 'deleted';
    }
    hash.update(JSON.stringify([name, digest]));
  }
  return {head:head.stdout.trim(), dirty:Boolean(status.stdout.trim()), status:status.stdout.trim(), sourceDigest:hash.digest('hex')};
}
