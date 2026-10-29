import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {POSES, FEATURE_COUNT, ENROLLMENT_VIEWS, MIN_SAMPLES_PER_VIEW, extractFeatures, getHandQuality, trainEnvironment, classifyPersonal, validateProfile} from '../src/personalization.js';

const hand = [
  [0.48,0.80,0], [0.38,0.72,-.01], [0.30,0.63,-.02], [0.24,0.55,-.03], [0.19,0.49,-.04],
  [0.39,0.55,0], [0.38,0.40,-.01], [0.37,0.29,-.02], [0.36,0.20,-.03],
  [0.49,0.53,0], [0.49,0.36,-.01], [0.49,0.23,-.02], [0.49,0.13,-.03],
  [0.59,0.56,0], [0.61,0.40,-.01], [0.62,0.29,-.02], [0.63,0.21,-.03],
  [0.68,0.62,0], [0.73,0.51,-.01], [0.76,0.43,-.02], [0.79,0.37,-.03]
].map(([x,y,z]) => ({x,y,z}));
const base = extractFeatures(hand, {width:640,height:480});
const assertVectorsNear = (a,b,tolerance=1e-7) => {assert.equal(a.length,b.length);a.forEach((v,i)=>assert.ok(Math.abs(v-b[i])<tolerance,`feature ${i}: ${v} vs ${b[i]}`));};
const vector = (label, n=0) => Array.from({length:FEATURE_COUNT},(_,i)=>0.10+label*0.18+Math.sin(i+n)*0.003);
const samples = () => Object.fromEntries(POSES.map((pose,i)=>[pose,Array.from({length:12},(_,n)=>vector(i,n))]));

test('features are finite and bounded with the declared dimension',()=>{assert.equal(base.length,FEATURE_COUNT);assert.ok(base.every(v=>Number.isFinite(v)&&v>=0&&v<=1));});
test('translation and uniform physical scale leave features unchanged',()=>{const moved=hand.map(p=>({x:(p.x-.5)*.45+.6,y:(p.y-.5)*.45+.4,z:p.z*.45}));assertVectorsNear(base,extractFeatures(moved,{width:640,height:480}));});
test('image aspect is corrected before geometric feature extraction',()=>{const other=hand.map(p=>({x:p.x*640/1920,y:p.y*480/1080,z:p.z*640/1920}));assertVectorsNear(base,extractFeatures(other,{width:1920,height:1080}));});
test('left/right reflection and in-plane rotation preserve features',()=>{const reflected=hand.map(p=>({x:1-p.x,y:p.y,z:p.z}));assertVectorsNear(base,extractFeatures(reflected,{width:640,height:480,handedness:'Left'}));const angle=.72;const rotated=hand.map(p=>{const x=(p.x-.5)*640,y=(p.y-.5)*480;return{x:(x*Math.cos(angle)-y*Math.sin(angle))/640+.5,y:(x*Math.sin(angle)+y*Math.cos(angle))/480+.5,z:p.z}});assertVectorsNear(base,extractFeatures(rotated,{width:640,height:480}));});
test('missing, nonfinite and degenerate landmarks are rejected',()=>{assert.equal(extractFeatures([]),null);assert.equal(extractFeatures(Array(21).fill({x:0,y:0,z:0})),null);assert.equal(extractFeatures(hand.map((p,i)=>i===7?{...p,z:NaN}:p)),null);assert.equal(extractFeatures(hand,{width:0,height:480}),null);});
test('quality identifies too-small and clipped hands without claiming metric distance',()=>{assert.equal(getHandQuality(hand,{width:640,height:480}).ok,true);const small=hand.map(p=>({x:(p.x-.5)*.04+.5,y:(p.y-.5)*.04+.5,z:p.z*.04}));assert.equal(getHandQuality(small,{width:640,height:480}).reason,'hand-too-small');const cut=hand.map(p=>({...p,x:p.x-.25}));assert.equal(getHandQuality(cut,{width:640,height:480}).boundaryCutoff,true);});
test('personal models recognize held-out local features and explicit neutral',()=>{const model=trainEnvironment(samples());assert.equal(model.usable,true);for(let i=0;i<POSES.length;i++){const match=classifyPersonal(vector(i,100),model);assert.equal(match.accepted,true);assert.equal(match.gesture,POSES[i]);}assert.equal(classifyPersonal(vector(4,100),model).reason,'neutral');});
test('features far from all registrations are rejected instead of forced to a command',()=>{const model=trainEnvironment(samples());const unknown=Array.from({length:FEATURE_COUNT},(_,i)=>i%2?.99:.01);const match=classifyPersonal(unknown,model);assert.equal(match.accepted,false);assert.equal(match.gesture,null);assert.equal(match.reason,'outside-enrollment');});
test('overlapping registrations require re-enrollment',()=>{const data=samples();data.Thumb_Up=data.Open_Palm.map(v=>[...v]);const model=trainEnvironment(data);assert.equal(model.usable,false);assert.ok(model.warnings.some(w=>w.includes('Open_Palm / Thumb_Up')));assert.equal(classifyPersonal(data.Open_Palm[0],model).reason,'ambiguous-enrollment');});
test('widely varying pose cannot inflate its acceptance radius',()=>{const data=samples();data.Thumb_Up=Array.from({length:12},(_,n)=>Array.from({length:FEATURE_COUNT},(_,i)=>(n+i)%2?.99:.01));const model=trainEnvironment(data);assert.equal(model.usable,false);assert.ok(model.classes.Thumb_Up.radius<=.14);assert.ok(model.warnings.length);});
test('a single outlier is removed without discarding consistent enrollment',()=>{const data=samples();data.Thumb_Up.push(Array(FEATURE_COUNT).fill(.99));const model=trainEnvironment(data);assert.equal(model.usable,true);assert.equal(model.classes.Thumb_Up.inlierCount,12);assert.equal(classifyPersonal(vector(1,100),model).accepted,true);});
test('models are JSON serializable and reject malformed vectors and models',()=>{const model=JSON.parse(JSON.stringify(trainEnvironment(samples())));assert.equal(classifyPersonal(vector(2,100),model).gesture,'Victory');assert.equal(classifyPersonal([1,2],model).reason,'invalid-features');model.classes.Victory.radius=5;assert.equal(classifyPersonal(vector(2,100),model).reason,'invalid-model');assert.throws(()=>trainEnvironment({}),/require/);});
test('desk and sofa train independently and preserve different personal examples',()=>{const desk=samples(),sofa=samples();[sofa.Thumb_Up,sofa.Victory]=[sofa.Victory,sofa.Thumb_Up];const deskModel=trainEnvironment(desk),sofaModel=trainEnvironment(sofa);assert.equal(classifyPersonal(vector(1,50),deskModel).gesture,'Thumb_Up');assert.equal(classifyPersonal(vector(1,50),sofaModel).gesture,'Victory');const profile={version:1,id:'p1',name:'테스트 사용자',environments:{desk:{samples:desk,model:deskModel},sofa:{samples:sofa,model:sofaModel}}};assert.equal(validateProfile(JSON.parse(JSON.stringify(profile))).valid,true);});
test('profile validation bounds untrusted serialized data',()=>{assert.equal(validateProfile(null).valid,false);assert.equal(validateProfile({version:2,id:'p',name:'name',environments:{}}).valid,false);assert.equal(validateProfile({version:1,id:'p',name:'name',environments:{other:{samples:samples()}}}).valid,false);const data=samples();data.None[0][0]=Infinity;assert.equal(validateProfile({version:1,id:'p',name:'name',environments:{desk:{samples:data}}}).valid,false);assert.equal(validateProfile({version:1,id:'p',name:'name',environments:{}}).valid,true);});

test('profile validation permits onboarding metadata while rejecting corrupted model statistics', () => {
  const data = samples();
  const model = trainEnvironment(data);
  const profile = {
    version: 1, id: 'local-user', name: '사용자', createdAt: 123,
    environments: {
      desk: {samples: data, model, verified: true, verifiedAt: 456,
        verification: {passed: 5}, resolution: {width: 1920, height: 1080}, createdAt: 234}
    }
  };
  assert.equal(validateProfile(profile).valid, true);
  model.classes.None.spread = NaN;
  assert.equal(validateProfile(profile).valid, false);
  assert.equal(classifyPersonal(vector(4), model).reason, 'invalid-model');
});

test('sparse arrays are rejected as malformed samples and landmarks', () => {
  const model = trainEnvironment(samples());
  assert.equal(extractFeatures(Array(21)), null);
  assert.equal(classifyPersonal(Array(FEATURE_COUNT), model).reason, 'invalid-features');
  const data = samples();
  data.None = Array(12);
  assert.throws(() => trainEnvironment(data), /invalid feature vector/);
});

test('recorded real MediaPipe landmark fixtures train and classify withheld source-image variants', async () => {
  // These are variants of five source photographs, not independent people or
  // camera sessions. This regression verifies the actual-model data path only.
  const reference = JSON.parse(await readFile(new URL('./fixtures/personalization-reference.json', import.meta.url), 'utf8'));
  const data = {};
  for (const observation of reference.observations) {
    const dimensions = {width: observation.width, height: observation.height};
    data[observation.pose] = observation.rows.filter(row => row.split === 'training')
      .map(row => extractFeatures(row.landmarks, dimensions));
    for (const row of observation.rows) {
      assert.equal(getHandQuality(row.landmarks, dimensions).ok, true);
      assertVectorsNear(extractFeatures(row.landmarks, dimensions), row.features);
    }
  }
  const model = trainEnvironment(data);
  assert.equal(model.usable, true);
  for (const observation of reference.observations) {
    for (const row of observation.rows.filter(row => row.split === 'verification')) {
      const result = classifyPersonal(row.features, model);
      assert.equal(result.accepted, true);
      assert.equal(result.gesture, observation.pose);
    }
  }
  const palm = reference.observations.find(observation => observation.pose === 'Open_Palm');
  const cannedMismatch = palm.rows.find(row => row.canned.categoryName === 'None');
  assert.ok(cannedMismatch, 'Reference contains a canned-label miss');
  assert.equal(classifyPersonal(cannedMismatch.features, model).gesture, 'Open_Palm');
});

// Separate view clusters deliberately differ more than the old global radius.
// Pose identity is encoded in the first ten dimensions; view variation is in
// the remaining dimensions. These are controlled classifier inputs, not camera
// accuracy or a claim that the orientation estimator observed those angles.
const viewVector = (poseIndex, viewIndex, n = 0) => Array.from({length:FEATURE_COUNT}, (_,i) => {
  const center = i < 10 ? (Math.floor(i / 2) === poseIndex ? .8 : .1) : .1 + viewIndex * .2;
  return center + Math.sin(i * .7 + n) * .002;
});
const multiViewSamples = () => Object.fromEntries(POSES.map((pose,p) => [pose,
  Object.fromEntries(ENROLLMENT_VIEWS.map((view,v) => [view, Array.from({length:MIN_SAMPLES_PER_VIEW + 2}, (_,n) => viewVector(p,v,n))]))
]));
const flattenViews = views => Object.fromEntries(POSES.map(pose => [pose, ENROLLMENT_VIEWS.flatMap(view => views[pose][view])]));
const trainMulti = views => trainEnvironment(flattenViews(views), {viewSamples:views});

test('multi-view models retain separate valid angle distributions rejected by a single global center', () => {
  const viewSamples = multiViewSamples(), flat = flattenViews(viewSamples);
  assert.equal(trainEnvironment(flat).usable, false, 'Legacy center rejects the wide combined distribution');
  const model = trainEnvironment(flat, {viewSamples});
  assert.equal(model.version, 2); assert.equal(model.usable, true); assert.deepEqual(model.warnings, []);
  for (let p = 0; p < POSES.length; p++) for (let v = 0; v < ENROLLMENT_VIEWS.length; v++) {
    const match = classifyPersonal(viewVector(p,v,100), model);
    assert.equal(match.accepted, true); assert.equal(match.gesture, POSES[p]); assert.equal(match.view, ENROLLMENT_VIEWS[v]);
  }
  assert.equal(classifyPersonal(viewVector(4,2,100), model).reason, 'neutral');
});

test('multi-view registration does not accept unobserved gaps or far out-of-distribution features', () => {
  const model = trainMulti(multiViewSamples());
  const between = viewVector(2,.5,100);
  assert.equal(classifyPersonal(between, model).accepted, false, 'Empty feature-space gaps are not filled by a global acceptance radius');
  const unknown = Array.from({length:FEATURE_COUNT}, (_,i) => i % 2 ? .99 : .01);
  assert.equal(classifyPersonal(unknown, model).reason, 'outside-enrollment');
});

test('a noisy angle cannot inflate its radius or silently disappear from a usable profile', () => {
  const viewSamples = multiViewSamples();
  viewSamples.Thumb_Up.left = Array.from({length:14}, (_,n) => Array.from({length:FEATURE_COUNT}, (_,i) => (n+i)%2 ? .99 : .01));
  const model = trainMulti(viewSamples);
  assert.equal(model.usable, false); assert.equal(model.classes.Thumb_Up.views.left.enabled, false);
  assert.ok(model.classes.Thumb_Up.views.left.radius <= .14);
  assert.ok(model.warnings.some(w => w.includes('Thumb_Up/left')));
  assert.equal(classifyPersonal(viewVector(1,0,100), model).reason, 'ambiguous-enrollment');
});

test('one local outlier is filtered while the other view distributions are preserved', () => {
  const viewSamples = multiViewSamples();
  viewSamples.Victory.up.push(Array(FEATURE_COUNT).fill(.99));
  const model = trainMulti(viewSamples);
  assert.equal(model.usable, true);
  assert.equal(model.classes.Victory.views.up.inlierCount, 14);
  for (let v = 0; v < ENROLLMENT_VIEWS.length; v++) assert.equal(classifyPersonal(viewVector(2,v,110), model).gesture, 'Victory');
});

test('an overlap between different commands in different views makes enrollment unusable', () => {
  const viewSamples = multiViewSamples();
  viewSamples.Closed_Fist.down = structuredClone(viewSamples.Open_Palm.left);
  const model = trainMulti(viewSamples);
  assert.equal(model.usable, false);
  assert.equal(model.classes.Open_Palm.views.left.enabled, false);
  assert.equal(model.classes.Closed_Fist.views.down.enabled, false);
  assert.ok(model.warnings.some(w => w.includes('Open_Palm/left') && w.includes('Closed_Fist/down')));
});

test('all five named view buckets and their captured sample correspondence are required', () => {
  const original = multiViewSamples(), flat = flattenViews(original);
  const absent = structuredClone(original); delete absent.None.down;
  assert.throws(() => trainEnvironment(flat, {viewSamples:absent}), /None\/down: require/);
  const short = structuredClone(original); short.Victory.up = short.Victory.up.slice(0,11);
  assert.throws(() => trainEnvironment(flat, {viewSamples:short}), /Victory\/up: require/);
  const sparse = structuredClone(original); sparse.Thumb_Up.left = Array(14);
  assert.throws(() => trainEnvironment(flat, {viewSamples:sparse}), /invalid feature vector/);
  const changed = structuredClone(original); changed.Open_Palm.front[0][0] += .01;
  assert.throws(() => trainEnvironment(flat, {viewSamples:changed}), /view samples do not match/);
  const reversedFlat = Object.fromEntries(POSES.map(pose => [pose, [...flat[pose]].reverse()]));
  assert.equal(trainEnvironment(reversedFlat, {viewSamples:original}).usable, true, 'Flat storage order does not change grouping');
});

test('multi-view groups remain bounded in serialized models and malformed views are rejected', () => {
  const model = JSON.parse(JSON.stringify(trainMulti(multiViewSamples())));
  assert.equal(classifyPersonal(viewVector(3,4,101), model).gesture, 'Closed_Fist');
  for (const pose of POSES) for (const view of ENROLLMENT_VIEWS) assert.ok(model.classes[pose].views[view].templates.length <= 8);
  const missing = structuredClone(model); delete missing.classes.None.views.front;
  assert.equal(classifyPersonal(viewVector(4,0), missing).reason, 'invalid-model');
  const badRadius = structuredClone(model); badRadius.classes.None.views.front.radius = Infinity;
  assert.equal(classifyPersonal(viewVector(4,0), badRadius).reason, 'invalid-model');
  const wrongTotals = structuredClone(model); wrongTotals.classes.Victory.sampleCount++;
  assert.equal(classifyPersonal(viewVector(2,0), wrongTotals).reason, 'invalid-model');
  const sparseTemplates = structuredClone(model); sparseTemplates.classes.Thumb_Up.views.up.templates = Array(8);
  assert.equal(classifyPersonal(viewVector(1,3), sparseTemplates).reason, 'invalid-model');
});

test('legacy and multi-view environments can coexist and survive profile serialization', () => {
  const legacySamples = samples(), viewSamples = multiViewSamples(), flat = flattenViews(viewSamples);
  const profile = {version:1,id:'mixed-user',name:'기존 및 새 등록',environments:{
    desk:{samples:legacySamples,model:trainEnvironment(legacySamples),verified:true},
    sofa:{samples:flat,viewSamples,model:trainEnvironment(flat,{viewSamples}),verified:true}
  }};
  const restored = JSON.parse(JSON.stringify(profile));
  assert.equal(validateProfile(restored).valid, true);
  assert.equal(restored.environments.desk.model.version, 1);
  assert.equal(restored.environments.sofa.model.version, 2);
  assert.equal(classifyPersonal(vector(1,100),restored.environments.desk.model).gesture,'Thumb_Up');
  assert.equal(classifyPersonal(viewVector(1,4,100),restored.environments.sofa.model).gesture,'Thumb_Up');
  restored.environments.sofa.viewSamples.None.front[0][1] += .01;
  assert.equal(validateProfile(restored).valid,false);
});

test('profile validation reports malformed flat data with view metadata instead of throwing', () => {
  const viewSamples = multiViewSamples();
  for (const bad of [null, [], {Open_Palm:{}}, {Open_Palm:'wrong'}]) {
    const result = validateProfile({version:1,id:'p',name:'name',environments:{desk:{samples:bad,viewSamples}}});
    assert.equal(result.valid,false);
  }
});
