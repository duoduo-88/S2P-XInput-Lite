import unittest
from unittest.mock import patch

from standalone_profile import (
    STANDALONE_PROFILE_SCHEMA,
    StandaloneTransferError,
    _recover_committed_profile,
    analyze_compatibility,
    analyze_standalone_v2_compatibility,
    compile_standalone_profile,
    set_esp32_mode,
)


def _settings():
    return {
        "sections": {
            "stick_curve_left": {"deadzone": 0.05},
            "stick_curve_right": {"deadzone": 0.06},
            "rumble": {"max_amplitude": 800},
            "audio_haptics": {"mode": "GAME"},
            "gyro_mapping": {"activation_mode": "OFF"},
        },
        "buttons": {"a": "A", "b": "B"},
        "direction_mappings": {
            "left": {"up": "NONE"},
            "right": {"up": "NONE"},
        },
    }


class StandaloneProfileTests(unittest.TestCase):
    def test_compilation_is_deterministic(self):
        first = compile_standalone_profile("General", _settings())
        second = compile_standalone_profile("General", _settings())
        self.assertEqual(first.payload, second.payload)
        self.assertEqual(first.crc32, second.crc32)
        self.assertEqual(first.document["schema"], STANDALONE_PROFILE_SCHEMA)

    def test_compilation_uses_native_json_types(self):
        settings = _settings()
        settings["sections"]["stick_curve_left"].update({
            "deadzone_compress": "true",
            "outer_deadzone_compress": "false",
            "output_shape": "4",
        })
        settings["sections"]["gyro_mapping"].update({
            "invert_x": "true",
            "player_space": "false",
            "curve_strength": "5",
        })

        document = compile_standalone_profile("Typed", settings).document

        self.assertIsInstance(
            document["stick_curve_left"]["deadzone"], float
        )
        self.assertIs(
            document["stick_curve_left"]["deadzone_compress"], True
        )
        self.assertIs(
            document["stick_curve_left"]["outer_deadzone_compress"], False
        )
        self.assertIsInstance(
            document["stick_curve_left"]["output_shape"], int
        )
        self.assertIsInstance(document["rumble"]["max_amplitude"], int)
        self.assertIs(document["gyro_mapping"]["invert_x"], True)
        self.assertIs(document["gyro_mapping"]["player_space"], False)
        self.assertIsInstance(
            document["gyro_mapping"]["curve_strength"], float
        )

    def test_windows_only_features_are_reported(self):
        settings = _settings()
        settings["sections"]["audio_haptics"]["mode"] = "MIX"
        settings["sections"]["gyro_mapping"]["activation_mode"] = "HOLD"
        settings["buttons"]["a"] = "KEYBOARD:SPACE"
        settings["direction_mappings"]["left"]["up"] = "MOUSE:WHEEL_UP"
        issues = analyze_compatibility(
            settings,
            [{"name": "Aim", "buttons": {}}],
        )
        self.assertEqual(
            {issue.feature for issue in issues},
            {"音訊震動", "陀螺儀映射", "按鍵映射", "搖桿方向映射", "映射層"},
        )
        self.assertEqual(
            sum(issue.severity == "ignored" for issue in issues),
            1,
        )
        self.assertEqual(
            sum(issue.severity == "blocking" for issue in issues),
            4,
        )

    def test_extended_standalone_fields_are_serialized(self):
        settings = _settings()
        settings["sections"]["stick_direction_left"] = {
            "mode": "XINPUT_LT_LINEAR",
            "analog_direction": "UP",
        }
        settings["sections"]["stick_direction_right"] = {"mode": "8WAY"}
        settings["sections"]["gyro_mapping"] = {
            "activation_mode": "HOLD",
            "target": "RIGHT_STICK",
        }
        settings["gyro_activation_buttons"] = ["ZL"]
        settings["calibration"] = {
            "left": {
                "center": (2000, 2100),
                "max": (1500, 1500),
                "min": (1500, 1500),
            },
            "right": {
                "center": (2050, 2060),
                "max": (1500, 1500),
                "min": (1500, 1500),
            },
        }
        compiled = compile_standalone_profile("Extended", settings)
        self.assertEqual(
            compiled.document["stick_direction_left"]["mode"],
            "XINPUT_LT_LINEAR",
        )
        self.assertEqual(
            compiled.document["gyro_activation_buttons"], ["ZL"]
        )
        self.assertEqual(
            compiled.document["calibration"]["left"]["center"],
            (2000, 2100),
        )
        self.assertFalse(compiled.blocking_issues)

    def test_v2_rejects_only_windows_output_targets(self):
        settings = _settings()
        settings["sections"]["stick_direction_left"] = {"mode": "4WAY"}
        settings["sections"]["stick_direction_right"] = {"mode": "4WAY"}
        settings["sections"]["gyro_mapping"] = {
            "activation_mode": "HOLD",
            "target": "RIGHT_STICK",
        }
        self.assertFalse(
            analyze_standalone_v2_compatibility(settings)
        )
        settings["sections"]["gyro_mapping"]["target"] = "MOUSE"
        issues = analyze_standalone_v2_compatibility(settings)
        self.assertEqual(
            [issue.feature for issue in issues], ["陀螺儀滑鼠"]
        )


    @patch("standalone_profile._send_and_expect")
    @patch("standalone_profile.serial.Serial")
    def test_lost_commit_ack_is_recovered_by_crc(
        self, serial_factory, send_and_expect
    ):
        compiled = compile_standalone_profile("General", _settings())
        retry_port = serial_factory.return_value.__enter__.return_value
        send_and_expect.return_value = {
            "cmd": "profile_status",
            "ok": 1,
            "valid": 1,
            "slot": "B",
            "length": len(compiled.payload),
            "crc32": f"{compiled.crc32:08x}",
        }

        recovered = _recover_committed_profile(
            "COM5", 2_000_000, compiled, timeout=0.2
        )

        retry_port.reset_input_buffer.assert_called_once_with()
        self.assertEqual(recovered["slot"], "B")
        self.assertEqual(recovered["runtime_applied"], 1)
        self.assertEqual(recovered["ack_recovered"], 1)

    @patch("standalone_profile._send_and_expect")
    @patch("standalone_profile.serial.Serial")
    def test_mobile_usb_hid_mode_is_sent_to_capable_firmware(
        self, serial_factory, send_and_expect
    ):
        send_and_expect.side_effect = (
            {
                "cmd": "capabilities",
                "ok": 1,
                "features": {
                    "standalone_usb_xinput": 1,
                    "standalone_usb_hid": 1,
                },
            },
            {
                "cmd": "mode",
                "ok": 1,
                "mode": "standalone_hid",
                "restart_required": 0,
            },
        )

        result = set_esp32_mode("COM5", 2_000_000, "standalone_hid")

        port = serial_factory.return_value.__enter__.return_value
        self.assertEqual(result["mode"], "standalone_hid")
        self.assertEqual(
            send_and_expect.call_args_list[1].args,
            (port, "mode standalone_hid", "mode"),
        )

    @patch("standalone_profile._send_and_expect")
    @patch("standalone_profile.serial.Serial")
    def test_mobile_usb_hid_mode_rejects_old_firmware(
        self, _serial_factory, send_and_expect
    ):
        send_and_expect.return_value = {
            "cmd": "capabilities",
            "ok": 1,
            "features": {
                "standalone_usb_xinput": 1,
                "standalone_usb_hid": 0,
            },
        }

        with self.assertRaisesRegex(
            StandaloneTransferError, "手機 USB HID"
        ):
            set_esp32_mode("COM5", 2_000_000, "standalone_hid")


if __name__ == "__main__":
    unittest.main()
