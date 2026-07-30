"""Install a minimal vgamepad substitute when ViGEmBus is unavailable."""

import ctypes
from enum import IntEnum
import sys
import types


class _XUSBButton(IntEnum):
    XUSB_GAMEPAD_DPAD_UP = 0x0001
    XUSB_GAMEPAD_DPAD_DOWN = 0x0002
    XUSB_GAMEPAD_DPAD_LEFT = 0x0004
    XUSB_GAMEPAD_DPAD_RIGHT = 0x0008
    XUSB_GAMEPAD_START = 0x0010
    XUSB_GAMEPAD_BACK = 0x0020
    XUSB_GAMEPAD_LEFT_THUMB = 0x0040
    XUSB_GAMEPAD_RIGHT_THUMB = 0x0080
    XUSB_GAMEPAD_LEFT_SHOULDER = 0x0100
    XUSB_GAMEPAD_RIGHT_SHOULDER = 0x0200
    XUSB_GAMEPAD_GUIDE = 0x0400
    XUSB_GAMEPAD_A = 0x1000
    XUSB_GAMEPAD_B = 0x2000
    XUSB_GAMEPAD_X = 0x4000
    XUSB_GAMEPAD_Y = 0x8000


class _XUSBReport(ctypes.LittleEndianStructure):
    _fields_ = (
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    )


class _VX360Gamepad:
    def __init__(self):
        self.report = _XUSBReport()
        self._busp = None
        self._devicep = None
        self._notification_callbacks = []

    def register_notification(self, callback_function):
        self._notification_callbacks.append(callback_function)

    def reset(self):
        self.report = _XUSBReport()

    def update(self):
        return None

    def left_trigger(self, value):
        self.report.bLeftTrigger = value

    def right_trigger(self, value):
        self.report.bRightTrigger = value

    def left_joystick(self, x_value, y_value):
        self.report.sThumbLX = x_value
        self.report.sThumbLY = y_value

    def right_joystick(self, x_value, y_value):
        self.report.sThumbRX = x_value
        self.report.sThumbRY = y_value

    def left_joystick_float(self, x_value, y_value):
        self.left_joystick(round(x_value * 32767), round(y_value * 32767))

    def right_joystick_float(self, x_value, y_value):
        self.right_joystick(round(x_value * 32767), round(y_value * 32767))


class _VigemErrors(IntEnum):
    VIGEM_ERROR_NONE = 0


def _install_stub():
    for name in tuple(sys.modules):
        if name == "vgamepad" or name.startswith("vgamepad."):
            del sys.modules[name]

    package = types.ModuleType("vgamepad")
    package.__path__ = []
    package.XUSB_BUTTON = _XUSBButton
    package.VX360Gamepad = _VX360Gamepad

    win = types.ModuleType("vgamepad.win")
    win.__path__ = []
    commons = types.ModuleType("vgamepad.win.vigem_commons")
    commons.XUSB_REPORT = _XUSBReport
    commons.VIGEM_ERRORS = _VigemErrors
    virtual = types.ModuleType("vgamepad.win.virtual_gamepad")
    virtual.VX360Gamepad = _VX360Gamepad
    client = types.ModuleType("vgamepad.win.vigem_client")
    client.vigem_target_x360_get_user_index = lambda *_args: 1

    package.win = win
    win.vigem_commons = commons
    win.virtual_gamepad = virtual
    win.vigem_client = client
    sys.modules.update({
        "vgamepad": package,
        "vgamepad.win": win,
        "vgamepad.win.vigem_commons": commons,
        "vgamepad.win.virtual_gamepad": virtual,
        "vgamepad.win.vigem_client": client,
    })


def install_if_vigembus_unavailable():
    """Keep real vgamepad when usable; otherwise isolate tests from the driver."""
    try:
        import vgamepad  # noqa: F401
    except Exception:
        _install_stub()
