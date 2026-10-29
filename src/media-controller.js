export const ACTION_NAMES={pause:'일시정지',play:'재생',forward:'10초 앞으로',backward:'10초 뒤로'};
export async function applyMediaAction(video,action){
  if(!Object.hasOwn(ACTION_NAMES,action))throw new Error('지원하지 않는 명령입니다.');
  if(video.readyState<1||!Number.isFinite(video.duration)||video.duration<=0)throw new Error('먼저 재생 가능한 영상 파일을 열어주세요.');
  const before=video.currentTime;
  if(action==='pause')video.pause();
  if(action==='play')await video.play();
  if(action==='forward'||action==='backward')video.currentTime=Math.max(0,Math.min(video.duration,before+(action==='forward'?10:-10)));
  return {action,before,after:video.currentTime,paused:video.paused};
}
export function formatTime(value){const v=Math.max(0,Math.floor(Number.isFinite(value)?value:0));return v>=3600?`${Math.floor(v/3600)}:${String(Math.floor(v%3600/60)).padStart(2,'0')}:${String(v%60).padStart(2,'0')}`:`${String(Math.floor(v/60)).padStart(2,'0')}:${String(v%60).padStart(2,'0')}`;}
