"""Qt presentation state for worker-backed recording inspection and replay. No data imports."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import threading
import time

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, Qt


class StudioBridge(QObject):
    changed = Signal()
    frameChanged = Signal()
    playbackChanged = Signal()
    resultReady = Signal(int, object)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner, self.service = owner, owner.tc
        self.rows, self.versions, self.events, self.frames, self.diagnostics = [], [], [], [], []
        self.models, self.remote_models = [], []
        self.current, self.simulation = {}, {}
        self.key, self.note, self.action = "", "녹화를 불러와 검수하세요.", ""
        self.index, self.generation = 0, 0
        self.pending, self.reading, self.closed = False, False, False
        self.active = False
        self.timestamps, self.hand_indices = [], []
        self.speed, self.play_origin, self.clock_origin = 1., 0., 0.
        self._clock = time.monotonic
        self.play_timer = QTimer(self)
        self.play_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.play_timer.setInterval(16)
        self.play_timer.timeout.connect(self._advance)
        self.pose_note = "팔·어깨 기록은 기본 꺼짐 · 켜면 녹화 중 추가 처리"
        self.resultReady.connect(self._apply)
        self.timer = QTimer(self)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self._poll)

    @Property(bool, notify=changed)
    def busy(self):
        job = getattr(self.service, "studio_job", None)
        return self.pending or self.reading or bool(job and job.running)

    @Property(str, notify=changed)
    def status(self): return self.note

    @Property(bool, notify=changed)
    def poseEnabled(self): return bool(self.owner.settings.get("tracking.pose_enabled"))

    @Property(str, notify=changed)
    def poseStatus(self): return self.pose_note

    @Slot()
    def togglePose(self):
        if self.busy or self.owner.rec_mode != "off" or self.owner.training_job.running or self.service.hub_job.running:
            return
        self.owner._pause()
        self.owner._set("tracking.pose_enabled", not self.poseEnabled)
        self.changed.emit()

    @Property("QVariantList", notify=changed)
    def recordings(self): return self.rows

    @Property("QVariantList", notify=changed)
    def modelVersions(self): return self.versions

    @Property("QVariantList", notify=changed)
    def localModels(self): return self.models

    @Property("QVariantList", notify=changed)
    def remoteModels(self): return self.remote_models

    @Slot(str, str, str)
    def modelAction(self, action, identifier="", name=""):
        if action in ("remote", "upload", "download", "rename", "preview", "apply"):
            self._run("model_" + action, identifier=identifier, name=name)

    @Slot()
    def importModel(self):
        if self.busy: return
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(None, "모델 파일 가져오기", "", "MANUX 모델 (*.onnx *.json)")
        if path: self._run("model_import", source=path)

    @Slot(str)
    def poseDatasetAction(self, action):
        if action in ("upload", "download"):
            if action == "upload": self.owner.saveStudioDataset()
            self._run("pose_" + action, classes=[self.owner.studioGroundTruth])

    @Property("QVariantList", notify=changed)
    def diagnosticRows(self): return self.diagnostics

    @Property("QVariantMap", notify=changed)
    def selected(self): return self.current

    @Property(str, notify=changed)
    def selectedKey(self): return self.key

    @Property("QVariantList", notify=changed)
    def timeline(self): return self.events

    @Property("QVariantMap", notify=changed)
    def simulationReport(self): return self.simulation

    @Property(int, notify=frameChanged)
    def frameCount(self): return len(self.frames)

    @Property(int, notify=frameChanged)
    def frameIndex(self): return self.index

    @Property("QVariantMap", notify=frameChanged)
    def frame(self): return self.frames[self.index] if self.frames else {}

    @Property(bool, notify=playbackChanged)
    def playing(self): return self.play_timer.isActive()

    @Property(float, notify=playbackChanged)
    def playbackSpeed(self): return self.speed

    @Property(int, notify=frameChanged)
    def handFrameCount(self): return len(self.hand_indices)

    @Slot()
    def pause(self):
        self.play_timer.stop()
        self.playbackChanged.emit()

    @Slot()
    def togglePlayback(self):
        if self.playing:
            self._advance()
            self.pause()
        elif self.frames and self.active and not self.busy:
            if self.index == len(self.frames) - 1: self.seek(0)
            self.play_origin, self.clock_origin = self.frame["t"], self._clock()
            self.play_timer.start()
            self.playbackChanged.emit()

    @Slot(float)
    def setPlaybackSpeed(self, speed):
        if speed not in (.5, 1., 2.): return
        if self.playing:
            now = self._clock()
            self.play_origin += (now - self.clock_origin) * self.speed
            self.clock_origin = now
        self.speed = speed
        self.playbackChanged.emit()

    def _advance(self):
        if not self.playing or not self.frames: return
        t = self.play_origin + (self._clock() - self.clock_origin) * self.speed
        index = max(0, min(len(self.frames) - 1, bisect_right(self.timestamps, t) - 1))
        if index != self.index:
            self.index = index
            self.frameChanged.emit()
        if t >= self.timestamps[-1]: self.pause()

    @Slot()
    def nextHand(self):
        """Seek to the next measured hand, wrapping explicitly at the end."""
        if not self.hand_indices: return
        pos = bisect_right(self.hand_indices, self.index)
        self.seek(self.hand_indices[pos] if pos < len(self.hand_indices) else self.hand_indices[0])

    @Property(float, notify=frameChanged)
    def frameDelay(self):
        if self.index + 1 >= len(self.frames): return 33.
        return max(1., min(10000., (self.frames[self.index+1]["t"]-self.frames[self.index]["t"])*1000))

    @Slot(int)
    def seek(self, index):
        self.pause()
        self.index = min(max(0, index), max(0, len(self.frames) - 1))
        self.frameChanged.emit()

    @Slot(float)
    def seekTime(self, t):
        self.seek(bisect_left(self.timestamps, t))

    @Slot()
    def nextEvent(self):
        if not self.frames: return
        t = self.frame.get("t", 0)
        event = next((e for e in self.events if e["t"] > t + .001), None)
        if event: self.seekTime(event["t"])

    def _run(self, action, **values):
        if self.closed or self.busy:
            return False
        self.pause()
        if self.owner.rec_mode != "off" or self.owner.training_job.running or self.service.hub_job.running:
            self.note = "녹화·학습·공유가 끝난 뒤 시작하세요."
            self.changed.emit()
            return False
        start = getattr(self.service, "studio_request", None)
        if start is None:
            self.note = "스튜디오 구성요소가 없습니다."
            self.changed.emit()
            return False
        self.owner._pause()
        values["settings"] = {key: self.owner.settings.get(key) for key in self.owner.settings.schema.keys()}
        values["global_tcn_enabled"] = getattr(self.owner, "_tcn_enabled", True)
        if hasattr(self.service, "simulation_bindings"):
            values["bindings"] = self.service.simulation_bindings
        if self.key and action in ("load", "simulate", "select", "trash", "clean", "augment"):
            values["expected_sha256"] = self.current.get("sha256")
        if not start(action, **values):
            self.note = str(self.service.studio_job.snapshot().get("error", "시작하지 못했습니다."))
            self.changed.emit()
            return False
        self.generation += 1
        self.pending, self.action, self.note = True, action, "준비 중…"
        self.timer.start()
        self.changed.emit()
        return True

    @Slot()
    def refresh(self): self._run("scan")

    @Slot()
    def open(self):
        self.active = True
        self.refresh()

    @Slot()
    def hide(self):
        self.active = False
        self.pause()

    @Slot(str)
    def selectRecording(self, key):
        if self.busy: return
        row = next((r for r in self.rows if r["key"] == key), None)
        if row is None: return
        self.key, self.current = key, row
        self.pause()
        self.frames, self.events, self.simulation, self.index = [], [], {}, 0
        self.timestamps, self.hand_indices = [], []
        self.changed.emit()
        self.frameChanged.emit()
        self._run("load", key=key)

    @Slot(str)
    def transform(self, action):
        if action in ("clean", "augment", "trash") and self.key:
            self._run(action, key=self.key)

    @Slot(bool)
    def include(self, value):
        if self.key: self._run("select", key=self.key, included=value)

    @Slot()
    def simulate(self):
        if self.key: self._run("simulate", key=self.key)

    @Slot(str)
    def restore(self, version): self._run("restore", version=version)

    @Slot()
    def diagnose(self): self._run("diagnostics")

    @Slot()
    def cancel(self):
        job = getattr(self.service, "studio_job", None)
        if job: job.stop()

    def _poll(self):
        state = self.service.studio_job.snapshot()
        self.note = state.get("error") or state.get("stage", "처리 중…")
        if state.get("running"):
            self.changed.emit()
            return
        self.timer.stop()
        self.pending = False
        if state.get("error"):
            self.changed.emit()
            return
        self.reading = True
        generation = self.generation
        def load():
            try: result = self.service.studio_result()
            except (OSError, ValueError): result = {"note": "완료 결과를 읽지 못했습니다. 다시 실행하세요."}
            if not self.closed: self.resultReady.emit(generation, result)
        threading.Thread(target=load, daemon=True).start()
        self.changed.emit()

    @Slot(int, object)
    def _apply(self, generation, result):
        if self.closed or generation != self.generation: return
        self.reading = False
        if "recordings" in result:
            self.rows = result["recordings"]
            current = next((r for r in self.rows if r["key"] == self.key), None)
            if current:
                if current.get("sha256") != self.current.get("sha256"):
                    self.frames, self.events, self.index = [], [], 0
                self.current = current
            elif self.key:
                self.current, self.key, self.frames, self.events, self.index = {}, "", [], [], 0
        if "versions" in result: self.versions = result["versions"]
        if "models" in result: self.models = result["models"]
        if result.get("spatial_selected"):
            self.owner.pose_studio.model_summary = self.owner.pose_studio._load_saved_model_summary()
            self.owner.studioChanged.emit()
        if "remote_models" in result: self.remote_models = result["remote_models"]
        if "diagnostics" in result: self.diagnostics = result["diagnostics"]
        if "frames" in result:
            self.pause()
            self.frames, self.events, self.current = result["frames"], result["events"], result["recording"]
            self.simulation, self.index = result.get("simulation", {}), 0
        self.timestamps = [f["t"] for f in self.frames]
        self.hand_indices = [i for i, f in enumerate(self.frames) if len(f.get("lm", [])) == 21]
        self.note = result.get("note", self.note)
        self.changed.emit()
        self.frameChanged.emit()
        if self.action in ("select", "clean", "augment", "trash", "restore", "model_apply", "model_preview"):
            self.owner.refreshTraining()

    def close(self):
        self.pause()
        self.closed = True
        self.generation += 1
        self.timer.stop()
        self.cancel()
