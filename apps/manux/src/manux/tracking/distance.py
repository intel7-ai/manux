"""Apparent palm size, not metres: stable, bounded distance assistance in original coordinates."""
from __future__ import annotations

import math
from collections import deque
from statistics import median


def palm_fraction(hand):
    """Palm-base span in frame heights; finger curls do not change the reference."""
    spans = []
    for a, b in ((0, 5), (0, 9), (0, 17), (5, 17)):
        p, q = hand.landmarks[a], hand.landmarks[b]
        length = math.hypot((p[0] - q[0]) / hand.frame.aspect, p[1] - q[1])
        if hand.world is not None:
            u, v = hand.world[a], hand.world[b]
            total = math.dist(u, v)
            projection = math.hypot(u[0] - v[0], u[1] - v[1]) / total if total > 1e-6 else 1.
            if projection < .55:
                continue  # edge-on is not evidence that the user moved farther away
            length /= projection
        if math.isfinite(length) and .01 <= length <= .8:
            spans.append(length)
    return max(spans) if spans else None


class DistanceAssist:
    def __init__(self):
        self.reset()

    def reset(self):
        self.gain = 1.
        self.fraction = 0.
        self.last_valid = None
        self.last_t = None
        self.samples = deque(maxlen=7)

    def update(self, hand, now, enabled=True):
        if not enabled:
            self.reset()
            return self.gain
        dt = min(.1, max(0., now - self.last_t)) if self.last_t is not None else 0.
        value = palm_fraction(hand) if hand is not None and hand.score >= .75 else None
        if value is None:
            if self.last_valid is not None and now - self.last_valid > 1.:
                self.reset()
            return self.gain
        self.last_t = now
        if self.last_valid is None or now - self.last_valid > .4:
            self.samples.clear()
        self.last_valid = now
        self.samples.append(value)
        if len(self.samples) < 5:
            return self.gain
        sample = median(self.samples)
        alpha = 1 - math.exp(-dt / .45)
        self.fraction = sample if not self.fraction else self.fraction + alpha * (sample - self.fraction)
        target = min(2.5, max(1., .16 / self.fraction))
        if abs(target - self.gain) > .06:
            limit = dt * (.8 if target > self.gain else 1.5)
            self.gain += max(-limit, min(limit, target - self.gain))
        return self.gain
