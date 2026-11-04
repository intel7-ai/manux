"""A visible camera rectangle maps to one fixed screen rectangle.

One Euro filtering is applied once in screen pixels. There is no integration of
frame deltas, velocity-dependent sensitivity or accumulated position error.
"""
import math
from .filters import OneEuroFilter
from .reach_area import valid_area


class AbsolutePointer:
    def __init__(self):
        self.reset()

    def reset(self):
        self.fx=OneEuroFilter(d_cutoff=3.)
        self.fy=OneEuroFilter(d_cutoff=3.)
        self.out=None
        self.geometry=None

    @staticmethod
    def area(scale,base=None):
        if valid_area(base):
            # Scale within the registered reach, never outside its safe limits.
            scale=max(.55,min(1.,float(scale)))
            left,top,right,bottom=base
            cx,cy=(left+right)/2,(top+bottom)/2
            hx,hy=(right-left)*scale/2,(bottom-top)*scale/2
            return cx-hx,cy-hy,cx+hx,cy+hy
        scale=max(.45,min(1.,float(scale)))
        margin=(1-scale)/2
        return margin,margin,1-margin,1-margin

    @classmethod
    def target(cls,anchor,aspect,bounds,scale,base=None):
        left,top,right,bottom=cls.area(scale,base)
        u=max(0.,min(1.,(anchor[0]-left)/(right-left)))
        v=max(0.,min(1.,(anchor[1]/aspect-top)/(bottom-top)))
        x,y,w,h=bounds
        # Pixel-sized edge capture avoids losing the final pixel to floating
        # point error or the stationary deadband. The interior remains linear.
        u=0. if u*(w-1)<=1.5 else 1. if (1-u)*(w-1)<=1.5 else u
        v=0. if v*(h-1)<=1.5 else 1. if (1-v)*(h-1)<=1.5 else v
        return (x+u*(w-1),y+v*(h-1)),(u,v)

    @classmethod
    def camera_point(cls,position,aspect,bounds,scale,base=None):
        x,y,w,h=bounds
        left,top,right,bottom=cls.area(scale,base)
        return (left+(position[0]-x)/max(1,w-1)*(right-left),
                (top+(position[1]-y)/max(1,h-1)*(bottom-top))*aspect)

    def update(self,anchor,aspect,bounds,now,scale=.76,stability=.55,base=None):
        geometry=(aspect,tuple(bounds),scale,tuple(base) if base else None)
        if geometry!=self.geometry:
            self.reset();self.geometry=geometry
        target,uv=self.target(anchor,aspect,bounds,scale,base)
        steady=max(0.,min(1.,stability))
        for f in (self.fx,self.fy):
            f.min_cutoff=4.8-3.6*steady
            f.beta=.035-.022*steady
        point=(self.fx(target[0],now),self.fy(target[1],now))
        # A pixel-sized deadband holds the cursor when the hand is still.
        # It never reduces the total travel or stops the last few edge pixels.
        radius=.45+1.65*steady
        if self.out is None:self.out=point
        distance=math.dist(point,self.out)
        if distance>radius:
            k=(distance-radius)/distance
            self.out=tuple(a+(b-a)*k for a,b in zip(self.out,point))
        self.out=tuple(target[i] if uv[i] in (0.,1.) else self.out[i] for i in (0,1))
        return self.out
