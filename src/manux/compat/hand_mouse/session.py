"""Runtime wiring for the original probe and stores; host engine operations are injected."""
from __future__ import annotations

from collections import deque
import copy
import math
import threading
import time
from types import SimpleNamespace

from manux.compat.hand_mouse import custom_postures
from manux.compat.hand_mouse.action_bindings import get_action_registry
from manux.compat.hand_mouse.pose_probe import PoseProbe, hand_posture, relabelled, set_hand_label
from manux.compat.hand_mouse.virtual_hand import palm_centre, hand_px
from manux.compat.hand_mouse.paths import DATA_ROOT


class CustomizationSession:
    def __init__(self, install, make_shot, *, voice=lambda: None, draw_view=lambda: {"on": False}):
        self.commands = deque()
        self.install, self.make_shot, self.voice = install, make_shot, voice
        self.draw_snapshot = draw_view
        self.probe = PoseProbe()
        self.probe.log_dir = DATA_ROOT / "logs" / "pose_probe"
        self.lock = threading.RLock()
        self._view, self._shot = None, None
        self.closed = False
        self.profile = None
        custom_postures.register()
        self.install(get_action_registry())

    def request_voice_typing(self):
        # Explicit Windows voice typing button, independent of gesture output.
        self.voice()

    def drain(self):
        while self.commands:
            cmd = self.commands.popleft()
            if cmd[0] == "seq_reload":
                self.install(get_action_registry())
            elif cmd[0] == "binding_profile":
                from manux.compat.hand_mouse.action_bindings import use_binding_profile
                self.install(use_binding_profile(cmd[1]))
                self.profile = cmd[1]
            elif cmd[0] == "pose_probe":
                self.probe.start(time.perf_counter())
            elif cmd[0] == "pose_probe_cancel":
                self.probe.cancel()
            elif cmd[0] == "pose_teach_save":
                self._save_example(cmd[1])

    def _save_example(self, label):
        # qt_runtime._pose_teach_save, preserved apart from the host object names.
        from manux.compat.hand_mouse.posture_teach import save_example
        result = self.probe.result or {}
        frames = [f["lm"] for f in getattr(self.probe, "_log", []) if f.get("lm")]
        if result.get("state") != "done" or not frames or not label:
            return
        aspect = self.probe.aspect
        rule = [hand_posture([SimpleNamespace(x=p[0], y=p[1], z=p[2]) for p in lm], aspect, taught=False) for lm in frames]
        save_example(label, frames, rule)
        self.probe.result = relabelled(result, label)

    def observe(self, now, hand=None, image=None):
        if self.closed:
            return
        if self.probe.active and hand:
            lm = [SimpleNamespace(x=p[0], y=p[1], z=p[2]) for p in hand.landmarks]
            k = hand.frame.aspect
            self.probe.aspect = k
            set_hand_label(hand.handedness, "")
            posture = hand_posture(lm, k)
            palm, size = palm_centre(lm), hand_px(lm, 1.0, k)
            angles = (math.degrees(math.atan2(-(lm[9].y-lm[0].y)*k, lm[9].x-lm[0].x)),
                      math.degrees(math.atan2(-(lm[4].y-lm[0].y)*k, lm[4].x-lm[0].x)))
            self.probe.step(now, posture, (palm[0], palm[1]*k), size, angles,
                            [[round(p.x,4), round(p.y,4), round(p.z,4)] for p in lm],
                            shot=self.make_shot(image, lm) if image is not None else None)
        self.probe.finish_if_due(now)
        with self.lock:
            self._view = copy.deepcopy(self.probe.view(now))
            self._shot = ((self.probe.shot_seq, self.probe.shot, self.probe.clip)
                          if self.probe.shot_seq else None)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self._view), self._shot

    def close(self):
        self.closed = True
        self.probe.cancel()
