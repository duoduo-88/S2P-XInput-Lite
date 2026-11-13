import configparser
import ctypes
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))
_injected_modules = {}

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
    _injected_modules["vgamepad"] = module

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
    _injected_modules["imufusion"] = module

from settings_schema import read_section_settings
from switch2_input import SWITCH_BUTTONS
from xinput_controller import XInputController

for _module_name, _module in _injected_modules.items():
    if sys.modules.get(_module_name) is _module:
        del sys.modules[_module_name]


class ControllerProfileLoadingTests(unittest.TestCase):
    def test_button_spike_guard_keeps_tracking_through_zr_edges(self):
        config = configparser.ConfigParser()
        self.assertTrue(config.read(SRC_DIR / "profiles" / "FPS-COMP.ini", encoding="utf-8"))
        config["gyro_mapping"].update(
            deadzone="0", smoothing_ms="0", player_space="false",
            stick_sensitivity="1", button_freeze_ms="20",
            button_stabilization_mode="SPIKE",
        )
        calibration = dict.fromkeys(
            ("left", "right"), ((2048, 2048), (4095, 4095), (0, 0))
        )
        controller = XInputController(config, calibration, activate_runtime=False)
        controller.set_gyro_bias((0, 0, 0))
        zl, zr = SWITCH_BUTTONS["ZL"], SWITCH_BUTTONS["ZR"]
        state = types.SimpleNamespace(
            buttons=zl, gyroscope=(0, 0, -143),
            accelerometer=(0, 0, 4096), magnetometer=(0, 0, 0),
        )
        with patch("xinput_controller.time.perf_counter", return_value=100.0):
            tracking = controller._get_gyro_output(state, 0.008)[0]
            for buttons in (zl | zr, zl):
                with self.subTest(buttons=buttons):
                    state.buttons = buttons
                    state.gyroscope = (0, 0, -1000)
                    guarded = controller._get_gyro_output(state, 0.008)[0]
                    self.assertGreater(guarded, tracking)
                    self.assertLess(guarded, tracking + 0.05)
                    state.gyroscope = (0, 0, -143)
                    self.assertAlmostEqual(controller._get_gyro_output(state, 0.008)[0], tracking)
                    controller._get_gyro_output(state, 0.008)
                    controller._get_gyro_output(state, 0.008)
            state.gyroscope = (0, 0, -1000)
            self.assertAlmostEqual(controller._get_gyro_output(state, 0.008)[0], 1.12)

    def test_fps_comp_uses_speed_adaptive_smoothing(self):
        config = configparser.ConfigParser()
        self.assertTrue(config.read(SRC_DIR / "profiles" / "FPS-COMP.ini", encoding="utf-8"))
        self.assertEqual(read_section_settings(config, "gyro_mapping")["smoothing_ms"], 10)

    def test_center_gyro_deadzone_preserves_diagonal_motion(self):
        config = configparser.ConfigParser()
        self.assertTrue(config.read(SRC_DIR / "profiles" / "FPS-COMP.ini", encoding="utf-8"))
        config["gyro_mapping"].update(
            deadzone="0.50",
            adaptive_deadzone="0",
            smoothing_ms="0",
            player_space="false",
            stick_sensitivity="1.00",
        )
        calibration = dict.fromkeys(
            ("left", "right"), ((2048.0, 2048.0), (4095.0, 4095.0), (0.0, 0.0))
        )
        controller = XInputController(config, calibration, activate_runtime=False)
        controller.set_gyro_bias((0, 0, 0))
        for rates, expected in (
            ((-4, 0, -3), (0.0, 0.0)),
            ((0, 0, -8), (0.00096, 0.0)),
            ((-6, 0, -8), (0.00256, -0.00192)),
        ):
            with self.subTest(rates=rates):
                state = types.SimpleNamespace(
                    buttons=SWITCH_BUTTONS["ZL"],
                    gyroscope=rates,
                    accelerometer=(0, 0, 4096),
                    magnetometer=(0, 0, 0),
                )
                x, y, _, _ = controller._get_gyro_output(state, 0.008)
                self.assertAlmostEqual(x, expected[0])
                self.assertAlmostEqual(y, expected[1])

    def test_output_compensation_scales_after_smoothing_and_curve(self):
        config = configparser.ConfigParser()
        config.read(SRC_DIR / "profiles" / "FPS-COMP.ini", encoding="utf-8")
        gyro = config["gyro_mapping"]
        gyro.update(deadzone="0", smoothing_ms="15", player_space="false",
                    response_curve="LATE", curve_strength="100")
        calibration = dict.fromkeys(
            ("left", "right"), ((2048.0, 2048.0), (4095.0, 4095.0), (0.0, 0.0))
        )
        baseline = XInputController(config, calibration, activate_runtime=False)
        gyro.update(x_compensation="0.16", y_compensation="0.50")
        compensated = XInputController(config, calibration, activate_runtime=False)
        for controller in (baseline, compensated):
            controller.set_gyro_bias((0, 0, 0))
        for rates in ((-160, 0, -240), (-30, 0, -50), (0, 0, 0)):
            state = types.SimpleNamespace(
                buttons=SWITCH_BUTTONS["ZL"], gyroscope=rates,
                accelerometer=(0, 0, 4096), magnetometer=(0, 0, 0),
            )
            x, y, _, _ = baseline._get_gyro_output(state, 0.008)
            actual_x, actual_y, _, _ = compensated._get_gyro_output(state, 0.008)
            self.assertEqual(baseline._gyro_smoothed, compensated._gyro_smoothed)
            self.assertAlmostEqual(actual_x, x * 0.16)
            self.assertAlmostEqual(actual_y, y * 0.50)

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
