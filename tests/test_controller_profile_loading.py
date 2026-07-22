import configparser
import ctypes
import sys
import types
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

if not hasattr(ctypes, "windll"):
    class _DummyUser32:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: 0
    ctypes.windll = types.SimpleNamespace(user32=_DummyUser32())

try:
    import vgamepad  # noqa: F401
except ImportError:
    class _Buttons:
        pass
    for index, name in enumerate((
        "XUSB_GAMEPAD_DPAD_UP", "XUSB_GAMEPAD_DPAD_DOWN",
        "XUSB_GAMEPAD_DPAD_LEFT", "XUSB_GAMEPAD_DPAD_RIGHT",
        "XUSB_GAMEPAD_START", "XUSB_GAMEPAD_BACK",
        "XUSB_GAMEPAD_LEFT_THUMB", "XUSB_GAMEPAD_RIGHT_THUMB",
        "XUSB_GAMEPAD_LEFT_SHOULDER", "XUSB_GAMEPAD_RIGHT_SHOULDER",
        "XUSB_GAMEPAD_GUIDE", "XUSB_GAMEPAD_A", "XUSB_GAMEPAD_B",
        "XUSB_GAMEPAD_X", "XUSB_GAMEPAD_Y",
    ), start=1):
        setattr(_Buttons, name, index)
    module = types.ModuleType("vgamepad")
    module.XUSB_BUTTON = _Buttons
    module.VX360Gamepad = lambda: None
    sys.modules["vgamepad"] = module

try:
    import imufusion  # noqa: F401
except ImportError:
    module = types.ModuleType("imufusion")
    class _Ahrs:
        def __init__(self):
            self.settings = None
    module.Ahrs = _Ahrs
    module.Settings = lambda *args: args
    module.CONVENTION_NWU = 0
    sys.modules["imufusion"] = module

from settings_schema import read_section_settings
from xinput_controller import XInputController


class ControllerProfileLoadingTests(unittest.TestCase):
    def test_all_profiles_build_runtime_snapshot(self):
        calibration = {
            "left": ((2048.0, 2048.0), (4095.0, 4095.0), (0.0, 0.0)),
            "right": ((2048.0, 2048.0), (4095.0, 4095.0), (0.0, 0.0)),
        }
        for path in sorted((SRC_DIR / "profiles").glob("*.ini")):
            with self.subTest(profile=path.name):
                config = configparser.ConfigParser()
                self.assertTrue(config.read(path, encoding="utf-8"))
                controller = XInputController(
                    config,
                    calibration,
                    activate_runtime=False,
                )
                left = read_section_settings(config, "stick_curve_left")
                gyro = read_section_settings(config, "gyro_mapping")
                rumble = read_section_settings(config, "rumble")
                self.assertEqual(controller.left_deadzone, left["deadzone"])
                self.assertEqual(
                    controller.left_outer_deadzone,
                    left["outer_deadzone"],
                )
                self.assertEqual(
                    controller.gyro_button_freeze_ms,
                    gyro["button_freeze_ms"],
                )
                self.assertEqual(controller.gyro_target, gyro["target"])
                self.assertEqual(
                    controller.max_amplitude,
                    rumble["max_amplitude"],
                )


if __name__ == "__main__":
    unittest.main()
