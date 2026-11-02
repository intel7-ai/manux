import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL, fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {createServer} from '../server.js';
import {hand} from '../tests/helpers/mouse-hand.js';

const root = fileURLToPath(new URL('..', import.meta.url));
const playwright = process.env.PLAYWRIGHT_MODULE || path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs');
const {chromium} = await import(pathToFileURL(playwright));
const server = createServer(); await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
const browser = await chromium.launch({channel:'chrome',headless:true});
const context = await browser.newContext({viewport:{width:1440,height:1100}});
const page = await context.newPage();
const errors = [];
page.on('pageerror',error => { errors.push(error.message); console.error(error.stack); });
const report = {purpose:'Synthetic landmarks + canvas camera through app integration. Does not measure actual hand recognition or inject OS input.',checks:[]};
try {
  await context.addInitScript(({source}) => {
    const makeHand = (0,eval)(`(${source})`);
    const input = window.mouseTest = {pose:'move',x:0,y:0,lost:false,enabled:false,batches:[],generation:0};
    let onStatus = () => {};
    const status = () => ({available:true,enabled:input.enabled,platform:'win32'});
    window.manuxDesktop = {
      getStatus:async () => status(),
      setEnabled:async value => { input.enabled = value; return status(); },
      stop:() => { input.enabled = false; },
      send:events => { if (input.enabled) input.batches.push(events); },
      onStatus:callback => { onStatus = callback; return () => {}; },
    };
    input.emergencyStop = () => { input.enabled = false; onStatus(status()); };
    window.Worker = class {
      postMessage(packet) {
        packet.bitmap?.close();
        if (input.stalled && packet.type !== 'init') return;
        setTimeout(() => this.onmessage?.({data:packet.type === 'init'
          ? {id:packet.id,ok:true,delegate:'TEST'}
          : {id:packet.id,ok:true,inferenceMs:3,result:{
            landmarks:input.lost ? [] : [makeHand(input.pose,{x:input.x,y:input.y})],
            gestures:[[{categoryName:'Open_Palm',score:.99}]], handedness:[[{categoryName:'Right',score:.99}]],
          }}}), 4);
      }
      terminate() { this.onmessage = null; }
    };
    navigator.mediaDevices.getUserMedia = async () => {
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1000;
      const ctx = canvas.getContext('2d');
      let n = 0;
      const paint = () => { ctx.fillStyle = '#112331'; ctx.fillRect(0,0,1000,1000); ctx.fillStyle = '#46e5bd'; ctx.fillRect((n++%100)*5,10,10,10); };
      paint(); const timer = setInterval(paint,32); const stream = canvas.captureStream(30);
      const track = stream.getVideoTracks()[0], stop = track.stop.bind(track);
      track.stop = () => { clearInterval(timer); stop(); };
      return stream;
    };
  }, {source:hand.toString()});
  await page.goto(`http://127.0.0.1:${server.address().port}/#mouse`);
  if (errors.length) { console.error(await page.evaluate(async () => { const raw=await (await fetch('/')).text(); return {raw:raw.match(/<select id="mouse-target">.*?<\/select>/)?.[0],html:document.querySelector('#mouse-target').outerHTML,parsed:new DOMParser().parseFromString(raw,'text/html').querySelector('#mouse-target').outerHTML}; })); throw new Error('App initialization failed'); }
  await page.locator('#camera-start').click();
  await page.waitForFunction(() => !document.querySelector('#mouse-enabled').disabled);
  assert.equal(await page.locator('#gesture-enabled').isDisabled(),true, 'mouse is independent of video load');
  await page.locator('#mouse-enabled').check();
  const pose = async (name, extras={}) => { await page.evaluate(value => Object.assign(window.mouseTest,value),{pose:name,...extras}); await page.waitForTimeout(320); };
  await pose('move');
  const initial = await page.locator('#mouse-cursor').evaluate(el => el.style.transform);
  await pose('move',{x:-.035,y:.02});
  assert.notEqual(await page.locator('#mouse-cursor').evaluate(el => el.style.transform),initial);
  assert.equal(await page.locator('#mouse-panel').getAttribute('data-mode'),'move');
  report.checks.push('open palm + whole-hand movement moves practice cursor');

  const moveTo = async selector => {
    await pose('move');
    for (let attempt=0; attempt<10; attempt++) {
      const delta = await page.evaluate(selector => {
        const cursor=document.querySelector('#mouse-cursor').getBoundingClientRect(), target=document.querySelector(selector).getBoundingClientRect();
        return {x:target.left+target.width/2-cursor.left,y:target.top+target.height/2-cursor.top};
      },selector);
      if (Math.hypot(delta.x,delta.y)<5) return;
      await page.evaluate(delta => {
        window.mouseTest.x -= Math.max(-45,Math.min(45,delta.x))/1800;
        window.mouseTest.y += Math.max(-45,Math.min(45,delta.y))/1800;
      },delta);
      await page.waitForTimeout(220);
    }
    throw new Error(`Practice cursor did not reach ${selector}`);
  };
  await moveTo('#mouse-click-target'); await pose('left'); await pose('move');
  assert.equal(await page.locator('#mouse-click-count').textContent(),'1');
  await moveTo('#mouse-drag-target');
  const dragBefore=await page.locator('#mouse-drag-target').evaluate(el => el.offsetLeft);
  await pose('left');
  await page.evaluate(() => { window.mouseTest.x -= .035; }); await page.waitForTimeout(350);
  assert.ok(await page.locator('#mouse-drag-target').evaluate(el => el.offsetLeft) > dragBefore + 20);
  await pose('move');
  report.checks.push('pinch-release clicks practice target and held pinch moves draggable object');

  await pose('right');
  assert.equal(await page.locator('#mouse-context-menu').isVisible(),true);
  await pose('move'); await pose('scroll');
  await page.evaluate(() => { window.mouseTest.y += .075; }); await page.waitForTimeout(350);
  assert.ok(await page.locator('#mouse-scroll-area').evaluate(el => el.scrollTop) > 0);
  report.checks.push('thumb-middle opens context menu; thumb-ring + vertical movement scrolls');

  await page.locator('#mouse-target').selectOption('desktop');
  assert.equal(await page.locator('#mouse-enabled').isChecked(),false);
  await page.locator('#mouse-enabled').check();
  await pose('move',{x:0,y:0}); await pose('left');
  await pose('left',{x:-.025});
  assert.ok(await page.evaluate(() => window.mouseTest.batches.flat().some(e => e.type === 'button' && e.button === 'left' && e.down)));
  assert.ok(await page.evaluate(() => window.mouseTest.batches.flat().some(e => e.type === 'move')));
  await pose('left',{lost:true});
  assert.ok(await page.evaluate(() => window.mouseTest.batches.flat().some(e => e.type === 'button' && e.button === 'left' && !e.down)));
  report.checks.push('desktop stub receives left-down, drag delta, and release when tracking is lost');
  await pose('move',{lost:false});
  await page.evaluate(() => { window.mouseTest.batches.length = 0; });
  await pose('right'); await page.waitForTimeout(500);
  assert.equal(await page.evaluate(() => window.mouseTest.batches.flat().filter(e => e.type === 'button' && e.button === 'right' && e.down).length),1);
  report.checks.push('held right pinch clicks only once');
  await page.evaluate(() => window.mouseTest.emergencyStop());
  assert.equal(await page.locator('#mouse-enabled').isChecked(),false);
  await page.locator('#mouse-enabled').check(); await pose('move');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#mouse-enabled').isChecked(),false);
  report.checks.push('native stop notification and Esc disarm UI');
  await page.locator('#mouse-target').selectOption('practice');
  await page.locator('#mouse-enabled').check(); await pose('move');
  await page.locator('#mode-media').click();
  assert.equal(await page.locator('#mouse-enabled').isChecked(),false);
  assert.equal(await page.locator('#media-panel').isVisible(),true);
  await page.locator('#mode-mouse').click();
  await page.locator('#mouse-enabled').check(); await pose('move');
  await page.evaluate(() => { window.mouseTest.stalled = true; });
  await page.waitForFunction(() => !document.querySelector('#mouse-enabled').checked);
  assert.match(await page.locator('#mouse-status').textContent(),/응답이 멈춰/);
  report.checks.push('stalled inference disarms without a fresh frame');
  await page.locator('#camera-stop').click();
  assert.equal(await page.locator('#mouse-enabled').isDisabled(),true);
  report.checks.push('mode switch and camera stop release/disarm');
  await fs.mkdir(path.join(root,'test-results'),{recursive:true});
  await page.screenshot({path:path.join(root,'test-results','air-mouse-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.join(root,'test-results','air-mouse-mobile.png'),fullPage:true});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true,'mobile must not overflow');
  assert.deepEqual(errors,[]);
  report.checks.push('responsive layout and no JavaScript errors');
  await fs.writeFile(path.join(root,'test-results','air-mouse-browser.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
} catch (error) {
  console.error(await page.evaluate(() => ({camera:document.querySelector('#camera-message')?.textContent, mouse:document.querySelector('#mouse-status')?.textContent, ready:document.querySelector('#camera-badge')?.textContent})),errors);
  throw error;
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
