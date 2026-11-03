"""Tk desktop shell for the existing camera and gesture engine."""
from collections import deque
import time
import tkinter as tk

import cv2

NAVY = "#112331"
MINT = "#46e5bd"
PAPER = "#f5f7f8"
WHITE = "#ffffff"
MUTED = "#506575"

ACTIONS = {
    "pointer_wait": "커서 고정 · 검지를 펴면 이동",
    "pointer_release": "커서 고정 · 손끝을 떼면 클릭",
    "move_cursor": "포인터 이동", "left_click": "좌클릭", "right_click": "우클릭",
    "scroll_grab": "스크롤 중", "drag_hold": "드래그 중", "drag_toggle": "드래그 전환",
    "grab": "동작 기록 중", "task_switcher": "창 선택", "show_desktop": "바탕화면 보기",
    "alt_tab": "창 전환",
}


class Dashboard:
    def __init__(self, dry_run=False):
        self.root = tk.Tk()
        self.root.title("Hand-Mouse · MANUX")
        self.root.geometry("1120x720")
        self.root.minsize(760, 520)
        self.root.configure(bg=PAPER)
        self.commands = deque()
        self.closed = False
        self.details = False
        self.dry_run = dry_run
        self.image = None
        self.last_preview = 0.0
        self.root.protocol("WM_DELETE_WINDOW", self.request_close)
        self.root.bind("<KeyPress>", self.on_key)

        header = tk.Frame(self.root, bg=WHITE, padx=24, pady=16)
        header.pack(fill="x")
        self.label(header, "MANUX", 21, bold=True).pack(side="left")
        self.label(header, "입력 제어", 10, fg=MUTED).pack(side="left", padx=14)
        self.control = self.button(header, "제어 시작", lambda: self.commands.append(32), accent=True)
        self.control.pack(side="right")
        self.button(header, "설정", lambda: self.commands.append(ord("o"))).pack(side="right", padx=8)

        body = tk.Frame(self.root, bg=PAPER, padx=20, pady=18)
        body.pack(fill="both", expand=True)
        side = tk.Frame(body, bg=WHITE, padx=17, pady=18, width=235)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.label(side, "시작하기", 15, bold=True).pack(anchor="w")
        self.label(side, "카메라에 손을 보여주세요.\n준비되면 제어 시작을 누르세요.", 10, fg=MUTED).pack(anchor="w", pady=(8, 20))
        self.label(side, "동작 안내", 14, bold=True).pack(anchor="w", pady=(0, 8))
        for gesture, action in (
            ("손을 편하게 이동", "포인터 이동"),
            ("엄지 + 검지", "페이지 잡고 스크롤"),
            ("엄지 + 중지", "좌클릭"),
            ("엄지 + 약지", "우클릭"),
            ("엄지 + 새끼", "창 선택"),
        ):
            self.label(side, gesture, 11, bold=True).pack(anchor="w", pady=(8, 0))
            self.label(side, action, 10, fg=MUTED).pack(anchor="w")
        self.label(side, "Space  제어 일시정지\nEsc  제어 일시정지\nF2  설정", 10, fg=MUTED).pack(side="bottom", anchor="w")

        card = tk.Frame(body, bg=WHITE, padx=18, pady=16)
        card.pack(side="left", fill="both", expand=True, padx=(14, 0))
        heading = tk.Frame(card, bg=WHITE)
        heading.pack(fill="x", pady=(0, 10))
        self.label(heading, "카메라", 16, bold=True).pack(side="left")
        self.detected = self.label(heading, "연결 대기", 10, fg=MUTED)
        self.detected.pack(side="right")
        self.preview = tk.Canvas(card, bg=NAVY, highlightthickness=0)
        self.preview.pack(fill="both", expand=True)
        self.preview_image = self.preview.create_image(0, 0, anchor="center")
        self.action = self.label(card, "입력 꺼짐", 13, bold=True)
        self.action.pack(anchor="w", pady=(12, 2))
        self.detail = self.label(card, "", 10, fg=MUTED)
        self.button(card, "상세 정보", self.toggle_details).pack(anchor="e", pady=(4, 0))

    @staticmethod
    def label(parent, text, size, bold=False, fg=NAVY):
        return tk.Label(parent, text=text, fg=fg, bg=WHITE, justify="left",
                        font=("Malgun Gothic", size, "bold" if bold else "normal"))

    @staticmethod
    def button(parent, text, action, accent=False):
        return tk.Button(parent, text=text, command=action, bg=MINT if accent else PAPER,
                         fg=NAVY, activebackground="#bcefe1", activeforeground=NAVY,
                         font=("Malgun Gothic", 10, "bold"), relief="flat", padx=15, pady=7)

    def toggle_details(self):
        self.details = not self.details
        if self.details:
            self.detail.pack(anchor="w", before=self.detail.master.winfo_children()[-1])
        else:
            self.detail.pack_forget()

    def request_close(self):
        self.closed = True

    def on_key(self, event):
        if event.keysym == "Escape":
            self.commands.append(27)
        elif event.keysym == "space":
            self.commands.append(32)
        elif event.char and event.char.lower() in "qomn+-=_[]rspc0":
            self.commands.append(ord(event.char))

    def poll(self):
        self.root.update()
        return self.commands.popleft() if self.commands else -1

    def show(self, frame, detected, paused, action, fps, detect_ms, status=""):
        self.control.configure(text="일시정지" if not paused else "제어 시작")
        self.detected.configure(text="● 손 감지됨" if detected else "○ 손 대기", fg="#08775e" if detected else MUTED)
        state = "입력 꺼짐" if paused else "연습 모드" if self.dry_run else "입력 활성"
        label = ACTIONS.get(action, "손짓 대기") if detected and not paused else ""
        self.action.configure(text=f"{state}  ·  {label}" if label else state)
        if self.details:
            self.detail.configure(text=f"{fps:.1f} fps   ·   추론 {detect_ms:.1f} ms" + (f"   ·   {status}" if status else ""))
        now = time.perf_counter()
        if now - self.last_preview < 1 / 24:
            return
        self.last_preview = now
        width, height = self.preview.winfo_width(), self.preview.winfo_height()
        if width < 2 or height < 2:
            return
        fh, fw = frame.shape[:2]
        scale = min(width / fw, height / fh)
        size = max(1, int(fw * scale)), max(1, int(fh * scale))
        rgb = cv2.cvtColor(cv2.resize(frame, size), cv2.COLOR_BGR2RGB)
        ppm = f"P6\n{size[0]} {size[1]}\n255\n".encode() + rgb.tobytes()
        self.image = tk.PhotoImage(master=self.root, data=ppm, format="PPM")
        self.preview.itemconfigure(self.preview_image, image=self.image)
        self.preview.coords(self.preview_image, width // 2, height // 2)

    def close(self):
        self.root.destroy()
