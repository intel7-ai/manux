"""Run the shipped Internet page with synthetic browser APIs; no camera/network is used."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest


PAGE = Path(__file__).resolve().parents[3] / "src/manux/link/static/internet.html"


def test_internet_page_auth_backpressure_and_camera_cleanup():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the simulated browser check")
    script = re.search(r"<script>(.*?)</script>", PAGE.read_text(encoding="utf-8"), re.S).group(1)
    harness = r"""
const vm = require('node:vm'), assert = require('node:assert/strict');
let source = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', part => source += part);
process.stdin.on('end', async () => {
try {
  const elements = new Map(), tracks = [], sockets = [], cameraRequests = [], screenRequests = [], blobs = [];
  let clock = 1000, playMode = 'success', deferredPlay, docListeners = {}, wakeReleased = 0;
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: id === 'camera' ? 'user' : '123456', disabled: false, textContent: '', srcObject: null,
      classList: {toggle() {}}, listeners: {}, readyState: 2, currentTime: 1, videoWidth: 1280, videoHeight: 960,
      addEventListener(name, callback) {this.listeners[name] = callback;},
      getContext() {return {drawImage() {}, clearRect() {}};},
      toBlob(callback,type,quality) {assert.equal(type,'image/jpeg');assert(quality>=.62 && quality<=.72);blobs.push(callback);},
      async play() {if (playMode === 'reject') throw Error('synthetic play failure');
        if (playMode === 'pending') await new Promise((resolve, reject) => deferredPlay = {resolve, reject});},
      requestVideoFrameCallback() {return 1;}, cancelVideoFrameCallback() {},
      requestFullscreen: async () => {}
    });
    return elements.get(id);
  }
  class Socket {
    static OPEN = 1;
    constructor(url) {assert.equal(url, 'wss://pc.example.test/ws'); this.readyState=0; this.bufferedAmount=0; this.sent=[]; sockets.push(this);}
    send(value) {this.sent.push(value);}
    close() {this.readyState=3;}
  }
  const context = vm.createContext({
    document: {getElementById:element, hidden:false, fullscreenElement:null,
      addEventListener(name, callback) {docListeners[name]=callback;}, exitFullscreen:async()=>{}},
    window:{addEventListener(){}}, location:{protocol:'https:',href:'https://pc.example.test/'},
    navigator:{mediaDevices:{getUserMedia:async config => {
      cameraRequests.push(config);
      const track={contentHint:'',stopped:false,stop(){this.stopped=true;},getSettings(){return {facingMode:config.video.facingMode.ideal};}};
      tracks.push(track); return {getTracks:()=>[track],getVideoTracks:()=>[track]};
    }},wakeLock:{request:async()=>({release:async()=>{wakeReleased++;}})}},
    WebSocket:Socket, performance:{now:()=>clock}, TextEncoder, Uint8Array, DataView, URL, AbortController,
    fetch:async(url,options)=>{
      assert.equal(options.credentials,'omit'); assert.equal(options.redirect,'error');
      if(url==='/pair') return {ok:true,json:async()=>({token:'synthetic-pair-token',iceServers:[]})};
      assert.equal(url,'/screen'); assert.equal(options.headers.Authorization,'Bearer synthetic-screen-token');
      assert.equal(options.headers['X-Manux-Screen-Profile'],'internet_detail');
      return await new Promise(resolve=>screenRequests.push({resolve,options}));
    },
    setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,clearInterval(){},requestAnimationFrame:()=>1,cancelAnimationFrame(){},console
  });
  vm.runInContext(source,context);
  assert.equal(cameraRequests.length,0,'camera stays closed before pairing');
  await element('pairForm').listeners.submit({preventDefault(){}});
  const socket=sockets[0]; socket.readyState=1; socket.onopen();
  assert.deepEqual(JSON.parse(socket.sent[0]),{kind:'auth',token:'synthetic-pair-token'});
  assert.equal(cameraRequests.length,0,'camera stays closed before authenticated READY');
  await socket.onmessage({data:JSON.stringify({kind:'ready',server_time:Date.now()/1000,screen_token:'synthetic-screen-token'})});
  assert.equal(cameraRequests.length,1); assert.equal(cameraRequests[0].audio,false);
  assert.equal(cameraRequests[0].video.width.ideal,1280); assert.equal(cameraRequests[0].video.height.ideal,960);
  assert.equal(cameraRequests[0].video.aspectRatio.exact,4/3); assert.equal(cameraRequests[0].video.frameRate.max,30);
  assert.equal(tracks[0].contentHint,'motion');
  assert(socket.sent.some(data=>typeof data==='string' && JSON.parse(data).kind==='fallback'),'unsupported RTC explicitly negotiates relay');
  assert.equal(vm.runInContext('mode',context),'relay');
  assert.equal(screenRequests.length,1,'only one PC screenshot request is pending');
  const produce = async () => {
    const pending=vm.runInContext('sendRelayFrame(performance.now(),generation)',context);
    assert(blobs.length); blobs.shift()({arrayBuffer:async()=>new Uint8Array([255,216,255,217]).buffer}); await pending;
    clock+=100; element('localVideo').currentTime+=1;
  };
  await produce(); await produce();
  const sent=socket.sent.filter(value=>typeof value!=='string'); assert.equal(sent.length,2);
  const headerLength=new DataView(sent[0]).getUint32(0,false);
  const metadata=JSON.parse(Buffer.from(sent[0],4,headerLength).toString());
  assert.equal(metadata.width,960); assert.equal(metadata.height,720); assert.equal(metadata.mirrored,true); assert.equal(metadata.rotation,0);
  await vm.runInContext('sendRelayFrame(performance.now(),generation)',context);
  assert.equal(blobs.length,0,'two unacknowledged images apply backpressure');
  await socket.onmessage({data:JSON.stringify({kind:'ack',seq:metadata.seq,accepted:true})});
  const encoding=vm.runInContext('sendRelayFrame(performance.now(),generation)',context);
  vm.runInContext('cameraEpoch++',context);
  blobs.shift()({arrayBuffer:async()=>new Uint8Array([255,216,255,217]).buffer}); await encoding;
  assert.equal(socket.sent.filter(value=>typeof value!=='string').length,2,'old camera encoding is dropped after a camera switch');
  context.window.manuxDisconnect();
  assert(tracks[0].stopped); assert.equal(socket.readyState,3);
  assert.equal(vm.runInContext('token',context),null); assert.equal(vm.runInContext('screenToken',context),null);
  assert.equal(vm.runInContext('pending.size',context),0); assert.equal(element('code').value,'');
  assert(screenRequests[0].options.signal.aborted,'pending screenshot is cancelled on disconnect');
  playMode='reject';
  await assert.rejects(vm.runInContext('requestCamera(generation)',context));
  assert(tracks.at(-1).stopped,'getUserMedia stream is released if video.play fails');
  playMode='pending';
  const opening=vm.runInContext('requestCamera(generation)',context);
  await new Promise(resolve => setImmediate(resolve));
  assert(deferredPlay); context.window.manuxDisconnect();
  assert(tracks.at(-1).stopped,'background disconnect immediately stops stream while play is pending');
  deferredPlay.reject(Error('synthetic background cancellation')); await assert.rejects(opening);
  assert.equal(vm.runInContext('openingStream',context),null);
  assert(wakeReleased>=1);
  assert.deepEqual(Array.from(vm.runInContext('frameSize(960,1280)',context)),[720,960],'portrait relay preserves 3:4 geometry');
  assert.deepEqual(Array.from(vm.runInContext('frameSize(320,240)',context)),[320,240],'low resolution camera is never artificially upscaled');
  context.window.matchMedia=()=>({matches:true});
  const portrait=vm.runInContext('cameraConstraints()',context);
  assert.equal(portrait.width.ideal,960); assert.equal(portrait.height.ideal,1280); assert.equal(portrait.aspectRatio.exact,3/4);
  vm.runInContext('rttMs=400',context);
  const reduced=vm.runInContext('relayQuality()',context);
  assert.equal(reduced.width,640); assert.equal(reduced.height,480); assert.equal(reduced.jpeg,.62);
  const attempts=[];
  context.qualitySender={getParameters:()=>({transactionId:String(attempts.length),encodings:[{rid:'existing'}]}),
    setParameters:async parameters=>{attempts.push(parameters);if(attempts.length===1)throw Error('unsupported degradationPreference');}};
  assert.equal(await vm.runInContext('configureCameraSender(qualitySender)',context),true);
  assert.equal(attempts.length,2); assert.equal(attempts[0].degradationPreference,'maintain-resolution');
  assert.equal(attempts[1].encodings[0].scaleResolutionDownBy,1); assert.equal(attempts[1].encodings[0].maxBitrate,4000000);
  assert.equal(attempts[1].encodings[0].maxFramerate,30); assert.equal(attempts[1].encodings[0].rid,'existing');
  context.qualitySender.setParameters=async()=>{throw Error('unsupported API');};
  assert.equal(await vm.runInContext('configureCameraSender(qualitySender)',context),false,'unsupported quality controls do not break connection');
  context.statsPeer={getStats:async()=>new Map([
    ['out',{type:'outbound-rtp',kind:'video',frameWidth:320,frameHeight:240,framesEncoded:26}],
    ['in',{type:'inbound-rtp',kind:'video',frameWidth:1920,frameHeight:1080,framesDecoded:15}]])};
  vm.runInContext('mode="webrtc";peer=statsPeer;lastStatsAt=performance.now()-1000;lastOutboundFrames=lastRemoteFrames=0;',context);
  await vm.runInContext('updateStats()',context);
  assert(element('transport').textContent.includes('카메라 전송 320×240 26.0 fps'),'show encoded resolution, not requested HD');
  assert(element('transport').textContent.includes('PC 화면 1920×1080 15.0 fps'));
  console.log('PASS internet auth, relay backpressure, camera switch, background and play failure cleanup');
} catch(error) {console.error(error);process.exitCode=1;}
});
"""
    result = subprocess.run([node, "-e", harness], input=script, encoding="utf-8", capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_internet_page_contains_no_persistent_auth_or_third_party_scripts():
    page = PAGE.read_text(encoding="utf-8")
    assert "localStorage" not in page and "sessionStorage" not in page
    assert not re.search(r"<script\b[^>]*\bsrc=", page)
    assert "window.manuxDisconnect" in page
    assert "connect-src 'self'" in page and "frame-src 'none'" in page


def test_ice_gather_timeout_keeps_collected_candidates_and_cleans_callbacks():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the simulated browser check")
    page = PAGE.read_text(encoding="utf-8")
    function = re.search(r"    async function waitIceComplete.*?(?=    async function startRtc)", page, re.S).group(0)
    harness = r"""
const vm = require('node:vm'), assert = require('node:assert/strict');
let source=''; process.stdin.setEncoding('utf8'); process.stdin.on('data',part=>source+=part);
process.stdin.on('end',async()=>{
 try {
  function setup(sdp) {
    let timer, callback, removed=0, cleared=0;
    const pc={iceGatheringState:'gathering',localDescription:{type:'offer',sdp},
      addEventListener(name,fn){assert.equal(name,'icegatheringstatechange');callback=fn;},
      removeEventListener(name,fn){assert.equal(name,'icegatheringstatechange');assert.equal(fn,callback);removed++;}};
    const context=vm.createContext({pc,setTimeout(fn,delay){assert.equal(delay,8000);timer=fn;return 1;},clearTimeout(){cleared++;}});
    vm.runInContext('let generation=1, peer=pc, iceGatherCancel=null;'+source,context);
    return {context,pc,timer:()=>timer(),changed:()=>callback(),removed:()=>removed,cleared:()=>cleared};
  }
  const usable=setup('v=0\r\na=candidate:1 1 udp 2122260223 192.0.2.1 50000 typ host\r\n');
  const partial=vm.runInContext('waitIceComplete(peer,generation)',usable.context);
  usable.timer(); await partial;
  assert.equal(usable.removed(),1); assert.equal(usable.cleared(),1);
  assert.equal(vm.runInContext('iceGatherCancel',usable.context),null);
  usable.pc.iceGatheringState='complete'; usable.changed(); usable.timer();
  assert.equal(usable.removed(),1,'late completion/timeout cannot settle or clean twice');

  const empty=setup('v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n');
  const failed=vm.runInContext('waitIceComplete(peer,generation)',empty.context);
  const rejected=assert.rejects(failed,/ICE timeout/); empty.timer(); await rejected;
  assert.equal(empty.removed(),1,'empty gather timeout still falls back and releases listener');

  const cancelled=setup('v=0\r\na=candidate:1 1 udp 1 192.0.2.2 50002 typ host\r\n');
  const pending=vm.runInContext('waitIceComplete(peer,generation)',cancelled.context);
  const abandoned=assert.rejects(pending,/cancelled/);
  vm.runInContext('generation++; iceGatherCancel()',cancelled.context); await abandoned;
  assert.equal(cancelled.removed(),1); assert.equal(cancelled.cleared(),1);
  cancelled.pc.iceGatheringState='complete'; cancelled.changed(); cancelled.timer();
  assert.equal(cancelled.removed(),1,'old session cannot resume negotiation');
  console.log('PASS gathered ICE candidates survive STUN delay; empty/aborted sessions cleanly fail');
 }catch(error){console.error(error);process.exitCode=1;}
});
"""
    result = subprocess.run([node, "-e", harness], input=function, encoding="utf-8", capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
