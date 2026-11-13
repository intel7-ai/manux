"""Output callbacks supplied by desktop.py; imports never start Windows input."""

def _unavailable(*args, **kwargs):
    return False, "출력 연결이 준비되지 않았어요"

execute = _unavailable
macro_start = lambda *args, **kwargs: (False, "출력 연결이 준비되지 않았어요", [])
macro_cancel = lambda: None
macro_running = lambda: False
launch = lambda target: None
command_status = lambda: dict(running=False, message="", output="", exit_code=None)
command_cancel = lambda: None
