"""The same decisions feed a practice pointer or Windows. No gesture rules here."""
import ctypes
import math
from .native import NativeMouse
from .cursor_motion import CursorMotion, PracticePointer
from .observation import clamp


class Output:
    def __init__(self):
        self.native = NativeMouse()
        self.practice = PracticePointer()
        self.practice.position = (960, 540)
        self.desktop = False
        self.motor = CursorMotion(self.practice)
        self.wheel_residue = 0.
        self.zoom_residue = 0.

    def bounds(self): return self.native.bounds() if self.desktop else (0, 0, 1920, 1080)

    def normalized(self):
        x, y, w, h = self.bounds()
        px, py = self.motor.native.cursor()
        return (clamp((px-x)/max(1, w-1)), clamp((py-y)/max(1, h-1)))

    def pixels(self, position):
        x, y, w, h = self.bounds()
        return (round(x+clamp(position[0])*(w-1)), round(y+clamp(position[1])*(h-1)))

    def enable(self, desktop):
        self.motor.close()
        self.desktop = bool(desktop)
        self.motor = CursorMotion(self.native if self.desktop else self.practice)
        self.wheel_residue = self.zoom_residue = 0.

    def stop(self):
        self.motor.cancel()
        self.enable(False)

    def takeover(self):
        if not self.desktop or self.motor.last_written is None: return False
        actual, owned = self.motor.feedback()
        return not owned and math.dist(actual, self.motor.last_written) > 10

    def apply(self, result):
        actions = result['actions']
        primary = 'hold' if result['dragging'] else 'none'
        clicks = []
        wheel = 0
        for action in actions:
            if action['type'] == 'down': primary = 'press'
            elif action['type'] == 'up': primary = 'release'
            elif action['type'] == 'click': clicks.append(action.get('button', 'left'))
            elif action['type'] == 'scroll':
                self.wheel_residue += -action['pixels'] # DOM down is positive; Windows up is positive.
                wheel += int(self.wheel_residue)
                self.wheel_residue -= int(self.wheel_residue)
        if not result['moving'] and primary == 'none' and not clicks and not wheel:
            primary = 'pending'
        packet = dict(primary=primary, clicks=clicks, wheel=wheel,
                      mode='hover' if result['moving'] else 'held', cursor=self.pixels(result['position']),
                      dragging=result['dragging'] and result['moving'])
        self.motor.apply(packet)
        if self.desktop:
            for action in actions:
                if action['type'] == 'zoom':
                    self.zoom_residue += math.log(action['factor'])/math.log(1.1)*120
                    # Most desktop apps consume complete Ctrl+wheel notches.
                    notches = math.trunc(self.zoom_residue/120)
                    if notches:
                        self.zoom_residue -= notches*120
                        self.native.api.keybd_event(0x11, 0, 0, 0)
                        try: self.native.wheel(notches*120)
                        finally: self.native.api.keybd_event(0x11, 0, 2, 0)
                elif action['type'] == 'zoomEnd': self.zoom_residue = 0.
                elif action['type'] == 'page':
                    key = 0x22 if action['direction'] > 0 else 0x21
                    self.native.api.keybd_event(key, 0, 0, 0)
                    self.native.api.keybd_event(key, 0, 2, 0)

    def close(self): self.motor.close()
