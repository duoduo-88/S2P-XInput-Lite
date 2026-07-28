import json
import io
import math
import mmap
import struct
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from raw_hid_probe import (
    PROBE_EXECUTABLE,
    RawHidProbeClient,
    RawHidAnalysisClient,
    RawHidStreamClient,
    STREAM_HEADER,
    STREAM_MAGIC,
    STREAM_SLOT,
    STREAM_VERSION,
    _StickUpdateTracker,
    _enumerate_raw_hid_gamepads_isolated,
    _fixed_stream_probe_supported,
    enumerate_raw_hid_gamepads,
    _is_virtual_hid_path,
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
    def test_isolated_enumeration_contains_child_process_crash(self):
        crashed = subprocess.CompletedProcess(
            args=("python",), returncode=0xC0000374, stdout=b"", stderr=b""
        )
        with patch("raw_hid_probe.subprocess.run", return_value=crashed):
            devices = _enumerate_raw_hid_gamepads_isolated()

        self.assertEqual(devices, [])

    def test_isolated_enumeration_decodes_child_payload(self):
        payload = [{
            "key": "raw:key",
            "path": "raw-path",
            "name": "Controller",
            "vendor_id": 0x1234,
            "product_id": 0x5678,
            "usage_page": 1,
            "usage": 5,
            "interface_number": 2,
            "is_virtual": False,
        }]
        completed = subprocess.CompletedProcess(
            args=("python",),
            returncode=0,
            stdout=json.dumps(payload).encode("utf-8"),
            stderr=b"",
        )
        with patch("raw_hid_probe.subprocess.run", return_value=completed):
            devices = _enumerate_raw_hid_gamepads_isolated()

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].path, "raw-path")
        self.assertEqual(devices[0].interface_number, 2)

    def test_root_system_parent_identifies_virtual_hid(self):
        with patch(
            "raw_hid_probe._hid_parent_device_ids",
            return_value=(
                r"USB\VID_045E&PID_028E\01",
                r"ROOT\SYSTEM\0002",
            ),
        ):
            self.assertTrue(_is_virtual_hid_path("hid-path"))

    def test_usb_parent_chain_remains_physical(self):
        with patch(
            "raw_hid_probe._hid_parent_device_ids",
            return_value=(
                r"USB\VID_413D&PID_2104\PAD",
                r"USB\ROOT_HUB30\0",
                r"PCI\VEN_1022&DEV_43F7\BUS",
            ),
        ):
            self.assertFalse(_is_virtual_hid_path("hid-path"))

    def test_enumeration_keeps_gamepad_collections_only(self):
        devices = enumerate_raw_hid_gamepads(FakeHid)

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].path, "gamepad-path")
        self.assertEqual(devices[0].name, "Maker Pad")
        self.assertEqual(devices[0].interface_number, 2)

    def test_enumeration_preserves_interface_zero(self):
        class InterfaceZeroHid:
            @staticmethod
            def enumerate():
                return [{
                    "path": b"interface-zero",
                    "product_string": "Controller",
                    "usage_page": 1,
                    "usage": 5,
                    "interface_number": 0,
                }]

        devices = enumerate_raw_hid_gamepads(InterfaceZeroHid)

        self.assertEqual(devices[0].interface_number, 0)

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
        self.assertEqual(dropped, 1)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0][3], 1)
        self.assertAlmostEqual(samples[0][0], 1.0)
        self.assertEqual(samples[0][1], (0.25, -0.5))
        client._mapping = None
        mapping.close()

    def test_stream_reader_can_return_per_report_axis_mask(self):
        capacity = 1024
        mapping = mmap.mmap(
            -1, STREAM_HEADER.size + capacity * STREAM_SLOT.size
        )
        STREAM_HEADER.pack_into(
            mapping, 0,
            STREAM_MAGIC, STREAM_VERSION,
            STREAM_HEADER.size, STREAM_SLOT.size,
            capacity, 1, 0, 0x0F,
            1, 1, 10_000_000, 1,
        )
        STREAM_SLOT.pack_into(
            mapping, STREAM_HEADER.size,
            1, 10_000_000, 0.25, -0.5, 0.75, -1.0,
            0.0, 0.0, 0, 0x03,
        )
        client = RawHidStreamClient(capacity=capacity)
        client._mapping = mapping

        samples, newest, dropped = client.read_samples(
            0, include_axes=True
        )

        self.assertEqual((newest, dropped), (1, 0))
        self.assertEqual(samples[0][4], 0x03)
        client._mapping = None
        mapping.close()

    def test_stream_reader_can_return_trigger_controls(self):
        capacity = 1024
        mapping = mmap.mmap(
            -1, STREAM_HEADER.size + capacity * STREAM_SLOT.size
        )
        STREAM_HEADER.pack_into(
            mapping, 0,
            STREAM_MAGIC, STREAM_VERSION,
            STREAM_HEADER.size, STREAM_SLOT.size,
            capacity, 1, 0, 0x3F,
            1, 1, 10_000_000, 1,
        )
        STREAM_SLOT.pack_into(
            mapping, STREAM_HEADER.size,
            1, 10_000_000, 0.0, 0.0, 0.0, 0.0,
            0.25, 0.75, 0, 0x30,
        )
        client = RawHidStreamClient(capacity=capacity)
        client._mapping = mapping

        samples, newest, dropped = client.read_samples(
            0, include_axes=True, include_controls=True
        )

        self.assertEqual((newest, dropped), (1, 0))
        self.assertEqual(samples[0][4], 0x30)
        self.assertEqual(samples[0][5], (0.25, 0.75))
        client._mapping = None
        mapping.close()

    def test_fixed_stream_helper_is_limited_to_verified_layouts(self):
        self.assertFalse(_fixed_stream_probe_supported(
            r"\\?\HID#VID_9999&PID_0001&IG_03#generic"
        ))
        self.assertTrue(_fixed_stream_probe_supported(
            r"\\?\HID#VID_CAFE&PID_4020#standalone"
        ))
        self.assertTrue(_fixed_stream_probe_supported(
            r"\\?\HID#VID_045E&PID_028E&IG_00#vigem"
        ))

    def test_analysis_keeps_raw_rate_separate_from_effective_rate(self):
        tracker = _StickUpdateTracker()
        for state in range(250):
            point = (state / 250.0, -state / 250.0)
            for _repeat in range(4):
                tracker.add(point)
        client = RawHidAnalysisClient()
        started = time.perf_counter() - 1.0
        histogram = [0] * 100001
        histogram[1000] = 999

        client._publish_analysis_snapshot(
            state="complete",
            started_wall=started,
            duration_seconds=1.0,
            first_timestamp=0.0,
            last_timestamp=1.0,
            histogram=histogram,
            interval_count=999,
            interval_sum_us=999_000.0,
            interval_min_us=1000.0,
            interval_max_us=1000.0,
            trackers={"left": tracker, "right": _StickUpdateTracker()},
            stream_status={"raw_reports": 1000, "ignored_reports": 0},
            dropped_samples=0,
            finished=True,
        )
        snapshot = client.read_snapshot()

        self.assertAlmostEqual(snapshot["rate_hz"], 999.0)
        self.assertAlmostEqual(snapshot["effective_rate_hz"], 249.0)
        self.assertAlmostEqual(
            snapshot["effective_ratio"], 249.0 / 999.0
        )

    def test_analysis_detects_regular_four_report_repeats(self):
        tracker = _StickUpdateTracker()
        states = 256
        repeats = 4
        for state in range(states):
            angle = state / (states / 4.0) * 2.0 * math.pi
            point = (math.cos(angle), math.sin(angle))
            for _repeat in range(repeats):
                tracker.add(point)
        client = RawHidAnalysisClient()
        duration = 4.0
        raw_reports = states * repeats
        histogram = [0] * 100001
        histogram[3906] = raw_reports - 1

        client._publish_analysis_snapshot(
            state="complete",
            started_wall=time.perf_counter() - duration,
            duration_seconds=duration,
            first_timestamp=0.0,
            last_timestamp=duration,
            histogram=histogram,
            interval_count=raw_reports - 1,
            interval_sum_us=duration * 1_000_000.0,
            interval_min_us=3906.0,
            interval_max_us=3906.0,
            trackers={"left": tracker, "right": _StickUpdateTracker()},
            stream_status={
                "raw_reports": raw_reports,
                "ignored_reports": 0,
            },
            dropped_samples=0,
            finished=True,
        )
        snapshot = client.read_snapshot()

        self.assertTrue(snapshot["activity_sufficient"])
        self.assertEqual(snapshot["dominant_run_length"], 4)
        self.assertTrue(snapshot["regular_repeat"])
        self.assertAlmostEqual(snapshot["effective_ratio"], 0.25, places=2)

    def test_stream_latest_reads_only_newest_slot(self):
        capacity = 1024
        mapping = mmap.mmap(
            -1, STREAM_HEADER.size + capacity * STREAM_SLOT.size
        )
        STREAM_HEADER.pack_into(
            mapping, 0,
            STREAM_MAGIC, STREAM_VERSION,
            STREAM_HEADER.size, STREAM_SLOT.size,
            capacity, 1, 0, 0x0F,
            3, 3, 10_000_000, 3,
        )
        STREAM_SLOT.pack_into(
            mapping, STREAM_HEADER.size + STREAM_SLOT.size * 2,
            3, 30_000_000, 0.5, -0.5, 0.25, -0.25,
            0.0, 0.0, 0, 0,
        )
        client = RawHidStreamClient(capacity=capacity)
        client._mapping = mapping

        sample, newest, dropped = client.read_latest(0)

        self.assertEqual(newest, 3)
        self.assertEqual(dropped, 0)
        self.assertEqual(sample[3], 3)
        self.assertEqual(sample[1], (0.5, -0.5))
        client._mapping = None
        mapping.close()

    def test_stream_latest_counts_one_unstable_newest_slot(self):
        capacity = 16384
        mapping = mmap.mmap(
            -1, STREAM_HEADER.size + capacity * STREAM_SLOT.size
        )
        STREAM_HEADER.pack_into(
            mapping, 0,
            STREAM_MAGIC, STREAM_VERSION,
            STREAM_HEADER.size, STREAM_SLOT.size,
            capacity, 1, 0, 0x0F,
            9000, 9000, 10_000_000, 9000,
        )
        client = RawHidStreamClient(capacity=capacity)
        client._mapping = mapping

        sample, newest, dropped = client.read_latest(1000)

        self.assertIsNone(sample)
        self.assertEqual(newest, 9000)
        self.assertEqual(dropped, 1)
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
            "parser, parser.left_x, report, bytes_read, hid_data",
            source,
        )
        self.assertIn(
            "parser, parser.right_y, report, bytes_read, hid_data",
            source,
        )
        self.assertNotIn("if (sample_axes == 0) continue", source)
        self.assertIn("if (sample_axes != 0) ++parsed_samples", source)
        self.assertIn("Publish every raw report", source)
        self.assertIn(
            "even when no standard stick usage is found",
            source,
        )
        self.assertIn(
            "raw report timing",
            source,
        )
        self.assertIn("slot.reserved = sample_axes", source)
        self.assertIn('folded_path.find(L"&IG_")', source)
        self.assertIn('folded_path.find(L"VID_413D&PID_2104")', source)
        self.assertIn("bytes_read >= 9", source)
        self.assertIn("++stop_poll_counter >= 64", source)
        self.assertIn("stop_poll_interval", source)
        self.assertIn("std::vector<HIDP_DATA> hid_data", source)
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
