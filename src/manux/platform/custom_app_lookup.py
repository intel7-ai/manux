"""What a `launch_app` value starts on THIS computer (2026-11-11).

Reports: "카카오톡 연결해달라는것도 못하는데", "arduino ide 열어줘 … 시스템 경로까지 넣어줬는데 그래도 못함". The AI
that writes these values does not see the computer: it wrote `arduino`, `kakaoTalk:`, a folder
(`C:\\Program Files\\Arduino IDE`), a path with spaces. The app started each as a command line, which fails for all of
them, and said nothing.

`resolve(value)` turns a value into what is really started, looking where a person would:

  a folder or a document            opened like a double click
  a web address / app link          opened by Windows (https://…, ms-settings:, kakaotalk:)
  a program file (quoted, or with spaces and no quotes), with arguments
  a program name                    PATH, then Windows "App Paths", then the Start menu and desktop shortcuts and the
                                    programs under Program Files, by name ("arduino" -> "Arduino IDE", "kakaotalk")
  a folder that holds one program   that program ("C:\\Program Files\\Arduino IDE" -> Arduino IDE.exe) - only when the
                                    folder is under Program Files; any other folder is opened

Nothing found: `Target(kind="missing")` with a sentence for the screen. Only names are matched; nothing is sent
anywhere and nothing is started here except by `start()`.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

RUNNABLE = (".exe", ".bat", ".cmd", ".com")
NOT_THE_APP = ("unins", "uninstall", "crash", "update", "helper", "setup", "install", "report", "제거")
LINK = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]{1,}:")          # a scheme, not a drive letter (one letter)


@dataclass
class Target:
    kind: str                    # "open" (startfile) | "run" (program + args) | "missing"
    path: str = ""
    args: List[str] = field(default_factory=list)
    note: str = ""               # how it was found / why not, for the screen

    @property
    def ok(self) -> bool:
        return self.kind != "missing"

    @property
    def name(self) -> str:
        return (Path(self.path).stem if self.kind == "run" or self.path.lower().endswith(".lnk")
                else Path(self.path).name) or self.path


def _norm(text: str) -> str:
    t = str(text).lower()
    for suffix in RUNNABLE + (".lnk",):
        if t.endswith(suffix):
            t = t[: -len(suffix)]
    return re.sub(r"[\W_]+", "", t)


def _expand(text: str) -> str:
    return os.path.expandvars(os.path.expanduser(str(text).strip().strip('"')))


# ------------------------------------------------------------------ the programs a person would find
_index: Tuple[float, List[Tuple[str, str]]] = (0.0, [])
INDEX_SECONDS = 300.0


def _roots() -> List[Tuple[Path, str, int]]:
    env = os.environ.get
    out = []
    for base in (env("PROGRAMDATA"), env("APPDATA")):
        if base:
            out.append((Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs", ".lnk", 4))
    for base in (env("USERPROFILE"), env("PUBLIC")):
        if base:
            out.append((Path(base) / "Desktop", ".lnk", 1))
    for base in (env("ProgramFiles"), env("ProgramFiles(x86)"), env("LOCALAPPDATA") and str(Path(env("LOCALAPPDATA")) / "Programs")):
        if base:
            out.append((Path(base), ".exe", 3))
    return out


def programs(refresh: bool = False) -> List[Tuple[str, str]]:
    """(normalised name, path) of the shortcuts and programs on this computer; shortcuts first. Cached for 5 minutes."""
    global _index
    if not refresh and _index[1] and time.monotonic() - _index[0] < INDEX_SECONDS:
        return _index[1]
    found: List[Tuple[str, str]] = []
    for root, suffix, depth in _roots():
        if not root.is_dir():
            continue
        base = len(root.parts)
        for folder, dirs, files in os.walk(root):
            if len(Path(folder).parts) - base >= depth:
                dirs[:] = []
            for f in files:
                low = f.lower()
                if low.endswith(suffix) and not any(w in low for w in NOT_THE_APP):
                    found.append((_norm(f), str(Path(folder) / f)))
    _index = (time.monotonic(), found)
    return found


def find_by_name(name: str) -> str:
    """The shortcut or program whose name is `name`, or begins with / holds it (3+ letters). "" when none or unclear."""
    q = _norm(name)
    if len(q) < 2:
        return ""
    items = programs()
    for test in (lambda n: n == q, lambda n: len(q) >= 3 and n.startswith(q), lambda n: len(q) >= 4 and q in n):
        hits = [(n, p) for n, p in items if test(n)]
        if hits:
            hits.sort(key=lambda h: (len(h[0]), not h[1].lower().endswith(".lnk")))     # the shortest name; a shortcut first
            return hits[0][1]
    return ""


def _app_path(name: str) -> str:
    """Windows "App Paths" (what the Run box knows: chrome.exe, winword.exe, …)."""
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    exe = name if name.lower().endswith(".exe") else name + ".exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths" + "\\" + exe) as key:
                path = _expand(winreg.QueryValue(key, None))
                if path and os.path.isfile(path):
                    return path
        except OSError:
            continue
    return ""


def _program_in(folder: str) -> str:
    """The one program of an installed program's folder (named like the folder, else the only .exe)."""
    exes = [f for f in os.listdir(folder) if f.lower().endswith(".exe") and not any(w in f.lower() for w in NOT_THE_APP)]
    same = [f for f in exes if _norm(f) == _norm(Path(folder).name)]
    pick = same or (exes if len(exes) == 1 else [])
    return str(Path(folder) / pick[0]) if pick else ""


def _split(value: str) -> Tuple[str, List[str]]:
    """(program, arguments): a quoted program, else the longest beginning that is a file (a path with spaces and no
    quotes), else the first word."""
    import shlex
    v = value.strip()
    if v.startswith('"') and '"' in v[1:]:
        end = v.index('"', 1)
        rest = v[end + 1:].strip()
        return v[1:end], [p.strip('"') for p in shlex.split(rest, posix=False)] if rest else []
    words = v.split(" ")
    for n in range(len(words), 1, -1):
        head = _expand(" ".join(words[:n]))
        if os.path.isfile(head):
            rest = " ".join(words[n:]).strip()
            return head, [p.strip('"') for p in shlex.split(rest, posix=False)] if rest else []
    parts = shlex.split(v, posix=False) if v else []
    return (parts[0].strip('"'), [p.strip('"') for p in parts[1:]]) if parts else ("", [])


def resolve(value: str) -> Target:
    raw = str(value or "").strip()
    if not raw:
        return Target("missing", note="실행할 프로그램이 비어 있어요")
    whole = _expand(raw)
    if os.path.isdir(whole):
        inside = _program_in(whole) if _is_program_folder(whole) else ""
        if inside:
            return Target("run", inside, note=f"폴더 안의 프로그램: {Path(inside).name}")
        return Target("open", whole)
    if os.path.isfile(whole):
        return Target("run", whole) if whole.lower().endswith(RUNNABLE) else Target("open", whole)
    if LINK.match(raw) and not os.path.exists(raw):
        return Target("open", raw)
    program, args = _split(os.path.expandvars(raw))
    path = _expand(program)
    if os.path.isfile(path):
        return Target("run", path, args) if path.lower().endswith(RUNNABLE) else Target("open", path)
    if not any(sep in program for sep in "\\/"):
        on_path = shutil.which(program)
        if on_path:
            return Target("run", on_path, args)
        known = _app_path(program)
        if known:
            return Target("run", known, args, note="Windows에 등록된 프로그램")
        # a name, perhaps of several words ("arduino ide"): the whole value first, then its first word
        for name in ([raw] if not args else []) + [program]:
            hit = find_by_name(name)
            if hit:
                kind = "run" if hit.lower().endswith(RUNNABLE) else "open"
                return Target(kind, hit, args if kind == "run" else [], note=f"이 PC에서 찾음: {Path(hit).name}")
    return Target("missing", raw, note=f"이 PC에서 '{program or raw}'을(를) 찾지 못했어요. 프로그램의 정확한 이름이나 "
                                       f"전체 경로(예: \"C:\\Program Files\\…\\이름.exe\")를 적어 주세요.")


def _is_program_folder(folder: str) -> bool:
    low = os.path.normcase(os.path.abspath(folder))
    bases = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        bases.append(str(Path(local) / "Programs"))
    return any(b and low.startswith(os.path.normcase(os.path.abspath(b)) + os.sep) for b in bases)



