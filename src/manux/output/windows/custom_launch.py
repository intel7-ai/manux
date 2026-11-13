"""Original a700452 launch implementation at the output boundary."""
import os, subprocess
from pathlib import Path
from manux.platform.custom_app_lookup import Target
def start(target: Target) -> None:
    """Start it. Raises OSError when Windows refuses."""
    if target.kind == "missing":
        raise FileNotFoundError(target.note)
    if target.kind == "open":
        if os.name == "nt":
            os.startfile(target.path)  # noqa: S606 - a folder, a document, a shortcut or a link: like a double click
        else:
            import webbrowser
            webbrowser.open(target.path)
        return
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if not target.path.lower().endswith((".bat", ".cmd")) else 0
    subprocess.Popen([target.path] + list(target.args), creationflags=flags, close_fds=True,
                     cwd=str(Path(target.path).parent))
