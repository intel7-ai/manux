"""Synthetic hand builders copied from a700452 tests (no recorded user joints)."""
import numpy as np
from types import SimpleNamespace

def hand_world(pinch=0.0, wiggle=0.0):
    """21 joints of an open hand. `pinch` 1 brings the index tip to the thumb tip, `wiggle` shifts the fingertips sideways."""
    w = np.zeros((21, 3), np.float64)
    for i, (x, y) in {1: (-.3, .2), 2: (-.45, .4), 3: (-.55, .6), 4: (-.65, .8), 5: (-.25, 1.0), 6: (-.25, 1.3), 7: (-.25, 1.5),
                      8: (-.25, 1.7), 9: (0, 1.0), 10: (0, 1.35), 11: (0, 1.6), 12: (0, 1.8), 13: (.2, .95), 14: (.2, 1.25),
                      15: (.2, 1.45), 16: (.2, 1.6), 17: (.4, .85), 18: (.4, 1.05), 19: (.4, 1.2), 20: (.4, 1.3)}.items():
        w[i] = (x, y, 0)
    w[8] = w[8] * (1 - pinch) + w[4] * pinch
    w[[8, 12, 16, 20], 0] += wiggle
    return w * .08

def pinched_hand(cx, cy, scale=.25):
    """A hand with thumb and index tips together, its palm centre at (cx, cy) of the camera image."""
    w = hand_world()
    pts = [[.5 + p[0] * scale, .5 + p[1] * scale] for p in w]
    pts[4] = list(pts[8])
    palm = [sum(pts[i][0] for i in (0, 5, 9, 13, 17)) / 5, sum(pts[i][1] for i in (0, 5, 9, 13, 17)) / 5]
    return [SimpleNamespace(x=x - palm[0] + cx, y=y - palm[1] + cy, z=0) for x, y in pts]

def open_hand(cx, cy):
    lm = pinched_hand(cx, cy)
    lm[4] = SimpleNamespace(x=lm[4].x - .2, y=lm[4].y + .1, z=0)
    return lm

