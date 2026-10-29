import test from 'node:test';
import assert from 'node:assert/strict';
import {VIEWS, extractOrientation, orientationFeedback} from '../src/orientation.js';

const radians = degrees => degrees*Math.PI/180;
const physical = Array.from({length:21},(_,i)=>[i*2-20,20-i*4,0]);
physical[0]=[0,90,0];physical[5]=[-45,-15,0];physical[9]=[0,-30,0];physical[17]=[50,-5,0];
const rotate = (points,{x=0,y=0,z=0}={}) => points.map(point=>{
  let [a,b,c]=point;let t=radians(x);[b,c]=[b*Math.cos(t)-c*Math.sin(t),b*Math.sin(t)+c*Math.cos(t)];
  t=radians(y);[a,c]=[a*Math.cos(t)+c*Math.sin(t),-a*Math.sin(t)+c*Math.cos(t)];
  t=radians(z);[a,b]=[a*Math.cos(t)-b*Math.sin(t),a*Math.sin(t)+b*Math.cos(t)];return[a,b,c];
});
const landmarks = (points=physical,width=640,height=480) => points.map(([x,y,z])=>({x:.5+x/width,y:.5+y/height,z:z/width}));
const orientation = (rotation={}) => extractOrientation(landmarks(rotate(physical,rotation)),{width:640,height:480});
const near = (actual,expected,epsilon=1e-8) => assert.ok(Math.abs(actual-expected)<epsilon,`${actual} != ${expected}`);
const baseline = {yaw:0,pitch:0};

test('front and four real 3D tilts map to five exclusive relative view bands',()=>{
  const examples={front:{},left:{y:25},right:{y:-25},up:{x:25},down:{x:-25}};
  for(const [expected,rotation] of Object.entries(examples)){
    const value=orientation(rotation);assert.equal(value.valid,true);
    for(const target of VIEWS)assert.equal(orientationFeedback(value,baseline,target).matched,target===expected);
  }
});
test('camera-coordinate translation and uniform scaling cannot create view coverage',()=>{
  const tilted=rotate(physical,{x:12,y:24});const expected=extractOrientation(landmarks(tilted),{width:640,height:480});
  const moved=tilted.map(([x,y,z])=>[x*.4+85,y*.4-50,z*.4+30]);
  const actual=extractOrientation(landmarks(moved),{width:640,height:480});
  near(actual.yaw,expected.yaw);near(actual.pitch,expected.pitch);
  assert.equal(orientationFeedback(actual,expected,'front').matched,true);
  assert.equal(orientationFeedback(actual,expected,'left').matched,false);
});
test('image-plane roll is removed even when the reference palm is already tilted',()=>{
  const expected=orientation({x:17,y:23});
  for(const z of [-179,-90,-30,45,135,179]){
    const actual=orientation({x:17,y:23,z});
    near(actual.yaw,expected.yaw);near(actual.pitch,expected.pitch);
    assert.equal(orientationFeedback(actual,expected,'front').matched,true);
  }
});
test('aspect-ratio correction gives the same estimate in different input resolutions',()=>{
  const points=rotate(physical,{x:13,y:-21,z:35});
  const first=extractOrientation(landmarks(points,640,480),{width:640,height:480});
  const second=extractOrientation(landmarks(points,1920,1080),{width:1920,height:1080});
  near(first.yaw,second.yaw);near(first.pitch,second.pitch);near(first.roll,second.roll);
});
test('mirrored preview reverses only the reported horizontal direction',()=>{
  const hand=landmarks(rotate(physical,{x:15,y:25}));
  const mirrored=extractOrientation(hand,{width:640,height:480,mirrored:true});
  const raw=extractOrientation(hand,{width:640,height:480,mirrored:false});
  near(mirrored.yaw,-raw.yaw);near(mirrored.pitch,raw.pitch);
});
test('invalid, sparse, degenerate and edge-on palms cannot claim an angle',()=>{
  for(const hand of [[],Array(21),Array(21).fill({x:.5,y:.5,z:0}),landmarks().map((p,i)=>i===5?{...p,z:NaN}:p)])assert.equal(extractOrientation(hand).valid,false);
  assert.equal(extractOrientation(landmarks(),{width:0,height:480}).valid,false);
  assert.equal(orientation({y:85}).valid,false);assert.equal(orientation({x:90}).valid,false);
});
test('diagonals, gaps and excessive tilt do not satisfy another direction by accident',()=>{
  for(const value of [{yaw:12,pitch:0},{yaw:20,pitch:20},{yaw:46,pitch:0}]){
    const input={valid:true,...value};
    for(const view of VIEWS)assert.equal(orientationFeedback(input,baseline,view).matched,false);
  }
  assert.equal(orientationFeedback({valid:true,yaw:46,pitch:0},baseline,'right').reason,'tilt-too-far');
  assert.equal(orientationFeedback({valid:true,yaw:15,pitch:12},baseline,'right').matched,true);
  assert.equal(orientationFeedback({valid:true,yaw:10,pitch:-10},baseline,'front').matched,true);
});
test('relative feedback uses each pose reference and reports screen-space guide coordinates',()=>{
  const reference={yaw:12,pitch:9};
  const right=orientationFeedback({valid:true,yaw:37,pitch:9},reference,'right');
  near(right.deltaYaw,25);near(right.deltaPitch,0);near(right.x,25/30);near(right.y,0);assert.equal(right.label,'right');
  const up=orientationFeedback({valid:true,yaw:12,pitch:34},reference,'up');
  assert.ok(up.y<0);assert.equal(up.matched,true);
  assert.equal(orientationFeedback({valid:true,yaw:0,pitch:0},null,'front').reason,'need-front-reference');
});
