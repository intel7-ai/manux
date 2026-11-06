"""Physical continuity for new direct TCN training; labels never reset time."""
from __future__ import annotations

import numpy as np
from manux.perception.local3d import FEATURES, Local3dStream

PIPELINE = "continuous-v2"
MAX_GAP = .25


def inputs(frames, aspect, length):
    stream = Local3dStream(length)
    windows = np.zeros((len(frames), length, FEATURES), dtype=np.float32)
    usable = np.zeros(len(frames), dtype=bool)
    previous, valid = None, 0
    for i, frame in enumerate(frames):
        now = float(frame.get("t", i / 30.))
        if previous is not None and (now <= previous or now - previous > MAX_GAP):
            stream.reset()
            valid = 0
        window = stream.push_frame(frame.get("world"), frame.get("lm"), now, aspect)
        if window[-1, -1] != 1:
            stream.reset()
            valid = 0
        else:
            valid += 1
            windows[i] = window
            usable[i] = valid >= length
        previous = now
    return windows, usable
