export const CAMERA_PRESETS={1080:{width:1920,height:1080},720:{width:1280,height:720},480:{width:640,height:480}};
export function cameraConstraints(preset='1080',deviceId=''){
  const size=CAMERA_PRESETS[preset]||CAMERA_PRESETS[1080];
  return{audio:false,video:{width:{ideal:size.width},height:{ideal:size.height},frameRate:{ideal:30,max:30},...(deviceId?{deviceId:{exact:deviceId}}:{facingMode:'user'})}};
}
export function describeCamera(track,video,preset){
  const s=track.getSettings();const width=s.width||video.videoWidth,height=s.height||video.videoHeight;
  const requested=CAMERA_PRESETS[preset]||CAMERA_PRESETS[1080];
  return{width,height,frameRate:s.frameRate||null,reduced:width<requested.width||height<requested.height,label:`${width} × ${height}${s.frameRate?` · ${Math.round(s.frameRate)} fps`:''}`};
}
