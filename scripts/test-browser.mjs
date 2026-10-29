import fs from 'node:fs/promises';import path from 'node:path';import{pathToFileURL,fileURLToPath}from'node:url';import assert from'node:assert/strict';import{createServer}from'../server.js';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const playwrightPath=process.env.PLAYWRIGHT_MODULE||path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs');
const{chromium}=await import(pathToFileURL(playwrightPath));
const server=createServer();await new Promise(r=>server.listen(0,'127.0.0.1',r));const base=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream','--enable-unsafe-swiftshader']});
const context=await browser.newContext({viewport:{width:1440,height:1100},permissions:['camera']});const page=await context.newPage();
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.route('**/test-assets/*',async route=>{const name=new URL(route.request().url()).pathname.split('/').pop();const p=name==='webm-muxer.mjs'?path.join(root,'node_modules/webm-muxer/build/webm-muxer.mjs'):path.join(root,'tests/fixtures',name);await route.fulfill({body:await fs.readFile(p),contentType:name.endsWith('.mjs')?'text/javascript':'image/jpeg'});});
const report={};
try{
 await page.goto(base);await page.waitForFunction(()=>document.querySelector('#camera-start')&&!document.querySelector('#demo-load').disabled);
 if(process.argv.includes('--make-demo')){
  const bytes=await page.evaluate(async()=>{const{Muxer,ArrayBufferTarget}=await import('/test-assets/webm-muxer.mjs');const canvas=document.createElement('canvas');canvas.width=960;canvas.height=540;const c=canvas.getContext('2d');const muxer=new Muxer({target:new ArrayBufferTarget(),video:{codec:'V_VP8',width:960,height:540,frameRate:2}});let err=null;const encoder=new VideoEncoder({output:(chunk,meta)=>muxer.addVideoChunk(chunk,meta),error:e=>err=e.message});encoder.configure({codec:'vp8',width:960,height:540,bitrate:500000,framerate:2});for(let i=0;i<180;i++){const seconds=i/2;c.fillStyle='#112331';c.fillRect(0,0,960,540);c.fillStyle='#46e5bd';c.font='bold 32px Segoe UI';c.fillText('MANUX',60,76);c.fillStyle='#f5f7f3';c.font='bold 112px Segoe UI';c.fillText(`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(Math.floor(seconds%60)).padStart(2,'0')}`,60,288);c.fillStyle='#9eafa9';c.font='26px Segoe UI';c.fillText('Gesture playback test',64,364);c.fillStyle='#2d454d';c.fillRect(64,430,832,8);c.fillStyle='#46e5bd';c.fillRect(64,430,832*seconds/90,8);c.font='22px Segoe UI';c.fillText('90 seconds / local sample',64,493);const frame=new VideoFrame(canvas,{timestamp:i*500000,duration:500000});encoder.encode(frame,{keyFrame:true});frame.close();if(encoder.encodeQueueSize>8)await new Promise(r=>encoder.addEventListener('dequeue',r,{once:true}));}await encoder.flush();encoder.close();if(err)throw new Error(err);muxer.finalize();return Array.from(new Uint8Array(muxer.target.buffer));});
  await fs.writeFile(path.join(root,'public/media/demo.webm'),new Uint8Array(bytes));report.demoBytes=bytes.length;
 }
 await page.getByRole('button',{name:'90초 테스트 영상 열기'}).click();await page.waitForFunction(()=>Number.isFinite(document.querySelector('#player').duration)&&document.querySelector('#player').duration>80);
 report.duration=await page.locator('#player').evaluate(v=>v.duration);assert.ok(report.duration>=89&&report.duration<=91);
 await page.locator('#player').evaluate(v=>{v.currentTime=30});await page.locator('[data-action=forward]').click();assert.equal(await page.locator('#player').evaluate(v=>v.currentTime),40);await page.locator('[data-action=backward]').click();assert.equal(await page.locator('#player').evaluate(v=>v.currentTime),30);
 await page.locator('[data-action=play]').click();await page.waitForFunction(()=>!document.querySelector('#player').paused);await page.locator('[data-action=pause]').click();assert.equal(await page.locator('#player').evaluate(v=>v.paused),true);
 report.videoControls='passed: distinct play/pause and exact ±10 seconds';
 await page.getByRole('button',{name:'카메라 시작',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#camera-badge').textContent==='인식 중',{},{timeout:60000});
 await page.waitForFunction(()=>/처리 [1-9]/.test(document.querySelector('#camera-stats').textContent));
 report.cameraStats=await page.locator('#camera-stats').textContent();
 report.actualCamera=await page.locator('#camera').evaluate(v=>v.srcObject.getVideoTracks()[0].getSettings());
 assert.equal(report.actualCamera.width,1920);assert.equal(report.actualCamera.height,1080);
 await page.locator('#gesture-enabled').check();
 await page.locator('#hold-time').evaluate(el=>{el.value='350';el.dispatchEvent(new Event('input',{bubbles:true}));});
 assert.equal(await page.locator('#gesture-enabled').isChecked(),false);
 await page.locator('#gesture-enabled').check();await page.locator('#repeat-enabled').check();
 assert.equal(await page.locator('#gesture-enabled').isChecked(),false);
 await page.locator('#repeat-enabled').uncheck();
 await page.locator('#profile-name').fill('브라우저 테스트');await page.locator('#profile-add').click();
 await page.locator('#enroll-start').click();assert.equal(await page.locator('#gesture-enabled').isDisabled(),true);
 await page.locator('#enroll-cancel').click();
 assert.deepEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem('manux.profiles.v1'))[0].environments),{});
 await page.locator('#gesture-enabled').check();await page.keyboard.press('Escape');assert.equal(await page.locator('#gesture-enabled').isChecked(),false);
 await page.getByRole('button',{name:'카메라 끄기',exact:true}).click();assert.equal(await page.locator('#gesture-enabled').isDisabled(),true);report.cameraLifecycle='passed with synthetic camera (not physical webcam)';
 report.model=await page.evaluate(async()=>{const{GestureRecognizer,FilesetResolver}=await import('/vendor/vision_bundle.mjs');const files=await FilesetResolver.forVisionTasks('/vendor/wasm');const rec=await GestureRecognizer.createFromOptions(files,{baseOptions:{modelAssetPath:'/models/gesture_recognizer.task',delegate:'CPU'},runningMode:'IMAGE',numHands:2});const rows=[];for(const [name,expected]of [['fist.jpg','Closed_Fist'],['thumb_up.jpg','Thumb_Up'],['victory.jpg','Victory'],['right_hands.jpg','Open_Palm']]){const image=new Image();image.src='/test-assets/'+name;await image.decode();const result=rec.recognize(image);rows.push({name,expected,gestures:result.gestures.map(g=>({name:g[0].categoryName,score:g[0].score}))});}rec.close();return rows;});
 // Fixed photos validate model wiring, not user accuracy or an acceptance rate.
 // The two-hand palm fixture is correctly labelled but below the 0.70 gate.
 for(const sample of report.model)assert.ok(sample.gestures.some(g=>g.name===sample.expected&&Number.isFinite(g.score)&&g.score>0&&g.score<=1),JSON.stringify(sample));
 report.belowDefaultGate=report.model.filter(sample=>!sample.gestures.some(g=>g.name===sample.expected&&g.score>=.70)).map(s=>s.name);
 await fs.mkdir(path.join(root,'test-results'),{recursive:true});await page.screenshot({path:path.join(root,'test-results/desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(root,'test-results/mobile.png'),fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.deepEqual(errors,[]);report.pageErrors=errors;report.status='passed';await fs.writeFile(path.join(root,'test-results/browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
}catch(error){
 console.error(JSON.stringify({error:error.message,pageErrors:errors,camera:await page.locator('#camera-message').textContent().catch(()=>null),badge:await page.locator('#camera-badge').textContent().catch(()=>null)}));
 throw error;
}finally{await browser.close();await new Promise(r=>server.close(r));}
