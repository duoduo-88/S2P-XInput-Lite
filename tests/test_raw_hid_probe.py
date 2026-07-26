import json
import io
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from raw_hid_probe import (
    PROBE_EXECUTABLE,
    RawHidProbeClient,
    enumerate_raw_hid_gamepads,
    normalize_probe_snapshot,
)


class FakeHid:
    @staticmethod
    def enumerate():
        return [
            {
                "path": b"gamepad-path",
                "vendor_id": 0x1234,
                "product_id": 0x5678,
                "manufacturer_string": "Maker",
                "product_string": "Pad",
                "usage_page": 0x01,
                "usage": 0x05,
                "interface_number": 2,
            },
            {
                "path": b"keyboard-path",
                "product_string": "Keyboard",
                "usage_page": 0x01,
                "usage": 0x06,
            },
            {
                "path": b"vendor-path",
                "product_string": "Vendor Interface",
                "usage_page": 0xFF00,
                "usage": 0x01,
            },
            {
                "path": b"rgb-path",
                "product_string": "MSI MYSTIC LIGHT",
                "usage_page": 0x01,
                "usage": 0,
            },
        ]


class RawHidProbeTests(unittest.TestCase):
    def test_enumeration_keeps_gamepad_collections_only(self):
        devices = enumerate_raw_hid_gamepads(FakeHid)

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].path, "gamepad-path")
        self.assertEqual(devices[0].name, "Maker Pad")
        self.assertEqual(devices[0].interface_number, 2)

    def test_snapshot_percentiles_and_bad_values_are_bounded(self):
        payload = {
            "type": "snapshot",
            "state": "running",
            "reports": "12",
            "rate_hz": "8000",
            "p99_us": -5,
            "histogram_max_us": "500",
            "histogram_counts": [1, -2, "3"],
        }

        snapshot = normalize_probe_snapshot(payload)

        self.assertEqual(snapshot["reports"], 12)
        self.assertEqual(snapshot["rate_hz"], 8000.0)
        self.assertEqual(snapshot["p99_us"], 0.0)
        self.assertEqual(snapshot["histogram_max_us"], 500)
        self.assertEqual(snapshot["histogram_counts"], (1, 0, 3))

    def test_client_returns_independent_snapshot_copies(self):
        client = RawHidProbeClient(executable=ROOT / "missing.exe")
        first = client.read_snapshot()
        first["state"] = "corrupted"

        self.assertEqual(client.read_snapshot()["state"], "idle")

    def test_late_output_from_an_old_generation_is_ignored(self):
        class FinishedProcess:
            def __init__(self, line):
                self.stdout = io.StringIO(line)

            @staticmethod
            def wait(timeout=None):
                return 0

        client = RawHidProbeClient(executable=ROOT / "missing.exe")
        current_process = FinishedProcess("")
        old_process = FinishedProcess(
            json.dumps({
                "type": "snapshot",
                "state": "complete",
                "reports": 999,
            }) + "\n"
        )
        client._generation = 2
        client._process = current_process

        client._read_stdout(old_process, generation=1)

        self.assertEqual(client.read_snapshot()["reports"], 0)
        self.assertIs(client._process, current_process)

    @unittest.skipUnless(
        sys.platform == "win32" and PROBE_EXECUTABLE.is_file(),
        "native Windows helper is not built",
    )
    def test_native_helper_models_an_8000_hz_stream(self):
        completed = subprocess.run(
            [
                str(PROBE_EXECUTABLE),
                "--self-test",
                "8000",
                "--duration-ms",
                "1000",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        snapshot = json.loads(completed.stdout.strip().splitlines()[-1])

        self.assertEqual(snapshot["state"], "complete")
        self.assertEqual(snapshot["reports"], 8001)
        self.assertEqual(snapshot["intervals"], 8000)
        self.assertAlmostEqual(snapshot["rate_hz"], 8000.0, places=1)
        self.assertAlmostEqual(snapshot["p99_us"], 125.0, places=1)

    def test_native_source_uses_overlapped_qpc_and_cancellation(self):
        source = (
            ROOT / "native" / "raw_hid_probe.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn("FILE_FLAG_OVERLAPPED", source)
        self.assertIn("QueryPerformanceCounter", source)
        self.assertIn("THREAD_PRIORITY_HIGHEST", source)
        self.assertIn("CancelIoEx", source)
        self.assertIn("HidD_SetNumInputBuffers(device, 512)", source)
        self.assertIn("histogram_counts", source)
        self.assertIn("std::atomic", source)

    def test_native_snapshot_loads_each_histogram_bucket_once(self):
        source = (
            ROOT / "native" / "raw_hid_probe.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn("HistogramAnalysis analyze_histogram", source)
        self.assertNotIn("uint32_t percentile_us(", source)
        self.assertEqual(
            source.count("measurement.histogram[index].load("),
            1,
        )


if __name__ == "__main__":
    unittest.main()
