"""Use a comfortably reachable, fully visible hand range for the whole screen.

This calibrates coordinates, not a vision model. The inferred initial rectangle
uses only a previous movement test with sufficient two-axis coverage; it is not
an assertion that every logged pose matched the user's intention.
"""
from statistics import median
import math

DEFAULT_AREA=(.28,.34,.72,.74)


def quantile(values,fraction):
    ordered=sorted(values)
    return ordered[round((len(ordered)-1)*fraction)]


def valid_area(area):
    return (isinstance(area,(list,tuple)) and len(area)==4
            and all(isinstance(v,(int,float)) and math.isfinite(v) for v in area)
            and .02<=area[0]<area[2]<=.98 and .02<=area[1]<area[3]<=.98
            and area[2]-area[0]>=.12 and area[3]-area[1]>=.12)


def fully_visible(points,margin=.02):
    return bool(points and len(points)==21 and all(
        len(p)==3 and all(math.isfinite(v) for v in p) and
        all(margin<=p[a]<=1-margin for a in (0,1)) for p in points))


def suggest_from_movement(samples):
    anchors=[]
    for row in samples:
        features=row.get('features')
        if (row.get('tracking')!='tracked' or row.get('result',{}).get('mode')!='fist'
                or not features or not fully_visible(row.get('raw'))):continue
        aspect=features.get('aspect',row.get('height',1)/row.get('width',1))
        anchors.append((features['anchor'][0],features['anchor'][1]/aspect))
    if len(anchors)<90:return None
    xs,ys=zip(*anchors)
    area=(quantile(xs,.02),quantile(ys,.02),quantile(xs,.98),quantile(ys,.98))
    # Do not infer a full workspace from a resting hand or a single-axis stroke.
    if not valid_area(area) or area[2]-area[0]<.25 or area[3]-area[1]<.25:return None
    return list(area)


class ReachCalibration:
    duration=8.
    def __init__(self,now):
        self.start=now;self.upper=[];self.lower=[]

    def update(self,features,points,now):
        elapsed=now-self.start
        if elapsed<2:return '손 전체가 보이는 편한 왼쪽 위 위치로 주먹을 옮겨주세요'
        if elapsed<4:
            destination=self.upper
            message='왼쪽 위 위치를 유지해주세요 · 카메라 끝까지 갈 필요 없습니다'
        elif elapsed<6:return '이제 편한 오른쪽 아래 위치로 주먹을 옮겨주세요'
        elif elapsed<8:
            destination=self.lower
            message='오른쪽 아래 위치를 유지해주세요 · 손 전체가 보여야 합니다'
        else:return '범위 확인 중'
        if features and fully_visible(points) and max(features.fingers)<.90:
            destination.append((features.anchor[0],features.anchor[1]/features.aspect))
        return message

    def finish(self):
        if min(len(self.upper),len(self.lower))<20:
            return None,'손 전체가 잡힌 표본이 부족합니다 · 기존 이동 범위를 유지합니다'
        for samples in (self.upper,self.lower):
            for axis in (0,1):
                values=[p[axis] for p in samples]
                if quantile(values,.9)-quantile(values,.1)>.08:
                    return None,'위치를 잠깐 유지해주세요 · 기존 이동 범위를 유지합니다'
        left,top=(median(p[i] for p in self.upper) for i in (0,1))
        right,bottom=(median(p[i] for p in self.lower) for i in (0,1))
        area=(left,top,right,bottom)
        if not valid_area(area):
            return None,'왼쪽 위와 오른쪽 아래가 충분히 떨어져야 합니다 · 기존 범위를 유지합니다'
        # Reach the edge a little before the captured limit, keeping room for
        # small changes in hand shape while clicking an edge target.
        dx,dy=(right-left)*.025,(bottom-top)*.025
        area=[left+dx,top+dy,right-dx,bottom-dy]
        if not valid_area(area):return None,'이동 범위가 너무 좁습니다 · 기존 범위를 유지합니다'
        return area,'저장했습니다 · 지정한 손 이동 범위가 모니터 전체에 연결됩니다'
