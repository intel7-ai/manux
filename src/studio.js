// Input Studio: the Qt dashboard (hand-mouse/src/dashboard.qml, qt_dashboard.py) as the desktop app's
// main screen. Onboarding picks the hand once; the main screen controls the mouse with the chosen pointer.
import {VisionClient} from './vision-client.js';
import {cameraConstraints, describeCamera} from './camera.js';
import {getHandQuality, validateRegistrations} from './personalization.js';
import {SettingsStore} from './settings-store.js';
import {AirMouseController} from './air-mouse-controller.js';
import {IndexPointerController} from './index-pointer-controller.js';
import {TwoHandController} from './two-hand-controller.js';
import {LandmarkSmoother} from './landmark-smoother.js';
import {MouseSession} from './mouse-session.js';
import {HandSelection, HAND_LABELS, selectedDetection} from './hand-selection.js';
import {guideRows, guideNote, contactText} from './pointer-guide.js';
import {thumbContacts, thumbDistances} from './pointer-engine.js';
import {BrightnessSampler} from './camera-metrics.js';

const $ = selector => document.querySelector(selector);
const CONNECTIONS = [[0,1],[1,2],[2,3],[3,4],[0,5],[5,6],[6,7],[7,8],[5,9],[9,10],[10,11],[11,12],[9,13],[13,14],[14,15],[15,16],[13,17],[0,17],[17,18],[18,19],[19,20]];
const ACTION_TEXT = {move:'포인터 이동', left:'좌클릭 · 드래그', right:'우클릭', scroll:'스크롤 중'};

const store = new SettingsStore({validateRegistrations});
let settings = store.settings;
const makeController = () => {
  const {sensitivity, pointer} = settings.mouse;
  if (pointer.hands === 'two') return new TwoHandController({sensitivity, anchor:pointer.anchor});
  return pointer.engine === 'index'
    ? new IndexPointerController({sensitivity, clickStyle:pointer.click, anchor:pointer.anchor})
    : new AirMouseController({sensitivity});
};
const session = new MouseSession({controller:makeController(), onChange:render});
const selection = new HandSelection();
const brightness = new BrightnessSampler();
const smoother = new LandmarkSmoother();
const otherHand = side => side === 'left' ? 'right' : 'left';

let stage = 'splash', stream = null, vision = null, token = 0, ready = false, loop = 0;
let lastVideoTime = -1, lastInference = 0, lastResultAt = null, fps = 0, lastStatsAt = 0, cameraLabel = '';
let detected = false, contacts = [], details = false;

function save(change, failure) {
  try { settings = store.setMouse(change); return true; }
  catch (error) { session.say(`${failure} ${error.message}`, true); return false; }
}

/* ---------------------------------------------------------------- onboarding */
function setStage(next) {
  stage = next;
  const onboarding = $('#onboarding');
  onboarding.dataset.stage = next;
  onboarding.hidden = next === 'main';
  $('#studio').hidden = next !== 'main';
  if (next === 'welcome') {
    $('#onboarding-title').textContent = '안녕하세요';
    $('#onboarding-copy').textContent = '편하게 쓸 손을 한 번 정하면, 다음부터 바로 시작할 수 있어요.';
  }
  if (next === 'capture') {
    session.stop();
    selection.reset();
    $('#onboarding-title').textContent = '어느 손으로 조작할까요?';
    $('#onboarding-copy').textContent = '카메라에 사용할 손과 움직일 공간을 보여주세요.\n한 손만 5초 동안 보여주면 자동으로 시작합니다.';
    $('#onboarding-cancel').hidden = !settings.mouse.mainHand;
    $('#capture-host').append($('#preview-frame'));
    void startCamera();
  }
  if (next === 'main') {
    $('#preview-host').append($('#preview-frame'));
    void startCamera();
  }
  render();
}
$('#onboarding-start').addEventListener('click', () => setStage('capture'));
$('#onboarding-cancel').addEventListener('click', () => setStage('main'));
$('#hand-setup').addEventListener('click', () => setStage('capture'));

function observeCapture(result, now) {
  const hands = (result.handedness ?? []).map(h => ({handedness:h?.[0]?.categoryName, score:h?.[0]?.score}));
  const side = selection.observe(hands, now / 1000);
  const status = $('#capture-status');
  status.classList.toggle('found', Boolean(selection.side));
  status.textContent = selection.side
    ? `${HAND_LABELS[selection.side]} 감지됨 · ${Math.ceil(selection.secondsRemaining(now / 1000))}초 남음`
    : '한 손을 기다리는 중';
  if (side && save({mainHand:side}, '사용할 손을 저장하지 못했습니다.')) setStage('main');
}

/* ---------------------------------------------------------------- camera loop */
async function startCamera() {
  if (stream) return;
  const current = ++token;
  $('#camera-empty').textContent = '카메라를 준비하고 있습니다…';
  $('#camera-empty').hidden = false;
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('카메라는 HTTPS 또는 localhost 주소에서 사용할 수 있습니다.');
    const acquired = await navigator.mediaDevices.getUserMedia(cameraConstraints($('#resolution').value));
    if (current !== token) { acquired.getTracks().forEach(track => track.stop()); return; }
    stream = acquired;
    for (const track of stream.getVideoTracks()) track.addEventListener('ended', () => {
      if (current === token) { stopCamera(); session.say('카메라 연결이 끊겼습니다. 다시 연결한 뒤 설정에서 카메라를 다시 고르세요.', true); }
    });
    const camera = $('#camera');
    camera.srcObject = stream; await camera.play();
    if (current !== token) return;
    cameraLabel = describeCamera(stream.getVideoTracks()[0], camera, $('#resolution').value).label;
    const client = new VisionClient({numHands:settings.mouse.pointer.hands === 'two' ? 2 : 1}); vision = client; await client.init();
    if (current !== token) return;
    await client.recognize(camera, performance.now());   // shader warm-up before controls are offered
    if (current !== token) return;
    ready = true; lastVideoTime = -1; lastInference = 0; lastResultAt = null; fps = 0;
    $('#camera-empty').hidden = true;
    infer(current);
  } catch (error) {
    if (current !== token) return;
    stopCamera();
    const reasons = {NotAllowedError:'카메라 권한이 차단되었습니다.', NotFoundError:'연결된 카메라가 없습니다.', NotReadableError:'다른 앱이 카메라를 사용 중입니다.', OverconstrainedError:'카메라가 요청한 해상도를 지원하지 않습니다. 설정에서 낮춰 보세요.'};
    $('#camera-empty').textContent = reasons[error.name] || `카메라 또는 모델을 준비하지 못했습니다. ${error.message}`;
    $('#camera-empty').hidden = false;
  }
  render();
}

function stopCamera() {
  token++; ready = false;
  globalThis.manuxDesktop ? clearTimeout(loop) : cancelAnimationFrame(loop);
  session.stop();
  vision?.close(); vision = null;
  stream?.getTracks().forEach(track => track.stop()); stream = null;
  $('#camera').srcObject = null;
  const canvas = $('#landmarks'); canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
  detected = false; contacts = [];
  render();
}

function infer(current) {
  if (current !== token || !ready) return;
  // Desktop input keeps running while another app covers this window.
  loop = globalThis.manuxDesktop ? setTimeout(() => infer(current), 16) : requestAnimationFrame(() => infer(current));
  const camera = $('#camera'), now = performance.now();
  if (!vision || vision.busy || now - lastInference < 30 || camera.readyState < 2 || camera.currentTime === lastVideoTime) return;
  lastInference = now; lastVideoTime = camera.currentTime;
  vision.recognize(camera, now).then(packet => {
    if (!packet || current !== token || !ready) return;
    onResult(packet, performance.now(), performance.now() - now);
  }).catch(error => {
    if (current !== token) return;
    stopCamera(); session.say(`인식이 중단되었습니다. ${error.message}`, true);
  });
}

function onResult(packet, now, roundTripMs) {
  const result = packet.result, camera = $('#camera');
  const width = camera.videoWidth, height = camera.videoHeight;
  // Every hand is smoothed per physical side before drawing or judging (landmark jitter).
  const hands = (result.handedness ?? []).map((h, i) => {
    const side = h?.[0]?.categoryName, raw = result.landmarks?.[i];
    const key = side === 'Left' || side === 'Right' ? side.toLowerCase() : `hand-${i}`;
    return {handedness:side, score:h?.[0]?.score, landmarks:smoother.smooth(key, raw, now / 1000)};
  });
  for (const side of ['left', 'right']) if (!hands.some(h => h.handedness?.toLowerCase() === side)) smoother.reset(side);
  drawHands(hands.map(h => h.landmarks), width, height);
  if (lastResultAt !== null) fps = fps ? fps * .8 + 1000 / (now - lastResultAt) * .2 : 1000 / (now - lastResultAt);
  lastResultAt = now;
  if (now - lastStatsAt > 500) {
    lastStatsAt = now;
    showBrightness(brightness.sample(camera));
    $('#details').textContent = `${fps.toFixed(1)} fps  ·  추론 ${packet.inferenceMs.toFixed(1)} ms  ·  왕복 ${Math.round(roundTripMs)} ms${triggerText()}`;
  }
  if (stage === 'capture') { observeCapture(result, now); return; }
  if (stage !== 'main') return;
  const usable = side => {
    const hand = selectedDetection(hands, side)?.landmarks;
    return hand && getHandQuality(hand, {width, height}).ok ? hand : null;
  };
  const mainHand = settings.mouse.mainHand;
  if (settings.mouse.pointer.hands === 'two') {
    // The chosen hand moves; the other hand only acts.
    const moveHand = usable(mainHand), actionHand = usable(otherHand(mainHand));
    detected = Boolean(moveHand);
    contacts = actionHand ? thumbContacts(thumbDistances(actionHand, height / width)) : [];
    session.update({moveHand, actionHand, now, width, height, latency:roundTripMs});
  } else {
    // Only the chosen hand controls the pointer; any other hand counts as no hand.
    const landmarks = usable(mainHand);
    detected = Boolean(landmarks);
    contacts = landmarks ? thumbContacts(thumbDistances(landmarks, height / width)) : [];
    session.update({landmarks, now, quality:Boolean(landmarks), handedness:landmarks ? mainHand : null, width, height, latency:roundTripMs});
  }
  render();
}

function drawHands(hands, width, height) {
  const canvas = $('#landmarks'), ctx = canvas.getContext('2d');
  if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = '#46e5bd'; ctx.lineWidth = Math.max(2, width / 400); ctx.fillStyle = '#fff';
  for (const hand of hands ?? []) {
    for (const [a, b] of CONNECTIONS) { ctx.beginPath(); ctx.moveTo(hand[a].x * width, hand[a].y * height); ctx.lineTo(hand[b].x * width, hand[b].y * height); ctx.stroke(); }
    for (const p of hand) { ctx.beginPath(); ctx.arc(p.x * width, p.y * height, Math.max(2, width / 350), 0, Math.PI * 2); ctx.fill(); }
  }
}

function showBrightness(reading) {
  const chip = $('#chip-light');
  chip.hidden = reading.percent === null;
  chip.textContent = reading.percent === null ? '' : `${reading.lowLight ? '어두움' : '밝기'} ${reading.percent}%`;
  chip.classList.toggle('warn', reading.lowLight);
  $('#low-light').hidden = !reading.lowLight;
}

function triggerText() {
  const two = session.controller instanceof TwoHandController;
  if (two) {
    const [index, middle] = session.controller.contacts ?? [];
    const f = v => Number.isFinite(v) ? v.toFixed(2) : '—';
    const {press, release} = session.controller.settings;
    return `
${HAND_LABELS[otherHand(settings.mouse.mainHand)] ?? '동작 손'} 엄지–검지 ${f(index)} · 엄지–중지 ${f(middle)} (누름 ≤ ${press} · 놓기 ≥ ${release})`;
  }
  const trigger = session.controller.engine?.trigger;
  if (!trigger || settings.mouse.pointer.click !== 'trigger') return '';
  const ratio = Number.isFinite(trigger.ratio) ? trigger.ratio.toFixed(2) : '—';
  return `\n방아쇠 거리 ${ratio} (누름 ≤ ${trigger.settings.trigger_press_ratio} · 놓기 ≥ ${trigger.settings.trigger_release_ratio}) · ${{idle:'대기', approach:'접근', press:'누름', drag:'드래그'}[trigger.state] ?? trigger.state}`;
}

/* ---------------------------------------------------------------- main screen */
function setPointer(setting, value) {
  if (settings.mouse.pointer[setting] === value) return;
  const hands = settings.mouse.pointer.hands;
  if (!save({pointer:{[setting]:value}}, '포인터 설정을 저장하지 못했습니다.')) return;
  session.setController(makeController());
  // One or two tracked hands is a MediaPipe option: restart the camera to apply it.
  if (setting === 'hands' && hands !== value && stream) { stopCamera(); void startCamera(); }
  session.say('설정을 바꿔 입력을 멈췄습니다. 다시 제어 시작을 누르세요.');
}
document.querySelectorAll('.segmented[data-setting] button[data-value]').forEach(button => button.addEventListener('click', () => {
  setPointer(button.closest('.segmented').dataset.setting, button.dataset.value);
}));
$('#control-toggle').addEventListener('click', () => { void session.toggle(); });
$('#overlay-toggle').addEventListener('change', () => { void session.setOverlay($('#overlay-toggle').checked); });
$('#details-toggle').addEventListener('click', () => { details = !details; render(); });

const dialog = $('#settings-dialog');
$('#settings-open').addEventListener('click', () => { session.stop(); dialog.showModal(); });
$('#settings-close').addEventListener('click', () => dialog.close());
$('#sensitivity').addEventListener('input', () => { $('#sensitivity-output').textContent = `${Math.round(Number($('#sensitivity').value) / 18)}%`; });
$('#sensitivity').addEventListener('change', () => {
  if (save({sensitivity:Number($('#sensitivity').value)}, '감도를 저장하지 못했습니다.')) session.setController(makeController());
});
$('#sensitivity-reset').addEventListener('click', () => {
  $('#sensitivity').value = '1800'; $('#sensitivity').dispatchEvent(new Event('input')); $('#sensitivity').dispatchEvent(new Event('change'));
});
$('#resolution').addEventListener('change', () => { stopCamera(); void startCamera(); });

document.addEventListener('keydown', event => {
  if (stage !== 'main' || dialog.open || event.repeat || event.target?.closest?.('input, select, textarea, button')) {
    if (event.key === 'Escape' && stage === 'main') session.stop('Esc를 눌러 제어를 껐습니다.');
    return;
  }
  if (event.code === 'Space') { event.preventDefault(); if (ready) void session.toggle(); }
  else if (event.key === 'Escape') session.stop('Esc를 눌러 제어를 껐습니다.');
  else if (event.key === 'o' || event.key === 'O') { session.stop(); dialog.showModal(); }
});
document.addEventListener('visibilitychange', () => {
  // The desktop app keeps controlling Windows in the background; a browser preview stops.
  if (document.hidden && !(session.enabled && session.desktop)) session.stop();
});
window.addEventListener('beforeunload', () => { stopCamera(); session.close(); });

function render() {
  const {pointer, mainHand} = settings.mouse;
  for (const group of document.querySelectorAll('.segmented[data-setting]')) {
    for (const button of group.querySelectorAll('button')) {
      button.classList.toggle('selected', button.dataset.value === pointer[group.dataset.setting]);
      if (button.dataset.value) button.disabled = session.pending;
    }
  }
  const two = pointer.hands === 'two';
  $('#row-engine').hidden = two;
  $('#row-click').hidden = two || pointer.engine !== 'index';
  $('#row-anchor').hidden = !two && pointer.engine !== 'index';
  const names = {move:HAND_LABELS[mainHand] ?? '오른손', act:HAND_LABELS[otherHand(mainHand)] ?? '왼손'};
  const list = $('#guide-list');
  list.replaceChildren(...guideRows(pointer, names).flatMap(([gesture, result]) => {
    const dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = gesture; dd.textContent = result; return [dt, dd];
  }));
  $('#guide-note').textContent = guideNote(pointer);
  $('#contacts').hidden = !details;
  $('#contacts').textContent = `0 엄지 · 1 검지 · 2 중지 · 3 약지 · 4 새끼\n조합: ${contactText(contacts)}`;
  $('#details').hidden = !details;
  $('#details-toggle').textContent = details ? '상세 닫기' : '상세 정보';
  $('#hand-setup').textContent = mainHand ? `${HAND_LABELS[mainHand]} · 다시 설정` : '사용할 손 설정';
  $('#control-toggle').textContent = session.enabled ? '일시정지' : '제어 시작';
  $('#control-toggle').disabled = !ready || session.pending;
  $('#overlay-toggle').disabled = !session.desktop;
  $('#overlay-toggle').checked = session.overlay;
  $('#chip-mode').hidden = !cameraLabel; $('#chip-mode').textContent = cameraLabel;
  $('#chip-tracking').textContent = detected ? '손 감지됨' : '손 대기';
  $('#input-state').textContent = !session.enabled ? '입력 꺼짐' : session.desktop ? '입력 활성' : '미리보기 (데스크톱 앱 필요)';
  const state = session.state;
  $('#current-action').textContent = !session.enabled ? '제어 시작을 누르면 손짓으로 조작할 수 있어요.'
    : !detected ? `${mainHand ? HAND_LABELS[mainHand] : '손'}을 카메라에 보여주세요.`
    : state?.phase === 'action-hand-missing' ? `포인터 이동 · ${names.act}이 보여야 클릭할 수 있어요`
    : state?.phase === 'holding' ? '커서 고정 · 검지를 펴면 이동'
    : state?.phase === 'latched' ? '포인터 이동 · 멈추면 클릭할 수 있어요'
    : ACTION_TEXT[state?.mode] ?? '손짓 대기';
  $('#message').textContent = session.message;
  $('#message').classList.toggle('error', session.error);
  $('#sensitivity').value = String(settings.mouse.sensitivity);
  $('#sensitivity-output').textContent = `${Math.round(settings.mouse.sensitivity / 18)}%`;
}

if (store.error) session.say(store.error, true);
render();
// Splash for 0.7 s like the Qt app, then the hand question on first run.
setTimeout(() => setStage(settings.mouse.mainHand ? 'main' : 'welcome'), 700);
