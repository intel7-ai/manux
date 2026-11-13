"""A gesture about to be registered that another gesture also answers to (2026-11-12 request: "유저가 겹치는 동작을
등록하려고 하면 겹친다고 알려주는 기능", then "아예 못하게 하지는 말고 의사를 물어보게 해서 겹치는걸 원하면 겹치게").

That morning a look saved as an example of the taught "index only" shape was read as the rock sign in 43 of its 54
frames, and nothing said so: the close-window sign and the taught shape's command then ran on the same hand.

`find` takes what the app saw during a [보여주기] look (PoseProbe's result "seen": shape -> share of the hand frames)
and the shape it is about to be saved or connected as, and lists what else answers to that hand:
  - the signs with a detector of their own (rock sign = close window, V = control on/off, thumbs-up = motion mode),
    while their connection is switched on - also when the new gesture uses that very shape;
  - gestures made from another shape the look was partly read as (shape + hold / swipe / double pinch).
Nothing is refused here: the screen asks, and the person may register it anyway.

Honest limits: it knows what the look was READ as, frame by frame, not what two gestures will do in use; a shape
seen in under MIN_SHARE of the frames is not mentioned (a starting value, not measured); click and scroll pinches
of the hand modes are not listed (llm_binding warns about those shapes itself).
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

MIN_SHARE = .2      # a shape seen in at least this share of the look's hand frames counts as "also read as"

# shape -> (binding key, what the sign does) for the signs that have their own detector
TWINS = {"open_hand_back": "open_hand_down", "open_hand_down": "open_hand_back"}
SIGNS = {"rock_sign": ("rock_sign", "창 닫기"), "v_sign": ("v_sign", "마우스 제어 켜기/끄기"),
         "thumbs_up": ("thumbs_up", "모션 모드 켜기/끄기")}


def _name(posture: str) -> str:
    try:
        from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
        return POSTURE_NAMES_KO.get(posture, posture)
    except Exception:
        return posture


def _sign_on(registry, key: str) -> bool:
    try:
        binding = registry.get_binding(key)
        if not binding.get('enabled', True):
            return False
        if key == 'rock_sign':
            # The host installs this detector only for an explicitly saved/remapped binding.
            from manux.compat.hand_mouse.action_bindings import DEFAULT_GESTURE_BINDINGS
            from manux.compat.hand_mouse.llm_binding import History
            default = DEFAULT_GESTURE_BINDINGS[key]
            return any(binding.get(k) != default.get(k) for k in ('action_type', 'action_value', 'enabled', 'app_overrides')) or any(
                r.get('current') and r.get('gesture_key') == key and not r.get('app_match') for r in History().rows(registry))
        return True
    except Exception:
        return key != 'rock_sign'


def find(seen: Optional[Mapping[str, float]], label: str, registry=None, own_key: str = "") -> List[Dict[str, Any]]:
    """What else answers to the hand of this look: [{"posture", "name", "does", "share", "same"}], most seen first.
    `label`: the shape the look is saved / connected as ("" = a new shape without a name yet).
    `own_key`: the gesture being connected itself (never listed as overlapping with itself)."""
    shares = {str(k): float(v) for k, v in (seen or {}).items() if k and float(v) >= MIN_SHARE}
    # the back of the hand and the palm to the floor differ only by a tilt (pose_probe.faced): with a gesture on each,
    # a hand meant as one is read as the other now and then - said even when this look happened to read as one only
    twin = TWINS.get(label)
    if twin and twin not in shares:
        shares[twin] = 0.0
    if label and label in SIGNS:
        shares.setdefault(label, 1.0)                 # the new gesture uses a sign's own shape
    out: List[Dict[str, Any]] = []
    composites = dict(getattr(registry, "composite_gestures", {}) or {}) if registry is not None else {}
    for posture, share in sorted(shares.items(), key=lambda kv: -kv[1]):
        same = posture == label
        if posture in SIGNS and (registry is None or _sign_on(registry, SIGNS[posture][0])):
            does = SIGNS[posture][1]
            if posture == 'rock_sign' and registry is not None:
                from manux.compat.hand_mouse.action_bindings import format_action_badge
                binding = registry.get_binding('rock_sign')
                does = format_action_badge(binding.get('action_type', ''), binding.get('action_value', ''), binding.get('app_overrides'))
            out.append({"posture": posture, "name": _name(posture), "does": does,
                        "share": round(share, 2), "same": same})
        if same:
            continue                                  # other gestures of the same shape differ by their movement
        for key, comp in composites.items():
            if key == own_key or comp.get("base_posture") != posture or comp.get("enabled", True) is False:
                continue
            try:
                on = registry.get_binding(key).get("enabled", True) is not False
            except Exception:
                on = True
            if on:
                out.append({"posture": posture, "name": str(comp.get("name") or comp.get("display_name") or key),
                            "does": "", "share": round(share, 2), "same": False, "twin": share <= 0})
    return out


def words(items: Sequence[Dict[str, Any]], label_name: str = "") -> str:
    """The overlaps in one or two sentences ("" for none)."""
    if not items:
        return ""
    parts = []
    for it in items[:3]:
        what = f"'{it['name']}'" + (f" 신호({it['does']})" if it.get("does") else " 동작")
        if it.get("twin"):
            parts.append(what + "의 손 모양과 기울기만 달라 서로 섞여 읽힐 수 있어요")
            continue
        parts.append(what + ("와 같은 손 모양" if it.get("same") else f"(으)로도 읽혔어요 ({round(it['share'] * 100)}%)"))
    head = "이 손은 " + ", ".join(parts)
    if all(it.get("same") for it in items[:3]):
        head += "이에요"
    who = f"'{label_name}'" if label_name else "새 동작"
    return head + f". 등록하면 {who}과(와) 그 동작이 같은 손에 함께 실행될 수 있어요."


def mark_proposals(rows: List[Dict[str, Any]], seen: Optional[Mapping[str, float]], registry=None) -> List[Dict[str, Any]]:
    """The AI's proposals that would make a NEW gesture from an overlapping hand shape: the overlap is written into
    the warning and the line is left unticked, so the person ticks it knowingly (nothing is refused)."""
    for row in rows:
        posture = str(row.get("new_posture") or "")
        if not posture or not row.get("ok") or str(row.get("new_trigger", "")).startswith("motion_"):
            continue                                  # motion-mode gestures: the signs do not run there
        items = find(seen, posture, registry)
        if not items:
            continue
        text = words(items, _name(posture)) + " 겹쳐도 괜찮으면 체크하세요"
        row["warning"] = (row.get("warning", "").replace(" · 직접 확인하고 체크하세요", "") + " · " if row.get("warning") else "") + text
        row["risky"], row["checked"], row["overlap"] = True, False, True
    return rows
