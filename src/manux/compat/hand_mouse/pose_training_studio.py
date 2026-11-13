"""Immediate snapshot/stream collection for the AI hand-shape MLP.

Each start/stop is one independent attempt, never one trial per adjacent frame.
Saved samples use the existing teacher's labels/model and preserve evaluation splits.
"""
import math
import time
from types import SimpleNamespace
from uuid import uuid4

from manux.compat.hand_mouse import posture_teach as pt
from manux.compat.hand_mouse.pose_probe import hand_posture, faced


class PoseTrainingStudio:
    def __init__(self):
        self.mode, self.streaming = 'stream', False
        self.samples = []
        self.label, self.trial = '', ''
        self.split, self.profile = 'train', ''
        self.live, self.live_at, self.last_capture = None, 0., -1.
        self.note = '동작을 고르고 수집 시작을 누르세요. 대기시간 없이 바로 수집합니다.'
        self.prediction, self.confidence = '', 0.
        self.rule, self.effective, self.decision_reason = '', '', 'no_hand'
        self.has_model, self.discrepancy_only = False, False
        self.known_shapes = set()
        self.model_shapes = set()
        self.threshold = pt.THRESHOLD

    def select(self, label):
        if label != self.label:
            self.stop()
            self.label, self.trial = label, ''

    def observe(self, landmarks, aspect, now=None):
        now = time.monotonic() if now is None else now
        was_tracked = self.live is not None
        valid = (landmarks is not None and len(landmarks) == 21 and math.isfinite(aspect) and aspect > 0
                 and all(len(p) >= 3 and all(math.isfinite(v) for v in p[:3])
                         and -.005 <= p[0] <= 1.005 and -.005 <= p[1] <= 1.005 for p in landmarks)
                 and math.hypot(landmarks[9][0] - landmarks[0][0], landmarks[9][1] - landmarks[0][1]) >= .035)
        self.live = ([[float(v) for v in p[:3]] for p in landmarks], aspect) if valid else None
        self.live_at = now
        self.prediction, self.confidence = '', 0.
        self.rule, self.effective, self.decision_reason = '', '', 'no_hand'
        self.has_model = False
        self.model_shapes = set()
        if self.live:
            joints, aspect = self.live
            points = [SimpleNamespace(x=x, y=y, z=z) for x, y, z in joints]
            self.rule = hand_posture(points, aspect, taught=False)
            model = pt.active_model()
            self.has_model = model is not None
            if model:
                self.model_shapes = set(model.classes) - {pt.OTHER}
                result = model.decision(joints, self.rule, aspect)
                self.prediction, self.confidence = result['prediction'], result['confidence']
                self.effective, self.decision_reason = faced(result['posture'], points, aspect), result['reason']
                self.threshold = result['threshold']
            else:
                self.effective, self.decision_reason = faced(self.rule, points, aspect), 'no_model'
        elif self.streaming:
            self.note = '손 인식 대기 · 손이 다시 보이면 자동으로 수집을 이어갑니다.'
        if self.streaming and self.live and not was_tracked:
            self.note = '연속 수집 중 · 손의 각도와 거리를 조금씩 바꾸세요.'
        if self.streaming and self.live and now - self.last_capture >= .05:
            self.capture(now)

    def trigger(self, split='train', profile='', now=None):
        now = time.monotonic() if now is None else now
        if self.streaming:
            self.stop()
            return
        if not self.label:
            return
        self.split = 'eval' if split == 'eval' else 'train'
        self.profile = profile
        if self.mode == 'stream' or not self.trial:
            self.trial = uuid4().hex
        if self.mode == 'stream':
            self.streaming = True
            self.note = '연속 수집 중 · 손의 각도와 거리를 조금씩 바꾸세요.'
        self.last_capture = -1.
        self.capture(now)

    def capture(self, now):
        if not self.live or now - self.live_at > .5:
            self.note = '카메라에 손을 보여주세요. 유효한 손 관절이 들어오면 수집합니다.'
            return
        if self.discrepancy_only and self.is_match():
            self.note = '정답과 최종 판정이 일치하여 건너뛰었습니다. 불일치할 때만 수집합니다.'
            return
        if len(self.samples) >= 1000:
            self.stop()
            self.note = '1,000개를 모았습니다. 표본 저장 후 계속 수집하세요.'
            return
        joints, aspect = self.live
        rule = self.rule
        self.samples.append(dict(label=self.label, trial=self.trial, split=self.split, profile=self.profile,
                                 aspect=aspect, frame=joints, rule=rule))
        self.last_capture = now
        if not self.streaming:
            self.note = '한 장 캡처했습니다. 새 수집을 누르면 독립된 다음 시도가 시작됩니다.'

    def stop(self):
        if self.streaming:
            self.note = f'수집 정지 · 미저장 표본 {len(self.samples)}개. 표본 저장 후 다시 수집하거나 MLP를 학습하세요.'
        self.streaming = False
        self.trial = ''

    def undo(self):
        self.stop()
        if self.samples:
            self.samples.pop()
        self.note = '마지막 미저장 표본을 취소했습니다.'

    def clear(self):
        self.stop()
        self.samples.clear()
        self.note = '새 수집을 시작합니다. 저장된 표본과 모델은 유지됩니다.'

    def save(self):
        self.stop()
        # Remove only committed groups. A failed write leaves its samples available for retry.
        groups = list(dict.fromkeys((s['trial'], s['aspect'], s['split']) for s in self.samples))
        saved = 0
        for group in groups:
            rows = [s for s in self.samples if (s['trial'], s['aspect'], s['split']) == group]
            first = rows[0]
            pt.save_example(first['label'], [s['frame'] for s in rows], [s['rule'] for s in rows],
                            aspect=first['aspect'], split=first['split'], profile=first['profile'],
                            trial=first['trial'])
            self.samples = [s for s in self.samples if (s['trial'], s['aspect'], s['split']) != group]
            saved += len(rows)
        self.note = f'표본 {saved}개 저장 완료 · MLP 학습을 눌러 모델을 만드세요.' if saved else '저장할 새 표본이 없습니다.'
        return saved

    def summary(self):
        pending = {}
        for row in self.samples:
            pending[row['label']] = pending.get(row['label'], 0) + 1
        return dict(mode=self.mode, streaming=self.streaming, pending=pending, pendingTotal=len(self.samples),
                    note=self.note, prediction=self.prediction, confidence=self.confidence, tracked=self.live is not None,
                    rule=self.rule, effective=self.effective, decisionReason=self.decision_reason,
                    hasModel=self.has_model, threshold=self.threshold, match=self.is_match(),
                    discrepancyOnly=self.discrepancy_only)

    def is_match(self):
        if self.live is None or not self.label:
            return None
        if self.label == pt.OTHER:
            return self.effective not in (self.known_shapes | self.model_shapes)
        return self.effective == self.label


def saved_counts():
    counts = {}
    attempts = {}
    for example in pt.load_examples():
        row = counts.setdefault(example['label'], dict(frames=0, attempts=0, evalFrames=0))
        if example.get('split') == 'eval':
            row['evalFrames'] += len(example['frames'])
        else:
            row['frames'] += len(example['frames'])
            attempts.setdefault(example['label'], set()).add(example.get('trial') or example['file'])
            row['attempts'] = len(attempts[example['label']])
    return counts
