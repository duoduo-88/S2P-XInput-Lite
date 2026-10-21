import configparser
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xinput_controller import (
    XInputController,
    _apply_linear_axis_response,
    _compile_stick_direction_settings,
    _apply_compressed_radial_deadzone,
)
from desktop_output import accumulate_wheel_detents
from mapping_runtime import MappingRuntime, MappingRuntimeManager
from config_utils import load_stick_calibration
from switch2_input import SWITCH_BUTTONS


class StickAnalogModeTests(unittest.TestCase):
    @staticmethod
    def _runtime_controller(left_mode="4WAY", right_mode="4WAY"):
        config = configparser.ConfigParser()
        if not config.read(
            ROOT / "src" / "profiles" / "System Default.ini",
            encoding="utf-8",
        ):
            raise AssertionError("System Default.ini was not readable")
        config.set("stick_direction_left", "mode", left_mode)
        config.set("stick_direction_right", "mode", right_mode)
        controller = XInputController(
            config,
            load_stick_calibration(config),
            activate_runtime=False,
        )
        report = SimpleNamespace(
            wButtons=0,
            bLeftTrigger=0,
            bRightTrigger=0,
            sThumbLX=0,
            sThumbLY=0,
            sThumbRX=0,
            sThumbRY=0,
        )
        controller.pad = SimpleNamespace(report=report, update=lambda: None)
        return controller

    @staticmethod
    def _state(left=(2048, 2048), right=(2048, 2048), report_time=0):
        return SimpleNamespace(
            buttons=0,
            left_stick=left,
            right_stick=right,
            gyroscope=(0, 0, 0),
            accelerometer=(0, 0, 0),
            magnetometer=(0, 0, 0),
            report_time=report_time,
        )

    @staticmethod
    def _neutral_capture_controller(left_mode="映射為滑鼠"):
        config = configparser.ConfigParser()
        if not config.read(
            ROOT / "src" / "profiles" / "System Default.ini",
            encoding="utf-8",
        ):
            raise AssertionError("System Default.ini was not readable")
        config.set("stick_direction_left", "mode", left_mode)
        config.set("stick_direction_right", "mode", "4WAY")
        controller = XInputController(
            config,
            load_stick_calibration(config),
            activate_runtime=False,
        )
        controller.cal = {
            "left": {
                "center": (2000, 2000),
                "max": (1000, 1000),
                "min": (1000, 1000),
            },
            "right": {
                "center": (2000, 2000),
                "max": (1000, 1000),
                "min": (1000, 1000),
            },
        }
        return controller

    def test_main_continuous_mapping_captures_first_report_neutral(self):
        controller = self._neutral_capture_controller()
        state = SimpleNamespace(
            buttons=0,
            left_stick=(2020, 1980),
            right_stick=(2100, 1900),
        )
        controller._select_mapping_layer(state)
        for _ in range(8):
            controller._advance_runtime_stick_neutral_capture(state)
        self.assertEqual(
            controller._main_mapping_neutral_raw["LEFT"], (2020.0, 1980.0)
        )
        self.assertIsNone(controller._main_mapping_neutral_raw["RIGHT"])

    def test_main_normal_stick_mode_does_not_capture_temporary_neutral(self):
        controller = self._neutral_capture_controller(left_mode="4WAY")
        state = SimpleNamespace(
            buttons=0,
            left_stick=(2020, 1980),
            right_stick=(2000, 2000),
        )
        controller._select_mapping_layer(state)
        controller._advance_runtime_stick_neutral_capture(state)
        self.assertEqual(
            controller._main_mapping_neutral_raw,
            {"LEFT": None, "RIGHT": None},
        )

    def test_layer_stick_swap_mode_does_not_capture_temporary_neutral(self):
        controller = self._neutral_capture_controller(
            left_mode="映射為右搖桿"
        )
        controller._begin_runtime_stick_neutral_capture(
            {
                "映射為滑鼠", "MOUSE_WHEEL_LINEAR",
                "XINPUT_LT_LINEAR", "XINPUT_RT_LINEAR",
            },
            layer=True,
        )
        self.assertIsNone(controller._mapping_layer_neutral_samples["LEFT"])

    def test_main_capture_waits_for_return_after_stick_exceeds_ten_percent(self):
        controller = self._neutral_capture_controller()
        held_state = SimpleNamespace(
            buttons=0,
            left_stick=(2150, 2000),
            right_stick=(2000, 2000),
        )
        controller._select_mapping_layer(held_state)
        controller._advance_runtime_stick_neutral_capture(held_state)
        self.assertIsNone(controller._main_mapping_neutral_raw["LEFT"])
        self.assertEqual(controller._main_mapping_neutral_samples["LEFT"], [])

        neutral_state = SimpleNamespace(
            buttons=0,
            left_stick=(2010, 1990),
            right_stick=(2000, 2000),
        )
        for _ in range(8):
            controller._advance_runtime_stick_neutral_capture(neutral_state)
        self.assertEqual(
            controller._main_mapping_neutral_raw["LEFT"], (2010.0, 1990.0)
        )

    def test_linear_axis_response_reaches_full_outer_calibrated_travel(self):
        curve = ((0.0, 0.0), (0.5, 0.4), (1.0, 1.0))
        self.assertEqual(
            _apply_linear_axis_response(
                0.98, 0.03, 0.02, True, True, curve, "LINEAR"
            ),
            1.0,
        )
        self.assertEqual(
            _apply_linear_axis_response(
                0.02, 0.03, 0.02, True, True, curve, "LINEAR"
            ),
            0.0,
        )

    def test_linear_modes_and_direction_survive_compilation(self):
        base = {
            "trigger_threshold": "0.60",
            "release_threshold": "0.50",
            "direction_deadzone": "5",
            "mouse_speed": "900",
            "mouse_deadzone": "0.05",
            "up": "NONE", "up_right": "NONE", "right": "NONE",
            "down_right": "NONE", "down": "NONE", "down_left": "NONE",
            "left": "NONE", "up_left": "NONE",
        }
        settings = _compile_stick_direction_settings({
            "left": {**base, "mode": "XINPUT_LT_LINEAR", "analog_direction": "LEFT"},
            "right": {**base, "mode": "MOUSE_WHEEL_LINEAR"},
        })
        self.assertEqual(settings["LEFT"]["mode"], "XINPUT_LT_LINEAR")
        self.assertEqual(settings["LEFT"]["analog_direction"], "LEFT")
        self.assertEqual(settings["RIGHT"]["mode"], "MOUSE_WHEEL_LINEAR")
        self.assertEqual(settings["RIGHT"]["mouse_deadzone"], 0.05)

    def test_main_profile_preserves_linear_modes_and_trigger_direction(self):
        config = configparser.ConfigParser()
        self.assertTrue(config.read(
            ROOT / "src" / "profiles" / "System Default.ini",
            encoding="utf-8",
        ))
        config.set("stick_direction_left", "mode", "MOUSE_WHEEL_LINEAR")
        config.set("stick_direction_right", "mode", "XINPUT_RT_LINEAR")
        config.set("stick_direction_right", "analog_direction", "LEFT")

        controller = XInputController(
            config,
            load_stick_calibration(config),
            activate_runtime=False,
        )

        self.assertEqual(
            controller.stick_direction_config["LEFT"]["mode"],
            "MOUSE_WHEEL_LINEAR",
        )
        self.assertEqual(
            controller.stick_direction_config["RIGHT"]["mode"],
            "XINPUT_RT_LINEAR",
        )
        self.assertEqual(
            controller.stick_direction_config["RIGHT"]["analog_direction"],
            "LEFT",
        )
        # The selected axis uses its own calibrated negative travel. Motion
        # on the other axis cannot reduce the resulting trigger maximum.
        self.assertEqual(
            controller._linear_trigger_amount(
                (0, 4095), "right", "LEFT"
            ),
            1.0,
        )
        self.assertEqual(
            controller._linear_trigger_amount(
                (0, 4095), "right", "LEFT", (2050, 2046)
            ),
            1.0,
        )

    def test_mouse_deadzone_removes_drift_and_preserves_full_range(self):
        self.assertEqual(
            _apply_compressed_radial_deadzone(0.03, 0.03, 0.05),
            (0.0, 0.0),
        )
        x, y = _apply_compressed_radial_deadzone(1.0, 0.0, 0.05)
        self.assertAlmostEqual(x, 1.0)
        self.assertAlmostEqual(y, 0.0)
        x, y = _apply_compressed_radial_deadzone(0.10, 0.0, 0.05)
        self.assertAlmostEqual(x, (0.10 - 0.05) / 0.95)
        self.assertAlmostEqual(y, 0.0)

    def test_wheel_rate_is_time_based_and_does_not_replay_backlog(self):
        residual = [0.0, 0.0]
        steps = [accumulate_wheel_detents(residual, 0.5, 1.0, 20.0, 0.01)
                 for _ in range(10)]
        self.assertEqual(sum(item[0] for item in steps), 1)
        self.assertEqual(sum(item[1] for item in steps), 2)
        self.assertEqual(
            accumulate_wheel_detents(residual, 1.0, 1.0, 1000.0, 1.0),
            (4, 4),
        )
        self.assertLess(abs(residual[0]), 1.0)
        self.assertLess(abs(residual[1]), 1.0)

    def test_runtime_linear_trigger_reaches_255_and_consumes_stick(self):
        controller = self._runtime_controller(
            left_mode="XINPUT_LT_LINEAR"
        )
        controller.stick_direction_config["LEFT"]["analog_direction"] = "RIGHT"
        controller.update(self._state(left=(4095, 2048), report_time=0))

        self.assertEqual(controller.pad.report.bLeftTrigger, 255)
        self.assertEqual(controller.pad.report.sThumbLX, 0)
        self.assertEqual(controller.pad.report.sThumbLY, 0)

    def test_runtime_linear_wheel_emits_both_axes_and_consumes_stick(self):
        controller = self._runtime_controller(
            left_mode="MOUSE_WHEEL_LINEAR"
        )
        controller.stick_direction_config["LEFT"]["mouse_speed"] = 3000.0
        emitted = []
        controller._emit_mouse_wheel = emitted.append
        controller.update(self._state(left=(4095, 4095), report_time=0))
        controller.update(self._state(left=(4095, 4095), report_time=20))
        controller.update(self._state(left=(4095, 4095), report_time=40))

        self.assertIn("WHEEL_RIGHT", emitted)
        self.assertIn("WHEEL_UP", emitted)
        self.assertEqual(controller.pad.report.sThumbLX, 0)
        self.assertEqual(controller.pad.report.sThumbLY, 0)

    def test_runtime_stick_swap_preserves_calibrated_full_output(self):
        controller = self._runtime_controller(
            left_mode="映射為右搖桿"
        )
        controller.update(self._state(left=(4095, 2048), report_time=0))

        self.assertEqual(controller.pad.report.sThumbLX, 0)
        self.assertEqual(controller.pad.report.sThumbRX, 32767)

    def test_runtime_mouse_and_direction_modes_consume_original_stick(self):
        mouse = self._runtime_controller(left_mode="映射為滑鼠")
        moves = []
        mouse._emit_mouse_move = lambda x, y: moves.append((x, y))
        mouse.update(self._state(left=(4095, 2048), report_time=0))
        mouse.update(self._state(left=(4095, 2048), report_time=20))
        self.assertGreater(moves[-1][0], 0.0)
        self.assertEqual(mouse.pad.report.sThumbLX, 0)

        direction = self._runtime_controller(left_mode="4WAY")
        direction.stick_direction_config["LEFT"]["mappings"]["UP"] = "A"
        direction._stick_direction_mapping_enabled["LEFT"] = True
        direction.update(self._state(left=(2048, 4095), report_time=0))
        self.assertNotEqual(direction.pad.report.wButtons, 0)
        self.assertEqual(direction.pad.report.sThumbLY, 0)

    def test_mapping_layer_uses_same_linear_trigger_runtime(self):
        controller = self._runtime_controller()
        settings = _compile_stick_direction_settings({
            "left": {
                **controller.stick_direction_config["LEFT"]["mappings"],
                "mode": "XINPUT_RT_LINEAR",
                "analog_direction": "UP",
            },
            "right": {
                **controller.stick_direction_config["RIGHT"]["mappings"],
                "mode": "4WAY",
            },
        })
        layers = [{
            "id": "trigger-layer",
            "enabled": True,
            "mode": "HOLD",
            "activation_buttons": ["GL"],
        }]
        layer_runtime = MappingRuntime(
                controller._compiled_mapping,
                settings,
                {"LEFT": False, "RIGHT": False},
        )
        controller._mapping_runtime = MappingRuntimeManager(
            controller._mapping_runtime.base_runtime,
            {"trigger-layer": layer_runtime},
            layers,
            SWITCH_BUTTONS,
        )
        state = self._state(left=(2048, 4095), report_time=0)
        state.buttons = SWITCH_BUTTONS["GL"]
        controller.update(state)

        self.assertEqual(controller.pad.report.bRightTrigger, 255)
        self.assertEqual(controller.pad.report.sThumbLY, 0)

    def test_mapping_layer_switch_clears_mouse_and_wheel_residuals(self):
        controller = self._runtime_controller()
        base = controller._mapping_runtime.base_runtime
        layers = [{
            "id": "desktop",
            "enabled": True,
            "mode": "HOLD",
            "activation_buttons": ["GL"],
        }]
        controller._mapping_runtime = MappingRuntimeManager(
            base, {"desktop": base}, layers, SWITCH_BUTTONS
        )
        controller._desktop_output.mouse_residual[:] = (0.75, -0.25)
        controller._desktop_output.wheel_residual["LEFT"][:] = (0.5, 0.5)

        controller._select_mapping_layer(
            SimpleNamespace(buttons=SWITCH_BUTTONS["GL"])
        )

        self.assertEqual(controller._desktop_output.mouse_residual, [0.0, 0.0])
        self.assertEqual(
            controller._desktop_output.wheel_residual["LEFT"], [0.0, 0.0]
        )


if __name__ == "__main__":
    unittest.main()
