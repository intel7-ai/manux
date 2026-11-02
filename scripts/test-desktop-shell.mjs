import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath,pathToFileURL} from 'node:url';

const root=fileURLToPath(new URL('..',import.meta.url));
const playwright=process.env.PLAYWRIGHT_MODULE || path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs');
const {_electron}=await import(pathToFileURL(playwright));
const env={...process.env,MANUX_MOUSE_DRY_RUN:'1',MANUX_DESKTOP_PORT:'0'};
delete env.ELECTRON_RUN_AS_NODE;
const application=await _electron.launch({
  executablePath:path.join(root,'node_modules/electron/dist/electron.exe'),
  args:['--use-fake-device-for-media-stream',path.join(root,'desktop/main.mjs')],cwd:root,env,timeout:60000,
});
const checks=[];
let page;
try {
  page=await application.firstWindow();
  const errors=[]; page.on('pageerror',error => errors.push(error.message));
  await page.waitForFunction(async () => (await window.manuxDesktop.getStatus()).available,{},{timeout:30000});
  const initial=await page.evaluate(() => window.manuxDesktop.getStatus());
  assert.equal(initial.enabled,false); assert.equal(initial.dryRun,true);
  assert.equal(await page.evaluate(() => typeof window.require),'undefined');
  assert.equal(await page.locator('#mouse-panel').isVisible(),true);
  checks.push('isolated desktop shell opens mouse mode with native control OFF');
  await page.locator('#camera-start').click();
  await page.waitForFunction(() => document.querySelector('#camera-badge').textContent === '인식 중',{},{timeout:60000});
  checks.push('video permission, real MediaPipe Worker and fake 1080p camera initialize in Electron');
  const microphone=await page.evaluate(async () => {
    try {const stream=await navigator.mediaDevices.getUserMedia({audio:true});stream.getTracks().forEach(t=>t.stop());return 'allowed';}
    catch(error) {return error.name;}
  });
  assert.equal(microphone,'NotAllowedError');
  await page.locator('#mouse-target').selectOption('desktop');
  await page.locator('#mouse-enabled').check();
  await page.waitForFunction(async () => (await window.manuxDesktop.getStatus()).enabled);
  await page.waitForTimeout(1300);
  assert.equal((await page.evaluate(() => window.manuxDesktop.getStatus())).enabled,true,'fresh camera frames keep native lease alive');
  await page.locator('#mouse-stop').click();
  await page.waitForFunction(async () => !(await window.manuxDesktop.getStatus()).enabled);
  checks.push('native enable/stop IPC, fresh-frame lease and microphone rejection');
  await page.locator('#camera-stop').click();
  await page.evaluate(async () => { await window.manuxDesktop.setEnabled(true); window.manuxDesktop.send([{type:'button',button:'left',down:true}]); });
  await page.waitForFunction(async () => !(await window.manuxDesktop.getStatus()).enabled,{},{timeout:4000});
  checks.push('native watchdog expires held button without camera batches (dry-run)');
  await page.locator('#mouse-target').selectOption('practice');
  await fs.mkdir(path.join(root,'test-results'),{recursive:true});
  // Layout screenshots are taken in test-mouse-browser; this native smoke window stays hidden.
  assert.deepEqual(errors,[]);
  const report={passed:true,method:'Real Electron + native helper, synthetic camera; MANUX_MOUSE_DRY_RUN=1 prevents all OS injection.',checks};
  await fs.writeFile(path.join(root,'test-results','air-mouse-electron.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
} catch(error) {
  console.error(await page?.evaluate(async () => ({native:await window.manuxDesktop.getStatus(),mouse:document.querySelector('#mouse-status').textContent,camera:document.querySelector('#camera-message').textContent,stats:document.querySelector('#camera-stats').textContent,hidden:document.hidden,phase:document.querySelector('#mouse-panel').dataset.phase})));
  throw error;
} finally {await application.close();}
