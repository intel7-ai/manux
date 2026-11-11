"""Qt state for the capture-plan dialog; edits are drafts until saved or started."""
import copy

from PySide6.QtCore import QObject, Property, Signal, Slot

from src import recording_plan as plans
from src.gesture_catalog import BY_KEY, is_free, is_motion_task


class RecordingPlanEditor(QObject):
    changed = Signal()
    startRequested = Signal(str, object)

    def __init__(self, path=plans.PLAN_PATH):
        super().__init__()
        self.path = path
        self.plan = plans.preset()
        self.focus = ""
        self.index = 0
        self.note = ""
        self.coverage = {}

    def open(self, focus, coverage=None):
        self.focus, self.index, self.note = focus, 0, ""
        self.coverage = coverage or {}
        try:
            self.plan = plans.load_plan(focus, self.path)
        except (OSError, ValueError, TypeError) as exc:
            self.plan = plans.preset("basic", focus=focus)
            self.note = f"저장한 설정을 읽지 못해 기본안을 표시합니다: {exc}"
        self.changed.emit()

    @Property(bool, notify=changed)
    def isMotionTask(self):
        return bool(self.focus and is_motion_task(self.focus))

    @Property(str, notify=changed)
    def title(self):
        from src.training_center import NAMES
        label = NAMES.get(self.focus, "전체 동작")
        if self.isMotionTask:
            return f"{label} · 시계열 모션 녹화 설정"
        return f"{label} · 집중 녹화 설정"

    @Property("QVariantList", notify=changed)
    def rows(self):
        out = []
        for row in self.plan["conditions"]:
            data = self.coverage.get(plans.condition_key(row), {})
            if is_free(self.focus):
                count = f"수집 {data.get('seconds', 0) / 60:.1f}분"
            else:
                count = f"수집 {data.get('reps', 0)}회"
            out.append({**row, "coverage": count})
        return out

    @Property("QVariantMap", notify=changed)
    def selected(self):
        return dict(self.plan["conditions"][self.index])

    @Property(str, notify=changed)
    def orientationGuide(self):
        row = self.plan["conditions"][self.index] if 0 <= self.index < len(self.plan["conditions"]) else {}
        orient = str(row.get("orientation", ""))
        posture = str(row.get("posture", ""))
        o_txt = plans.orientation_guide(orient, focus=self.focus)
        p_txt = plans.posture_guide(posture)
        parts = []
        if o_txt:
            if self.isMotionTask:
                parts.append(f"모션 스트로크 [{orient}]: {o_txt}")
            elif orient == "동작 고유 손모양 유지" or self.focus in ("palm_side", "thumbs_up"):
                parts.append(f"동작 고유 손 모양: {o_txt}")
            else:
                parts.append(f"손 방향 [{orient}]: {o_txt}")
        if p_txt and posture != "바로 앉기":
            parts.append(f"몸/팔 위치 [{posture}]: {p_txt}")
        return " · ".join(parts)

    @Property(int, notify=changed)
    def selectedIndex(self):
        return self.index

    @Property("QVariantMap", notify=changed)
    def choices(self):
        out = dict(plans.OPTIONS)
        if self.isMotionTask:
            out["orientation"] = list(plans.MOTION_ORIENTATIONS)
        elif self.focus in ("palm_side", "thumbs_up"):
            out["orientation"] = ["동작 고유 손모양 유지"] + [o for o in plans.STATIC_ORIENTATIONS if o != "손바닥 정면"]
        else:
            out["orientation"] = list(plans.STATIC_ORIENTATIONS) + ["동작 고유 손모양 유지"]
        return out

    @Property(bool, notify=changed)
    def neutral(self):
        return is_free(self.focus)

    @Property(str, notify=changed)
    def instruction(self):
        task = BY_KEY.get(self.focus)
        if task is None:
            return "전체 동작을 차례로 녹화합니다."
        if self.isMotionTask:
            return f"🌊 시계열 모션 요령: {task.do} (손바닥 정면 고정 불필요 · [준비] 때 시작 위치 대기 → [쓸기] 때 1회 이동 후 정지 → [복귀·쉬기] 때 시작점으로 복귀)"
        extra = " 준비 때 시작 위치에 손을 놓고, 유지 시간에 한 번 쓸고, 쉬기 때 돌아오세요." if task.direction else ""
        return task.do + extra

    @Property(bool, notify=changed)
    def allGestures(self):
        return not self.focus

    @Property(str, notify=changed)
    def summary(self):
        count = sum(r["enabled"] for r in self.plan["conditions"])
        seconds = plans.duration(self.plan, self.focus)
        if self.isMotionTask:
            total_reps = sum(int(r.get("reps", 0)) for r in self.plan["conditions"] if r.get("enabled"))
            return f"선택 {count}/{len(self.plan['conditions'])}코스 (총 {total_reps}회 스트로크) · 순수 녹화 약 {seconds / 60:.1f}분 + 준비 시간"
        return f"선택 {count}/{len(self.plan['conditions'])}자세 · 순수 녹화 약 {seconds / 60:.1f}분 + 자세 준비 시간"

    @Property(str, notify=changed)
    def message(self):
        return self.note

    @Slot(int)
    def select(self, index):
        if 0 <= index < len(self.plan["conditions"]):
            self.index = index
            self.changed.emit()

    @Slot(str, "QVariant")
    def setField(self, key, value):
        if key not in self.selected:
            return
        candidate = copy.deepcopy(self.plan)
        candidate["conditions"][self.index][key] = value
        try:
            self.plan = plans.normalise(candidate, require_enabled=False)
            self.note = "아직 저장하지 않은 설정입니다."
        except (ValueError, TypeError) as exc:
            self.note = str(exc)
        self.changed.emit()

    @Slot(str)
    def usePreset(self, name):
        self.plan, self.index = plans.preset(name, focus=self.focus), 0
        self.note = "추천 코스를 불러왔습니다." if self.isMotionTask else "추천안을 불러왔습니다. 필요한 자세만 선택해도 됩니다."
        self.changed.emit()

    @Slot(bool)
    def add(self, duplicate=False):
        if len(self.plan["conditions"]) >= 30:
            self.note = "자세는 최대 30개입니다."
        else:
            if duplicate:
                row = self.selected
                row["name"] = row["name"][:75] + " 복사"
            elif self.isMotionTask:
                row = plans.condition(
                    name="새 모션 코스",
                    orientation="기본 속도·보통 폭",
                    reps=10,
                    prep=0.5,
                    hold=1.2,
                    rest=1.0,
                )
            elif self.focus in ("palm_side", "thumbs_up"):
                row = plans.condition(name="새 자세", orientation="동작 고유 손모양 유지")
            else:
                row = plans.condition()
            self.plan["conditions"].append(row)
            self.index = len(self.plan["conditions"]) - 1
            self.note = "추가한 코스의 조건을 설정하세요." if self.isMotionTask else "추가한 자세의 조건을 설정하세요."
        self.changed.emit()

    @Slot()
    def remove(self):
        if len(self.plan["conditions"]) > 1:
            self.plan["conditions"].pop(self.index)
            self.index = min(self.index, len(self.plan["conditions"]) - 1)
            self.note = "저장하면 선택한 자세가 설정 목록에서 빠집니다. 기존 녹화는 유지됩니다."
        else:
            self.note = "자세를 하나 이상 남겨주세요."
        self.changed.emit()

    @Slot(result=bool)
    def save(self):
        try:
            self.plan = plans.save_plan(self.plan, self.focus, self.path)
            self.note = "저장했습니다. 이 동작의 다음 집중 녹화에도 적용됩니다."
        except (OSError, ValueError, TypeError) as exc:
            self.note = f"저장하지 못했습니다: {exc}"
            self.changed.emit()
            return False
        self.changed.emit()
        return True

    @Slot(result=bool)
    def start(self):
        if not self.save():
            return False
        self.startRequested.emit(self.focus, copy.deepcopy(self.plan))
        return True
