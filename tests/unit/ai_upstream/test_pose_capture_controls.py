"""Exercise actual Qt key delivery and collection recovery without camera or OS input."""
import pytest

from manux.compat.hand_mouse import custom_postures
from manux.compat.hand_mouse.pose_training_studio import PoseTrainingStudio
from tests.unit.ai_upstream.test_pose_training_studio import sample_hand


@pytest.mark.parametrize('ai_selected', [False, True])
@pytest.mark.parametrize('focus_target', ['popup', 'category', 'capture', 'checkbox', 'alias'])
def test_space_in_learning_popup_starts_and_stops_capture(tmp_path, ai_selected, focus_target):
    from PySide6.QtCore import QObject, QMetaObject, Qt
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    from manux.app import schema
    from manux.settings.store import Settings
    from manux.ui.bridge import Bridge
    from tests.unit.ui.test_bridge import FakeRuntime

    custom_postures.add('키보드 합성 동작')
    settings = Settings(schema())
    runtime = FakeRuntime(settings)
    runtime.request = lambda request: True
    ui = Bridge(runtime, settings, tmp_path / 'config', layout='studio')
    try:
        ui.timer.stop()
        ui._set_stage('main')
        center = ui.window.findChild(QObject, 'studioDataManagement')
        QMetaObject.invokeMethod(center, 'open')
        ui.window.requestActivate()
        QTest.qWait(250)
        if ai_selected:
            QMetaObject.invokeMethod(ui.window.findChild(QObject, 'aiPoseCategory'), 'clicked')
        else:
            ui.setStudioCaptureMode('stream')
        QTest.qWait(50)
        if focus_target == 'category':
            ui.window.findChild(QQuickItem, 'aiPoseCategory').forceActiveFocus()
        elif focus_target == 'capture':
            ui.window.findChild(QQuickItem, 'captureAiPoseSample' if ai_selected else 'capturePoseSample').forceActiveFocus()
        elif focus_target == 'checkbox':
            candidates = [i for i in ui.window.findChildren(QQuickItem) if i.isVisible() and i.property('text') == '불일치 표본만' and i.metaObject().indexOfSignal('clicked()') >= 0]
            assert len(candidates) == 1
            candidates[0].forceActiveFocus()
        elif focus_target == 'alias':
            ui.window.findChild(QQuickItem, 'trainingAlias').forceActiveFocus()
        alias_text = ui.window.findChild(QQuickItem, 'trainingAlias').property('text')
        QTest.keyClick(ui.window, Qt.Key_Space)
        QTest.qWait(20)
        assert (ui.ai_customization.pose_studio.streaming if ai_selected else ui.pose_studio.is_streaming)
        QTest.keyClick(ui.window, Qt.Key_Space)
        QTest.qWait(20)
        assert not ui.ai_customization.pose_studio.streaming and not ui.pose_studio.is_streaming
        assert ui.window.findChild(QQuickItem, 'trainingAlias').property('text') == alias_text
        # The shortcut must not collect after switching to model management.
        center.setProperty('pageIndex', 1)
        ui.window.findChild(QQuickItem, 'trainingAlias').forceActiveFocus()
        QTest.keyClick(ui.window, Qt.Key_Space)
        QTest.qWait(20)
        assert not ui.ai_customization.pose_studio.streaming and not ui.pose_studio.is_streaming
        assert ui.window.findChild(QQuickItem, 'trainingAlias').property('text') == alias_text + ' '
    finally:
        ui.close()


@pytest.mark.parametrize('missing', [None, 'clipped', 'partial'])
def test_ai_stream_waits_and_resumes_without_changing_trial(missing):
    studio = PoseTrainingStudio()
    studio.select('fist')
    hand = sample_hand()
    studio.observe(hand, .75, now=1.)
    studio.trigger(now=1.)
    trial = studio.trial
    invalid = None if missing is None else [p[:] for p in hand]
    if missing == 'clipped':
        invalid[8][0] = 1.2
    elif missing == 'partial':
        invalid = invalid[:10]
    for now in (1.1, 2., 5.):
        studio.observe(invalid, .75, now=now)
        assert studio.streaming and len(studio.samples) == 1
        assert studio.trial == trial
    studio.observe(hand, .75, now=5.1)
    assert studio.streaming and len(studio.samples) == 2
    assert {s['trial'] for s in studio.samples} == {trial}
    studio.stop()
    studio.observe(hand, .75, now=6.)
    assert not studio.streaming and len(studio.samples) == 2


def test_ai_manual_stop_while_waiting_prevents_automatic_restart():
    studio = PoseTrainingStudio()
    studio.select('fist')
    studio.trigger(now=1.)
    studio.observe(None, .75, now=2.)
    assert studio.streaming and not studio.samples
    studio.trigger(now=2.1)
    studio.observe(sample_hand(), .75, now=3.)
    assert not studio.streaming and not studio.samples


def test_snapshot_mode_does_not_auto_collect_when_hand_returns():
    studio = PoseTrainingStudio()
    studio.select('fist')
    studio.mode = 'snapshot'
    studio.trigger(now=1.)
    studio.observe(sample_hand(), .75, now=2.)
    assert not studio.samples and not studio.streaming
    studio.trigger(now=2.)
    assert len(studio.samples) == 1
    studio.observe(None, .75, now=3.)
    studio.observe(sample_hand(), .75, now=4.)
    assert len(studio.samples) == 1 and not studio.streaming


def test_default_pose_collection_already_waits_and_resumes(tmp_path):
    from manux.desktop import make_pose_studio
    studio = make_pose_studio(tmp_path)
    hand = sample_hand()
    studio.start_stream()
    assert studio.observe_frame(hand, None, 'none', now_ms=100.)['success']
    assert not studio.observe_frame(None, None, 'none', now_ms=200.)['success']
    assert studio.is_streaming and len(studio.samples) == 1
    assert studio.observe_frame(hand, None, 'none', now_ms=1000.)['success']
    assert len(studio.samples) == 2
