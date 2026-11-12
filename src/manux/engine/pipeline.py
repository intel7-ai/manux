"""The frame pipeline (docs/architecture.md section 6). Replaces the decision part of v0.3 qt_runtime._step.

    commands -> state -> hand-loss timer -> arbiter mode -> active features .step() -> mode intents
    -> intent filters -> router -> output sink of the current target -> Snapshot

Safety (contracts sections 5 and 8): before a stage/target change, mode change, hand loss past
`hand_lost_reset`, or a feature/filter error, the sink's `release_all()` runs first, so no button,
drag or scroll inertia is left behind. A feature that raises loses that frame's intents and is reset;
the app keeps running.

The engine never imports tracking, output implementations, features or Qt: the composition root
(`app.py`) hands them in. It runs on the engine thread only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from manux.actions.router import Router
from manux.core.contracts import (
    Activity, Feature, FrameContext, FrameInfo, HandObservation, Intent, IntentFilter, OutputResult,
    OutputSink, OutputTarget, Stage, CONTROL_TOGGLE_ACTION,
)
from manux.engine.arbiter import Arbiter, ModeChange
from manux.engine.commands import CommandQueue, Mark, RequestMode, ResetPractice, SetBaseMode, SetBusy, SetControl, SetStage
from manux.engine.snapshot import FeatureError, Snapshot
from manux.engine.state import EngineState

SettingsFor = Callable[[str], Mapping[str, object]]   # feature id -> its settings section
OUTPUT_ACTIVITY = "output"   # pseudo feature id under which the arbiter sees the output's held state


@dataclass
class EngineConfig:
    hand_lost_reset: float = 0.3      # app.hand_lost_reset (v0.3 default)
    base_mode: str = "pointer"
    motion_quiet: float = 1.0         # v0.3 motion_enter_quiet


class Engine:
    def __init__(self, features: Sequence[Feature], filters: Sequence[IntentFilter], router: Router,
                 sinks: Mapping[OutputTarget, OutputSink], settings_for: SettingsFor,
                 config: Optional[EngineConfig] = None, commands: Optional[CommandQueue] = None,
                 gesture_sources: Optional[Mapping[str, str]] = None) -> None:
        if OutputTarget.NONE not in sinks:
            raise ValueError("a sink for OutputTarget.NONE is required")
        ids = [f.id for f in features]
        if len(set(ids)) != len(ids):
            raise ValueError(f"feature ids must be unique: {ids}")
        self.features = list(features)
        self.gesture_sources = self._check_sources(features, gesture_sources or {})
        self.filters = list(filters)
        self.router = router
        self.sinks = dict(sinks)
        self.settings_for = settings_for
        self.config = config or EngineConfig()
        self.commands = commands or CommandQueue()
        self.state = EngineState()
        self.arbiter = Arbiter(self.config.base_mode, self.config.motion_quiet)
        self._lost_since: Optional[float] = None
        self._lost_reset_done = True
        self._sink_target = OutputTarget.NONE
        self._pending_events: List[str] = []
        self._claiming_sources: set[str] = set()
        self._control_claiming = False

    @staticmethod
    def _check_sources(features, sources):
        by_id = {f.id: f for f in features}
        if len(by_id) != len(features):
            raise ValueError("feature ids must be unique")
        for gid, fid in sources.items():
            if fid not in by_id or gid not in by_id[fid].gestures:
                raise ValueError(f"gesture source {gid}: {fid} is not a declaring feature")
        return dict(sources)

    def replace_pipeline(self, features, filters, router, gesture_sources) -> None:
        """Composition root, engine thread only. Validate before releasing or mutating anything."""
        checked = self._check_sources(features, gesture_sources)
        self._release("settings")
        self._reset(self.features, "settings")
        for feature in features:
            feature.reset("settings")
        for flt in filters:
            flt.reset("settings")
        self.features, self.filters = list(features), list(filters)
        self.router, self.gesture_sources = router, checked
        self._claiming_sources.clear()
        self._pending_events.append("model:reloaded")

    def pause_for_settings(self) -> None:
        """Runtime thread: consume pending state commands, release input, then reset before retuning."""
        self._drain(self._pending_events)
        self._release("settings")
        self._reset(self.features, "settings")
        self._claiming_sources.clear()
        self.state = EngineState(self.state.stage, False, self.state.busy)
        self._sink_target = self.state.output

    def prepare_model_reload(self) -> None:
        """Release before potentially slow model IO; retain the current pipeline on load failure."""
        self._release("settings")
        self._reset(self.features, "settings")
        self._claiming_sources.clear()
        self._pending_events.append("model:loading")

    # ------------------------------------------------------------------ helpers
    def sink(self, target: Optional[OutputTarget] = None) -> OutputSink:
        target = target or self.state.output
        return self.sinks.get(target) or self.sinks[OutputTarget.NONE]

    def _release(self, reason: str) -> None:
        self.sink(self._sink_target).release_all(reason)

    def _reset(self, features: Sequence[Feature], reason: str) -> None:
        for f in features:
            f.reset(reason)
        for flt in self.filters:
            flt.reset(reason)

    def _output_holding(self) -> bool:
        """The current sink's optional `holding()` (contracts section 8). Missing or failing = not holding."""
        holding = getattr(self.sink(self._sink_target), "holding", None)
        if not callable(holding):
            return False
        try:
            return bool(holding())
        except Exception:
            return False

    def _active(self) -> List[Feature]:
        active_modes = {self.arbiter.mode, self.arbiter.base_mode}
        return [f for f in self.features if active_modes & f.modes]

    def _activities(self) -> Dict[str, Activity]:
        out: Dict[str, Activity] = {}
        for f in self._active():
            try:
                out[f.id] = f.activity()
            except Exception:       # a broken activity() must not stop the frame
                out[f.id] = Activity()
        return out

    def _apply_mode_change(self, change: Optional[ModeChange], before: List[Feature]) -> None:
        if change is None or change.old == change.new:
            return
        self._release("mode_change")
        now_active = set(id(f) for f in self._active())
        self._reset([f for f in before if id(f) not in now_active], "mode_change")

    # ------------------------------------------------------------------ commands
    def _drain(self, events: List[str]) -> None:
        for cmd in self.commands.drain():
            old_state = self.state
            before = self._active()
            if isinstance(cmd, SetStage):
                self.state = EngineState(stage=cmd.stage, control_on=False if cmd.stage != old_state.stage
                                         else old_state.control_on, busy=old_state.busy)
            elif isinstance(cmd, SetControl):
                self.state = EngineState(old_state.stage, bool(cmd.on), old_state.busy)
            elif isinstance(cmd, ResetPractice):
                if self.state.stage is Stage.TUTORIAL:
                    self._release("stage_change")
                    self._reset(self.features, "stage_change")
                    self._apply_mode_change(self.arbiter.request(self.arbiter.base_mode), before)
                    self._claiming_sources.clear()
                    events.append("practice:reset")
            elif isinstance(cmd, SetBusy):
                self.state = old_state.with_busy(cmd.kind, cmd.on)
            elif isinstance(cmd, RequestMode):
                self._apply_mode_change(self.arbiter.request(cmd.mode), before)
            elif isinstance(cmd, SetBaseMode):
                self._apply_mode_change(self.arbiter.set_base(cmd.mode), before)
            elif isinstance(cmd, Mark):
                events.append(f"mark:{cmd.note}")
            if self.state.stage != old_state.stage or self.state.output != old_state.output:
                stage_changed = self.state.stage != old_state.stage
                reason = "pause" if not stage_changed and self.state.output is OutputTarget.NONE else "stage_change"
                self._release(reason)
                self._reset(self.features, reason)
        self._sink_target = self.state.output

    def _check_hand_loss(self, now: float, events: List[str]) -> None:
        if self._lost_since is None:
            self._lost_since, self._lost_reset_done = now, False
        if not self._lost_reset_done and now - self._lost_since >= self.config.hand_lost_reset:
            self._release("hand_lost")
            self._reset(self.features, "hand_lost")
            self._lost_reset_done = True
            events.append("hand_lost")

    def frame_missing(self, now: float) -> None:
        """Apply control changes and run the loss timer while capture supplies no frame."""
        self._drain(self._pending_events)
        self._check_hand_loss(now, self._pending_events)

    # ------------------------------------------------------------------ the frame
    def step(self, frame: FrameInfo, hand: Optional[HandObservation]) -> Snapshot:
        events, self._pending_events = self._pending_events, []
        errors: List[FeatureError] = []
        self._drain(events)
        now = frame.t

        if hand is None:
            self._check_hand_loss(now, events)
        else:
            self._lost_since = None

        intents: List[Intent] = []
        control_intents: List[Intent] = []
        control_features = [f for f in self._active() if f.id == "control"]
        for f in control_features:
            if self.state.busy or self.state.stage not in (Stage.MAIN, Stage.TUTORIAL):
                f.reset("pause")
                continue
            ctx = FrameContext(frame, self.state.stage, self.state.output, self.arbiter.mode,
                               settings=self.settings_for(f.id))
            try:
                out = list(f.step(hand, ctx))
                if any(i.source != f.id or i.gesture not in f.gestures for i in out):
                    raise ValueError("undeclared control gesture")
                control_intents.extend(out)
            except Exception as exc:
                errors.append(FeatureError(f.id, repr(exc)))
                self._release("error")
                f.reset("error")
        control_activity = self._activities()
        control_claiming = any(control_activity.get(f.id, Activity()).holding for f in control_features)
        if control_claiming and not self._control_claiming:
            self._release("pause")
            self._reset([f for f in self.features if f.id != "control"], "pause")
        self._control_claiming = control_claiming
        suppress = (control_claiming or bool(control_intents)
                    or self.state.stage is Stage.MAIN and self.state.output is OutputTarget.NONE
                    or any(error.source == "control" for error in errors))
        activities = self._activities()
        for f in self._active():
            if f.id == "control" or suppress:
                continue
            mode_for_ctx = self.arbiter.base_mode if self.arbiter.mode == "motion" and f.id != "motion" else self.arbiter.mode
            ctx = FrameContext(frame, self.state.stage, self.state.output, mode_for_ctx,
                               settings=self.settings_for(f.id),
                               others={k: v for k, v in activities.items() if k != f.id},
                               active_mode=self.arbiter.mode)
            try:
                out = list(f.step(hand, ctx))
                for intent in out:
                    if intent.source != f.id or intent.gesture not in f.gestures and not _mode_gesture(intent, f.id):
                        raise ValueError(f"undeclared gesture {intent.gesture!r} from {f.id}")
                intents.extend(i for i in out if self.gesture_sources.get(i.gesture, f.id) == f.id)
            except Exception as exc:
                errors.append(FeatureError(f.id, repr(exc)))
                self._release("error")
                self._reset([f], "error")

        # An authoritative classifier owns the frame while confirming/holding a recognized pose.
        # Keep end events from competing features, but never execute a second rule action or cursor
        # movement alongside it. A takeover from a rule span releases it before the new model action.
        authoritative = set(self.gesture_sources.values())
        claiming = {fid for fid, act in self._activities().items() if fid in authoritative and act.holding}
        claiming.update(i.source for i in intents if i.source in authoritative and i.phase in ("tap", "begin", "hold"))
        if claiming:
            if claiming - self._claiming_sources and self._output_holding():
                self._release("settings")
                self._reset([f for f in self._active() if f.id not in claiming], "settings")
            intents = [i for i in intents if i.source in claiming or i.phase == "end"]
        self._claiming_sources = claiming

        # mode intents go to the arbiter. Besides the features' Activity it sees the output's own state
        # (a drag button down, scroll inertia still coasting), which v0.3 also blocked motion entry on.
        before = self._active()
        arbiter_view = self._activities()
        if self._output_holding():
            arbiter_view[OUTPUT_ACTIVITY] = Activity(holding=True)
        intents, changes = self.arbiter.handle(now, intents, arbiter_view)
        for change in changes:
            if not change.why.startswith("refused"):
                self._apply_mode_change(change, before)
            events.append(f"mode:{change.old}->{change.new}:{change.why}")

        # filters (e.g. the TCN gate)
        filter_mode = self.arbiter.base_mode if self.arbiter.mode == "motion" else self.arbiter.mode
        ctx = FrameContext(frame, self.state.stage, self.state.output, filter_mode,
                           active_mode=self.arbiter.mode)
        for flt in self.filters:
            if suppress:
                continue
            try:
                flt.observe(hand, ctx)
                intents = list(flt.filter(intents, ctx))
            except Exception as exc:
                errors.append(FeatureError(flt.id, repr(exc)))
                intents = []
                self._release("error")
                flt.reset("error")

        intents.extend(control_intents)
        intents.sort(key=lambda intent: intent.phase != "end")
        commands, routed = self.router.route_all(intents)
        sink = self.sink(self._sink_target)
        results: List[Tuple[str, str, OutputResult]] = []
        toggles = [cmd for cmd in commands if cmd.action == CONTROL_TOGGLE_ACTION]
        if toggles:
            self._release("pause")
            self._reset([f for f in self.features if f.id != "control"], "pause")
            if self.state.stage is Stage.MAIN and not self.state.busy:
                self.state = EngineState(self.state.stage, not self.state.control_on, self.state.busy)
                self._sink_target = self.state.output
                events.append(f"control:{'on' if self.state.control_on else 'off'}")
                results.append((CONTROL_TOGGLE_ACTION, "tap", OutputResult(True)))
            elif self.state.stage is Stage.TUTORIAL and not self.state.busy:
                events.append("control:practice")
                results.append((CONTROL_TOGGLE_ACTION, "tap", OutputResult(True)))
            else:
                results.append((CONTROL_TOGGLE_ACTION, "tap", OutputResult(False, "blocked")))
            # No pending move, click, begin or tick may cross the toggle boundary.
            commands = []
            sink = self.sink(self._sink_target)
        for cmd in commands:
            try:
                result = sink.apply(cmd)
            except Exception as exc:          # sinks must not raise; contain it anyway
                result = OutputResult(False, f"error:{exc!r}")
            results.append((cmd.action, cmd.phase, result))
        tick = getattr(sink, "tick", None)
        if callable(tick) and not toggles:
            try:
                tick(now)
            except Exception as exc:
                errors.append(FeatureError("output", repr(exc)))

        return Snapshot(
            seq=frame.seq, t=now, stage=self.state.stage, output=self.state.output, mode=self.arbiter.mode,
            control_on=self.state.control_on, busy=tuple(sorted(self.state.busy)), has_hand=hand is not None,
            intents=tuple((i.gesture, i.phase) for i in intents),
            unrouted=tuple((r.intent.gesture, r.reason) for r in routed if r.command is None),
            results=tuple((a, p, r.reason) for a, p, r in results),
            activities=tuple(sorted((k, v.holding) for k, v in self._activities().items())),
            events=tuple(events), errors=tuple(errors),
        )

    def shutdown(self) -> None:
        for target in {self._sink_target, OutputTarget.NONE}:
            self.sink(target).release_all("shutdown")
        self._reset(self.features, "shutdown")
        for s in self.sinks.values():
            s.close()


def _mode_gesture(intent: Intent, fid: str) -> bool:
    return intent.gesture in (f"{fid}.enter", f"{fid}.exit")
