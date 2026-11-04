"""One gesture owns input at a time. No OS/UI calls; deterministic frame replay."""
from collections import deque
from statistics import median
import math
from .observation import clamp, sub, dot, unit
from .pointer import Pointer
from .filters import OneEuroFilter

LABELS = {'waiting':'손을 보여주세요', 'ready':'검지 기준 잡는 중', 'move':'검지로 이동',
          'clutch':'손가락 움직임 확인', 'tap':'누름 · 떼면 클릭', 'palm':'편 손 · 시작 방향 선택',
          'scroll':'위아래로 스크롤', 'drag':'잡고 이동 · 손을 접으면 놓기',
          'zoom':'간격으로 확대 · 손을 펴면 확정', 'zoom_done':'배율 확정 · 손을 편하게 접으세요',
          'pinch':'확대 준비', 'settle':'현재 위치에서 다시 연결', 'swipe':'페이지 넘김'}


class Controller:
    def __init__(self, settings=None):
        self.settings = settings or {}
        self.pointer = Pointer(self.settings.get('area_scale', .77), self.settings.get('stability', .6))
        self.scope = 'all'
        self.last_click = -10.
        self.click_point = None
        self.reset()

    def reset(self, position=None):
        self.state = 'waiting'
        self.since = 0.
        self.previous = None
        self.previous_at = None
        self.neutral = None
        self.neutral_history = deque(maxlen=8)
        self.stable_since = None
        self.lock = tuple(position or self.pointer.position)
        self.pointer.position = self.lock
        self.reference = None
        self.score = 0.
        self.tap_peak = 0.
        self.was_pressed = False
        self.scroll_residue = 0.
        self.scroll_filter = OneEuroFilter(min_cutoff=3.,beta=1.,d_cutoff=3.)
        self.scroll_follow = 0.
        self.zoom_sent = 0.
        self.zoom_preview = 1.
        self.last_zoom_pinch = None
        self.zoom_confirmed = False
        self.last_gesture = -10.

    def allows(self, name): return self.scope in ('all', name)

    def result(self, actions=(), reason='', position=None, moving=False):
        return dict(state=self.state, label=LABELS.get(self.state, self.state),
                    position=tuple(position or self.pointer.position), actions=list(actions), reason=reason,
                    tap_score=round(self.score, 3), zoom_preview=round(self.zoom_preview, 3),
                    zoom_confirmed=self.zoom_confirmed, pressed=self.state == 'tap',
                    dragging=self.state == 'drag', scope=self.scope, moving=moving)

    def enter(self, state, now, shown=None):
        self.state, self.since = state, now
        if shown is not None:
            self.lock = tuple(shown)
            self.pointer.position = self.lock
        self.stable_since = None

    def finish(self, f, now, shown, actions=()):
        self.last_gesture = now
        if any(a['type']=='click' for a in actions):
            # Release already verified the original neutral. Do not make the
            # second tap wait for a second onboarding, especially at 15 FPS.
            self.enter('move',now,shown)
            self.pointer.anchor(f.tip,shown,now)
            return self.result(actions)
        self.enter('settle', now, shown)
        self.neutral = None
        self.neutral_history.clear()
        self.pointer.anchor(f.tip, shown, now)
        return self.result(actions)

    def neutral_pose(self, f):
        # A straight but knuckle-folded index must not become a new neutral mid-press.
        profile = self.settings.get('profile') or {}
        ref_y = (profile.get('rest') or [0., .65])[1]
        return f.visible[0] and f.fingers[0] > .91 and f.signature[1] > min(.52, max(.28, ref_y-.06))

    def tap_measure(self, f):
        if self.neutral is None: return 0.
        delta = sub(f.signature, self.neutral)
        profile = self.settings.get('profile') or {}
        direction = unit(sub(profile['press'], profile['rest'])) if profile.get('press') and profile.get('rest') else (0., -.8, -.5, .35)
        learned = dot(delta, direction)
        fold = -delta[1]*.8 + max(0., delta[3])*.7
        return max(learned, fold)

    def step(self, f, now, shown=None, valid=True, track_reset=False):
        shown = tuple(shown or self.pointer.position)
        if f is None or not valid:
            actions = [{'type':'up'}] if self.state == 'drag' else []
            self.reset(shown)
            return self.result(actions, '추적이 끊겨 입력을 놓았습니다')
        old, old_at = self.previous, self.previous_at
        self.previous, self.previous_at = f, now
        if track_reset or (old_at is not None and now-old_at > .20):
            actions = [{'type':'up'}] if self.state == 'drag' else []
            self.reset(shown)
            self.previous, self.previous_at = f, now
            return self.result(actions, '손을 다시 확인합니다')
        dt = min(.1, max(.008, now-old_at)) if old_at is not None else 1/30
        local_change = math.dist(f.local_tip(), old.local_tip()) if old else 0.
        palm_step = math.hypot(f.palm[0]-old.palm[0], (f.palm[1]-old.palm[1])*f.aspect) if old else 0.
        # This gate runs BEFORE pointer.update, including frames not classified as a tap.
        articulation = local_change > .24
        impossible = local_change > 1.25 or palm_step > max(.11, f.size*1.1)
        self.score = self.tap_measure(f)
        threshold = .14 + .10*clamp(self.settings.get('tap_enter', .65), .25, 1.)

        if self.state == 'waiting':
            self.enter('ready', now, shown)
        if self.state in ('ready', 'settle', 'clutch'):
            if self.neutral_pose(f) and not f.open and not f.unique_pinch and local_change < .18:
                if self.stable_since is None: self.stable_since = now
                self.neutral_history.append(f.signature)
                delay = .07 if self.state == 'settle' else .14
                if now-self.stable_since >= delay and len(self.neutral_history) >= 3:
                    self.neutral = tuple(median(row[a] for row in self.neutral_history) for a in range(4))
                    self.pointer.anchor(f.tip, shown, now)
                    self.enter('move', now)
                    return self.result(reason='현재 커서 위치에서 이어갑니다')
            else:
                self.stable_since = None
                self.neutral_history.clear()
                if f.open and (self.allows('scroll') or self.allows('drag')):
                    self.enter('palm', now, shown)
                    self.reference = f
                elif f.unique_pinch and self.allows('zoom'):
                    self.enter('pinch', now, shown)
                    self.reference = f
            return self.result(position=shown)

        if self.state == 'zoom_done':
            if not f.open_held and not f.unique_pinch:
                if self.stable_since is None: self.stable_since = now
                if now-self.stable_since > .16:
                    return self.finish(f, now, shown)
            else: self.stable_since = None
            return self.result(position=self.lock)

        if self.state == 'pinch':
            if f.open:
                self.zoom_confirmed = True
                self.enter('zoom_done', now, self.lock)
            elif f.pinch > .48 or not f.thumb_visible:
                return self.finish(f, now, shown)
            elif now-self.since >= .15:
                self.enter('zoom', now, self.lock)
                self.reference = f
                self.zoom_sent = 0.
                self.zoom_preview = 1.
                self.zoom_confirmed = False
                self.last_zoom_pinch = f.pinch
            return self.result(position=self.lock)

        if self.state == 'zoom':
            if f.open:
                self.zoom_confirmed = True
                self.enter('zoom_done', now, self.lock)
                return self.result([{'type':'zoomEnd'}], position=self.lock)
            if not f.thumb_visible or not f.visible[0] or (f.fingers[0] < .7 and f.folded):
                self.zoom_confirmed = True
                return self.finish(f, now, self.lock, [{'type':'zoomEnd'}])
            # Reject landmark swaps/occlusion; never send their transient gap as a zoom.
            if abs(f.pinch-self.last_zoom_pinch) > .5:
                self.last_zoom_pinch = f.pinch
                return self.result(reason='엄지·검지 간격 확인', position=self.lock)
            self.last_zoom_pinch = f.pinch
            desired = clamp((f.pinch-self.reference.pinch)*1.3, -1.25, 1.8)
            self.zoom_preview = math.exp(desired)
            change = desired-self.zoom_sent
            actions = []
            if abs(change) > .055:
                delta = clamp(change, -.15, .15)
                self.zoom_sent += delta
                actions.append({'type':'zoom', 'factor':math.exp(delta)})
            return self.result(actions, position=self.lock)

        if self.state in ('palm', 'scroll', 'drag'):
            # Pause immediately at loss of the open pose; confirm folding before release.
            if not f.open_held:
                if self.stable_since is None: self.stable_since = now
                if now-self.stable_since >= .07:
                    actions = [{'type':'up'}] if self.state == 'drag' else []
                    return self.finish(f, now, shown, actions)
                return self.result(position=shown)
            self.stable_since = None
            if impossible:
                return self.result(reason='손 위치 확인', position=shown)
            origin = self.reference
            dx = (f.palm[0]-origin.palm[0])/origin.size
            dy = (f.palm[1]-origin.palm[1])*f.aspect/origin.size
            if self.state == 'palm':
                if now-self.since < .12: return self.result(position=self.lock)
                if abs(dy) > .24 and abs(dy) > abs(dx)*1.35 and self.allows('scroll'):
                    self.enter('scroll', now, self.lock)
                    self.reference = f
                    self.scroll_residue = 0.
                    self.scroll_filter.reset()
                    self.scroll_filter(0.,now)
                    self.scroll_follow = 0.
                elif abs(dx) > .24 and abs(dx) > abs(dy)*1.35 and self.allows('drag'):
                    self.enter('drag', now, self.lock)
                    self.pointer.anchor(f.palm, self.lock, now)
                    return self.result([{'type':'down'}], position=self.lock)
                return self.result(position=self.lock)
            if self.state == 'drag':
                return self.result(position=self.pointer.update(f.palm, now), moving=True)
            # Displacement, not velocity: holding the hand still sends no wheel events.
            delta_y = (f.palm[1]-origin.palm[1])*f.aspect/origin.size
            # Stable palm landmarks + a spatial deadband remove stationary jitter.
            # No temporal catch-up here: stopping the hand stops the wheel now.
            smooth = delta_y
            displacement = smooth-self.scroll_follow
            if abs(displacement) <= .04:return self.result(position=self.lock)
            followed = smooth-math.copysign(.04,displacement)
            pixels = -(followed-self.scroll_follow)*240*self.settings.get('scroll_speed', 1.12)
            self.scroll_follow = followed
            if self.scroll_residue*pixels < 0: self.scroll_residue = 0.
            self.scroll_residue += pixels
            actions = []
            if abs(self.scroll_residue) >= 9:
                amount = clamp(self.scroll_residue, -90, 90)
                self.scroll_residue -= amount
                actions.append({'type':'scroll', 'pixels':amount})
            return self.result(actions, position=self.lock)

        if self.state == 'tap':
            if impossible or f.open or now-self.since > 1.1:
                return self.finish(f, now, self.lock)
            self.tap_peak = max(self.tap_peak, self.score)
            if self.score < threshold*.38 and self.neutral_pose(f):
                if now-self.since < .045: return self.finish(f, now, self.lock)
                point = self.lock
                double = now-self.last_click < .5 and self.click_point is not None and math.dist(point, self.click_point) < .025
                if double: point = self.click_point
                self.last_click, self.click_point = now, point
                return self.finish(f, now, point, [{'type':'click', 'button':'left', 'double':double}])
            return self.result(position=self.lock)

        # Movement: resolve pose ownership and finger motion BEFORE sending any pointer target.
        if impossible:
            self.enter('clutch', now, shown)
            self.neutral = None
            return self.result(reason='검지 위치 급변 · 커서를 고정했습니다', position=shown)
        if f.open and (self.allows('scroll') or self.allows('drag')):
            self.enter('palm', now, shown)
            self.reference = f
            return self.result(position=shown)
        if f.unique_pinch and self.allows('zoom'):
            self.enter('pinch', now, shown)
            self.reference = f
            return self.result(position=shown)
        if self.allows('click') and self.score >= threshold and self.neutral is not None:
            self.enter('tap', now, shown)
            self.tap_peak = self.score
            return self.result(position=shown)
        if articulation or self.score >= threshold*.42:
            # A partial press is held here; it may become a full press next frame.
            if self.stable_since is None:
                self.stable_since = now
                self.lock = shown
            if now-self.stable_since > .7:
                self.enter('clutch', now, shown)
                self.neutral = None
            return self.result(reason='손가락 움직임 · 위치 고정', position=self.lock)
        if self.stable_since is not None:
            self.pointer.anchor(f.tip, shown, now)
            self.stable_since = None
        # Neutral adapts to orientation only very slowly, and never toward a press.
        if self.neutral is not None and abs(self.score) < .045 and local_change < .08:
            self.neutral = tuple(a*.99+b*.01 for a, b in zip(self.neutral, f.signature))
        return self.result(position=self.pointer.update(f.tip, now), moving=True)
