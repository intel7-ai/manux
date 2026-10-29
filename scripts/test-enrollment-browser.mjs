import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL, fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {createServer} from '../server.js';
import {extractOrientation, orientationFeedback, VIEWS} from '../src/orientation.js';
import {getHandQuality, extractFeatures, POSES} from '../src/personalization.js';

// Synthetic geometry integration. Reference MediaPipe landmarks are rotated in
// 3D, then sent through the unmodified orientation/enrollment/model/gate/UI.
// The Worker and camera are replaced ONLY in this disposable test context.
// This does not measure MediaPipe tracking, webcam accuracy, or supported distance.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const playwrightPath = process.env.PLAYWRIGHT_MODULE || path.join(process.env.USERPROFILE,
  '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs');
const {chromium} = await import(pathToFileURL(playwrightPath));
const reference = JSON.parse(await fs.readFile(path.join(root,'tests/fixtures/personalization-reference.json'),'utf8'));
const width = 1920, height = 1080;
const dot = (a,b) => a.reduce((sum,n,i) => sum+n*b[i],0);
const cross = (a,b) => [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const subtract = (a,b) => a.map((n,i) => n-b[i]);
const unit = a => {const size=Math.hypot(...a);return a.map(n=>n/size);};
function canonicalPoints(observation) {
  const landmarks=observation.rows.find(row=>row.split==='training').landmarks;
  const points=landmarks.map(p=>[p.x*observation.width,p.y*observation.height,p.z*observation.width]);
  const distal=unit(subtract(points[9],points[0]));
  const across=subtract(points[5],points[17]);
  const lateral=unit(across.map((n,i)=>n-dot(across,distal)*distal[i]));
  const normal=unit(cross(lateral,distal));
  return points.map(p=>{const d=subtract(p,points[0]);return [dot(d,lateral),-dot(d,distal),dot(d,normal)];});
}
function rotateAndNormalize(points, xDeg=0, yDeg=0, zDeg=0) {
  const rx=xDeg*Math.PI/180,ry=yDeg*Math.PI/180,rz=zDeg*Math.PI/180;
  const transformed=points.map(([x,y,z])=>{
    const a=[x,y*Math.cos(rx)-z*Math.sin(rx),y*Math.sin(rx)+z*Math.cos(rx)];
    const b=[a[0]*Math.cos(ry)+a[2]*Math.sin(ry),a[1],-a[0]*Math.sin(ry)+a[2]*Math.cos(ry)];
    return [b[0]*Math.cos(rz)-b[1]*Math.sin(rz),b[0]*Math.sin(rz)+b[1]*Math.cos(rz),b[2]];
  });
  const bounds=[0,1].map(axis=>[Math.min(...transformed.map(p=>p[axis])),Math.max(...transformed.map(p=>p[axis]))]);
  const scale=440/Math.max(bounds[0][1]-bounds[0][0],bounds[1][1]-bounds[1][0]);
  return transformed.map(p=>({x:(p[0]-(bounds[0][0]+bounds[0][1])/2)*scale/width+.5,
    y:(p[1]-(bounds[1][0]+bounds[1][1])/2)*scale/height+.5,z:p[2]*scale/width}));
}
const geometry = {}, fixtureSummary = {};
for(const observation of reference.observations) {
  const points=canonicalPoints(observation),front=rotateAndNormalize(points);
  const baseline=extractOrientation(front,{width,height,mirrored:true});
  assert.equal(baseline.valid,true);
  geometry[observation.pose]={front};fixtureSummary[observation.pose]={baseline,views:{}};
  for(const view of VIEWS) {
    let candidate=view==='front'?front:null;
    for(const x of [-30,0,30]) for(const y of [-30,0,30]) {
      if(candidate)continue;
      const landmarks=rotateAndNormalize(points,x,y),orientation=extractOrientation(landmarks,{width,height,mirrored:true});
      if(orientationFeedback(orientation,baseline,view).matched)candidate=landmarks;
    }
    assert.ok(candidate,`No raw 3D rotation fixture for ${observation.pose}/${view}`);
    assert.equal(getHandQuality(candidate,{width,height}).ok,true);
    geometry[observation.pose][view]=candidate;
    fixtureSummary[observation.pose].views[view]=extractOrientation(candidate,{width,height,mirrored:true});
    const a=extractFeatures(front,{width,height}),b=extractFeatures(candidate,{width,height});
    assert.ok(a.every((n,i)=>Math.abs(n-b[i])<1e-8),'Rigid rotations preserve pose geometry');
  }
}
if(process.argv.includes('--fixtures-only')) {console.log(JSON.stringify(fixtureSummary,null,2));process.exit(0);}
const server = createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base=`http://127.0.0.1:${server.address().port}`;
const report={purpose:'Synthetic 3D landmark and canvas-camera integration of real orientation/enrollment/personalization/gate/UI code. Worker inference is simulated; not webcam accuracy.',
  startedAt:new Date().toISOString(),input:{width,height,frameRate:30,geometrySource:'Recorded official-photo MediaPipe landmarks with rigid 3D rotations'},
  environments:[],actions:[],errors:[]};
let browser,context,page;
const readState=async()=>page.evaluate(()=>({
  stage:document.querySelector('#enroll-stage').textContent,
  pose:document.querySelector('#enrollment').dataset.pose,
  view:document.querySelector('#enrollment').dataset.view,
  phase:document.querySelector('#enrollment').dataset.phase,
  sampleCount:Number(document.querySelector('#enrollment').dataset.sampleCount),
  samples:document.querySelector('#enroll-samples').textContent,
  quality:document.querySelector('#enroll-quality').textContent,
  retry:!document.querySelector('#enroll-retry').hidden,enrolling:document.querySelector('#enrollment').open,
  profileMessage:document.querySelector('#profile-message').textContent,
  recognition:document.querySelector('#recognition-label').textContent,
  cameraMessage:document.querySelector('#camera-message').textContent,
  controlsEnabled:document.querySelector('#gesture-enabled').checked,
  cameraTime:document.querySelector('#camera').currentTime,
  profiles:JSON.parse(localStorage.getItem('manux.profiles.v1')||'[]')
}));
try {
  browser=await chromium.launch({channel:'chrome',headless:true,args:['--autoplay-policy=no-user-gesture-required']});
  context=await browser.newContext({viewport:{width:1366,height:900}});
  await context.addInitScript(({geometry,width,height})=>{
    const NativeWorker=window.Worker;
    const state={pose:'None',view:'front',scale:1,auto:false,forcedView:null,motion:'none',requests:[],workers:0};
    const connections=[[0,1],[1,2],[2,3],[3,4],[0,5],[5,6],[6,7],[7,8],[5,9],[9,10],[10,11],[11,12],[9,13],[13,14],[14,15],[15,16],[13,17],[0,17],[17,18],[18,19],[19,20]];
    const observed=()=>{
      const dialog=document.querySelector('#enrollment');
      if(state.auto&&dialog?.open){state.pose=dialog.dataset.pose||state.pose;state.view=dialog.dataset.view||'front';}
      const view=state.forcedView||state.view;
      const source=geometry[state.pose]?.[view]||geometry.None.front;
      const angle=state.motion==='roll'?Math.sin(performance.now()/600)*.9:0;
      const dx=state.motion==='translate'?Math.sin(performance.now()/600)*.09:0;
      const dy=state.motion==='translate'?Math.cos(performance.now()/600)*.05:0;
      return source.map(p=>{const x=(p.x-.5)*width*state.scale,y=(p.y-.5)*height*state.scale;
        return {x:(x*Math.cos(angle)-y*Math.sin(angle))/width+.5+dx,
          y:(x*Math.sin(angle)+y*Math.cos(angle))/height+.5+dy,z:p.z*state.scale};});
    };
    const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
    const ctx=canvas.getContext('2d');
    const draw=()=>{const hand=observed();ctx.fillStyle='#102937';ctx.fillRect(0,0,width,height);
      ctx.strokeStyle='#49c4a6';ctx.lineWidth=8;
      for(const[a,b]of connections){ctx.beginPath();ctx.moveTo(hand[a].x*width,hand[a].y*height);ctx.lineTo(hand[b].x*width,hand[b].y*height);ctx.stroke();}
      ctx.fillStyle='#fff';for(const p of hand){ctx.beginPath();ctx.arc(p.x*width,p.y*height,8,0,Math.PI*2);ctx.fill();}
      // Text is mirrored because the product preview mirrors the webcam.
      ctx.save();ctx.translate(width,0);ctx.scale(-1,1);ctx.font='30px sans-serif';ctx.fillText('SYNTHETIC LANDMARK TEST',40,60);ctx.restore();};
    setInterval(draw,1000/30);
    Object.defineProperty(navigator.mediaDevices,'getUserMedia',{configurable:true,value:async constraints=>{state.requests.push(constraints);draw();return canvas.captureStream(30);}});
    // Preserve real transferable ImageBitmap ownership and worker scheduling.
    // Only the inference result is simulated, using raw transformed landmarks.
    const workerSource = `self.onmessage=({data})=>{
      if(data.type==='init'){self.postMessage({id:data.id,ok:true,delegate:'SYNTHETIC_GEOMETRY'});return;}
      data.bitmap?.close();const pose=data.syntheticPose;
      self.postMessage({id:data.id,ok:true,inferenceMs:1,result:{
        landmarks:[data.syntheticLandmarks],gestures:[[{categoryName:pose==='None'?'Pointing_Up':pose,score:.99}]],
        handedness:[[{categoryName:'Right',score:.99}]],handednesses:[[{categoryName:'Right',score:.99}]]
      }});
    };`;
    const workerUrl=URL.createObjectURL(new Blob([workerSource],{type:'text/javascript'}));
    class GeometryWorker {
      constructor(url){
        if(!String(url).endsWith('/src/vision-worker.js'))return new NativeWorker(url);
        state.workers++;
        const worker=new NativeWorker(workerUrl),post=worker.postMessage.bind(worker);
        worker.postMessage=(data,transfer)=>{
          const syntheticLandmarks=data.type==='frame'?observed():undefined;
          post({...data,syntheticLandmarks,syntheticPose:state.pose},transfer);
        };
        return worker;
      }
    }
    window.Worker=GeometryWorker;
    window.__enrollmentTest={
      setPose:pose=>{state.pose=pose;state.view='front';state.motion='none';state.forcedView=null;},
      setScale:scale=>{state.scale=scale;},followInstructions:value=>{state.auto=value;},
      forceView:view=>{state.forcedView=view;},motion:value=>{state.motion=value;},
      get requests(){return state.requests;},get workers(){return state.workers;}
    };
  },{geometry,width,height});
  page=await context.newPage();page.on('pageerror',error=>report.errors.push(error.message));
  await page.goto(base);
  assert.equal(await page.locator('#repeat-enabled').isChecked(),false);
  await page.locator('#demo-load').click();await page.waitForFunction(()=>document.querySelector('#player').duration>80);
  await page.locator('#profile-name').fill('Synthetic geometry QA');await page.locator('#profile-add').click();
  await page.locator('#camera-start').click();await page.waitForFunction(()=>document.querySelector('#camera-badge').textContent==='인식 중',{}, {timeout:15000});
  assert.equal(await page.locator('#camera').evaluate(v=>v.videoWidth),width);
  assert.equal(await page.locator('#camera').evaluate(v=>v.videoHeight),height);
  report.cameraRequests=await page.evaluate(()=>window.__enrollmentTest.requests);
  assert.equal(report.cameraRequests[0].video.width.ideal,width);assert.equal(report.cameraRequests[0].audio,false);

  await page.evaluate(()=>{window.__enrollmentTest.followInstructions(true);window.__enrollmentTest.forceView('front');});
  await page.locator('#enroll-start').click();const start=Date.now();
  await page.waitForFunction(()=>{const d=document.querySelector('#enrollment');return d.dataset.view==='left'&&d.dataset.phase==='capture';},{},{timeout:20000});
  report.negativeControls=[];
  for(const motion of ['none','translate','roll']){
    await page.evaluate(m=>window.__enrollmentTest.motion(m),motion);await page.waitForTimeout(2200);
    const state=await readState();assert.equal(state.view,'left');assert.equal(state.phase,'capture');assert.equal(state.sampleCount,0,`${motion} cannot fill a left-angle bucket`);
    report.negativeControls.push(`${motion}: no left-angle credit`);
  }
  await page.screenshot({path:path.join(root,'test-results/enrollment-angle-negative.png'),fullPage:true});
  await page.evaluate(()=>{window.__enrollmentTest.motion('none');window.__enrollmentTest.forceView(null);});
  let lastStep='',verificationShot=false;
  for(;;){
    const state=await readState();assert.equal(state.controlsEnabled,false);
    if(state.retry)throw new Error(`Enrollment failed: ${JSON.stringify({...state,profiles:undefined})}`);
    if(state.profiles[0]?.environments?.desk?.verified)break;
    assert.ok(state.enrolling,'Enrollment exited before saving');assert.ok(Date.now()-start<300000,'Enrollment exceeded five minutes');
    const step=`${state.stage} ${state.pose}/${state.view}`;
    if(step!==lastStep){console.log(`desk ${Math.round((Date.now()-start)/1000)}s: ${step}`);lastStep=step;}
    if(!verificationShot&&state.phase==='verify'){
      await page.screenshot({path:path.join(root,'test-results/enrollment-angle-verification.png'),fullPage:true});verificationShot=true;
    }
    await page.waitForTimeout(250);
  }
  const saved=(await readState()).profiles[0].environments.desk;
  assert.equal(saved.model.version,2);assert.equal(saved.model.usable,true);
  for(const pose of POSES){
    assert.ok(saved.samples[pose].length>=60);
    for(const view of VIEWS){
      assert.ok(saved.model.classes[pose].views[view].sampleCount>=12);
      assert.equal(saved.verification[pose].views[view].passed,true);
      assert.ok(saved.verification[pose].views[view].matched>=3);
      assert.ok(saved.verification[pose].views[view].durationMs>=300);
    }
  }
  report.environments.push({environment:'desk',elapsedMs:Date.now()-start,resolution:saved.resolution,
    viewSampleCounts:Object.fromEntries(POSES.map(pose=>[pose,Object.fromEntries(VIEWS.map(view=>[view,saved.model.classes[pose].views[view].sampleCount]))])),
    verification:saved.verification});
  console.log(`desk: 25 views captured and separately verified in ${Math.round((Date.now()-start)/1000)} seconds`);
  assert.equal(await page.locator('#camera-preview-home #camera').count(),1);
  assert.equal(await page.locator('#camera').evaluate(v=>v.paused),false);

  // The second environment receives independent temporary capture state. A
  // partial sofa registration must not overwrite the completed desk profile.
  const deskBefore=JSON.stringify(saved);
  await page.locator('#environment').selectOption('sofa');
  assert.equal(await page.locator('#personal-enabled').isChecked(),false);
  await page.evaluate(()=>window.__enrollmentTest.setScale(.6));
  await page.locator('#enroll-start').click();
  await page.waitForFunction(()=>{const d=document.querySelector('#enrollment');return d.dataset.phase==='capture'&&d.dataset.view==='front'&&Number(d.dataset.sampleCount)>0;},{},{timeout:15000});
  await page.locator('#enroll-cancel').click();
  assert.equal(JSON.stringify((await readState()).profiles[0].environments.desk),deskBefore);
  assert.equal((await readState()).profiles[0].environments.sofa,undefined);
  report.sofaIsolation='Partial smaller-input registration cancelled; desk data preserved; sofa remains unregistered';
  await page.locator('#environment').selectOption('desk');
  await page.evaluate(()=>window.__enrollmentTest.setScale(1));
  await page.evaluate(() => window.__enrollmentTest.followInstructions(false));
  const setPose = async (pose, label) => {
    await page.evaluate(pose => window.__enrollmentTest.setPose(pose), pose);
    await page.waitForFunction(label => document.querySelector('#recognition-label').textContent.startsWith(label), label, {timeout: 10000});
  };
  const position = () => page.locator('#player').evaluate(video => video.currentTime);
  const eventCount = () => page.locator('#event-log li:not(.empty-log)').count();
  await setPose('None', '중립');
  await page.locator('#clear-log').click();
  await page.locator('#player').evaluate(video => {video.pause(); video.currentTime = 30;});
  await page.locator('#gesture-enabled').check();
  await page.waitForTimeout(800);
  assert.equal(await eventCount(), 0);
  await setPose('Victory', 'V 모양');
  await page.waitForFunction(() => Math.abs(document.querySelector('#player').currentTime - 40) < .05);
  await page.waitForTimeout(1800);
  assert.equal(await position(), 40); assert.equal(await eventCount(), 1);
  report.actions.push('Victory: exactly one +10 seconds while held for 1.8 seconds');
  await setPose('None', '중립'); await page.waitForTimeout(800);
  await setPose('Victory', 'V 모양');
  await page.waitForFunction(() => Math.abs(document.querySelector('#player').currentTime - 50) < .05);
  await page.waitForTimeout(1200);
  assert.equal(await position(), 50); assert.equal(await eventCount(), 2);
  report.actions.push('Neutral then Victory: one additional +10 seconds');
  await setPose('Closed_Fist', '주먹');
  await page.waitForFunction(() => Math.abs(document.querySelector('#player').currentTime - 40) < .05);
  await page.waitForTimeout(1200);
  assert.equal(await position(), 40); assert.equal(await eventCount(), 3);
  report.actions.push('Closed_Fist: exactly one -10 seconds');
  await setPose('Thumb_Up', '엄지척');
  await page.waitForFunction(() => !document.querySelector('#player').paused);
  await setPose('Open_Palm', '손바닥');
  await page.waitForFunction(() => document.querySelector('#player').paused);
  assert.equal(await eventCount(), 5);
  report.actions.push('Thumb_Up starts playback; Open_Palm pauses playback');
  assert.ok((await page.locator('#event-log').innerText()).includes('개인 손동작'));

  // The explicit repeat preference must disarm the gate when it changes.
  // Fresh matching frames then repeat only seeks, at the normal 1-second interval.
  await setPose('None', '중립'); await page.waitForTimeout(800);
  await page.locator('#repeat-enabled').check();
  assert.equal(await page.locator('#gesture-enabled').isChecked(), false);
  await page.locator('#clear-log').click();
  await page.locator('#player').evaluate(video => {video.pause(); video.currentTime = 10;});
  await page.locator('#gesture-enabled').check(); await page.waitForTimeout(800);
  await setPose('Victory', 'V 모양');
  await page.waitForFunction(() => Math.abs(document.querySelector('#player').currentTime - 20) < .05);
  await page.waitForTimeout(2200);
  const repeatedForward = await position();
  assert.ok(repeatedForward >= 40 && repeatedForward <= 50, `Expected 3–4 ten-second seeks; position=${repeatedForward}`);
  assert.equal(repeatedForward % 10, 0);
  assert.equal(await eventCount(), (repeatedForward - 10) / 10);
  await setPose('None', '중립'); await page.waitForTimeout(800);
  const neutralPosition = await position(); await page.waitForTimeout(1200);
  assert.equal(await position(), neutralPosition, 'Neutral pose stops repeated seeks');
  await page.locator('#clear-log').click();
  await page.locator('#player').evaluate(video => {video.currentTime = 60;});
  await setPose('Closed_Fist', '주먹');
  await page.waitForFunction(() => Math.abs(document.querySelector('#player').currentTime - 50) < .05);
  await page.waitForTimeout(2200);
  const repeatedBackward = await position();
  assert.ok(repeatedBackward >= 20 && repeatedBackward <= 30, `Expected 3–4 reverse seeks; position=${repeatedBackward}`);
  assert.equal(repeatedBackward % 10, 0);
  assert.equal(await eventCount(), (60 - repeatedBackward) / 10);
  report.repeat = {forwardSeekCount: (repeatedForward - 10) / 10, backwardSeekCount: (60 - repeatedBackward) / 10,
    postFirstSeekObservationMs: 2200, neutralStops: true, preferenceChangeDisarms: true};
  await setPose('None', '중립'); await page.waitForTimeout(800);
  await page.locator('#clear-log').click();
  await setPose('Thumb_Up', '엄지척');
  await page.waitForFunction(() => !document.querySelector('#player').paused);
  await page.waitForTimeout(1500); assert.equal(await eventCount(), 1, 'Play remains one-shot in repeat mode');
  await setPose('Open_Palm', '손바닥');
  await page.waitForFunction(() => document.querySelector('#player').paused);
  await page.waitForTimeout(1500); assert.equal(await eventCount(), 2, 'Pause remains one-shot in repeat mode');
  await page.locator('#repeat-enabled').uncheck();
  assert.equal(await page.locator('#gesture-enabled').isChecked(), false);
  report.actions.push('Repeat enabled: forward and backward repeat in exact 10-second steps; neutral stops; play/pause remain one-shot; changing preference disarms control');

  const savedBeforeReload = (await readState()).profiles;
  await page.reload();
  await page.waitForFunction(() => document.querySelector('#profile-status').textContent.includes('책상 등록됨'));
  assert.deepEqual((await readState()).profiles, savedBeforeReload);
  await page.locator('#environment').selectOption('sofa');
  assert.equal(await page.locator('#personal-enabled').isChecked(), false);
  assert.equal(await page.locator('#personal-enabled').isDisabled(), true);
  await page.locator('#environment').selectOption('desk');
  assert.equal(await page.locator('#personal-enabled').isChecked(), true);
  assert.match(await page.locator('#profile-mode').innerText(), /책상 앞 · 개인 등록/);
  report.persistence = 'Completed desk v2 model survives reload; unregistered sofa does not inherit desk registration';
  assert.deepEqual(report.errors, []);
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.failure = error.message;
  if (page) {try {const state = await readState(); report.lastState = {...state, profiles: state.profiles.map(p => ({id:p.id, name:p.name, environments:Object.keys(p.environments)}))};} catch {}}
  process.exitCode = 1;
} finally {
  report.finishedAt = new Date().toISOString();
  await fs.mkdir(path.join(root, 'test-results'), {recursive: true});
  if (page) {try {await page.screenshot({path: path.join(root, 'test-results/enrollment-browser.png'), fullPage: true});} catch {}}
  await fs.writeFile(path.join(root, 'test-results/enrollment-browser-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  if (context) await context.close(); if (browser) await browser.close();
  await new Promise(resolve => server.close(resolve));
}
