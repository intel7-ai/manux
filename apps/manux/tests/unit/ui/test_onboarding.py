from manux.ui import cards
from manux.ui.tutorial import ATTEMPT_SECONDS, MAX_FAILURES, TARGETS, Tutorial, onboarding_steps


def test_onboarding_orders_daily_tasks_and_keeps_motion_optional():
    steps = onboarding_steps(cards.cards(False))
    assert [c['key'] for c in steps] == ['move', 'click', 'sensitivity', 'scroll', 'control', 'desktop', 'motion']
    assert steps[-2]['optional'] and steps[-1]['optional']
    # palm cursor: the hand is already chosen, so 1 move, 2 choose up/down, 3 optional cursor fit, then the rest
    palm = onboarding_steps(cards.cards(False), palm=True, pointer_setup=True)
    assert [c['key'] for c in palm][:5] == ['move', 'click', 'vertical', 'pointer_setup', 'sensitivity']
    assert [c['title'] for c in palm][:5] == ['1. 커서 이동', '2. 클릭 연습', '3. 상하 이동 방식 고르기',
                                             '4. 커서 움직임 맞춤 설정 (선택)', '5. 내 손에 맞는 속도']
    assert palm[3]['optional']
    assert cards.find(cards.cards(False), 'desktop') is not None


def test_vertical_step_needs_a_choice_then_rests_on_three_positions():
    steps = onboarding_steps(cards.cards(False), palm=True, pointer_setup=True)
    t = Tutorial([s for s in steps if s['key'] == 'vertical'])
    for i in range(10):                                   # resting on a position before choosing does nothing
        t.observe(['mouse.move'], False, i * .1, point=TARGETS[0])
    assert t.progress == 0 and '고르세요' in t.help_text
    t.vertical_chosen = True
    now = 2.
    for target in TARGETS:
        for _ in range(12):                               # 0.36 s on each position
            now += .03
            hit = t.observe(['mouse.move'], False, now, point=target)
    assert hit and t.done == [True]


def test_targets_require_gesture_click_at_all_three_practice_cursor_positions():
    t = Tutorial([next(s for s in onboarding_steps(cards.cards(False)) if s['key'] == 'sensitivity')])
    assert not t.observe(['mouse.move'], False, 1., point=TARGETS[0])
    assert t.progress == 0
    assert not t.observe(['mouse.left_click'], False, 2., point=(0., 0.))
    assert t.progress == 0
    for i, target in enumerate(TARGETS):
        t.observe([], False, 3. + 2 * i, point=target)
        hit = t.observe(['mouse.left_click'], False, 4. + 2 * i, point=target, precision=i == 0)
        assert hit is (i == 2)
    assert t.progress == 1 and t.precision_seen and t.done == [True]


def test_control_practice_requires_two_distinct_v_events():
    control = cards.find(onboarding_steps(cards.cards(False)), "control")
    t = Tutorial([control])
    assert not t.observe(['control.toggle'], False, 1.)
    assert t.progress == .5
    assert not t.observe(['control.toggle'], False, 2.)
    t.observe([], False, 3.)
    assert t.observe(['control.toggle'], False, 4.)


def test_three_incomplete_attempts_offer_skip_and_explain_the_reason():
    t = Tutorial([cards.find(cards.cards(False), 'click')])
    t.observe([], False, 0.)
    for i in range(MAX_FAILURES):
        assert t.observe([], False, (i + 1) * ATTEMPT_SECONDS)
    assert t.failures == 3 and t.can_skip and '3번' in t.help_text
    assert t.done == [False]
    t.next()
    assert t.finished and t.skipped == [True] and t.done == [False]


def test_step_change_forgets_previous_action_edge_and_clock():
    click = cards.find(cards.cards(False), 'click')
    t = Tutorial([click, click])
    assert t.observe(['mouse.left_click'], False, 1.)
    t.next()
    assert t.observe(['mouse.left_click'], False, 2.)
    t.back()
    assert t._last_t is None and t._last == frozenset()


def test_retry_clears_partial_target_attempt_without_marking_success():
    t = Tutorial([next(s for s in onboarding_steps(cards.cards(False)) if s['key'] == 'sensitivity')])
    t.observe(['mouse.left_click'], False, 1., point=TARGETS[0])
    assert t.progress == 1 / 3
    t.retry()
    assert t.progress == 0 and t.failures == 0 and not t.done[0]


def test_pointer_without_click_does_not_require_unavailable_target_clicks():
    steps = onboarding_steps(cards.cards(True, 'off'))
    assert [c['key'] for c in steps] == ['move', 'control']


def test_palm_targets_also_require_one_engine_confirmed_precision_move():
    t = Tutorial([next(s for s in onboarding_steps(cards.cards(False)) if s['key'] == 'sensitivity')])
    for i, target in enumerate(TARGETS):
        t.observe([], False, 1.+i*2, point=target)
        assert not t.observe(['mouse.left_click'], False, 2.+i*2, point=target)
    assert not t.done[0] and t.progress == .9 and '정밀' in t.help_text
    assert t.observe(['mouse.move'], False, 8., point=TARGETS[-1], precision=True)
    assert t.done[0]


def test_desktop_and_motion_practice_require_their_accepted_actions():
    desktop, motion = onboarding_steps(cards.cards(False))[-2:]
    t = Tutorial([desktop, motion])
    assert t.observe(["window.show_desktop"], False, 1.)
    t.next()
    assert not t.observe([], True, 2.)
    assert not t.observe(["nav.back"], False, 3.)
    t.observe([], True, 4.)
    assert t.observe(["nav.back"], True, 5.)
