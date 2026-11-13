"""Live AI training UI uses the original teacher, never Windows output or provider requests."""
import copy

import pytest

from manux.compat.hand_mouse import action_bindings as ab, custom_postures, llm_binding as lb
from manux.compat.hand_mouse import pose_training_catalog as catalog, posture_teach as pt


@pytest.fixture
def bridge():
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from manux.ui.ai_customization import AiCustomizationBridge
    application = QApplication.instance() or QApplication([])
    owner = QObject()
    ui = AiCustomizationBridge(owner, dry_run=True)
    ui.timer.stop()
    ui.setPoseStudioVisible(True)
    yield ui
    ui.close()


def composite(posture, name='합성 동작', key='comp_sample'):
    return ab.get_action_registry().register_composite_gesture(
        key, name, posture, 'hold', 'hotkey', 'ctrl+t')


def connect(gesture, app=''):
    reg = ab.get_action_registry()
    proposals = lb.check({'proposals': [dict(gesture_key=gesture, app_match=app, action_type='hotkey',
                                           action_value='ctrl+t', enabled=True, reason='')]}, lb.gesture_list(reg))['proposals']
    proposals[0]['checked'] = True
    assert lb.apply(proposals, reg, history=lb.History())


def test_live_add_rename_delete_without_restart(bridge):
    assert bridge.poseStudioActions == []
    shape = custom_postures.add('합성 모양')
    bridge.llmChanged.emit()
    assert bridge.poseStudioActions[0]['name'] == '합성 모양'
    row = composite(shape)
    bridge.llmChanged.emit()
    assert [r['id'] for r in bridge.poseStudioActions] == ['gesture:' + row['key']]
    reg = ab.get_action_registry()
    reg.composite_gestures[row['key']]['name'] = '바꾼 이름'
    reg.save()
    bridge._pose_studio_checked = 0
    bridge._poll()  # a disk edit, with no UI refresh signal
    assert bridge.poseStudioActions[0]['name'] == '바꾼 이름'
    custom_postures.delete(shape)
    bridge.llmChanged.emit()
    assert bridge.poseStudioActions == [] and not bridge.poseStudioState['trainable']


def test_shared_shape_and_app_overrides_do_not_duplicate_training_classes(bridge):
    connect('thumb_ring')
    connect('thumb_ring', 'synthetic-app')
    composite('thumb_ring')
    from tests.unit.ai_upstream.hands import open_hand
    joints = [[p.x, p.y, p.z] for p in open_hand(.5, .5)]
    pt.save_example('thumb_ring', [joints] * 12, ['thumb_ring'] * 12)
    bridge.llmChanged.emit()
    rows = bridge.poseStudioActions
    assert len(rows) == 2
    assert all(r['count'] == 1 and r['poses'][0]['key'] == 'thumb_ring' for r in rows)
    assert pt.example_counts() == {'thumb_ring': 1}


def test_sequence_offers_each_hand_shape_and_unsupported_shapes_remain_visible(bridge):
    from manux.compat.hand_mouse.sequence import SequenceStore
    sequence = SequenceStore().add('합성 순서', ['fist', 'v_sign'])
    connect(sequence)
    composite('open_hand', '손바닥 방향')
    bridge.llmChanged.emit()
    bridge.selectPoseStudioAction('gesture:' + sequence)
    assert [p['key'] for p in bridge.poseStudioSelection['poses']] == ['fist', 'v_sign']
    bridge.selectPoseStudioPosture('v_sign')
    assert bridge.poseStudioState['posture'] == 'v_sign'
    bridge.selectPoseStudioAction('gesture:comp_sample')
    assert not bridge.poseStudioState['trainable'] and bridge.poseStudioState['reason']
    bridge.capturePoseStudioExample()
    assert not bridge.online_commands


def test_capture_routes_to_shape_and_cancels_on_close_without_changing_binding(bridge):
    shape = custom_postures.add('추가 학습')
    composite(shape)
    reg = ab.get_action_registry()
    before = copy.deepcopy(reg.composite_gestures)
    bridge.llmChanged.emit()
    from tests.unit.ai_upstream.hands import open_hand
    joints = [[p.x, p.y, p.z] for p in open_hand(.5, .5)]
    joints = [[.5 + (x - .5) * 2, .5 + (y - .5) * 2, z * 2] for x, y, z in joints]
    bridge.observePoseStudioFrame(joints, .75)
    bridge.capturePoseStudioExample()
    assert bridge.pose_studio.label == shape
    assert len(bridge.pose_studio.samples) == 1  # immediate capture, no countdown/probe command
    assert not bridge.online_commands
    assert bridge.poseStudioState['capturing']
    bridge.setPoseStudioVisible(False)
    assert len(bridge.pose_studio.samples) == 1  # closing preserves pending samples
    assert not bridge.poseStudioState['busy']
    assert reg.composite_gestures == before


def test_capture_waits_for_hand_and_deleted_target_cancels_pending_capture(bridge):
    shape = custom_postures.add('재시도')
    bridge.llmChanged.emit()
    bridge.capturePoseStudioExample()
    bridge.observePoseStudioFrame(None, .75)
    assert bridge.poseStudioState['capturing'] and not bridge.pose_studio.samples
    bridge.observePoseStudioFrame(sample_hand(), .75)
    assert len(bridge.pose_studio.samples) == 1
    custom_postures.delete(shape)
    bridge.llmChanged.emit()
    assert not bridge.poseStudioState['capturing']
    assert not bridge.online_commands


def test_saved_example_updates_counts_and_only_explicit_button_trains(bridge, monkeypatch):
    shape = custom_postures.add('학습 연결')
    bridge.llmChanged.emit()
    from tests.unit.ai_upstream.hands import open_hand
    joints = [[p.x, p.y, p.z] for p in open_hand(.5, .5)]
    for _ in range(2):
        pt.save_example(shape, [joints] * 12, ['fist'] * 12)
    called = []
    monkeypatch.setattr(bridge, 'poseTeachTrain', lambda: called.append(bridge.teach_focus))
    bridge._refresh_pose_studio()
    assert not called and bridge.poseStudioState['frames'] == 24
    assert not bridge.poseStudioState['busy']
    bridge.trainPoseStudioModel()
    assert called == [shape]


def test_profile_switch_refreshes_current_gestures(bridge):
    ab.use_binding_profile('first-synthetic')
    composite('fist', '첫 프로필')
    bridge.llmChanged.emit()
    assert bridge.poseStudioActions
    ab.use_binding_profile('other-synthetic')
    bridge.llmChanged.emit()
    assert not any(r['name'] == '첫 프로필' for r in bridge.poseStudioActions)


def test_command_pose_tab_and_keyboard_do_not_capture_default_samples(tmp_path):
    from PySide6.QtCore import QObject, QMetaObject
    from manux.app import schema
    from manux.settings.store import Settings
    from manux.ui.bridge import Bridge
    from tests.unit.ui.test_bridge import FakeRuntime
    shape = custom_postures.add('키보드 합성 표본')
    settings = Settings(schema())
    runtime = FakeRuntime(settings)
    runtime.request = lambda request: True
    ui = Bridge(runtime, settings, tmp_path / 'config', layout='studio')
    try:
        ui.timer.stop()
        ui._set_stage('main')
        center = ui.window.findChild(QObject, 'studioDataManagement')
        QMetaObject.invokeMethod(center, 'open')
        ui.application.processEvents()
        category = ui.window.findChild(QObject, 'aiPoseCategory')
        assert category is not None
        QMetaObject.invokeMethod(category, 'clicked')
        ui.application.processEvents()
        assert ui.ai_customization.poseStudioVisible
        from manux.ui.view import ViewFrame
        ui.apply_view(ViewFrame(None, landmarks=tuple(tuple(p) for p in sample_hand()), aspect=.75))
        ui.command(32)
        assert ui.ai_customization.pose_studio.label == shape and ui.ai_customization.poseStudioState['capturing']
        assert len(ui.ai_customization.pose_studio.samples) == 1
        assert not ui.pose_studio.is_streaming
        ui.command(27)
        assert not ui.ai_customization.poseStudioState['capturing']
        QMetaObject.invokeMethod(center, 'close')
        ui.application.processEvents()
        assert not ui.ai_customization.poseStudioVisible
    finally:
        ui.close()


def test_no_negative_validation_keeps_existing_model_and_explains_reason(bridge, monkeypatch):
    shape = custom_postures.add('검증 대기')
    bridge.llmChanged.emit()
    bridge.teach_focus = shape
    applied = []
    monkeypatch.setattr(pt, 'apply_candidate', lambda: applied.append(True))
    bridge._finish_pose_teach({'can_apply': False, 'false_rate': None, 'examples': {shape: 2}})
    assert not applied
    assert '비교 표본이 없어' in bridge.poseStudioState['text']


def sample_hand(positive=True):
    from tests.unit.ai_upstream.test_sequence import v_hand
    from tests.unit.ai_upstream.hands import open_hand
    points = v_hand(down=True, scale=.5) if positive else open_hand(.5, .5)
    joints = [[p.x, p.y, p.z] for p in points]
    return joints if positive else [[.5 + (x - .5) * 2, .5 + (y - .5) * 2, z * 2] for x, y, z in joints]


def collect(studio, label, joints, now, split='train', frames=25):
    studio.select(label)
    studio.observe(joints, .75, now=now)
    studio.trigger(split=split, now=now)
    assert studio.samples[-1]['label'] == label
    for i in range(1, frames):
        studio.observe(joints, .75, now=now + i * .06)
    studio.stop()


def test_stream_immediate_throttled_bounded_and_one_trial_per_start():
    from manux.compat.hand_mouse.pose_training_studio import PoseTrainingStudio
    studio = PoseTrainingStudio()
    studio.select('fist')
    joints = sample_hand()
    studio.observe(joints, .75, now=1.)
    studio.trigger(now=1.)
    assert len(studio.samples) == 1
    studio.observe(joints, .75, now=1.01)
    assert len(studio.samples) == 1
    for i in range(1, 1002):
        studio.observe(joints, .75, now=1. + i * .06)
    assert len(studio.samples) == 1000 and not studio.streaming
    assert len({s['trial'] for s in studio.samples}) == 1
    studio.undo()
    assert len(studio.samples) == 999
    studio.clear()
    assert not studio.samples


def test_manual_mlp_trains_applies_and_recognizes_without_external_recordings(bridge):
    import time
    from PySide6.QtWidgets import QApplication
    shape = custom_postures.add('실제 MLP 합성 검사')
    composite(shape)
    binding_before = ab.get_action_registry().get_binding('comp_sample').copy()
    bridge.llmChanged.emit()
    positive, negative = sample_hand(), sample_hand(False)
    for i in range(2):
        collect(bridge.pose_studio, shape, positive, 10. + i * 10.)
        collect(bridge.pose_studio, pt.OTHER, negative, 15. + i * 10.)
    # Held-out evaluation data must never train or influence learned thumb/finger stats.
    collect(bridge.pose_studio, 'eval_only_shape', negative, 40., split='eval', frames=7)
    assert not pt.REC_DIR.exists() and not pt.MODEL_PATH.exists()
    assert bridge.savePoseStudioSamples()
    assert not pt.MODEL_PATH.exists()  # collecting/saving never starts training implicitly
    assert bridge.poseStudioState['frames'] == 50 and bridge.poseStudioState['negativeFrames'] == 50
    bridge.trainPoseStudioModel()
    assert bridge.poseStudioState['training']
    deadline = time.monotonic() + 30
    while bridge.poseStudioState['training'] and time.monotonic() < deadline:
        QApplication.instance().processEvents()
        time.sleep(.01)  # release the GIL for the actual Python trainer, unlike QTest.qWait
    assert bridge.teach_view['state'] == 'applied', bridge.teach_view
    assert pt.MODEL_PATH.is_file() and not pt.CANDIDATE_PATH.exists()
    report = pt.read_report(pt.MODEL_PATH)
    assert report['classes'] == [pt.OTHER, shape]
    assert report['examples'] == {shape: 2} and report['negative_examples'] == 2
    assert report['evaluation_frames_excluded'] == 7
    assert report['recognition'][shape] >= .95 and report['false_rate'] <= pt.MAX_FALSE
    pt.reset_cache()
    assert pt.active_model().predict(positive, .75)[0] == shape
    assert pt.active_model().predict(negative, .75)[0] == pt.OTHER
    assert pt.override(positive, '', .75) == shape
    assert bridge.poseStudioState['active']
    assert ab.get_action_registry().get_binding('comp_sample') == binding_before


def test_save_failure_preserves_pending_samples(bridge, monkeypatch):
    shape = custom_postures.add('저장 복구')
    bridge.llmChanged.emit()
    collect(bridge.pose_studio, shape, sample_hand(), 1., frames=3)
    def failed(*args, **kwargs):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(pt, 'save_example', failed)
    assert not bridge.savePoseStudioSamples()
    assert len(bridge.pose_studio.samples) == 3 and '저장 실패' in bridge.poseStudioState['text']


def test_snapshot_and_saved_evaluation_never_create_training_classes(bridge):
    shape = custom_postures.add('평가 분리')
    bridge.llmChanged.emit()
    bridge.setPoseStudioCaptureMode('snapshot')
    studio = bridge.pose_studio
    studio.observe(sample_hand(), .75, now=1.)
    studio.trigger(split='eval', now=1.)
    studio.trigger(split='eval', now=1.1)
    assert len(studio.samples) == 2 and not studio.streaming
    assert bridge.savePoseStudioSamples()
    assert bridge.poseStudioState['evalFrames'] == 2
    assert not bridge.poseStudioState['canTrain'] and shape not in pt.example_counts()
