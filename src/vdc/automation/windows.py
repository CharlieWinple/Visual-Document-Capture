"""Guarded foreground and visible-window Windows wheel input."""
import ctypes
from ctypes import wintypes as w
import sys


class InputInterrupted(RuntimeError):
    pass


class WindowsWheel:
    def __init__(self, region):
        self.initialize(region)
        self.check(cursor=False)
        if not self.api.SetCursorPos(*self.point):
            raise InputInterrupted("无法将鼠标定位到阅读区")

    def initialize(self, region):
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
            ("IsWindowVisible", [w.HWND], w.BOOL),
            ("IsIconic", [w.HWND], w.BOOL),
            ("GetCursorPos", [ctypes.POINTER(w.POINT)], w.BOOL),
            ("SetCursorPos", [ctypes.c_int, ctypes.c_int], w.BOOL),
            ("GetAsyncKeyState", [ctypes.c_int], ctypes.c_short),
            ("SendInput", [w.UINT, ctypes.c_void_p, ctypes.c_int], w.UINT),
            ("GetWindowThreadProcessId", [w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            ("SendMessageTimeoutW", [w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT,
                                     ctypes.POINTER(ctypes.c_size_t)], ctypes.c_ssize_t),
            ("RegisterHotKey", [w.HWND, ctypes.c_int, w.UINT, w.UINT], w.BOOL),
            ("UnregisterHotKey", [w.HWND, ctypes.c_int], w.BOOL),
            ("PeekMessageW", [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT], w.BOOL),
        ]:
            fn = getattr(self.api, name)
            fn.argtypes, fn.restype = args, result
        r = region
        self.point = (r.left+r.width//2, r.top+r.height//2)
        self.target = self.root(*self.point)
        self.rect = self.window_rect()

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


def wheel_message(units, point):
    """WM_MOUSEWHEEL uses signed 16-bit screen coordinates, including negatives."""
    x, y = point
    if not all(-32768 <= value <= 32767 for value in (x, y, -units)):
        raise ValueError("滚轮消息坐标超出 Windows 支持范围")
    # No modifier flags: the user's Ctrl/Shift in another app must not zoom or
    # horizontally scroll the reader. Applications may still consult key state.
    return ((-int(units)) & 0xffff) << 16, (x & 0xffff) | ((y & 0xffff) << 16)


class VisibleWindowWheel(WindowsWheel):
    """Send only to a bound visible window. Never move/activate/click the mouse."""
    HOTKEY_ID = 0x5644

    def __init__(self, region):
        self.hotkey_registered = False
        self.initialize(region)
        self.receiver = self.api.WindowFromPoint(w.POINT(*self.point))
        self.identity = self.window_identity(self.target)
        self.receiver_identity = self.window_identity(self.receiver)
        self.check()
        self.hotkey_registered = bool(self.api.RegisterHotKey(None, self.HOTKEY_ID, 0x4006, 0x77))
        if not self.hotkey_registered:
            raise InputInterrupted("Ctrl+Shift+F8 暂停快捷键被占用，请释放该快捷键后重试")

    def window_identity(self, hwnd):
        pid = w.DWORD()
        thread = self.api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return thread, pid.value

    def check(self, cursor=True):
        a, r = self.api, self.region
        message = w.MSG()
        if a.PeekMessageW(ctypes.byref(message), None, 0x0312, 0x0312, 1):
            if message.wParam == self.HOTKEY_ID:
                raise InputInterrupted("已按 Ctrl+Shift+F8 暂停采集")
        if (not a.IsWindow(self.target) or not a.IsWindowVisible(self.target) or a.IsIconic(self.target)
                or self.window_identity(self.target) != self.identity):
            raise InputInterrupted("目标阅读器已关闭、隐藏或最小化，请恢复原窗口")
        if self.window_rect() != self.rect:
            raise InputInterrupted("阅读器位置或大小改变，请重新框选")
        if (not a.IsWindow(self.receiver) or a.GetAncestor(self.receiver, 2) != self.target
                or self.window_identity(self.receiver) != self.receiver_identity):
            raise InputInterrupted("阅读器内容窗口已更换，请结束后重新框选")
        points = [(r.left+round((r.width-5)*x)+2, r.top+round((r.height-5)*y)+2)
                  for x in (0, .25, .5, .75, 1) for y in (0, .25, .5, .75, 1)]
        if any(self.root(x, y) != self.target for x, y in points):
            raise InputInterrupted("目标阅读区被遮挡，请移开遮挡窗口后继续")

    def scroll(self, units):
        self.check()
        wp, lp = wheel_message(units, self.point)
        result = ctypes.c_size_t()
        # Bounded synchronous delivery prevents unbounded input queue buildup.
        # A successful delivery is NOT proof of scrolling; AutoEngine verifies.
        if not self.api.SendMessageTimeoutW(self.receiver, 0x020A, wp, lp, 0x23, 250, ctypes.byref(result)):
            raise InputInterrupted("阅读器未及时接收定向滚动；可能不兼容或权限不同，已暂停")

    def rearm(self):
        self.check()

    def close(self):
        if self.hotkey_registered:
            self.api.UnregisterHotKey(None, self.HOTKEY_ID)
            self.hotkey_registered = False
