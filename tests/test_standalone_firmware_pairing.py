from pathlib import Path
import re
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_SOURCE = (
    PROJECT_ROOT
    / "esp32s3"
    / "source"
    / "esp32s3_usb_bridge_bluedroid"
    / "main"
    / "main.c"
)


class StandaloneFirmwarePairingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FIRMWARE_SOURCE.read_text(encoding="utf-8")

    def test_reconnect_mac_uses_nintendo_manufacturer_offset(self):
        parser = self._function_body("adv_get_reconnect_mac")
        self.assertIn("mfg[12 + i]", parser)
        self.assertIn("NINTENDO_COMPANY_ID", parser)
        self.assertIn("NINTENDO_VENDOR_ID", parser)
        self.assertIn("PRO_CONTROLLER2_PID", parser)

    def test_sync_pairing_sends_complete_persistent_sequence(self):
        builder = self._function_body("build_standalone_pair_command")
        subcommands = re.findall(
            r"\*subcommand_id\s*=\s*(0x[0-9A-Fa-f]+)", builder
        )
        self.assertEqual(subcommands, ["0x01", "0x04", "0x02", "0x03"])
        self.assertIn("data[2 + i] = byte", builder)
        self.assertIn("data[8 + i] = byte", builder)

    def test_pairing_ack_must_match_current_subcommand(self):
        handler = self._function_body("note_standalone_pair_ack")
        self.assertIn("payload[0] != 0x15", handler)
        self.assertIn("payload[3] != expected_subcommand", handler)

    def test_reconnect_target_controls_pairing_for_that_connection(self):
        scanner = self._function_body("gap_cb")
        connector = self._function_body("do_conn")
        self.assertIn(
            "reconnect_mac == 0 || reconnect_mac == s_own_mac_value",
            scanner,
        )
        self.assertIn(
            "s_standalone_auto_conn_pair_required =",
            scanner,
        )
        self.assertIn(
            "s_standalone_mode && standalone_pair_required",
            connector,
        )

    def test_standalone_scan_waits_for_scan_parameter_completion(self):
        app_main = self._function_body("app_main")
        standalone_intent = app_main.index("s_scan_mode = true")
        configure_scan = app_main.index("esp_ble_gap_set_scan_params")
        self.assertLess(standalone_intent, configure_scan)

        gap_callback = self._function_body("gap_cb")
        completion_case = gap_callback.index(
            "ESP_GAP_BLE_SCAN_PARAM_SET_COMPLETE_EVT"
        )
        result_case = gap_callback.index("ESP_GAP_BLE_SCAN_RESULT_EVT")
        scan_setup = gap_callback[completion_case:result_case]
        self.assertIn("s_scan_params_ready =", scan_setup)
        self.assertIn("s_resume_scan = true", scan_setup)

        task = self._function_body("cdc_task")
        self.assertIn(
            "s_resume_scan && s_scan_mode && s_scan_params_ready",
            task,
        )

    def _function_body(self, name):
        match = re.search(
            rf"^[A-Za-z_][A-Za-z0-9_ *]*\b{name}\s*\([^;]*?\)\s*\{{",
            self.source,
            flags=re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(match, f"Could not find {name}()")
        start = match.end()
        depth = 1
        for index in range(start, len(self.source)):
            if self.source[index] == "{":
                depth += 1
            elif self.source[index] == "}":
                depth -= 1
                if depth == 0:
                    return self.source[start:index]
        self.fail(f"Could not find the end of {name}()")


if __name__ == "__main__":
    unittest.main()
