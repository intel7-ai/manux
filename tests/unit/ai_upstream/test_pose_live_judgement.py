import numpy as np

from manux.compat.hand_mouse import posture_teach as pt, custom_postures, action_bindings as ab
from manux.compat.hand_mouse.pose_training_studio import PoseTrainingStudio
from tests.unit.ai_upstream.test_pose_training_studio import bridge, sample_hand, composite, connect


def model(label, replaces, confidence=True):
    return pt.TaughtModel(dict(classes=[pt.OTHER, label], mean=[0.] * 98, std=[1.] * 98,
                              w1=np.zeros((4,98)).tolist(), b1=[0.] * 4, w2=np.zeros((2,4)).tolist(),
                              b2=[-10., 10.] if confidence else [0., .1], replaces={label: replaces}))


def test_raw_model_prediction_and_actual_rule_fallback_are_both_visible(bridge, monkeypatch):
    key = custom_postures.add('선택 정답')
    bridge.llmChanged.emit()
    classifier = model(key, [])
    monkeypatch.setattr(pt, 'active_model', lambda: classifier)
    bridge.observePoseStudioFrame(sample_hand(), .75)
    state = bridge.poseStudioState
    assert '선택 정답' in state['modelText'] and '100%' in state['modelText']
    assert state['compareBadge'] == '최종 판정 불일치'
    assert state['ruleText'] == state['finalText'] and '대체 범위' in state['decisionNote']
    classifier.replaces[key].add(bridge.pose_studio.rule)
    bridge.observePoseStudioFrame(sample_hand(), .75)
    assert bridge.poseStudioState['finalText'] == '선택 정답'
    assert bridge.poseStudioState['match'] is True
    bridge.observePoseStudioFrame(None, .75)
    assert bridge.poseStudioState['compareBadge'] == '최종 판정 대기'
    assert bridge.poseStudioState['modelText'] == '손 감지 안 됨'


def test_low_confidence_is_not_reported_as_an_applied_ai_shape(bridge, monkeypatch):
    key = custom_postures.add('낮은 확신')
    bridge.llmChanged.emit()
    classifier = model(key, [], confidence=False)
    monkeypatch.setattr(pt, 'active_model', lambda: classifier)
    bridge.observePoseStudioFrame(sample_hand(), .75)
    assert '기준 85%' in bridge.poseStudioState['decisionNote']
    assert bridge.poseStudioState['match'] is False


def test_discrepancy_only_captures_actual_mismatch_not_correct_frames(bridge, monkeypatch):
    key = custom_postures.add('불일치 수집')
    bridge.llmChanged.emit()
    classifier = model(key, [])
    monkeypatch.setattr(pt, 'active_model', lambda: classifier)
    bridge.setPoseStudioDiscrepancyOnly(True)
    studio = bridge.pose_studio
    studio.observe(sample_hand(), .75, now=1.)
    studio.trigger(now=1.)
    assert len(studio.samples) == 1  # MLP raw label matches, but real rule fallback still disagrees
    classifier.replaces[key].add(studio.rule)
    studio.observe(sample_hand(), .75, now=1.2)
    assert studio.is_match() is True and len(studio.samples) == 1
    bridge.setPoseStudioDiscrepancyOnly(False)
    studio.observe(sample_hand(), .75, now=1.4)
    assert len(studio.samples) == 2 and studio.streaming


def test_neutral_comparison_does_not_count_unbound_legacy_shapes_as_commands(monkeypatch):
    studio = PoseTrainingStudio()
    studio.select(pt.OTHER)
    monkeypatch.setattr(pt, 'active_model', lambda: None)
    studio.observe(sample_hand(), .75, now=1.)
    assert studio.is_match() is True
    studio.known_shapes.add(studio.rule)
    assert studio.is_match() is False


def test_disabled_default_rock_is_not_warned_as_an_active_close_command():
    from manux.compat.hand_mouse.gesture_overlap import find
    reg = ab.get_action_registry()
    assert find({'rock_sign': 1.}, 'custom_99', reg) == []
    connect('rock_sign')  # explicit AI connection must still be visible, with its actual command
    rows = find({'rock_sign': 1.}, 'custom_99', reg)
    assert rows and rows[0]['posture'] == 'rock_sign'
    assert '창 닫기' not in rows[0]['does']
    reg.set_binding('rock_sign', 'hotkey', 'ctrl+t', enabled=False)
    assert find({'rock_sign': 1.}, 'custom_99', reg) == []


def test_explicit_rock_composite_still_has_its_original_detector():
    from manux.compat.hand_mouse.action_bindings import CompositeGestureDetector
    composite('rock_sign', key='comp_rock_kept')
    detector = CompositeGestureDetector(ab.get_action_registry())
    from types import SimpleNamespace
    joints = [SimpleNamespace(x=x,y=y,z=z) for x,y,z in sample_hand()]
    fired = [detector.step(i * .1, joints, '', set(), posture='rock_sign') for i in range(12)]
    assert any(row and row['key'] == 'comp_rock_kept' for row in fired)


def test_disabled_explicit_rock_cannot_mask_a_native_click(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from manux.core.contracts import Stage
    from manux.engine.commands import SetStage, SetControl
    from manux.app import build_engine, schema
    from manux.settings.store import Settings
    from manux.compat.hand_mouse.close_sign import CloseSign
    from tests.unit.ai_upstream.test_ai_integration import pipeline, sample
    from manux.features.custom_gestures import gesture_id
    monkeypatch.setattr(CloseSign, 'update', lambda *args, **kwargs: SimpleNamespace(kind='close'))
    reg = ab.ActionBindingRegistry(tmp_path / 'bindings.json')
    reg.set_binding('rock_sign', 'hotkey', 'ctrl+t', enabled=False)
    engine = build_engine(Settings(schema()), 'pinch')
    attached = pipeline(engine)
    attached.install(reg)
    engine.commands.put(SetStage(Stage.MAIN))
    engine.commands.put(SetControl(True))
    frame, hand = sample(1., 1)
    snap = engine.step(frame, hand)
    assert not snap.errors
    assert not any(g == gesture_id('rock_sign') for g, _ in snap.intents)
