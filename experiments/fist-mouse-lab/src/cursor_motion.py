"""Frame-paced cursor output, independent of camera and preview rendering.

Only the newest target is retained. Clicks snap to the exact target before the
button event. Loss, stop, Escape and stale input discard unfinished motion.
"""
import math
import threading
import time


class Glide:
    def __init__(self,start,target,at,duration,velocity=(0.,0.)):
        self.start,self.target=tuple(start),tuple(target)
        self.at,self.duration=at,duration
        # Monotone Hermite tangents preserve speed between frames without
        # projecting beyond an observed target (including direction changes).
        self.tangents=tuple(max(0.,min(3.,v*duration/(b-a))) if b!=a else 0.
                            for a,b,v in zip(start,target,velocity))

    def position(self,now):
        t=max(0.,min(1.,(now-self.at)/max(.001,self.duration)))
        return tuple(a+(b-a)*((-2*t**3+3*t*t)+(t**3-2*t*t+t)*m+(t**3-t*t))
                     for a,b,m in zip(self.start,self.target,self.tangents))

    def velocity(self,now):
        if self.done(now):return (0.,0.)
        t=max(0.,(now-self.at)/self.duration)
        return tuple((b-a)/self.duration*((-6*t*t+6*t)+(3*t*t-4*t+1)*m+(3*t*t-2*t))
                     for a,b,m in zip(self.start,self.target,self.tangents))

    def done(self,now):return now>=self.at+self.duration


class CursorMotion:
    def __init__(self,native,autostart=True,clock=time.perf_counter):
        self.native=native;self.clock=clock;self.autostart=autostart
        self.lock=threading.RLock();self.wake=threading.Event();self.stop=threading.Event()
        self.thread=None;self.plan=None;self.last_submit=None;self.halted=''
        self.moves=0;self.glides=0;self.last_glide_ms=0.
        self.held=False;self.last_command=0.
        self.frame_interval=1/30
        self.last_written=None
        self.last_move_at=None;self.move_intervals=[]

    def feedback(self):
        """Distinguish our unfinished glide from a physical mouse takeover."""
        with self.lock:
            actual=self.native.cursor()
            owned=self.last_written is not None and math.dist(actual,self.last_written)<=2
            return actual,owned

    def _move(self,point):
        point=tuple(round(v) for v in point)
        if self.native.cursor()!=point:
            self.native.move(point);self.moves+=1
            now=self.clock()
            if self.last_move_at is not None:
                self.move_intervals.append((now-self.last_move_at)*1000)
                self.move_intervals=self.move_intervals[-120:]
            self.last_move_at=now
        self.last_written=point

    def _release(self):
        if self.held:
            try:self.native.release_left()
            except OSError as error:self.halted=str(error)
            self.held=False

    def resume(self):
        with self.lock:
            self._release();self.halted='';self.plan=None;self.last_submit=None;self.last_written=None

    def cancel(self):
        with self.lock:
            self.plan=None;self.last_submit=None;self.last_written=None;self._release()
        self.wake.set()

    def _ensure_thread(self):
        if self.autostart and self.thread is None:
            self.thread=threading.Thread(target=self._run,daemon=True,name='cursor-motion')
            self.thread.start()

    def submit(self,target):
        if len(target)!=2 or not all(math.isfinite(v) for v in target):raise ValueError('Invalid cursor target')
        now=self.clock()
        with self.lock:
            if self.halted:return
            target=tuple(round(v) for v in target)
            interval=now-self.last_submit if self.last_submit is not None else self.frame_interval
            self.last_submit=now
            if .012<=interval<=.10:
                self.frame_interval=.65*self.frame_interval+.35*interval
            if self.plan and self.plan.target==target:return
            actual=self.native.cursor()
            start=actual
            velocity=(0.,0.)
            if (self.plan and now-self.plan.at<=.15 and self.last_written is not None
                    and math.dist(actual,self.last_written)<=2):
                # Advance the mathematical trajectory, not the OS cursor. All
                # motion writes stay on the output clock instead of bunching
                # camera-thread and timer writes a fraction of a ms apart.
                velocity=self.plan.velocity(now)
                virtual=self.plan.position(now)
                start=tuple(max(min(a,b),min(max(a,b),v)) for a,b,v in zip(actual,target,virtual))
                velocity=tuple(v if abs(a-b)<1e-6 else 0. for a,b,v in zip(start,virtual,velocity))
            if actual==target:
                self.plan=None;return
            duration=max(.020,min(.052,self.frame_interval*1.05))
            self.plan=Glide(start,target,now,duration,velocity)
            self.last_glide_ms=duration*1000;self.glides+=1
        self._ensure_thread()
        self.wake.set()

    def _tick(self):
        with self.lock:
            now=self.clock()
            if self.native.escape_pressed():
                self.plan=None;self._release();self.halted='escape';return False
            if self.held and now-self.last_command>.22:
                self.plan=None;self._release();self.halted='stale';return False
            if self.plan is None or self.halted:return False
            if now-self.plan.at>.15:
                self.plan=None;self._release();return False
            point=tuple(round(v) for v in self.plan.position(now))
            try:
                self._move(point)
            except OSError as error:
                self.halted=str(error);self.plan=None;self._release();return False
            if self.plan.done(now):self.plan=None;return False
            return True

    def _run(self):
        deadline=self.clock()
        while not self.stop.is_set():
            moving=self._tick()
            if moving:
                # CPython 3.11+ on Windows uses a high-resolution timer for
                # sleep. Event.wait(8 ms) rounded to ~16 ms on the target PC.
                deadline=max(deadline+1/120,self.clock())
                time.sleep(max(.001,deadline-self.clock()))
            else:
                self.wake.wait(.025);self.wake.clear();deadline=self.clock()

    def apply(self,result):
        primary=result.get('primary','none')
        with self.lock:
            self.last_command=self.clock()
            if self.halted or self.native.escape_pressed():
                self.plan=None;self._release();self.halted=self.halted or 'escape';return
            if primary=='cancel':
                self.plan=None;self.last_submit=None;self._release()
            if primary in ('press','release') or result['clicks'] or result['wheel']:
                self.plan=None;self.last_submit=None
                self._move(result['cursor'])
                if primary=='press' and not self.held:
                    self.native.press_left();self.held=True
                elif primary=='release':self._release()
                for button in result['clicks']:
                    # Taps emit a complete click; only deliberate drags hold.
                    if button=='left' and primary in ('press','hold','release'):continue
                    self._release();self.native.click(button)
                if result['wheel']:
                    self._release();self.native.wheel(result['wheel'])
            elif primary=='pending':
                self.plan=None;self.last_submit=None
            elif primary=='hold':
                if result.get('dragging'):self.submit(result['cursor'])
                else:self.plan=None
            elif result.get('mode')=='fist':self.submit(result['cursor'])
            else:self.cancel()
            if self.held:self._ensure_thread()

    def diagnostics(self):
        with self.lock:return dict(glides=self.glides,moves=self.moves,
                                   glide_ms=round(self.last_glide_ms,1),halted=self.halted,left_held=self.held,
                                   output_period_ms=round(sorted(self.move_intervals)[len(self.move_intervals)//2],2) if self.move_intervals else 0.)

    def close(self):
        self.cancel();self.stop.set();self.wake.set()
        if self.thread:self.thread.join(timeout=1)


class PracticePointer:
    """In-memory backend: identical trajectory, no desktop input."""
    def __init__(self):self.position=(500,300)
    def cursor(self):return self.position
    def move(self,point):self.position=tuple(point)
    def escape_pressed(self):return False
    def press_left(self):pass
    def release_left(self):pass
    def click(self,button):pass
    def wheel(self,amount):pass
