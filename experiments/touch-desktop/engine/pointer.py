"""Continuous hand-to-screen mapping, including after clicks and hand re-entry."""
import math
from .filters import OneEuroFilter
from .observation import clamp


class Pointer:
    def __init__(self, area=.77, stability=.6):
        self.area = clamp(area, .35, .95)
        self.stability = clamp(stability)
        self.bounds = ((1-self.area)/2, (1-self.area)/2, (1+self.area)/2, (1+self.area)/2)
        self.active_bounds = self.bounds
        self.anchor_hand = None
        self.anchor_screen = (.5, .5)
        self.position = (.5, .5)
        self.filters = [OneEuroFilter(d_cutoff=4.) for _ in range(2)]
        self.at = None

    def anchor(self, hand, screen, now):
        l, t, r, b = self.bounds
        self.active_bounds = (min(l,hand[0]-.035),min(t,hand[1]-.035),max(r,hand[0]+.035),max(b,hand[1]+.035))
        self.anchor_hand = tuple(hand)
        self.anchor_screen = tuple(clamp(v, .005, .995) for v in screen)
        self.position = tuple(clamp(v) for v in screen)
        for f, v in zip(self.filters, self.position):
            f.reset()
            f(v, now)
        self.at = now

    def target(self, hand):
        if self.anchor_hand is None:
            l, t, r, b = self.bounds
            return (clamp((hand[0]-l)/(r-l)), clamp((hand[1]-t)/(b-t)))
        result = []
        for v, c, s, low, high in zip(hand, self.anchor_hand, self.anchor_screen, self.active_bounds[:2], self.active_bounds[2:]):
            result.append(clamp(s*(v-low)/max(.01, c-low) if v <= c else s+(1-s)*(v-c)/max(.01, high-c)))
        return tuple(result)

    def update(self, hand, now):
        target = self.target(hand)
        out = []
        for f, value in zip(self.filters, target):
            f.min_cutoff, f.beta = 2.8-1.5*self.stability, 18.-7*self.stability
            out.append(f(value, now))
        dt = min(.08, max(.008, now-self.at)) if self.at is not None else 1/30
        self.at = now
        # Last defence for a tracking discontinuity: no one-frame screen jumps.
        distance = math.dist(out, self.position)
        max_step = 2.7*dt
        if distance > max_step:
            out = [a+(b-a)*max_step/distance for a, b in zip(self.position, out)]
        deadband = .0008 + .0012*self.stability
        distance = math.dist(out, self.position)
        if distance > deadband:
            q = (distance-deadband)/distance
            self.position = tuple(clamp(a+(b-a)*q) for a, b in zip(self.position, out))
        # Permit the last pixel at each edge, without requiring a fingertip outside the camera.
        self.position = tuple(0. if v == 0 and p < .004 else 1. if v == 1 and p > .996 else p
                              for v, p in zip(target, self.position))
        return self.position
