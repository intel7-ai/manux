"""Windows output sink: the only place that sends real input (docs/contracts.md section 8).

`WindowsOutputSink(Win32Backend())` in the app; `WindowsOutputSink(FakeBackend(), threaded=False)` in tests."""
from manux.output.windows.backend import FakeBackend, InputBackend
from manux.output.windows.config import WindowsOutputConfig
from manux.output.windows.sink import WindowsOutputSink
from manux.output.windows.win32 import Win32Backend

__all__ = ["FakeBackend", "InputBackend", "Win32Backend", "WindowsOutputConfig", "WindowsOutputSink"]
