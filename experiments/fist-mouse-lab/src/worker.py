"""Single newest-frame pipeline; native output only after an explicit UI start."""
from collections import deque
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import queue
from statistics import median
import threading
import time
import cv2
from .capture import LatestCamera, orient
from .landmarker import Landmarker
from .tracking_core import HandAssociation
from .fist_engine import FistEngine, extract
from .native import NativeMouse
from .reach_area import DEFAULT_AREA,ReachCalibration,valid_area
from .cursor_motion import CursorMotion,PracticePointer

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'.runtime'
TEST_STEPS=['주먹으로 좌우·상하 이동 · 클릭 없이', '엄지를 벌렸다 되돌리기 · 천천히 3번',
            '엄지 두 번 빠르게 · 더블클릭 2번', '새끼손가락 펴기 · 2번',
            '검지 펴고 손을 위아래로 · 휠', '손을 뺐다가 주먹으로 돌아오기 · 커서 위치 확인']
MOVEMENT_STEPS=['초록 영역 중앙에서 주먹을 편하게 멈춰주세요',
                '초록 영역의 네 모서리 쪽으로 천천히 이동해주세요',
                '주먹을 좌우로 빠르게 움직였다가 멈춰주세요',
                '손을 화면 밖으로 뺐다가 초록 영역 중앙으로 돌아오세요']
CLICK_STEPS=['중앙에서 엄지를 벌렸다 되돌리기 · 좌클릭 3번',
             '초록 영역 왼쪽 위에서 좌클릭 3번 · 손 전체는 카메라 안에',
             '초록 영역 오른쪽 아래에서 좌클릭 3번',
             '엄지를 빠르게 두 번 벌렸다 되돌리기 · 더블클릭',
             '엄지를 0.3초 유지한 뒤 손을 움직여 드래그 · 되돌리면 놓기']


def load(path,default):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError): return dict(default)


class Worker:
    def __init__(self):
        (ROOT/'.local').mkdir(parents=True,exist_ok=True)
        self.camera_settings=load(ROOT/'.local/camera.json',dict(camera=0,width=1920,height=1080,mirror=True,rotation=0,num_hands=2,detector_width=1280))
        defaults=dict(sensitivity=1.3,scroll_speed=1.,thumb_enter=.82,thumb_exit=.64,
                      movement_mode='absolute',area_scale=.76,stability=.55,area_base=None)
        saved=load(ROOT/'.local/control.json',defaults)
        self.settings=defaults | saved
        if not valid_area(saved.get('area_base')):
            self.settings['area_base']=None
        self.settings['movement_mode']='absolute'
        self.commands=queue.Queue()
        self.stop_event=threading.Event()
        self.output_guard=threading.RLock()
        self.enabled=False
        self.motion=None
        self.practice_pointer=PracticePointer()
        self.practice_motion=CursorMotion(self.practice_pointer)
        self.ui_seen=time.perf_counter()
        self.lock=threading.Lock()
        self.latest=None
        self.status={}
        self.serial=0
        self.status_write_errors=0
        self.thread=threading.Thread(target=self.run,daemon=True,name='fist-mouse')

    def start(self): self.thread.start()
    def command(self,action,value=None):
        if action=='native' and not value: self.disable()
        self.commands.put((action,value))
    def disable(self):
        with self.output_guard:
            self.enabled=False
            if self.motion:self.motion.cancel()
            self.practice_motion.cancel()
    def heartbeat(self): self.ui_seen=time.perf_counter()
    def close(self):
        self.disable()
        self.stop_event.set()
    def snapshot(self):
        with self.lock: return self.serial,self.latest,dict(self.status)
    def publish(self,frame,status):
        with self.lock:
            self.latest,self.status=frame,status
            self.serial+=1
    def write_status(self):
        _,_,state=self.snapshot()
        clean={k:v for k,v in state.items() if k!='points'}
        clean['pid']=os.getpid()
        clean['status_write_errors']=self.status_write_errors
        temp=RUNTIME/'status.tmp'
        try:
            temp.write_text(json.dumps(clean,ensure_ascii=False,indent=2),encoding='utf-8')
            temp.replace(RUNTIME/'status.json')
            return True
        except OSError:
            # Windows readers can briefly deny replacement. Diagnostics must
            # never stop camera tracking or native input; retry next interval.
            self.status_write_errors+=1
            return False

    def run(self):
        camera=model=None
        native=NativeMouse()
        self.motion=CursorMotion(native)
        engine=FistEngine(**self.settings,double_time=native.double_time)
        association=HandAssociation()
        practice=(500,300)
        timestamps=deque(maxlen=60)
        capture_samples=deque(maxlen=60)
        stage_times=deque(maxlen=60)
        events=deque(maxlen=14)
        counts=dict(left=0,right=0,double=0,wheel=0,drag=0)
        last_track=None
        last_write=0.
        calibration=None
        reach=None
        recording=None
        report=''
        message=''
        seq=0
        def log(text):
            nonlocal seq
            seq+=1
            events.append(dict(id=seq,text=text,time=datetime.now().strftime('%H:%M:%S')))
        def save_recording():
            nonlocal recording,report
            if recording is None: return
            path=RUNTIME/'reports'/('fist-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.json')
            path.parent.mkdir(exist_ok=True)
            recording['settings']=dict(self.settings)
            recording['note']='Prompt phases are not verified gesture labels. Landmarks only; no images. Counts are not accuracy.'
            path.write_text(json.dumps(recording,ensure_ascii=False),encoding='utf-8')
            report=str(path)
            recording=None
        try:
            cfg=self.camera_settings
            model=Landmarker(cfg.get('detector_width',1280),cfg.get('num_hands',2))
            camera=LatestCamera(cfg['camera'],cfg['width'],cfg['height'])
            while not self.stop_event.is_set() and not (RUNTIME/'stop.request').exists():
                now=time.perf_counter()
                if native.escape_pressed() or now-self.ui_seen>1 or self.motion.halted:
                    if self.enabled:
                        self.disable(); engine.reset(); log('마우스 출력 중지')
                while not self.commands.empty():
                    action,value=self.commands.get_nowait()
                    if action=='native':
                        with self.output_guard: self.enabled=bool(value) and calibration is None and reach is None
                        self.practice_motion.cancel()
                        if self.enabled:self.motion.resume()
                        else:self.motion.cancel()
                        engine.reset(); log('실제 마우스 시작' if self.enabled else '연습 모드')
                    elif action=='settings':
                        allowed={'sensitivity':(.3,3.),'scroll_speed':(.25,3.),'area_scale':(.45,1.),'stability':(0.,1.)}
                        for k,v in value.items():
                            if k in allowed:
                                self.settings[k]=max(allowed[k][0],min(allowed[k][1],float(v)))
                                setattr(engine,k,self.settings[k])
                        (ROOT/'.local/control.json').write_text(json.dumps(self.settings,indent=2),encoding='utf-8')
                    elif action=='camera':
                        if calibration is not None or reach is not None:
                            message='위치 등록이 끝난 뒤 카메라 방향을 변경해주세요';continue
                        self.disable(); engine.reset(); association.reset()
                        self.camera_settings.update(value)
                        cfg=self.camera_settings
                        (ROOT/'.local/camera.json').write_text(json.dumps(cfg,indent=2),encoding='utf-8')
                        model.close(); model=Landmarker(cfg.get('detector_width',1280),cfg.get('num_hands',2))
                        self.settings['area_base']=None
                        engine.area_base=None
                        (ROOT/'.local/control.json').write_text(json.dumps(self.settings,indent=2),encoding='utf-8')
                        message='카메라 방향이 바뀌었습니다 · 중앙 조작 영역을 사용합니다'
                    elif action=='calibrate':
                        self.disable(); engine.reset()
                        reach=None
                        calibration=dict(start=now,rest=[],out=[])
                        message=''
                    elif action=='reach':
                        if recording is None:
                            self.disable();engine.reset();calibration=None
                            reach=ReachCalibration(now);message=''
                    elif action=='record':
                        if recording is None and calibration is None and reach is None:
                            recording=dict(start=now,samples=[],initial_counts=dict(counts),
                                           record_type=value if value in ('movement','clicks') else 'gestures')
                    elif action=='reset':
                        engine.reset(); practice=(500,300)
                        self.practice_motion.cancel();self.practice_pointer.move(practice)
                        counts=dict(left=0,right=0,double=0,wheel=0,drag=0)
                    elif action=='cancel':
                        calibration=None;reach=None;engine.reset();message='맞추기를 취소했습니다'
                packet=camera.read()
                now=time.perf_counter()
                frame=None; points=None; features=None; age=0.; tracking='missing'
                if packet is not None:
                    sequence,captured,frame=packet
                    capture_samples.append((sequence,captured))
                    frame=orient(frame,cfg['mirror'],cfg['rotation'])
                    h,w=frame.shape[:2]
                    if now-captured < .18:
                        hands=model.detect(frame,captured)
                        now=time.perf_counter();age=(now-captured)*1000
                        hand=association.select(hands,captured,h/w) if age<180 else None
                        if hand:
                            points=hand.points
                            model.remember_selected(points,captured)
                            if last_track!=association.serial: engine.reset()
                            last_track=association.serial
                            features=extract(points,w,h)
                            tracking='tracked' if features else 'geometry_rejected'
                        elif association.ambiguous: tracking='ambiguous'
                    timestamps.append(now)
                else:
                    h,w=cfg['height'],cfg['width']
                if reach is not None:
                    message=reach.update(features,points,now)
                    if now-reach.start>=reach.duration:
                        area,message=reach.finish()
                        if area:
                            self.settings.update(area_base=area,area_scale=1.)
                            engine.area_base=area;engine.area_scale=1.
                            (ROOT/'.local/control.json').write_text(json.dumps(self.settings,indent=2),encoding='utf-8')
                            profile=dict(source='two_corner_registration',area=area,camera_settings=dict(cfg),
                                         captured_at=datetime.now().isoformat(),upper_samples=len(reach.upper),lower_samples=len(reach.lower))
                            (ROOT/'.local/reach-profile.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
                        reach=None;engine.reset()
                    features=None
                if calibration is not None:
                    elapsed=now-calibration['start']
                    if elapsed<2: message=f'편한 주먹 · 엄지를 쉬는 위치에 두세요 ({max(1,2-int(elapsed))})'
                    elif elapsed<5:
                        message='평소 주먹을 유지해주세요 · 엄지는 편하게'
                        if features and features.thumb_visible and all(features.fingers_visible) and max(features.fingers)<.9: calibration['rest'].append(features.thumb)
                    elif elapsed<7: message='이제 엄지를 클릭할 만큼 벌려주세요'
                    elif elapsed<10:
                        message='엄지를 벌린 위치에서 잠깐 유지해주세요'
                        if features and features.thumb_visible and all(features.fingers_visible) and max(features.fingers)<.9: calibration['out'].append(features.thumb)
                    else:
                        a,b=sorted(calibration['rest']),sorted(calibration['out'])
                        if len(a)>=45 and len(b)>=45 and b[int(len(b)*.1)]-a[int(len(a)*.9)]>.14:
                            lo,hi=a[int(len(a)*.9)],b[int(len(b)*.1)]
                            engine.thumb_exit=lo+(hi-lo)*.25
                            engine.thumb_enter=lo+(hi-lo)*.65
                            self.settings.update(thumb_exit=engine.thumb_exit,thumb_enter=engine.thumb_enter)
                            (ROOT/'.local/control.json').write_text(json.dumps(self.settings,indent=2),encoding='utf-8')
                            message='엄지 범위를 저장했습니다 · 연습 화면에서 클릭해보세요'
                        else: message='두 자세를 충분히 구분하지 못했습니다 · 기존 설정을 유지합니다'
                        calibration=None;engine.reset()
                    features=None
                frame_native=self.enabled
                output_start=time.perf_counter()
                output_motion=self.motion if frame_native else self.practice_motion
                actual,cursor_owned=output_motion.feedback()
                bounds=native.bounds() if frame_native else (0,0,1000,600)
                result=engine.step(features,now,actual,bounds,cursor_owned=cursor_owned,
                                   sample_time=captured if packet is not None else now)
                with self.output_guard:
                    # Check Escape again after inference, before any OS output.
                    if self.enabled and (native.escape_pressed() or self.motion.halted):
                        self.disable();engine.reset()
                    if self.enabled!=frame_native:
                        # Stop can arrive during inference. Discard that frame's
                        # commands, and never copy desktop pixels into the pad.
                        engine.reset()
                        bounds=(0,0,1000,600)
                        result=engine.step(None,now,practice,bounds)
                    if self.enabled:
                        try:self.motion.apply(result)
                        except OSError as error:
                            self.disable();engine.reset();message=str(error);log(message)
                    else:
                        self.practice_motion.apply(result)
                        practice=self.practice_pointer.cursor()
                output_ms=(time.perf_counter()-output_start)*1000
                stage_times.append(output_ms)
                for click in result['clicks']:
                    counts[click]+=1;log('좌클릭' if click=='left' else '우클릭')
                if result['primary']=='press':counts['drag']+=1;log('드래그 시작')
                elif result['primary']=='release':log('드래그 놓기')
                if result['double']: counts['double']+=1;log('더블클릭 조합')
                if result['wheel']:
                    counts['wheel']+=result['wheel']//120;log('휠 위' if result['wheel']>0 else '휠 아래')
                test_elapsed=now-recording['start'] if recording is not None else None
                steps=({'movement':MOVEMENT_STEPS,'clicks':CLICK_STEPS}.get(recording.get('record_type'),TEST_STEPS)
                       if recording else TEST_STEPS)
                duration=len(steps)*5
                test_step=steps[min(len(steps)-1,int(test_elapsed//5))] if test_elapsed is not None else ''
                if recording is not None:
                    recording['samples'].append(dict(t=round(test_elapsed,4),raw=points,width=w,height=h,
                        tracking=tracking,features=asdict(features) if features else None,result=result,
                        native=self.enabled,phase=min(len(steps)-1,int(test_elapsed//5)),age_ms=round(age,2),output_ms=round(output_ms,2),
                        captured_at=captured if packet is not None else None))
                    if test_elapsed>=duration:
                        recording['final_counts']=dict(counts);save_recording();log('점검 기록 저장')
                fps=(len(timestamps)-1)/(timestamps[-1]-timestamps[0]) if len(timestamps)>1 else 0
                camera_fps=(capture_samples[-1][0]-capture_samples[0][0])/(capture_samples[-1][1]-capture_samples[0][1]) if len(capture_samples)>1 else 0
                state=dict(**result,points=points,tracking=tracking,features=asdict(features) if features else None,
                    native_output=self.enabled,counts=dict(counts),events=list(events),fps=round(fps,1),age_ms=round(age,1),
                    camera_fps=round(camera_fps,1),camera_skipped=getattr(camera,'skipped',0),
                    output_ms=round(output_ms,2),output_p95_ms=round(sorted(stage_times)[int((len(stage_times)-1)*.95)],2),
                    cursor_motion=output_motion.diagnostics(),
                    width=w,height=h,settings=dict(self.settings),camera_settings=dict(cfg),
                    recording=recording is not None,test_step=test_step,test_elapsed=test_elapsed,test_duration=duration,report=report,
                    calibrating=calibration is not None or reach is not None,
                    calibration_kind='reach' if reach is not None else 'thumb' if calibration is not None else None,
                    calibration_elapsed=now-calibration['start'] if calibration else now-reach.start if reach else None,
                    message=message,camera_error=camera.error,output_bounds=bounds,timestamp=now)
                if frame is not None:
                    scale=min(1,800/w,560/h)
                    if scale<1: frame=cv2.resize(frame,(round(w*scale),round(h*scale)))
                self.publish(frame,state)
                if now-last_write>.5: self.write_status();last_write=now
        except Exception as error:
            self.disable()
            self.publish(None,dict(error=str(error),native_output=False))
            self.write_status()
            import traceback;traceback.print_exc()
        finally:
            self.disable()
            if self.motion:self.motion.close()
            self.practice_motion.close()
            save_recording()
            if model: model.close()
            if camera:
                try: camera.close()
                except RuntimeError: pass
            self.stop_event.set()
