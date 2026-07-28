import mmap
import math
import struct
import sys
import time
import unittest
import zlib
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gamepad_devices import (
    GamepadDevice,
    GamepadState,
    JOYCAPS_HASR,
    JOYCAPS_HASU,
    JOYCAPS_HASV,
    JOYCAPS_HASZ,
    JOYCAPSW,
    NativeGamepadSampler,
    S2P_MOBILE_HID_PROFILE,
    WindowsGamepadBackend,
    _decode_isolated_enumeration,
    is_s2p_mobile_hid_device,
    is_s2p_mobile_hid_name,
    is_s2p_standalone_xinput_winmm_mirror,
    normalize_signed_axis,
    normalize_trigger_axis,
    normalize_unsigned_axis,
    s2p_mobile_hid_winmm_buttons,
    winmm_pov_buttons,
)
from gamepad_test_app import (
    GAMEPAD_TEST_STARTUP_IMAGE,
    GAMEPAD_TEST_APP_ID,
    GamepadTestHost,
    GamepadTestStartupOverlay,
    apply_root_icon,
    command_line_language,
    configure_windows_taskbar_identity,
    notify_parent_closing,
    notify_parent_ready,
    watch_parent_commands,
)
from gamepad_test_window import (
    GYRO_RESPONSE_COLORS,
    GYRO_RESPONSE_OUTER_INSET,
    GAMEPAD_TESTER_TITLE,
    GamepadTestWindow,
    PLOT_CENTER,
    PLOT_RADIUS,
    PLOT_SIZE,
    SHAPE_BIN_COUNT,
    StickHistory,
    StickPlot,
    TEST_ICON_PATH,
    build_device_display_mapping,
    curve_output_radii,
    diagnostic_telemetry_for_device,
    display_refresh_rate,
    encode_rgba_png,
    localized_device_name,
    normalize_test_parameter,
    primary_display_refresh_rate,
    raw_hid_test_device,
    shape_ease_amount,
)
from stick_processing import apply_output_shape
from raw_hid_probe import RawHidDevice
import test_telemetry
from test_telemetry import SharedTestTelemetry, TELEMETRY_SIZE


class DiagnosticDeviceSelectionTests(unittest.TestCase):
    def test_native_target_never_uses_fresh_s2p_telemetry(self):
        target = GamepadDevice(
            "xinput:1", "xinput", 1, "Other Controller", True
        )
        native = {
            "device_key": "xinput:1",
            "device_name": "Other Controller",
            "source_rate_hz": 125.0,
        }

        selected = diagnostic_telemetry_for_device(
            target,
            {"device_key": "s2p", "source_rate_hz": 250.0},
            native,
            True,
        )

        self.assertEqual(selected["device_key"], "xinput:1")
        self.assertEqual(selected["source_rate_hz"], 125.0)

    def test_mismatched_native_snapshot_is_rejected(self):
        target = GamepadDevice(
            "xinput:2", "xinput", 2, "Selected Controller", True
        )

        selected = diagnostic_telemetry_for_device(
            target,
            {"device_key": "s2p", "source_rate_hz": 250.0},
            {"device_key": "xinput:1", "source_rate_hz": 125.0},
            True,
        )

        self.assertEqual(selected, {})

    def test_s2p_target_uses_only_fresh_shared_telemetry(self):
        target = GamepadDevice(
            "s2p", "s2p", 0, "S2P-XInput-Lite", True
        )
        s2p = {"source_rate_hz": 250.0}

        self.assertEqual(
            diagnostic_telemetry_for_device(
                target, s2p, {"device_key": "xinput:1"}, True
            ),
            s2p,
        )
        self.assertEqual(
            diagnostic_telemetry_for_device(
                target, s2p, {"device_key": "xinput:1"}, False
            ),
            {},
        )

    def test_gamepad_tester_title_includes_product_and_version(self):
        self.assertEqual(
            GAMEPAD_TESTER_TITLE,
            "S2P-XInput-Lite v0.7.0 GamepadTester",
        )


class SharedTelemetryTests(unittest.TestCase):
    def test_reader_heartbeat_gates_and_round_trips_payload(self):
        clock = [1_000_000_000]
        mapping = mmap.mmap(-1, TELEMETRY_SIZE)
        channel = SharedTestTelemetry(
            mapping=mapping, clock_ns=lambda: clock[0]
        )
        try:
            self.assertFalse(channel.publish_if_requested({"value": 1}))
            channel.mark_reader_active()
            clock[0] += 20_000_000
            self.assertTrue(channel.publish_if_requested({"value": 2}))
            self.assertEqual(channel.read_latest()["value"], 2)
        finally:
            channel.close()

    def test_publish_rate_limit_and_reader_timeout(self):
        clock = [2_000_000_000]
        mapping = mmap.mmap(-1, TELEMETRY_SIZE)
        channel = SharedTestTelemetry(
            mapping=mapping, clock_ns=lambda: clock[0]
        )
        try:
            channel.mark_reader_active()
            clock[0] += 20_000_000
            self.assertTrue(channel.publish_due())
            self.assertTrue(channel.publish_if_requested({"frame": 1}))
            clock[0] += 1_000_000
            self.assertFalse(channel.publish_due())
            self.assertFalse(channel.publish_if_requested({"frame": 2}))
            clock[0] += 3_000_000_000
            self.assertFalse(channel.reader_is_active())
        finally:
            channel.close()

    def test_trail_ring_preserves_reports_between_display_frames(self):
        mapping = mmap.mmap(-1, TELEMETRY_SIZE)
        channel = SharedTestTelemetry(mapping=mapping)
        try:
            for index in range(3):
                channel.write_trail_sample(
                    1_000_000_000 + index,
                    (index / 10.0, 0.0),
                    (0.0, -index / 10.0),
                    (0.01 * index, -0.01 * index),
                    (index / 20.0, 0.0),
                    (0.0, -index / 20.0),
                )

            samples, newest, dropped = channel.read_trail_samples(0)

            self.assertEqual(newest, 3)
            self.assertEqual(dropped, 0)
            self.assertEqual([item["sequence"] for item in samples], [1, 2, 3])
            self.assertAlmostEqual(samples[2]["physical_left"][0], 0.2)
            self.assertAlmostEqual(samples[2]["final_right"][1], -0.1)
            self.assertEqual(channel.read_trail_samples(newest), ([], 3, 0))
        finally:
            channel.close()

    def test_trail_ring_reports_overwritten_samples(self):
        mapping = mmap.mmap(-1, TELEMETRY_SIZE)
        channel = SharedTestTelemetry(mapping=mapping)
        try:
            for index in range(520):
                channel.write_trail_sample(
                    index + 1,
                    (0.0, 0.0),
                    (0.0, 0.0),
                    (0.0, 0.0),
                    (0.0, 0.0),
                    (0.0, 0.0),
                )

            samples, newest, dropped = channel.read_trail_samples(0)

            self.assertEqual(newest, 520)
            self.assertEqual(len(samples), 512)
            self.assertEqual(dropped, 8)
            self.assertEqual(samples[0]["sequence"], 9)
        finally:
            channel.close()

    def test_trail_slot_is_invalidated_before_wrapped_payload_write(self):
        mapping = mmap.mmap(-1, TELEMETRY_SIZE)
        channel = SharedTestTelemetry(mapping=mapping)
        try:
            with patch(
                "test_telemetry.struct.pack_into",
                wraps=struct.pack_into,
            ) as pack_into:
                channel.write_trail_sample(
                    1,
                    (0.0, 0.0),
                    (0.0, 0.0),
                    (0.0, 0.0),
                    (0.0, 0.0),
                    (0.0, 0.0),
                )

            calls = [entry.args for entry in pack_into.call_args_list]
            invalidated = next(
                index for index, args in enumerate(calls)
                if args[0] == "<Q" and args[3] == 0
            )
            payload = next(
                index for index, args in enumerate(calls)
                if args[0] == "<Q10f"
            )
            self.assertLess(invalidated, payload)
        finally:
            channel.close()


class GamepadMathTests(unittest.TestCase):
    def test_raw_hid_collection_is_a_first_class_virtual_test_device(self):
        raw = RawHidDevice(
            "hid:vigem", "hid-path", "Xbox Controller",
            0x045E, 0x028E, 1, 5, 3, True,
        )

        device = raw_hid_test_device(
            raw, 2, lambda text: "Virtual" if text == "虛擬" else text
        )
        mapping = build_device_display_mapping((device,))

        self.assertEqual(device.kind, "raw_hid")
        self.assertEqual(device.raw_hid_key, raw.key)
        self.assertFalse(device.supports_rumble)
        self.assertIn(
            "Xbox Controller [Raw HID 045E:028E IF 3 Virtual]",
            mapping,
        )

    def test_generated_device_names_follow_ui_language(self):
        device = GamepadDevice(
            "xinput:0",
            "xinput",
            0,
            "XInput Gamepad 1",
            True,
            name_translation_key="XInput 手把 {index}",
        )

        translated = localized_device_name(
            device,
            lambda text: {
                "XInput 手把 {index}": "XInput Gamepad {index}",
            }.get(text, text),
        )

        self.assertEqual(translated, "XInput Gamepad 1")

    def test_high_rate_interval_formatter_preserves_sub_millisecond_detail(self):
        self.assertEqual(
            GamepadTestWindow._format_interval_us(125.0), "0.125"
        )
        self.assertEqual(
            GamepadTestWindow._format_interval_us(1500.0), "1.50"
        )
        self.assertEqual(GamepadTestWindow._format_interval_us(0.0), "—")

    def test_high_rate_chart_zooms_to_the_observed_distribution(self):
        start, end = GamepadTestWindow._raw_hid_chart_visible_range(
            (0, 0, 0, 12, 40, 18, 0, 0, 0, 0),
            10_000.0,
            (4_000.0, 5_000.0, 6_000.0),
        )

        self.assertLessEqual(start, 3)
        self.assertGreaterEqual(end, 5)
        self.assertLess(end - start, 9)

    def test_effective_rate_requires_sufficient_stick_activity(self):
        base = {
            "axes_available": True,
            "effective_rate_hz": 250.0,
            "activity_sufficient": False,
        }
        self.assertFalse(
            GamepadTestWindow._raw_hid_effective_rate_visible(base)
        )
        base["activity_sufficient"] = True
        self.assertTrue(
            GamepadTestWindow._raw_hid_effective_rate_visible(base)
        )

    def test_analysis_explains_isolated_double_period_maximum(self):
        tester = object.__new__(GamepadTestWindow)
        tester.gui = SimpleNamespace(tr=lambda text: text)
        text = tester._raw_hid_analysis_text({
            "state": "complete",
            "rate_hz": 1000.0,
            "p50_us": 1000.0,
            "p95_us": 1100.0,
            "p99_us": 1200.0,
            "max_us": 2000.0,
            "axes_available": False,
        })
        self.assertIn("最大間隔約為典型間隔的 2 倍", text)
        self.assertIn("不代表平均輸入延遲加倍", text)

    def test_analysis_flags_frequent_double_period_tail(self):
        tester = object.__new__(GamepadTestWindow)
        tester.gui = SimpleNamespace(tr=lambda text: text)
        text = tester._raw_hid_analysis_text({
            "state": "complete",
            "rate_hz": 1000.0,
            "p50_us": 1000.0,
            "p95_us": 1200.0,
            "p99_us": 1900.0,
            "max_us": 2100.0,
            "axes_available": False,
        })
        self.assertIn("跨週期情形並非單一極端值", text)
        self.assertIn("較頻繁", text)

    def test_each_radar_reports_its_own_presentation_fps(self):
        plot = object.__new__(StickPlot)
        plot.canvas = Mock(create_text=Mock(return_value=19))
        plot._presentation_times = deque(maxlen=512)
        plot._fps_item = None
        plot._fps_text = None
        plot._last_fps_draw_at = 0.0

        plot.record_presentation_fps(0.0, True)
        plot.record_presentation_fps(0.25, True)

        plot.canvas.itemconfigure.assert_called_with(19, text="4 FPS")
        plot.record_presentation_fps(1.5, False)
        plot.canvas.itemconfigure.assert_called_with(19, text="0 FPS")

    def test_high_rate_ui_does_not_force_bold_metric_fonts(self):
        source = (ROOT / "src" / "gamepad_test_window.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("_raw_hid_metric_font", source)
        self.assertNotIn('(\"Segoe UI\", 16, \"bold\")', source)

    def test_sample_display_percentage_accepts_one_percent(self):
        tester = object.__new__(GamepadTestWindow)
        tester.sample_display_percent_var = Mock(
            get=Mock(return_value=0.4)
        )
        tester.sample_display_percent_text = Mock()

        tester._update_sample_display_percent()

        tester.sample_display_percent_var.set.assert_called_once_with(1.0)
        tester.sample_display_percent_text.set.assert_called_once_with("1%")

    def test_high_rate_tab_is_detected_without_drawing_hidden_input_plots(self):
        tester = object.__new__(GamepadTestWindow)
        tester.test_notebook = Mock(select=Mock(return_value=".high_rate"))
        tester.input_tab = ".input"
        tester.rumble_tab = ".rumble"
        tester.high_rate_tab = ".high_rate"

        self.assertEqual(tester._active_test_tab(), "high_rate")

    def test_actual_sampling_stream_is_not_tied_to_visible_tab(self):
        raw_device = SimpleNamespace(path="hid-path")
        stream = SimpleNamespace(
            active=False,
            stop=Mock(return_value=True),
            start=Mock(return_value=True),
        )
        tester = object.__new__(GamepadTestWindow)
        tester.window = Mock()
        tester.raw_hid_probe = SimpleNamespace(
            read_snapshot=Mock(return_value={"state": "complete"})
        )
        tester.raw_hid_stream = stream
        tester.raw_hid_stream_enabled_var = Mock(
            get=Mock(return_value=True)
        )
        tester.raw_hid_stream_status_var = Mock()
        tester._raw_hid_stream_path = None
        tester._raw_hid_stream_sequence = 0
        tester._raw_hid_stream_dropped = 0
        tester._selected_device = lambda: GamepadDevice(
            "raw_hid:pad", "raw_hid", 0, "Raw Controller", False,
            raw_hid_key="hid:pad",
        )
        tester._selected_raw_hid_device = lambda: raw_device
        tester._active_test_tab = Mock(
            side_effect=AssertionError("stream must not depend on the tab")
        )
        tester.gui = SimpleNamespace(tr=lambda text: text)

        tester._sync_raw_hid_stream()

        stream.start.assert_called_once_with("hid-path")

    def test_tab_boundary_discards_every_pending_input_queue(self):
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_stream = SimpleNamespace(
            active=True,
            read_latest=Mock(return_value=(None, 19, 2)),
        )
        tester._raw_hid_stream_sequence = 7
        tester._raw_hid_stream_dropped = 3
        tester.native_sampler = Mock()
        tester.telemetry = Mock(
            latest_trail_sequence=Mock(return_value=23)
        )
        tester._last_trail_sequence = 5
        tester._last_consumed_token = "old"

        tester._discard_pending_monitor_samples()

        tester.raw_hid_stream.read_latest.assert_called_once_with(7)
        tester.native_sampler.read_snapshot.assert_called_once_with()
        self.assertEqual(tester._raw_hid_stream_sequence, 19)
        self.assertEqual(tester._raw_hid_stream_dropped, 5)
        self.assertEqual(tester._last_trail_sequence, 23)
        self.assertIsNone(tester._last_consumed_token)

    def test_unexpected_raw_hid_stop_updates_monitor_status(self):
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_stream_enabled_var = Mock(
            get=Mock(return_value=True)
        )
        tester.raw_hid_stream = Mock()
        tester.raw_hid_stream.status.return_value = {
            "state": "stopped",
            "error_code": 0,
        }
        tester._raw_hid_stream_path = "hid-path"
        tester.raw_hid_stream_status_var = Mock()
        tester.gui = SimpleNamespace(tr=lambda text: text)

        tester._update_raw_hid_stream_status()

        tester.raw_hid_stream_status_var.set.assert_called_once_with(
            "Raw HID 實際採樣已停止"
        )

    def test_partial_raw_hid_axes_are_reported_without_disabling_stream(self):
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_stream_enabled_var = Mock(
            get=Mock(return_value=True)
        )
        tester.raw_hid_stream = Mock()
        tester.raw_hid_stream.status.return_value = {
            "state": "running",
            "axes_mask": 0b0011,
            "raw_reports": 25,
        }
        tester._raw_hid_stream_dropped = 0
        tester._raw_hid_stream_latest_axes = ((0.25, -0.5), (0.0, 0.0))
        tester.raw_hid_stream_status_var = Mock()
        tester.gui = SimpleNamespace(tr=lambda text: text)

        tester._update_raw_hid_stream_status()

        status = tester.raw_hid_stream_status_var.set.call_args.args[0]
        self.assertIn("可用軸：LX, LY", status)
        self.assertIn("無法解析：RX, RY", status)
        self.assertIn("25", status)

    def test_raw_hid_without_stick_axes_remains_rate_measurable(self):
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_stream_enabled_var = Mock(
            get=Mock(return_value=True)
        )
        tester.raw_hid_stream = Mock()
        tester.raw_hid_stream.status.return_value = {
            "state": "running",
            "axes_mask": 0,
            "raw_reports": 120,
        }
        tester._raw_hid_stream_dropped = 0
        tester._raw_hid_stream_latest_axes = None
        tester.raw_hid_stream_status_var = Mock()
        tester.gui = SimpleNamespace(tr=lambda text: text)

        tester._update_raw_hid_stream_status()

        status = tester.raw_hid_stream_status_var.set.call_args.args[0]
        self.assertIn("無法解析標準搖桿軸", status)
        self.assertIn("120", status)

    def test_stopping_raw_hid_stream_clears_previous_axes(self):
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_stream = Mock()
        tester.raw_hid_stream.stop.return_value = True
        tester._raw_hid_stream_path = "hid-path"
        tester._raw_hid_stream_sequence = 9
        tester._raw_hid_stream_latest_axes = ((0.5, 0.25), (0.0, 0.0))

        self.assertTrue(tester._stop_raw_hid_stream())

        self.assertIsNone(tester._raw_hid_stream_latest_axes)
        self.assertIsNone(tester._raw_hid_stream_path)
        self.assertEqual(tester._raw_hid_stream_sequence, 0)

    def test_completed_measurement_resumes_actual_sampling_once(self):
        tester = object.__new__(GamepadTestWindow)
        tester._raw_hid_resume_after_measurement = True
        tester.raw_hid_probe = Mock()
        tester._sync_raw_hid_stream = Mock()

        self.assertTrue(
            tester._resume_actual_sampling_after_measurement()
        )
        self.assertFalse(
            tester._resume_actual_sampling_after_measurement()
        )
        tester.raw_hid_probe.stop.assert_called_once_with(timeout=0.1)
        tester._sync_raw_hid_stream.assert_called_once_with()

    def test_high_rate_measurement_follows_selected_device(self):
        raw_device = RawHidDevice(
            key="hid:pad",
            path="hid-path",
            name="Example Wireless Controller",
            vendor_id=0x1234,
            product_id=0x5678,
            usage_page=1,
            usage=5,
            interface_number=1,
        )
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_devices = {raw_device.key: raw_device}
        selected = GamepadDevice(
            "raw_hid:pad", "raw_hid", 0, "Wireless Controller", False,
            raw_hid_key=raw_device.key,
        )
        tester.devices = {"Wireless Controller": selected}
        tester._selected_device = lambda: selected

        self.assertIs(tester._selected_raw_hid_device(), raw_device)

    def test_raw_hid_selection_keeps_virtual_and_physical_collections(self):
        vigem = RawHidDevice(
            key="hid:vigem",
            path=r"\\?\HID#VID_045E&PID_028E&IG_00#virtual",
            name="Microsoft Controller (XBOX 360 For Windows)",
            vendor_id=0x045E,
            product_id=0x028E,
            usage_page=1,
            usage=5,
            interface_number=-1,
            is_virtual=True,
        )
        physical = RawHidDevice(
            key="hid:physical",
            path=r"\\?\HID#VID_413D&PID_2104&IG_03#physical",
            name="Microsoft Controller (XBOX 360 For Windows)",
            vendor_id=0x413D,
            product_id=0x2104,
            usage_page=1,
            usage=5,
            interface_number=-1,
        )
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_devices = {
            vigem.key: vigem,
            physical.key: physical,
        }
        selected_physical = GamepadDevice(
            "raw_hid:physical", "raw_hid", 0, physical.name, False,
            raw_hid_key=physical.key,
        )
        selected_virtual = GamepadDevice(
            "raw_hid:vigem", "raw_hid", 1, vigem.name, False,
            raw_hid_key=vigem.key,
        )
        tester._selected_device = lambda: selected_physical
        self.assertIs(tester._selected_raw_hid_device(), physical)
        tester._selected_device = lambda: selected_virtual
        self.assertIs(tester._selected_raw_hid_device(), vigem)

    def test_native_and_s2p_rows_do_not_implicitly_select_raw_hid(self):
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_devices = {}
        for selected in (
            GamepadDevice(
                "xinput:0", "xinput", 0, "Physical Controller", True
            ),
            GamepadDevice(
                "winmm:0", "winmm", 0, "Legacy Controller", False
            ),
            GamepadDevice(
                "s2p", "s2p", 1, "S2P-XInput-Lite", True
            ),
        ):
            tester._selected_device = lambda selected=selected: selected
            self.assertIsNone(tester._selected_raw_hid_device())

    def test_s2p_output_measurement_requires_selecting_raw_collection(self):
        tester = object.__new__(GamepadTestWindow)
        selected = GamepadDevice(
            "s2p", "s2p", 1, "S2P-XInput-Lite", True
        )
        tester._selected_device = lambda: selected
        tester.raw_hid_devices = {}
        tester.raw_hid_probe = SimpleNamespace(available=True)
        tester.raw_hid_start_button = Mock()
        tester.raw_hid_stop_button = Mock()
        tester.raw_hid_duration_combo = Mock()
        tester.device_combo = Mock()
        tester.gui = SimpleNamespace(tr=lambda text: text)

        tester._set_raw_hid_controls_active(False)

        tester.raw_hid_start_button.configure.assert_called_once_with(
            state="disabled"
        )

    def test_missing_selected_raw_hid_collection_is_rejected(self):
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_devices = {}
        selected = GamepadDevice(
            "raw_hid:missing", "raw_hid", 0, "Missing", False,
            raw_hid_key="hid:missing",
        )
        tester._selected_device = lambda: selected
        self.assertIsNone(tester._selected_raw_hid_device())

    def test_raw_hid_output_measurement_is_available_for_explicit_collection(self):
        raw_device = RawHidDevice(
            "hid:bridge", "hid-path", "Virtual Controller",
            0x045E, 0x028E, 1, 5, 0, True,
        )
        selected = GamepadDevice(
            "raw_hid:bridge", "raw_hid", 0, raw_device.name, False,
            raw_hid_key=raw_device.key,
        )
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_devices = {raw_device.key: raw_device}
        tester._selected_device = lambda: selected
        tester.raw_hid_probe = SimpleNamespace(available=True)
        tester.raw_hid_start_button = Mock()
        tester.raw_hid_stop_button = Mock()
        tester.raw_hid_duration_combo = Mock()
        tester.device_combo = Mock()
        tester.gui = SimpleNamespace(tr=lambda text: text)

        tester._set_raw_hid_controls_active(False)

        tester.raw_hid_start_button.configure.assert_called_once_with(
            state="normal"
        )

    def test_multiple_raw_hid_collections_select_exact_requested_path(self):
        first = RawHidDevice(
            "hid:first", "hid-first", "Generic Controller",
            0x1111, 0x0001, 1, 5, 0,
        )
        second = RawHidDevice(
            "hid:second", "hid-second", "Generic Controller",
            0x2222, 0x0002, 1, 5, 1,
        )
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_devices = {first.key: first, second.key: second}
        selected = GamepadDevice(
            "raw_hid:second", "raw_hid", 1, second.name, False,
            raw_hid_key=second.key,
        )
        tester._selected_device = lambda: selected
        self.assertIs(tester._selected_raw_hid_device(), second)

    def test_raw_hid_monitor_pairs_exact_third_party_winmm_vid_pid(self):
        raw = RawHidDevice(
            "hid:third-party", "hid-path", "Third Party Pad",
            0x1234, 0x5678, 1, 5, 0,
        )
        native = GamepadDevice(
            "winmm:2", "winmm", 2, "Third Party Pad", False
        )
        caps = JOYCAPSW()
        caps.wMid = 0x1234
        caps.wPid = 0x5678
        tester = object.__new__(GamepadTestWindow)
        tester._selected_raw_hid_device = lambda: raw
        tester._native_test_devices = (native,)
        tester.backend = SimpleNamespace(_winmm_caps={2: caps})
        tester.latest_telemetry = {}

        self.assertIs(tester._native_device_for_raw_hid(), native)

    def test_standalone_xinput_prefers_xinput_over_matching_winmm(self):
        raw = RawHidDevice(
            "hid:standalone", "hid-path", "S2P Standalone",
            0xCAFE, 0x4020, 1, 5, 0,
        )
        xinput = GamepadDevice(
            "xinput:0", "xinput", 0, "XInput Gamepad 1", True
        )
        winmm = GamepadDevice(
            "winmm:0", "winmm", 0, "Microsoft PC joystick", False
        )
        caps = JOYCAPSW()
        caps.wMid = 0xCAFE
        caps.wPid = 0x4020
        tester = object.__new__(GamepadTestWindow)
        tester._selected_raw_hid_device = lambda: raw
        tester._native_test_devices = (xinput, winmm)
        tester.backend = SimpleNamespace(_winmm_caps={0: caps})
        tester.latest_telemetry = {}

        self.assertIs(tester._native_device_for_raw_hid(), xinput)

    def test_raw_hid_monitor_pairs_single_xinput_ig_collection(self):
        raw = RawHidDevice(
            "hid:xinput",
            r"\\?\HID#VID_9999&PID_0001&IG_00#pad",
            "XInput-compatible Pad",
            0x9999, 0x0001, 1, 5, 0,
        )
        native = GamepadDevice(
            "xinput:1", "xinput", 1, "XInput Gamepad 2", True
        )
        tester = object.__new__(GamepadTestWindow)
        tester._selected_raw_hid_device = lambda: raw
        tester._native_test_devices = (native,)
        tester.backend = SimpleNamespace(_winmm_caps={})
        tester.latest_telemetry = {}

        self.assertIs(tester._native_device_for_raw_hid(), native)

    def test_raw_hid_monitor_does_not_guess_between_xinput_devices(self):
        raw = RawHidDevice(
            "hid:xinput",
            r"\\?\HID#VID_9999&PID_0001&IG_00#pad",
            "XInput-compatible Pad",
            0x9999, 0x0001, 1, 5, 0,
        )
        tester = object.__new__(GamepadTestWindow)
        tester._selected_raw_hid_device = lambda: raw
        tester._native_test_devices = (
            GamepadDevice(
                "xinput:0", "xinput", 0, "XInput Gamepad 1", True
            ),
            GamepadDevice(
                "xinput:1", "xinput", 1, "XInput Gamepad 2", True
            ),
        )
        tester.backend = SimpleNamespace(_winmm_caps={})
        tester.latest_telemetry = {}

        self.assertIsNone(tester._native_device_for_raw_hid())

    def test_tester_parameter_input_clamps_and_snaps(self):
        self.assertAlmostEqual(
            normalize_test_parameter("2.54", 0.5, 5.0, 0.1),
            2.5,
        )
        self.assertEqual(
            normalize_test_parameter("999", 10.0, 100.0, 1.0),
            100.0,
        )
        with self.assertRaises(ValueError):
            normalize_test_parameter("nan", 0.0, 1.0, 0.1)

    def test_tester_value_label_supports_drag_and_click_entry(self):
        class FakeVariable:
            def __init__(self):
                self.value = 2.5

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        class FakeWidget:
            def __init__(self):
                self.callbacks = {}
                self.options = {}

            def configure(self, **options):
                self.options.update(options)

            def bind(self, sequence, callback):
                self.callbacks[sequence] = callback

            def instate(self, _states):
                return False

        tester = object.__new__(GamepadTestWindow)
        tester._open_parameter_editor = Mock()
        widget = FakeWidget()
        variable = FakeVariable()
        changed = Mock()
        tester._bind_parameter_control(
            widget,
            variable,
            "軌跡長度",
            0.5,
            5.0,
            step=0.1,
            number_format=".1f",
            on_change=changed,
            state_widget=widget,
        )
        start = SimpleNamespace(x_root=10, y_root=10)
        moved = SimpleNamespace(x_root=34, y_root=10)

        widget.callbacks["<ButtonPress-1>"](start)
        widget.callbacks["<B1-Motion>"](moved)
        widget.callbacks["<ButtonRelease-1>"](moved)

        self.assertAlmostEqual(variable.value, 2.8)
        changed.assert_called_once_with()
        tester._open_parameter_editor.assert_not_called()

        widget.callbacks["<ButtonPress-1>"](start)
        widget.callbacks["<ButtonRelease-1>"](start)
        tester._open_parameter_editor.assert_called_once()

    def test_tester_window_stays_hidden_until_final_geometry_is_committed(self):
        root = Mock()
        window = Mock()
        with patch(
            "gamepad_test_window.tk.Toplevel",
            return_value=window,
        ):
            created = GamepadTestWindow._create_hidden_window(root)

        self.assertIs(created, window)
        window.withdraw.assert_called_once_with()

        window.reset_mock()
        GamepadTestWindow._reveal_positioned_window(
            window,
            "750x720+100+80",
        )

        self.assertEqual(
            window.method_calls,
            [
                call.geometry("750x720+100+80"),
                call.update_idletasks(),
                call.deiconify(),
                call.lift(),
            ],
        )

    def test_parent_pipe_can_raise_tester_then_close_it(self):
        stream = SimpleNamespace(
            readline=Mock(side_effect=(b"show\n", b"close\n"))
        )
        shutdown_requested = Mock()
        show_requested = Mock()

        watch_parent_commands(
            stream,
            shutdown_requested,
            show_requested,
        )

        show_requested.set.assert_called_once_with()
        shutdown_requested.set.assert_called_once_with()
        self.assertEqual(stream.readline.call_count, 2)

    def test_mobile_hid_product_name_selects_known_mapping(self):
        self.assertTrue(is_s2p_mobile_hid_name("S2P Mobile Gamepad"))
        self.assertTrue(
            is_s2p_mobile_hid_name("S2P Mobile Gamepad (Interface 2)")
        )
        self.assertFalse(is_s2p_mobile_hid_name("Generic USB Gamepad"))

    def test_mobile_hid_vid_pid_survives_generic_winmm_name(self):
        caps = JOYCAPSW()
        caps.wMid = 0xCAFE
        caps.wPid = 0x4021

        self.assertTrue(
            is_s2p_mobile_hid_device(caps, "Microsoft PC joystick driver")
        )
        caps.wPid = 0x4020
        self.assertFalse(
            is_s2p_mobile_hid_device(caps, "Microsoft PC joystick driver")
        )

    def test_standalone_xinput_winmm_mirror_is_identified_by_vid_pid(self):
        caps = JOYCAPSW()
        caps.wMid = 0xCAFE
        caps.wPid = 0x4020
        self.assertTrue(is_s2p_standalone_xinput_winmm_mirror(caps))
        caps.wPid = 0x4021
        self.assertFalse(is_s2p_standalone_xinput_winmm_mirror(caps))

    def test_mobile_hid_uses_semantic_buttons_axes_and_triggers(self):
        caps = JOYCAPSW()
        for minimum_name in (
            "wXmin", "wYmin", "wZmin", "wRmin", "wUmin", "wVmin"
        ):
            setattr(caps, minimum_name, 0)
        for maximum_name in (
            "wXmax", "wYmax", "wZmax", "wRmax", "wUmax", "wVmax"
        ):
            setattr(caps, maximum_name, 65535)
        caps.wCaps = (
            JOYCAPS_HASZ | JOYCAPS_HASR | JOYCAPS_HASU | JOYCAPS_HASV
        )
        caps.wNumAxes = 6
        caps.wNumButtons = 16
        backend = object.__new__(WindowsGamepadBackend)
        backend._winmm_caps = {0: caps}

        def fill_state(_index, pointer):
            info = pointer._obj
            info.dwXpos = info.dwYpos = 32768
            info.dwZpos = 65535
            info.dwRpos = 0
            info.dwUpos = 16384
            info.dwVpos = 49151
            # WinMM indexes these by HID Usage: Button 1=A, Button 8=RB,
            # Button 14=L3. The raw report positions differ for Android.
            info.dwButtons = (1 << 0) | (1 << 7) | (1 << 13)
            info.dwPOV = 9000
            return 0

        backend.winmm = SimpleNamespace(joyGetPosEx=fill_state)
        state = backend._read_winmm(0, S2P_MOBILE_HID_PROFILE)

        self.assertEqual(state.buttons, ("A", "RB", "L3", "→"))
        self.assertAlmostEqual(state.right[0], 1.0)
        self.assertAlmostEqual(state.right[1], 1.0)
        self.assertAlmostEqual(state.left_trigger, 0.25, places=3)
        self.assertAlmostEqual(state.right_trigger, 0.75, places=3)

    def test_mobile_hid_trigger_normalization_clamps_to_unit_range(self):
        self.assertEqual(normalize_trigger_axis(-1, 0, 255), 0.0)
        self.assertEqual(normalize_trigger_axis(128, 0, 255), 128 / 255)
        self.assertEqual(normalize_trigger_axis(300, 0, 255), 1.0)

    def test_mobile_hid_raw_report_supplies_triggers_hidden_by_winmm(self):
        caps = JOYCAPSW()
        for minimum_name in (
            "wXmin", "wYmin", "wZmin", "wRmin", "wUmin", "wVmin"
        ):
            setattr(caps, minimum_name, 0)
        for maximum_name in (
            "wXmax", "wYmax", "wZmax", "wRmax", "wUmax", "wVmax"
        ):
            setattr(caps, maximum_name, 65535)
        caps.wCaps = JOYCAPS_HASZ | JOYCAPS_HASR
        caps.wNumAxes = 4
        backend = object.__new__(WindowsGamepadBackend)
        backend._winmm_caps = {0: caps}
        backend._s2p_mobile_hid = SimpleNamespace(
            read=Mock(side_effect=[
                [0, 0, 8, 0, 0, 0, 0, 0, 0, 0, 0, 64, 192],
                [],
            ])
        )
        backend._s2p_mobile_hid_triggers = None

        def fill_state(_index, pointer):
            info = pointer._obj
            info.dwXpos = info.dwYpos = 32768
            info.dwZpos = info.dwRpos = 32768
            info.dwPOV = 0xFFFF
            return 0

        backend.winmm = SimpleNamespace(joyGetPosEx=fill_state)
        state = backend._read_winmm(0, S2P_MOBILE_HID_PROFILE)

        self.assertAlmostEqual(state.left_trigger, 64 / 255)
        self.assertAlmostEqual(state.right_trigger, 192 / 255)

    def test_mobile_hid_invalid_winmm_trigger_ranges_use_raw_report(self):
        caps = JOYCAPSW()
        caps.wCaps = (
            JOYCAPS_HASZ | JOYCAPS_HASR | JOYCAPS_HASU | JOYCAPS_HASV
        )
        caps.wNumAxes = 6
        caps.wUmin = caps.wUmax = 0
        caps.wVmin = caps.wVmax = 0
        for name in ("wXmax", "wYmax", "wZmax", "wRmax"):
            setattr(caps, name, 65535)
        backend = object.__new__(WindowsGamepadBackend)
        backend._winmm_caps = {0: caps}
        backend._s2p_mobile_hid = SimpleNamespace(
            read=Mock(side_effect=[
                [0, 0, 8, 0, 0, 0, 0, 0, 0, 0, 0, 51, 204],
                [],
            ])
        )
        backend._s2p_mobile_hid_triggers = None

        def fill_state(_index, pointer):
            info = pointer._obj
            info.dwXpos = info.dwYpos = 32768
            info.dwZpos = info.dwRpos = 32768
            info.dwPOV = 0xFFFF
            return 0

        backend.winmm = SimpleNamespace(joyGetPosEx=fill_state)
        state = backend._read_winmm(0, S2P_MOBILE_HID_PROFILE)

        self.assertAlmostEqual(state.left_trigger, 51 / 255)
        self.assertAlmostEqual(state.right_trigger, 204 / 255)

    def test_mobile_hid_winmm_buttons_follow_usage_numbers(self):
        expected_by_usage = {
            1: "A",
            2: "B",
            4: "X",
            5: "Y",
            7: "LB",
            8: "RB",
            9: "L2",
            10: "R2",
            11: "BACK",
            12: "START",
            13: "GUIDE",
            14: "L3",
            15: "R3",
        }

        for usage, expected in expected_by_usage.items():
            with self.subTest(usage=usage):
                self.assertEqual(
                    s2p_mobile_hid_winmm_buttons(1 << (usage - 1)),
                    (expected,),
                )
        self.assertEqual(
            s2p_mobile_hid_winmm_buttons(
                (1 << (3 - 1)) | (1 << (6 - 1)) | (1 << (16 - 1))
            ),
            (),
        )

    def test_duplicate_device_names_receive_unique_selector_labels(self):
        first = GamepadDevice("winmm:0", "winmm", 0, "USB Gamepad", False)
        second = GamepadDevice("winmm:1", "winmm", 1, "USB Gamepad", False)

        mapping = build_device_display_mapping((first, second))

        self.assertEqual(len(mapping), 2)
        self.assertEqual(
            {device.key for device in mapping.values()},
            {"winmm:0", "winmm:1"},
        )

    def test_stop_rumble_zeros_the_slot_that_was_actually_active(self):
        tester = object.__new__(GamepadTestWindow)
        tester.window = None
        tester.backend = Mock()
        tester.backend.set_xinput_rumble.return_value = True
        tester.devices = {
            "old": GamepadDevice("xinput:0", "xinput", 0, "old", True),
            "new": GamepadDevice("xinput:1", "xinput", 1, "new", True),
        }
        tester.selected_device_var = Mock(get=Mock(return_value="new"))
        tester._active_rumble_slot = 0
        tester._rumble_jobs = {}
        tester._repeat_rumble_jobs = {}
        tester._rumble_layers = {}
        tester._rumble_layer_templates = {}
        tester._active_rumble_templates = {}
        tester._refresh_rumble_template_styles = Mock()

        tester.stop_rumble()

        tester.backend.set_xinput_rumble.assert_called_once_with(0, 0.0, 0.0)
        self.assertIsNone(tester._active_rumble_slot)

    def test_stop_rumble_forgets_disconnected_slot_after_failed_zero(self):
        tester = object.__new__(GamepadTestWindow)
        tester.window = None
        tester.backend = Mock()
        tester.backend.set_xinput_rumble.return_value = False
        tester.devices = {}
        tester.selected_device_var = Mock(get=Mock(return_value=""))
        tester._active_rumble_slot = 2
        tester._rumble_jobs = {}
        tester._repeat_rumble_jobs = {}
        tester._rumble_layers = {}
        tester._rumble_layer_templates = {}
        tester._active_rumble_templates = {}
        tester._refresh_rumble_template_styles = Mock()

        tester.stop_rumble()

        tester.backend.set_xinput_rumble.assert_called_once_with(2, 0.0, 0.0)
        self.assertIsNone(tester._active_rumble_slot)

    def test_native_status_does_not_mirror_unrelated_bridge_status(self):
        tester = object.__new__(GamepadTestWindow)
        tester.devices = {
            "pad": GamepadDevice("xinput:1", "xinput", 1, "pad", True)
        }
        tester.selected_device_var = Mock(get=Mock(return_value="pad"))
        tester.status_var = Mock()
        tester.status_label = Mock()
        tester.gui = SimpleNamespace(tr=lambda text: text)

        with patch(
            "gamepad_test_window.read_connection_status_summary"
        ) as bridge_status:
            tester._sync_connection_status({"token": 1})

        bridge_status.assert_not_called()
        tester.status_var.set.assert_called_once_with("● 已連線")

    def test_device_refresh_reschedules_itself(self):
        tester = object.__new__(GamepadTestWindow)
        tester.window = Mock()
        tester.window.winfo_exists.return_value = True
        tester.window.after.return_value = "next-refresh"
        tester._refresh_devices = Mock()
        tester._device_refresh_job = "old-refresh"

        tester._refresh_devices_after_telemetry()

        tester._refresh_devices.assert_called_once_with(force=False)
        tester.window.after.assert_called_once_with(
            1000, tester._refresh_devices_after_telemetry
        )
        self.assertEqual(tester._device_refresh_job, "next-refresh")

    def test_periodic_device_refresh_uses_cached_enumeration(self):
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_probe = Mock(
            read_snapshot=Mock(return_value={"state": "idle"})
        )
        tester._device_enumeration_initialized = True
        tester._native_test_devices = ()
        tester.raw_hid_devices = {}
        tester.selected_device_var = Mock(get=Mock(return_value=""))
        tester.devices = {}
        tester.latest_telemetry = {}
        tester.native_sampler = Mock()
        tester.gui = SimpleNamespace(tr=lambda text: text)
        tester._set_raw_hid_controls_active = Mock()

        with patch(
            "gamepad_test_window.enumerate_raw_hid_gamepads"
        ) as enumerate_raw:
            tester._refresh_devices(force=False)

        enumerate_raw.assert_not_called()
        tester.native_sampler.enumerate_devices.assert_not_called()

    def test_manual_refresh_quiesces_readers_and_contains_errors(self):
        tester = object.__new__(GamepadTestWindow)
        tester._device_refresh_in_progress = False
        tester.device_refresh_button = Mock()
        tester.stop_rumble = Mock()
        tester.native_sampler = Mock()
        tester._stop_raw_hid_stream = Mock(return_value=True)
        tester._refresh_devices = Mock(side_effect=RuntimeError("hot plug"))
        tester._configure_native_sampler = Mock()
        tester._sync_raw_hid_stream = Mock()
        tester.window = Mock()
        tester.window.winfo_exists.return_value = True
        tester.raw_hid_probe = Mock(
            read_snapshot=Mock(return_value={"state": "idle"})
        )

        tester._request_device_refresh()

        tester.native_sampler.set_device.assert_called_once_with(None)
        tester._stop_raw_hid_stream.assert_called_once_with()
        tester._configure_native_sampler.assert_called_once_with()
        tester._sync_raw_hid_stream.assert_called_once_with()
        self.assertFalse(tester._device_refresh_in_progress)
        self.assertEqual(
            tester.device_refresh_button.configure.call_args_list[-1].kwargs,
            {"state": "normal"},
        )

    def test_winmm_pov_hat_reports_cardinal_and_diagonal_buttons(self):
        self.assertEqual(winmm_pov_buttons(0), ("↑",))
        self.assertEqual(winmm_pov_buttons(4500), ("↑", "→"))
        self.assertEqual(winmm_pov_buttons(9000), ("→",))
        self.assertEqual(winmm_pov_buttons(0xFFFF), ())

    def test_winmm_xyzr_layout_uses_z_and_r_for_right_stick(self):
        caps = JOYCAPSW()
        caps.wXmin = caps.wYmin = caps.wZmin = caps.wRmin = 0
        caps.wXmax = caps.wYmax = caps.wZmax = caps.wRmax = 65535
        caps.wCaps = JOYCAPS_HASZ | JOYCAPS_HASR
        caps.wNumButtons = 0
        backend = object.__new__(WindowsGamepadBackend)
        backend._winmm_caps = {0: caps}

        def fill_state(_index, pointer):
            info = pointer._obj
            info.dwXpos = info.dwYpos = 32768
            info.dwZpos = 65535
            info.dwRpos = 0
            info.dwPOV = 0xFFFF
            return 0

        backend.winmm = SimpleNamespace(joyGetPosEx=fill_state)
        state = backend._read_winmm(0)

        self.assertAlmostEqual(state.right[0], 1.0)
        self.assertAlmostEqual(state.right[1], 1.0)

    def test_gamepad_test_sets_root_icon_for_windows_taskbar(self):
        root = Mock()
        icon = Mock()

        with patch(
            "gamepad_test_app.tk.PhotoImage",
            return_value=icon,
        ) as image_factory:
            result = apply_root_icon(root)

        image_factory.assert_called_once_with(
            master=root,
            file=str(TEST_ICON_PATH),
        )
        root.iconphoto.assert_called_once_with(True, icon)
        self.assertIs(result, icon)

    def test_direct_gamepad_tester_shows_requested_startup_banner(self):
        root = Mock()
        root.winfo_screenwidth.return_value = 1920
        root.winfo_screenheight.return_value = 1080
        window = Mock()
        window.after.return_value = "animation-job"
        image = Mock()
        image.width.return_value = 600
        image.height.return_value = 340
        canvas = Mock()
        canvas.create_text.return_value = "loading-text"

        with (
            patch(
                "gamepad_test_app.tk.Toplevel",
                return_value=window,
            ),
            patch(
                "gamepad_test_app.tk.PhotoImage",
                return_value=image,
            ) as image_factory,
            patch(
                "gamepad_test_app.tk.Canvas",
                return_value=canvas,
            ),
        ):
            overlay = GamepadTestStartupOverlay(root, language="en")

        image_factory.assert_called_once_with(
            master=window,
            file=str(GAMEPAD_TEST_STARTUP_IMAGE),
        )
        self.assertEqual(
            canvas.create_text.call_args.kwargs["text"],
            "Starting Gamepad Tester...",
        )
        window.geometry.assert_called_once_with("600x340+660+370")
        root.update.assert_called_once_with()
        overlay.destroy()

    def test_gamepad_test_uses_separate_windows_app_id(self):
        setter = Mock()
        windows_libraries = SimpleNamespace(
            shell32=SimpleNamespace(
                SetCurrentProcessExplicitAppUserModelID=setter
            )
        )

        with patch("gamepad_test_app.ctypes.windll", windows_libraries):
            configure_windows_taskbar_identity()

        setter.assert_called_once_with(GAMEPAD_TEST_APP_ID)

    def test_gamepad_test_accepts_explicit_parent_language(self):
        with patch("gamepad_test_app.load_config") as load_config:
            host = GamepadTestHost(Mock(), language="ZH")

        self.assertEqual(host.language, "zh")
        load_config.assert_not_called()

    def test_gamepad_test_language_argument_is_validated(self):
        self.assertEqual(
            command_line_language(("--parent-pipe", "--language", "en")),
            "en",
        )
        self.assertEqual(
            command_line_language(("--language=ZH",)),
            "zh",
        )
        self.assertIsNone(
            command_line_language(("--language", "invalid")),
        )

    def test_gamepad_test_notifies_parent_before_closing(self):
        stream = Mock()

        self.assertTrue(notify_parent_closing(stream))

        stream.write.assert_called_once_with(b"closing\n")
        stream.flush.assert_called_once_with()

    def test_gamepad_test_notifies_parent_when_ready(self):
        stream = Mock()

        self.assertTrue(notify_parent_ready(stream))

        stream.write.assert_called_once_with(b"ready\n")
        stream.flush.assert_called_once_with()

    def test_gamepad_test_uses_dedicated_window_icon(self):
        self.assertTrue(TEST_ICON_PATH.is_file())
        tester = object.__new__(GamepadTestWindow)
        window = Mock()
        icon = Mock()

        with patch(
            "gamepad_test_window.tk.PhotoImage",
            return_value=icon,
        ) as image_factory:
            tester._apply_window_icon(window)

        image_factory.assert_called_once_with(
            master=window,
            file=str(TEST_ICON_PATH),
        )
        window.iconphoto.assert_called_once_with(False, icon)
        self.assertIs(tester._window_icon, icon)

    def test_localized_source_label_maps_back_to_stable_source(self):
        labels = {
            "實體搖桿": "Stick",
            "陀螺儀": "Gyro",
            "合成結果": "Final",
            "實際輸入": "Input",
        }
        plot = object.__new__(StickPlot)
        plot.owner = SimpleNamespace(
            gui=SimpleNamespace(
                tr=lambda text: labels.get(text, text)
            )
        )
        plot.source_var = Mock(get=Mock(return_value="Final"))

        self.assertEqual(plot.selected_source(), "合成結果")

    def test_s2p_plot_draw_keeps_source_available_for_detail_update(self):
        plot = object.__new__(StickPlot)
        plot.side = "left"
        plot.trail_color = "#1976D2"
        plot.source_var = Mock(get=Mock(return_value="合成結果"))
        plot.zoom = 1.0
        plot.pan_x = 0.0
        plot.pan_y = 0.0
        plot._last_trace_draw_at = time.perf_counter()
        plot._static_key = (True, "合成結果", 1.0, 0.0, 0.0, False)
        plot.owner = SimpleNamespace(
            shape_enabled_var=Mock(get=Mock(return_value=False)),
            trail_length_var=Mock(get=Mock(return_value=2.5)),
        )
        plot.canvas = Mock()
        plot.value_var = Mock()
        plot.metrics_var = Mock()
        plot.detail_var = Mock()

        plot.draw(StickHistory(), 0.25, -0.5, {"left": {}}, True)

        plot.detail_var.set.assert_called_once()

    def test_extra_rumble_templates_end_at_silence(self):
        for name in (
            "heartbeat", "footsteps", "terrain", "low_rumble", "burst",
            "machine_gun", "shotgun", "turbo", "rotor", "countdown",
        ):
            pattern = GamepadTestWindow._rumble_pattern(None, name)
            self.assertIsNotNone(pattern)
            self.assertEqual(pattern[-1][1:], (0.0, 0.0))

    def test_display_refresh_rate_is_safely_bounded(self):
        self.assertGreaterEqual(primary_display_refresh_rate(), 30.0)
        self.assertLessEqual(primary_display_refresh_rate(), 500.0)

    def test_window_refresh_change_updates_frame_interval(self):
        tester = object.__new__(GamepadTestWindow)
        tester.window = Mock()
        tester._display_refresh_hz = 60.0
        tester._frame_interval = 1.0 / 60.0
        tester._next_frame_at = 0.0

        with patch(
            "gamepad_test_window.display_refresh_rate",
            return_value=144.0,
        ):
            changed = tester._update_display_refresh_rate()

        self.assertTrue(changed)
        self.assertEqual(tester._display_refresh_hz, 144.0)
        self.assertAlmostEqual(tester._frame_interval, 1.0 / 144.0)

    def test_frame_scheduler_never_rounds_before_display_deadline(self):
        tester = object.__new__(GamepadTestWindow)
        tester.window = Mock()
        tester.window.winfo_exists.return_value = True
        tester._frame_interval = 1.0 / 165.0
        tester._next_frame_at = 10.0061
        tester._window_motion_until = 0.0
        tester._active_test_tab = Mock(return_value="input")

        with patch(
            "gamepad_test_window.time.perf_counter",
            return_value=10.0,
        ):
            tester._schedule_poll()

        delay = tester.window.after.call_args.args[0]
        self.assertEqual(delay, 7)

    def test_monitor_presentation_pauses_during_window_motion(self):
        tester = object.__new__(GamepadTestWindow)
        tester._window_motion_until = 10.0

        self.assertFalse(
            tester._monitor_presentation_allowed("input", 9.99)
        )
        self.assertTrue(
            tester._monitor_presentation_allowed("input", 10.0)
        )
        self.assertFalse(
            tester._monitor_presentation_allowed("rumble", 11.0)
        )

    def test_monitor_rate_frame_skips_detail_widget_updates(self):
        plot = object.__new__(StickPlot)
        plot.side = "left"
        plot.trail_color = "#1976D2"
        plot.source_var = Mock(get=Mock(return_value="合成結果"))
        plot.zoom = 1.0
        plot.pan_x = 0.0
        plot.pan_y = 0.0
        plot._last_trace_draw_at = time.perf_counter()
        plot._static_key = (True, "合成結果", 1.0, 0.0, 0.0, False)
        plot._bitmap_enabled = True
        plot._bitmap_photo = Mock()
        plot._bitmap_item = 8
        plot._draw_trail_bitmap = Mock(return_value=True)
        plot._dynamic_deadzone_item = None
        plot._dot_item = None
        plot.owner = SimpleNamespace(
            shape_enabled_var=Mock(get=Mock(return_value=False)),
        )
        plot.canvas = Mock()
        plot.canvas.create_oval.return_value = 9
        plot.value_var = Mock()
        plot.metrics_var = Mock()
        plot.detail_var = Mock()

        plot.draw(
            StickHistory(),
            0.25,
            -0.5,
            {"left": {}},
            True,
            update_details=False,
        )

        plot.canvas.coords.assert_called_once()
        plot.value_var.set.assert_not_called()
        plot.metrics_var.set.assert_not_called()
        plot.detail_var.set.assert_not_called()

    def test_physical_colour_overlay_stays_below_trail_and_dot(self):
        plot = object.__new__(StickPlot)
        plot.side = "left"
        plot.trail_color = "#1976D2"
        plot.source_var = Mock(get=Mock(return_value="實體搖桿"))
        plot.zoom = 1.0
        plot.pan_x = 0.0
        plot.pan_y = 0.0
        plot._last_trace_draw_at = 0.0
        plot._bitmap_enabled = True
        plot._bitmap_photo = Mock()
        plot._bitmap_item = 8
        plot._draw_trail_bitmap = Mock(return_value=True)
        plot._dynamic_deadzone_item = None
        plot._dot_item = None
        plot.owner = SimpleNamespace(
            shape_enabled_var=Mock(get=Mock(return_value=True)),
        )
        plot.canvas = Mock()
        plot.canvas.create_oval.return_value = 9
        plot._draw_curve_overlay = Mock()
        plot._draw_curve_limits = Mock()
        plot._draw_shape = Mock()
        plot.value_var = Mock()
        plot.metrics_var = Mock()
        plot.detail_var = Mock()
        telemetry = {"left": {}}
        plot._static_key = plot._static_signature(
            telemetry["left"], telemetry, True
        )
        history = StickHistory()

        plot.draw(
            history,
            0.25,
            -0.5,
            telemetry,
            True,
            update_details=False,
        )

        plot.canvas.tag_raise.assert_has_calls([
            call("trail_bitmap"), call(9)
        ])
        plot._last_trace_draw_at = 0.0
        plot.draw(
            history,
            0.25,
            -0.5,
            telemetry,
            True,
            update_details=False,
        )
        self.assertEqual(plot._draw_shape.call_count, 1)

    def test_gyro_100_percent_guide_stays_inside_dark_radar_border(self):
        plot = object.__new__(StickPlot)
        plot.canvas = Mock()
        telemetry = {
            "gyro": {
                "motion_mode": "CENTER",
                "stick_sensitivity": 1.0,
                "base_deadzone": 0.0,
                "response_curve": "LINEAR",
                "curve_strength": 0.0,
                "stick_anti_deadzone": 0.0,
            }
        }

        plot._draw_gyro_overlay(telemetry)

        outer_guide = plot.canvas.create_oval.call_args_list[3]
        radius = PLOT_RADIUS - GYRO_RESPONSE_OUTER_INSET
        self.assertEqual(
            outer_guide.args,
            (
                PLOT_CENTER - radius,
                PLOT_CENTER - radius,
                PLOT_CENTER + radius,
                PLOT_CENTER + radius,
            ),
        )
        self.assertEqual(
            outer_guide.kwargs["outline"],
            GYRO_RESPONSE_COLORS[-1],
        )
        self.assertEqual(outer_guide.kwargs["width"], 1)

    def test_shape_animation_is_time_based_across_refresh_rates(self):
        at_60_hz = StickHistory()
        at_180_hz = StickHistory()
        at_60_hz.shape_target[0] = 1.0
        at_180_hz.shape_target[0] = 1.0
        for _ in range(60):
            at_60_hz.advance_shape(shape_ease_amount(1.0 / 60.0))
        for _ in range(180):
            at_180_hz.advance_shape(shape_ease_amount(1.0 / 180.0))
        self.assertAlmostEqual(
            at_60_hz.shape_display[0],
            at_180_hz.shape_display[0],
            places=6,
        )

    def test_shape_trace_reaches_95_percent_within_150_ms(self):
        history = StickHistory()
        history.shape_target[0] = 1.0

        for _ in range(9):
            history.advance_shape(shape_ease_amount(1.0 / 60.0))

        self.assertGreater(history.shape_display[0], 0.95)

    def test_signed_axis_preserves_both_full_scale_endpoints(self):
        self.assertEqual(normalize_signed_axis(-32768), -1.0)
        self.assertEqual(normalize_signed_axis(32767), 1.0)
        self.assertEqual(normalize_signed_axis(0), 0.0)

    def test_unsigned_axis_supports_y_inversion(self):
        self.assertEqual(normalize_unsigned_axis(0, 0, 65535), -1.0)
        self.assertEqual(
            normalize_unsigned_axis(0, 0, 65535, invert=True), 1.0
        )

    def test_curve_ranges_follow_applied_output_coordinates(self):
        points = (
            (0.0, 0.0),
            (0.2, 0.1),
            (0.5, 0.4),
            (0.8, 0.9),
            (1.0, 1.0),
        )
        self.assertEqual(
            curve_output_radii(points),
            ((0.0, 0.1), (0.1, 0.4), (0.4, 0.9), (0.9, 1.0)),
        )

    def test_shape_capture_expands_neighbour_bins_and_reset_clears(self):
        history = StickHistory()
        history.add(1.0, 0.0, 10.0, record_shape=True)
        self.assertGreater(history.shape_target[0], 0.99)
        self.assertGreater(history.shape_target[1], 0.8)
        self.assertIn(0, history.covered_bins)
        self.assertTrue(history.advance_shape())
        self.assertGreater(history.shape_display[0], 0.0)
        coverage, error, maximum = history.shape_statistics()
        self.assertAlmostEqual(coverage, 100.0 / 72.0)
        self.assertAlmostEqual(error, 0.0)
        self.assertAlmostEqual(maximum, 100.0)
        history.reset()
        self.assertFalse(history.covered_bins)
        self.assertEqual(max(history.shape_display), 0.0)

    def test_complete_shape_freezes_after_one_stable_second(self):
        history = StickHistory()
        for index in range(SHAPE_BIN_COUNT):
            angle = (
                (index + 0.5) / SHAPE_BIN_COUNT
                * 2.0 * math.pi
            )
            history.add(
                math.cos(angle),
                math.sin(angle),
                5.0,
                record_shape=True,
            )
        history.advance_shape(1.0)

        self.assertFalse(history.freeze_shape_if_complete(5.99))
        self.assertTrue(history.freeze_shape_if_complete(6.0))
        self.assertTrue(history.shape_frozen)

        target_before = list(history.shape_target)
        history.add(1.0, 1.0, 7.0, record_shape=True)
        self.assertEqual(history.shape_target, target_before)
        history.reset()
        self.assertFalse(history.shape_frozen)

    def test_measured_shape_trace_keeps_visual_corner_rounding(self):
        plot = object.__new__(StickPlot)
        plot.owner = SimpleNamespace(
            shape_enabled_var=Mock(get=Mock(return_value=True))
        )
        plot.canvas = Mock()
        plot.zoom = 1.0
        plot.pan_x = 0.0
        plot.pan_y = 0.0
        history = StickHistory()
        history.shape_display[:] = [1.0] * len(history.shape_display)

        plot._draw_shape(history)

        self.assertTrue(plot.canvas.create_line.call_args.kwargs["smooth"])

    def test_square_shape_keeps_each_measured_point_on_true_boundary(self):
        history = StickHistory()
        angle_offset = math.radians(2.0)
        for index in range(len(history.shape_target)):
            angle = (
                index / len(history.shape_target) * 2.0 * math.pi
                + angle_offset
            )
            x, y = apply_output_shape(
                math.cos(angle),
                math.sin(angle),
                1.0,
            )
            history.add(x, y, float(index), record_shape=True)
        history.advance_shape(1.0)

        plot = object.__new__(StickPlot)
        plot.owner = SimpleNamespace(
            shape_enabled_var=Mock(get=Mock(return_value=True))
        )
        plot.canvas = Mock()
        plot.zoom = 1.0
        plot.pan_x = 0.0
        plot.pan_y = 0.0
        plot._draw_shape(history)

        coordinates = plot.canvas.create_line.call_args.args
        measured_points = zip(
            coordinates[0:SHAPE_BIN_COUNT * 2:2],
            coordinates[1:SHAPE_BIN_COUNT * 2:2],
        )
        for canvas_x, canvas_y in measured_points:
            x = (canvas_x - PLOT_CENTER) / PLOT_RADIUS
            y = (PLOT_CENTER - canvas_y) / PLOT_RADIUS
            self.assertAlmostEqual(max(abs(x), abs(y)), 1.0, places=6)

    def test_shape_capture_resets_when_output_shape_setting_changes(self):
        tester = object.__new__(GamepadTestWindow)
        tester.shape_enabled_var = Mock(get=Mock(return_value=True))
        tester.plots = {
            side: SimpleNamespace(
                source_var=Mock(get=Mock(return_value="??蝯?"))
            )
            for side in ("left", "right")
        }
        tester.histories = {
            "left": StickHistory(),
            "right": StickHistory(),
        }
        tester.histories["left"].add(
            1.0, 0.0, 1.0, record_shape=True
        )
        tester.latest_telemetry = {
            "left": {"output_shape": 0.0},
            "right": {"output_shape": 0.0},
            "gyro": {},
        }
        tester._baseline_trail_sequence = Mock()
        device = SimpleNamespace(key="s2p", kind="s2p")
        tester._shape_capture_signature = (
            tester._current_shape_capture_signature(device, True)
        )

        tester.latest_telemetry["left"]["output_shape"] = 1.0
        tester._sync_shape_capture_context(device, True)

        self.assertEqual(
            max(tester.histories["left"].shape_target),
            0.0,
        )
        tester._baseline_trail_sequence.assert_called_once_with()

    def test_trail_length_is_time_based(self):
        history = StickHistory()
        history.add(0.0, 0.0, 1.0, record_shape=False)
        history.add(0.5, 0.0, 2.0, record_shape=False)
        history.add(1.0, 0.0, 3.0, record_shape=False)
        history.prune(now=3.1, length_seconds=1.5)
        self.assertEqual(len(history.trail), 2)
        self.assertEqual(history.trail[0][0], 2.0)

    def test_trail_percentage_uses_original_reports_incrementally(self):
        plot = object.__new__(StickPlot)
        plot.trail_color = "#1976D2"
        plot.zoom = 1.0
        plot.pan_x = 0.0
        plot.pan_y = 0.0
        percentage = Mock(get=Mock(return_value=30.0))
        plot.owner = SimpleNamespace(
            sample_display_percent_var=percentage
        )
        plot.canvas = Mock()
        plot.canvas.create_oval.side_effect = range(1, 100)
        history = StickHistory()
        for sequence in range(10):
            history.add(
                sequence / 10.0, 0.0, float(sequence),
                record_shape=False,
            )

        plot._draw_trail(history, 9.0)
        self.assertEqual(len(plot._trail_bucket_items), 3)

        percentage.get.return_value = 100.0
        plot._draw_trail(history, 9.0)
        self.assertEqual(len(plot._trail_bucket_items), len(history.trail))

        plot.canvas.coords.reset_mock()
        plot._draw_trail(history, 9.0)
        plot.canvas.coords.assert_not_called()

    def test_canvas_fallback_uniformly_caps_high_rate_history(self):
        plot = object.__new__(StickPlot)
        plot.trail_color = "#1976D2"
        plot.zoom = 1.0
        plot.pan_x = 0.0
        plot.pan_y = 0.0
        plot.owner = SimpleNamespace(
            sample_display_percent_var=Mock(get=Mock(return_value=100.0))
        )
        plot.canvas = Mock()
        plot.canvas.create_oval.side_effect = range(1, 3000)
        history = StickHistory()
        for sequence in range(4000):
            history.add(0.0, 0.0, float(sequence), record_shape=False)

        plot._draw_trail(history, 4000.0)

        self.assertEqual(len(plot._trail_bucket_items), 2000)
        self.assertEqual(plot.canvas.create_oval.call_count, 2000)
        selected = tuple(plot._trail_selected_sequences)
        self.assertEqual(selected[0], history.trail[0][3])
        self.assertEqual(selected[-1], history.trail[-1][3])

        for sequence in range(4000, 8000):
            history.add(0.0, 0.0, float(sequence), record_shape=False)
        plot._draw_trail(history, 8000.0)

        selected = tuple(plot._trail_selected_sequences)
        self.assertLessEqual(len(selected), 2000)
        self.assertEqual(selected[0], history.trail[0][3])
        self.assertEqual(selected[-1], history.trail[-1][3])

    def test_bitmap_trail_uses_one_canvas_image_and_updates_in_place(self):
        plot = object.__new__(StickPlot)
        plot.trail_color = "#1976D2"
        plot.zoom = 1.0
        plot.pan_x = 0.0
        plot.pan_y = 0.0
        plot.owner = SimpleNamespace(
            sample_display_percent_var=Mock(
                get=Mock(return_value=30.0)
            ),
            trail_length_var=Mock(get=Mock(return_value=2.5)),
        )
        plot.canvas = Mock()
        plot.canvas.create_image.return_value = 9
        plot._bitmap_photo = None
        plot._bitmap_item = None
        plot._bitmap_scanlines = None
        plot._bitmap_pixel_expiry = None
        plot._bitmap_expiry_heap = []
        plot._bitmap_last_processed_sequence = -1
        plot._bitmap_render_config = None
        history = StickHistory()
        for sequence in range(10):
            history.add(
                0.1,
                0.1,
                float(sequence),
                record_shape=False,
            )
        photo = Mock()

        with patch(
            "gamepad_test_window.tk.PhotoImage",
            return_value=photo,
        ) as photo_factory:
            plot._draw_trail_bitmap(history, now=9.0)
            bitmap_data = photo_factory.call_args.kwargs["data"]
            self.assertTrue(bitmap_data.startswith(b"\x89PNG\r\n\x1a\n"))
            plot._draw_trail_bitmap(history, now=9.0)
            photo.configure.assert_not_called()

            history.add(0.2, 0.1, 10.0, record_shape=False)
            plot._draw_trail_bitmap(history, now=10.0)

        plot.canvas.create_image.assert_called_once()
        photo.configure.assert_called_once()

    def test_bitmap_trail_deduplicates_high_rate_samples_per_pixel(self):
        plot = object.__new__(StickPlot)
        plot.trail_color = "#1976D2"
        plot.zoom = 1.0
        plot.pan_x = 0.0
        plot.pan_y = 0.0
        plot.owner = SimpleNamespace(
            sample_display_percent_var=Mock(
                get=Mock(return_value=100.0)
            ),
            trail_length_var=Mock(get=Mock(return_value=2.5)),
            _display_refresh_hz=144.0,
        )
        plot.canvas = Mock()
        plot.canvas.create_image.return_value = 9
        plot._bitmap_photo = None
        plot._bitmap_item = None
        plot._bitmap_scanlines = None
        plot._bitmap_pixel_expiry = None
        plot._bitmap_expiry_heap = []
        plot._bitmap_last_processed_sequence = -1
        plot._bitmap_render_config = None
        history = StickHistory()
        for sequence in range(500):
            history.add(0.25, -0.5, 1.0 + sequence / 8000.0,
                        record_shape=False)

        with patch(
            "gamepad_test_window.tk.PhotoImage", return_value=Mock()
        ):
            plot._draw_trail_bitmap(history, now=1.1)

        self.assertEqual(
            plot._bitmap_last_processed_sequence,
            history.trail[-1][3],
        )
        self.assertLessEqual(len(plot._bitmap_expiry_heap), 16)
    def test_bitmap_merge_preserves_subpixel_footprint_union(self):
        plot = object.__new__(StickPlot)
        plot.trail_color = "#1976D2"
        plot.zoom = 1.0
        plot.pan_x = 0.0
        plot.pan_y = 0.0
        plot.owner = SimpleNamespace(
            sample_display_percent_var=Mock(
                get=Mock(return_value=100.0)
            ),
            trail_length_var=Mock(get=Mock(return_value=2.5)),
        )
        plot.canvas = Mock()
        plot.canvas.create_image.return_value = 9
        plot._bitmap_photo = None
        plot._bitmap_item = None
        plot._bitmap_scanlines = None
        plot._bitmap_pixel_expiry = None
        plot._bitmap_expiry_heap = []
        plot._bitmap_last_processed_sequence = -1
        plot._bitmap_render_config = None
        history = StickHistory()
        history.add(
            0.01 / PLOT_RADIUS,
            -0.01 / PLOT_RADIUS,
            1.0,
            record_shape=False,
        )
        history.add(
            0.49 / PLOT_RADIUS,
            -0.49 / PLOT_RADIUS,
            1.01,
            record_shape=False,
        )

        with patch(
            "gamepad_test_window.tk.PhotoImage", return_value=Mock()
        ):
            plot._draw_trail_bitmap(history, now=1.1)

        row_stride = 1 + PLOT_SIZE * 4
        alpha_index = 159 * row_stride + 1 + 159 * 4 + 3
        self.assertEqual(plot._bitmap_scanlines[alpha_index], 255)

    def test_expired_tile_deletes_canvas_item_before_photo(self):
        events = []

        class LoggedPhotos(dict):
            def pop(self, key, default=None):
                events.append("photo")
                return super().pop(key, default)

        plot = object.__new__(StickPlot)
        plot.trail_color = "#1976D2"
        plot.zoom = 1.0
        plot.pan_x = 0.0
        plot.pan_y = 0.0
        plot.owner = SimpleNamespace(
            sample_display_percent_var=Mock(
                get=Mock(return_value=100.0)
            ),
            trail_length_var=Mock(get=Mock(return_value=2.5)),
        )
        plot.canvas = Mock()
        plot.canvas.delete.side_effect = lambda _item: events.append(
            "canvas"
        )
        row_stride = 1 + PLOT_SIZE * 4
        plot._bitmap_scanlines = bytearray(row_stride * PLOT_SIZE)
        plot._bitmap_scanlines[4] = 255
        plot._bitmap_pixel_expiry = [0.0] * (PLOT_SIZE * PLOT_SIZE)
        plot._bitmap_pixel_expiry[0] = 1.0
        plot._bitmap_expiry_heap = [(1.0, 0)]
        plot._bitmap_last_processed_sequence = -1
        plot._bitmap_render_config = (100, 1.0, 0.0, 0.0, 2.5)
        plot._bitmap_tile_photos = LoggedPhotos({(0, 0): Mock()})
        plot._bitmap_tile_items = {(0, 0): 9}
        plot._bitmap_tile_live_counts = {(0, 0): 1}
        plot._bitmap_photo = plot._bitmap_tile_photos[(0, 0)]
        plot._bitmap_item = 9

        plot._draw_trail_bitmap(StickHistory(), now=2.0)

        self.assertEqual(events[:2], ["canvas", "photo"])

    def test_rgba_png_encoder_preserves_transparency_and_colour(self):
        scanlines = bytearray(2 * (1 + 2 * 4))
        scanlines[1:5] = bytes((25, 118, 210, 255))

        payload = encode_rgba_png(2, 2, scanlines)

        self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")
        offset = 8
        image_data = bytearray()
        while offset < len(payload):
            length = struct.unpack(">I", payload[offset:offset + 4])[0]
            kind = payload[offset + 4:offset + 8]
            data = payload[offset + 8:offset + 8 + length]
            if kind == b"IDAT":
                image_data.extend(data)
            offset += 12 + length
        self.assertEqual(zlib.decompress(image_data), bytes(scanlines))

    def test_s2p_batch_consumes_every_unseen_report(self):
        tester = object.__new__(GamepadTestWindow)
        tester.latest_telemetry = {
            "gyro": {"available": True, "target": "left"}
        }
        tester.shape_enabled_var = Mock(get=Mock(return_value=False))
        tester.plots = {
            "left": SimpleNamespace(
                source_var=Mock(get=Mock(return_value="實體搖桿"))
            ),
            "right": SimpleNamespace(
                source_var=Mock(get=Mock(return_value="合成結果"))
            ),
        }
        tester.histories = {
            "left": StickHistory(),
            "right": StickHistory(),
        }
        samples = [
            {
                "timestamp_ns": 1_000_000_000 + index * 10_000_000,
                "physical_left": (index / 10.0, 0.0),
                "physical_right": (0.0, 0.0),
                "gyro": (0.0, 0.0),
                "final_left": (0.0, 0.0),
                "final_right": (0.0, -index / 10.0),
            }
            for index in range(4)
        ]

        tester._consume_s2p_trail_samples(samples, now=10.0)

        self.assertEqual(len(tester.histories["left"].trail), 4)
        self.assertEqual(len(tester.histories["right"].trail), 4)
        self.assertAlmostEqual(
            tester.histories["left"].trail[-1][1], 0.3
        )
        self.assertAlmostEqual(
            tester.histories["right"].trail[-1][2], -0.3
        )
        self.assertAlmostEqual(
            tester.histories["left"].trail[0][0], 9.97
        )

    def test_raw_hid_background_samples_do_not_enter_monitor_trail(self):
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_stream = SimpleNamespace(
            read_latest=Mock(return_value=(
                (10.0, (0.5, -0.25), (0.0, 0.0), 7),
                7,
                0,
            ))
        )
        tester._raw_hid_stream_sequence = 0
        tester._raw_hid_stream_dropped = 0
        tester.shape_enabled_var = Mock(get=Mock(return_value=False))
        tester.histories = {
            "left": StickHistory(),
            "right": StickHistory(),
        }

        sample, consumed = tester._consume_raw_hid_stream(
            None, record_trail=False
        )

        self.assertTrue(consumed)
        self.assertEqual(sample["left"], (0.5, -0.25))
        self.assertEqual(len(tester.histories["left"].trail), 0)
        self.assertEqual(len(tester.histories["right"].trail), 0)

    def test_raw_hid_trail_ignores_invalid_axes_per_stick(self):
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_stream = SimpleNamespace(
            read_samples=Mock(return_value=(
                (
                    (
                        10.0,
                        (0.1, 0.2),
                        (0.7, 0.3),
                        7,
                        0x3F,
                        (0.25, 0.75),
                    ),
                    (
                        10.01,
                        (0.2, 0.3),
                        (0.0, -0.8),
                        8,
                        0x03,
                    ),
                ),
                8,
                0,
            ))
        )
        tester._raw_hid_stream_sequence = 0
        tester._raw_hid_stream_dropped = 0
        tester._raw_hid_stream_latest_axes = None
        tester._raw_hid_stream_latest_sample = None
        tester.shape_enabled_var = Mock(get=Mock(return_value=False))
        tester.histories = {
            "left": StickHistory(),
            "right": StickHistory(),
        }

        sample, consumed = tester._consume_raw_hid_stream(
            None, record_trail=True
        )

        self.assertTrue(consumed)
        tester.raw_hid_stream.read_samples.assert_called_once_with(
            0, include_axes=True, include_controls=True, include_buttons=True
        )
        self.assertEqual(len(tester.histories["left"].trail), 2)
        self.assertEqual(len(tester.histories["right"].trail), 1)
        self.assertEqual(sample["left"], (0.2, 0.3))
        self.assertEqual(sample["right"], (0.7, 0.3))
        self.assertEqual(sample["triggers"], (0.25, 0.75))

    def test_raw_hid_monitor_keeps_latest_details_between_reports(self):
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_stream = SimpleNamespace(
            read_latest=Mock(side_effect=(
                (
                    (10.0, (0.5, -0.25), (0.1, 0.2), 7),
                    7,
                    0,
                ),
                (None, 7, 0),
            ))
        )
        tester._raw_hid_stream_sequence = 0
        tester._raw_hid_stream_dropped = 0
        tester._raw_hid_stream_latest_sample = None
        tester.shape_enabled_var = Mock(get=Mock(return_value=False))
        tester.histories = {
            "left": StickHistory(),
            "right": StickHistory(),
        }

        first, first_consumed = tester._consume_raw_hid_stream(
            None, record_trail=False
        )
        held, held_consumed = tester._consume_raw_hid_stream(
            None, record_trail=False
        )

        self.assertTrue(first_consumed)
        self.assertFalse(held_consumed)
        self.assertEqual(held, first)
        self.assertEqual(held["layer"], "原始輸入")
        self.assertEqual(held["triggers"], (0.0, 0.0))

    def test_raw_hid_cached_controls_do_not_flicker_against_native_sample(self):
        tester = object.__new__(GamepadTestWindow)
        tester.raw_hid_stream = SimpleNamespace(
            read_latest=Mock(return_value=(None, 9, 0))
        )
        tester._raw_hid_stream_sequence = 9
        tester._raw_hid_stream_dropped = 0
        tester._raw_hid_stream_latest_sample = {
            "left": (0.2, -0.1),
            "right": (0.4, 0.3),
            "triggers": (0.75, 0.25),
            "token": ("raw_hid", 9),
        }

        merged, consumed = tester._consume_raw_hid_stream({
            "buttons": ("A",), "triggers": (None, None),
        }, record_trail=False)

        self.assertFalse(consumed)
        self.assertEqual(merged["buttons"], ("A",))
        self.assertEqual(merged["triggers"], (0.75, 0.25))
        self.assertEqual(merged["left"], (0.2, -0.1))

    def test_native_trigger_axes_appear_as_pressed_events(self):
        tester = object.__new__(GamepadTestWindow)

        active = tester._event_inputs({
            "buttons": ("A",),
            "triggers": (30.0 / 255.0, 0.1),
        }, is_s2p=False)

        self.assertEqual(active["A"], "原始輸入")
        self.assertEqual(active["LT"], "原始輸入")
        self.assertNotIn("RT", active)

    def test_analog_mobile_triggers_replace_digital_l2_r2_mirrors(self):
        tester = object.__new__(GamepadTestWindow)

        active = tester._event_inputs({
            "buttons": ("L2", "R2"),
            "triggers": (0.5, 0.0),
        }, is_s2p=False)

        self.assertEqual(active["LT"], "原始輸入")
        self.assertNotIn("L2", active)
        self.assertNotIn("R2", active)

    def test_source_change_seeds_latest_value_without_center_flash(self):
        tester = object.__new__(GamepadTestWindow)
        tester.histories = {
            "left": StickHistory(),
            "right": StickHistory(),
        }
        tester._last_consumed_token = 123
        tester._last_trail_sequence = 0
        tester.telemetry = None
        tester.latest_telemetry = {
            "timestamp_ns": time.monotonic_ns(),
            "left": {
                "physical": (0.42, -0.18),
                "gyro": (0.1, 0.2),
                "final": (0.5, -0.1),
            },
        }
        tester.plots = {
            "left": SimpleNamespace(
                source_var=Mock(get=Mock(return_value="實體搖桿"))
            )
        }
        tester.shape_enabled_var = Mock(get=Mock(return_value=False))
        tester._selected_device = Mock(
            return_value=SimpleNamespace(kind="s2p")
        )

        tester._on_source_changed("left")

        self.assertEqual(len(tester.histories["left"].trail), 1)
        _, x, y, _sequence = tester.histories["left"].trail[0]
        self.assertAlmostEqual(x, 0.42)
        self.assertAlmostEqual(y, -0.18)

    def test_gyro_legend_switch_raises_widgets_without_regridding(self):
        plot = object.__new__(StickPlot)
        plot.side = "left"
        plot.owner = SimpleNamespace(
            show_gyro_legend_var=Mock(get=Mock(return_value=True))
        )
        plot.gyro_legend = Mock()
        plot.metrics_label = Mock()
        plot._gyro_legend_visible = False
        telemetry = {
            "gyro": {"available": True, "target": "left"}
        }

        plot._update_gyro_legend_visibility(
            telemetry, True, "合成結果"
        )

        plot.gyro_legend.lift.assert_called_once_with()
        plot.gyro_legend.grid.assert_not_called()
        plot.metrics_label.grid_remove.assert_not_called()

    def test_disabled_gyro_does_not_show_combined_output_legend(self):
        plot = object.__new__(StickPlot)
        plot.side = "left"
        plot.owner = SimpleNamespace(
            show_gyro_legend_var=Mock(get=Mock(return_value=True))
        )
        plot.gyro_legend = Mock()
        plot.metrics_label = Mock()
        plot._gyro_legend_visible = True
        telemetry = {
            "gyro": {
                "available": True,
                "target": "left",
                "activation_mode": "OFF",
            }
        }

        plot._update_gyro_legend_visibility(
            telemetry, True, "合成結果"
        )

        plot.gyro_legend.lift.assert_not_called()
        plot.metrics_label.lift.assert_called_once_with()

    def test_disabled_gyro_is_removed_from_source_choices(self):
        tester = object.__new__(GamepadTestWindow)
        tester.latest_telemetry = {
            "gyro": {
                "available": True,
                "target": "left",
                "activation_mode": "OFF",
            }
        }
        tester._selected_device = Mock(
            return_value=SimpleNamespace(kind="s2p")
        )
        tester.plots = {
            "left": Mock(),
            "right": Mock(),
        }

        tester._configure_source_controls()

        tester.plots["left"].set_source_capability.assert_called_once_with(
            True, False
        )
        tester.plots["right"].set_source_capability.assert_called_once_with(
            True, False
        )


class NativeSamplerTests(unittest.TestCase):
    def test_isolated_enumeration_restores_winmm_capabilities(self):
        devices, caps_by_index = _decode_isolated_enumeration({
            "devices": [{
                "key": "winmm:2",
                "kind": "winmm",
                "index": 2,
                "name": "Hotplug Pad",
                "supports_rumble": False,
                "input_profile": "generic",
            }],
            "winmm_caps": {
                "2": {
                    "wMid": 0x1234,
                    "wPid": 0x5678,
                    "szPname": "Hotplug Pad",
                    "wXmin": 0,
                    "wXmax": 65535,
                },
            },
        })

        self.assertEqual(devices[0].key, "winmm:2")
        self.assertEqual(caps_by_index[2].wPid, 0x5678)
        self.assertEqual(caps_by_index[2].szPname, "Hotplug Pad")

    def test_sampler_uses_isolated_enumeration_when_requested(self):
        device = GamepadDevice(
            "xinput:1", "xinput", 1, "XInput Gamepad 2", True
        )
        caps = JOYCAPSW()
        caps.szPname = "Pad"
        backend = SimpleNamespace(_winmm_caps={})
        sampler = NativeGamepadSampler(
            backend, isolate_enumeration=True
        )

        with patch(
            "gamepad_devices.enumerate_windows_gamepads_isolated",
            return_value=((device,), {3: caps}),
        ):
            result = sampler.enumerate_devices(excluded_xinput_slot=0)

        self.assertEqual(result, [device])
        self.assertIs(backend._winmm_caps[3], caps)

    def test_stop_retains_live_thread_reference_after_timeout(self):
        sampler = NativeGamepadSampler(SimpleNamespace())
        thread = Mock()
        thread.is_alive.return_value = True
        sampler._thread = thread

        self.assertFalse(sampler.stop(timeout=0.01))
        self.assertIs(sampler._thread, thread)
        thread.join.assert_called_once_with(timeout=0.01)

        thread.is_alive.return_value = False
        self.assertTrue(sampler.stop(timeout=0.01))
        self.assertIsNone(sampler._thread)

    def test_start_waits_for_stop_requested_generation_to_exit(self):
        sampler = NativeGamepadSampler(SimpleNamespace())
        old_thread = Mock()
        old_thread.is_alive.return_value = True
        sampler._thread = old_thread
        sampler._stop.set()

        self.assertFalse(sampler.start())
        self.assertTrue(sampler._stop.is_set())

        old_thread.is_alive.return_value = False
        new_thread = Mock()
        with patch(
            "gamepad_devices.threading.Thread",
            return_value=new_thread,
        ):
            self.assertTrue(sampler.start())

        self.assertIs(sampler._thread, new_thread)
        self.assertFalse(sampler._stop.is_set())
        new_thread.start.assert_called_once_with()

    def test_generic_rate_and_samples_are_independent_of_drawing(self):
        clock = [10.0]
        states = [
            GamepadState(
                packet_number=index,
                buttons_mask=0,
                buttons=(),
                left=(index / 10.0, 0.0),
                right=(0.0, 0.0),
                left_trigger=0.0,
                right_trigger=0.0,
            )
            for index in (1, 2, 3)
        ]
        backend = SimpleNamespace(
            read_state=Mock(side_effect=states)
        )
        sampler = NativeGamepadSampler(
            backend,
            clock=lambda: clock[0],
        )
        sampler.set_device(GamepadDevice(
            key="xinput:0",
            kind="xinput",
            index=0,
            name="XInput 手把 1",
            supports_rumble=True,
        ))

        for _ in range(3):
            sampler._poll_once()
            clock[0] += 0.005
        latest, samples, rate = sampler.read_snapshot()

        self.assertEqual(latest.packet_number, 3)
        self.assertEqual(len(samples), 3)
        self.assertAlmostEqual(rate, 200.0)

    def test_repeated_xinput_packet_is_not_counted_twice(self):
        state = GamepadState(
            packet_number=7,
            buttons_mask=0,
            buttons=(),
            left=(0.0, 0.0),
            right=(0.0, 0.0),
            left_trigger=0.0,
            right_trigger=0.0,
        )
        backend = SimpleNamespace(read_state=Mock(return_value=state))
        sampler = NativeGamepadSampler(backend)
        sampler.set_device(GamepadDevice(
            key="xinput:0",
            kind="xinput",
            index=0,
            name="XInput 手把 1",
            supports_rumble=True,
        ))

        sampler._poll_once()
        sampler._poll_once()
        _latest, samples, rate = sampler.read_snapshot()

        self.assertEqual(len(samples), 1)
        self.assertIsNone(rate)

    def test_xinput_packet_gaps_preserve_updates_between_polls(self):
        clock = [20.0]
        states = [
            GamepadState(
                packet_number=packet,
                buttons_mask=0,
                buttons=(),
                left=(0.0, 0.0),
                right=(0.0, 0.0),
                left_trigger=0.0,
                right_trigger=0.0,
            )
            for packet in (100, 108, 116)
        ]
        sampler = NativeGamepadSampler(
            SimpleNamespace(read_state=Mock(side_effect=states)),
            clock=lambda: clock[0],
        )
        sampler.set_device(GamepadDevice(
            key="xinput:0",
            kind="xinput",
            index=0,
            name="XInput 手把 1",
            supports_rumble=True,
        ))

        for _ in states:
            sampler._poll_once()
            clock[0] += 0.001
        _latest, samples, rate = sampler.read_snapshot()

        self.assertEqual(len(samples), 3)
        self.assertAlmostEqual(rate, 8000.0)


if __name__ == "__main__":
    unittest.main()
