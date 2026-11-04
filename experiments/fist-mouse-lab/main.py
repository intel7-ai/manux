"""Standalone fist mouse experiment. Starts in practice mode every time."""
import argparse
import ctypes
import time
from src.instance import InstanceLock
from src.worker import Worker,RUNTIME


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=float,default=0)
    args=parser.parse_args()
    try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError,OSError):pass
    RUNTIME.mkdir(exist_ok=True)
    instance=InstanceLock(RUNTIME)
    if not instance.acquire():return 0
    (RUNTIME/'stop.request').unlink(missing_ok=True)
    worker=Worker()
    try:
        from src.ui import Window
        window=Window(worker);worker.start()
        if args.seconds:window.root.after(round(args.seconds*1000),window.close)
        window.run()
    finally:
        worker.close();worker.thread.join(timeout=5);instance.close()
    return 1 if worker.snapshot()[2].get('error') else 0


if __name__=='__main__':raise SystemExit(main())
