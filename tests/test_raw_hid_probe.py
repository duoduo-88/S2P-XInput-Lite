import json
import io
import mmap
import struct
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from raw_hid_probe import (
    PROBE_EXECUTABLE,
    RawHidProbeClient,
    RawHidStreamClient,
    STREAM_HEADER,
    STREAM_MAGIC,
    STREAM_SLOT,
    STREAM_VERSION,
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

    def test_manual_stop_publishes_terminal_snapshot_state(self):
        class FinishedProcess:
            @staticmethod
            def poll():
                return 0

        client = RawHidProbeClient(executable=ROOT / "missing.exe")
        client._process = FinishedProcess()
        client._snapshot["state"] = "running"

        self.assertTrue(client.stop())
        self.assertEqual(client.read_snapshot()["state"], "stopped")
        self.assertEqual(client.read_snapshot()["remaining_ms"], 0.0)

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

    def test_stream_reader_rejects_torn_slots(self):
        capacity = 1024
        mapping = mmap.mmap(
            -1, STREAM_HEADER.size + capacity * STREAM_SLOT.size
        )
        STREAM_HEADER.pack_into(
            mapping, 0,
            STREAM_MAGIC, STREAM_VERSION,
            STREAM_HEADER.size, STREAM_SLOT.size,
            capacity, 1, 0, 0x0F,
            2, 2, 10_000_000, 0,
        )
        STREAM_SLOT.pack_into(
            mapping, STREAM_HEADER.size,
            1, 10_000_000, 0.25, -0.5, 0.75, -1.0,
            0.0, 0.0, 0, 0,
        )
        STREAM_SLOT.pack_into(
            mapping, STREAM_HEADER.size + STREAM_SLOT.size,
            0, 20_000_000, 1.0, 1.0, 1.0, 1.0,
            0.0, 0.0, 0, 0,
        )
        client = RawHidStreamClient(capacity=capacity)
        client._mapping = mapping

        samples, newest, dropped = client.read_samples(0)

        self.assertEqual(newest, 2)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0][3], 1)
        self.assertAlmostEqual(samples[0][0], 1.0)
        self.assertEqual(samples[0][1], (0.25, -0.5))
        client._mapping = None
        mapping.close()

    def test_native_stream_commits_slot_before_latest_sequence(self):
        source = (
            ROOT / "native" / "raw_hid_probe.cpp"
        ).read_text(encoding="utf-8")

        slot_commit = source.index(
            "InterlockedExchange64(\n            &slot.sequence"
        )
        latest_commit = source.index(
            "InterlockedExchange64(\n            &header->latest_sequence"
        )
        self.assertLess(slot_commit, latest_commit)
        self.assertIn("HidP_GetUsageValue", source)
        self.assertIn("status != HIDP_STATUS_SUCCESS) return false", source)
        self.assertIn(
            "read_axis(parser, parser.left_x, report, false, left_x)",
            source,
        )
        self.assertIn(
            "parser.right_y, report, true, right_y",
            source,
        )
        self.assertIn("if (sample_axes == 0) continue", source)
        self.assertIn("slot.reserved = sample_axes", source)
        self.assertIn('folded_path.find(L"&IG_")', source)
        self.assertIn("bytes_read >= 9", source)
        self.assertIn(
            "normalize_xinput_hid_axis(value_at(1), false)",
            source,
        )
        self.assertIn(
            "normalize_xinput_hid_axis(value_at(3), true)",
            source,
        )
        self.assertIn("OpenFileMappingW", source)
        self.assertIn("ERROR_BROKEN_PIPE", source)
        self.assertIn("ERROR_PIPE_NOT_CONNECTED", source)


if __name__ == "__main__":
    unittest.main()
