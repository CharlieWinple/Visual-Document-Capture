import ctypes
import pytest
from vdc.automation.windows import VisibleWindowWheel, WindowsWheel, InputInterrupted, wheel_message
from vdc.capture.region import Region


class FakeAPI:
    def __init__(self):
        self.alive, self.visible, self.iconic, self.obstructed = True, True, False, False
        self.rect = (-2500, 0, -1000, 1000)
        self.hotkey = False
        self.registered = False
        self.sent = []

    def WindowFromPoint(self, point): return 101
    def GetAncestor(self, hwnd, mode): return 200 if self.obstructed else 100
    def GetWindowThreadProcessId(self, hwnd, pid):
        pid._obj.value = 55
        return 44
    def GetWindowRect(self, hwnd, rect):
        rect._obj.left, rect._obj.top, rect._obj.right, rect._obj.bottom = self.rect
        return True
    def IsWindow(self, hwnd): return self.alive
    def IsWindowVisible(self, hwnd): return self.visible
    def IsIconic(self, hwnd): return self.iconic
    def RegisterHotKey(self, hwnd, identifier, mods, key):
        assert (hwnd, mods, key) == (None, 0x4006, 0x77)
        self.registered = True
        return True
    def UnregisterHotKey(self, hwnd, identifier):
        self.registered = False
        return True
    def PeekMessageW(self, msg, hwnd, low, high, remove):
        if self.hotkey:
            self.hotkey = False
            msg._obj.wParam = VisibleWindowWheel.HOTKEY_ID
            return True
        return False
    def SendMessageTimeoutW(self, hwnd, msg, wp, lp, flags, timeout, result):
        self.sent.append((hwnd, msg, wp, lp, flags, timeout))
        return True
    # Focus, cursor and global key APIs intentionally absent: background
    # construction/check/scroll/rearm must not use them at all.


@pytest.fixture
def driver(monkeypatch):
    api = FakeAPI()
    def initialize(self, region):
        self.api, self.region, self.point = api, region, (-2000, 300)
        self.target, self.rect = 100, api.rect
    monkeypatch.setattr(WindowsWheel, "initialize", initialize)
    value = VisibleWindowWheel(Region(-2400, 100, 800, 500))
    yield value, api
    value.close()
    assert not api.registered


def test_directed_message_preserves_signed_coordinates_and_no_modifiers(driver):
    control, api = driver
    control.rearm()
    control.scroll(120)
    hwnd, msg, wp, lp, flags, timeout = api.sent[0]
    assert (hwnd, msg, flags, timeout) == (101, 0x020a, 0x23, 250)
    assert wp & 0xffff == 0
    assert ctypes.c_int16(wp >> 16).value == -120
    assert ctypes.c_int16(lp & 0xffff).value == -2000
    assert ctypes.c_int16(lp >> 16).value == 300


@pytest.mark.parametrize("field,value", [("alive", False), ("visible", False), ("iconic", True),
                                         ("obstructed", True), ("rect", (0, 0, 100, 100)), ("hotkey", True)])
def test_invalid_target_and_shortcut_pause_before_input(driver, field, value):
    control, api = driver
    setattr(api, field, value)
    with pytest.raises(InputInterrupted):
        control.scroll(120)
    assert not api.sent


def test_delivery_timeout_never_falls_back_to_global_input(driver):
    control, api = driver
    api.SendMessageTimeoutW = lambda *args: False
    with pytest.raises(InputInterrupted, match="未及时接收"):
        control.scroll(120)


def test_message_rejects_unrepresentable_desktop_coordinates():
    with pytest.raises(ValueError):
        wheel_message(120, (40000, 10))
