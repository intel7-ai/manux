"""MediaPipe's published Hand Landmarker API, isolated from all mouse code."""
from pathlib import Path
import time

import cv2
import mediapipe as mp

from .tracking_core import Hand
from .recovery import RecoverySearch


class Landmarker:
    def __init__(self, max_width=1280, num_hands=1):
        model = Path(__file__).resolve().parents[1]/"models"/"hand_landmarker.task"
        self.max_width = max_width
        self.last_ms = 0.0
        self.last_timestamp = 0
        self.recovery = RecoverySearch()
        self.last_source = "primary"
        self.primary_count = 0
        self.recovery_model = None
        self.model_bytes = model.read_bytes()
        self.model = mp.tasks.vision.HandLandmarker.create_from_options(
            mp.tasks.vision.HandLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_buffer=self.model_bytes),
                running_mode=mp.tasks.vision.RunningMode.VIDEO,
                num_hands=num_hands,
                min_hand_detection_confidence=.6,
                min_hand_presence_confidence=.6,
                min_tracking_confidence=.6,
            ))

    def detect(self, frame, captured_at):
        start = time.perf_counter()
        original = frame
        height, width = frame.shape[:2]
        if width > self.max_width:
            frame = cv2.resize(frame, (self.max_width, round(height*self.max_width/width)),
                               interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        stamp = max(self.last_timestamp+1, int(captured_at*1000))
        self.last_timestamp = stamp
        result = self.model.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), stamp)
        hands = self._hands(result)
        self.primary_count = len(hands)
        if hands:
            self.last_source = "primary"
            self.recovery.observe_primary(hands,captured_at)
        else:
            hands = self.recovery.search(original,captured_at,self._detect_image)
            self.last_source = "recovery" if hands else "missing"
        self.last_ms = (time.perf_counter()-start)*1000
        return hands

    @staticmethod
    def _hands(result):
        hands=[]
        for index, points in enumerate(result.hand_landmarks or []):
            label, score = "", 0.0
            if result.handedness and result.handedness[index]:
                category = result.handedness[index][0]
                label, score = category.category_name or "", float(category.score or 0)
            hands.append(Hand([(float(p.x),float(p.y),float(p.z)) for p in points], label, score))
        return hands

    def _detect_image(self, frame):
        if self.recovery_model is None:
            # Fresh detection ignores the VIDEO tracker's previous ROI. Keep the
            # same thresholds; recovery is not a global confidence reduction.
            self.recovery_model=mp.tasks.vision.HandLandmarker.create_from_options(
                mp.tasks.vision.HandLandmarkerOptions(
                    base_options=mp.tasks.BaseOptions(model_asset_buffer=self.model_bytes),
                    running_mode=mp.tasks.vision.RunningMode.IMAGE,num_hands=1,
                    min_hand_detection_confidence=.6,min_hand_presence_confidence=.6))
        h,w=frame.shape[:2]
        if max(h,w)>960:
            scale=960/max(h,w)
            frame=cv2.resize(frame,(round(w*scale),round(h*scale)),interpolation=cv2.INTER_AREA)
        rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
        return self._hands(self.recovery_model.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=rgb)))

    def diagnostics(self):
        return dict(source=self.last_source,primary_hands=self.primary_count,**self.recovery.diagnostics())

    def remember_selected(self,points,now):
        self.recovery.remember_selected(points,now)

    def close(self):
        self.model.close()
        if self.recovery_model is not None:
            self.recovery_model.close()
