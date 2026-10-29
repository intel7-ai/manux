import test from 'node:test';
import assert from 'node:assert/strict';
import {EnrollmentController} from '../src/enrollment.js';
import {POSES, FEATURE_COUNT} from '../src/personalization.js';
import {VIEWS} from '../src/orientation.js';

const vector=n=>Array(FEATURE_COUNT).fill(n),quality={ok:true};
const angle=(yaw=0,pitch=0)=>({valid:true,yaw,pitch,roll:0});
const options={prepareMs:100,viewPrepareMs:100,calibrationMs:300,calibrationSamples:3,captureMs:600,sampleIntervalMs:50,minSamplesPerView:12,maxGapMs:150,captureTimeoutMs:3000,verifySamples:3,verifyHoldMs:300,verifyMaxGapMs:150,verifyTimeoutMs:2000};
const create=extra=>new EnrollmentController({...options,...extra});
const relative={front:[0,0],left:[-25,0],right:[25,0],up:[0,25],down:[0,-25]};
function observe(controller,now,extra={}){
 const state=controller.snapshot(),base=state.baseline||{yaw:0,pitch:0},[x,y]=relative[state.targetView];
 return controller.update({now,features:vector((state.poseIndex+1)/10),quality,orientation:angle(base.yaw+x,base.pitch+y),handedness:'Right',...extra});
}
function calibrate(controller,now,{yaw=0,pitch=0,handedness='Right'}={}){
 if(controller.snapshot().phase==='prepare'){now+=options.prepareMs;controller.update({now});}
 assert.equal(controller.snapshot().phase,'calibrate');
 for(let i=0;i<=6;i++){observe(controller,now,{orientation:angle(yaw,pitch),handedness});if(i<6)now+=50;}
 assert.equal(controller.snapshot().phase,'capture');return now;
}
function captureView(controller,now){
 if(controller.snapshot().phase==='prepare'){now+=options.viewPrepareMs;controller.update({now});}
 const previous=controller.snapshot().step;
 for(let i=0;i<13;i++){now+=50;observe(controller,now);}
 assert.ok(controller.snapshot().step!==previous||controller.snapshot().phase==='captured');return now;
}
function enroll(controller,now=0){
 controller.start({now,environment:'desk'});
 for(let p=0;p<POSES.length;p++){
  now=calibrate(controller,now,{yaw:p*2,pitch:p});
  for(const view of VIEWS){assert.equal(controller.snapshot().targetView,view);now=captureView(controller,now);}
 }
 assert.equal(controller.snapshot().phase,'captured');return now;
}
function verifyView(controller,now){
 const state=controller.snapshot();
 assert.equal(state.phase,'verify-prepare');now+=state.viewIndex===0?options.prepareMs:options.viewPrepareMs;
 controller.update({now});
 for(const interval of [0,150,150]){now+=interval;observe(controller,now,{match:{accepted:true,gesture:state.pose},features:vector(.99)});}
 return now;
}

test('all five views of every pose require measured fresh samples and valid observed time',()=>{
 const controller=create();enroll(controller);const state=controller.snapshot();
 assert.equal(state.completedViews,25);assert.equal(state.totalProgress,1);assert.equal(state.active,false);
 for(const pose of POSES){
  assert.equal(state.samplesByPose[pose].length,65);
  for(const view of VIEWS)assert.equal(state.sampleGroupsByPose[pose][view].length,13);
  assert.equal(state.orientationBaselines[pose].handedness,'Right');
 }
});
test('static front never fills left and a timeout preserves completed front coverage',()=>{
 const controller=create();controller.start({now:0,environment:'desk'});let now=calibrate(controller,0);now=captureView(controller,now);
 now+=100;controller.update({now});const started=now;
 for(let i=0;i<15;i++){now+=100;observe(controller,now,{orientation:angle()});}
 let state=controller.snapshot();assert.equal(state.targetView,'left');assert.equal(state.views.front.complete,true);assert.equal(state.views.left.count,0);assert.equal(state.progress,0);
 state=controller.update({now:started+3000});assert.equal(state.phase,'failed');assert.equal(state.reason,'angle-timeout');assert.equal(state.targetView,'left');
 assert.equal(state.views.front.complete,true);assert.equal(controller.getSamples().Open_Palm.length,13);
});
test('timer ticks do not calibrate, collect samples, or manufacture angle coverage',()=>{
 const controller=create();controller.start({now:0,environment:'desk'});controller.update({now:100});controller.update({now:1000});
 assert.equal(controller.snapshot().phase,'calibrate');assert.equal(controller.snapshot().calibration.count,0);assert.equal(controller.snapshot().progress,0);
 let now=calibrate(controller,1000);controller.update({now:now+1000});
 assert.equal(controller.snapshot().phase,'capture');assert.equal(controller.snapshot().sampleCount,0);assert.equal(controller.snapshot().progress,0);
 assert.equal(controller.update({now:now+3000}).phase,'failed');
});
test('calibration rejects edge-on starts, resets unstable or missing observations and locks the chosen hand',()=>{
 const controller=create();controller.start({now:0,environment:'desk'});
 let state=observe(controller,100,{orientation:angle(46,0)});assert.equal(state.reason,'face-camera');assert.equal(state.calibration.count,0);
 observe(controller,150);observe(controller,200);state=observe(controller,250,{orientation:angle(8,0)});assert.equal(state.calibration.count,1);assert.equal(state.reason,'front-moving');
 state=observe(controller,300,{quality:{ok:false,reason:'hand-too-small'}});assert.equal(state.calibration.count,0);
 for(let i=0;i<=6;i++)observe(controller,350+i*50,{orientation:angle(8,0),handedness:'Left'});
 assert.equal(controller.snapshot().phase,'capture');assert.equal(controller.snapshot().baseline.handedness,'Left');
 state=observe(controller,700,{orientation:angle(8,0),handedness:'Right'});assert.equal(state.reason,'different-hand');assert.equal(state.sampleCount,0);
});
test('invalid quality, another hand and wrong direction interrupt duration without adding samples',()=>{
 const controller=create();controller.start({now:0,environment:'desk'});let now=calibrate(controller,0);
 for(let i=0;i<6;i++){now+=50;observe(controller,now);}
 const before=controller.snapshot().sampleCount;
 for(const extra of [{quality:{ok:false,reason:'hand-too-small'}},{handedness:'Left'},{orientation:angle(-25,0)}]){
  now+=50;const state=observe(controller,now,extra);assert.equal(state.sampleCount,before);assert.equal(state.progress,0);
 }
 for(let i=0;i<6;i++){now+=50;observe(controller,now);}
 assert.equal(controller.snapshot().phase,'capture');assert.equal(controller.snapshot().sampleCount,12);assert.ok(controller.snapshot().progress<1);
 for(let i=0;i<7;i++){now+=50;observe(controller,now);}
 assert.equal(controller.snapshot().targetView,'left');
});
test('sample timestamps are independent, spaced, finite and copy-isolated',()=>{
 const controller=create();controller.start({now:0,environment:'desk'});let now=calibrate(controller,0);
 const original=vector(.2);now+=50;observe(controller,now,{features:original});original[0]=.9;
 observe(controller,now);observe(controller,now+1);assert.equal(controller.snapshot().sampleCount,1);
 const missing=vector(.2);delete missing[7];
 for(const features of [Array(FEATURE_COUNT),missing,vector(NaN)]){now+=50;assert.equal(observe(controller,now,{features}).reason,'invalid-features');}
 assert.equal(controller.snapshot().sampleCount,1);
 const saved=controller.getSamples();assert.equal(saved.Open_Palm[0][0],.2);saved.Open_Palm[0][0]=.8;assert.equal(controller.getSamples().Open_Palm[0][0],.2);
});
test('large unobserved intervals cannot count toward capture duration',()=>{
 const controller=create();controller.start({now:0,environment:'desk'});let now=calibrate(controller,0);
 for(let i=0;i<6;i++){now+=50;observe(controller,now);}
 now+=500;let state=observe(controller,now);assert.equal(state.views.front.durationMs,0);assert.equal(state.sampleCount,7);
 for(let i=0;i<6;i++){now+=50;state=observe(controller,now);}
 assert.equal(state.sampleCount,13);assert.equal(state.phase,'capture');assert.equal(state.views.front.durationMs,300);
});
test('retry clears only the failed view and retains earlier views and its front reference',()=>{
 const controller=create();controller.start({now:0,environment:'desk'});let now=calibrate(controller,0);now=captureView(controller,now);
 now+=100;controller.update({now});const started=now;
 for(let i=0;i<6;i++){now+=50;observe(controller,now);}
 controller.update({now:started+3000});const base=controller.snapshot().baseline;
 controller.retry({now:started+3100});assert.equal(controller.snapshot().targetView,'left');assert.equal(controller.snapshot().views.left.count,0);
 assert.equal(controller.snapshot().views.front.complete,true);assert.deepEqual(controller.snapshot().baseline,base);
 now=captureView(controller,started+3100);assert.equal(controller.snapshot().targetView,'right');
});
test('bounded storage prevents a shaky prolonged view from growing without limit',()=>{
 const controller=create({captureTimeoutMs:10000,maxSamplesPerView:24});controller.start({now:0,environment:'desk'});let now=calibrate(controller,0);
 for(let i=0;i<80;i++){now+=50;observe(controller,now,i%3===2?{quality:{ok:false}}:{});}
 assert.equal(controller.snapshot().phase,'capture');assert.equal(controller.snapshot().sampleCount,24);assert.equal(controller.snapshot().views.front.complete,false);
});
test('fresh verification checks every trained view and never reuses or adds training samples',()=>{
 const controller=create();let now=enroll(controller);const original=controller.getSamples();controller.startVerification({now});
 for(const pose of POSES)for(const view of VIEWS){assert.equal(controller.snapshot().pose,pose);assert.equal(controller.snapshot().targetView,view);now=verifyView(controller,now);}
 const state=controller.snapshot();assert.equal(state.phase,'verified');assert.equal(state.completedViews,25);assert.deepEqual(controller.getSamples(),original);
 for(const pose of POSES){assert.equal(state.verification[pose].passed,true);for(const view of VIEWS)assert.equal(state.verification[pose].views[view].passed,true);}
});
test('verification requires both the registered pose and target view; mismatch and stale gaps reset streaks',()=>{
 const controller=create();let now=enroll(controller);controller.startVerification({now});now+=100;controller.update({now});
 observe(controller,now,{match:{accepted:true,gesture:'Open_Palm'}});
 observe(controller,now+150,{orientation:angle(-25,0),match:{accepted:true,gesture:'Open_Palm'}});assert.equal(controller.snapshot().sampleCount,0);
 observe(controller,now+300,{match:{accepted:true,gesture:'Thumb_Up'}});assert.equal(controller.snapshot().reason,'verification-mismatch');
 observe(controller,now+450,{match:{accepted:true,gesture:'Open_Palm'}});observe(controller,now+700,{match:{accepted:true,gesture:'Open_Palm'}});
 assert.equal(controller.snapshot().sampleCount,1);
 observe(controller,now+850,{match:{accepted:true,gesture:'Open_Palm'}});observe(controller,now+1000,{match:{accepted:true,gesture:'Open_Palm'}});
 assert.equal(controller.snapshot().targetView,'left');
});
test('verification ticks and duplicate observations cannot pass and timeout retries only that view',()=>{
 const controller=create();let now=enroll(controller);controller.startVerification({now});now+=100;controller.update({now});
 const input={match:{accepted:true,gesture:'Open_Palm'}};observe(controller,now,input);observe(controller,now,input);observe(controller,now+150,input);
 controller.update({now:now+300});assert.equal(controller.snapshot().sampleCount,2);assert.equal(controller.snapshot().phase,'verify');
 controller.update({now:now+2000});assert.equal(controller.snapshot().phase,'failed');assert.equal(controller.snapshot().reason,'verification-timeout');
 controller.retry({now:now+2100});assert.equal(controller.snapshot().phase,'verify-prepare');assert.equal(controller.snapshot().targetView,'front');
});
test('full sample groups and orientation baselines are copied only at train and save boundaries',()=>{
 const controller=create();controller.start({now:0,environment:'desk'});assert.equal(controller.snapshot().sampleGroupsByPose,undefined);
 let now=enroll(controller);let state=controller.snapshot();assert.ok(state.sampleGroupsByPose);state.sampleGroupsByPose.Open_Palm.front[0][0]=.99;state.orientationBaselines.Open_Palm.yaw=60;
 assert.equal(controller.getSamples().Open_Palm[0][0],.1);assert.equal(controller.snapshot().orientationBaselines.Open_Palm.yaw,0);
 controller.startVerification({now});assert.equal(controller.snapshot().samplesByPose,undefined);assert.equal(controller.snapshot().orientationBaselines,undefined);
 for(let i=0;i<25;i++)now=verifyView(controller,now);
 assert.deepEqual(controller.snapshot().samplesByPose,controller.getSamples());
});
test('cancel clears all collected views and a new environment starts independently',()=>{
 const controller=create();controller.start({now:0,environment:'desk'});let now=calibrate(controller,0);now=captureView(controller,now);
 assert.equal(controller.getSamples().Open_Palm.length,13);controller.cancel();assert.ok(Object.values(controller.getSamples()).every(rows=>rows.length===0));
 controller.start({now:now+100,environment:'sofa'});assert.equal(controller.snapshot().environment,'sofa');assert.equal(controller.snapshot().step,1);assert.equal(controller.snapshot().baseline,null);
});
test('invalid configuration, unknown hand, missing orientation and backwards time fail closed',()=>{
 for(const config of [{minSamplesPerView:8},{maxSamplesPerView:121},{sampleIntervalMs:200,maxGapMs:100},{captureTimeoutMs:500},{verifyTimeoutMs:200},{calibrationSpreadDeg:11}])assert.throws(()=>create(config),/invalid/);
 const controller=create();assert.throws(()=>controller.start({now:0,environment:'else'}),/environment/);controller.start({now:10,environment:'desk'});
 assert.throws(()=>controller.update({now:9}),/monotonic/);assert.throws(()=>controller.update({now:NaN}),/monotonic/);
 assert.throws(()=>controller.retry({now:20}),/failed/);assert.throws(()=>controller.startVerification({now:20}),/capture all/);
 assert.equal(observe(controller,110,{orientation:null}).reason,'orientation-unavailable');assert.equal(observe(controller,160,{handedness:null}).reason,'handedness-unavailable');
});
