"""The Windows output sink: executes `ActionCommand`s as real input (docs/contracts.md section 8).

Responsibilities are split over small parts (v0.3 had them all in `MouseController`):
  cursor.CursorMapper   normalized point -> screen pixels (relative / absolute, monitors, gain)
  buttons.Buttons/Keys  clicks, drag button, key chords
  scroll.GrabScroll     grab scroll + inertia; scroll.ScrollPump spreads wheel amounts over time
  backend.InputBackend  the only thing that touches the OS (win32.Win32Backend; FakeBackend in tests)

Contract:
- `apply` never raises; it returns `OutputResult(accepted, reason)` with reason
  ok / unsupported (action or phase this sink does not execute) / blocked (safety rule) / error:<detail>.
- `end` of a drag or scroll that is not open (e.g. after `release_all`) is `ok` and does nothing.
  `hold` without its `begin` is `blocked`: nothing is pressed implicitly.
- `release_all` is idempotent: releases buttons and keys, ends the scroll grab and its inertia,
  drops unsent wheel amounts, re-anchors the cursor mapping.
- After `close()` every `apply` is `blocked`.
"""
from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Callable, List, Optional, Tuple

from manux.actions.catalog import spec
from manux.core.contracts import ActionCommand, OutputResult, OutputTarget
from manux.output.windows.backend import InputBackend
from manux.output.windows.buttons import KEY_CHORDS, Buttons, Keys
from manux.output.windows.config import WindowsOutputConfig
from manux.output.windows.cursor import CursorMapper
from manux.output.windows.scroll import GrabScroll, ScrollPump
from manux.platform.monitors import Monitor, ScreenLayout

OK = OutputResult(True, "ok")
UNSUPPORTED = OutputResult(False, "unsupported")
BLOCKED = OutputResult(False, "blocked")

# Actions that stop a coasting page (v0.3: clicks, drag and the task switcher did)
_STOPS_INERTIA = frozenset({"mouse.left_click", "mouse.right_click", "mouse.drag", "window.task_switcher"})


def _error(detail: object) -> OutputResult:
    text = str(detail).replace("\n", " ").strip() or type(detail).__name__
    return OutputResult(False, f"error:{text[:200]}")


class WindowsOutputSink:
    target = OutputTarget.WINDOWS

    def __init__(
        self,
        backend: InputBackend,
        screens: Optional[ScreenLayout] = None,
        config: Optional[WindowsOutputConfig] = None,
        *,
        threaded: bool = True,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        """threaded=False (tests, replay): wheel amounts are sent at once and nothing runs in the
        background; inertia advances on each `apply` and on `tick(now)`."""
        self.backend = backend
        self.screens = screens or ScreenLayout.detect()
        self.config = config or WindowsOutputConfig()
        self._threaded = threaded
        self._clock = clock
        self._lock = threading.RLock()
        self._closed = False
        start = None
        try:
            start = backend.cursor_pos()
        except Exception:  # noqa: BLE001
            pass
        self.cursor = CursorMapper(self.screens, self.config, start)
        self.buttons = Buttons(backend)
        self.keys = Keys(backend)
        self.pump = ScrollPump(self.config.scroll_smooth_tau)
        self.scroll = GrabScroll(self.config, self._wheel)
        self.dragging = False
        self.errors: List[str] = []           # errors swallowed by release_all / the pump thread (for logs)
        self._move_gesture: Optional[str] = None
        self._move_t = float("-inf")
        self._dbl_until = float("-inf")
        self._dbl_hand: Optional[Tuple[float, float]] = None
        self._pump_thread: Optional[threading.Thread] = None
        self._pump_stop = threading.Event()
        self._last_sent: Optional[Tuple[int, int]] = start
        # Glide: camera frames arrive at ~30 Hz, so the cursor would step once per frame. A thread moves it
        # from where it is to each new target over about one frame interval at `cursor_hz` instead.
        self._glide_from: Optional[Tuple[float, float]] = None
        self._glide_to: Optional[Tuple[int, int]] = None
        self._glide_t0 = 0.0
        self._glide_dur = 1 / 30
        self._frame_dt = 1 / 30
        self._last_target_t: Optional[float] = None
        self._shown: Optional[Tuple[float, float]] = None
        self._glide_thread: Optional[threading.Thread] = None
        self._glide_wake = threading.Event()
        self.glide_sends = 0                   # cursor positions sent by the glide thread (diagnostics)

    # ------------------------------------------------------------------ public API
    def apply(self, cmd: ActionCommand) -> OutputResult:
        try:
            with self._lock:
                if self._closed:
                    return BLOCKED
                s = spec(cmd.action)
                if s is None or cmd.phase not in s.phases:
                    return UNSUPPORTED
                if not self._threaded:
                    self.scroll.tick(cmd.t)
                if cmd.action in _STOPS_INERTIA and cmd.phase in ("tap", "begin"):
                    self._stop_inertia()
                handler = getattr(self, "_do_" + cmd.action.replace(".", "_"), None)
                if handler is not None:
                    return handler(cmd)
                keys = KEY_CHORDS.get(cmd.action)
                if keys is None:
                    return UNSUPPORTED
                self._end_drag()
                self.keys.chord(keys)
                return OK
        except Exception as exc:  # noqa: BLE001 - apply never raises
            return _error(exc)

    def release_all(self, reason: str = "") -> None:
        """Release everything that is held. Safe to call any number of times; never raises."""
        with self._lock:
            steps = (
                self.scroll.cancel,
                self.pump.clear,
                lambda: self._collect(self.keys.release_all()),
                lambda: self._collect(self.buttons.release_all()),
            )
            for step in steps:
                try:
                    step()
                except Exception as exc:  # noqa: BLE001
                    self.errors.append(f"release_all({reason}): {exc}")
            self.dragging = False
            self._stop_glide()
            self.cursor.reset()
            self._move_gesture = None
            self._dbl_until = float("-inf")

    def close(self) -> None:
        self.release_all("shutdown")
        with self._lock:
            self._closed = True
        self._pump_stop.set()
        self._glide_wake.set()
        for t in (self._pump_thread, self._glide_thread):
            if t is not None and t is not threading.current_thread():
                t.join(timeout=0.5)

    def tick(self, now: Optional[float] = None) -> None:
        """Advance scroll inertia (and, threaded, the wheel pump). The pump thread calls this every ~8 ms."""
        now = self._clock() if now is None else now
        with self._lock:
            if self._closed:
                return
            self.scroll.tick(now)
            if self._threaded:
                v, h = self.pump.step(now, self.config.scroll_smooth_tau)
                if v or h:
                    self.backend.wheel(v, h)

    def configure(self, config: WindowsOutputConfig) -> None:
        with self._lock:
            self.config = config
            self.cursor.config = config
            self.scroll.config = config

    def set_frame_aspect(self, aspect: float) -> None:
        if aspect > 0:
            self.configure(replace(self.config, frame_aspect=float(aspect)))

    def set_palm_pitch(self, pitch: Optional[float]) -> None:
        self._palm_input = True
        self._palm_pitch = pitch

    def next_monitor(self) -> Monitor:
        """Move to the next monitor's centre (absolute mode maps onto it from now on)."""
        with self._lock:
            self._stop_glide()
            target = self.cursor.next_monitor()
            self._send(*self.cursor.pos)
            return target

    @property
    def cursor_pos(self) -> Tuple[int, int]:
        return self.cursor.pos

    @property
    def scroll_coasting(self) -> bool:
        return self.scroll.coasting

    def holding(self) -> bool:
        """A drag button is down or a scroll is active / still coasting (the arbiter refuses mode entry then)."""
        with self._lock:
            return bool(self.dragging or self.scroll.active or self.scroll.coasting)

    # ------------------------------------------------------------------ helpers
    def _collect(self, errors: List[str]) -> None:
        self.errors.extend(errors)

    def _stop_inertia(self) -> None:
        self.scroll.stop_inertia()
        self.pump.clear()

    def _wheel(self, v: int, h: int) -> None:
        if not self._threaded or self.config.scroll_smooth_tau <= 0:
            self.backend.wheel(v, h)
            return
        self.pump.add(v, h)
        self._ensure_pump()

    def _ensure_pump(self) -> None:
        if self._pump_thread is not None and self._pump_thread.is_alive():
            return
        self._pump_stop.clear()
        self._pump_thread = threading.Thread(target=self._pump_loop, name="manux-scroll-pump", daemon=True)
        self._pump_thread.start()

    def _pump_loop(self) -> None:
        idle = 0
        while not self._pump_stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"pump: {exc}")
            pv, ph = self.pump.pending()
            busy = abs(pv) >= 1 or abs(ph) >= 1 or self.scroll.coasting
            idle = 0 if busy else idle + 1
            if idle > 250:            # ~2 s with nothing to send: end (restarted on demand)
                break
            self._pump_stop.wait(0.008)

    def _send(self, x: int, y: int) -> None:
        if (x, y) != self._last_sent:
            self.backend.set_cursor_pos(x, y)
            self._last_sent = (x, y)
        self._shown = (float(x), float(y))

    def _place(self, x: int, y: int) -> None:
        """New cursor target for this frame: glide there when the glide thread can run, else jump."""
        hz = float(self.config.cursor_hz)
        if not self._threaded or hz <= 0 or self._closed:
            self._send(x, y)
            return
        now = self._clock()
        if self._last_target_t is not None:
            dt = now - self._last_target_t
            if 0 < dt <= .2:   # a longer gap is a pause, not the frame rate
                self._frame_dt += .3 * (min(max(dt, 1 / 240), 1 / 15) - self._frame_dt)
        self._last_target_t = now
        start = self._shown if self._shown is not None else (
            (float(self._last_sent[0]), float(self._last_sent[1])) if self._last_sent is not None else None)
        if start is None:
            self._send(x, y)
            return
        self._glide_from, self._glide_to = start, (x, y)
        self._glide_t0, self._glide_dur = now, self._frame_dt
        self._ensure_glide()

    def _settle(self) -> None:
        """Finish the glide at once: a click or press must land where the hand points now."""
        if self._glide_to is not None:
            self._send(*self._glide_to)
        self._glide_from = self._glide_to = None

    def _stop_glide(self) -> None:
        """Stop gliding where the cursor is (pause, hand loss, release)."""
        self._glide_from = self._glide_to = None
        self._last_target_t = None

    def glide_step(self, now: Optional[float] = None) -> bool:
        """Move one glide step; False when there is nothing left to glide. The glide thread calls this."""
        now = self._clock() if now is None else now
        with self._lock:
            if self._closed or self._glide_to is None or self._glide_from is None:
                return False
            f = 1.0 if self._glide_dur <= 0 else min(1.0, (now - self._glide_t0) / self._glide_dur)
            (fx, fy), (tx, ty) = self._glide_from, self._glide_to
            x, y = fx + (tx - fx) * f, fy + (ty - fy) * f
            before = self._last_sent
            self._send(int(round(x)), int(round(y)))
            self._shown = (x, y)
            if self._last_sent != before:
                self.glide_sends += 1
            if f >= 1.0:
                self._glide_from = self._glide_to = None
                return False
            return True

    def _ensure_glide(self) -> None:
        self._glide_wake.set()
        if self._glide_thread is not None and self._glide_thread.is_alive():
            return
        self._glide_thread = threading.Thread(target=self._glide_loop, name="manux-cursor-glide", daemon=True)
        self._glide_thread.start()

    def _glide_loop(self) -> None:
        while not self._closed:
            self._glide_wake.clear()
            try:
                busy = self.glide_step()
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"glide: {exc}")
                busy = False
            if busy:
                time.sleep(1.0 / max(60.0, float(self.config.cursor_hz)))  # high-resolution on Windows (3.11+)
            elif not self._glide_wake.wait(1.0):
                break                                  # a second with nothing to glide: end (restarted on demand)

    def _move(self, cmd: ActionCommand, gain: float = 1.0) -> None:
        """Move the cursor for a command that carries a hand point."""
        assert cmd.point is not None
        fresh = (cmd.gesture != self._move_gesture
                 or cmd.t - self._move_t > self.config.reanchor_gap)
        self._move_gesture = cmd.gesture
        self._move_t = cmd.t
        if fresh:
            self.cursor.pause()
        if not self.cursor.anchored and self.config.movement_mode != "absolute":
            # start from the real cursor (the user may have moved the physical mouse)
            try:
                self.cursor.warp(*self.backend.cursor_pos())
                self._stop_glide()
                self._last_sent = self.cursor.pos
                self._shown = (float(self._last_sent[0]), float(self._last_sent[1]))
            except Exception:  # noqa: BLE001
                pass
        if self._uses_palm(cmd):
            position = self.cursor.step_palm(cmd.point[0], self._palm_pitch, cmd.t, gain)
        else:
            position = self.cursor.step(cmd.point, cmd.t, gain)
        self._place(*position)

    def _uses_palm(self, cmd: ActionCommand) -> bool:
        return getattr(self, "_palm_input", False) and cmd.gesture.startswith("pinch.")

    def _gain(self, cmd: ActionCommand) -> float:
        g = cmd.params.get("gain", 1.0)
        if isinstance(g, bool) or not isinstance(g, (int, float)) or not g > 0:
            raise ValueError(f"gain must be a positive number, got {g!r}")
        return float(g)

    def _end_drag(self) -> None:
        if self.dragging:
            self.dragging = False
            self.buttons.release("left")

    def _double_click_hold(self, cmd: ActionCommand) -> bool:
        """True while the cursor should stay still after a left click (Windows wants both clicks within ~4 px)."""
        if cmd.t >= self._dbl_until or cmd.point is None:
            return False
        point = cmd.point
        if self._uses_palm(cmd):
            pitch = self._palm_pitch
            y = -pitch / (150 * self.config.frame_aspect) if pitch is not None else (
                self._dbl_hand[1] if self._dbl_hand is not None else 0.0)
            point = (point[0], y)
        if self._dbl_hand is None:
            self._dbl_hand = point
        dx = point[0] - self._dbl_hand[0]
        dy = (point[1] - self._dbl_hand[1]) * self.config.frame_aspect
        self.cursor.pause()            # either way the next move starts fresh (no jump)
        if (dx * dx + dy * dy) ** 0.5 > self.config.double_click_move_tolerance:
            self._dbl_until = float("-inf")   # clearly moving away: release the hold now
            return False
        return True

    # ------------------------------------------------------------------ actions
    def _do_mouse_move(self, cmd: ActionCommand) -> OutputResult:
        if cmd.point is None:
            return _error("mouse.move needs a point")
        gain = self._gain(cmd)
        if self.scroll.active:
            return BLOCKED          # the hand drives the page while grabbing, not the cursor
        if self._double_click_hold(cmd):
            self._move_gesture = cmd.gesture
            self._move_t = cmd.t
            return OK
        self._move(cmd, gain)
        return OK

    def _click(self, cmd: ActionCommand, button: str) -> OutputResult:
        self._settle()
        self._end_drag()
        if self.scroll.active:
            self.scroll.end(cmd.t, launch=False)
        self.buttons.click(button)
        return OK

    def _do_mouse_left_click(self, cmd: ActionCommand) -> OutputResult:
        result = self._click(cmd, "left")
        self._dbl_until = cmd.t + max(0.0, self.config.double_click_window)
        self._dbl_hand = None if self._uses_palm(cmd) else cmd.point
        return result

    def _do_mouse_right_click(self, cmd: ActionCommand) -> OutputResult:
        return self._click(cmd, "right")

    def _do_mouse_drag(self, cmd: ActionCommand) -> OutputResult:
        if cmd.phase == "begin":
            if self.scroll.active:
                self.scroll.end(cmd.t, launch=False)
            if cmd.point is not None:
                self._move(cmd, self._gain(cmd))
            self._settle()
            self.buttons.press("left")
            self.dragging = True
            return OK
        if cmd.phase == "hold":
            if not self.dragging:
                return BLOCKED
            if cmd.point is not None:
                self._move(cmd, self._gain(cmd))
            return OK
        # end
        if self.dragging:
            if cmd.point is not None:
                self._move(cmd, self._gain(cmd))
            self._settle()
            self._end_drag()
        return OK

    def _do_mouse_scroll(self, cmd: ActionCommand) -> OutputResult:
        if cmd.phase == "begin":
            if cmd.point is None:
                return _error("mouse.scroll begin needs a point")
            gain = self._gain(cmd)
            self._settle()
            self._end_drag()
            self.cursor.pause()
            self.scroll.begin(cmd.point, cmd.t, gain)
            return OK
        if cmd.phase == "hold":
            if not self.scroll.active:
                return BLOCKED
            if cmd.point is not None:
                self.scroll.hold(cmd.point, cmd.t)
            return OK
        # end
        self.scroll.end(cmd.t)
        self._move_gesture = None
        if self._threaded and self.scroll.coasting:
            self._ensure_pump()
        return OK
