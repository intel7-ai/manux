import {VIEWS, VIEW_LABELS} from './orientation.js';

const $ = selector => document.querySelector(selector);
const tips = {
  front:'처음의 편한 정면 자세로 돌아오세요.',
  left:'손목을 돌려 손바닥 면이 왼쪽을 향하게 해주세요.',
  right:'손목을 돌려 손바닥 면이 오른쪽을 향하게 해주세요.',
  up:'손목을 기울여 손바닥 면이 위쪽을 향하게 해주세요.',
  down:'손목을 기울여 손바닥 면이 아래쪽을 향하게 해주세요.',
};
const targets = {front:[0,0],left:[-.68,0],right:[.68,0],up:[0,-.68],down:[0,.68]};
const ticks = [], rows = {};
const NS = 'http://www.w3.org/2000/svg';
for (let i = 0; i < 80; i++) {
  const angle = (i * 4.5 - 90) * Math.PI / 180;
  const tick = document.createElementNS(NS, 'line');
  for (const [name, value] of Object.entries({x1:200 + Math.cos(angle)*181,y1:200 + Math.sin(angle)*181,x2:200 + Math.cos(angle)*194,y2:200 + Math.sin(angle)*194})) tick.setAttribute(name,value.toFixed(2));
  tick.setAttribute('class','orbit-tick');
  $('#enroll-ring-ticks').append(tick); ticks.push(tick);
}
for (const view of VIEWS) {
  const row = document.createElement('li'), label = document.createElement('strong'), count = document.createElement('span');
  row.dataset.view = view; label.textContent = VIEW_LABELS[view]; count.textContent = '대기';
  row.append(label,count); $('#enroll-views').append(row); rows[view]={row,count};
}
const cap = value => Math.max(0,Math.min(1,Number.isFinite(value)?value:0));
function place(element,x,y) {element.style.left=`${50+x*38}%`;element.style.top=`${50+y*38}%`;}

/** Visual progress reflects only controller coverage, never elapsed wall time. */
export function renderAngleCoverage(state) {
  const calibrating = state.phase === 'calibrate';
  const verifying = state.mode === 'verify';
  const target = state.targetView || 'front';
  const coverage = state.views || {};
  let completed = 0;
  for (let index=0;index<VIEWS.length;index++) {
    const view = VIEWS[index], item = coverage[view] || {}, progress = cap(item.progress);
    const done = item.complete === true;
    if(done)completed++;
    const active = view===target;
    rows[view].row.classList.toggle('current',active);
    rows[view].row.classList.toggle('complete',done);
    rows[view].row.dataset.count = item.count || 0;
    rows[view].row.dataset.complete = done;
    rows[view].row.setAttribute('aria-current',active?'step':'false');
    rows[view].count.textContent=done?'완료':active&&calibrating?'기준 잡기':verifying?(progress?`${Math.round(progress*100)}%`:'대기'):`${item.count||0}/${item.required||state.minSamples||12}`;
    for(let n=0;n<16;n++) {
      const tick=ticks[index*16+n];
      tick.classList.toggle('filled',done||n<Math.floor(progress*16));
      tick.classList.toggle('current',active);
    }
  }
  $('#enroll-ring').setAttribute('aria-label',`${verifying?'확인':'수집'} ${completed}/5 각도 완료, 현재 ${VIEW_LABELS[target]}`);
  $('#enroll-orbit-title').textContent = calibrating?'정면 기준 잡는 중':`${VIEW_LABELS[target]} · ${verifying?'확인':'수집'}`;
  $('#enroll-orbit-progress').textContent=`${completed} / 5 각도 ${verifying?'확인':'수집'}`;
  $('#enroll-angle-title').textContent = calibrating?'정면 기준 잡기':`${VIEW_LABELS[target]}으로 ${verifying?'다시 보여주세요':'천천히 기울이세요'}`;
  $('#enroll-angle-hint').textContent=calibrating?'이 동작의 편한 정면 자세를 1초 유지하세요.':tips[target];
  const feedback=state.orientationFeedback || {};
  const [x,y]=targets[target];place($('#enroll-aim-target'),x,y);
  place($('#enroll-aim-current'),feedback.x||0,feedback.y||0);
  $('#enroll-aim').classList.toggle('matched',Boolean(feedback.matched));
  $('#enroll-aim-current').hidden=feedback.deltaYaw===null||feedback.deltaYaw===undefined;
  const reasons={
    'need-front-reference':'초록 원은 목표, 흰 점은 현재 기울기입니다.',
    'orientation-unavailable':'손목과 손 전체를 카메라에 보여주세요.',
    'palm-edge-on':'손이 너무 옆을 향합니다. 카메라 쪽으로 돌려주세요.',
    'face-camera':'손바닥 면을 카메라 쪽으로 조금 더 돌려주세요.',
    'front-not-facing-camera':'손바닥 면을 카메라 쪽으로 조금 더 돌려주세요.',
    'hand-changed':'처음 기준을 잡은 손으로 계속 진행하세요.',
    'wrong-hand':'처음 기준을 잡은 손으로 계속 진행하세요.',
    'different-hand':'처음 기준을 잡은 손으로 계속 진행하세요.',
    'handedness-unavailable':'한 손 전체를 보여주세요. 어느 손인지 확인하고 있습니다.',
    'verification-mismatch':'각도와 함께 등록할 때의 손 모양도 맞춰주세요.',
    'tilt-too-far':'너무 많이 기울였습니다. 정면 쪽으로 조금 돌아오세요.',
    'wrong-view':`현재 ${VIEW_LABELS[feedback.label]||'다른 각도'}입니다. ${VIEW_LABELS[target]} 목표를 맞춰주세요.`,
    'between-views':'손 위치를 옮기지 말고 손목 각도를 조금 조절하세요.',
    'calibration-unstable':'정면 기준을 잡을 때는 손목을 잠깐 고정하세요.',
    'front-moving':'정면 기준을 잡을 때는 손목을 잠깐 고정하세요.',
  };
  $('#enroll-angle-feedback').textContent=reasons[state.reason]||(feedback.matched?'목표 각도입니다. 손 모양을 유지하세요.':reasons[feedback.reason]||'손 관절에서 추정한 기울기로 안내합니다.');
  $('#enroll-overall').textContent=`전체 ${state.completedViews||0} / ${state.totalViews||25} 각도 ${verifying?'확인':'수집'} · 웹캠 기울기 추정`;
}
