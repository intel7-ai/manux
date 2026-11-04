"""Fist pointer + thumb stroke / pinky / index commands. No OS dependencies.

All thresholds describe estimated geometry, not model confidence. Absolute
camera-to-screen mapping is the default. Click/scroll recognition is independent.
"""
from dataclasses import dataclass
import math
from .filters import TremorStabilizer
from .absolute_pointer import AbsolutePointer
from .thumb_button import ThumbButton


@dataclass
class Features:
    anchor: tuple
    size: float
    fingers: tuple  # index, middle, ring, pinky: chord / articulated bone length
    thumb: float   # thumb tip to index MCP / palm scale
    aspect: float = 1.0
    thumb_visible: bool = True
    fingers_visible: tuple = (True,True,True,True)


def extract(points, width, height):
    if not points or len(points) != 21:
        return None
    if not all(len(p) == 3 and all(math.isfinite(v) for v in p) for p in points):
        return None
    aspect = height / width
    p = [(x, y * aspect, z) for x, y, z in points]
    # Wrist and central MCPs hardly change when index/pinky/thumb are commanded.
    anchor = tuple(.5*p[0][a] + .25*p[9][a] + .25*p[13][a] for a in (0, 1))
    size = max(math.dist(p[0], p[9]), math.dist(p[5], p[17]))
    visible=lambda indexes:all(0 <= points[i][j] <= 1 for i in indexes for j in (0,1))
    # Palm tracking does not disappear when one distal fingertip is cropped.
    # Commands receive their own visibility flags instead of invented poses.
    if size < .025 or not visible((0,5,9,13,17)):
        return None
    scores = []
    for base in (5, 9, 13, 17):
        bones = [math.dist(p[i], p[i+1]) for i in range(base, base+3)]
        if min(bones) < .001 or max(bones) > size*1.5:
            return None
        scores.append(math.dist(p[base], p[base+3])/sum(bones))
    return Features(anchor, size, tuple(scores), math.dist(p[4], p[5])/size, aspect,
                    visible((1,2,3,4,5)),tuple(visible(range(b,b+4)) for b in (5,9,13,17)))


class FistEngine:
    def __init__(self, sensitivity=1.3, scroll_speed=1.0, thumb_enter=.82, thumb_exit=.64, double_time=.5,
                 movement_mode='absolute',area_scale=.76,stability=.55,area_base=None):
        self.sensitivity = sensitivity
        self.scroll_speed = scroll_speed
        self.thumb_enter, self.thumb_exit = thumb_enter, thumb_exit
        self.double_time = double_time
        self.movement_mode=movement_mode
        self.area_scale,self.stability=area_scale,stability
        self.area_base=area_base
        self.reset()

    def reset(self):
        self.filter = TremorStabilizer()
        self.absolute = AbsolutePointer()
        self.anchor = self.raw_anchor = self.last_time = None
        self.last_size = None
        self.mode = 'waiting'
        self.candidate = None
        self.candidate_at = 0.
        self.extended = [False, False]
        self.thumb_armed = self.right_armed = False
        self.scroll_acc = 0.
        self.position = self.last_output = None
        self.click_lock = None
        self.last_click = None
        self.primary_down=False
        self.dragging=False
        self.button=ThumbButton()
        self.finger_seen=[None]*4
        self.finger_scores=[None]*4
        self.pending_click=False

    def _pose(self, f, now):
        scores=[]
        for i,(score,visible) in enumerate(zip(f.fingers,f.fingers_visible)):
            if visible:
                self.finger_seen[i]=now;self.finger_scores[i]=score
            elif self.finger_seen[i] is None or now-self.finger_seen[i]>.14:
                return 'uncertain'
            scores.append(self.finger_scores[i])
        index, middle, ring, pinky = scores
        for k, score in enumerate((index, pinky)):
            self.extended[k] = score > (.79 if self.extended[k] else .90)
        i, p = self.extended
        if middle > .91 or ring > .97:
            return 'rest'
        if i and p:
            return 'uncertain'
        if i:
            return 'scroll'
        if p:
            return 'right'
        moving=self.mode=='fist' or self.candidate=='fist'
        if max(index, pinky) < (.90 if moving else .86) and middle < (.90 if moving else .86) and ring < (.95 if moving else .91):
            return 'fist'
        return 'uncertain'

    def step(self, f, now, cursor=(500, 400), bounds=(0,0,1920,1080), cursor_owned=False, sample_time=None):
        out = dict(mode='waiting', cursor=tuple(cursor), clicks=[], wheel=0, double=False,
                   armed=False, anchor=None, reason='손을 보여주세요',primary='none',primary_down=False,dragging=False)
        if f is None:
            if self.primary_down or self.pending_click:out['primary']='cancel'
            self.reset()
            return out
        gap = self.last_time is None or now-self.last_time > .18
        jump = self.raw_anchor is not None and math.dist(f.anchor,self.raw_anchor)>max(.075,f.size*.65)
        scale_jump = self.last_size is not None and abs(math.log(f.size/self.last_size))>.35
        if gap or jump or scale_jump:
            if self.primary_down or self.pending_click:out['primary']='cancel'
            self.reset()
        dt = max(.001, now-self.last_time) if self.last_time is not None else 1/30
        self.last_time, self.raw_anchor, self.last_size = now, f.anchor, f.size
        # Filter by camera sampling time, not variable inference completion time.
        sampled=now if sample_time is None else sample_time
        pt = self.filter(f.anchor, sampled, min_cutoff=2.3, beta=48, deadzone=.00045)
        out['anchor'] = pt
        pose = self._pose(f,now)
        if pose != self.candidate:
            self.candidate, self.candidate_at = pose, now
        # Pause movement immediately on a proposed command; debounce only firing.
        dwell = .055 if pose in ('fist','scroll') else .08
        stable = now-self.candidate_at >= dwell
        previous_mode = self.mode
        self.mode = pose if stable else 'transition'
        out['mode'] = self.mode
        if self.last_output is None or (not cursor_owned and math.dist(cursor,self.last_output)>3):
            self.position = tuple(map(float,cursor))
            self.anchor = None
            self.click_lock = self.last_click = None
        if self.position is None:
            self.position = tuple(map(float,cursor))

        was_pending=self.pending_click
        event=self.button.update(f.thumb,f.thumb_visible,pt,f.size,now,self.thumb_enter,self.thumb_exit) if self.mode=='fist' else self.button.cancel()
        self.thumb_armed=self.button.armed
        self.pending_click=event=='pending'
        self.primary_down=self.dragging=self.button.stage=='drag'
        out['primary']=event
        if self.pending_click and not was_pending and not self.click_lock:
            # Freeze the visible cursor, not the next interpolation destination.
            self.position=tuple(map(float,cursor))
        if event=='click':out['clicks'].append('left')
        # A finger necessarily crosses an intermediate bend before extension.
        # Keep right-click readiness across that transition, but not conflicting
        # index+pinky commands, open-hand rest, or scroll mode.
        if pose in ('rest','scroll') or pose=='uncertain' and all(self.extended):
            self.right_armed = False
        if self.mode == 'fist':
            # Pinky readiness depends on folded fingers, not the thumb's rest
            # range; an extended thumb must not disable an independent command.
            self.right_armed = True
        elif self.mode == 'right' and self.right_armed and f.fingers_visible[3]:
            out['clicks'].append('right')
            self.right_armed = False

        if out['clicks']:
            if 'left' in out['clicks']:
                if self.last_click:
                    at, clickpos = self.last_click
                    out['double'] = now-at <= self.double_time and math.dist(self.position,clickpos)<=3
                self.last_click = None if out['double'] else (now,self.position)
                self.click_lock = (now,pt,self.position)
            else:
                self.last_click = self.click_lock = None
            self.anchor = pt
        elif self.mode == 'fist':
            if self.dragging:self.click_lock=self.last_click=None
            thumb_motion = event in ('pending','press','release','cancel')
            locked = False
            if self.click_lock:
                at, hand, pos = self.click_lock
                locked = now-at <= self.double_time and math.dist(pt,hand)<max(.006,min(.016,f.size*.10))
                if not locked: self.click_lock = None
            if self.movement_mode=='absolute':
                target=self.absolute.update(f.anchor,f.aspect,bounds,sampled,self.area_scale,self.stability,self.area_base)
                if not locked and not thumb_motion:self.position=target
            elif self.anchor is not None and previous_mode == 'fist' and not locked and not thumb_motion:
                dx,dy = pt[0]-self.anchor[0],pt[1]-self.anchor[1]
                speed = math.hypot(dx,dy)/dt
                acceleration = min(1,max(0,(speed-.035)/.35))
                gain = bounds[2]*self.sensitivity*(.75+2.1*acceleration)
                self.position = (self.position[0]+dx*gain,self.position[1]+dy*gain)
            self.anchor = pt
            self.scroll_acc = 0.
        elif self.mode == 'scroll' and f.fingers_visible[0]:
            self.click_lock = self.last_click = None
            if self.anchor is not None and previous_mode == 'scroll':
                self.scroll_acc += -(pt[1]-self.anchor[1])*self.scroll_speed*7200
                # Accumulate sub-notch travel; hold still => no repeated wheel.
                units = int(self.scroll_acc/120)*120
                out['wheel'] = max(-360,min(360,units))
                self.scroll_acc -= out['wheel']
            else:
                self.scroll_acc = 0.
            self.anchor = pt
        else:
            self.anchor = None
            self.absolute.reset()
            self.scroll_acc = 0.
            self.click_lock = self.last_click = None
        x,y,w,h = bounds
        self.position = (max(x,min(x+w-1,self.position[0])),max(y,min(y+h-1,self.position[1])))
        self.last_output = tuple(round(v) for v in self.position)
        if self.movement_mode=='absolute' and self.mode in ('fist','right','scroll'):
            out['anchor']=self.absolute.camera_point(self.position,f.aspect,bounds,self.area_scale,self.area_base)
        out.update(cursor=self.last_output,armed=self.thumb_armed,primary_down=self.primary_down,dragging=self.dragging,
                   pending_click=self.pending_click,thumb_visible=f.thumb_visible,
                   drag_ready=self.button.stage=='pending' and self.button.drag_origin is not None,
                   reason={'waiting':'손을 보여주세요','transition':'자세 전환 중',
                           'fist':'드래그 중 · 엄지를 되돌리면 놓습니다' if self.dragging else
                                  '엄지를 되돌리면 클릭 · 유지한 뒤 손을 움직이면 드래그' if self.pending_click else
                                  '주먹 이동 · 엄지를 벌리면 클릭' if self.thumb_armed else '엄지를 편하게 되돌리면 클릭 준비',
                           'right':'우클릭 · 새끼손가락을 접으면 다시 준비',
                           'scroll':'휠 · 손 전체를 위아래로 움직이세요',
                           'rest':'손을 쉬는 중 · 편한 주먹으로 다시 시작',
                           'uncertain':'자세 확인 중 · 검지와 새끼를 동시에 펴지 마세요'}[self.mode])
        if not f.thumb_visible:out['reason']='엄지가 카메라 밖입니다 · 엄지가 보이게 손을 조금 안쪽으로 옮겨주세요'
        return out
