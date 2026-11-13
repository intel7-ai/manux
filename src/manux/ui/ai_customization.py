"""Original AI Qt view-model extracted from a700452; host wiring is below the source methods."""
from __future__ import annotations
from collections import deque
import time
from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, QSettings
from PySide6.QtGui import QImage, QGuiApplication
from PySide6.QtQuick import QQuickImageProvider

class ClipImage(QQuickImageProvider):
    """The pictures of a [손 모양·움직임 보여주기] look, replayed on the screen like a GIF: image://probeshot/<look>/<n>
    is the n-th picture (a bare <look> or a number past the end: the one still picture of the result)."""

    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self.image = QImage()
        self.frames = []

    def requestImage(self, image_id, size, requested_size):
        try:
            n = int(str(image_id).split("/")[1])
        except (IndexError, ValueError):
            return self.image
        return self.frames[n] if 0 <= n < len(self.frames) else self.image

class AiCustomizationBridge(QObject):
    drawChanged = Signal()
    llmChanged = Signal()
    stateChanged = Signal()
    drawShapesChanged = Signal()
    poseTeachDone = Signal(object, str)
    poseTeachProgress = Signal(str)
    overlapAsk = Signal(str, str, str)
    poseStudioChanged = Signal()
    poseStudioStateChanged = Signal()

    def __init__(self, owner, service=None, dry_run=False):
        super().__init__(owner)
        from manux.compat.hand_mouse.paths import DATA_ROOT
        self.owner, self.service, self.dry_run = owner, service, dry_run
        self.preferences = QSettings(str(DATA_ROOT / "ai-ui.ini"), QSettings.IniFormat)
        self.llm_provider = self.preferences.value("llmProvider", "claude", type=str)
        self._llm_session_keys = {}
        self.llm_busy, self.llm_message, self.llm_result, self.llm_models = False, "", {}, []
        self.teach_view, self._teach_counts = {"state": "", "text": ""}, (None, {})
        self.teach_focus, self.teach_adding, self.teach_new = "", False, False
        self._probe_wait, self.pose_probe_view = False, None
        self.probe_shot, self.probe_shot_seq, self.probe_shot_ok = ClipImage(), 0, False
        self.captured_key, self.key_capturing = "", False
        self.online_commands = service.commands if service else deque()
        self.training_note = ""
        self.draw_view = {"on": False}
        self._command_state = dict(running=False, message="", output="", exit_code=None)
        self.closed = False
        self._requested_profile = None
        self._active_profile = None
        self._pose_studio_visible = self._pose_studio_capture = False
        self._pose_studio_rows, self._pose_studio_stamp = [], None
        self._pose_studio_id = self._pose_studio_posture = ""
        self._pose_studio_checked = 0.0
        from manux.compat.hand_mouse.pose_training_studio import PoseTrainingStudio
        self.pose_studio = PoseTrainingStudio()
        self._pose_studio_saved = {}
        self._pose_studio_status = ''
        self._pose_studio_active = {}
        self._pose_studio_frame_at = 0.
        self.poseStudioChanged.connect(self.poseStudioStateChanged.emit)
        self.llmChanged.connect(self._refresh_pose_studio)
        self.poseTeachDone.connect(self._finish_pose_teach)
        self.poseTeachProgress.connect(self._pose_teach_progress)
        self.timer = QTimer(self)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self._poll)
        self.timer.start()

    def _hand_mode(self):
        return "index" if getattr(self.owner, "use_index_pointer", False) else "palm"

    @Slot()
    def refreshContext(self):
        self.llmChanged.emit()

    def refreshTraining(self):
        self.online_commands.append(("seq_reload",))

    def _poll(self):
        if self._pose_studio_visible and time.monotonic() - self._pose_studio_checked >= .5:
            self._refresh_pose_studio()
        from manux.compat.hand_mouse import ports
        command_state = ports.command_status()
        if command_state != self._command_state:
            self._command_state = command_state
            self.llmChanged.emit()
        if self.service is not None and not self.closed:
            profile = getattr(self.owner, "profile", None) or None
            if profile != self._requested_profile:
                self.online_commands.append(("binding_profile", profile))
                self._requested_profile = profile
            if self.service.profile != self._active_profile:
                self._active_profile = self.service.profile
                self.llmChanged.emit()
            draw = self.service.draw_snapshot()
            if self.draw_view != draw:
                self.draw_view = draw
                self.drawChanged.emit()
            probe, shot = self.service.snapshot()
            self._set_probe_shot(shot)
            self._set_pose_probe(probe)

    def close(self):
        self.pose_studio.stop()
        self.closed = True
        self.timer.stop()

    def _refresh_pose_studio(self):
        from manux.compat.hand_mouse.action_bindings import get_action_registry
        from manux.compat.hand_mouse import pose_training_catalog as catalog
        self._pose_studio_checked = time.monotonic()
        registry = get_action_registry()
        stamp = catalog.fingerprint(registry)
        if stamp != self._pose_studio_stamp:
            from manux.compat.hand_mouse.pose_training_studio import saved_counts
            from manux.compat.hand_mouse import posture_teach as pt
            self._pose_studio_active = pt.read_report(pt.MODEL_PATH) or {}
            self._pose_studio_saved = saved_counts()
            rows = catalog.rows(registry)
            self.pose_studio.known_shapes = {p['key'] for r in rows for p in r['poses'] if p['key'] != 'any'}
            self._pose_studio_stamp, self._pose_studio_rows = stamp, rows
            selected = next((r for r in rows if r['id'] == self._pose_studio_id), None)
            if selected is None:
                self.cancelPoseStudioCapture()
                self._pose_studio_id = rows[0]['id'] if rows else ''
                selected = rows[0] if rows else {}
            poses = selected.get('poses', [])
            if self._pose_studio_posture not in [p['key'] for p in poses] and self._pose_studio_posture != 'other':
                self.cancelPoseStudioCapture()
                self._pose_studio_posture = next((p['key'] for p in poses if p['trainable']),
                                                 poses[0]['key'] if poses else '')
            self.pose_studio.select(self._pose_studio_posture)
            self.poseStudioChanged.emit()
        self.poseStudioStateChanged.emit()

    @Property('QVariantList', notify=poseStudioChanged)
    def poseStudioActions(self):
        return [{**r, 'selected': r['id'] == self._pose_studio_id,
                 'frames': sum(self._pose_studio_saved.get(p['key'], {}).get('frames', 0) for p in r['poses'])}
                for r in self._pose_studio_rows]

    @Property(bool, notify=poseStudioChanged)
    def poseStudioVisible(self):
        return self._pose_studio_visible

    @Property('QVariantMap', notify=poseStudioChanged)
    def poseStudioSelection(self):
        return next((r for r in self._pose_studio_rows if r['id'] == self._pose_studio_id), {})

    @Property('QVariantMap', notify=poseStudioStateChanged)
    def poseStudioState(self):
        from manux.compat.hand_mouse import posture_teach as pt
        from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
        pose = next((p for p in self.poseStudioSelection.get('poses', [])
                     if p['key'] == self._pose_studio_posture), {})
        capture = self.pose_studio.summary()
        neutral = self._pose_studio_posture == pt.OTHER
        saved = self._pose_studio_saved.get(self._pose_studio_posture, {})
        training = self.teach_view.get('state') == 'training'
        report = self.teach_view.get('report') or {}
        active = self._pose_studio_active
        text = self._pose_studio_status or capture['note']
        if training:
            text = 'MLP 학습 중 · ' + self.teach_view.get('text', '')
        elif self.teach_view.get('state') == 'error':
            text = self.teach_view.get('text', '')
        elif self.teach_view.get('state') == 'applied':
            text = 'MLP 학습 완료 · 검증을 통과한 모델을 AI 동작 인식에 적용했습니다.'
        elif self.teach_view.get('state') == 'ready':
            if report.get('false_rate') is None:
                text = 'MLP 학습 완료 · 비교 표본이 없어 검증하지 못했습니다. ‘동작 아님·휴식’도 두 차례 수집·저장한 뒤 다시 학습하세요.'
            elif not report.get('can_apply'):
                text = 'MLP 학습 완료 · 오인식 검증을 통과하지 못했습니다. 동작과 휴식 표본을 더 모아 다시 학습하세요. 기존 모델은 유지됩니다.'
        result = report if self.teach_view.get('state') == 'ready' else active
        rate = (result.get('recognition') or {}).get(self._pose_studio_posture)
        metrics = (f'별도 시도 인식률 {rate * 100:.1f}%' if rate is not None else '인식률 평가: 같은 동작을 두 차례 이상 나누어 수집하세요.')
        if result.get('false_rate') is not None:
            metrics += f" · 휴식 오인식 {result['false_rate'] * 100:.1f}%"
        positive_saved = sum(v.get('frames', 0) for k, v in self._pose_studio_saved.items() if k != pt.OTHER)
        positive_pending = any(s['label'] != pt.OTHER and s['split'] == 'train' for s in self.pose_studio.samples)
        name = lambda key: '동작 아님·휴식' if key == pt.OTHER else POSTURE_NAMES_KO.get(key, key) or '분류 없음'
        reasons = {'no_hand': '카메라에 손을 보여주세요.', 'no_model': 'AI 동작 MLP가 없어 규칙 판정으로 비교합니다.',
                   'other': 'MLP가 학습한 동작으로 보지 않아 규칙 판정을 유지합니다.',
                   'confidence': f"MLP 확신도가 적용 기준 {capture['threshold'] * 100:.0f}%보다 낮아 규칙 판정을 유지합니다.",
                   'rule_scope': 'MLP 예측과 규칙이 다르지만, 이 규칙 판정은 학습한 대체 범위에 없어 유지합니다.',
                   'shape_check': 'MLP 예측이 손가락 형태 검증을 통과하지 못해 규칙 판정을 유지합니다.',
                   'same': '규칙과 AI 동작 MLP가 같은 손 모양으로 판정했습니다.',
                   'accepted': 'AI 동작 MLP의 손 모양을 최종 판정에 사용합니다.'}
        compare = '일치' if capture['match'] is True else '불일치' if capture['match'] is False else '대기'
        model_text = (f"{name(capture['prediction'])} · {capture['confidence'] * 100:.0f}%"
                      if capture['hasModel'] else '학습 모델 없음') if capture['tracked'] else '손 감지 안 됨'
        return dict(posture=self._pose_studio_posture, name='동작 아님·휴식' if neutral else pose.get('name', ''),
                    trainable=neutral or bool(pose.get('trainable')), count=pose.get('count', 0),
                    reason='' if neutral else pose.get('reason') or self.poseStudioSelection.get('reason', ''),
                    busy=training or self.teach_adding, training=training, neutral=neutral,
                    capturing=capture['streaming'], text=text, steps='', metrics=metrics,
                    mode=capture['mode'], pending=capture['pendingTotal'], frames=saved.get('frames', 0),
                    attempts=saved.get('attempts', 0), evalFrames=saved.get('evalFrames', 0),
                    negativeFrames=self._pose_studio_saved.get(pt.OTHER, {}).get('frames', 0),
                    canTrain=bool(positive_saved or positive_pending),
                    prediction=name(capture['prediction']),
                    confidence=capture['confidence'], active=bool(active),
                    activeShapes=len(active.get('examples', {})),
                    tracked=capture['tracked'], match=capture['match'], compareBadge='최종 판정 ' + compare,
                    modelText=model_text, ruleText=name(capture['rule']) if capture['tracked'] else '손 감지 안 됨',
                    finalText=name(capture['effective']) if capture['tracked'] else '손 감지 안 됨',
                    decisionNote=reasons.get(capture['decisionReason'], ''),
                    discrepancyOnly=capture['discrepancyOnly'],
                    modelName='AI 동작 MLP' + (' · ' + str(active['trained']) if active.get('trained') else ''))

    @Slot(bool)
    def setPoseStudioVisible(self, visible):
        self._pose_studio_visible = bool(visible)
        if not visible:
            self.cancelPoseStudioCapture()
        self._refresh_pose_studio()

    @Slot(str)
    def selectPoseStudioAction(self, key):
        if self.poseStudioState['busy']:
            return
        if any(r['id'] == key for r in self._pose_studio_rows):
            self._pose_studio_posture = ''
            self._pose_studio_id, self._pose_studio_stamp = key, None
            self._refresh_pose_studio()

    @Slot(str)
    def selectPoseStudioPosture(self, key):
        if not self.poseStudioState['busy'] and (key == 'other' or any(p['key'] == key for p in self.poseStudioSelection.get('poses', []))):
            self._pose_studio_posture = key
            self.pose_studio.select(key)
            self._pose_studio_status = ''
            self.poseStudioChanged.emit()

    @Slot()
    def capturePoseStudioExample(self):
        self._refresh_pose_studio()
        state = self.poseStudioState
        if self._pose_studio_visible and state['trainable'] and not state['busy']:
            self.pose_studio.select(state['posture'])
            self.pose_studio.trigger(getattr(self.owner, 'recordingSplit', 'train'),
                                     getattr(self.owner, 'trainingProfile', ''))
            self._pose_studio_status = ''
            self.teach_view = {'state': '', 'text': ''}
            self.poseStudioChanged.emit()

    @Slot()
    def cancelPoseStudioCapture(self):
        self.pose_studio.stop()
        if self._pose_studio_capture:
            self._pose_studio_capture = False
            self.clearPoseProbe()
        self.poseStudioChanged.emit()

    def observePoseStudioFrame(self, landmarks, aspect):
        if self._pose_studio_visible:
            self.pose_studio.observe(landmarks, aspect)
            now = time.monotonic()
            if now - self._pose_studio_frame_at >= .1:
                self._pose_studio_frame_at = now
                self.poseStudioStateChanged.emit()

    @Slot(str)
    def setPoseStudioCaptureMode(self, mode):
        if mode in ('stream', 'snapshot') and not self.poseStudioState['busy']:
            self.pose_studio.stop()
            self.pose_studio.mode = mode
            self.poseStudioChanged.emit()

    @Slot(bool)
    def setPoseStudioDiscrepancyOnly(self, enabled):
        self.pose_studio.discrepancy_only = bool(enabled)
        self.poseStudioStateChanged.emit()

    @Slot()
    def undoPoseStudioSample(self):
        if not self.poseStudioState['busy']:
            self.pose_studio.undo()
            self.poseStudioChanged.emit()

    @Slot()
    def clearPoseStudioSamples(self):
        if not self.poseStudioState['busy']:
            self.pose_studio.clear()
            self.poseStudioChanged.emit()

    @Slot(result=bool)
    def savePoseStudioSamples(self):
        if self.poseStudioState['busy']:
            return False
        try:
            self.pose_studio.save()
        except (OSError, ValueError) as exc:
            self._pose_studio_status = f'저장 실패 · 수집 표본은 유지됩니다. {exc}'
            self.poseStudioChanged.emit()
            return False
        self._pose_studio_status = ''
        self._teach_counts = (None, {})
        self._pose_studio_stamp = None
        self._refresh_pose_studio()
        return True

    @Slot()
    def trainPoseStudioModel(self):
        state = self.poseStudioState
        if not state['busy'] and self.savePoseStudioSamples():
            self.teach_focus = state['posture'] if state['posture'] != 'other' else next(
                (k for k in self._pose_studio_saved if k != 'other'), '')
            self.poseTeachTrain()

    @Property("QVariantMap", notify=drawChanged)
    def drawView(self):
        return self.draw_view

    @Property("QVariantList", notify=drawChanged)
    def drawArea(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return [0, 0, 1920, 1080]
        g = screen.geometry()
        return [g.x(), g.y(), g.width(), g.height()]

    def _llm_config(self):
        from manux.compat.hand_mouse.llm_providers import LlmConfig, PROVIDERS
        from manux.compat.hand_mouse import secret_store
        pid = self.llm_provider if self.llm_provider in PROVIDERS else "claude"
        saved = str(self.preferences.value(f"llmKey/{pid}", "") or "")
        key = secret_store.unprotect(saved) if saved else self._llm_session_keys.get(pid, "")
        if not key:
            key = secret_store.read_env_key(str(self.preferences.value(f"llmEnvFile/{pid}", "") or ""),
                                           PROVIDERS[pid].key_env)
        return LlmConfig(provider=pid, model=str(self.preferences.value(f"llmModel/{pid}", "") or ""),
                         base_url=str(self.preferences.value(f"llmBase/{pid}", "") or ""), api_key=key)


    def _llm_set(self, busy=None, message=None):
        if self.closed:
            return
        if busy is not None:
            self.llm_busy = busy
        if message is not None:
            self.llm_message = message
        self.llmChanged.emit()


    @Property("QVariantList", constant=True)
    def llmProviders(self):
        from manux.compat.hand_mouse.llm_providers import PROVIDERS
        return [{"id": p.id, "label": p.label, "note": p.note, "needsKey": bool(p.key_env), "keyEnv": p.key_env,
                 "defaultModel": p.default_model, "defaultBase": p.base_url} for p in PROVIDERS.values()]


    @Property("QVariantMap", notify=llmChanged)
    def llmSettings(self):
        import os
        cfg = self._llm_config()
        spec = cfg.spec
        saved = bool(self.preferences.value(f"llmKey/{cfg.provider}", "")) or cfg.provider in self._llm_session_keys
        linked = bool(self.preferences.value(f"llmEnvFile/{cfg.provider}", "")) and bool(cfg.api_key)
        env = bool(spec.key_env and os.environ.get(spec.key_env))
        source = ("필요 없음" if not spec.key_env else "저장된 키 사용" if saved
                  else "연결한 키 파일 사용" if linked
                  else f"환경 변수 {spec.key_env} 사용" if env else "키 없음")
        from manux.compat.hand_mouse.llm_binding import describe_request
        return {"provider": cfg.provider, "model": cfg.model, "modelShown": cfg.model_name(), "baseUrl": cfg.base_url,
                "defaultBase": spec.base_url, "needsKey": bool(spec.key_env), "hasKey": bool(cfg.key()) or not spec.key_env,
                "keySource": source, "keySaved": saved or linked, "privacy": describe_request(cfg, "")}


    @Property(bool, notify=llmChanged)
    def llmBusy(self):
        return self.llm_busy


    @Property(str, notify=llmChanged)
    def llmMessage(self):
        return self.llm_message

    @Property("QVariantMap", notify=llmChanged)
    def commandExecution(self):
        return self._command_state

    @Property("QVariantList", constant=True)
    def systemActionPresets(self):
        from manux.compat.hand_mouse.pc_commands import SYSTEM_ACTIONS
        return [dict(value=key, label=row["label"]) for key, row in SYSTEM_ACTIONS.items()]

    @Slot()
    def cancelCommandExecution(self):
        from manux.compat.hand_mouse import ports
        ports.macro_cancel()
        ports.command_cancel()


    @Property("QVariantMap", notify=llmChanged)
    def llmResult(self):
        return self.llm_result


    @Property("QVariantList", notify=llmChanged)
    def llmModels(self):
        return self.llm_models


    @Slot(str)
    def setLlmProvider(self, pid):
        from manux.compat.hand_mouse.llm_providers import PROVIDERS
        if pid in PROVIDERS and pid != self.llm_provider:
            self.llm_provider = pid
            self.preferences.setValue("llmProvider", pid)
            self.llm_models = []
            self._llm_set(message="")


    @Slot(str)
    def setLlmModel(self, model):
        self.preferences.setValue(f"llmModel/{self.llm_provider}", str(model or "").strip())
        self.llmChanged.emit()


    @Slot(str)
    def setLlmBaseUrl(self, url):
        url = str(url or "").strip()
        if url and not url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            self._llm_set(message="주소는 https:// 또는 이 PC(http://localhost)만 쓸 수 있어요.")
            return
        self.preferences.setValue(f"llmBase/{self.llm_provider}", url)
        self.llmChanged.emit()


    @Slot(str)
    def saveLlmKey(self, key):
        from manux.compat.hand_mouse import secret_store
        key = str(key or "").strip()
        if not key:
            return
        token = secret_store.protect(key)
        if token:
            self.preferences.setValue(f"llmKey/{self.llm_provider}", token)
            self._llm_set(message="키를 이 Windows 사용자 전용으로 암호화해 저장했어요.")
        else:
            self._llm_session_keys[self.llm_provider] = key
            self._llm_set(message="이 시스템에서는 키를 저장할 수 없어 이번 실행 동안만 써요.")


    @Slot()
    def clearLlmKey(self):
        self.preferences.remove(f"llmKey/{self.llm_provider}")
        self.preferences.remove(f"llmEnvFile/{self.llm_provider}")
        self._llm_session_keys.pop(self.llm_provider, None)
        self._llm_set(message="저장한 키를 지웠어요.")


    def _llm_job(self, work, busy_text):
        if self.llm_busy:
            return
        self._llm_set(busy=True, message=busy_text)

        def run():
            from manux.compat.hand_mouse.llm_providers import LlmError
            try:
                message = work()
            except LlmError as exc:
                message = str(exc)
            except Exception as exc:                      # never let a worker error reach the screen as a crash
                message = f"실패했어요: {type(exc).__name__}"
            self._llm_set(busy=False, message=message)

        import threading
        threading.Thread(target=run, daemon=True).start()


    @Slot()
    def loadLlmModels(self):
        cfg = self._llm_config()

        def work():
            from manux.compat.hand_mouse.llm_providers import list_models
            self.llm_models = list_models(cfg)
            return f"모델 {len(self.llm_models)}개를 불러왔어요." if self.llm_models else "쓸 수 있는 모델이 없어요."
        self._llm_job(work, "모델 목록을 불러오는 중…")


    @Slot(str)
    def askLlm(self, text):
        from manux.compat.hand_mouse.action_bindings import get_action_registry
        from manux.compat.hand_mouse.llm_binding import ask, gesture_list
        cfg = self._llm_config()
        gestures = gesture_list(get_action_registry())
        shown = dict(self.pose_probe_view) if (self.pose_probe_view or {}).get("state") == "done" else None
        key = self.captured_key
        hand_mode = self._hand_mode()            # the two hand modes are separate worlds: the AI is told which is in use

        def work():
            result = ask(cfg, text, gestures, shown=shown, key=key, hand_mode=hand_mode)
            try:        # a new gesture from a hand another gesture answers to: said, and left for the person to tick
                from manux.compat.hand_mouse.gesture_overlap import mark_proposals
                mark_proposals(result.get("proposals") or [], (shown or {}).get("seen") if not (shown or {}).get("kind") else None,
                               get_action_registry())
            except Exception:
                pass
            self.llm_result = {**result, "request": str(text).strip()}
            n = sum(1 for r in result["proposals"] if r["ok"])
            if not result["proposals"]:
                return "바꿀 연결을 찾지 못했어요." + (" 아래 질문을 확인해 주세요." if result["questions"] else "")
            return f"제안 {len(result['proposals'])}개 ({n}개 적용 가능). 확인하고 [선택한 연결 적용]을 눌러 주세요."
        self._llm_job(work, f"{cfg.spec.label}에 묻는 중…")


    @Property("QVariantMap", notify=llmChanged)
    def llmContext(self):
        """What 이/저 point to: the shown hand shape and the remembered key."""
        done = (self.pose_probe_view or {}).get("state") == "done"
        shot = f"image://probeshot/{self.probe_shot_seq}" if done and getattr(self, "probe_shot_ok", False) else ""
        return {"pose": self.pose_probe_view or {}, "key": self.captured_key, "capturing": self.key_capturing,
                "overlap": self._overlap_words((self.pose_probe_view or {}).get("posture", "")),
                "shot": shot, "shotFrames": len(self.probe_shot.frames) if shot else 0, "teach": self._teach_context()}


    @Property("QVariantList", notify=llmChanged)
    def postureChoices(self):
        """The listed shapes and the ones the user named (src/custom_postures.py)."""
        from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
        return [{"key": k, "name": v} for k, v in POSTURE_NAMES_KO.items() if k != "any"]


    @Property("QVariantList", notify=llmChanged)
    def customPostures(self):
        """The hand shapes the user named, with what hangs on each (for the list with [삭제])."""
        from manux.compat.hand_mouse import custom_postures
        import manux.compat.hand_mouse.posture_teach as pt
        counts = pt.example_counts()
        rows = []
        for key, name in custom_postures.load().items():
            made = custom_postures.uses(key)
            rows.append({"key": key, "name": name, "examples": counts.get(key, 0), "uses": len(made),
                         "usesText": ", ".join(n for _, _, n in made)})
        return rows


    @Slot(str)
    def removeCustomPosture(self, key):
        from manux.compat.hand_mouse import custom_postures
        name = custom_postures.load().get(key, "")
        gone = custom_postures.delete(key)
        if not gone:
            return
        if self.teach_focus == key:
            self.teach_focus, self.teach_adding = "", False
        if (self.pose_probe_view or {}).get("posture") == key:       # the result on the screen is of that shape
            self.clearPoseProbe()
        self._teach_counts = (None, {})
        self.online_commands.append(("seq_reload",))
        self.teach_view = {"state": "", "text": f"손 모양 '{name}'을 지웠어요 (견본 {gone['examples']}개, 연결한 동작 {gone['gestures']}개 포함)."}
        self.stateChanged.emit()
        self.llmChanged.emit()


    @Slot(str)
    def poseTeachSaveNew(self, name):
        """A look that is no listed shape (or a listed one the user wants as its own): a new shape with this name,
        the look as its first example."""
        if (self.pose_probe_view or {}).get("state") != "done":
            return
        if self._overlap_asked("new", str(name), ""):
            return
        self._pose_teach_new(name)


    def _pose_teach_new(self, name):
        from manux.compat.hand_mouse import custom_postures
        try:
            key = custom_postures.add(name)
        except ValueError as exc:
            self.teach_view = {"state": "error", "text": str(exc)}
            self.llmChanged.emit()
            return
        self._pose_teach_save(key)


    def _overlaps(self, label):
        probe = self.pose_probe_view or {}
        if probe.get("state") != "done" or probe.get("kind"):
            return []                               # a movement: the shapes on the way are not what is registered
        try:
            from manux.compat.hand_mouse.action_bindings import get_action_registry
            from manux.compat.hand_mouse.gesture_overlap import find
            return find(probe.get("seen"), str(label or ""), get_action_registry())
        except Exception:
            return []


    def _overlap_words(self, label):
        from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
        from manux.compat.hand_mouse.gesture_overlap import words
        return words(self._overlaps(label), POSTURE_NAMES_KO.get(label, "") if label else "")


    def _overlap_asked(self, kind, what, label) -> bool:
        """True when the look overlaps and the person is being asked (nothing saved yet): [그래도 저장] comes back
        through overlapConfirmed. Asked once per look and shape."""
        text = self._overlap_words(label)
        if not text:
            return False
        self._overlap_adding = bool(self.teach_adding)
        self.teach_adding = False
        self.teach_view = {"state": "", "text": "겹치는 동작이 있어 저장하지 않고 물어보는 중이에요."}
        self.llmChanged.emit()
        self.overlapAsk.emit(kind, what, text + " 그래도 저장할까요?")
        return True


    @Slot(str, str)
    def overlapConfirmed(self, kind, what):
        """The person wants the overlapping shape anyway."""
        if (self.pose_probe_view or {}).get("state") != "done":
            return
        if kind == "new":
            self._pose_teach_new(what)
        elif kind == "save":
            self.teach_adding = bool(getattr(self, "_overlap_adding", False))
            self._pose_teach_save(what)


    def _teach_context(self):
        from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
        import manux.compat.hand_mouse.posture_teach as pt
        try:
            stamp = pt.EXAMPLES_DIR.stat().st_mtime_ns
        except OSError:
            stamp = 0
        if self._teach_counts[0] != stamp:
            self._teach_counts = (stamp, pt.example_counts())
        counts = self._teach_counts[1]
        active = pt.read_report(pt.MODEL_PATH) if pt.MODEL_PATH.is_file() else None
        name = lambda k: POSTURE_NAMES_KO.get(k, k)
        focus = self.teach_focus if self.teach_focus in POSTURE_NAMES_KO else ""
        state = self.teach_view.get("state", "")
        words = pt.guide(focus, name(focus), counts, state, self.teach_view.get("report"), active,
                         self.teach_view.get("text", ""))
        return {
            "state": state,
            "steps": words["steps"],
            "text": words["text"],
            "canApply": bool(self.teach_view.get("canApply")),
            "focus": focus,
            "focusName": name(focus) if focus else "",
            "adding": bool(focus and self.teach_adding),
            "naming": bool(self.teach_new),
            # with no shape being taught: one short line, no numbers of shapes nobody is working on
            "summary": "" if focus or not (counts or active) else
                       f"가르친 손 모양 {len(counts)}개" + (f" · 사용 중 {len(active.get('examples') or {})}개" if active else ""),
            "hasExamples": bool(counts),
            "counts": dict(counts),                 # examples saved per shape (the cards' [수정] says how many)
            "active": "on" if active else "",
        }


    def _after_example_saved(self, label):
        """The runtime saved a look as an example (the result now says "taught"): with enough examples of that shape
        the training starts by itself, so showing the shape again IS the next step."""
        import manux.compat.hand_mouse.posture_teach as pt
        self._teach_counts = (None, {})
        self._pose_studio_capture = False
        n = pt.example_counts().get(label, 0)
        if label == self.teach_focus and n >= pt.AUTO_TRAIN and self.teach_view.get("state") != "training":
            self.poseTeachTrain()
        else:
            self.llmChanged.emit()


    @Slot(str)
    def poseTeachSave(self, label):
        if (self.pose_probe_view or {}).get("state") != "done" or not label:
            return
        if self._overlap_asked("save", str(label), str(label)):
            return
        self._pose_teach_save(label)


    def _pose_teach_save(self, label):
        self.online_commands.append(("pose_teach_save", str(label)))
        self.teach_focus, self.teach_new = str(label), False
        self.teach_view = {"state": "", "text": ""}
        self.llmChanged.emit()


    @Slot()
    def poseTeachTrain(self):
        if self.teach_view.get("state") == "training":
            return
        import threading
        import manux.compat.hand_mouse.posture_teach as pt
        self.teach_view = {"state": "training", "text": "학습 준비 중…"}
        self.llmChanged.emit()

        def work():
            try:
                self.poseTeachDone.emit(pt.train(progress=self.poseTeachProgress.emit), "")
            except Exception as exc:
                self.poseTeachDone.emit(None, str(exc))

        threading.Thread(target=work, daemon=True).start()


    def _pose_teach_progress(self, text):
        if self.teach_view.get("state") == "training":
            self.teach_view = {"state": "training", "text": text}
            self.llmChanged.emit()


    def _finish_pose_teach(self, report, error=""):
        from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
        import manux.compat.hand_mouse.posture_teach as pt
        if error or not isinstance(report, dict):
            self.teach_view = {"state": "error", "text": f"학습하지 못했어요: {error or '결과 없음'}"}
            self.llmChanged.emit()
            return
        ok = bool(report.get("can_apply"))
        text = ""
        if not self.teach_focus:              # [학습] pressed with no shape being taught: every shape, as before
            parts = []
            for k, r in (report.get("recognition") or {}).items():
                n = (report.get("examples") or {}).get(k, 0)
                parts.append(f"{POSTURE_NAMES_KO.get(k, k)} 인식 {round(r * 100)}%" if r is not None
                             else f"{POSTURE_NAMES_KO.get(k, k)}: 견본 {n}개라 인식률은 못 쟀어요 (2개 이상 필요)")
            fr = report.get("false_rate")
            parts.append(f"평소 손에서 잘못 걸림 {fr * 100:.1f}%" if fr is not None else "잘못 걸림은 못 쟀어요")
            text = " · ".join(parts) + (" · [적용]하면 바로 쓰여요" if ok else
                                        f" · {pt.MAX_FALSE * 100:.0f}%를 넘어서 적용할 수 없어요. 견본을 더 모아 주세요")
        self.teach_view = {"state": "ready", "canApply": ok, "report": report, "text": text}
        if ok and self.teach_focus:
            # the shape being taught passed the check: it is put to use at once. 2026-11-11 report ("등록했는데 작동
            # 안함"): the training was done and a command connected, but [적용] had not been pressed, so the model in
            # use did not know the shape and the command never ran
            self.poseTeachApply()
            return
        self.llmChanged.emit()


    @Slot()
    def poseTeachApply(self):
        import manux.compat.hand_mouse.posture_teach as pt
        if self.teach_view.get("canApply") and pt.apply_candidate():
            self.teach_view = {"state": "applied", "text": "적용했어요. 이제 보여주기와 연결한 동작에서 가르친 손 모양을 알아봐요."}
        self.llmChanged.emit()


    @Slot()
    def poseTeachDiscard(self):
        import manux.compat.hand_mouse.posture_teach as pt
        pt.discard_candidate()
        self.teach_view = {"state": "", "text": "학습 결과를 버렸어요 (견본은 남아 있어요)."}
        self.llmChanged.emit()


    @Slot()
    def poseTeachOff(self):
        import manux.compat.hand_mouse.posture_teach as pt
        pt.turn_off()
        self.teach_view = {"state": "", "text": "가르친 손 모양 모델을 껐어요. 규칙만으로 판정해요 (견본은 남아 있어요)."}
        self.llmChanged.emit()


    @Slot()
    def poseTeachNew(self):
        """[새 동작 등록] (2026-11-11 request: "새로운 동작을 곧바로 이어서 등록할 수 있도록"): the shape taught so far
        is done with; a fresh look starts at once, and when it ends the screen asks for its name."""
        self.teach_focus, self.teach_adding, self.teach_new = "", False, True
        self.teach_view = {"state": "", "text": "새 손 모양을 보여 주세요. 끝나면 이름을 지어 저장하면 돼요."}
        self.startPoseProbe()


    @Slot()
    def poseTeachAgain(self):
        """[한 번 더 보여주기]: one more look at the shape being taught. It is saved as an example of that shape by
        itself when the look ends - whatever the rules call it, and without the "new hand shape" form (2026-11-11
        report: the form for a NEW shape came up after it, as if another shape were being made)."""
        if not self.teach_focus:
            return
        self.teach_adding = True
        self.startPoseProbe()


    @Slot(str)
    def poseTeachMore(self, posture):
        """[수정] → [보여 주고 추가 학습] on a connected command's card (2026-11-12 request): that command's hand shape
        becomes the one being taught and one more look at it starts. The look is saved as an example of it, and from
        two examples on the model is trained and put to use by itself, as with [한 번 더 보여주기]."""
        from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
        posture = str(posture or "")
        if posture not in POSTURE_NAMES_KO or self.teach_view.get("state") == "training":
            return
        self.teach_focus, self.teach_new = posture, False
        self.teach_view = {"state": "", "text": ""}
        self.poseTeachAgain()


    def _teach_look_ended(self, probe):
        """The look started by [한 번 더 보여주기] is over."""
        from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
        name = POSTURE_NAMES_KO.get(self.teach_focus, self.teach_focus)
        if probe.get("state") == "done" and not probe.get("kind"):
            self.poseTeachSave(self.teach_focus)          # teach_adding stays on until the runtime has saved it
            return
        self.teach_adding = False
        why = ("손이 움직였어요. 가만히 든 채로" if probe.get("state") == "done" else "손이 잘 보이지 않았어요.")
        self._pose_studio_capture = False
        self.teach_view = {"state": "error", "text": f"{why} [한 번 더 보여주기]를 눌러 '{name}'을 다시 보여 주세요 (견본은 추가되지 않았어요)."}
        self.llmChanged.emit()


    @Slot()
    def startPoseProbe(self):
        self.online_commands.append(("pose_probe",))
        self.pose_probe_view = {"state": "countdown", "seconds": 3}
        self._probe_wait = True
        self._llm_set(message="손을 카메라에 보여 주세요. 3초 뒤 2초 동안 손 모양을 봅니다.")


    @Slot()
    def clearPoseProbe(self):
        self._pose_studio_capture = False
        self.online_commands.append(("pose_probe_cancel",))
        self.pose_probe_view = None
        self.teach_adding = self.teach_new = False
        self._probe_wait = False
        self.llmChanged.emit()


    @Slot()
    def captureKey(self):
        if self.key_capturing:
            return
        import sys
        if sys.platform != "win32":
            self._llm_set(message="키 기억하기는 Windows에서만 돼요. 요청에 키를 직접 적어 주세요 (예: ctrl+shift+t).")
            return
        self.key_capturing = True
        self._llm_set(message="8초 안에 기억할 키 조합을 누르세요 (예: Ctrl+Shift+T). 이 조합 하나만 기억해요.")

        def work():
            from manux.compat.hand_mouse.key_capture import capture
            combo = None
            try:
                combo = capture(timeout=8.0)
            finally:
                self.key_capturing = False
            if combo:
                self.captured_key = combo
                self._llm_set(message=f"기억한 키: {combo}")
            else:
                self._llm_set(message="키 조합이 눌리지 않았어요.")
        import threading
        threading.Thread(target=work, daemon=True).start()


    @Slot()
    def clearCapturedKey(self):
        self.captured_key = ""
        self.llmChanged.emit()


    @Slot(int, bool)
    def setLlmProposalChecked(self, index, on):
        rows = list(self.llm_result.get("proposals") or [])
        if 0 <= index < len(rows) and rows[index].get("ok"):
            rows[index] = {**rows[index], "checked": bool(on)}
            self.llm_result = {**self.llm_result, "proposals": rows}
            self.llmChanged.emit()


    @Slot()
    def applyLlmProposals(self):
        from manux.compat.hand_mouse.action_bindings import get_action_registry
        from manux.compat.hand_mouse.llm_binding import History, apply
        rows = list(self.llm_result.get("proposals") or [])
        try:
            done = apply(rows, get_action_registry(), history=History(), request=str(self.llm_result.get("request", "")))
        except Exception as exc:
            self._llm_set(message=f"저장하지 못했어요: {exc}")
            return
        if not done:
            self._llm_set(message="선택한 연결이 없어요.")
            return
        if all(line.startswith("저장하지 못함") for line in done):   # nothing was saved: keep the proposals on screen
            self._llm_set(message=" / ".join(done))
            return
        self.llm_result = {}
        self.online_commands.append(("seq_reload",))    # a new sequence: the runtime reads the file again
        self.drawShapesChanged.emit()
        self.training_note = "말·글로 연결: " + " / ".join(done)
        self.refreshTraining()
        self.stateChanged.emit()
        self._llm_set(message="적용했어요: " + " / ".join(done))


    @Property("QVariantList", notify=llmChanged)
    def llmHistory(self):
        """What the AI connected, newest first, with whether it is still in force (src/llm_binding.History)."""
        from manux.compat.hand_mouse.action_bindings import get_action_registry
        from manux.compat.hand_mouse.llm_binding import History
        try:
            rows = History().rows(get_action_registry())
        except Exception:
            return []
        from manux.compat.hand_mouse.action_bindings import works_in
        mode = self._hand_mode()
        for row in rows:                         # a gesture of the other hand mode does nothing in the one in use
            row["mode_works"] = works_in(str(row.get("mode", "all")), mode)
        return rows


    @Slot(int)
    def deleteLlmConnection(self, index):
        from manux.compat.hand_mouse.action_bindings import get_action_registry
        from manux.compat.hand_mouse.llm_binding import History
        try:
            message = History().delete(int(index), get_action_registry())
        except ValueError as exc:
            message = str(exc)
        self.online_commands.append(("seq_reload",))
        self.drawShapesChanged.emit()
        self.refreshTraining()
        self.stateChanged.emit()
        self._llm_set(message=message)


    @Slot(str, result="QVariantMap")
    def gesturePreview(self, key):
        """A moving hand picture of a gesture, from its own saved joints (src/gesture_preview.py). Cached a few
        seconds: the AI list re-reads its rows on every status message."""
        import time
        cache = getattr(self, "_preview_cache", None)
        if cache is None:
            cache = self._preview_cache = {}
        key = str(key or "")
        hit = cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < 10:
            return hit[1]
        try:
            from manux.compat.hand_mouse.action_bindings import get_action_registry
            from manux.compat.hand_mouse.gesture_preview import preview
            value = preview(key, get_action_registry())
        except Exception:
            value = {"frames": [], "source": "", "move": "", "shapes": [], "steps": [], "fps": 15}
        cache[key] = (time.monotonic(), value)
        return value


    @Slot(int, str, str, float, result=bool)
    def editLlmConnection(self, index, action_type, action_value, hold_seconds):
        """Change an AI connection's command and (hold gestures; < 0 = leave it) how long the shape is held."""
        from manux.compat.hand_mouse.action_bindings import get_action_registry
        from manux.compat.hand_mouse.llm_binding import History
        ok = True
        try:
            message = History().edit(int(index), get_action_registry(), str(action_type), str(action_value),
                                     None if float(hold_seconds) < 0 else float(hold_seconds))
        except ValueError as exc:
            message, ok = str(exc), False
        self.refreshTraining()
        self.stateChanged.emit()
        self._llm_set(message=message)
        return ok


    @Slot(int, bool)
    def setLlmMotionOnly(self, index, on):
        """The card's [모션 모드에서만] switch: the gesture counts only while motion mode is on (src/llm_binding)."""
        from manux.compat.hand_mouse.action_bindings import get_action_registry
        from manux.compat.hand_mouse.llm_binding import History
        try:
            message = History().set_motion_only(int(index), get_action_registry(), bool(on))
        except ValueError as exc:
            message = str(exc)
        self.online_commands.append(("seq_reload",))
        self.stateChanged.emit()
        self._llm_set(message=message)


    @Slot(int)
    def forgetLlmConnection(self, index):
        from manux.compat.hand_mouse.llm_binding import History
        History().forget(int(index))
        self._llm_set(message="목록에서 지웠어요.")


    @Slot(int)
    def undoLlmConnection(self, index):
        from manux.compat.hand_mouse.action_bindings import get_action_registry
        from manux.compat.hand_mouse.llm_binding import History
        try:
            message = History().undo(int(index), get_action_registry())
        except ValueError as exc:
            message = str(exc)
        self.online_commands.append(("seq_reload",))
        self.drawShapesChanged.emit()
        self.refreshTraining()
        self.stateChanged.emit()
        self._llm_set(message=message)


    @Slot()
    def clearLlmResult(self):
        self.llm_result = {}
        self._llm_set(message="")


    @Slot()
    def startVoiceTyping(self):
        """Windows voice typing (Win+H) into the focused text box: speech and typing go through the same request box."""
        import sys
        if sys.platform != "win32" or self.dry_run:
            self._llm_set(message="음성 입력은 Windows의 음성 입력(Win+H)을 씁니다. 글상자를 누르고 Win+H를 눌러 주세요.")
            return

        def press():
            try:
                self.service.request_voice_typing()
            except Exception:
                self._llm_set(message="음성 입력을 열지 못했어요. 글상자를 누르고 Win+H를 직접 눌러 주세요.")
        QTimer.singleShot(200, press)      # after the text box has the focus
        self._llm_set(message="말씀하세요. 끝나면 [제안 받기]를 누르세요. (Windows 음성 입력, 인터넷 필요)")


    def _set_pose_probe(self, probe):
        """The runtime's view of [보여주기] (None, countdown, holding, done, failed)."""
        if self._probe_wait:
            # the button was just pressed: until the runtime has started the new look its snapshots still carry the
            # PREVIOUS result. 2026-11-11 report: that old result (saved as an example, so "taught") was taken for the
            # new look's - it switched "this look adds an example" off and trained again, and when the new look ended
            # the form for a new hand shape came up
            if (probe or {}).get("state") not in ("countdown", "holding"):
                return
            self._probe_wait = False
        if probe == self.pose_probe_view:
            return
        before = self.pose_probe_view or {}
        ended = (probe or {}).get("state") in ("done", "failed") and before.get("state") in ("countdown", "holding")
        saved = (probe or {}).get("taught") if before.get("state") == "done" and not before.get("taught") else None
        self.pose_probe_view = probe
        if ended and self.teach_adding:
            self._teach_look_ended(probe)
        if saved:
            self.teach_adding = False
        self.llmChanged.emit()
        if saved:
            self._after_example_saved(saved)


    def _set_probe_shot(self, shot):
        """(number, BGR picture, the look's pictures in order) of a [보여주기] result: the still and the replay."""
        if shot is None or shot[0] == getattr(self, "probe_shot_seq", 0):
            return
        img = shot[1]
        self.probe_shot_ok = img is not None
        to_image = lambda a: QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format_BGR888).copy()
        self.probe_shot.frames = [to_image(a) for a in (shot[2] if len(shot) > 2 and img is not None else []) if a is not None]
        if img is not None:
            self.probe_shot.image = to_image(img)
        self.probe_shot_seq = shot[0]
        self.llmChanged.emit()


    @Property("QVariantList", constant=True)
    def mouseActionPresets(self):
        from manux.compat.hand_mouse.action_bindings import MOUSE_ACTION_LABELS
        return [{"value": k, "label": v["label"], "badge": v["badge"]} for k, v in MOUSE_ACTION_LABELS.items()]
