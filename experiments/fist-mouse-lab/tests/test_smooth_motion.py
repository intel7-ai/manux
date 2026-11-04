"""Trajectory and feedback regressions; no camera or OS input."""
from dataclasses import replace
import math
import statistics
import pytest
from src.cursor_motion import CursorMotion,Glide
from src.fist_engine import FistEngine,Features
from test_drag_and_motion import Backend,result


@pytest.mark.parametrize('fps',[20,30,60])
def test_continuous_ramp_has_no_camera_frame_pauses_and_bounded_lag(fps):
    native=Backend();native.position=(0,100);now=[1.]
    motion=CursorMotion(native,False,lambda:now[0])
    next_frame=0.;xs=[];lags=[]
    for tick in range(240):
        t=tick/120;now[0]=1+t
        if t+1e-9>=next_frame:
            motion.submit((900*t,100));next_frame+=1/fps
        motion._tick()
        if t>.3:
            xs.append(native.position[0]);lags.append(t-native.position[0]/900)
    steps=[b-a for a,b in zip(xs,xs[1:])]
    assert min(steps)>0  # no repeated stall at every camera frame
    assert statistics.pstdev(steps)/statistics.mean(steps)<.35
    assert max(lags)<.065
    # Once the last observed point stops, reach it without extra drift.
    last_target=motion.plan.target if motion.plan else native.position
    now[0]+=.060;motion._tick()
    assert native.position==last_target
    now[0]+=.2;motion._tick()
    assert native.position==last_target


def test_rapid_reversal_never_crosses_the_new_target():
    native=Backend();now=[1.];motion=CursorMotion(native,False,lambda:now[0])
    motion.submit((400,100));now[0]+=.015;motion._tick()
    start=native.position[0]
    motion.submit((50,100))
    xs=[]
    for i in range(1,10):
        now[0]+=.008;motion._tick();xs.append(native.position[0])
    assert all(50<=x<=start for x in xs)
    assert xs==sorted(xs,reverse=True)
    assert xs[-1]==50


def test_camera_submission_only_updates_plan_and_never_bursts_os_writes():
    native=Backend();now=[1.];motion=CursorMotion(native,False,lambda:now[0])
    motion.submit((400,100));now[0]+=.008;motion._tick()
    events=list(native.events)
    now[0]+=.003;motion.submit((500,100))
    now[0]+=.002;motion.submit((600,100))
    assert native.events==events
    now[0]+=.003;motion._tick()
    assert len(native.events)==len(events)+1


def test_inflight_motion_does_not_reset_click_lock_but_physical_mouse_does():
    engine=FistEngine();pose=Features((.5,.5),.12,(.65,.43,.4,.35),.5)
    for i in range(15):engine.step(pose,1+i/30,(500,300),(0,0,1000,600))
    engine.last_output=(500,300);engine.position=(500,300)
    engine.last_click=(1.4,(500,300));engine.click_lock=(1.4,pose.anchor,(500,300))
    native=Backend();native.position=(480,300)
    motion=CursorMotion(native,False);motion.last_written=(480,300)
    actual,owned=motion.feedback()
    engine.step(pose,1.5,actual,(0,0,1000,600),cursor_owned=owned)
    assert engine.last_click is not None
    assert engine.position==(500,300)
    native.position=(400,400)  # a real mouse intervened, not our output
    actual,owned=motion.feedback()
    assert not owned
    engine.step(pose,1.53,actual,(0,0,1000,600),cursor_owned=owned)
    assert engine.last_click is None


def test_latest_target_is_not_postponed_by_repeated_identical_frames():
    native=Backend();now=[1.];motion=CursorMotion(native,False,lambda:now[0])
    motion.submit((300,100))
    for i in range(1,10):
        now[0]=1+i/120;motion.submit((300,100));motion._tick()
    assert native.position==(300,100)
    assert motion.plan is None


def test_variable_inference_time_does_not_change_filtered_hand_path():
    engines=[FistEngine(area_scale=.63),FistEngine(area_scale=.63)]
    cursors=[(500,300),(500,300)];paths=[[],[]]
    for i in range(90):
        sampled=1+i/30
        pose=Features((.4+i*.002,.4+.005*math.sin(i)),.12,(.65,.43,.4,.35),.5)
        for k,delay in enumerate((.02,.012 if i%2 else .035)):
            r=engines[k].step(pose,sampled+delay,cursors[k],(0,0,1920,1080),
                              cursor_owned=True,sample_time=sampled)
            cursors[k]=r['cursor'];paths[k].append(r['cursor'])
    # Startup pose debounce can differ by one frame; the steady trajectory must not.
    assert all(math.dist(a,b)<=1 for a,b in zip(paths[0][20:],paths[1][20:]))
