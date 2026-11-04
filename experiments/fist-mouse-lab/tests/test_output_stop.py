"""Check a stop during a frame, without opening a camera or sending OS input."""
import time
import pytest
import src.worker as module
from pathlib import Path


def test_status_reader_lock_does_not_abort_tracking(monkeypatch,tmp_path):
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(module,'RUNTIME',tmp_path)
    worker=module.Worker()
    worker.publish(None,{'native_output':False,'mode':'fist'})
    original=Path.replace
    def denied(*a,**kw):raise PermissionError('Windows reader still has the old status open')
    monkeypatch.setattr(Path,'replace',denied)
    assert worker.write_status() is False
    assert worker.status_write_errors==1
    assert not worker.stop_event.is_set()
    monkeypatch.setattr(Path,'replace',original)
    assert worker.write_status() is True
    assert (tmp_path/'status.json').exists()


@pytest.mark.parametrize('stale_ui',[False,True])
def test_escape_or_ui_stall_preserves_practice_cursor_and_emits_nothing(monkeypatch,tmp_path,stale_ui):
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(module,'RUNTIME',tmp_path)
    worker=module.Worker()
    worker.enabled=True
    if stale_ui:worker.ui_seen=time.perf_counter()-2
    calls=[]
    class Mouse:
        double_time=.5
        def __init__(self):self.probes=0
        def escape_pressed(self):
            self.probes+=1
            return self.probes>=2 and not stale_ui
        def cursor(self):return (5000,900)
        def bounds(self):return (0,0,6000,1200)
        def apply(self,result):calls.append(result)
    class Model:
        def __init__(self,*a):pass
        def close(self):pass
    class Camera:
        error=''
        def __init__(self,*a):pass
        def read(self):worker.stop_event.set();return None
        def close(self):pass
    monkeypatch.setattr(module,'NativeMouse',Mouse)
    monkeypatch.setattr(module,'Landmarker',Model)
    monkeypatch.setattr(module,'LatestCamera',Camera)
    worker.run()
    status=worker.snapshot()[2]
    assert not calls
    assert not status['native_output']
    assert status['cursor']==(500,300)
