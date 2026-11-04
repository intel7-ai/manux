"""Small Windows output backend; never generates a separate synthetic double click."""
import ctypes
from ctypes import wintypes


class MouseInput(ctypes.Structure):
    _fields_ = [('dx',wintypes.LONG),('dy',wintypes.LONG),('mouseData',wintypes.DWORD),
                ('dwFlags',wintypes.DWORD),('time',wintypes.DWORD),('dwExtraInfo',ctypes.c_size_t)]


class InputUnion(ctypes.Union):
    _fields_ = [('mi',MouseInput)]


class Input(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type',wintypes.DWORD),('u',InputUnion)]


class NativeMouse:
    def __init__(self):
        self.api = ctypes.WinDLL('user32',use_last_error=True)
        self.api.SendInput.argtypes = [wintypes.UINT,ctypes.POINTER(Input),ctypes.c_int]
        self.api.SendInput.restype = wintypes.UINT
        self.api.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.api.SetCursorPos.argtypes = [ctypes.c_int,ctypes.c_int]
        self.double_time = self.api.GetDoubleClickTime()/1000

    def cursor(self):
        p = wintypes.POINT()
        if not self.api.GetCursorPos(ctypes.byref(p)): raise ctypes.WinError(ctypes.get_last_error())
        return p.x,p.y

    def bounds(self):
        return tuple(self.api.GetSystemMetrics(i) for i in (76,77,78,79))

    def escape_pressed(self):
        return bool(self.api.GetAsyncKeyState(0x1B)&0x8000)

    def move(self,position):
        if not self.api.SetCursorPos(*map(int,position)): raise ctypes.WinError(ctypes.get_last_error())

    def _send(self,entries):
        packets = (Input*len(entries))(*[Input(type=0,u=InputUnion(mi=MouseInput(0,0,data,flag,0,0)))
                                       for flag,data in entries])
        sent = self.api.SendInput(len(packets),packets,ctypes.sizeof(Input))
        if sent != len(packets):
            # Best effort release if Windows accepted only a button-down packet.
            releases=(Input*2)(Input(type=0,u=InputUnion(mi=MouseInput(0,0,0,4,0,0))),
                               Input(type=0,u=InputUnion(mi=MouseInput(0,0,0,16,0,0))))
            self.api.SendInput(2,releases,ctypes.sizeof(Input))
            raise OSError('Windows가 마우스 입력을 받지 못했습니다. 출력이 중지됩니다.')

    def click(self,button):
        down,up = (2,4) if button=='left' else (8,16)
        self._send([(down,0),(up,0)])

    def press_left(self):self._send([(2,0)])

    def release_left(self):self._send([(4,0)])

    def wheel(self,amount):
        self._send([(0x0800,amount & 0xffffffff)])

    def apply(self,result):
        if self.cursor()!=tuple(result['cursor']): self.move(result['cursor'])
        for button in result['clicks']: self.click(button)
        if result['wheel']: self.wheel(result['wheel'])
