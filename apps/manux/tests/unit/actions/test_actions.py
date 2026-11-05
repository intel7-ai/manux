"""Action catalog, binding validation and the router (docs/contracts.md section 7)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from manux.actions import CATALOG, Binding, Router, from_v03, resolve_bindings, spec
from manux.core.contracts import ACTION_ID_RE, Intent

DOC = Path(__file__).resolve().parents[3] / "docs" / "contracts.md"


def _doc_action_table():
    """{action id: (phases, params)} from the section 7 table; rows marked **제안** are skipped."""
    text = DOC.read_text(encoding="utf-8")
    section = text.split("### 액션 목록", 1)[1].split("###", 1)[0]
    rows = {}
    for line in section.splitlines():
        if not line.startswith("| `") or "**제안**" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        phases = frozenset(p.strip() for p in cells[1].split("/"))
        params = tuple(re.findall(r"`([a-z_]+)`", cells[2])) if len(cells) > 3 else ()
        for action in re.findall(r"`([^`]+)`", cells[0]):
            rows[action] = (phases, params)
    return rows


def test_catalog_matches_the_contract_table():
    table = _doc_action_table()
    assert set(table) == set(CATALOG), "catalog and docs/contracts.md section 7 list different actions"
    for action, (phases, params) in table.items():
        assert CATALOG[action].phases == phases, action
        assert CATALOG[action].params == params, action


def test_every_action_id_is_well_formed_and_v03_names_map_back():
    for action, s in CATALOG.items():
        assert ACTION_ID_RE.match(action) and s.id == action
    assert from_v03("drag_toggle") == "mouse.drag"
    assert from_v03("alt_tab") == "window.task_switcher"
    assert from_v03("back") == "nav.back"
    assert from_v03("grab") is None            # record & fling: not in the catalog yet (contract section 7)
    assert spec("mouse.teleport") is None


# ------------------------------------------------------------------ bindings
DEFAULTS = {"pinch.thumb_middle": "mouse.left_click", "pinch.thumb_index": "mouse.scroll",
            "pointer.move": "mouse.move"}
PHASES = {"pinch.thumb_middle": frozenset({"tap"}), "pinch.thumb_index": frozenset({"begin", "hold", "end"}),
          "pointer.move": frozenset({"hold"})}


def test_defaults_resolve_without_problems():
    bindings, problems = resolve_bindings(DEFAULTS, None, PHASES)
    assert problems == []
    assert bindings["pinch.thumb_middle"] == Binding("mouse.left_click")


def test_unknown_action_is_rejected_and_the_default_stays():
    bindings, problems = resolve_bindings(DEFAULTS, {"pinch.thumb_middle": "mouse.teleport"}, PHASES)
    assert bindings["pinch.thumb_middle"].action == "mouse.left_click"
    assert [(p.gesture, p.reason) for p in problems] == [("pinch.thumb_middle", "unknown_action")]


def test_phase_mismatch_is_rejected():
    # a tap gesture cannot drive a drag; a drag gesture cannot be a click
    bindings, problems = resolve_bindings(DEFAULTS, {"pinch.thumb_middle": "mouse.drag",
                                                     "pinch.thumb_index": "mouse.right_click"}, PHASES)
    assert {p.reason for p in problems} == {"phase_mismatch"}
    assert bindings["pinch.thumb_middle"].action == "mouse.left_click"
    assert bindings["pinch.thumb_index"].action == "mouse.scroll"


def test_params_must_be_declared_by_the_action():
    ok, problems = resolve_bindings({}, {"pointer.fast": {"action": "mouse.move", "params": {"gain": 3.0}}})
    assert problems == [] and ok["pointer.fast"].params["gain"] == 3.0
    bad, problems = resolve_bindings({}, {"pinch.thumb_middle": {"action": "mouse.left_click",
                                                                 "params": {"gain": 2}}})
    assert bad == {} and problems[0].reason == "bad_params"


def test_override_none_unbinds_and_bad_values_never_raise():
    bindings, problems = resolve_bindings(DEFAULTS, {"pinch.thumb_middle": None, "Bad Id": "mouse.move",
                                                     "pointer.move": 42, "pinch.x": {"action": 1}})
    assert "pinch.thumb_middle" not in bindings
    assert bindings["pointer.move"].action == "mouse.move"          # the bad override left the default
    assert {p.reason for p in problems} == {"bad_gesture", "bad_value"}


def test_a_bad_default_leaves_the_gesture_unbound():
    bindings, problems = resolve_bindings({"pinch.thumb_ring": "mouse.nope"})
    assert bindings == {} and problems[0].reason == "unknown_action"


# ------------------------------------------------------------------ router
def _intent(gesture, phase, point=None, t=1.0):
    return Intent(t=t, source=gesture.split(".")[0], gesture=gesture, phase=phase, point=point,
                  info={"param": "ignored"})


def test_router_copies_phase_point_and_binding_params():
    router = Router({"pinch.thumb_index": Binding("mouse.scroll", {"gain": 2.0})})
    r = router.route(_intent("pinch.thumb_index", "hold", (0.2, 0.3), t=4.5))
    assert r.reason == "ok"
    cmd = r.command
    assert (cmd.t, cmd.action, cmd.phase, cmd.point, cmd.gesture) == (
        4.5, "mouse.scroll", "hold", (0.2, 0.3), "pinch.thumb_index")
    assert dict(cmd.params) == {"gain": 2.0}           # never from Intent.info


def test_unbound_gesture_gives_no_command():
    r = Router({}).route(_intent("pinch.thumb_ring", "tap"))
    assert r.command is None and r.reason == "unbound"


def test_router_refuses_a_phase_the_action_does_not_accept():
    router = Router({"pinch.thumb_middle": Binding("mouse.left_click")})
    assert router.route(_intent("pinch.thumb_middle", "begin")).reason == "phase_mismatch"
    assert Router({"pinch.x": Binding("mouse.nope")}).route(_intent("pinch.x", "tap")).reason == "unknown_action"


def test_route_all_splits_commands_and_dropped():
    router = Router({"pinch.thumb_middle": Binding("mouse.left_click")})
    cmds, dropped = router.route_all([_intent("pinch.thumb_middle", "tap"), _intent("pinch.thumb_ring", "tap")])
    assert [c.action for c in cmds] == ["mouse.left_click"]
    assert [d.reason for d in dropped] == ["unbound"]


@pytest.mark.parametrize("action", sorted(CATALOG))
def test_each_catalog_action_routes_for_each_of_its_phases(action):
    router = Router({"test.g": Binding(action)})
    for phase in CATALOG[action].phases:
        r = router.route(_intent("test.g", phase, (0.5, 0.5)))
        assert r.command is not None and r.command.action == action


def test_router_multiplies_intent_scale_into_the_gain():
    """Per-frame speed (v0.3 hand-shape / long-press multiplier) arrives as Intent.scale (2026-11-05)."""
    router = Router({"pinch.move": Binding("mouse.move", {"gain": 2.0}), "pinch.drag": Binding("mouse.drag"),
                     "pinch.thumb_middle": Binding("mouse.left_click")})

    def scaled(gesture, phase, scale):
        intent = Intent(t=1.0, source="pinch", gesture=gesture, phase=phase, point=(0.5, 0.5), scale=scale)
        return dict(router.route(intent).command.params)

    assert scaled("pinch.move", "hold", 12.5) == {"gain": 25.0}       # binding gain x scale
    assert scaled("pinch.move", "hold", None) == {"gain": 2.0}
    assert scaled("pinch.drag", "hold", 15.0) == {"gain": 15.0}       # default gain 1
    assert scaled("pinch.thumb_middle", "tap", 3.0) == {}             # no gain on a click: ignored
    assert dict(router.bindings["pinch.move"].params) == {"gain": 2.0}   # the binding itself is unchanged
