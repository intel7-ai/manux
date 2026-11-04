// Draws the finger tracker around the real Windows cursor. Rendering only: no input, no storage.
const LABELS = {idle:'쉬는 중', move:'커서 이동', left:'좌클릭 · 드래그', right:'우클릭', scroll:'스크롤', other:'손 인식 중'};
const COLORS = {idle:'#94a3b8', move:'#46e5bd', left:'#10b981', right:'#3b82f6', scroll:'#c084fc', other:'#94a3b8'};
const PALM_PX = 46;
const FADE_MS = 500;

const canvas = document.getElementById('overlay');
const ctx = canvas.getContext('2d');
let frame = null, receivedAt = 0;

function resize() {
  const ratio = window.devicePixelRatio || 1;
  const w = Math.round(innerWidth * ratio), h = Math.round(innerHeight * ratio);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
}

function draw(now) {
  requestAnimationFrame(draw);
  resize();
  ctx.clearRect(0, 0, innerWidth, innerHeight);
  if (!frame) return;
  // Frames stop when tracking or control stops; the tracker fades instead of freezing in place.
  const alpha = 1 - Math.min(1, (now - receivedAt) / FADE_MS);
  if (alpha <= 0) return;
  const color = COLORS[frame.mode] ?? COLORS.other;
  const {x:cx, y:cy} = frame.cursor;
  const at = ([dx, dy]) => ({x:cx + dx * PALM_PX, y:cy + dy * PALM_PX});
  const tips = Object.fromEntries(Object.entries(frame.tips).map(([name, offset]) => [name, at(offset)]));
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.shadowColor = 'rgba(0,0,0,.45)'; ctx.shadowBlur = 6;
  ctx.lineWidth = 3; ctx.strokeStyle = color;
  for (const finger of frame.contacts) {
    ctx.beginPath(); ctx.moveTo(tips.thumb.x, tips.thumb.y); ctx.lineTo(tips[finger].x, tips[finger].y); ctx.stroke();
  }
  for (const [name, p] of Object.entries(tips)) {
    const active = name === 'thumb' || frame.contacts.includes(name);
    ctx.beginPath(); ctx.arc(p.x, p.y, name === 'index' ? 7 : 5, 0, Math.PI * 2);
    ctx.fillStyle = active ? color : 'rgba(255,255,255,.85)'; ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = color; ctx.stroke();
  }
  const label = LABELS[frame.mode] ?? LABELS.other;
  ctx.font = '600 13px "Malgun Gothic","Segoe UI",sans-serif';
  const width = ctx.measureText(label).width + 20, lx = cx + 18, ly = cy + 26;
  ctx.shadowBlur = 0;
  ctx.fillStyle = 'rgba(17,35,49,.88)';
  ctx.beginPath(); ctx.roundRect(lx, ly, width, 24, 12); ctx.fill();
  ctx.fillStyle = color; ctx.fillText(label, lx + 10, ly + 17);
  ctx.restore();
}

window.manuxOverlay?.onFrame(next => { frame = next; receivedAt = performance.now(); });
requestAnimationFrame(draw);
