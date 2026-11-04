"""Camera measurements in palm coordinates; no input events in this module."""
from dataclasses import dataclass
from statistics import median
import math


def clamp(v, lo=0., hi=1.): return max(lo, min(hi, v))
def sub(a, b): return tuple(x-y for x, y in zip(a, b))
def dot(a, b): return sum(x*y for x, y in zip(a, b))
def unit(v):
    n = math.sqrt(dot(v, v))
    return tuple(x/max(n, 1e-8) for x in v)
def cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


@dataclass
class Observation:
    tip: tuple
    palm: tuple
    size: float
    fingers: tuple
    signature: tuple
    pinch: float
    edge: float = 1.
    visible: tuple = (True, True, True, True)
    thumb_visible: bool = True
    aspect: float = .5625
    thumb_local: tuple | None = None
    pinches: tuple | None = None

    @property
    def open(self):
        return all(self.visible) and min(self.fingers) > .93 and median(self.fingers) > .978

    @property
    def open_held(self):
        return all(self.visible) and min(self.fingers) > .86 and median(self.fingers) > .94

    @property
    def folded(self):
        return sum(v < .84 for v in self.fingers) >= 2

    @property
    def unique_pinch(self):
        if not self.thumb_visible or not self.visible[0]: return False
        return self.pinch < .31 and (not self.pinches or self.pinch + .12 < min(self.pinches[1:]))

    def local_tip(self):
        return ((self.tip[0]-self.palm[0])/self.size,
                (self.tip[1]-self.palm[1])*self.aspect/self.size)

    @classmethod
    def from_record(cls, data):
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def observe(points, width, height):
    if not points or len(points) != 21: return None
    if not all(len(p) == 3 and all(math.isfinite(v) for v in p) for p in points): return None
    inside = lambda ids: all(0 <= points[i][0] <= 1 and 0 <= points[i][1] <= 1 for i in ids)
    if not inside((0, 5, 6, 7, 8, 9, 13, 17)): return None
    aspect = height/width
    p = [(x, y*aspect, z) for x, y, z in points]
    size = max(math.dist(p[0], p[9]), math.dist(p[5], p[17]))
    if size < .025: return None
    ratios = []
    for base in (5, 9, 13, 17):
        length = sum(math.dist(p[i], p[i+1]) for i in range(base, base+3))
        if length < .015: return None
        ratios.append(clamp(math.dist(p[base], p[base+3])/length))
    y = unit(sub(p[9], p[0]))
    v = sub(p[5], p[17])
    x = unit(sub(v, tuple(dot(v, y)*q for q in y)))
    z = unit(cross(x, y))
    tip = sub(p[8], p[5])
    signature = tuple(dot(tip, axis)/size for axis in (x, y, z)) + (1-ratios[0],)
    # The wrist and central knuckles don't move when the thumb/index articulates.
    palm = tuple(.5*points[0][a]+.25*points[9][a]+.25*points[13][a] for a in (0, 1))
    pinches = tuple(math.dist(p[4], p[i])/size for i in (8, 12, 16, 20))
    edge = math.hypot(p[5][0]-p[17][0], p[5][1]-p[17][1])/max(.001, math.dist(p[5], p[17]))
    return Observation(tuple(points[8][:2]), palm, size, tuple(ratios), signature,
                       pinches[0], edge, tuple(inside(range(b, b+4)) for b in (5, 9, 13, 17)),
                       inside((1, 2, 3, 4)), aspect,
                       tuple(dot(sub(p[4], p[5]), axis)/size for axis in (x, y, z)), pinches)
