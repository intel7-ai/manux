from dataclasses import replace
import math
import pytest
from src.fist_engine import Features,FistEngine,extract

FIST=Features((.5,.3),.12,(.65,.43,.4,.35),.5)
THUMB=replace(FIST,thumb=1.)
PINKY=replace(FIST,fingers=(.65,.5,.83,.97))
INDEX=replace(FIST,fingers=(.98,.5,.45,.4))
OPEN=replace(FIST,fingers=(.99,.99,.99,.99))


class Run:
    def __init__(self):
        self.e=FistEngine(movement_mode='relative');self.t=10.;self.cursor=(500,300);self.results=[]
    def hold(self,f,seconds=.25):
        out=[]
        for _ in range(round(seconds*60)):
            self.t+=1/60
            r=self.e.step(f,self.t,self.cursor,(0,0,1000,600))
            self.cursor=r['cursor'];self.results.append(r);out.append(r)
        return out
    def clicks(self):return [c for r in self.results for c in r['clicks']]


def test_thumb_excursion_fires_once_and_release_rearms():
    run=Run();run.hold(FIST);run.hold(THUMB,1)
    assert not run.clicks()
    run.hold(FIST,.12)
    assert run.clicks()==['left']
    run.hold(THUMB,.12);run.hold(FIST,.12)
    assert run.clicks()==['left','left']


def test_double_is_two_clicks_never_three():
    run=Run();run.hold(FIST);run.hold(THUMB,.10);run.hold(FIST,.10);run.hold(THUMB,.10);run.hold(FIST,.10)
    assert run.clicks()==['left','left']
    assert sum(r['double'] for r in run.results)==1


def test_single_click_does_not_wait_for_double_timeout():
    run=Run();run.hold(FIST)
    run.hold(THUMB,.1)
    first=next(i for i,r in enumerate(run.hold(FIST,.1)) if r['clicks'])
    assert first/60<.08


def test_open_thumb_on_entry_cannot_click():
    run=Run();run.hold(THUMB,1);assert not run.clicks()
    run.hold(FIST,.2);run.hold(THUMB);run.hold(FIST,.1);assert run.clicks()==['left']


def test_pinky_one_click_holding_and_ring_can_follow():
    run=Run();run.hold(FIST);run.hold(PINKY,1)
    assert run.clicks()==['right']
    run.hold(FIST);run.hold(PINKY)
    assert run.clicks()==['right','right']


def test_pinky_after_missing_requires_fist_neutral():
    run=Run();run.hold(FIST);run.hold(None,.1);run.hold(PINKY)
    assert not run.clicks()


def test_slow_pinky_extension_crosses_intermediate_pose_without_losing_readiness():
    run=Run();run.hold(FIST)
    run.hold(replace(PINKY,fingers=(.65,.5,.82,.87)),.2)
    run.hold(PINKY)
    assert run.clicks()==['right']


def test_pinky_command_does_not_depend_on_thumb_rest_threshold():
    run=Run();run.hold(THUMB);run.hold(replace(PINKY,thumb=1.))
    assert run.clicks()==['right']


def test_translation_moves_without_clicking():
    run=Run();run.hold(FIST)
    for i in range(30):run.hold(replace(FIST,anchor=(.5+i*.002,.3)),1/60)
    assert run.cursor[0]>550 and run.cursor[1]==300 and not run.clicks()


def test_fast_motion_travels_further_than_same_slow_motion():
    end=[]
    for frames in (24,120):
        run=Run();run.hold(FIST)
        for i in range(frames):run.hold(replace(FIST,anchor=(.5+.06*(i+1)/frames,.3)),1/60)
        end.append(run.cursor[0])
    assert end[0]>end[1]+20


def test_loss_and_distant_reentry_stays_at_current_cursor():
    run=Run();run.hold(FIST)
    run.hold(replace(FIST,anchor=(.53,.3)))
    before=run.cursor;run.hold(None,.3)
    run.hold(replace(FIST,anchor=(.85,.48)))
    assert run.cursor==before and not run.clicks()


def test_reacquisition_no_click_even_if_thumb_already_extended():
    run=Run();run.hold(FIST);run.hold(None,.1);run.hold(THUMB)
    assert not run.clicks()


def test_unexplained_palm_jump_is_reanchored():
    run=Run();run.hold(FIST);run.hold(replace(FIST,anchor=(.8,.3)))
    assert run.cursor==(500,300)


def test_scroll_fixes_cursor_and_never_fires_thumb():
    run=Run();run.hold(FIST);run.hold(INDEX)
    before=run.cursor
    for i in range(30):run.hold(replace(INDEX,anchor=(.5,.3-i*.002),thumb=1.1),1/60)
    assert run.cursor==before and not run.clicks()
    assert sum(r['wheel'] for r in run.results)>0
    run.hold(replace(FIST,anchor=(.5,.242)))
    assert run.cursor==before


def test_wheel_stationary_does_not_repeat():
    run=Run();run.hold(INDEX)
    assert all(r['wheel']==0 for r in run.hold(INDEX,1))


def test_open_hand_and_conflicting_commands_stop():
    run=Run();run.hold(FIST)
    for f in (OPEN,replace(PINKY,fingers=(.98,.5,.7,.98))):
        run.hold(replace(f,anchor=(.52,.3)))
    assert not run.clicks() and run.cursor==(500,300)


def test_brief_thumb_glitch_does_not_click():
    run=Run();run.hold(FIST);run.hold(THUMB,1/60);run.hold(FIST)
    assert not run.clicks()


def test_tiny_motion_during_double_click_is_held_at_target():
    run=Run();run.hold(FIST);run.hold(THUMB,.1)
    before=run.cursor
    run.hold(replace(FIST,anchor=(.502,.3)),.1)
    run.hold(replace(THUMB,anchor=(.502,.3)),.1)
    run.hold(replace(FIST,anchor=(.502,.3)),.1)
    assert run.cursor==before and sum(r['double'] for r in run.results)==1


def test_physical_mouse_takeover_does_not_snap_back():
    run=Run();run.hold(FIST);run.cursor=(700,400);run.hold(FIST)
    assert run.cursor==(700,400)


def test_paused_frame_gap_reanchors_and_requires_new_click():
    run=Run();run.hold(FIST);run.t+=1
    run.hold(replace(THUMB,anchor=(.55,.3)))
    assert not run.clicks() and run.cursor==(500,300)


def test_finger_extension_hysteresis_avoids_mode_chatter():
    run=Run();run.hold(INDEX)
    for score in (.88,.86,.89,.84):
        r=run.hold(replace(INDEX,fingers=(score,.4,.4,.4)))
        assert r[-1]['mode']=='scroll'


def test_extraction_rejects_nan_and_cropped_command_tip():
    assert extract([(float('nan'),0.,0.)]*21,1920,1080) is None
    assert extract([],1920,1080) is None


def test_estimated_geometry_is_translation_and_scale_invariant():
    from pathlib import Path
    import json
    path=Path(__file__).resolve().parents[2]/'manux-tracking-lab/.runtime/reports/tracking-20261104-113123.json'
    if not path.exists():pytest.skip('Optional local replay fixture unavailable')
    samples=json.loads(path.read_text(encoding='utf-8'))['samples']
    row=next(r for r in samples if r.get('raw') and extract(r['raw'],r['width'],r['height']))
    p=row['raw'];base=extract(p,row['width'],row['height'])
    shifted=[(.15+x*.6,.1+y*.6,z*.6) for x,y,z in p]
    other=extract(shifted,row['width'],row['height'])
    assert other.thumb==pytest.approx(base.thumb)
    assert other.fingers==pytest.approx(base.fingers)
