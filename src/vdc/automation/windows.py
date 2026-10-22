"""Foreground-only Windows wheel input with focus/geometry/escape guards."""
import ctypes
from ctypes import wintypes as w
import sys


class InputInterrupted(RuntimeError):
    pass


class WindowsWheel:
    def __init__(self, region):
        if sys.platform != "win32":
            raise RuntimeError("自动滚动目前仅支持 Windows")
        self.region = region
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        for name, args, result in [
            ("WindowFromPoint", [w.POINT], w.HWND),
            ("GetAncestor", [w.HWND, w.UINT], w.HWND),
            ("GetForegroundWindow", [], w.HWND),
            ("GetWindowRect", [w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            ("IsWindow", [w.HWND], w.BOOL),
            ("IsIconic", [w.HWND], w.BOOL),
            ("GetCursorPos", [ctypes.POINTER(w.POINT)], w.BOOL),
            ("SetCursorPos", [ctypes.c_int, ctypes.c_int], w.BOOL),
            ("GetAsyncKeyState", [ctypes.c_int], ctypes.c_short),
            ("SendInput", [w.UINT, ctypes.c_void_p, ctypes.c_int], w.UINT),
        ]:
            fn = getattr(self.api, name)
            fn.argtypes, fn.restype = args, result
        r = region
        self.point = (r.left+r.width//2, r.top+r.height//2)
        self.target = self.root(*self.point)
        self.rect = self.window_rect()
        self.check(cursor=False)
        if not self.api.SetCursorPos(*self.point):
            raise InputInterrupted("无法将鼠标定位到阅读区")

    def root(self, x, y):
        return self.api.GetAncestor(self.api.WindowFromPoint(w.POINT(x, y)), 2)

    def window_rect(self):
        rect = w.RECT()
        if not self.api.GetWindowRect(self.target, ctypes.byref(rect)):
            raise InputInterrupted("阅读器窗口已关闭")
        return rect.left, rect.top, rect.right, rect.bottom

    def check(self, cursor=True):
        a, r = self.api, self.region
        if a.GetAsyncKeyState(27) & 0x8000:
            raise InputInterrupted("已按 Esc 暂停自动采集")
        if any(a.GetAsyncKeyState(key) & 0x8000 for key in (1, 2, 4, 16, 17, 18)):
            raise InputInterrupted("检测到鼠标按键或修饰键，已暂停自动采集")
        if not a.IsWindow(self.target) or a.IsIconic(self.target) or a.GetForegroundWindow() != self.target:
            raise InputInterrupted("阅读器不再是前台窗口，已暂停自动采集")
        if self.window_rect() != self.rect:
            raise InputInterrupted("阅读器位置或大小改变，请重新框选")
        points = [self.point, (r.left+2, r.top+2), (r.left+r.width-3, r.top+2),
                  (r.left+2, r.top+r.height-3), (r.left+r.width-3, r.top+r.height-3)]
        if any(self.root(x, y) != self.target for x, y in points):
            raise InputInterrupted("阅读区被其他窗口遮挡，已暂停自动采集")
        if cursor:
            point = w.POINT()
            if not a.GetCursorPos(ctypes.byref(point)) or not (
                r.left <= point.x < r.left+r.width and r.top <= point.y < r.top+r.height
            ):
                raise InputInterrupted("鼠标已移出阅读区，已暂停自动采集")

    def scroll(self, units):
        self.check()
        # INPUT's union must retain the native mouse-member size/alignment.
        class Mouse(ctypes.Structure):
            _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
                        ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
        class Payload(ctypes.Union):
            _fields_ = [("mi", Mouse)]
        class Input(ctypes.Structure):
            _fields_ = [("type", w.DWORD), ("payload", Payload)]
        event = Input(0, Payload(mi=Mouse(0, 0, (-int(units)) & 0xffffffff, 0x0800, 0, 0)))
        if self.api.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
            raise InputInterrupted("系统未接受滚轮输入，请检查阅读器权限")

    def rearm(self):
        self.check(cursor=False)
        if not self.api.SetCursorPos(*self.point):
            raise InputInterrupted("无法将鼠标定位到阅读区")
