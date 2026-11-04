"""Fresh detection from cropped/rotated views after the VIDEO tracker loses a hand.

Never extrapolates hidden joints or returns an old pose. Each recovered result
comes from the current image; first acquisition needs two consistent detections.
"""
import math

import cv2

from .tracking_core import Hand, valid, distance


def undo_view(hand, bounds, rotation, width, height):
    x0,y0,x1,y1 = bounds
    cw,ch = x1-x0,y1-y0
    points=[]
    for x,y,z in hand.points:
        if rotation==90:x,y=y,1-x
        elif rotation==180:x,y=1-x,1-y
        elif rotation==270:x,y=1-y,x
        points.append(((x0+x*cw)/width,(y0+y*ch)/height,z*(ch if rotation in (90,270) else cw)/width))
    return Hand(points,hand.label,hand.label_score)


def palm(hand, aspect):
    center=tuple(sum(hand.points[i][j] for i in (0,5,9,13,17))/5 for j in (0,1))
    size=max(distance(hand.points[0],hand.points[9],aspect),distance(hand.points[5],hand.points[17],aspect))
    return center,size


class RecoverySearch:
    ANGLES=(0,90,270,180)

    def __init__(self):
        self.attempts=self.successes=0
        self.reset()

    def reset(self):
        self.last_hand=self.last_seen=None
        self.pending=self.pending_at=None
        self.active=False
        self.misses=0
        self.angle_index=0
        self.last_attempt=-float("inf")
        self.phase="idle"
        self.last_scope=""
        self.last_angle=0

    def observe_primary(self,hands,now):
        self.misses=0
        self.pending=None
        self.active=False
        self.phase="primary"
        # The core's selected hand overwrites this after association when available.
        candidates=[h for h in hands if valid(h)]
        if candidates:
            self.last_hand=max(candidates,key=lambda h:palm(h,1)[1])
            self.last_seen=now

    def remember_selected(self,points,now):
        if points:
            self.last_hand=Hand(points)
            self.last_seen=now

    def bounds(self,width,height,now):
        if self.last_hand is None or now-self.last_seen>1.0:
            return (0,0,width,height),"full"
        p=self.last_hand.points
        xs=[max(0,min(width,x*width)) for x,_,_ in p]
        ys=[max(0,min(height,y*height)) for _,y,_ in p]
        cx,cy=(min(xs)+max(xs))/2,(min(ys)+max(ys))/2
        half=max(64,(max(max(xs)-min(xs),max(ys)-min(ys)))*.85)
        bounds=(max(0,int(cx-half)),max(0,int(cy-half)),min(width,int(cx+half)),min(height,int(cy+half)))
        return bounds,"crop"

    def search(self,frame,now,detect_image):
        self.misses+=1
        self.phase="searching"
        if self.misses<2:
            return []
        if not self.active and self.pending is None and now-self.last_attempt<.08:
            return []
        height,width=frame.shape[:2]
        bounds,scope=self.bounds(width,height,now)
        x0,y0,x1,y1=bounds
        angle=self.ANGLES[self.angle_index]
        crop=frame[y0:y1,x0:x1]
        if crop.size==0:return []
        if angle==90:crop=cv2.rotate(crop,cv2.ROTATE_90_CLOCKWISE)
        elif angle==180:crop=cv2.rotate(crop,cv2.ROTATE_180)
        elif angle==270:crop=cv2.rotate(crop,cv2.ROTATE_90_COUNTERCLOCKWISE)
        self.attempts+=1
        self.last_attempt=now
        self.last_scope,self.last_angle=scope,angle
        candidates=[]
        for raw in detect_image(crop):
            if not valid(raw):continue
            hand=undo_view(raw,bounds,angle,width,height)
            center,size=palm(hand,height/width)
            if not (.0<=center[0]<=1 and .0<=center[1]<=1 and size*width>=16):continue
            if scope=="crop" and self.last_hand is not None:
                previous,previous_size=palm(self.last_hand,height/width)
                if distance(center,previous,height/width)>max(.10,previous_size*1.5):continue
                if not .35< size/previous_size <2.8:continue
            candidates.append(hand)
        if not candidates:
            self.pending=None
            self.active=False
            self.angle_index=(self.angle_index+1)%len(self.ANGLES)
            return []
        hand=max(candidates,key=lambda h:palm(h,height/width)[1])
        center,size=palm(hand,height/width)
        consistent=False
        if self.pending is not None and now-self.pending_at<.16:
            old,old_size=palm(self.pending,height/width)
            consistent=(distance(center,old,height/width)<max(.035,size*.5) and .6<size/old_size<1.7)
        self.pending,self.pending_at=hand,now
        if not consistent:
            self.active=False
            self.phase="confirming"
            return []
        if not self.active:self.successes+=1
        self.active=True
        self.last_hand,self.last_seen=hand,now
        self.phase="recovered"
        return [hand]

    def diagnostics(self):
        return dict(phase=self.phase,attempts=self.attempts,successes=self.successes,
                    scope=self.last_scope,rotation=self.last_angle)
