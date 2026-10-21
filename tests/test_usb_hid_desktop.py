"""Behavioral identity/reconnect tests without USB hardware."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import esp32_detection as detection
import standalone_profile as profiles


def identity(serial="S2P-AABBCCDDEEFF", **changes):
    value = dict(cmd="status", product="S2P-FW", version="1.0.7",
                 protocol="s2p_bridge", protocol_version="1.0.0",
                 profile="s2p_usb_bridge", build="standalone_diagnostics",
                 usb_serial=serial, profile_schemas=[1], features={"standalone_profile_write": 1,
                 "standalone_profile_runtime": 1, "standalone_usb_hid": 1})
    value.update(changes)
    return value


class FakeSerial:
    def __init__(self, status, profile=None):
        self.status = status
        self.profile = profile or {}
        self.commands = []
        self.pending = []
        self.is_open = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        self.pending.clear()

    def flush(self):
        pass

    def write(self, payload):
        command = payload.decode().strip()
        self.commands.append(command)
        if command == "status lite":
            response = self.status
        elif command == "profile status":
            response = dict(cmd="profile_status", ok=1, **self.profile)
        else:
            response = dict(cmd="capabilities", ok=1, **{
                k: v for k, v in self.status.items() if k != "cmd"})
        self.pending.append((json.dumps(response) + "\n").encode())

    def readline(self):
        return self.pending.pop(0) if self.pending else b""


class DesktopUSBIdentityTests(unittest.TestCase):
    def test_json_array_is_not_a_firmware_status(self):
        port = SimpleNamespace(device="COM8", vid=0x303A, pid=0x4005,
                               product="S2P Mobile Gamepad", serial_number="S2P-AABBCCDDEEFF")
        with patch.object(detection.serial, "Serial", return_value=FakeSerial([identity()])):
            self.assertIsNone(detection.find_esp32_firmware(port_infos=[port]))

    def test_shared_pid_requires_matching_product_metadata_and_features(self):
        for product, status in (("Other Gamepad", identity()),
                                ("S2P Mobile Gamepad", identity(product="Other")),
                                ("S2P Mobile Gamepad", identity(features={})),
                                ("S2P Mobile Gamepad", identity())):
            with self.subTest(product=product, status=status):
                port = SimpleNamespace(device="COM8", vid=0x303A, pid=0x4005,
                                       product=product, serial_number="S2P-AABBCCDDEEFF")
                with patch.object(detection.serial, "Serial", return_value=FakeSerial(status)):
                    result = detection.find_esp32_firmware(port_infos=[port])
                self.assertEqual(result is not None,
                                 product == "S2P Mobile Gamepad" and status == identity())

    def test_windows_ports_without_product_metadata_accept_verified_stable_identity(self):
        for pid in (0x4001, 0x4005):
            with self.subTest(pid=pid):
                port = SimpleNamespace(device="COM8", vid=0x303A, pid=pid,
                                       product=None, serial_number="S2P-AABBCCDDEEFF")
                with patch.object(detection.serial, "Serial", return_value=FakeSerial(identity())):
                    result = detection.find_esp32_firmware(port_infos=[port])
                self.assertIsNotNone(result)
                self.assertEqual(result["usb_serial"], "S2P-AABBCCDDEEFF")

    def test_windows_missing_product_never_replaces_firmware_or_serial_verification(self):
        port = SimpleNamespace(device="COM8", vid=0x303A, pid=0x4005,
                               product=None, serial_number="S2P-AABBCCDDEEFF")
        for status in (identity(product="Other"), identity(protocol="other"),
                       identity(profile="other"), identity(build="other"),
                       identity(features={}), identity("S2P-001122334455"),
                       identity(usb_serial="")):
            with self.subTest(status=status):
                self.assertFalse(detection.usb_metadata_matches(port, status))

    def test_windows_old_bridge_without_product_metadata_remains_upgrade_detectable(self):
        port = SimpleNamespace(device="COM8", vid=0x303A, pid=0x4001,
                               product=None, serial_number="S2P-BRIDGE-DEV1")
        old_status = identity(version="1.0.6")
        old_status.pop("usb_serial")
        with patch.object(detection.serial, "Serial", return_value=FakeSerial(old_status)):
            result = detection.find_esp32_firmware(port_infos=[port])
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "1.0.6")
        self.assertFalse(detection.usb_metadata_matches(port, dict(old_status, version="1.0.7")))
        self.assertFalse(detection.usb_metadata_matches(port, dict(old_status, product="Other")))
        port.serial_number = "OTHER"
        self.assertFalse(detection.usb_metadata_matches(port, old_status))

    def test_pre_1_0_bridge_with_sdk_serial_remains_an_exact_upgrade_path(self):
        port = SimpleNamespace(device="COM8", vid=0x303A, pid=0x4001,
                               product=None, serial_number="123456")
        legacy = dict(cmd="status", **detection.LEGACY_S2P_RELEASE)
        with patch.object(detection.serial, "Serial", return_value=FakeSerial(legacy)):
            result = detection.find_esp32_firmware(port_infos=[port])
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "0.14.3")
        self.assertFalse(detection.firmware_identity_matches(legacy, legacy))
        for field, value in (("version", "0.14.2"), ("profile", "other"),
                             ("build", "other")):
            with self.subTest(field=field):
                self.assertFalse(detection.usb_metadata_matches(port, dict(legacy, **{field: value})))
        port.pid = 0x4005
        self.assertFalse(detection.usb_metadata_matches(port, legacy))
        port.pid = 0x4001
        port.product = "Unrelated Bridge"
        self.assertFalse(detection.usb_metadata_matches(port, legacy))

    def test_old_bridge_with_verified_status_remains_upgrade_detectable(self):
        port = SimpleNamespace(device="COM8", vid=0x303A, pid=0x4001,
                               product="S2P ESP32 Bridge", serial_number="S2P-BRIDGE-DEV1")
        old_status = identity(version="1.0.6")
        old_status.pop("usb_serial")
        with patch.object(detection.serial, "Serial", return_value=FakeSerial(old_status)):
            self.assertEqual(detection.find_esp32_firmware(port_infos=[port])["version"], "1.0.6")

    def test_reconnect_verifies_serial_before_reading_matching_profile(self):
        compiled = profiles.compile_standalone_profile("General", {"sections": {}})
        target = identity()
        profile = dict(valid=1, length=len(compiled.payload), crc32=f"{compiled.crc32:08x}")
        wrong = FakeSerial(identity("S2P-001122334455"), profile)
        right = FakeSerial(target, profile)
        ports = [SimpleNamespace(device="COM9", vid=0x303A, pid=0x4005,
                                 product="S2P Mobile Gamepad", serial_number=target["usb_serial"])]
        with patch.object(profiles.list_ports, "comports", return_value=ports), patch.object(
                profiles.serial, "Serial", side_effect=lambda name, *_a, **_k: wrong if name == "COM5" else right):
            recovered = profiles._recover_committed_profile(
                "COM5", 2000000, compiled, expected_identity=target, timeout=0.1)
        self.assertEqual(recovered["port"], "COM9")
        self.assertNotIn("profile status", wrong.commands)
        self.assertEqual(right.commands[:2], ["status lite", "profile status"])

    def test_malformed_status_shape_is_rejected_as_a_transfer_error(self):
        compiled = profiles.compile_standalone_profile("General", {"sections": {}})
        port = FakeSerial(["not", "a", "status"])
        with patch.object(profiles.serial, "Serial", return_value=port):
            with self.assertRaises(profiles.StandaloneTransferError):
                profiles.write_standalone_profile("COM8", 2000000, compiled)

    def test_reopened_mode_port_closes_when_identity_or_reset_fails(self):
        compiled = profiles.compile_standalone_profile("General", {"sections": {}})

        def send_response(_port, command, *_args, **_kwargs):
            if command == "capabilities":
                return dict(identity(), cmd="capabilities", ok=1)
            if command.startswith("profile chunk "):
                _, _, offset, chunk = command.split()
                return {"received": int(offset) + len(bytes.fromhex(chunk))}
            if command == "profile commit":
                raise OSError("commit ACK lost")
            return {"ok": 1}

        for failure in ("identity", "reset"):
            with self.subTest(failure=failure):
                original = FakeSerial(identity())
                reopened = FakeSerial(identity("S2P-001122334455"))
                if failure == "reset":
                    reopened.reset_input_buffer = Mock(side_effect=OSError("reset failed"))
                with patch.object(profiles.serial, "Serial", side_effect=[original, reopened]), patch.object(
                        profiles, "_send_and_expect", side_effect=send_response) as send, patch.object(
                        profiles, "_recover_committed_profile", return_value={"runtime_applied": 1, "port": "COM9"}):
                    with self.assertRaises((profiles.StandaloneTransferError, OSError)):
                        profiles.write_standalone_profile("COM8", 2000000, compiled,
                                                          target_mode="standalone_hid")
                self.assertFalse(reopened.is_open)
                self.assertFalse(any(call.args[1].startswith("mode ") for call in send.call_args_list))

    def test_wrong_identity_cannot_start_a_profile_transfer(self):
        compiled = profiles.compile_standalone_profile("General", {"sections": {}})
        port = FakeSerial(identity(product="Other"))
        with patch.object(profiles.serial, "Serial", return_value=port):
            with self.assertRaises(profiles.StandaloneTransferError):
                profiles.write_standalone_profile("COM8", 2000000, compiled)
        self.assertFalse(any(c.startswith("profile begin") for c in port.commands))


class DesktopUSBUIAndAssociationTests(unittest.TestCase):
    def test_write_menu_only_offers_android_hid_and_bridge_rescue(self):
        import ast
        from pathlib import Path
        tree = ast.parse(Path("src/gui_sections/footer.py").read_text(encoding="utf-8"))
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                        and n.name == "refresh_write_esp32_menu")
        menu = SimpleNamespace(delete=Mock(), add_command=Mock(), add_separator=Mock())
        gui = SimpleNamespace(tr=lambda s: s, write_esp32_menu=menu,
                              write_current_profile_to_esp32=Mock(), set_esp32_bridge_mode=Mock())
        scope = {"gui": gui}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "footer.py", "exec"), scope)
        scope["refresh_write_esp32_menu"]()
        entries = [call.kwargs for call in menu.add_command.call_args_list]
        self.assertEqual([e["label"] for e in entries],
                         ["寫入並啟用 Android USB HID 獨立模式", "切回 ESP32 橋接模式"])
        entries[0]["command"]()
        gui.write_current_profile_to_esp32.assert_called_once_with("standalone_hid")

    def test_migration_notice_is_deduplicated_per_device(self):
        import config_gui
        gui = object.__new__(config_gui.ConfigGUI)
        gui.tr = lambda s: s
        first = identity(mode_migration="standalone")
        with patch.object(config_gui.messagebox, "showinfo") as show:
            gui._show_esp32_mode_migration(first)
            gui._show_esp32_mode_migration(first)
            gui._show_esp32_mode_migration(identity("S2P-001122334455", mode_migration="standalone_auto"))
        self.assertEqual(show.call_count, 2)
        self.assertIn("Android USB HID", show.call_args.args[1])

    def test_raw_hid_requires_exact_serial_and_product_before_cdc_handshake(self):
        import gamepad_devices
        expected = identity()
        interface = dict(vendor_id=0x303A, product_id=0x4005,
                         product_string="S2P Mobile Gamepad", serial_number=expected["usb_serial"])
        ports = [SimpleNamespace(device="COM8", vid=0x303A, pid=0x4005,
                                 product="S2P Mobile Gamepad", serial_number=expected["usb_serial"])]
        with patch.object(detection.serial.tools.list_ports, "comports", return_value=ports), patch.object(
                detection.serial, "Serial", return_value=FakeSerial(expected)):
            self.assertEqual(gamepad_devices.find_s2p_hid_firmware(interface)["usb_serial"], expected["usb_serial"])
        for change in (dict(serial_number="S2P-001122334455"), dict(product_string="Other")):
            with patch.object(detection.serial, "Serial") as serial_factory:
                with patch.object(detection.serial.tools.list_ports, "comports", return_value=ports):
                    self.assertIsNone(gamepad_devices.find_s2p_hid_firmware(dict(interface, **change)))
                serial_factory.assert_not_called()


class DesktopDiagnosticIdentityTests(unittest.TestCase):
    def test_diagnostic_reader_rechecks_expected_device_before_any_commands(self):
        import diagnostic_session
        reader = diagnostic_session.ESP32DiagnosticReader()
        reader.expected_identity = identity()
        reader._stop_event.wait = Mock(return_value=True)
        wrong_port = FakeSerial(identity("S2P-001122334455"))
        with patch.object(diagnostic_session, "find_esp32_firmware", return_value=identity(port="COM8")), patch.object(
                diagnostic_session.serial.tools.list_ports, "comports", return_value=[]), patch.object(
                diagnostic_session.serial, "Serial", return_value=wrong_port):
            reader._run()
        self.assertEqual(reader.snapshot()["state"], "unavailable")
        self.assertNotIn("capabilities", wrong_port.commands)


class DesktopMigrationPublishingTests(unittest.TestCase):
    def test_routine_bridge_status_publishes_verified_migration_identity(self):
        from esp32_bridge import ESP32Bridge
        bridge = ESP32Bridge("COM8")
        status = identity(mode_migration="standalone_auto")
        bridge._handle_text(json.dumps(status).encode())
        self.assertEqual(bridge.get_firmware_diagnostics().get("identity"), status)

    def test_wrong_status_is_not_published_as_firmware_identity(self):
        from esp32_bridge import ESP32Bridge
        bridge = ESP32Bridge("COM8")
        bridge._handle_text(json.dumps(identity(product="Other")).encode())
        self.assertNotIn("identity", bridge.get_firmware_diagnostics())
