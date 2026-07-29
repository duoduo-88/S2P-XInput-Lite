import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from diagnostic_session import (
    DiagnosticSession,
    ESP32DiagnosticReader,
    diagnostic_firmware_needs_update,
    read_controller_status,
)
from esp32_bridge import ESP32Bridge


class DiagnosticSessionTests(unittest.TestCase):
    def test_reader_stop_interrupts_an_in_progress_response_wait(self):
        class SilentPort:
            def __init__(self):
                self.reads = 0

            def readline(self):
                self.reads += 1
                return b""

        reader = ESP32DiagnosticReader()
        port = SilentPort()
        reader._stop_event.set()

        with self.assertRaises(InterruptedError):
            reader._read_response(port, "capabilities", timeout=10.0)

        self.assertEqual(port.reads, 0)

    def test_reader_stop_joins_and_releases_its_worker_generation(self):
        reader = ESP32DiagnosticReader()
        reader._run = lambda: reader._stop_event.wait(10.0)

        self.assertTrue(reader.start())
        self.assertTrue(reader.active)
        self.assertTrue(reader.stop(timeout=0.5))
        self.assertFalse(reader.active)
        self.assertIsNone(reader._thread)

    def test_timed_session_collects_input_and_rumble_peaks(self):
        session = DiagnosticSession(1.0)
        session.start(now=10.0, wall_time=1_700_000_000.0)
        session.add_sample(
            {
                "timestamp_ns": 1_000_000_000,
                "source_rate_hz": 125.0,
                "report_interval_ms": 8.0,
            },
            {
                "state": "connected",
                "mode": "esp32",
                "sensor_mode": "9-axis",
                "rumble": {
                    "input": [80, 20],
                    "output": [300, 100],
                },
            },
            {"latency_status": {"ble_input_reports": 100}},
            now=10.25,
        )
        session.add_sample(
            {
                "timestamp_ns": 1_008_000_000,
                "source_rate_hz": 125.0,
                "report_interval_ms": 8.0,
            },
            {
                "state": "connected",
                "mode": "esp32",
                "sensor_mode": "9-axis",
                "rumble": {
                    "input": [40, 90],
                    "output": [200, 500],
                },
            },
            {"latency_status": {"ble_input_reports": 133}},
            now=10.50,
        )
        session.stop("stopped_by_user", now=10.5, wall_time=1_700_000_001.0)

        summary = session.summary()
        self.assertEqual(summary["mode"], "esp32")
        self.assertEqual(summary["rumble_peak_input"], [80, 90])
        self.assertEqual(summary["rumble_peak_output"], [300, 500])
        self.assertEqual(summary["input_interval_ms_p95"], 8.0)
        self.assertEqual(summary["input_interval_ms_p99"], 8.0)
        self.assertAlmostEqual(
            summary["ble_raw_report_rate_hz_avg"], 132.0
        )
        self.assertEqual(summary["verdict"], "OK")

    def test_firmware_failures_become_ai_visible_warnings(self):
        session = DiagnosticSession(60)
        session.start(now=1.0, wall_time=1_700_000_000.0)
        session.add_sample(
            {},
            {
                "state": "connected",
                "mode": "esp32",
                "rumble": {"send_failures": 2},
            },
            {
                "latency_status": {
                    "notify_queue_drops": 3,
                    "source_gap_events": 1,
                }
            },
            now=1.25,
        )
        session.stop(now=1.5, wall_time=1_700_000_001.0)
        log = session.format_log()

        self.assertEqual(session.summary()["verdict"], "WARN")
        self.assertIn("FIRMWARE_NOTIFY_QUEUE_DROPS", log)
        self.assertIn("INPUT_SOURCE_GAPS", log)
        self.assertIn("RUMBLE_SEND_FAILURES", log)
        self.assertIn("[SAMPLES_JSONL]", log)
        self.assertIn("[AI_ANALYSIS_HINT]", log)

    def test_occasional_source_gaps_are_notice_not_warning(self):
        session = DiagnosticSession(60)
        session.start(now=1.0, wall_time=1_700_000_000.0)
        session.add_sample(
            {},
            {},
            {
                "latency_status": {
                    "ble_input_reports": 7695,
                    "source_gap_events": 53,
                    "source_gap_max_ms": 18,
                }
            },
            now=1.25,
        )

        summary = session.summary()
        self.assertNotIn("INPUT_SOURCE_GAPS", summary["warnings"])
        self.assertIn(
            "OCCASIONAL_INPUT_SOURCE_GAPS", summary["notices"]
        )
        self.assertAlmostEqual(
            summary["source_gap_ratio"], 53 / 7695
        )

    def test_source_gap_ratio_over_one_percent_is_warning(self):
        session = DiagnosticSession(60)
        session.start(now=1.0, wall_time=1_700_000_000.0)
        session.add_sample(
            {},
            {},
            {
                "latency_status": {
                    "ble_input_reports": 1000,
                    "source_gap_events": 15,
                }
            },
            now=1.25,
        )

        self.assertIn(
            "INPUT_SOURCE_GAPS", session.summary()["warnings"]
        )

    def test_basic_windows_modes_do_not_require_esp32_sensor_data(self):
        for mode in ("wired", "bluetooth"):
            with self.subTest(mode=mode):
                session = DiagnosticSession(60)
                session.start(now=1.0, wall_time=1_700_000_000.0)
                session.add_sample(
                    {},
                    {"state": "connected", "mode": mode},
                    {},
                    now=1.25,
                )

                self.assertNotIn(
                    "SENSOR_MODE_UNAVAILABLE",
                    session.summary()["warnings"],
                )

    def test_selected_diagnostic_device_is_ai_visible(self):
        session = DiagnosticSession(60)
        session.start(now=1.0, wall_time=1_700_000_000.0)
        session.add_sample(
            {"device_key": "xinput:2", "source_rate_hz": 125.0},
            {
                "state": "connected",
                "mode": "xinput",
                "diagnostic_target": {
                    "device_key": "xinput:2",
                    "device_kind": "xinput",
                    "device_name": "Selected Controller [XInput 3]",
                },
            },
            {},
            now=1.25,
        )

        summary = session.summary()
        log = session.format_log()
        self.assertEqual(summary["device_key"], "xinput:2")
        self.assertEqual(
            summary["device_name"],
            "Selected Controller [XInput 3]",
        )
        self.assertIn("device_key=xinput:2", log)
        self.assertIn(
            "device_name=Selected Controller [XInput 3]",
            log,
        )

    def test_expired_controller_status_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controller_status.json"
            path.write_text(
                '{"state":"connected","updated_at":1}',
                encoding="utf-8",
            )
            self.assertEqual(read_controller_status(path), {})

    def test_old_esp32_firmware_requires_diagnostic_update_notice(self):
        self.assertTrue(diagnostic_firmware_needs_update(
            {"state": "unavailable", "error": "ESP32 did not answer capabilities"},
            {"state": "connected", "mode": "esp32"},
            1.5,
        ))

    def test_current_s2p_firmware_supports_diagnostics(self):
        self.assertFalse(diagnostic_firmware_needs_update(
            {
                "capabilities": {
                    "product": "S2P-FW",
                    "protocol": "s2p_bridge",
                    "protocol_version": "1.0.0",
                    "features": {"diagnostics": 1},
                }
            },
            {"state": "connected", "mode": "esp32"},
            3.5,
        ))

    def test_completed_session_elapsed_time_is_frozen(self):
        session = DiagnosticSession(60)
        session.start(now=10.0, wall_time=1_700_000_000.0)
        session.stop("completed", now=70.0, wall_time=1_700_000_060.0)

        self.assertEqual(session.elapsed(now=999.0), 60.0)
        self.assertEqual(session.remaining(now=999.0), 0.0)


class DiagnosticFirmwareContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main_source = (
            ROOT
            / "esp32s3"
            / "source"
            / "esp32s3_usb_bridge_bluedroid"
            / "main"
            / "main.c"
        ).read_text(encoding="utf-8")

    def test_standalone_rumble_diagnostics_expose_raw_and_converted_values(self):
        source = self.main_source
        self.assertIn('"rumble status"', source)
        self.assertIn('"rumble reset"', source)
        self.assertIn('\\"input\\":[%u,%u]', source)
        self.assertIn('\\"frequency\\":[%u,%u]', source)
        self.assertIn('\\"peak_output\\":[%u,%u]', source)
        self.assertIn("s_standalone_rumble_received++", source)
        self.assertIn("s_standalone_rumble_sent++", source)

    def test_bridge_routes_firmware_diagnostics_without_mixing_input(self):
        bridge = ESP32Bridge("COM_TEST")
        bridge._handle_text(
            b'{"cmd":"rumble_status","ok":1,'
            b'"input":[12,34],"output":[56,78]}'
        )
        snapshot = bridge.get_firmware_diagnostics()
        self.assertEqual(
            snapshot["rumble_status"]["input"], [12, 34]
        )
        self.assertEqual(
            snapshot["rumble_status"]["output"], [56, 78]
        )

    def test_bridge_routes_link_quality_diagnostics(self):
        bridge = ESP32Bridge("COM_TEST")
        bridge._handle_text(
            b'{"cmd":"link_status","ok":1,"bridge_mac":"AA:BB:CC:DD:EE:FF",'
            b'"links":[[0,"11:22:33:44:55:66",1,7.5,-58,25]]}'
        )
        snapshot = bridge.get_firmware_diagnostics()
        self.assertEqual(
            snapshot["link_status"]["bridge_mac"], "AA:BB:CC:DD:EE:FF"
        )
        self.assertEqual(snapshot["link_status"]["links"][0][4], -58)

    def test_firmware_link_status_reports_mac_interval_and_rssi(self):
        source = self.main_source
        self.assertIn('"link status"', source)
        self.assertIn('"cmd\\\":\\\"link_status', source)
        self.assertIn("ESP_GAP_BLE_READ_RSSI_COMPLETE_EVT", source)
        self.assertIn("esp_ble_gap_read_rssi", source)


if __name__ == "__main__":
    unittest.main()
