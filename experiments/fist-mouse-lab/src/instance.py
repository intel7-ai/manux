"""Prevent two Lab windows from competing for the same camera."""
import os


class InstanceLock:
    def __init__(self, folder):
        self.folder = folder
        self.handle = None

    def acquire(self):
        if os.name != "nt":
            return True
        import msvcrt
        self.handle = (self.folder/"instance.lock").open("a+b")
        if self.handle.seek(0,2) == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
            return True
        except OSError:
            self.handle.close()
            self.handle = None
            (self.folder/"show.request").write_text("show",encoding="utf8")
            return False

    def close(self):
        if self.handle is not None:
            import msvcrt
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(),msvcrt.LK_UNLCK,1)
            self.handle.close()
            self.handle = None
