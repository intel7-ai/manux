"""Recorded v0.3 rule decisions (`frame.rule.action`) as v0.4 intents, for replay comparisons.

A `hand-mouse-recording/1` frame stores what the v0.3 rules decided (`rule.action`:
`right_click`, `scroll_grab`, `move_cursor` ...). To check an `IntentFilter` such as `tcn_gate`
on a recording without porting the rules first, this turns those decisions into the intents a
v0.4 feature would send:

- a tap action (one frame in v0.3, e.g. `right_click`) -> one `tap` intent on its first frame;
- a span action (held over frames, e.g. `scroll_grab`) -> `begin` on its first frame, `hold` on
  the following ones, `end` on the first frame without it.

`V03_RULES` maps the two actions Dan's TCN was trained to confirm to the gesture ids of the v0.4
`pinch` feature (contracts section 6). Other actions give no intent unless a map names them.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

from manux.core.contracts import Intent

TAP, SPAN = "tap", "span"
V03_RULES: Mapping[str, Tuple[str, str]] = {
    "right_click": ("pinch.thumb_ring", TAP),
    "scroll_grab": ("pinch.scroll", SPAN),
}


def rule_action(record: Mapping[str, Any]) -> Optional[str]:
    return (record.get("rule") or {}).get("action")


class RuleIntents:
    """Stateful converter: feed one recorded frame at a time, get that frame's intents."""

    def __init__(self, rules: Mapping[str, Tuple[str, str]] = V03_RULES) -> None:
        for action, (gesture, kind) in rules.items():
            if kind not in (TAP, SPAN):
                raise ValueError(f"{action}: kind must be {TAP!r} or {SPAN!r}")
        self.rules = dict(rules)
        self._prev: Optional[str] = None
        self._open: Optional[str] = None          # span action with an open begin

    def step(self, t: float, action: Optional[str]) -> List[Intent]:
        out: List[Intent] = []
        if self._open is not None and action != self._open:
            out.append(self._intent(t, self._open, "end"))
            self._open = None
        rule = self.rules.get(action or "")
        if rule is not None:
            _, kind = rule
            if kind == TAP and action != self._prev:
                out.append(self._intent(t, action, "tap"))
            elif kind == SPAN:
                out.append(self._intent(t, action, "hold" if self._open == action else "begin"))
                self._open = action
        self._prev = action
        return out

    def finish(self, t: float) -> List[Intent]:
        """Close an open span at the end of the recording."""
        if self._open is None:
            return []
        out = [self._intent(t, self._open, "end")]
        self._open = None
        return out

    def _intent(self, t: float, action: str, phase: str) -> Intent:
        gesture = self.rules[action][0]
        return Intent(t=t, source=gesture.split(".", 1)[0], gesture=gesture, phase=phase,  # type: ignore[arg-type]
                      info={"rule": action})


def rule_intents(records: Iterable[Mapping[str, Any]],
                 rules: Mapping[str, Tuple[str, str]] = V03_RULES) -> Iterator[Tuple[int, List[Intent]]]:
    """(frame index, intents) for every recorded frame dict, ending open spans after the last one."""
    conv = RuleIntents(rules)
    t = 0.0
    i = -1
    for i, rec in enumerate(records):
        t = float(rec.get("t", 0.0))
        yield i, conv.step(t, rule_action(rec))
    tail = conv.finish(t)
    if tail:
        yield i, tail


def parse_rules(pairs: Iterable[str]) -> Dict[str, Tuple[str, str]]:
    """`action=gesture_id[:tap|span]` strings (command-line form) -> a rules map."""
    out: Dict[str, Tuple[str, str]] = {}
    for pair in pairs:
        action, _, rest = pair.partition("=")
        gesture, _, kind = rest.partition(":")
        if not action or not gesture:
            raise ValueError(f"expected action=gesture_id[:tap|span], got {pair!r}")
        out[action] = (gesture, kind or TAP)
    return out
