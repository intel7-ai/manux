// Test tooling only. Restore the exact source photographs declared by the manifest.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const directory = path.join(root, 'tests', 'fixtures');
const manifest = JSON.parse(await fs.readFile(path.join(directory, 'sources.json'), 'utf8'));
const verifyOnly = process.argv.includes('--verify-only');
if (process.argv.slice(2).some(arg => arg !== '--verify-only')) throw new Error('Supported option: --verify-only');
for (const item of manifest.files) {
  if (!/^[a-z_]+\.jpg$/.test(item.file) || !/^[a-f0-9]{64}$/.test(item.sha256)) throw new Error('Invalid fixture manifest');
  const url = new URL(item.url);
  if (url.protocol !== 'https:' || url.hostname !== 'storage.googleapis.com') throw new Error(`Unexpected fixture host: ${item.file}`);
  const target = path.join(directory, item.file);
  let bytes;
  try { bytes = await fs.readFile(target); }
  catch (error) {
    if (error.code !== 'ENOENT') throw error;
    if (verifyOnly) throw new Error(`Missing fixture: ${item.file}`);
    const response = await fetch(url, {signal: AbortSignal.timeout(60000)});
    if (!response.ok) throw new Error(`Fixture ${item.file}: HTTP ${response.status}`);
    bytes = Buffer.from(await response.arrayBuffer());
    if (createHash('sha256').update(bytes).digest('hex') !== item.sha256) throw new Error(`Downloaded hash mismatch: ${item.file}`);
    await fs.writeFile(target, bytes, {flag: 'wx'});
  }
  if (createHash('sha256').update(bytes).digest('hex') !== item.sha256) throw new Error(`Existing hash mismatch: ${item.file}; inspect the file before replacing it`);
  console.log(`Verified: ${item.file}`);
}
console.log(`Verified ${manifest.files.length} fixtures. These source photos do not establish webcam accuracy.`);
