const $ = id=>document.getElementById(id);
const api = window.manux;
const model = new WorkspaceModel.Model();
const workspace = $('workspace');
const cursor = $('hand-cursor');
let current = {native:false,paused:false,state:'waiting',position:[.5,.5],camera:{mirror:true,rotation:0}};
let shown = [.5,.5], target=[.5,.5], lastFrame=performance.now(), lastPacket=0, nativePending=false;
let lastSequence=0, dragOrigin=null, lastMousePoint=null, scope='all';
let lastFeedback=0, practiceMoving=false, lastClickPoint=null;
const tips = {
  all:['검지는 가리키고, 나머지는 편하게.','검지 누르기 → 클릭 · 편 손의 시작 방향 → 스크롤 / 잡기 · 엄지와 검지 간격 → 확대'],
  click:['검지를 누른 뒤, 원래 자세로 돌아오세요.','누르는 순간 커서는 멈춥니다. 빠르게 두 번 반복하면 더블클릭합니다.'],
  scroll:['손을 펴고, 위아래로 시작하세요.','스크롤이 시작되면 옆으로 움직여도 잡기로 바뀌지 않습니다. 손을 접으면 끝납니다.'],
  drag:['물체를 가리킨 뒤 손을 펴고, 옆으로 시작하세요.','잡기 표시가 뜨면 원하는 곳으로 이동하세요. 손을 접으면 놓습니다.'],
  zoom:['엄지와 검지를 모았다가, 간격을 조절하세요.','손바닥을 펴면 그 배율로 확정합니다. 손을 편하게 접어 다시 이동하세요.']
};
function command(value){api.command(value);}
setInterval(()=>command({type:'heartbeat'}),500);
window.addEventListener('beforeunload',()=>command({type:'native',enabled:false}));
window.addEventListener('keydown',e=>{if(e.key==='Escape')command({type:'native',enabled:false});});

function receipt(text){$('receipt').textContent=text;$('receipt-count').textContent=`클릭 ${model.clicks} · 더블 ${model.doubles} · 놓기 ${model.drops}`;}
function pointFor(position){const r=workspace.getBoundingClientRect();return{x:r.left+position[0]*r.width,y:r.top+position[1]*r.height};}
function sendMouse(type,point,element,detail=1,buttons=0){
  (element||document.elementFromPoint(point.x,point.y)||workspace).dispatchEvent(new MouseEvent(type,{bubbles:true,cancelable:true,clientX:point.x,clientY:point.y,button:0,buttons,detail,view:window}));
}
function updateDrag(point){
  const p=model.move(point);if(!p)return;
  const s=$('drag-surface'), card=$('file-card');
  card.style.left=Math.max(-10,Math.min(s.clientWidth-card.offsetWidth+10,p.x))+'px';
  card.style.top=Math.max(-10,Math.min(s.clientHeight-card.offsetHeight+10,p.y))+'px';
}
function endDrag(){
  if(!model.drag)return;
  const card=$('file-card'), r=card.getBoundingClientRect(), d=$('drop-zone').getBoundingClientRect();
  const inside=r.left+r.width/2>d.left && r.left+r.width/2<d.right && r.top+r.height/2>d.top && r.top+r.height/2<d.bottom;
  model.end(inside);card.classList.remove('grabbed');$('drop-zone').classList.toggle('done',inside);
  $('drop-zone').innerHTML=inside?'놓기 완료<span>DONE ✓</span>':'이곳에 놓으세요<span>DROP HERE</span>';
  receipt(inside?'아이디어 카드를 목적지에 놓았습니다':'카드를 놓았습니다');
}
workspace.addEventListener('mousedown',e=>{
  const card=e.target.closest('#file-card');if(!card||e.button!==0)return;
  e.preventDefault();model.begin({x:e.clientX,y:e.clientY},{x:card.offsetLeft,y:card.offsetTop});card.classList.add('grabbed');receipt('아이디어 카드를 잡았습니다');
});
window.addEventListener('mousemove',e=>updateDrag({x:e.clientX,y:e.clientY}));
window.addEventListener('mouseup',endDrag);
workspace.addEventListener('click',e=>{
  const button=e.target.closest('[data-target]');if(!button)return;
  document.querySelectorAll('[data-target]').forEach(b=>b.classList.remove('selected','double'));
  button.classList.add('selected');const text=model.click(button.dataset.target);$('click-feedback').textContent=text;receipt(text);
});
workspace.addEventListener('dblclick',e=>{
  const button=e.target.closest('[data-target]');if(!button)return;
  button.classList.add('double');const text=model.double(button.dataset.target);$('click-feedback').textContent=text;receipt(text);
});
$('scroll-list').addEventListener('wheel',e=>{
  e.preventDefault();$('scroll-list').scrollTop+=e.deltaY;receipt(`목록 이동 · ${Math.round($('scroll-list').scrollTop)}px`);
},{passive:false});
$('zoom-viewport').addEventListener('wheel',e=>{
  if(!e.ctrlKey)return;e.preventDefault();setZoom(Math.exp(-e.deltaY/300));
},{passive:false});
function setZoom(factor){const z=model.scale(factor);$('poster').style.transform=`scale(${z})`;$('zoom-ratio').textContent=Math.round(z*100)+'%';$('zoom-badge').textContent='손바닥을 펴서 배율 확정';receipt(`그림 크기 · ${Math.round(z*100)}%`);}
function applyAction(packet){
  if(packet.id<=lastSequence)return;lastSequence=packet.id;
  if(packet.native)return;
  const a=packet.action;
  // The rendered pointer owns the practice hit location. Never click at a
  // camera target that the user has not yet seen while interpolation finishes.
  const p=a.type==='click'&&a.double&&lastClickPoint?lastClickPoint:pointFor(shown);
  if(a.type==='click'){
    const element=document.elementFromPoint(p.x,p.y);if(!element||!workspace.contains(element))return;
    sendMouse('mousedown',p,element,a.double?2:1,1);sendMouse('mouseup',p,element,a.double?2:1,0);sendMouse('click',p,element,a.double?2:1,0);
    if(a.double)sendMouse('dblclick',p,element,2,0);
    lastClickPoint=p;
  }else if(a.type==='down')sendMouse('mousedown',p,null,1,1);
  else if(a.type==='up')sendMouse('mouseup',p,window,1,0);
  else if(a.type==='scroll'){
    const el=document.elementFromPoint(p.x,p.y);const list=el?.closest('.scroll-list');
    if(list)list.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:a.pixels,clientX:p.x,clientY:p.y}));
    else receipt('스크롤 판정됨 · 목록 위에 커서를 두세요');
  }else if(a.type==='zoom'){
    // The zoom target is chosen by pointing first, just as a desktop Ctrl+wheel target is.
    const el=document.elementFromPoint(p.x,p.y);const view=el?.closest('.zoom-viewport');
    if(view)view.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,ctrlKey:true,deltaY:-Math.log(a.factor)*300,clientX:p.x,clientY:p.y}));
    else receipt('확대 판정됨 · 그림 위에 커서를 두세요');
  }else if(a.type==='zoomEnd'){$('zoom-badge').textContent=`${Math.round(model.zoom*100)}%로 확정`;receipt(`배율 확정 · ${Math.round(model.zoom*100)}%`);}
}

function setScope(value){
  if(model.drag)endDrag();scope=value;command({type:'scope',value});
  document.querySelectorAll('[data-scope]').forEach(b=>b.classList.toggle('selected',b.dataset.scope===value));
  $('scene').classList.toggle('single',value!=='all');
  for(const key of ['click','scroll','drag','zoom'])$(key+'-task').hidden=value!=='all'&&value!==key;
  $('guide-title').textContent=tips[value][0];$('guide-description').textContent=tips[value][1];
  $('scene-title').textContent=value==='all'?'작은 작업대':({click:'클릭 · 더블클릭',scroll:'스크롤',drag:'잡고 놓기',zoom:'확대 · 축소'})[value];
  resetWorkspace();
}
function resetWorkspace(){
  model.reset();$('file-card').style.left='';$('file-card').style.top='';$('file-card').classList.remove('grabbed');
  $('drop-zone').classList.remove('done');$('drop-zone').innerHTML='이곳에 놓으세요<span>DROP HERE</span>';
  $('poster').style.transform='scale(1)';$('zoom-ratio').textContent='100%';$('zoom-badge').textContent='엄지·검지를 모아 시작';
  $('scroll-list').scrollTop=0;document.querySelectorAll('[data-target]').forEach(b=>b.classList.remove('selected','double'));
  $('click-feedback').textContent='버튼을 선택하면 여기에 결과가 표시됩니다.';receipt('연습을 초기화했습니다');
}
document.querySelectorAll('[data-scope]').forEach(b=>b.addEventListener('click',()=>setScope(b.dataset.scope)));
$('reset').addEventListener('click',()=>{command({type:'scope',value:scope});resetWorkspace();});
$('native').addEventListener('click',()=>{command({type:'native',enabled:!current.native});nativePending=true;$('native').textContent='전환 중…';});
$('native-stop').addEventListener('click',()=>command({type:'native',enabled:false}));
$('pause').addEventListener('click',()=>command({type:'pause',enabled:!current.paused}));
$('mirror').addEventListener('click',()=>command({type:'camera',mirror:!current.camera.mirror}));
$('rotate').addEventListener('click',()=>command({type:'camera',rotation:(current.camera.rotation+90)%360}));
$('quality').addEventListener('change',()=>command({type:'camera',width:Number($('quality').value)}));
$('calibrate').addEventListener('click',()=>{command({type:'calibrate'});$('calibrate').disabled=true;});
$('show-help').addEventListener('click',()=>$('help').showModal());
$('close-help').addEventListener('click',()=>$('help').close());
$('save-record').addEventListener('click',()=>{command({type:'save'});$('notice').textContent='최근 동작 기록 저장을 요청했습니다.';});
$('open-records').addEventListener('click',()=>api.showRecords());
for(const id of ['area','stability','tap']){
  $(id).addEventListener('input',()=>$(id+'-value').textContent=$(id).value+'%');
  $(id).addEventListener('change',()=>command({type:'settings',[$(id).dataset.setting]:Number($(id).value)/100}));
}
function updateSettings(settings){
  for(const id of ['area','stability','tap'])if(document.activeElement!==$(id)){
    const v=Math.round(settings[$(id).dataset.setting]*100);$(id).value=v;$(id+'-value').textContent=v+'%';
  }
}
const edges=[[0,1,2,3,4],[0,5,6,7,8],[5,9,10,11,12],[9,13,14,15,16],[13,17,18,19,20],[17,0]];
function drawHand(points,resolution,area){
  const c=$('landmarks');c.width=640;c.height=Math.round(640*resolution[1]/resolution[0]);const g=c.getContext('2d');
  g.clearRect(0,0,c.width,c.height);
  if(area){g.strokeStyle='#d9f9b7a0';g.lineWidth=1.5;g.setLineDash([5,6]);g.strokeRect(area[0]*c.width,area[1]*c.height,(area[2]-area[0])*c.width,(area[3]-area[1])*c.height);g.setLineDash([]);}
  if(!points)return;
  g.strokeStyle='#b8e9c4a0';g.lineWidth=2;
  for(const chain of edges){g.beginPath();chain.forEach((i,n)=>{const p=points[i];n?g.lineTo(p[0]*c.width,p[1]*c.height):g.moveTo(p[0]*c.width,p[1]*c.height);});g.stroke();}
  for(let i=0;i<points.length;i++){g.beginPath();g.arc(points[i][0]*c.width,points[i][1]*c.height,i===8?8:3,0,Math.PI*2);g.fillStyle=i===8?'#e7fba3':'#c2efd0';g.fill();}
}
api.onEngine(packet=>{
  if(packet.type==='action'){applyAction(packet);return;}
  if(packet.type==='boot'){updateSettings(packet.settings);$('notice').textContent=packet.message;return;}
  if(packet.type==='error'){$('notice').textContent=packet.message;$('state-label').textContent='연결 확인이 필요합니다';$('state-reason').textContent=packet.message;$('native').disabled=true;return;}
  if(packet.type!=='frame'&&packet.type!=='status')return;
  current={...current,...packet};lastPacket=performance.now();nativePending=false;
  practiceMoving=!!packet.moving;
  if(packet.target&&practiceMoving)target=packet.target;
  else target=[...shown];
  document.body.classList.toggle('native-mode',current.native);
  $('native').innerHTML=current.native?'PC 제어 정지 <span>■</span>':'PC 제어 시작 <span>↗</span>';
  $('native-banner').hidden=!current.native;$('output-mode').textContent=current.native?'실제 PC 제어':'연습 공간';
  $('paused-cover').hidden=!current.paused;$('pause').textContent=current.paused?'다시 시작':'잠시 멈춤';
  $('state-label').textContent=packet.label||'손 대기';
  const reason=packet.reason||({waiting:'손 전체가 보이게 카메라 안으로',ready:'검지로 가리킨 자세를 잠시 유지',move:'노란 점이 검지 끝입니다',palm:'위아래 → 스크롤 · 좌우 → 잡기',settle:'커서 위치를 유지하며 손을 다시 연결',clutch:'검지를 가리킨 자세로 돌아오세요',zoom_done:'배율은 고정됨 · 손을 편하게 접으세요'})[packet.state]||'동작을 마치면 다시 이동할 수 있습니다';
  $('state-reason').textContent=reason;
  $('state-icon').textContent=({move:'↗',tap:'●',scroll:'↕',drag:'✥',zoom:'⤢',zoom_done:'✓'})[packet.state]||'◎';
  $('camera-badge').textContent=packet.tracking==='tracked'?'손 추적 중':'손 위치 확인';
  $('connection').innerHTML='<i></i>'+(packet.tracking==='tracked'?'손 연결됨':'카메라 연결됨');
  const pct=Math.round(Math.max(0,Math.min(1,(packet.tap_score||0)/.22))*100);$('press-fill').style.width=pct+'%';$('press-value').textContent=pct+'%';
  if(packet.fps!=null){$('fps').textContent=packet.fps;$('latency').textContent=packet.inference_ms;$('input-age').textContent=packet.age_ms;}
  if(packet.resolution)$('resolution').textContent=packet.resolution.join(' × ');
  if(packet.notice)$('notice').textContent=packet.notice;
  if(packet.settings)updateSettings(packet.settings);
  if(packet.camera&&document.activeElement!==$('quality'))$('quality').value=packet.camera.width;
  if(packet.preview){$('preview').src='data:image/jpeg;base64,'+packet.preview;$('camera-empty').hidden=true;drawHand(packet.landmarks,packet.resolution,packet.area);}
  $('calibration').hidden=!packet.calibration;$('calibrate').disabled=!!packet.calibration;
  if(packet.calibration){$('calibration-message').textContent=packet.calibration.message;$('calibration-progress').value=packet.calibration.progress;}
  if(!packet.dragging&&model.drag)endDrag();
  cursor.classList.toggle('pressed',packet.state==='tap');cursor.classList.toggle('dragging',!!packet.dragging);
});
function animate(now){
  const dt=Math.min(.1,(now-lastFrame)/1000);lastFrame=now;
  const fresh=now-lastPacket<300;
  cursor.hidden=current.native||current.paused||!fresh||['waiting','ready'].includes(current.state);
  const k=1-Math.exp(-dt/.022);
  if(practiceMoving&&fresh)shown=shown.map((v,i)=>v+(target[i]-v)*k);
  cursor.style.transform=`translate(${shown[0]*workspace.clientWidth}px,${shown[1]*workspace.clientHeight}px)`;
  if(model.drag&&!current.native)updateDrag(pointFor(shown));
  if(!fresh&&model.drag)endDrag();
  if(now-lastFeedback>30){command({type:'feedback',position:shown});lastFeedback=now;}
  requestAnimationFrame(animate);
}
requestAnimationFrame(animate);
