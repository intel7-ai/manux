"""JSONL service. Camera, UI and output each have their own clock; no frame queue."""
import base64
from collections import deque
from dataclasses import asdict
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback
import cv2
from .capture import LatestCamera, orient
from .landmarker import Landmarker
from .tracking_core import TrackingCore
from .observation import observe
from .controller import Controller
from .output import Output

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT/'.runtime'
LOCAL = ROOT/'.local'
commands = queue.Queue()
alive = threading.Event()
alive.set()


def read_json(path, fallback):
    try: return json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError): return fallback


def save_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(',', ':'))+'\n')
    sys.stdout.flush()


def read_commands():
    try:
        for line in sys.stdin:
            try: commands.put(json.loads(line))
            except ValueError: pass
    finally: alive.clear()


def main():
    # Set DPI awareness before reading desktop coordinates, including on scaled displays.
    import ctypes
    try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (OSError, AttributeError): pass
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stdin.reconfigure(encoding='utf-8')
    RUNTIME.mkdir(exist_ok=True)
    LOCAL.mkdir(exist_ok=True)
    camera_settings = read_json(LOCAL/'camera.json', dict(camera=0, width=1920, height=1080, mirror=True, rotation=0))
    initial = read_json(LOCAL/'previous-control.json', {})
    settings = read_json(LOCAL/'settings.json', initial)
    settings = dict(area_scale=.77, stability=.6, tap_enter=.65, scroll_speed=1.12, **{k:v for k,v in settings.items() if k not in ('area_scale','stability','tap_enter','scroll_speed')}) | {k:v for k,v in settings.items() if k in ('area_scale','stability','tap_enter','scroll_speed')}
    controller = Controller(settings)
    tracker = TrackingCore(smoothing=0.)
    output = Output()
    detector = None
    camera = None
    history = deque(maxlen=1800)
    event_id = 0
    last_preview = last_save = last_state = time.perf_counter()
    heartbeat = time.perf_counter()
    frame_times = deque(maxlen=30)
    last_meaningful = 0.
    notice = ''
    paused = False
    calibrate = None
    last_status = {}
    practice_feedback = None
    feedback_at = 0.
    threading.Thread(target=read_commands, daemon=True).start()
    try:
        send(dict(type='boot', settings=settings, camera=camera_settings, message='카메라와 손 추적 모델을 연결합니다'))
        detector = Landmarker(max_width=1280, num_hands=2)
        camera = LatestCamera(camera_settings.get('camera', 0), camera_settings.get('width',1920), camera_settings.get('height',1080))
        while alive.is_set():
            now = time.perf_counter()
            while not commands.empty():
                cmd = commands.get_nowait()
                kind = cmd.get('type')
                if kind == 'heartbeat': heartbeat = now
                elif kind == 'feedback':
                    p=cmd.get('position')
                    import math
                    if isinstance(p,list) and len(p)==2 and all(isinstance(v,(int,float)) and math.isfinite(v) and 0<=v<=1 for v in p):
                        practice_feedback=tuple(p)
                        feedback_at=now
                elif kind == 'quit': alive.clear()
                elif kind == 'native':
                    output.enable(bool(cmd.get('enabled')))
                    controller.reset(output.normalized())
                    notice = 'PC 제어 중 · Esc 또는 실제 마우스 이동으로 정지' if output.desktop else '연습 모드'
                    paused = False
                elif kind == 'pause':
                    paused = bool(cmd.get('enabled'))
                    output.stop()
                    controller.reset(output.normalized())
                elif kind == 'scope':
                    scope = cmd.get('value')
                    if scope in ('all','click','scroll','drag','zoom'):
                        output.motor.cancel()
                        controller.scope = scope
                        controller.reset(output.normalized())
                elif kind == 'settings':
                    for k, lo, hi in (('area_scale',.35,.95),('stability',0.,1.),('tap_enter',.25,1.),('scroll_speed',.3,2.5)):
                        if k in cmd:
                            try: settings[k] = max(lo, min(hi, float(cmd[k])))
                            except (ValueError, TypeError): pass
                    save_json(LOCAL/'settings.json', settings)
                    output.motor.cancel()
                    scope = controller.scope
                    controller = Controller(settings)
                    controller.scope = scope
                    controller.reset(output.normalized())
                elif kind == 'calibrate':
                    output.stop()
                    controller.reset(output.normalized())
                    calibrate = dict(start=now, rest=[], press=[])
                elif kind == 'camera':
                    output.stop()
                    controller.reset(output.normalized())
                    if 'mirror' in cmd: camera_settings['mirror'] = bool(cmd['mirror'])
                    if cmd.get('rotation') in (0,90,180,270): camera_settings['rotation'] = cmd['rotation']
                    if cmd.get('width') in (1280,1920):
                        camera_settings['width'] = cmd['width']
                        camera_settings['height'] = 720 if cmd['width'] == 1280 else 1080
                    camera.close()
                    camera = LatestCamera(camera_settings.get('camera',0),camera_settings.get('width',1920),camera_settings.get('height',1080))
                    tracker.reset()
                    save_json(LOCAL/'camera.json',camera_settings)
                elif kind == 'save':
                    save_json(RUNTIME/'session.json', dict(version=1, settings=settings, samples=list(history)))
                    notice = '최근 동작 기록을 저장했습니다'
            if not alive.is_set(): break
            if output.desktop and (now-heartbeat > 2.5 or output.native.escape_pressed() or output.takeover() or output.motor.halted):
                reason = '실제 마우스로 전환했습니다' if output.takeover() else 'PC 제어를 멈췄습니다'
                output.stop()
                controller.reset(output.normalized())
                notice = reason
            frame_item = camera.read()
            if frame_item is None:
                if now-last_state > .25:
                    output.motor.cancel()
                    controller.reset(output.normalized())
                    send(dict(type='status', native=output.desktop, paused=paused, state='waiting', label='카메라 연결 확인', notice=camera.error or '새 화면을 기다립니다'))
                    last_state = now
                continue
            seq, captured, original = frame_item
            frame = orient(original, camera_settings.get('mirror',True), camera_settings.get('rotation',0))
            height, width = frame.shape[:2]
            hands = detector.detect(frame, captured)
            tracking = tracker.update(hands, captured, width, height)
            if tracking['raw']: detector.remember_selected(tracking['raw'],captured)
            f = observe(tracking['raw'], width, height) if tracking['state'] == 'tracked' else None
            age = time.perf_counter()-captured
            valid = age < .18
            calibration_status = None
            if calibrate:
                elapsed = now-calibrate['start']
                phase = 'rest' if elapsed < 4.5 else 'press'
                calibration_status = dict(progress=min(1.,elapsed/10), phase=phase,
                    message='검지는 평소 가리키는 위치로 두세요' if phase == 'rest' else '검지를 누른 자세로 유지하세요 · 힘주어 접지 마세요')
                if f and ((1.5 < elapsed < 4.5) or (6.5 < elapsed < 9.8)):
                    calibrate[phase].append(f.signature)
                if elapsed >= 10:
                    from statistics import median
                    rest, press = calibrate['rest'], calibrate['press']
                    if len(rest) >= 15 and len(press) >= 15:
                        a,b = [tuple(median(row[i] for row in rows) for i in range(4)) for rows in (rest,press)]
                        import math
                        if math.dist(a,b) > .16:
                            settings['profile'] = dict(version=1, rest=a, press=b)
                            save_json(LOCAL/'settings.json',settings)
                            notice = '내 검지 누르기를 저장했습니다'
                        else: notice = '두 자세가 비슷합니다 · 기존 설정을 유지했습니다'
                    else: notice = '손 표본이 부족합니다 · 기존 설정을 유지했습니다'
                    calibrate = None
                    controller.reset(output.normalized())
            shown = practice_feedback if not output.desktop and practice_feedback is not None and now-feedback_at<.2 else output.normalized()
            result = controller.step(f if not paused and not calibrate else None, captured, shown,
                                     valid=valid, track_reset=tracking['reset'])
            output.apply(result)
            for action in result['actions']:
                event_id += 1
                send(dict(type='action', id=event_id, native=output.desktop, position=result['position'], action=action))
            frame_times.append(captured)
            fps = (len(frame_times)-1)/(frame_times[-1]-frame_times[0]) if len(frame_times)>1 else 0.
            last_status = dict(type='frame', native=output.desktop, paused=paused, position=output.normalized(),
                target=result['position'], state=result['state'], label=result['label'], reason=result['reason'],
                tap_score=result['tap_score'], zoom_preview=result['zoom_preview'], zoom_confirmed=result['zoom_confirmed'],
                dragging=result['dragging'], scope=controller.scope, fps=round(fps,1),
                moving=result['moving'],
                age_ms=round(age*1000), inference_ms=round(detector.last_ms,1), resolution=[width,height],
                tracking=tracking['state'], notice=notice, camera=camera_settings, area=controller.pointer.bounds,
                settings=settings, calibration=calibration_status, output=output.motor.diagnostics())
            preview_now = time.perf_counter()
            if preview_now-last_preview > .075:
                small = cv2.resize(frame,(640,round(height*640/width)))
                ok, jpeg = cv2.imencode('.jpg',small,[cv2.IMWRITE_JPEG_QUALITY,70])
                if ok: last_status['preview'] = base64.b64encode(jpeg).decode('ascii')
                last_status['landmarks'] = tracking['raw']
                last_preview = preview_now
            send(last_status)
            history.append(dict(at=captured, features=asdict(f) if f else None, result=result,
                                landmarks=tracking['raw'], resolution=[width,height],
                                native=output.desktop, tracking=tracking['state'], age_ms=round(age*1000)))
            while history and captured-history[0]['at'] > 30: history.popleft()
            last_state = preview_now
            if preview_now-last_save > 4:
                save_json(RUNTIME/'status.json',{k:v for k,v in last_status.items() if k not in ('preview','landmarks')})
                save_json(RUNTIME/'session.json',dict(version=1, settings=settings, samples=list(history)))
                if f and sum(bool(row['features']) for row in history)>=60:
                    # Idle camera frames must not overwrite the last useful hand session.
                    save_json(RUNTIME/'last-hand-session.json',dict(version=1,settings=settings,samples=list(history)))
                last_save = preview_now
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        try: send(dict(type='error', message=str(error)))
        except (BrokenPipeError, OSError): pass
    finally:
        output.close()
        if camera: camera.close()
        if detector: detector.close()
        save_json(RUNTIME/'session.json',dict(version=1,settings=settings,samples=list(history)))


if __name__ == '__main__': main()
