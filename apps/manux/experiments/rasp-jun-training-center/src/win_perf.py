"""
Keep Hand-Mouse responsive when its window is NOT in the foreground (Windows).

Windows 11 throttles background processes: power throttling / EcoQoS lowers CPU speed,
timer-resolution requests of occluded processes are ignored, and the foreground priority
boost goes to the app you switched to. MediaPipe inference then slows down and the cursor
lags as soon as another program is on screen. This opts the process out of that.
"""

from __future__ import annotations

import sys
from typing import List

PROCESS_POWER_THROTTLING = 4  # PROCESS_INFORMATION_CLASS.ProcessPowerThrottling
POWER_THROTTLING_CURRENT_VERSION = 1
POWER_THROTTLING_EXECUTION_SPEED = 0x1
POWER_THROTTLING_IGNORE_TIMER_RESOLUTION = 0x4
ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000


def keep_responsive_in_background() -> List[str]:
    """Apply all tweaks; returns a list of what succeeded (empty off Windows)."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    done: List[str] = []
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetProcessInformation.restype = wintypes.BOOL
    kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetPriorityClass.restype = wintypes.BOOL
    proc = kernel32.GetCurrentProcess()

    class PowerThrottlingState(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]

    # ControlMask = what we manage, StateMask = 0 -> throttling OFF for those
    state = PowerThrottlingState(
        POWER_THROTTLING_CURRENT_VERSION,
        POWER_THROTTLING_EXECUTION_SPEED | POWER_THROTTLING_IGNORE_TIMER_RESOLUTION,
        0,
    )
    try:
        if kernel32.SetProcessInformation(proc, PROCESS_POWER_THROTTLING, ctypes.byref(state), ctypes.sizeof(state)):
            done.append("power-throttling off")
    except (AttributeError, OSError, ctypes.ArgumentError):
        pass
    try:
        if kernel32.SetPriorityClass(proc, ABOVE_NORMAL_PRIORITY_CLASS):
            done.append("priority above-normal")
    except (OSError, ctypes.ArgumentError):
        pass
    try:
        if ctypes.windll.winmm.timeBeginPeriod(1) == 0:
            done.append("1 ms timer")
    except OSError:
        pass
    return done
