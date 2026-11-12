import unittest
from dataclasses import replace
from unittest.mock import patch

from test_usb_hid_desktop import FakeSerial, identity

from standalone_profile import (
    CompatibilityIssue,
    STANDALONE_PROFILE_SCHEMA,
    StandaloneTransferError,
    _recover_committed_profile,
    analyze_compatibility,
    analyze_standalone_v2_compatibility,
    compile_standalone_profile,
    set_esp32_mode,
    write_standalone_profile,
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
    def test_recovered_custom_deadzone_commit_checks_runtime_even_when_crc_matches(self):
        settings = _settings()
        settings["sections"]["stick_direction_left"] = {
            "mode": "4WAY", "direction_deadzone_custom": {"4WAY": [[0, 20]] * 4},
        }
        compiled = compile_standalone_profile("General", settings)
        for reported, expected in (({}, 0), ({"runtime_applied": 0}, 0), ({"runtime_applied": 1}, 1)):
            with self.subTest(reported=reported):
                status = {
                    "valid": 1, "slot": "B", "length": len(compiled.payload),
                    "crc32": f"{compiled.crc32:08x}", **reported,
                }
                port = FakeSerial(identity(), status)
                with (
                    patch("standalone_profile.serial.Serial", return_value=port),
                    patch("standalone_profile.list_ports.comports", return_value=[]),
                ):
                    recovered = _recover_committed_profile(
                        "COM5", 2_000_000, compiled, expected_identity=identity(), timeout=0.2
                    )
                self.assertEqual(recovered["runtime_applied"], expected)

    def test_explicit_commit_error_is_reported_without_reconnect(self):
        class RejectingCommitPort(FakeSerial):
            def write(self, payload):
                command = payload.decode().strip()
                if command.startswith("profile begin "):
                    response = b'{"cmd":"profile_begin","ok":1}\n'
                elif command.startswith("profile chunk "):
                    _, _, offset, chunk = command.split()
                    received = int(offset) + len(bytes.fromhex(chunk))
                    response = f'{{"cmd":"profile_chunk","ok":1,"received":{received}}}\n'.encode()
                elif command == "profile commit":
                    response = b'{"cmd":"profile_commit","ok":0,"error":"nvs_ESP_ERR_NVS_NOT_ENOUGH_SPACE"}\n'
                else:
                    return super().write(payload)
                self.commands.append(command)
                self.pending.append(response)

        port = RejectingCommitPort(identity())
        compiled = compile_standalone_profile("General", _settings())
        with (
            patch("standalone_profile.serial.Serial", return_value=port),
            patch("standalone_profile._recover_committed_profile",
                  side_effect=StandaloneTransferError("CRC mismatch")) as recover,
        ):
            with self.assertRaisesRegex(StandaloneTransferError, "ESP_ERR_NVS_NOT_ENOUGH_SPACE"):
                write_standalone_profile("COM5", 2_000_000, compiled, target_mode="standalone_hid")
        recover.assert_not_called()
        self.assertIn("profile abort", port.commands)
        self.assertNotIn("mode standalone_hid", port.commands)

    def test_custom_deadzone_layer_requires_firmware_support_before_staging(self):
        layer = {
            "id": "layer", "enabled": True, "activation_buttons": ["A"],
            "stick_left": {
                "mode": "4WAY", "direction_deadzone_custom": {"4WAY": [[0, 20]] * 4},
            },
        }
        compiled = compile_standalone_profile("General", _settings(), [layer])
        port = FakeSerial(identity())
        with patch("standalone_profile.serial.Serial", return_value=port):
            with self.assertRaisesRegex(StandaloneTransferError, "請先更新韌體"):
                write_standalone_profile("COM5", 2_000_000, compiled, target_mode="standalone_hid")
        self.assertEqual(port.commands, ["status lite", "capabilities"])

    @patch("standalone_profile.serial.Serial")
    def test_blocking_issue_prevents_serial_write(self, serial_factory):
        compiled = compile_standalone_profile("General", _settings())
        blocked = replace(
            compiled,
            issues=(
                CompatibilityIssue(
                    "blocking", "keyboard output", "Windows-only target"
                ),
            ),
        )

        with self.assertRaisesRegex(
            StandaloneTransferError, "blocking compatibility issues"
        ):
            write_standalone_profile("COM5", 2_000_000, blocked)

        serial_factory.assert_not_called()

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


    def test_v2_rejects_mapping_layer_windows_direction_targets(self):
        settings = _settings()
        layer = {
            "name": "Aim",
            "enabled": True,
            "activation_buttons": ["A"],
            "buttons": {},
            "stick_left": {
                "mode": "8WAY",
                "up": "KEYBOARD:W",
                "up_right": "A",
            },
            "stick_right": {
                "mode": "4WAY",
                "down": "MOUSE:WHEEL_DOWN",
            },
        }

        issues = analyze_standalone_v2_compatibility(settings, [layer])

        direction_issues = [
            issue for issue in issues
            if issue.feature == "映射層方向輸出"
        ]
        self.assertEqual(len(direction_issues), 2)
        self.assertTrue(
            all(issue.severity == "blocking" for issue in direction_issues)
        )
        self.assertTrue(
            any("LEFT UP" in issue.detail for issue in direction_issues)
        )
        self.assertTrue(
            any("RIGHT DOWN" in issue.detail for issue in direction_issues)
        )

    def test_enabled_layer_without_activation_buttons_does_not_consume_esp32_capacity(self):
        settings = _settings()
        layers = [
            {"id": button, "enabled": True, "activation_buttons": [button]}
            for button in ("A", "B", "X", "Y", "L", "R", "GL", "GR")
        ]
        dormant = {
            "id": "dormant", "enabled": True, "activation_buttons": [],
            "buttons": {"a": "KEYBOARD:W"},
            "stick_left": {
                "mode": "4WAY", "direction_deadzone_custom": {"4WAY": [[0, 20]] * 4},
            },
        }
        baseline = compile_standalone_profile("General", settings, layers)
        compiled = compile_standalone_profile("General", settings, [dormant, *layers])

        self.assertFalse(compiled.blocking_issues)
        self.assertEqual(compiled.payload, baseline.payload)
        self.assertFalse(compiled.uses_custom_deadzones)

    def test_layer_chord_with_unsupported_button_cannot_silently_shrink(self):
        layer = {
            "name": "Aim", "enabled": True, "activation_buttons": ["A", "SR_R"],
            "stick_left": {
                "mode": "4WAY", "direction_deadzone_custom": {"4WAY": [[0, 20]] * 4},
            },
        }
        compiled = compile_standalone_profile("General", _settings(), [layer])

        self.assertEqual(len(compiled.blocking_issues), 1)
        self.assertIn("Aim", compiled.blocking_issues[0].detail)
        self.assertIn("SR_R", compiled.blocking_issues[0].detail)

    def test_standalone_blocks_layer_chords_consumed_by_usb_mode_recovery(self):
        for buttons, blocked in (
            (["HOME", "A"], True),
            (["HOME", "X", "GL"], True),
            (["HOME"], False),
            (["HOME", "Y"], False),
            (["HOME", "A", "Y"], False),
            (["HOME", "A", "X"], False),
        ):
            with self.subTest(buttons=buttons):
                layer = {
                    "name": "Aim", "enabled": True, "activation_buttons": buttons,
                    "stick_left": {"mode": "4WAY", "up": "A"},
                }
                compiled = compile_standalone_profile("General", _settings(), [layer])
                if blocked:
                    self.assertEqual(len(compiled.blocking_issues), 1)
                    self.assertIn("Aim", compiled.blocking_issues[0].detail)
                    self.assertIn("救援組合鍵", compiled.blocking_issues[0].detail)
                else:
                    self.assertFalse(compiled.blocking_issues)

    def test_usb_recovery_chord_cannot_be_required_for_gyro_activation(self):
        for mode, match, buttons, blocked in (
            ("HOLD", "ALL", ["HOME", "A"], True),
            ("TOGGLE", "ALL", ["HOME", "X", "GL"], True),
            ("HOLD", "ANY", ["HOME", "A"], False),
            ("OFF", "ALL", ["HOME", "X"], False),
            ("HOLD", "ALL", ["HOME", "A", "Y"], False),
        ):
            with self.subTest(mode=mode, match=match, buttons=buttons):
                settings = _settings()
                settings["sections"]["gyro_mapping"] = {
                    "activation_mode": mode, "activation_match": match,
                }
                settings["gyro_activation_buttons"] = buttons
                compiled = compile_standalone_profile("General", settings)
                self.assertEqual(bool(compiled.blocking_issues), blocked)
                if blocked:
                    self.assertEqual(compiled.blocking_issues[0].feature, "陀螺儀啟用按鍵")

    def test_standalone_checks_direction_targets_only_in_effective_modes(self):
        settings = _settings()
        settings["sections"]["stick_direction_left"] = {"mode": "4WAY"}
        settings["sections"]["stick_direction_right"] = {"mode": "XINPUT_LT_LINEAR"}
        settings["direction_mappings"]["left"]["up_right"] = "KEYBOARD:W"
        settings["direction_mappings"]["right"]["up"] = "MOUSE:WHEEL_DOWN"
        layer = {
            "name": "Aim", "enabled": True, "activation_buttons": ["A"],
            "stick_left": {"up_right": "KEYBOARD:W"},
            "stick_right": {"mode": "XINPUT_RT_LINEAR", "down": "MOUSE:WHEEL_DOWN"},
        }

        compiled = compile_standalone_profile("General", settings, [layer])
        self.assertFalse(compiled.blocking_issues)

        settings["sections"]["stick_direction_left"]["mode"] = "8WAY"
        compiled = compile_standalone_profile("General", settings, [layer])
        self.assertEqual(
            [issue.feature for issue in compiled.blocking_issues],
            ["搖桿方向映射", "映射層方向輸出"],
        )

        settings["sections"]["stick_direction_left"]["mode"] = "映射為右搖桿"
        settings["direction_mappings"]["left"] = {"up": "NONE"}
        compiled = compile_standalone_profile("General", settings)
        self.assertEqual(
            [issue.feature for issue in compiled.blocking_issues],
            ["搖桿 Windows 輸出"],
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

        with patch("standalone_profile._read_verified_identity", return_value=identity()):
            recovered = _recover_committed_profile(
                "COM5", 2_000_000, compiled, expected_identity=identity(), timeout=0.2
            )

        retry_port.reset_input_buffer.assert_called_once_with()
        self.assertEqual(recovered["slot"], "B")
        self.assertEqual(recovered["runtime_applied"], 1)
        self.assertEqual(recovered["ack_recovered"], 1)

    @patch("standalone_profile._read_verified_identity", return_value=identity())
    @patch("standalone_profile._send_and_expect")
    @patch("standalone_profile.serial.Serial")
    def test_mobile_usb_hid_mode_is_sent_to_capable_firmware(
        self, serial_factory, send_and_expect, _identity
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

    @patch("standalone_profile._read_verified_identity", return_value=identity())
    @patch("standalone_profile._send_and_expect")
    @patch("standalone_profile.serial.Serial")
    def test_mobile_usb_hid_mode_rejects_old_firmware(
        self, _serial_factory, send_and_expect, _identity
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

    @patch("standalone_profile.serial.Serial")
    def test_removed_modes_are_rejected_before_opening_serial(self, serial_factory):
        compiled = compile_standalone_profile("General", _settings())
        for mode in ("standalone", "standalone_auto"):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    set_esp32_mode("COM5", 2_000_000, mode)
                with self.assertRaises(ValueError):
                    write_standalone_profile("COM5", 2_000_000, compiled,
                                             target_mode=mode)
        serial_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
