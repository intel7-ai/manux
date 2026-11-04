"""Separate a completed thumb tap from deliberate hold-then-move dragging."""
import math


class ThumbButton:
    HOLD_SECONDS = .28
    MOVE_SECONDS = .06
    VISIBILITY_GRACE = .14

    def __init__(self):
        self.reset()

    def reset(self):
        self.armed = False
        self.stage = 'idle'
        self.neutral_at = self.high_at = self.started = None
        self.drag_origin = self.move_at = self.hidden_at = None

    @property
    def pending(self):
        return self.stage == 'pending' or self.high_at is not None

    def cancel(self):
        self.reset()
        return 'cancel'

    def update(self, thumb, visible, point, size, now, enter, exit):
        if not visible:
            if self.hidden_at is None:self.hidden_at = now
            self.high_at = None
            # Never start a click/drag using a model's out-of-image thumb.
            if now-self.hidden_at > self.VISIBILITY_GRACE:return self.cancel()
            return 'pending' if self.pending or self.stage == 'drag' else 'none'
        self.hidden_at = None
        if thumb <= exit:
            if self.neutral_at is None:self.neutral_at = now
            self.high_at = None
            event = 'none'
            if self.stage != 'idle' and now-self.neutral_at >= .02:
                event = 'release' if self.stage == 'drag' else 'click'
                self.stage = 'idle'
                self.started = self.drag_origin = self.move_at = None
            if now-self.neutral_at >= .045:self.armed = True
            return event if event != 'none' else 'pending' if self.stage != 'idle' else 'none'
        self.neutral_at = None
        if self.stage == 'idle':
            if self.armed and thumb >= enter:
                if self.high_at is None:self.high_at = now
                if now-self.high_at >= .025:
                    self.stage = 'pending';self.started = self.high_at
                    self.armed = False;self.high_at = None
                return 'pending'
            self.high_at = None
            return 'none'
        if self.stage == 'drag':return 'hold'
        # Ignore displacement caused by opening the thumb itself. Only motion
        # AFTER the hold is armed can begin a drag, in palm-relative units.
        if now-self.started >= self.HOLD_SECONDS:
            if self.drag_origin is None:self.drag_origin = point
            if math.dist(point,self.drag_origin) >= max(.006,size*.09):
                if self.move_at is None:self.move_at = now
                if now-self.move_at >= self.MOVE_SECONDS:
                    self.stage = 'drag';return 'press'
            else:self.move_at = None
        return 'pending'
