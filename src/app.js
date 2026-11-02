import {GestureController} from './gesture-controller.js';
import {applyMediaAction, ACTION_NAMES, formatTime} from './media-controller.js';
import {VisionClient} from './vision-client.js';
import {cameraConstraints, describeCamera} from './camera.js';
import {extractFeatures, getHandQuality, trainEnvironment, classifyPersonal} from './personalization.js';
import {readProfiles, writeProfiles} from './profile-store.js';
import {EnrollmentController} from './enrollment.js';
import {extractOrientation, VIEW_LABELS} from './orientation.js';
import {renderAngleCoverage} from './enrollment-view.js';
import {MouseWorkspace} from './mouse-workspace.js';

const $ = selector => document.querySelector(selector);
const player = $('#player'), camera = $('#camera'), canvas = $('#landmarks');
const ctx = canvas.getContext('2d');
const cameraPreview = $('.camera-wrap'), enrollmentDialog = $('#enrollment');
const poses = {
  Open_Palm: {name:'손바닥 · 일시정지', icon:'✋', tip:'손가락 다섯 개를 자연스럽게 펴서 보여주세요.'},
  Thumb_Up: {name:'엄지척 · 재생', icon:'👍', tip:'엄지만 위로 세우고 나머지 손가락은 접으세요.'},
  Victory: {name:'V 모양 · 10초 앞으로', icon:'✌️', tip:'검지와 중지로 V 모양을 만들어주세요.'},
  Closed_Fist: {name:'주먹 · 10초 뒤로', icon:'✊', tip:'주먹을 쥐고 손 전체가 보이게 해주세요.'},
  None: {name:'중립 · 명령 없음', icon:'☝️', tip:'검지만 펼쳐주세요. 이 자세에서는 아무 명령도 실행하지 않습니다.'},
};
const connections = [[0,1],[1,2],[2,3],[3,4],[0,5],[5,6],[6,7],[7,8],[5,9],[9,10],[10,11],[11,12],[9,13],[13,14],[14,15],[15,16],[13,17],[0,17],[17,18],[18,19],[19,20]];
const stored = readProfiles();
let profiles = stored.profiles;
let gate = new GestureController(), vision = null, stream = null, ready = false;
let session = 0, raf = 0, lastVideoTime = -1, lastInference = 0, mediaUrl = null, actionBusy = false;
let cameraInfo = null, lastResultAt = null, fps = 0, lastStatsAt = 0;
let enrollment = null, enrollmentModel = null, enrollmentContext = null, enrollmentError = null;
let previewMoveId = 0;
let controlMode = 'media';
const mouse = new MouseWorkspace();

function switchMode(mode) {
  disableGestures(); cancelEnrollment('제어 모드를 바꿔 등록을 취소했습니다.');
  controlMode = mode; mouse.setActive(mode === 'mouse');
  document.querySelectorAll('.media-only').forEach(el => { el.hidden = mode !== 'media'; });
  $('#mouse-panel').hidden = mode !== 'mouse';
  $('#mode-media').setAttribute('aria-selected', String(mode === 'media'));
  $('#mode-mouse').setAttribute('aria-selected', String(mode === 'mouse'));
  $('#hold-progress').style.width = '0%';
  if (mode === 'mouse') player.pause();
}
$('#mode-media').addEventListener('click', () => switchMode('media'));
$('#mode-mouse').addEventListener('click', () => switchMode('mouse'));

function message(id, text, error = false) {
  $(id).textContent = text;
  $(id).classList.toggle('error', error);
}
function badge(text, kind = '') {
  $('#camera-badge').textContent = text;
  $('#camera-badge').className = `status-pill ${kind}`;
}
function currentProfile() { return profiles.find(p => p.id === $('#profile-select').value); }
function environmentName(key = $('#environment').value) { return key === 'sofa' ? '소파 거리' : '책상 앞'; }
function currentEnvironment() { return currentProfile()?.environments[$('#environment').value]; }
function usableEnvironment() {
  const env = currentEnvironment();
  return env?.verified === true && env.model?.usable === true ? env : null;
}
function personalModel() { return $('#personal-enabled').checked ? usableEnvironment()?.model : null; }
function disableGestures() {
  gate.reset();
  $('#gesture-enabled').checked = false;
  $('#hold-progress').style.width = '0%';
  document.querySelectorAll('[data-gesture]').forEach(el => el.classList.remove('active'));
}
function syncMedia() {
  const ok = player.readyState >= 1 && Number.isFinite(player.duration) && player.duration > 0;
  document.querySelectorAll('[data-action]').forEach(b => b.disabled = !ok);
  $('#gesture-enabled').disabled = !ready || !ok || Boolean(enrollment);
  $('#time-display').textContent = `${formatTime(player.currentTime)} / ${formatTime(player.duration)}`;
  if (!ok) disableGestures();
}
function log(action, result, source) {
  const list = $('#event-log');
  list.querySelector('.empty-log')?.remove();
  const row = document.createElement('li'), time = document.createElement('time');
  const title = document.createElement('strong'), detail = document.createElement('span');
  time.textContent = new Date().toLocaleTimeString('ko-KR', {hour12:false});
  title.textContent = `${source} · ${ACTION_NAMES[action]}`;
  detail.className = 'result';
  detail.textContent = `${formatTime(result.before)} → ${formatTime(result.after)}`;
  row.append(time, title, detail); list.prepend(row);
  while (list.children.length > 6) list.lastElementChild.remove();
}
async function execute(action, source) {
  if (actionBusy) return;
  actionBusy = true;
  try {
    const result = await applyMediaAction(player, action);
    log(action, result, source);
    message('#media-message', `${ACTION_NAMES[action]} 실행 · ${formatTime(result.after)}`);
    syncMedia();
  } catch (error) {
    message('#media-message', error.name === 'NotAllowedError' ? '재생 버튼을 한 번 눌러 브라우저 재생을 허용하세요.' : error.message, true);
    if (source !== '버튼') disableGestures();
  } finally { actionBusy = false; }
}
function setVideo(src, name) {
  disableGestures(); player.pause();
  if (mediaUrl) { URL.revokeObjectURL(mediaUrl); mediaUrl = null; }
  player.src = src; player.load();
  $('#video-empty').hidden = true;
  $('#video-name').textContent = name;
  message('#media-message', '영상을 읽는 중입니다.'); syncMedia();
}
$('#video-file').addEventListener('change', event => {
  const file = event.target.files?.[0]; if (!file) return;
  const url = URL.createObjectURL(file); setVideo(url, file.name); mediaUrl = url;
});
$('#demo-load').addEventListener('click', () => setVideo('/media/demo.webm', 'MANUX 90초 테스트 영상'));
player.addEventListener('loadedmetadata', () => { syncMedia(); message('#media-message', '영상 준비 완료. 카메라 시작 후 손동작 제어를 켜세요.'); });
for (const event of ['timeupdate','durationchange','emptied']) player.addEventListener(event, syncMedia);
player.addEventListener('error', () => { syncMedia(); message('#media-message', '영상을 열 수 없습니다. MP4(H.264) 또는 WebM 파일을 선택하세요.', true); $('#video-empty').hidden = false; });
document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => execute(button.dataset.action, '버튼')));
$('#clear-log').addEventListener('click', () => { $('#event-log').replaceChildren(); });

function configureGate() {
  disableGestures();
  gate = new GestureController({holdMs:Number($('#hold-time').value), minConfidence:Number($('#min-confidence').value) / 100, repeatEnabled:$('#repeat-enabled').checked, repeatIntervalMs:1000});
  $('#hold-output').textContent = `${(Number($('#hold-time').value) / 1000).toFixed(2)}초`;
  $('#confidence-output').textContent = `${$('#min-confidence').value}%`;
  message('#camera-message', '설정을 적용했습니다. 손동작 제어를 다시 켜세요.');
}
$('#hold-time').addEventListener('input', configureGate);
$('#min-confidence').addEventListener('input', configureGate);
$('#repeat-enabled').addEventListener('change', configureGate);
$('#gesture-enabled').addEventListener('change', () => {
  gate.reset();
  message('#camera-message', $('#gesture-enabled').checked ? '제어 중입니다. 동작을 짧게 유지하세요. 다른 동작으로 바로 바꿀 수 있습니다.' : '인식 미리보기만 표시합니다.');
});

function persist(next) {
  writeProfiles(next); // Commit in memory only after storage succeeds.
  profiles = next;
}
function renderProfiles(selected = $('#profile-select').value) {
  const select = $('#profile-select');
  select.replaceChildren(new Option('공통 모델 사용', ''));
  for (const profile of profiles) select.add(new Option(profile.name, profile.id));
  select.value = profiles.some(p => p.id === selected) ? selected : '';
  refreshProfile(true);
}
function refreshProfile(enablePersonal = false) {
  const profile = currentProfile(), env = usableEnvironment();
  $('#personal-enabled').disabled = !env || Boolean(enrollment);
  if (!env) $('#personal-enabled').checked = false;
  else if (enablePersonal) $('#personal-enabled').checked = true;
  $('#profile-delete').disabled = !profile || Boolean(enrollment);
  $('#enroll-start').disabled = !profile || !ready || Boolean(enrollment) || Boolean(stored.error);
  $('#enroll-start').textContent = env ? '이 위치 다시 등록' : '이 위치에서 동작 등록';
  $('#profile-mode').textContent = personalModel() ? `${environmentName()} · 개인 등록` : '공통 모델';
  $('#profile-status').textContent = profile
    ? `책상 ${profile.environments.desk?.verified ? '등록됨' : '미등록'} · 소파 ${profile.environments.sofa?.verified ? '등록됨' : '미등록'}${!env ? ' / 현재 위치는 공통 모델 사용' : ''}`
    : '사용자를 추가한 뒤 두 위치에서 각각 등록하세요.';
  $('#profile-select').disabled = Boolean(enrollment);
  $('#environment').disabled = Boolean(enrollment);
  $('#profile-add').disabled = Boolean(enrollment) || Boolean(stored.error) || profiles.length >= 12;
}
$('#profile-form').addEventListener('submit', event => {
  event.preventDefault();
  const name = $('#profile-name').value.trim(); if (!name || enrollment || stored.error) return;
  try {
    const profile = {version:1, id:crypto.randomUUID(), name, createdAt:new Date().toISOString(), environments:{}};
    persist([...profiles, profile]); disableGestures(); renderProfiles(profile.id);
    $('#profile-name').value = '';
    message('#profile-message', `${name} 사용자 생성 완료. 카메라를 켜고 현재 위치에서 등록하세요.`);
  } catch (error) { message('#profile-message', `저장하지 못했습니다. ${error.message}`, true); }
});
$('#profile-select').addEventListener('change', () => { disableGestures(); refreshProfile(true); });
$('#environment').addEventListener('change', () => { disableGestures(); refreshProfile(true); message('#profile-message', `${environmentName()} 설정을 선택했습니다. 손동작 제어를 다시 켜세요.`); });
$('#personal-enabled').addEventListener('change', () => { disableGestures(); refreshProfile(); });
$('#profile-delete').addEventListener('click', () => {
  const profile = currentProfile();
  if (!profile || !window.confirm(`${profile.name}의 책상·소파 등록을 이 브라우저에서 삭제할까요?`)) return;
  try { persist(profiles.filter(p => p.id !== profile.id)); disableGestures(); renderProfiles(''); message('#profile-message', '사용자 등록을 삭제했습니다.'); }
  catch (error) { message('#profile-message', `삭제 내용을 저장하지 못했습니다. ${error.message}`, true); }
});

function moveCameraPreview(host) {
  const moveId = ++previewMoveId;
  host.append(cameraPreview);
  // Moving a media element can pause playback. Keep the same camera stream.
  if (ready && camera.srcObject) {
    const token = session;
    void camera.play().catch(error => {
      if (token !== session || moveId !== previewMoveId) return;
      stopCamera(); message('#camera-message', `카메라 미리보기를 다시 시작하지 못했습니다. ${error.message}`, true);
    });
  }
}
function cancelEnrollment(reason = '') {
  if (!enrollment) return;
  enrollment.cancel(); enrollment = null; enrollmentModel = null; enrollmentContext = null; enrollmentError = null;
  moveCameraPreview($('#camera-preview-home'));
  enrollmentDialog.close(); enrollmentDialog.hidden = true;
  refreshProfile(); syncMedia();
  if (ready && !document.hidden) $('#enroll-start').focus({preventScroll:true});
  if (reason) message('#profile-message', reason);
}
function beginEnrollment() {
  mouse.stop();
  const profile = currentProfile(); if (!profile || !ready || enrollment) return;
  disableGestures();
  enrollment = new EnrollmentController({prepareMs:$('#environment').value === 'sofa' ? 5000 : 3000, captureMs:2000, sampleIntervalMs:150, minSamplesPerView:12});
  enrollmentModel = null; enrollmentError = null;
  enrollmentContext = {profileId:profile.id, environment:$('#environment').value, resolution:{width:camera.videoWidth, height:camera.videoHeight}};
  enrollment.start({now:performance.now(), environment:enrollmentContext.environment});
  enrollmentDialog.hidden = false;
  $('#enroll-dialog-title').textContent = `${profile.name} · ${environmentName()} 동작 등록`;
  message('#enroll-quality', '손 전체가 보이도록 위치를 맞춰주세요.');
  moveCameraPreview($('#enroll-preview-host'));
  enrollmentDialog.showModal(); refreshProfile(); syncMedia();
  message('#profile-message', `${environmentName()}에 앉아 한 손으로 따라 해주세요. 등록하는 동안 손동작 명령은 멈춥니다.`);
  renderEnrollment(enrollment.snapshot());
}
function renderEnrollment(state) {
  enrollmentDialog.dataset.phase = state.phase;
  enrollmentDialog.dataset.pose = state.pose;
  enrollmentDialog.dataset.view = state.targetView;
  enrollmentDialog.dataset.mode = state.mode;
  enrollmentDialog.dataset.sampleCount = state.sampleCount;
  const pose = poses[state.pose];
  const verifying = state.mode === 'verify';
  $('#enroll-stage').textContent = enrollmentError ? '등록 보류' : state.phase === 'failed' ? '이 각도 다시 확인' : verifying ? '새 수행으로 확인' : state.phase === 'calibrate' ? '정면 기준 잡기' : '각도별 표본 수집';
  $('#enroll-step').textContent = `동작 ${state.poseIndex + 1}/5 · 각도 ${state.viewIndex + 1}/5`;
  $('#enroll-icon').textContent = pose.icon;
  $('#enroll-pose').textContent = pose.name;
  $('#enroll-instruction').textContent = enrollmentError || (state.phase.includes('prepare') ? `${pose.tip} 곧 시작합니다.` : state.phase === 'failed' ? '손 전체와 조명을 확인한 뒤 이 단계를 다시 시도하세요.' : pose.tip);
  $('#enroll-countdown').textContent = state.phase.includes('prepare') ? `${Math.ceil(state.remainingMs / 1000)}초` : state.phase === 'failed' ? '보류' : state.phase === 'calibrate' ? '기준' : verifying ? '확인' : '유지';
  $('#enroll-progress').value = state.progress;
  $('#enroll-retry').hidden = state.phase !== 'failed' && !enrollmentError;
  $('#enroll-retry').textContent = enrollmentError ? (state.phase === 'verified' ? '저장 다시 시도' : '처음부터 다시 등록') : '이 각도 다시 시도';
  const currentView = state.views?.[state.targetView];
  $('#enroll-samples').textContent = state.phase === 'calibrate' ? `정면 안정 ${((state.calibration?.durationMs || 0)/1000).toFixed(1)} / 1.0초 · 기준 표본 ${state.sampleCount}개`
    : state.phase === 'capture' ? `유효 표본 ${state.sampleCount} / ${state.minSamples}개 · 각도 유지 ${((currentView?.durationMs || 0)/1000).toFixed(1)} / 2.0초`
    : state.phase === 'verify' ? `새 관찰 ${state.sampleCount}회 · 이 각도에서 등록 동작을 확인합니다.`
    : state.phase === 'failed' ? `${VIEW_LABELS[state.targetView]} 조건을 충분히 확인하지 못했습니다. 이 각도만 다시 시도할 수 있습니다.`
    : enrollmentError ? '안내를 확인하고 다시 시도하세요.' : '카메라를 보면서 다음 자세를 준비하세요.';
  renderAngleCoverage(state);
}
function saveEnrollment(state) {
  try {
    const index = profiles.findIndex(p => p.id === enrollmentContext.profileId);
    if (index < 0) throw new Error('등록할 사용자를 찾을 수 없습니다.');
    const next = structuredClone(profiles), environment = enrollmentContext.environment;
    next[index].environments[environment] = {
      model:enrollmentModel, samples:state.samplesByPose || enrollment.getSamples(), verified:true,
      verifiedAt:new Date().toISOString(), verification:state.verification,
      resolution:enrollmentContext.resolution, createdAt:new Date().toISOString(),
      enrollmentVersion:2, orientationBaselines:state.orientationBaselines,
      viewOrder:['front','left','right','up','down'],
      viewSampleCounts:Object.fromEntries(Object.entries(state.sampleGroupsByPose).map(([pose,views])=>[pose,Object.fromEntries(Object.entries(views).map(([view,rows])=>[view,rows.length]))])),
    };
    persist(next);
    const profileName = next[index].name;
    cancelEnrollment(); refreshProfile(true);
    message('#profile-message', `${profileName} · ${environmentName(environment)} 등록 및 확인 완료. 개인 등록을 적용했습니다. 손동작 제어를 켜서 사용하세요.`);
  } catch (error) {
    enrollmentError = `저장하지 못했습니다. ${error.message}`;
    renderEnrollment(state); message('#profile-message', enrollmentError, true);
  }
}
function advanceEnrollment(now, observation = {}) {
  if (!enrollment || enrollmentError) return;
  let state = enrollment.update({now, ...observation});
  if (state.phase === 'captured') {
    enrollmentModel = trainEnvironment(state.samplesByPose || enrollment.getSamples(), {viewSamples:state.sampleGroupsByPose});
    if (!enrollmentModel.usable) {
      const warning = enrollmentModel.warnings?.[0] || '서로 구분되는 손 모양으로 다시 등록하세요.';
      enrollmentError = `등록된 동작을 충분히 구분하지 못했습니다. ${warning}`;
      message('#profile-message', enrollmentError, true);
    } else state = enrollment.startVerification({now});
  }
  if (state.phase === 'verified') { saveEnrollment(state); return; }
  renderEnrollment(state);
}
$('#enroll-start').addEventListener('click', beginEnrollment);
$('#enroll-cancel').addEventListener('click', () => cancelEnrollment('등록을 취소했습니다. 기존에 저장한 등록은 유지됩니다.'));
enrollmentDialog.addEventListener('cancel', event => {
  event.preventDefault(); cancelEnrollment('Esc를 눌러 등록을 취소했습니다.');
});
$('#enroll-retry').addEventListener('click', () => {
  if (!enrollment) return;
  if (enrollmentError) {
    if (enrollment.snapshot().phase === 'verified') saveEnrollment(enrollment.snapshot());
    else { cancelEnrollment(); beginEnrollment(); }
  } else renderEnrollment(enrollment.retry({now:performance.now()}));
});

function drawHands(landmarks) {
  const width = camera.videoWidth || 640, height = camera.videoHeight || 480;
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width; canvas.height = height;
    cameraPreview.style.aspectRatio = `${width} / ${height}`;
    cameraPreview.style.setProperty('--camera-aspect', width / height);
  }
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = '#46e5bd'; ctx.lineWidth = Math.max(2, width / 400); ctx.fillStyle = '#fff';
  for (const hand of landmarks || []) {
    for (const [a,b] of connections) {
      ctx.beginPath(); ctx.moveTo(hand[a].x * width, hand[a].y * height); ctx.lineTo(hand[b].x * width, hand[b].y * height); ctx.stroke();
    }
    for (const point of hand) { ctx.beginPath(); ctx.arc(point.x * width, point.y * height, Math.max(2, width / 350), 0, Math.PI * 2); ctx.fill(); }
  }
}
function qualityText(quality, handPresent) {
  if (!handPresent) return '손 전체를 보여주세요.';
  if (quality.reason === 'hand-too-small') return '손이 너무 작게 보입니다. 손을 앞으로 내밀거나 카메라에 가까이 와주세요.';
  if (quality.reason === 'near-frame-edge') return '손이 화면 가장자리에서 잘립니다. 손 전체를 안쪽으로 옮겨주세요.';
  return quality.ok ? `손 크기 ${Math.round(quality.spanPx)}px · 입력 조건 통과` : '손 모양을 확인하는 중입니다.';
}
function processResult(packet, now, roundTripMs) {
  const result = packet.result, landmarks = result.landmarks?.[0];
  const handPresent = Boolean(landmarks), best = result.gestures?.[0]?.[0];
  const dimensions = {width:camera.videoWidth, height:camera.videoHeight};
  const quality = getHandQuality(landmarks, dimensions);
  const features = quality.ok ? extractFeatures(landmarks, dimensions) : null;
  const model = personalModel(), personal = model ? classifyPersonal(features, model) : null;
  let gesture = model ? (personal.accepted ? personal.gesture : null) : best?.categoryName;
  const score = model ? (personal.accepted ? 1 : 0) : best?.score || 0;
  const neutral = model ? personal.accepted && gesture === 'None' : ['None','Pointing_Up','Thumb_Down','ILoveYou'].includes(gesture) && score >= Number($('#min-confidence').value) / 100;
  drawHands(result.landmarks);
  $('#recognition-label').textContent = !handPresent ? '손을 보여주세요' : !quality.ok ? '입력 보류' : poses[gesture]?.name || (model ? '등록과 다른 손 모양' : '다른 손 모양');
  $('#confidence').textContent = !handPresent ? '' : model ? (personal.accepted ? '개인 등록 판정' : '개인 등록 불일치') : `모델 점수 ${Math.round(score * 100)}%`;
  if (lastResultAt !== null) fps = fps ? fps * .8 + 1000 / (now - lastResultAt) * .2 : 1000 / (now - lastResultAt);
  lastResultAt = now;
  if (now - lastStatsAt > 500) {
    lastStatsAt = now;
    $('#camera-stats').textContent = `실제 입력 ${cameraInfo.label}${cameraInfo.reduced ? ' · 요청보다 낮음' : ''} | 처리 ${fps.toFixed(1)} fps · 모델 ${Math.round(packet.inferenceMs)}ms · 왕복 ${Math.round(roundTripMs)}ms`;
  }
  if (enrollment) {
    const match = enrollmentModel ? classifyPersonal(features, enrollmentModel) : undefined;
    const orientation = extractOrientation(landmarks, {...dimensions,mirrored:true});
    const handedness = result.handedness?.[0]?.[0]?.categoryName || null;
    advanceEnrollment(now, {features, quality, orientation, handedness, ...(match ? {match} : {})});
    if (enrollment) {
      const state = enrollment.snapshot();
      $('#recognition-label').textContent = !handPresent ? '손을 보여주세요' : quality.ok ? '손 추적 중' : '입력 보류';
      $('#confidence').textContent = state.phase === 'capture' ? (state.orientationFeedback?.matched ? `${VIEW_LABELS[state.targetView]} 수집 중` : '각도 맞추기') : state.mode === 'verify' ? '각도별 확인 중' : '정면 기준 준비';
      message('#enroll-quality', qualityText(quality, handPresent), !quality.ok);
      $('.enroll-preview-pane').dataset.quality = quality.ok ? 'ok' : 'waiting';
    }
    return;
  }
  if (controlMode === 'mouse') {
    const state = mouse.update({landmarks, now, quality:quality.ok, handedness:result.handedness?.[0]?.[0]?.categoryName || null, ...dimensions, latency:roundTripMs});
    $('#recognition-label').textContent = !handPresent ? '손을 보여주세요' : !quality.ok ? '입력 보류' : $('#mouse-mode').textContent;
    $('#confidence').textContent = '손바닥 위치 · 손가락 간격';
    $('#hold-progress').style.width = `${Math.round((state?.progress || 0) * 100)}%`;
    if (!quality.ok) message('#camera-message', qualityText(quality, handPresent));
    else message('#camera-message', '손바닥 중심으로 이동합니다. 손가락을 붙여 클릭·스크롤을 사용하세요.');
    return;
  }
  const state = gate.update({gesture, score, now, handPresent, neutral, quality:quality.ok, enabled:$('#gesture-enabled').checked});
  $('#hold-progress').style.width = `${Math.round(state.progress * 100)}%`;
  document.querySelectorAll('[data-gesture]').forEach(el => el.classList.toggle('active', quality.ok && el.dataset.gesture === gesture && score >= gate.minConfidence));
  if (!quality.ok) message('#camera-message', qualityText(quality, handPresent));
  else if ($('#gesture-enabled').checked) {
    const hints = {holding:'동작을 잠깐 유지하세요.', fired:'실행했습니다. 다른 동작으로 바로 바꿀 수 있습니다.', latched:'같은 동작은 한 번 실행됩니다. 반복하려면 중립 자세로 쉬거나 손을 내려주세요.', repeating:'10초 이동 반복 중입니다. 손을 내리거나 동작을 바꾸면 반복이 멈춥니다.', uncertain:'동작을 확인하는 중입니다.', releasing:'다음 같은 동작을 위해 쉬는 중입니다.', ready:'다음 동작을 받을 준비가 되었습니다.', cooldown:'다음 명령을 준비하고 있습니다.'};
    message('#camera-message', hints[state.phase] || '손동작 제어 중입니다.');
  } else message('#camera-message', '인식 미리보기 중입니다. 사용할 위치를 선택하고 손동작 제어를 켜세요.');
  if (state.action) void execute(state.action, model ? '개인 손동작' : '손동작');
}
function infer(token) {
  if (token !== session || !ready) return;
  // Desktop input must keep receiving fresh frames while another app covers this window.
  // requestAnimationFrame can stop for an occluded window even with background throttling off.
  raf = globalThis.manuxDesktop ? setTimeout(() => infer(token), 16) : requestAnimationFrame(() => infer(token));
  const now = performance.now();
  advanceEnrollment(now);
  if (!vision || vision.busy || now - lastInference < (controlMode === 'mouse' ? 30 : 50) || camera.readyState < 2 || camera.currentTime === lastVideoTime) return;
  lastInference = now; lastVideoTime = camera.currentTime;
  const client = vision;
  client.recognize(camera, now).then(packet => {
    if (!packet || token !== session || !ready) return;
    processResult(packet, performance.now(), performance.now() - now);
  }).catch(error => {
    if (token !== session) return;
    stopCamera(); badge('인식 오류', 'error');
    message('#camera-message', `인식이 중단되었습니다. 카메라를 다시 시작하세요. (${error.message})`, true);
  });
}
function stopCamera() {
  mouse.setReady(false);
  session++; ready = false;
  if (globalThis.manuxDesktop) clearTimeout(raf); else cancelAnimationFrame(raf);
  disableGestures();
  cancelEnrollment('카메라가 종료되어 진행 중인 등록을 취소했습니다.');
  vision?.close(); vision = null;
  stream?.getTracks().forEach(track => track.stop()); stream = null;
  camera.srcObject = null; ctx.clearRect(0, 0, canvas.width, canvas.height);
  $('#camera-empty').hidden = false;
  $('#camera-start').disabled = false; $('#camera-start').textContent = '카메라 시작';
  $('#camera-stop').disabled = true; $('#resolution').disabled = false;
  $('#recognition-label').textContent = '대기 중'; $('#confidence').textContent = '';
  badge('카메라 꺼짐'); refreshProfile(); syncMedia();
}
$('#camera-start').addEventListener('click', async () => {
  const token = ++session;
  $('#camera-start').disabled = true; $('#camera-stop').disabled = false; $('#resolution').disabled = true;
  $('#camera-start').textContent = '준비 중…'; badge('연결 중');
  message('#camera-message', '카메라 접근을 허용하세요. 인식 모델을 준비합니다.');
  let acquired;
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('카메라는 HTTPS 또는 localhost 주소에서 사용할 수 있습니다. LAN에서는 HTTPS 주소를 Edge 또는 Chrome으로 열어주세요.');
    acquired = await navigator.mediaDevices.getUserMedia(cameraConstraints($('#resolution').value));
    if (token !== session) { acquired.getTracks().forEach(track => track.stop()); return; }
    stream = acquired;
    for (const track of stream.getVideoTracks()) track.addEventListener('ended', () => {
      if (token === session) { stopCamera(); message('#camera-message', '카메라 연결이 끊겼습니다. 다시 연결한 뒤 시작하세요.', true); }
    });
    camera.srcObject = stream; await camera.play();
    if (token !== session) return;
    cameraInfo = describeCamera(stream.getVideoTracks()[0], camera, $('#resolution').value);
    $('#camera-stats').textContent = `실제 입력 ${cameraInfo.label}${cameraInfo.reduced ? ' · 요청한 해상도보다 낮게 연결됨' : ''}`;
    const client = new VisionClient(); vision = client; await client.init();
    if (token !== session) return;
    message('#camera-message', '첫 영상으로 인식을 준비하고 있습니다. 잠시만 기다려주세요.');
    // Shader/model warm-up belongs to startup, before controls become available.
    const warmupStarted = performance.now();
    const warmup = await client.recognize(camera, warmupStarted);
    if (token !== session) return;
    if (stream.getVideoTracks().some(track => track.readyState !== 'live')) throw new Error('카메라 연결이 종료되었습니다.');
    ready = true; lastVideoTime = -1; lastInference = 0; lastResultAt = null; fps = 0;
    mouse.setReady(true);
    gate.reset(); $('#camera-empty').hidden = true; $('#camera-start').textContent = '카메라 연결됨';
    badge('인식 중', 'on'); refreshProfile(); syncMedia();
    if (warmup) drawHands(warmup.result.landmarks);
    infer(token);
    message('#camera-message', '손동작 제어를 켜거나 사용자별 동작을 등록하세요.');
  } catch (error) {
    if (token !== session) return;
    acquired?.getTracks().forEach(track => track.stop()); stopCamera(); badge('연결 실패', 'error');
    const reasons = {NotAllowedError:'카메라 권한이 차단되었습니다. 주소창의 사이트 권한에서 카메라를 허용하세요.', NotFoundError:'연결된 카메라가 없습니다. USB 웹캠을 연결한 뒤 다시 시작하세요.', NotReadableError:'카메라를 사용할 수 없습니다. 다른 앱의 카메라 사용을 종료한 뒤 다시 시도하세요.', OverconstrainedError:'카메라가 요청한 설정을 지원하지 않습니다. 해상도를 낮춰 다시 시도하세요.'};
    message('#camera-message', reasons[error.name] || `모델 또는 카메라를 준비하지 못했습니다. ${error.message}`, true);
  }
});
$('#camera-stop').addEventListener('click', () => { stopCamera(); message('#camera-message', '카메라와 손동작 제어를 종료했습니다.'); });
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') { mouse.stop('Esc를 눌러 마우스 제어를 껐습니다.'); disableGestures(); cancelEnrollment('Esc를 눌러 등록을 취소했습니다.'); message('#camera-message', 'Esc를 눌러 손동작 제어를 껐습니다.'); }
});
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    if (!mouse.backgroundAllowed) mouse.stop('창을 벗어나 연습 제어를 껐습니다.');
    disableGestures(); cancelEnrollment('창을 벗어나 진행 중인 등록을 취소했습니다.');
    if (!mouse.backgroundAllowed) message('#camera-message', '창을 벗어나 손동작 제어를 껐습니다. 다시 켜서 사용하세요.');
  }
});
window.addEventListener('beforeunload', () => { stopCamera(); if (mediaUrl) URL.revokeObjectURL(mediaUrl); });
renderProfiles(profiles[0]?.id || ''); syncMedia();
if (location.hash === '#mouse' || globalThis.manuxDesktop) switchMode('mouse');
if (stored.error) message('#profile-message', `${stored.error} 기존 데이터를 보호하기 위해 새 저장을 멈췄습니다. 공통 모델은 사용할 수 있습니다.`, true);
