"""Engine-owned toggle routing and safety at pause/busy/tutorial boundaries."""
from manux.actions.bindings import Binding
from manux.actions.router import Router
from manux.core.contracts import Activity, FrameInfo, HandObservation, Intent, OutputResult, OutputTarget, Stage
from manux.engine.commands import ResetPractice, RequestMode, SetBusy, SetControl, SetStage
from manux.engine.pipeline import Engine
from manux.features.control import ControlFeature, V_SIGN


class Sink:
    def __init__(self, target):
        self.target, self.commands, self.releases = target, [], []
    def apply(self, cmd):
        self.commands.append(cmd)
        return OutputResult(True)
    def release_all(self, reason):
        self.releases.append(reason)
    def close(self):
        pass


class Pointer:
    id, modes, gestures = "pointer", {"pointer"}, ("pointer.move",)
    def __init__(self):
        self.steps, self.resets = 0, []
    def step(self, hand, ctx):
        self.steps += 1
        return [Intent(ctx.frame.t, self.id, "pointer.move", "hold", point=(.5, .5))]
    def reset(self, reason):
        self.resets.append(reason)
    def activity(self):
        return Activity()


def pose(v=True):
    pts = [(0.5, .8, 0)] * 21
    pts[4] = (.52, .64, 0)
    for mcp, tip, x in ((5,8,.44),(9,12,.48),(13,16,.52),(17,20,.56)):
        pts[mcp] = (x,.6,0)
        pts[tip] = (x,.4 if mcp in (5,9) or not v else .68,0)
    return tuple(pts)


def build():
    control, pointer = ControlFeature(), Pointer()
    sinks = {t: Sink(t) for t in OutputTarget}
    e = Engine([control, pointer], [], Router({V_SIGN: Binding("control.toggle"),
                "pointer.move": Binding("mouse.move")}), sinks, lambda fid: {})
    return e, pointer, sinks


def step(e, t, v=True):
    f = FrameInfo(round(t*100), t, 1280,720,True)
    return e.step(f, HandObservation(f, pose(v), "Right", .9))


def hold(e, start, v=True, count=18):
    return [step(e, start+n*.05, v) for n in range(count)]


def test_main_off_runs_only_v_then_releases_and_toggles_once():
    e, p, sinks = build()
    e.commands.put(SetStage(Stage.MAIN))
    snaps = hold(e, 1)
    assert e.state.control_on and p.steps == 0
    assert sum("control:on" in s.events for s in snaps) == 1
    assert all(not sink.commands for sink in sinks.values())
    hold(e, 2, count=60)
    assert e.state.control_on and p.steps == 0
    step(e, 5.1, False)
    assert p.steps == 1 and sinks[OutputTarget.WINDOWS].commands
    hold(e, 5.2)
    assert not e.state.control_on
    assert "pause" in sinks[OutputTarget.WINDOWS].releases
    before = p.steps
    hold(e, 6.2, False)
    assert p.steps == before


def test_tutorial_reports_success_without_changing_output_or_sending_os_commands():
    e, p, sinks = build()
    e.commands.put(SetStage(Stage.TUTORIAL))
    snaps = hold(e, 1)
    assert any(("control.toggle", "tap", "ok") in s.results for s in snaps)
    assert e.state.output is OutputTarget.PRACTICE and not e.state.control_on
    assert not sinks[OutputTarget.WINDOWS].commands


def test_busy_never_accumulates_a_toggle_and_needs_fresh_hold():
    e, p, sinks = build()
    e.commands.put(SetStage(Stage.MAIN))
    e.commands.put(SetBusy("recording", True))
    hold(e, 1)
    assert not e.state.control_on
    e.commands.put(SetBusy("recording", False))
    assert not step(e, 2).control_on
    assert hold(e, 2.1)[-1].control_on


def test_practice_reset_releases_resets_and_exits_motion_only_in_tutorial():
    e, p, sinks = build()
    e.commands.put(SetStage(Stage.TUTORIAL))
    e.commands.put(RequestMode("motion"))
    step(e, 1, False)
    e.commands.put(ResetPractice())
    snap = step(e, 1.1, False)
    assert "practice:reset" in snap.events and snap.mode == "pointer"
    assert "stage_change" in p.resets and "stage_change" in sinks[OutputTarget.PRACTICE].releases
    e.commands.put(SetStage(Stage.MAIN))
    e.commands.put(RequestMode("motion"))
    e.commands.put(ResetPractice())
    assert step(e, 2, False).mode == "motion"
