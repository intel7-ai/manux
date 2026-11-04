"""
Settings window for Hand-Mouse (Tkinter, opened with O / 0 / F2).

- Grouped tabs, Korean labels and a one-line hint per setting
- Each row: slider + exact-value box (type a number and press Enter)
- Changes apply to the running engine immediately
- Buttons: Save (config/gestures.json), Revert to saved, Defaults
- Values changed elsewhere ([ ], +/-, R reload) are reflected live

The Tk window runs in its own thread (Tk calls stay on that thread); the main loop only
posts show / hide / quit commands through a queue.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    from src.gesture_engine import DEFAULT_CONFIG
except ImportError:  # pragma: no cover
    from gesture_engine import DEFAULT_CONFIG

WINDOW_TITLE = "Hand-Mouse Settings"


@dataclass(frozen=True)
class Setting:
    key: str     # engine.config key
    label: str   # shown label (Korean OK in Tk)
    lo: float
    hi: float
    step: float
    hint: str
    group: str
    kind: str = "float"  # "float" (slider + number box) or "bool" (checkbox)

    @property
    def decimals(self) -> int:
        s = f"{self.step:.6f}".rstrip("0")
        return max(0, len(s.split(".")[1]) if "." in s else 0)

    def clamp(self, value: Any) -> Any:
        if self.kind == "bool":
            return bool(value)
        v = max(self.lo, min(self.hi, float(value)))
        snapped = self.lo + round((v - self.lo) / self.step) * self.step
        return round(max(self.lo, min(self.hi, snapped)), 6)

    def fmt(self, value: float) -> str:
        return f"{float(value):.{self.decimals}f}"


GROUPS = [
    ("FLING", "엄지·검지 (스크롤 · 던지기)"),
    ("PINCH", "핀치 · 클릭"),
    ("MOVE", "커서 이동"),
    ("SWITCH", "창 전환 · 바탕화면"),
]

SETTINGS: List[Setting] = [
    # Thumb + index: grab-scroll
    Setting("grab_scroll_enabled", "엄지·검지로 페이지 잡고 스크롤", 0, 1, 1,
            "엄지+검지를 붙인 채 손을 움직이면 터치스크린처럼 페이지가 따라 움직임 (Record & Fling을 켜면 그쪽이 우선)",
            "FLING", kind="bool"),
    Setting("scroll_gain", "스크롤 속도", 1000.0, 80000.0, 500.0,
            "손을 프레임 폭만큼 움직였을 때 휠 양 (120 = 휠 한 칸)", "FLING"),
    Setting("scroll_invert", "스크롤 방향 반대로", 0, 1, 1,
            "켜면 휠처럼: 손을 내리면 페이지가 위로 (기본은 터치스크린처럼 페이지가 손을 따라감)", "FLING", kind="bool"),
    Setting("scroll_inertia", "스크롤 관성", 0, 1, 1,
            "손을 움직이던 중에 핀치를 떼면 페이지가 그 속도로 계속 미끄러지다 서서히 멈춤 (멈춘 뒤 떼면 관성 없음)",
            "FLING", kind="bool"),
    Setting("scroll_inertia_time", "관성 지속 시간 (초)", 0.1, 1.5, 0.05,
            "클수록 오래 미끄러짐 (감속 시간 상수)", "FLING"),
    # Record & Fling
    Setting("record_fling_enabled", "엄지·검지 Record & Fling 사용", 0, 1, 1,
            "켜면 엄지+검지 핀치로 경로를 그려 던지기 · 끄면 엄지+검지는 아무 동작 안 함 (주먹·클릭과 간섭 없음)",
            "FLING", kind="bool"),
    Setting("fling_strength", "던지기 거리", 0.1, 5.0, 0.05,
            "전체 재생 크기 배율 ( [ / ] 키로도 조절 )", "FLING"),
    Setting("record_gain_max", "빠른 획 가속", 1.0, 10.0, 0.1,
            "빠르게 그은 구간의 확대 배율", "FLING"),
    Setting("record_gain_min", "느린 획 배율", 0.2, 4.0, 0.1,
            "천천히 그린 구간의 확대 배율 (정밀)", "FLING"),
    Setting("record_speed_high", "가속 도달 속도", 0.3, 3.0, 0.05,
            "이 속도(프레임 폭/초) 이상이면 최대 가속 · 낮을수록 쉽게 가속", "FLING"),
    Setting("replay_max_duration", "재생 최대 시간 (초)", 0.1, 1.5, 0.05,
            "커서가 경로를 그리는 최대 시간", "FLING"),
    # Pinch / click
    Setting("pinch_threshold", "엄지·검지 핀치 감도", 0.02, 0.12, 0.001,
            "클수록 쉽게 잡힘 (해제 거리는 +0.02 자동)", "PINCH"),
    Setting("pinch_release_grace", "핀치 유지 보정 (초)", 0.0, 0.5, 0.01,
            "카메라 각도로 손끝이 잠깐 가려져도 이 시간까지 핀치 유지", "PINCH"),
    Setting("pinch_fistlike_confirm", "주먹 모양 핀치 확인 (초)", 0.0, 0.4, 0.01,
            "나머지 손가락을 쥔 채 엄지·검지를 붙이면 이 시간 유지돼야 핀치 (그 사이 주먹이 되면 주먹)", "PINCH"),
    Setting("click_threshold", "엄지·중지 클릭 감도", 0.02, 0.12, 0.001,
            "엄지+중지(좌클릭) 핀치 거리 (클수록 쉽게)", "PINCH"),
    Setting("ring_click_threshold", "엄지·약지 클릭 감도", 0.02, 0.12, 0.001,
            "엄지+약지(우클릭) 핀치 거리 (작을수록 확실히 닿아야 인식, 닿지도 않았는데 우클릭되면 낮추기)", "PINCH"),
    Setting("click_release_margin", "클릭 핀치 유지 여유", 0.0, 0.06, 0.002,
            "중지·약지·새끼 핀치는 클릭 감도 + 이 값을 넘어야 풀린 것으로 판정 (클수록 깜빡임↓)", "PINCH"),
    Setting("click_release_grace", "클릭 핀치 끊김 보정 (초)", 0.0, 0.2, 0.01,
            "이 시간보다 짧은 인식 끊김은 무시 (너무 길면 빠른 더블클릭이 안 됨)", "PINCH"),
    Setting("ui_magnet_enabled", "버튼 근처에서 커서 감속 (에임 어시스트)", 0, 1, 1,
            "닫기(X) 버튼·체크박스·링크 같은 클릭 대상 근처에서 커서가 자동으로 느려져 작은 대상을 맞히기 쉬워짐 "
            "(커서를 끌어당기지는 않음, 주먹 빠른 이동에는 적용 안 됨)", "MOVE", kind="bool"),
    Setting("ui_magnet_radius", "감속 시작 거리 (px)", 8.0, 80.0, 1.0,
            "클릭 대상에서 이 거리 안에 들어오면 서서히 느려지기 시작", "MOVE"),
    Setting("ui_magnet_min_factor", "대상 위 속도 비율", 0.1, 1.0, 0.05,
            "클릭 대상 위에서의 커서 속도 (작을수록 더 느림, 1.0 = 감속 없음)", "MOVE"),
    Setting("long_press_drag", "엄지·중지 길게 누르면 드래그", 0, 1, 1,
            "엄지+중지를 붙인 채 유지하면 왼쪽 버튼을 누른 채 손을 따라 이동 (파일 끌기), 떼면 놓기",
            "PINCH", kind="bool"),
    Setting("long_press_time", "길게 누르기 시간 (초)", 0.55, 1.5, 0.05,
            "이 시간 이상 붙이고 있으면 드래그 시작 (0.55초 미만이면 첫 클릭과 합쳐져 더블클릭이 될 수 있음)", "PINCH"),
    Setting("long_press_drag_multiplier", "길게 눌러 드래그할 때 이동 배율", 1.0, 30.0, 0.5,
            "엄지+중지를 길게 눌러 드래그하는 동안 커서 속도 (핀치 없음 이동 대비)", "PINCH"),
    Setting("click_confirm_time", "클릭 확인 시간 (초)", 0.0, 0.3, 0.01,
            "이 시간 유지돼야 클릭 (오클릭 방지)", "PINCH"),
    Setting("double_click_window", "더블클릭 인식 시간 (초)", 0.1, 0.8, 0.05,
            "좌클릭 후 이 시간 동안 커서를 고정해 두 번째 클릭이 같은 자리에 (길수록 더블클릭 쉬움)", "PINCH"),
    Setting("double_click_move_tolerance", "더블클릭 흔들림 허용", 0.005, 0.1, 0.005,
            "이 거리(프레임 폭 비율) 이상 손을 움직이면 고정을 바로 풀고 이동 (클수록 더블클릭 쉬움)", "PINCH"),
    # Movement
    Setting("cursor_interpolation", "커서 보간 (부드러운 이동)", 0, 1, 1,
            "카메라는 초당 30번이지만 커서는 초당 144번 부드럽게 이동 · 끄면 반응이 약 1프레임 빠르지만 뚝뚝 끊겨 보임",
            "MOVE", kind="bool"),
    Setting("relative_gain_min", "이동 감도 (천천히)", 0.05, 3.0, 0.05,
            "핀치 없이 천천히 움직일 때 배율 (정밀)", "MOVE"),
    Setting("relative_gain_max", "이동 감도 (빠르게)", 0.5, 6.0, 0.1,
            "핀치 없이 빠르게 움직일 때 배율", "MOVE"),
    Setting("smoothing", "이동 부드러움", 0.05, 1.0, 0.01,
            "커서 스무딩 (손떨림 보정이 켜져 있으면 손떨림 보정이 대신 처리)", "MOVE"),
    Setting("tremor_min_cutoff", "손떨림 보정 (멈춰 있을 때)", 0.1, 5.0, 0.05,
            "낮을수록 강한 보정 · 가만히 조준할 때 손떨림 제거 (너무 낮으면 느리게 따라옴)", "MOVE"),
    Setting("tremor_beta", "빠른 움직임 반응", 0.0, 40.0, 0.5,
            "클수록 빠르게 움직일 때 보정을 풀어 지연 감소 (0 = 항상 같은 강도)", "MOVE"),
    Setting("tremor_deadzone", "미세 떨림 무시 거리", 0.0, 0.01, 0.0005,
            "이보다 작은 손 움직임은 완전히 무시 → 조준 시 커서 고정 (0 = 끔)", "MOVE"),
    Setting("fist_curl_enter", "주먹 판정 기준", 0.5, 1.3, 0.05,
            "네 손가락 끝이 모두 손목 쪽으로 이만큼 말려야 주먹 (작을수록 꽉 쥐어야 함)", "MOVE"),
    Setting("fist_confirm_time", "주먹 확인 시간 (초)", 0.0, 0.5, 0.01,
            "주먹 모양이 이 시간 유지돼야 빠른 이동 시작 (지나가는 동작은 무시)", "MOVE"),
    Setting("fist_dpi_multiplier", "주먹 이동 배율", 1.0, 30.0, 0.5,
            "주먹을 쥐고 움직일 때 속도 배율", "MOVE"),
    Setting("active_margin", "절대좌표 여백", 0.0, 0.45, 0.01,
            "절대좌표 모드 전용 · 클수록 DPI↑ ( M 키로 모드 전환 )", "MOVE"),
    # Task switcher
    Setting("switcher_step", "창 선택 이동 거리", 0.02, 0.2, 0.005,
            "방향키 모드 전용: 손을 이만큼 옮기면 한 칸 이동 (기본 포인터 모드는 커서로 선택)", "SWITCH"),
    Setting("switcher_timeout", "자동 확정 시간 (초)", 3.0, 30.0, 1.0,
            "창 전환 화면이 이 시간 넘게 열려 있으면 현재 선택으로 확정", "SWITCH"),
    Setting("switcher_confirm_delay", "창 전환 확정 대기 (초)", 0.0, 0.8, 0.05,
            "엄지+중지(열기)/엄지+약지(취소)가 이 시간 뒤 실행 · 그 사이 주먹을 쥐면 무시 (주먹 쥐다 닫힘 방지)", "SWITCH"),
    Setting("switcher_fist_multiplier", "창 전환 중 주먹 배율", 1.0, 30.0, 0.5,
            "창 전환 화면에서 주먹으로 움직일 때 속도 (썸네일이 작아서 평소보다 낮게)", "SWITCH"),
    Setting("pinky_click_threshold", "엄지·새끼 핀치 감도", 0.02, 0.12, 0.001,
            "창 전환을 여는 엄지+새끼 거리 (클수록 쉽게) · 카메라 창 상단 Pky 값 참고", "SWITCH"),
    Setting("desktop_thumb_tuck", "바탕화면: 엄지 접힘 기준", 0.15, 0.9, 0.01,
            "엄지 끝 ~ 약지/새끼 뿌리 거리 ÷ 손바닥 길이가 이 값 이하면 '엄지 접음' (클수록 쉽게) · 카메라 창 Tuck 값", "SWITCH"),
    Setting("desktop_hold", "바탕화면: 유지 시간 (초)", 0.1, 1.5, 0.05,
            "엄지 접고 네 손가락 편 자세를 이 시간 유지하면 Win+D (길수록 오작동 적음)", "SWITCH"),
    Setting("desktop_extended_ratio", "바탕화면: 손가락 펴짐 기준", 1.2, 2.0, 0.05,
            "네 손가락 굽힘 비율이 모두 이 값 이상이어야 '펴짐' (작을수록 쉽게)", "SWITCH"),
    Setting("gesture_exclusive_time", "바탕화면 ↔ 창 전환 배타 시간 (초)", 0.0, 3.0, 0.1,
            "한쪽이 실행되면 이 시간 동안 다른 쪽은 무시 (비슷한 손 모양 충돌 방지)", "SWITCH"),
]

SETTINGS_BY_KEY = {st.key: st for st in SETTINGS}


def apply_setting(config: Dict[str, Any], setting: Setting, value: Any) -> Any:
    """Write one setting into the engine config (keeps dependent values consistent)."""
    v = setting.clamp(value)
    config[setting.key] = v
    if setting.kind == "bool":
        return v
    if setting.key == "pinch_threshold":
        config["release_threshold"] = round(v + 0.02, 4)
    pairs = (("relative_gain_min", "relative_gain_max"), ("record_gain_min", "record_gain_max"))
    for lo_key, hi_key in pairs:
        if setting.key == lo_key and float(config.get(hi_key, v)) < v:
            config[hi_key] = v
        if setting.key == hi_key and float(config.get(lo_key, v)) > v:
            config[lo_key] = v
    return v


def reset_to_defaults(config: Dict[str, Any]) -> None:
    """Restore every setting shown in the window to its built-in default."""
    for st in SETTINGS:
        if st.key in DEFAULT_CONFIG:
            apply_setting(config, st, DEFAULT_CONFIG[st.key])


# ---------------------------------------------------------------------------- colors
BG = "#f4f5f8"
CARD = "#ffffff"
TEXT = "#1f2430"
MUTED = "#6b7280"
ACCENT = "#2563eb"
OK = "#15803d"


class SettingsPanel:
    """Tkinter settings window bound to a live engine (runs in its own thread)."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.is_open = False
        self._q: "queue.Queue[str]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        # Tk objects (only touched on the Tk thread)
        self._root = None
        self._vars: Dict[str, Any] = {}
        self._entries: Dict[str, Any] = {}
        self._dragging: Optional[str] = None
        self._status = None

    # ------------------------------------------------------------ main-loop API
    def toggle(self) -> bool:
        if self.is_open:
            self.close()
        else:
            self.open()
        return self.is_open

    def open(self) -> None:
        self.is_open = True
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="settings-ui", daemon=True)
            self._thread.start()
        else:
            self._q.put("show")

    def close(self) -> None:
        self.is_open = False
        self._q.put("hide")

    def shutdown(self) -> None:
        self.is_open = False
        self._q.put("quit")

    def update(self) -> None:
        """Kept for main-loop compatibility (the Tk thread updates itself)."""

    # ------------------------------------------------------------ value logic
    def set_value(self, key: str, value: float) -> float:
        return apply_setting(self.engine.config, SETTINGS_BY_KEY[key], value)

    def value(self, key: str) -> float:
        st = SETTINGS_BY_KEY[key]
        return float(self.engine.config.get(key, DEFAULT_CONFIG.get(key, st.lo)))

    # ------------------------------------------------------------ Tk thread
    def _run(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        self._root = root
        root.title(WINDOW_TITLE)
        root.configure(bg=BG)
        root.minsize(620, 520)
        root.protocol("WM_DELETE_WINDOW", self._hide_from_ui)
        root.attributes("-topmost", True)
        root.after(400, lambda: root.attributes("-topmost", False))

        font = "Malgun Gothic"
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=(font, 10))
        style.configure("Card.TFrame", background=CARD)
        style.configure("Card.TLabel", background=CARD, foreground=TEXT, font=(font, 10, "bold"))
        style.configure("Hint.TLabel", background=CARD, foreground=MUTED, font=(font, 9))
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=(font, 15, "bold"))
        style.configure("Sub.TLabel", background=BG, foreground=MUTED, font=(font, 9))
        style.configure("Status.TLabel", background=BG, foreground=OK, font=(font, 9))
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 6), font=(font, 10))
        style.map("TNotebook.Tab", foreground=[("selected", ACCENT)])
        style.configure("Card.Horizontal.TScale", background=CARD)
        style.configure("Card.TCheckbutton", background=CARD, foreground=TEXT, font=(font, 10, "bold"))
        style.configure("Accent.TButton", foreground="white", background=ACCENT, font=(font, 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#1d4ed8")])

        header = ttk.Frame(root, padding=(18, 14, 18, 6))
        header.pack(fill="x")
        ttk.Label(header, text="Hand-Mouse 설정", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="슬라이더를 움직이거나 숫자를 입력하고 Enter → 바로 적용됩니다. "
                               "계속 쓰려면 [저장].", style="Sub.TLabel").pack(anchor="w", pady=(2, 0))

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=14, pady=6)
        for gid, gname in GROUPS:
            tab = ttk.Frame(nb, style="Card.TFrame", padding=(16, 10))
            tab.columnconfigure(1, weight=1)
            nb.add(tab, text=gname)
            row = 0
            for st in (s for s in SETTINGS if s.group == gid):
                self._build_row(tk, ttk, tab, st, row)
                row += 2

        bar = ttk.Frame(root, padding=(14, 6, 14, 14))
        bar.pack(fill="x")
        ttk.Button(bar, text="저장", style="Accent.TButton", command=self._save).pack(side="left")
        ttk.Button(bar, text="저장된 값으로 되돌리기", command=self._reload).pack(side="left", padx=6)
        ttk.Button(bar, text="기본값", command=self._defaults).pack(side="left")
        self._status = ttk.Label(bar, text="", style="Status.TLabel")
        self._status.pack(side="right")

        root.after(150, self._poll)
        root.mainloop()
        # Release every Tk object on this thread (freeing them from the main thread
        # triggers "Tcl_AsyncDelete: async handler deleted by the wrong thread")
        self._vars = {}
        self._entries = {}
        self._status = None
        self._root = None
        del root, style, header, nb, bar
        import gc

        gc.collect()

    def _build_row(self, tk: Any, ttk: Any, parent: Any, st: Setting, row: int) -> None:
        if st.kind == "bool":
            bvar = tk.BooleanVar(value=bool(self.engine.config.get(st.key, DEFAULT_CONFIG.get(st.key))))
            self._vars[st.key] = bvar
            ttk.Checkbutton(parent, text=st.label, variable=bvar, style="Card.TCheckbutton",
                            command=lambda st=st: self.set_value(st.key, self._vars[st.key].get())).grid(
                row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))
            ttk.Label(parent, text=st.hint, style="Hint.TLabel").grid(
                row=row + 1, column=0, columnspan=3, sticky="w", pady=(0, 4))
            return
        var = tk.DoubleVar(value=self.value(st.key))
        self._vars[st.key] = var

        ttk.Label(parent, text=st.label, style="Card.TLabel").grid(
            row=row, column=0, sticky="w", pady=(10, 0), padx=(0, 12))
        scale = ttk.Scale(parent, from_=st.lo, to=st.hi, variable=var, style="Card.Horizontal.TScale",
                          command=lambda _v, st=st: self._on_slide(st))
        scale.grid(row=row, column=1, sticky="ew", pady=(10, 0))
        scale.bind("<ButtonPress-1>", lambda _e, k=st.key: setattr(self, "_dragging", k))
        scale.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_dragging", None))

        entry = ttk.Entry(parent, width=7, justify="right", font=("Consolas", 11))
        entry.insert(0, st.fmt(var.get()))
        entry.grid(row=row, column=2, sticky="e", pady=(10, 0), padx=(10, 0))
        entry.bind("<Return>", lambda _e, st=st: self._on_entry(st))
        entry.bind("<FocusOut>", lambda _e, st=st: self._on_entry(st))
        self._entries[st.key] = entry

        rng = f"{st.fmt(st.lo)} ~ {st.fmt(st.hi)}"
        ttk.Label(parent, text=f"{st.hint}   ·   범위 {rng}", style="Hint.TLabel").grid(
            row=row + 1, column=0, columnspan=3, sticky="w", pady=(0, 4))

    def _set_entry(self, st: Setting, value: float) -> None:
        e = self._entries[st.key]
        if self._root is not None and self._root.focus_get() is e:
            return  # don't overwrite while the user is typing
        e.delete(0, "end")
        e.insert(0, st.fmt(value))

    def _on_slide(self, st: Setting) -> None:
        v = self.set_value(st.key, self._vars[st.key].get())
        self._set_entry(st, v)

    def _on_entry(self, st: Setting) -> None:
        raw = self._entries[st.key].get().strip()
        try:
            v = self.set_value(st.key, float(raw))
        except ValueError:
            v = self.value(st.key)
        self._vars[st.key].set(v)
        e = self._entries[st.key]
        e.delete(0, "end")
        e.insert(0, st.fmt(v))

    def _sync_from_config(self) -> None:
        for st in SETTINGS:
            if st.key == self._dragging:
                continue
            if st.kind == "bool":
                bvar = self._vars.get(st.key)
                want = bool(self.engine.config.get(st.key, DEFAULT_CONFIG.get(st.key)))
                if bvar is not None and bool(bvar.get()) != want:
                    bvar.set(want)
                continue
            v = self.value(st.key)
            var = self._vars.get(st.key)
            if var is not None and abs(var.get() - v) > st.step / 2:
                var.set(v)
                self._set_entry(st, v)

    def _flash(self, text: str) -> None:
        if self._status is not None:
            self._status.configure(text=text)
            self._root.after(2500, lambda: self._status.configure(text=""))

    def _save(self) -> None:
        path = self.engine.save_config()
        self._flash(f"저장됨 ✓  {path.name}")

    def _reload(self) -> None:
        self.engine.load_config()
        self._sync_from_config()
        self._flash("저장된 값으로 되돌림")

    def _defaults(self) -> None:
        reset_to_defaults(self.engine.config)
        self._sync_from_config()
        self._flash("기본값 적용됨 (저장하려면 [저장])")

    def _hide_from_ui(self) -> None:
        self.is_open = False
        if self._root is not None:
            self._root.withdraw()

    def _poll(self) -> None:
        root = self._root
        if root is None:
            return
        try:
            while True:
                cmd = self._q.get_nowait()
                if cmd == "show":
                    root.deiconify()
                    root.lift()
                    root.attributes("-topmost", True)
                    root.after(400, lambda: root.attributes("-topmost", False))
                elif cmd == "hide":
                    root.withdraw()
                elif cmd == "quit":
                    root.destroy()
                    return
        except queue.Empty:
            pass
        self._sync_from_config()
        root.after(150, self._poll)
