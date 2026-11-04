"""Local camera: one capture thread, newest frame only, no backlog."""
import threading
import time

import cv2


def orient(frame, mirror, rotation):
    if rotation == 90:
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    elif rotation == 180:
        frame = cv2.rotate(frame, cv2.ROTATE_180)
    elif rotation == 270:
        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return cv2.flip(frame, 1) if mirror else frame


class LatestCamera:
    def __init__(self, source=0, width=1920, height=1080):
        self.source, self.width, self.height = source, width, height
        self.condition = threading.Condition()
        self.stop_event = threading.Event()
        self.latest = None
        self.sequence = self.consumed = self.skipped = 0
        self.error = "카메라 연결 중"
        self.thread = threading.Thread(target=self._capture, name="lab-camera", daemon=True)
        self.thread.start()

    def _capture(self):
        cap = None
        try:
            cap = cv2.VideoCapture(self.source)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(self.source, cv2.CAP_DSHOW)
            if not cap.isOpened():
                raise RuntimeError("카메라를 열 수 없습니다. 카메라 번호와 사용 중인 앱을 확인하세요.")
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, 30)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            while not self.stop_event.is_set():
                ok, frame = cap.read()
                captured = time.perf_counter()
                with self.condition:
                    if ok and frame is not None:
                        if self.sequence != self.consumed:
                            self.skipped += 1
                        self.sequence += 1
                        self.latest = (self.sequence, captured, frame)
                        self.error = ""
                    else:
                        self.error = "카메라에서 새 화면이 오지 않습니다 · 다시 연결을 눌러주세요"
                    self.condition.notify_all()
                if not ok:
                    self.stop_event.wait(.05)
        except Exception as error:
            with self.condition:
                self.error = str(error)
                self.condition.notify_all()
        finally:
            if cap is not None:
                cap.release()

    def read(self):
        with self.condition:
            self.condition.wait_for(lambda: self.sequence != self.consumed or self.stop_event.is_set(), .1)
            if self.latest is None or self.sequence == self.consumed:
                return None
            self.consumed = self.sequence
            return self.latest

    def close(self):
        self.stop_event.set()
        with self.condition:
            self.condition.notify_all()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise RuntimeError("기존 카메라 연결 종료를 기다리는 중입니다. 앱을 닫고 다시 실행해주세요.")
