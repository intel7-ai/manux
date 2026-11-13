"""Attach explicitly saved source bindings to the current pipeline without replacing host defaults."""
from manux.actions.bindings import Binding
from manux.actions.router import Router

# Same physical events across the two runtimes. New gestures retain their source detector.
HOST_GESTURES = {
    "thumb_middle": ("pinch.thumb_middle",),
    "thumb_ring": ("pinch.thumb_ring",),
    "thumb_pinky": ("pinch.thumb_pinky",),
    "palm_side": ("pinch.thumb_tucked_open",),
    "thumb_index": ("pinch.scroll",),
    "thumb_middle_drag": ("pinch.drag",),
    "point_double": ("pointer.left_click",),
    "point_right": ("pointer.right_click",),
    "v_sign": ("control.v_sign",),
    "swipe_left": ("motion.swipe_left",),
    "swipe_right": ("motion.swipe_right",),
    "swipe_tab_left": ("motion.swipe_left_tucked",),
    "swipe_tab_right": ("motion.swipe_right_tucked",),
}


class CustomPipeline:
    def __init__(self, engine, factory, filter_factory, gesture_id, source_defaults, history_keys):
        self.engine, self.factory = engine, factory
        self.filter_factory, self.gesture_id = filter_factory, gesture_id
        self.defaults, self.history_keys = source_defaults, history_keys
        self.original, self.installed = {}, {}
        self.feature = None

    def install(self, registry):
        engine = self.engine
        bindings = dict(engine.router.bindings)
        for gid, installed in self.installed.items():
            if bindings.get(gid) == installed:
                if self.original.get(gid) is not None:
                    bindings[gid] = self.original[gid]
                else:
                    bindings.pop(gid, None)
        owned = {}
        explicit = self.history_keys(registry)
        for key, ids in HOST_GESTURES.items():
            saved = registry.get_binding(key)
            default = self.defaults.get(key, {})
            changed = any(saved.get(k) != default.get(k) for k in ("action_type", "action_value", "enabled", "app_overrides"))
            if not changed and key not in explicit:
                continue
            for gid in ids:
                native = bindings.get(gid)
                if native is None:
                    continue
                self.original[gid] = native
                owned[gid] = Binding("custom.execute", {"gesture_key": key, "native": {
                    "action": native.action, "params": dict(native.params)}, "fallback_default": key not in explicit})
        extras = set()
        for key in ("thumbs_up", "rock_sign", "thumb_index_middle", "swipe_down"):
            saved, default = registry.get_binding(key), self.defaults.get(key, {})
            if key in explicit or any(saved.get(k) != default.get(k) for k in ("action_type", "action_value", "enabled", "app_overrides")):
                extras.add(key)
        want_draw = any(k.startswith("draw_") for k in explicit) or any(
            k != "draw_x_mark" and b.get("action_type") == "mouse_action" and b.get("action_value") == "toggle_draw"
            for k, b in registry.bindings.items())
        feature = self.factory(registry, extras, draw_enabled=want_draw)
        for key in feature.keys:
            gid = self.gesture_id(key)
            self.original.setdefault(gid, bindings.get(gid))
            owned[gid] = Binding("custom.execute", {"gesture_key": key})
            if key == "thumb_index_middle":
                owned[gid] = Binding("custom.execute", {"gesture_key": key, "native": {"action": "mouse.drag", "params": {}}})
        bindings.update(owned)
        if owned or self.installed:
            engine.pause_for_settings()
            engine.router = CustomRouter(bindings, lambda: engine.state.control_on)
        engine.features = [f.wrapped if isinstance(f, CustomFeatureMask) else f for f in engine.features if f.id != "custom_gestures"]
        if "thumbs_up" in extras:
            engine.features = [CustomFeatureMask(f, {"motion.enter", "motion.exit"}) if f.id == "motion" else f for f in engine.features]
        engine.filters = [f for f in engine.filters if f.id != "custom_gestures"]
        if feature.gestures:
            engine.features.append(feature)
            engine.filters.append(self.filter_factory(feature))
        self.installed, self.feature = owned, feature

    def ensure_attached(self, registry):
        if self.installed and (any(self.engine.router.bindings.get(k) != v for k, v in self.installed.items())
                               or self.feature.gestures and self.feature not in self.engine.features):
            self.install(registry)


class CustomFeatureMask:
    """Only a sign explicitly remapped by the user stops doing its original mode command."""
    def __init__(self, wrapped, masked):
        self.wrapped, self.masked = wrapped, frozenset(masked)

    def __getattr__(self, name):
        return getattr(self.wrapped, name)

    def step(self, hand, ctx):
        return tuple(i for i in self.wrapped.step(hand, ctx) if i.gesture not in self.masked)


class CustomRouter(Router):
    """Source V-sign behavior: while paused the sign can still start control."""
    def __init__(self, bindings, control_on):
        super().__init__(bindings)
        self.control_on = control_on

    def route(self, intent):
        binding = self._bindings.get(intent.gesture)
        if (not self.control_on() and binding and binding.action == "custom.execute"
                and binding.params.get("gesture_key") == "v_sign"):
            native = binding.params.get("native")
            if native:
                return Router({intent.gesture: Binding(native["action"], native["params"])}).route(intent)
        return super().route(intent)
