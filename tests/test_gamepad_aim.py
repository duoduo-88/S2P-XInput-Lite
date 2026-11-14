import gc
import math
import sys
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import Mock, patch


class AimModelTests(unittest.TestCase):
    def test_3d_stick_turn_speed_is_independent_per_axis_in_every_drill(self):
        from gamepad_aim_model import ShootingTest, TimedShootingTest, TrackingTest

        for drill in (ShootingTest, TimedShootingTest, TrackingTest):
            with self.subTest(drill=drill.__name__):
                test = drill(stick_turn_speed=(360, 60))
                test.start()
                test.turn((1.0, 0.5), 0.1)
                self.assertAlmostEqual(math.degrees(test.yaw), 36)
                self.assertAlmostEqual(math.degrees(test.pitch), 3)
                test.look(0.1, 0.2)
                self.assertAlmostEqual(test.yaw, math.radians(36) + 0.1)
                self.assertAlmostEqual(test.pitch, math.radians(3) + 0.2)

    def test_small_stick_output_moves_cursor_linearly(self):
        from gamepad_aim import CursorTest

        test = CursorTest()
        test.move((0.02, 0.04), 0.5, 800, 400)
        self.assertAlmostEqual(test.x, 404)
        self.assertAlmostEqual(test.y, 192)

    def test_cursor_score_uses_click_position_and_replays_target_sequence(self):
        from gamepad_aim import CursorTest

        test = CursorTest()
        test.start()
        first_target = test.target(800, 400)
        x, y, _radius = first_target
        test.click(x + 30, y, 800, 400)
        self.assertEqual((test.hits, test.shots, test.mean_error), (0, 1, 30))
        self.assertEqual(test.target(800, 400), first_target)
        test.click(x, y, 800, 400)
        self.assertEqual((test.hits, test.shots, test.mean_error), (1, 2, 15))
        self.assertNotEqual(test.target(800, 400), first_target)
        test.start()
        self.assertEqual(test.target(800, 400), first_target)

    def test_tracking_uses_the_same_linear_rate_across_frame_splits(self):
        from gamepad_aim import TrackingTest

        coarse = TrackingTest()
        fine = TrackingTest()
        coarse.advance((0.02, 0.04), 0.1)
        for _ in range(10):
            fine.advance((0.02, 0.04), 0.01)
        self.assertAlmostEqual(math.degrees(coarse.yaw), 0.28)
        self.assertAlmostEqual(math.degrees(coarse.pitch), 0.46)
        self.assertAlmostEqual(coarse.yaw, fine.yaw)
        self.assertAlmostEqual(coarse.pitch, fine.pitch)

    def test_tracking_score_weights_elapsed_time_and_stops_at_30_seconds(self):
        from gamepad_aim import TrackingTest

        test = TrackingTest()
        test.start()
        # The path starts straight ahead. First sample on target, then 90° off.
        test.record(0.25, 0, True)
        test.record(30, 90, False)
        self.assertEqual(test.elapsed, 30)
        self.assertFalse(test.running)
        self.assertAlmostEqual(test.on_target_percent, 100 * 0.25 / 30)
        self.assertAlmostEqual(test.mean_error, 90 * 29.75 / 30)

    def test_projection_and_angular_error_respond_to_3d_camera(self):
        from gamepad_aim import TrackingTest

        test = TrackingTest()
        position = (1, 0, 1)
        camera = test.camera_transform()
        self.assertAlmostEqual(test.target_error(camera(position)), 45)
        test.yaw = math.pi / 4
        camera = test.camera_transform()
        self.assertAlmostEqual(test.target_error(camera(position)), 0)
        test.yaw = math.pi
        self.assertLess(test.camera_transform()(position)[2], 0)

    def test_3d_shots_measure_angle_and_only_advance_on_hit(self):
        from gamepad_aim import ShootingTest

        test = ShootingTest()
        test.start()
        first = test.target_position()
        test.shoot()
        self.assertEqual((test.hits, test.shots), (0, 1))
        self.assertAlmostEqual(test.mean_error, 14.036243, places=5)
        self.assertEqual(test.target_position(), first)
        test.yaw = math.atan2(2, 8)
        test.shoot()
        self.assertEqual((test.hits, test.shots, test.accuracy), (1, 2, 50))
        self.assertNotEqual(test.target_position(), first)
        test.start()
        self.assertEqual(test.target_position(), first)

    def test_3d_target_round_stops_after_ten_hits(self):
        from gamepad_aim import ShootingTest

        test = ShootingTest()
        test.start()
        for _ in range(10):
            x, y, z = test.target_position()
            test.yaw = math.atan2(x, z)
            test.pitch = math.atan2(y, math.hypot(x, z))
            test.advance((0.0, 0.0), 0.25)
            test.shoot()
        test.advance((0.0, 0.0), 1)
        test.shoot()
        self.assertEqual((test.hits, test.shots, test.elapsed), (10, 10, 2.5))
        self.assertFalse(test.running)
        self.assertIsNone(test.target_position())


class AimOutputTests(unittest.TestCase):
    def test_aim_rumble_release_keeps_other_rumble_layers(self):
        from gamepad_test_window import GamepadTestWindow

        window = object.__new__(GamepadTestWindow)
        window._manual_rumble = (0.25, 0.0)
        window._rumble_layers = {1: (0.0, 0.25)}
        writes = []
        window._set_rumble = lambda left, right: writes.append((left, right)) or True
        window._set_aim_rumble(0.25, 0.5)
        window._set_aim_rumble(0.0, 0.0)
        self.assertEqual(writes, [(0.5, 0.75), (0.25, 0.25)])
        self.assertEqual(window._rumble_layers, {1: (0.0, 0.25)})

    def test_failed_hit_update_preserves_the_active_contact_pulse_for_release(self):
        from gamepad_test_window import GamepadTestWindow

        window = object.__new__(GamepadTestWindow)
        window._manual_rumble = (0.0, 0.0)
        window._rumble_layers = {}
        results = iter((True, False, True))
        writes = []
        window._set_rumble = lambda left, right: writes.append((left, right)) or next(results)
        self.assertTrue(window._set_aim_rumble(0.1, 0.2))
        self.assertFalse(window._set_aim_rumble(0.5, 0.5))
        window._set_aim_rumble(0.0, 0.0)
        self.assertEqual(writes, [(0.1, 0.2), (0.5, 0.5), (0.0, 0.0)])

    def test_aim_reuses_native_sample_without_draining_monitor_history(self):
        from gamepad_devices import GamepadDevice, GamepadState, NativeGamepadSampler
        from gamepad_test_window import GamepadTestWindow

        first = GamepadDevice("winmm:0", "winmm", 0, "First", False)
        second = GamepadDevice("winmm:1", "winmm", 1, "Second", False)
        state = GamepadState(1, 0, (), (0.0, 0.0), (0.125, -0.25), None, None)
        sampler = NativeGamepadSampler(SimpleNamespace(read_state=lambda _device: state))
        sampler.set_device(first)
        sampler._poll_once()
        window = object.__new__(GamepadTestWindow)
        window.native_sampler = sampler
        window.backend = SimpleNamespace(read_state=lambda _device: None)
        window._selected_device = lambda: first
        first_key, first_state = window._read_aim_output()
        self.assertIs(first_state, state)
        window._selected_device = lambda: second
        self.assertIsNone(window._read_aim_output())
        self.assertEqual(len(sampler.read_snapshot()[1]), 1)
        window._selected_device = lambda: first
        sampler.backend.read_state = Mock(side_effect=(None, state))
        sampler._poll_once()
        sampler._poll_once()
        self.assertNotEqual(window._read_aim_output()[0], first_key)

    def test_raw_hid_aim_uses_the_paired_windows_output(self):
        from gamepad_devices import GamepadDevice, GamepadState, NativeGamepadSampler
        from gamepad_test_window import GamepadTestWindow

        raw = GamepadDevice("raw:0", "raw_hid", 0, "Raw", False)
        paired = GamepadDevice("xinput:1", "xinput", 1, "Paired", True)
        state = GamepadState(1, 0, ("START",), (0.0, 0.0), (0.125, -0.25), 0.0, 1.0)
        window = object.__new__(GamepadTestWindow)
        window.native_sampler = NativeGamepadSampler(SimpleNamespace(read_state=lambda _device: state))
        window._selected_device = lambda: raw
        window._native_device_for_raw_hid = Mock(return_value=paired)
        window._configure_native_sampler()
        window.native_sampler._poll_once()
        self.assertEqual(window._read_aim_output()[1], state)
        self.assertEqual(len(window.native_sampler.read_snapshot()[1]), 1)
        window._native_device_for_raw_hid.return_value = None
        window._configure_native_sampler()
        self.assertIsNone(window._read_aim_output())

    def test_s2p_aim_reads_the_published_xinput_slot(self):
        from gamepad_devices import GamepadDevice, GamepadState
        from gamepad_test_window import GamepadTestWindow

        state = GamepadState(4, 0, (), (0.0, 0.0), (0.125, -0.25), 0.0, 0.5)
        window = object.__new__(GamepadTestWindow)
        window._aim_bridge_packet = None
        window._aim_bridge_generation = 0
        window._selected_device = lambda: GamepadDevice("s2p", "s2p", 2, "S2P", True)
        window.latest_telemetry = {"xinput_slot": 2, "right": {"final": (1.0, 1.0)}}
        window._telemetry_is_fresh = lambda _telemetry: True
        window.backend = SimpleNamespace(read_state=lambda device: (
            state if (device.kind, device.index) == ("xinput", 2) else None
        ))
        self.assertEqual(window._read_aim_output(), (("s2p", 2, 0), state))
        window.latest_telemetry["xinput_slot"] = 1
        self.assertIsNone(window._read_aim_output())

    def test_s2p_aim_distinguishes_packet_reset_from_normal_wrap(self):
        from gamepad_devices import GamepadDevice, GamepadState
        from gamepad_test_window import GamepadTestWindow

        window = object.__new__(GamepadTestWindow)
        window._aim_bridge_packet = None
        window._aim_bridge_generation = 0
        window._selected_device = lambda: GamepadDevice("s2p", "s2p", 2, "S2P", True)
        window.latest_telemetry = {"xinput_slot": 2}
        window._telemetry_is_fresh = lambda _telemetry: True
        packets = (0xFFFFFFFF, 0, 100, 4, 5)
        window.backend = SimpleNamespace(read_state=Mock(side_effect=[
            GamepadState(packet, 0, (), (0.0, 0.0), (0.0, 0.0), 0.0, 0.0)
            for packet in packets
        ]))

        keys = [window._read_aim_output()[0] for _packet in packets]

        self.assertEqual(keys[0], keys[1])
        self.assertEqual(keys[1], keys[2])
        self.assertNotEqual(keys[2], keys[3])
        self.assertEqual(keys[3], keys[4])


class AimPaneTests(unittest.TestCase):
    def setUp(self):
        from gamepad_aim_settings import AimSettings

        # Ordinary UI tests use a window. Fullscreen tests supply a fake display mode.
        defaults = patch("gamepad_aim.AimSettings", return_value=AimSettings(fullscreen=False))
        display = patch("gamepad_aim.activate_fullscreen_mode", side_effect=AssertionError("Unexpected display change"))
        for patcher in (defaults, display):
            patcher.start()
            self.addCleanup(patcher.stop)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(gc.collect)
        self.addCleanup(self.root.destroy)
        self.addCleanup(self.root.update_idletasks)
        self.root.withdraw()

    def test_escape_closes_both_compensation_windows_from_either_focus(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None)
        self.root.deiconify()
        self.root.update()
        for focus_in_overlay in (False, True):
            with self.subTest(focus_in_overlay=focus_in_overlay):
                pane._open_compensation_preview()
                overlay = pane.compensation_window
                self.assertTrue(pane.test_open)
                overlay.start()
                self.assertIsNotNone(overlay.job)
                widget = overlay.adjusted[0] if focus_in_overlay else pane.canvas
                widget.focus_force()
                self.root.update()
                widget.event_generate("<Escape>")
                self.root.update()
                self.assertFalse(overlay.window.winfo_exists())
                self.assertFalse(pane.test_open)
                self.assertIsNone(overlay.job)
                self.assertIsNone(overlay.measurement)

    def test_compensation_overlay_measures_two_3d_camera_speeds(self):
        from gamepad_aim import AimTestPane, VIEW_MARGIN
        from gamepad_aim_settings import AimSettings

        read_output = Mock(return_value=None)
        pane = AimTestPane(self.root, str, read_output)
        self.root.deiconify()
        self.root.update()
        pane._save_settings(AimSettings(fullscreen=True, window_size="800x450"))
        with patch("gamepad_aim.fullscreen_monitor_bounds", return_value=(50, 40, 1050, 640)):
            pane._open_compensation_preview()
        overlay = pane.compensation_window
        read_output.reset_mock()
        self.root.update()
        self.assertTrue(overlay.window.winfo_viewable())
        self.assertTrue(overlay.window.attributes("-topmost"))
        self.assertTrue(pane.window.winfo_viewable())
        self.assertTrue(pane.window.overrideredirect())
        self.assertIsNone(pane._fullscreen_mode)
        self.assertEqual(
            (pane.window.winfo_rootx(), pane.window.winfo_rooty(),
             pane.window.winfo_width(), pane.window.winfo_height()),
            (50, 40, 1000, 600),
        )
        self.assertGreaterEqual(overlay.window.winfo_rootx(), pane.window.winfo_rootx())
        self.assertLess(overlay.window.winfo_rootx(), pane.window.winfo_rootx() + pane.window.winfo_width())
        self.assertGreaterEqual(overlay.window.winfo_rooty(), pane.window.winfo_rooty())
        self.assertLess(overlay.window.winfo_rooty(), pane.window.winfo_rooty() + pane.window.winfo_height())
        self.assertEqual(
            overlay.window.winfo_rootx() + overlay.window.winfo_width(),
            pane.window.winfo_rootx() + pane.window.winfo_width() - VIEW_MARGIN,
        )
        self.assertEqual(overlay.window.winfo_y(), pane.window.winfo_rooty() + VIEW_MARGIN)

        overlay.adjusted[0].set("240")
        overlay.adjusted[1].set("1080")
        overlay.measure_button.invoke()
        for index, angle in enumerate((9.8, 8.05, 16.8, 75.6)):
            overlay.window.after_cancel(overlay.job)
            with patch("gyro_compensation_window.time.perf_counter", return_value=overlay.end_at):
                overlay._tick()
            self.assertAlmostEqual(overlay.angles[index], angle)
            if index < 3:
                overlay.window.after_cancel(overlay.job)
                overlay._start_turn()
        self.assertIn("建議 X 補償倍率 0.58", overlay.result.get())
        self.assertIn("建議 Y 補償倍率 0.11", overlay.result.get())
        self.assertEqual(pane.settings.stick_horizontal_speed, 140)
        self.assertEqual(pane.settings.stick_vertical_speed, 115)
        pane._sample_input(104)
        read_output.assert_called_once_with()
        self.assertFalse(pane.history.records)
        overlay.start()
        self.assertIsNotNone(overlay.job)
        pane.close_test()
        self.assertIsNone(overlay.job)
        self.assertIsNone(overlay.measurement)

    def test_preview_close_button_and_window_close_cancel_pending_measurement(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None)
        self.root.deiconify()
        self.root.update()
        for close_preview in (False, True):
            with self.subTest(close_preview=close_preview):
                pane._open_compensation_preview()
                overlay = pane.compensation_window
                overlay.start()
                self.assertIsNotNone(overlay.job)
                if close_preview:
                    overlay.close_button.invoke()
                else:
                    overlay.window.tk.call(overlay.window.protocol("WM_DELETE_WINDOW"))
                self.assertIsNone(overlay.job)
                self.assertIsNone(overlay.measurement)
                self.assertFalse(pane.test_open)
                self.assertFalse(overlay.window.winfo_exists())

    def test_controller_b_closes_both_preview_windows_from_either_focus(self):
        from gamepad_aim import AimTestPane

        neutral = SimpleNamespace(buttons=(), right=(1, 0), right_trigger=1)
        held_b = SimpleNamespace(buttons=("B",), right=(1, 0), right_trigger=1)
        read_input = Mock(return_value=None)
        pane = AimTestPane(self.root, str, lambda: None, read_input=read_input)
        self.root.deiconify()
        self.root.update()
        for overlay_focused in (False, True):
            with self.subTest(overlay_focused=overlay_focused):
                pane._input_events.append((99, SimpleNamespace(buttons=("START",))))
                pane._open_compensation_preview()
                overlay = pane.compensation_window
                self.assertFalse(pane._input_events)
                overlay.start()
                widget = overlay.adjusted[0] if overlay_focused else pane.canvas
                widget.focus_force()
                self.root.update_idletasks()

                now = time.perf_counter()
                read_input.return_value = ("device", held_b, (), 125)
                pane._sample_input(now)
                self.assertTrue(pane.test_open)
                read_input.return_value = (
                    "device", neutral, ((now + 0.01, neutral), (now + 0.02, held_b), (now + 0.03, neutral)), 125,
                )
                pane._sample_input(now + 0.03)
                self.assertFalse(pane.test_open)
                self.assertFalse(overlay.window.winfo_exists())
                self.assertIsNone(overlay.job)
                self.assertFalse(pane.cancel_pressed)
                self.assertEqual(pane.scene.yaw, 0.0)

    def test_preview_recognizes_new_b_press_in_first_batch_after_reconnect(self):
        from gamepad_aim import AimTestPane

        released = SimpleNamespace(buttons=(), right=(0.0, 0.0), right_trigger=0.0)
        held = SimpleNamespace(buttons=("B",), right=(0.0, 0.0), right_trigger=0.0)
        read_input = Mock(return_value=None)
        pane = AimTestPane(self.root, str, lambda: None, read_input=read_input)
        self.root.deiconify()
        self.root.update()
        opened = time.perf_counter()
        with patch("gamepad_aim.time.perf_counter", return_value=opened):
            pane._open_compensation_preview()
        overlay = pane.compensation_window
        with patch.object(pane.window, "focus_displayof", return_value=pane.canvas):
            # A B press from before the preview opened is only a held state.
            read_input.return_value = (
                "old", held, ((opened - 0.03, released), (opened - 0.01, held)), None,
            )
            pane._sample_input(opened + 0.01)
            self.assertTrue(pane.test_open)

            read_input.return_value = None
            pane._sample_input(opened + 0.02)

            # The first report can also arrive with B already held.
            read_input.return_value = ("replacement", held, ((opened + 0.025, held),), None)
            pane._sample_input(opened + 0.026)
            self.assertTrue(pane.test_open)

            # On the new connection, the initial report is neutral and the
            # user presses B before Tk gets to drain the first batch.
            read_input.return_value = (
                "reconnected", held, ((opened + 0.03, released), (opened + 0.04, held)), None,
            )
            pane._sample_input(opened + 0.05)

        self.assertFalse(pane.test_open)
        self.assertFalse(overlay.window.winfo_exists())

    def test_switching_aim_dimension_closes_the_compensation_preview(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None)
        self.root.deiconify()
        self.root.update()
        pane._open_compensation_preview()
        overlay = pane.compensation_window
        overlay.start()
        dimension = next(widget for widget in pane.dimension_controls.winfo_children()
                         if isinstance(widget, ttk.Radiobutton) and widget.cget("value") == "2d")
        dimension.invoke()
        self.assertFalse(pane.mode_is_3d)
        self.assertFalse(pane.test_open)
        self.assertFalse(overlay.window.winfo_exists())
        self.assertIsNone(overlay.job)

    def test_held_start_during_preview_does_not_start_a_round_when_it_closes(self):
        from gamepad_aim import AimTestPane

        neutral = SimpleNamespace(buttons=(), right=(0, 0), right_trigger=0)
        held_start = SimpleNamespace(buttons=("START",), right=(0, 0), right_trigger=0)
        read_output = Mock(return_value=("device", neutral))
        pane = AimTestPane(self.root, str, read_output)
        self.root.deiconify()
        self.root.update()
        pane._sample_input(100)
        pane._open_compensation_preview()
        read_output.return_value = ("device", held_start)
        pane._sample_input(101)
        pane.compensation_window.close_button.invoke()

        with patch.object(pane, "_start_shortcut") as start:
            pane._sample_input(102)
            start.assert_not_called()
            read_output.return_value = ("device", neutral)
            pane._sample_input(103)
            read_output.return_value = ("device", held_start)
            pane._sample_input(104)
            start.assert_called_once_with(at=104)

    def test_compensation_preview_uses_current_settings_without_saving_adjustments(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "aim.json"
        original = AimSettings(fullscreen=False, stick_horizontal_speed=180, stick_vertical_speed=95)
        original.save(path)
        with patch("gamepad_aim.AimSettings", AimSettings):
            pane = AimTestPane(self.root, str, lambda: None, settings_path=path)
        self.root.deiconify()
        self.root.update()
        pane._open_compensation_preview()
        overlay = pane.compensation_window
        self.assertEqual(overlay.speed_source.get(), "original")
        self.assertEqual(overlay.original, (140, 115))
        self.assertEqual(tuple(entry.get() for entry in overlay.adjusted), ("180", "95"))
        overlay.adjusted[1].set("1081")
        overlay.measure_button.invoke()
        self.assertIsNone(overlay.measurement)
        self.assertIn("1080", overlay.status.get())
        overlay.adjusted[1].set("120")
        overlay.measure_button.invoke()
        self.assertIsNotNone(overlay.measurement)
        pane._open_settings()
        self.assertFalse(overlay.window.winfo_exists())
        self.assertIsNone(overlay.job)
        self.assertEqual(AimSettings.load(path), original)

    def test_preview_hides_start_and_ignores_shortcuts_in_either_window(self):
        from gamepad_aim import AimTestPane

        neutral = SimpleNamespace(buttons=(), right=(0, 0), right_trigger=0)
        start = SimpleNamespace(buttons=("START",), right=(0, 0), right_trigger=0)
        read_input = Mock(return_value=None)
        pane = AimTestPane(self.root, str, lambda: None, read_input=read_input)
        self.root.deiconify()
        self.root.update()
        for shortcut in ("Enter", "START"):
            for overlay_focused in (False, True):
                with self.subTest(shortcut=shortcut, overlay_focused=overlay_focused):
                    read_input.return_value = None
                    pane._open_compensation_preview()
                    overlay = pane.compensation_window
                    self.assertFalse(pane.restart_button.winfo_ismapped())
                    self.assertFalse(pane.close_button.winfo_ismapped())
                    widget = overlay.adjusted[0] if overlay_focused else pane.canvas
                    widget.focus_force()
                    self.root.update_idletasks()
                    if shortcut == "Enter":
                        widget.event_generate("<KeyRelease-Return>")
                    else:
                        read_input.return_value = ("device", start, (), 125)
                        pane._sample_input(100)
                        self.assertIsNone(pane.countdown_until)
                        read_input.return_value = ("device", neutral, ((101, neutral), (102, start), (103, neutral)), 125)
                        pane._sample_input(103)
                    self.assertIsNone(pane.countdown_until)
                    self.assertFalse(pane.scene.running)
                    self.assertTrue(overlay.window.winfo_exists())
                    overlay.close_button.invoke()

    def test_preview_speed_changes_take_effect_during_free_look(self):
        import math

        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        sample = SimpleNamespace(buttons=(), right=(0.5, 0.5), right_trigger=0)
        pane = AimTestPane(self.root, str, lambda: ("device", sample))
        pane._save_settings(AimSettings(stick_horizontal_speed=560, stick_vertical_speed=230))
        self.root.deiconify()
        self.root.update()
        pane._open_compensation_preview()
        overlay = pane.compensation_window
        pane.canvas.focus_force()
        self.root.update_idletasks()
        pane._sample_input(100)
        pane._simulate_tick(100)
        yaw, pitch = pane.scene.yaw, pane.scene.pitch
        pane._simulate_tick(100.1)
        self.assertAlmostEqual(pane.scene.yaw - yaw, math.radians(7))
        self.assertAlmostEqual(pane.scene.pitch - pitch, math.radians(5.75))
        overlay.speed_options[1].invoke()
        yaw, pitch = pane.scene.yaw, pane.scene.pitch
        pane._simulate_tick(100.2)
        self.assertAlmostEqual(pane.scene.yaw - yaw, math.radians(28))
        self.assertAlmostEqual(pane.scene.pitch - pitch, math.radians(11.5))
        overlay.adjusted[0].set("400")
        yaw = pane.scene.yaw
        pane._simulate_tick(100.3)
        self.assertAlmostEqual(pane.scene.yaw - yaw, math.radians(20))
        overlay.speed_options[0].invoke()
        yaw = pane.scene.yaw
        pane._simulate_tick(100.4)
        self.assertAlmostEqual(pane.scene.yaw - yaw, math.radians(7))
        self.assertEqual(pane.settings.stick_horizontal_speed, 560)

    def test_preview_camera_keeps_up_with_display_during_stick_motion(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_timing import IDLE_PERIOD

        sample = SimpleNamespace(buttons=(), right=(0.5, 0.0), right_trigger=0)
        pane = AimTestPane(self.root, str, lambda: ("device", sample), display_hz=lambda _window: 120)
        self.root.deiconify()
        self.root.update()
        pane._open_compensation_preview()
        pane.canvas.focus_force()
        self.root.update_idletasks()
        pane._cancel_tick()
        start = 100.0
        pane.last_frame = pane.next_frame = pane.next_draw = start
        pane._rate_check_at = pane.label_update_at = start + 2
        frame_yaws = []
        with (
            patch.object(pane, "_schedule"),
            patch.object(pane, "_draw", side_effect=lambda: frame_yaws.append(pane.scene.yaw)),
        ):
            for step in range(1000):
                with patch("gamepad_aim.time.perf_counter", return_value=start + step / 1000):
                    pane._tick()
            moved_frames = sum(a != b for a, b in zip(frame_yaws, frame_yaws[1:]))
            self.assertGreaterEqual(moved_frames, 110)

            sample.right = (0.0, 0.0)
            with patch("gamepad_aim.time.perf_counter", return_value=start + 1.02):
                pane._tick()
            self.assertAlmostEqual(pane.next_frame, start + 1.02 + IDLE_PERIOD)

    def test_preview_shows_one_world_fixed_landmark_then_regular_test_spawns_targets(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane.mode.set("timed-shooting")
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450"))
        self.root.deiconify()
        self.root.update()
        pane._open_compensation_preview()
        pane._draw()
        initial, second = pane._ball_bounds
        self.assertIsNotNone(initial)
        self.assertIsNone(second)
        self.assertAlmostEqual(initial[0], pane.size[0] / 2)
        pane._turn_compensation_preview("X", 15)
        pane._draw()
        self.assertLess(pane._ball_bounds[0][0], initial[0])
        self.assertIsNone(pane._ball_bounds[1])
        pane._turn_compensation_preview("X", -15)
        pane._draw()
        self.assertEqual(pane._ball_bounds, [initial, None])
        self.assertIsNone(pane.round_basis)

        pane.compensation_window.close_button.invoke()
        pane.start_button.invoke()
        self.assertTrue(pane.restart_button.winfo_ismapped())
        pane._advance_countdown(pane.countdown_until)
        self.assertEqual(len(pane.scene.target_positions()), 2)

    def test_preview_measurement_applies_both_axes_without_compounding_repeated_runs(self):
        from gamepad_aim import AimTestPane
        from gyro_compensation import CompensationSnapshot

        snapshot = CompensationSnapshot("Alpha", {"X": 1.0, "Y": 1.0}, {"X": 1.0, "Y": 1.0})
        apply = Mock(return_value=None)
        pane = AimTestPane(self.root, str, lambda: None, read_gyro_compensation=lambda: snapshot,
                           apply_gyro_compensation=apply)
        self.root.deiconify()
        self.root.update()
        pane._open_compensation_preview()
        overlay = pane.compensation_window

        def measure(horizontal, vertical):
            overlay.adjusted[0].set(horizontal)
            overlay.adjusted[1].set(vertical)
            overlay.measure_button.invoke()
            for index in range(4):
                overlay.window.after_cancel(overlay.job)
                with patch("gyro_compensation_window.time.perf_counter", return_value=overlay.end_at):
                    overlay._tick()
                if index < 3:
                    overlay.window.after_cancel(overlay.job)
                    overlay._start_turn()

        measure("560", "230")
        ratios = {"X": 0.25, "Y": 0.50}
        apply.assert_called_once_with(snapshot, ratios)
        self.assertEqual(overlay.current_multiplier_text["X"].get(), "X（水平）  0.25")
        self.assertIn("已寫入", overlay.status.get())
        measure("280", "230")
        apply.assert_called_with(snapshot.with_multipliers(ratios), {"X": 0.50, "Y": 0.50})
        self.assertEqual(apply.call_count, 2)
        measure("10", "230")
        self.assertEqual(apply.call_count, 2)
        self.assertIn("超出", overlay.status.get())

    def test_preview_reports_the_matching_live_reload_result_after_saving(self):
        from gamepad_aim import AimTestPane
        from gyro_compensation import CompensationSnapshot

        for state in ("success", "failed", "timeout", "close"):
            with self.subTest(state=state):
                read_status = Mock(return_value={
                    "settings_reload_request_id": "unrelated",
                    "settings_reload_state": "success",
                })
                pane = AimTestPane(
                    self.root, str, lambda: None,
                    read_gyro_compensation=lambda: CompensationSnapshot(None, {"X": 1.0, "Y": 1.0}),
                    apply_gyro_compensation=Mock(return_value="request"),
                    read_gyro_reload_status=read_status,
                )
                self.root.deiconify()
                self.root.update()
                pane._open_compensation_preview()
                overlay = pane.compensation_window
                overlay.adjusted[0].set("280")
                overlay.start()
                for index in range(4):
                    overlay.window.after_cancel(overlay.job)
                    with patch("gyro_compensation_window.time.perf_counter", return_value=overlay.end_at):
                        overlay._tick()
                    if index < 3:
                        overlay.window.after_cancel(overlay.job)
                        overlay._start_turn()
                self.assertIn("等待", overlay.status.get())
                overlay.window.after_cancel(overlay.reload_job)
                overlay._poll_reload("request")
                self.assertIn("等待", overlay.status.get())
                self.assertIsNotNone(overlay.reload_job)
                if state == "close":
                    overlay.close_button.invoke()
                    self.assertIsNone(overlay.reload_job)
                    continue
                overlay.window.after_cancel(overlay.reload_job)
                if state == "timeout":
                    with patch("gyro_compensation_window.time.perf_counter", return_value=overlay.reload_deadline):
                        overlay._poll_reload("request")
                    self.assertIn("未確認", overlay.status.get())
                else:
                    read_status.return_value = {
                        "settings_reload_request_id": "later-request",
                        "settings_reload_state": "success",
                        "settings_reload_results": {
                            "request": {"state": state, "message": "calibration is running"},
                        },
                    }
                    overlay._poll_reload("request")
                    self.assertIn("並套用" if state == "success" else "即時套用失敗", overlay.status.get())
                self.assertIsNone(overlay.reload_job)
                saved_feedback = overlay.status.get()
                self.assertEqual(overlay.current_multiplier_text["X"].get(), "X（水平）  0.50")
                frame = overlay.window.winfo_children()[0].winfo_children()[0]
                self.assertEqual(frame.cget("text"), "已儲存的補償倍率")
                overlay.adjusted[0].set("560")
                self.assertFalse(overlay.result.get())
                self.assertEqual(overlay.status.get(), saved_feedback)
                overlay.adjusted[0].set("invalid")
                overlay.start()
                self.assertIn("整數", overlay.status.get())
                overlay.adjusted[0].set("280")
                self.assertEqual(overlay.status.get(), saved_feedback)
                overlay.close_button.invoke()

    def test_preview_hands_off_to_s2p_and_minimizes_the_tester(self):
        from gamepad_aim import AimTestPane

        handoff = Mock()
        pane = AimTestPane(self.root, str, lambda: None, open_gyro_settings=handoff)
        self.root.deiconify()
        self.root.update()
        pane._open_compensation_preview()
        overlay = pane.compensation_window
        overlay.measure_button.invoke()
        with patch.object(self.root, "iconify") as iconify:
            overlay.settings_button.invoke()
        handoff.assert_called_once_with()
        iconify.assert_called_once_with()
        self.assertFalse(overlay.window.winfo_exists())
        self.assertIsNone(overlay.job)

    def test_calculator_keeps_both_axes_after_opening_s2p_settings(self):
        from gamepad_aim import AimTestPane
        from gyro_compensation import CompensationSnapshot

        handoff = Mock()
        pane = AimTestPane(self.root, str, lambda: None, open_gyro_settings=handoff,
                           read_gyro_compensation=lambda: CompensationSnapshot("test", {"X": 0.8, "Y": 1.25}))
        self.root.deiconify()
        self.root.update()
        with patch("gamepad_aim.messagebox.askyesno", return_value=False):
            pane._open_compensation_calculator()
        calculator = pane.compensation_calculator
        for value, text in zip(calculator.inputs["X"][1:], ("3", "2")):
            value.set(text)
        calculator.axis.set("Y")
        for value, text in zip(calculator.inputs["Y"][1:], ("2", "1")):
            value.set(text)
        expected = calculator.result.get()

        with patch.object(self.root, "iconify"):
            calculator.settings_button.invoke()
        handoff.assert_called_once_with()
        self.assertFalse(calculator.window.winfo_exists())

        with patch("gamepad_aim.messagebox.askyesno", return_value=False):
            pane._open_compensation_calculator()
        calculator = pane.compensation_calculator
        self.assertEqual(calculator.axis.get(), "Y")
        self.assertEqual(calculator.result.get(), expected)
        calculator.axis.set("X")
        self.assertEqual(tuple(value.get() for value in calculator.inputs["X"]), ("0.80", "3", "2"))
        self.assertIn("0.53", calculator.result.get())

    def test_switching_between_supported_controllers_during_prompt_aborts_the_reset(self):
        from gamepad_aim import AimTestPane
        from gyro_compensation import CompensationSnapshot

        reset = Mock()
        pane = AimTestPane(self.root, str, lambda: None, compensation_available=lambda: True,
                           read_gyro_compensation=lambda: CompensationSnapshot("Alpha", {"X": 0.8, "Y": 1.0}),
                           reset_gyro_compensation=reset)

        def switch_controller(*_args, **_kwargs):
            pane.close_gyro_compensation_for_device_change()
            return True

        with (
            patch("gamepad_aim.messagebox.askyesno", side_effect=switch_controller),
            patch("gamepad_aim.messagebox.showerror") as error,
        ):
            pane._open_compensation_calculator()

        reset.assert_not_called()
        error.assert_called_once()
        self.assertIsNone(pane.compensation_calculator)

    def test_calculator_blocks_settings_handoff_after_external_compensation_changes(self):
        from gamepad_aim import AimTestPane
        from gyro_compensation import CompensationSnapshot

        original = CompensationSnapshot("Alpha", {"X": 1.0, "Y": 1.0})
        for changed in (
            CompensationSnapshot("Beta", original.multipliers),
            original.with_multipliers({"X": 0.5, "Y": 1.0}),
            CompensationSnapshot("Alpha", original.multipliers, output_target="LEFT_STICK"),
        ):
            with self.subTest(changed=changed):
                read = Mock(return_value=original)
                handoff = Mock()
                pane = AimTestPane(
                    self.root, str, lambda: None, open_gyro_settings=handoff,
                    read_gyro_compensation=read,
                )
                pane._open_compensation_calculator()
                calculator = pane.compensation_calculator
                for field, value in zip(calculator.inputs["X"][1:], ("4", "2")):
                    field.set(value)
                self.assertIn("0.50", calculator.result.get())

                read.return_value = changed
                with patch.object(self.root, "iconify") as iconify:
                    calculator.settings_button.invoke()

                handoff.assert_not_called()
                iconify.assert_not_called()
                self.assertTrue(calculator.window.winfo_exists())
                self.assertIn("重新開啟", calculator.result.get())
                self.assertTrue(calculator.settings_button.instate(("disabled",)))
                calculator.inputs["X"][2].set("3")
                self.assertIn("重新開啟", calculator.result.get())
                calculator.window.destroy()

    def test_returning_to_calculator_hides_the_stale_recommendation(self):
        from gamepad_aim import AimTestPane
        from gyro_compensation import CompensationSnapshot

        read = Mock(return_value=CompensationSnapshot("Alpha", {"X": 1.0, "Y": 1.0}))
        pane = AimTestPane(self.root, str, lambda: None, read_gyro_compensation=read)
        self.root.deiconify()
        pane._open_compensation_calculator()
        calculator = pane.compensation_calculator
        calculator.inputs["X"][1].set("4")
        calculator.inputs["X"][2].set("2")
        self.assertIn("0.50", calculator.result.get())

        read.return_value = CompensationSnapshot("Beta", {"X": 1.0, "Y": 1.0})
        calculator.window.event_generate("<FocusIn>")
        self.assertIn("重新開啟", calculator.result.get())
        calculator.window.destroy()
        pane._open_compensation_calculator()
        self.assertFalse(pane.compensation_calculator.result.get())

    def test_preview_and_calculator_offer_to_restore_changed_compensation(self):
        from gamepad_aim import AimTestPane
        from gyro_compensation import CompensationSnapshot

        snapshot = CompensationSnapshot("test", {"X": 0.8, "Y": 1.25})
        reset = Mock()
        pane = AimTestPane(self.root, str, lambda: None,
                           read_gyro_compensation=lambda: snapshot, reset_gyro_compensation=reset)
        self.root.deiconify()
        self.root.update()
        with patch("gamepad_aim.messagebox.askyesno", side_effect=(False, True)) as warning:
            pane._open_compensation_preview()
            overlay = pane.compensation_window
            self.assertEqual(overlay.original_multipliers, snapshot.multipliers)
            overlay.start()
            for index in range(4):
                overlay.window.after_cancel(overlay.job)
                with patch("gyro_compensation_window.time.perf_counter", return_value=overlay.end_at):
                    overlay._tick()
                if index < 3:
                    overlay.window.after_cancel(overlay.job)
                    overlay._start_turn()
            self.assertIn("建議 X 補償倍率 0.80", overlay.result.get())
            self.assertIn("建議 Y 補償倍率 1.25", overlay.result.get())
            pane._close_test_and_compensation()
            pane._open_compensation_calculator()
        self.assertEqual(warning.call_count, 2)
        reset.assert_called_once_with(snapshot)
        calculator = pane.compensation_calculator
        self.assertTrue(calculator.entries[0].instate(("readonly",)))
        self.assertEqual(calculator.entries[0].get(), "1.00")

    def test_compensation_preview_can_retry_after_an_open_error(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None)
        self.root.deiconify()
        self.root.update()
        with (
            patch.object(pane, "_open_test_window", side_effect=OSError("display restore failed")),
            self.assertLogs("gamepad_aim", level="ERROR"),
        ):
            pane._open_compensation_preview()
        self.assertFalse(pane.test_open)
        self.assertFalse(pane._compensation_preview)
        self.assertIsNone(pane.compensation_window)

        pane._open_compensation_preview()
        self.assertTrue(pane.test_open)
        self.assertTrue(pane.compensation_window.window.winfo_exists())

    def test_reopening_preview_keeps_result_and_alignment(self):
        from gamepad_aim import AimTestPane, VIEW_MARGIN
        from gamepad_aim_settings import AimSettings
        from localization import translate_text

        pane = AimTestPane(self.root, lambda key: translate_text(key, "en"), lambda: None)
        self.root.deiconify()
        self.root.update()
        pane._save_settings(AimSettings(stick_horizontal_speed=1080, stick_vertical_speed=1080))
        with patch("gamepad_aim.fullscreen_monitor_bounds", return_value=(0, 0, 800, 600)):
            pane._open_compensation_preview()
        overlay = pane.compensation_window
        original_width = overlay.window.winfo_width()
        for entry in overlay.adjusted:
            entry.set("10")
        overlay.start()
        for index in range(4):
            overlay.window.after_cancel(overlay.job)
            with patch("gyro_compensation_window.time.perf_counter", return_value=overlay.end_at):
                overlay._tick()
            if index < 3:
                overlay.window.after_cancel(overlay.job)
                overlay._start_turn()
        self.root.update()
        self.assertGreaterEqual(overlay.window.winfo_width(), original_width)
        with self.subTest("dialog alignment"):
            self.assertEqual(
                overlay.window.winfo_rootx() + overlay.window.winfo_width(),
                pane.window.winfo_rootx() + pane.window.winfo_width() - VIEW_MARGIN,
            )
        with self.subTest("camera position and result"):
            pitch = pane.scene.pitch
            result = overlay.result.get()
            pane._open_compensation_preview()
            self.assertEqual(pane.scene.pitch, pitch)
            self.assertEqual(overlay.result.get(), result)

    def test_editing_preview_speed_invalidates_the_previous_measurement(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None)
        self.root.deiconify()
        self.root.update()
        pane._open_compensation_preview()
        overlay = pane.compensation_window
        overlay.start()
        for index in range(4):
            overlay.window.after_cancel(overlay.job)
            with patch("gyro_compensation_window.time.perf_counter", return_value=overlay.end_at):
                overlay._tick()
            if index < 3:
                overlay.window.after_cancel(overlay.job)
                overlay._start_turn()
        self.assertTrue(overlay.result.get())
        self.assertTrue(overlay.status.get())

        overlay.adjusted[0].set("280")
        self.assertEqual(overlay.result.get(), "")
        self.assertEqual(overlay.status.get(), "")

    def test_compensation_buttons_follow_the_selected_controller(self):
        from gamepad_aim import AimTestPane

        available = Mock(return_value=False)
        pane = AimTestPane(self.root, str, lambda: None, compensation_available=available)
        buttons = (pane.compensation_button, pane.compensation_calculator_button)
        self.assertEqual([button.winfo_manager() for button in buttons], ["", ""])
        pane._open_compensation_calculator()
        pane._open_compensation_preview()
        self.assertIsNone(pane.compensation_calculator)
        self.assertIsNone(pane.compensation_window)

        available.return_value = True
        pane._update_labels()
        self.assertEqual([button.winfo_manager() for button in buttons], ["grid", "grid"])
        self.assertEqual([button.grid_info()["column"] for button in buttons], [1, 2])
        pane.compensation_calculator_button.invoke()
        calculator = pane.compensation_calculator
        self.assertTrue(calculator.window.winfo_exists())

        available.return_value = False
        pane._update_labels()
        self.assertEqual([button.winfo_manager() for button in buttons], ["", ""])
        self.assertFalse(calculator.window.winfo_exists())
        self.assertEqual(pane.settings_button.winfo_manager(), "grid")
        self.assertEqual(pane.start_button.winfo_manager(), "grid")

        self.root.deiconify()
        self.root.update()
        available.return_value = True
        pane.refresh_compensation_buttons()
        pane.compensation_button.invoke()
        overlay = pane.compensation_window
        overlay.start()
        self.assertIsNotNone(overlay.job)

        available.return_value = False
        pane.refresh_compensation_buttons()
        self.assertFalse(pane.test_open)
        self.assertFalse(overlay.window.winfo_exists())
        self.assertIsNone(overlay.job)

    def test_disconnected_controller_cannot_start_or_continue_preview_measurement(self):
        from gamepad_aim import AimTestPane

        available = Mock(return_value=True)
        pane = AimTestPane(self.root, str, lambda: None, compensation_available=available)
        self.root.deiconify()
        self.root.update()
        pane._open_compensation_preview()
        overlay = pane.compensation_window

        available.return_value = False
        overlay.start()
        self.assertIsNone(overlay.job)
        self.assertIsNone(overlay.measurement)

        available.return_value = True
        overlay.start()
        overlay.window.after_cancel(overlay.job)
        available.return_value = False
        with patch("gyro_compensation_window.time.perf_counter", return_value=overlay.end_at):
            overlay._tick()
        self.assertIsNone(overlay.job)
        self.assertIsNone(overlay.measurement)
        self.assertEqual(pane.scene.yaw, 0.0)

    def test_compensation_windows_close_when_the_same_bridge_slot_reconnects(self):
        from gamepad_aim import AimTestPane
        from gamepad_test_window import GamepadTestWindow
        from gyro_compensation import CompensationSnapshot

        status = {"state": "connected", "controller_id": "controller", "connection_id": "first"}
        pane = AimTestPane(
            self.root, str, lambda: None,
            read_gyro_compensation=lambda: CompensationSnapshot(
                "Alpha", {"X": 1.0, "Y": 1.0}, connection=("controller", status["connection_id"]),
            ),
            read_gyro_reload_status=lambda: status,
            apply_gyro_compensation=Mock(),
        )
        tester = object.__new__(GamepadTestWindow)
        tester.window = Mock()
        tester.aim_pane = pane
        tester._refresh_devices = Mock()
        self.root.deiconify()
        self.root.update()
        pane._open_compensation_calculator()
        pane._open_compensation_preview()
        calculator = pane.compensation_calculator
        overlay = pane.compensation_window
        overlay.start()

        tester._refresh_devices_after_telemetry()
        self.assertIsNotNone(overlay.job)
        self.assertTrue(calculator.window.winfo_exists())

        status, connected = {}, status
        tester._refresh_devices_after_telemetry()
        self.assertIsNotNone(overlay.job)
        self.assertTrue(calculator.window.winfo_exists())

        status = connected
        status["connection_id"] = "second"
        tester._refresh_devices_after_telemetry()

        self.assertIsNone(overlay.job)
        self.assertFalse(overlay.window.winfo_exists())
        self.assertFalse(calculator.window.winfo_exists())
        pane.apply_gyro_compensation.assert_not_called()
        pane._open_compensation_calculator()
        pane._open_compensation_preview()
        tester._refresh_devices_after_telemetry()
        self.assertTrue(pane.compensation_calculator.window.winfo_exists())
        self.assertTrue(pane.compensation_window.window.winfo_exists())

    def test_saved_3d_stick_speed_reaches_the_live_camera(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        for mode in ("shooting", "tracking", "timed-shooting"):
            with self.subTest(mode=mode):
                pane.mode.set(mode)
                pane._save_settings(AimSettings(
                    fullscreen=False, stick_horizontal_speed=360, stick_vertical_speed=60,
                ))
                pane.scene.start()
                pane._sim_state = SimpleNamespace(right=(1.0, 0.5))
                pane._simulated_at = 100.0
                pane._simulate_to(100.1)
                self.assertAlmostEqual(math.degrees(pane.scene.yaw), 36)
                self.assertAlmostEqual(math.degrees(pane.scene.pitch), 3)

    def test_saved_tracking_arc_and_speed_reach_the_live_target(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane.mode.set("tracking")
        pane._save_settings(AimSettings(fullscreen=False, tracking_arc=360, tracking_speed=200))
        pane.scene.start()
        pane._simulated_at = 100.0
        pane._simulate_to(101.0)
        x, _y, z = pane.scene.target_position()
        self.assertAlmostEqual(x, 0.0)
        self.assertLess(z, 0.0)

    def test_3d_tracking_result_reports_horizontal_full_stick_time(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane.mode.set("tracking")
        pane._save_settings(AimSettings(fullscreen=False, tracking_seconds=1))
        pane.countdown_until = 100
        pane._advance_countdown(100)

        def state(x):
            return SimpleNamespace(right=(x, 0.0), buttons=(), right_trigger=0.0)

        pane._input_events.extend((
            (100.1, state(1.0)), (100.125, state(0.5)),
            (100.2, state(-1.0)), (100.24, state(0.0)),
        ))
        pane._simulate_to_input(101.0)
        pane._check_finish(skip_animation=True)
        self.assertIn("水平輸出峰值 100.0%", pane.score.get())
        self.assertIn("最長連續滿桿 40 ms", pane.score.get())

    def test_gpu_3d_draws_current_grid_and_target_in_the_same_frame(self):
        from gamepad_aim import AimTestPane

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        draws = []
        closed = []

        def renderer(_hwnd, _width, _height):
            return SimpleNamespace(
                draw=lambda _w, _h, **parts: draws.append(parts),
                close=lambda: closed.append(True),
            )

        pane = AimTestPane(frame, str, lambda: None, gpu_renderer=renderer)
        self.root.deiconify()
        self.root.update()
        pane.dimension.set("3d")
        pane.mode.set("tracking")
        pane._reset()
        self.root.update()
        self.assertIsNone(pane.gpu)
        pane._open_test_window()
        self.root.update()
        self.assertFalse(pane.canvas.winfo_ismapped())
        self.assertTrue(pane._view.winfo_ismapped())

        pane.next_draw = 0.0
        pane._draw_if_due(1.0)
        self.assertTrue(draws[-1]["room"])
        first = draws[-1]
        self.assertEqual(first["overlays"], ())
        self.assertIn(pane.status.get(), [label[4] for label in first["labels"]])
        self.assertEqual(first["reticle"][2], pane.settings.reticle_size / 2)
        pane.scene.yaw = 0.08
        pane.next_draw = 0.0
        # Even if a software grid repaint would still be on cooldown, the GPU
        # frame uses the same current camera for the tiles and the target.
        pane.next_room_draw = float("inf")
        pane._draw_if_due(1.001)
        self.assertNotEqual(
            [vertices[:count * 6] for _, vertices, count in draws[-1]["room"]],
            [vertices[:count * 6] for _, vertices, count in first["room"]],
        )
        self.assertNotEqual(draws[-1]["balls"], first["balls"])

        pane.dimension.set("2d")
        pane._reset()
        self.root.update()
        self.assertEqual(closed, [])
        pane.next_draw = 0.0
        pane._draw_if_due(1.002)
        self.assertEqual(draws[-1]["room"], ())
        pane.close_test()
        self.assertEqual(closed, [True])
        self.assertIs(pane._view, pane.canvas)

    def test_timed_fullscreen_keeps_results_visible_after_gpu_rendering_stops(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        refresh = 180
        pane = AimTestPane(
            self.root, str, lambda: None,
            display_hz=lambda _window: refresh,
            gpu_renderer=lambda *_: SimpleNamespace(draw=Mock(), close=Mock()),
        )
        pane.dimension.set("3d")
        pane.mode.set("timed-shooting")
        pane._save_settings(AimSettings(fullscreen=True, fullscreen_size="800x450", timed_shooting_seconds=1))
        self.root.deiconify()
        self.root.update()
        display = SimpleNamespace(bounds=(0, 0, 800, 450), close=Mock())
        with patch("gamepad_aim.activate_fullscreen_mode", return_value=display):
            pane._start()
        gpu = pane.gpu
        self.assertIsNotNone(gpu)
        self.assertFalse(pane.canvas.winfo_ismapped())

        start = pane.countdown_until
        pane._advance_countdown(start)
        self.assertEqual(pane.canvas.itemcget(pane.hud.labels[1], "text"), "")
        self.assertEqual(pane.timer_text.get(), "剩餘 1.0 s")
        pane.fps_text.set("FPS 177   99% Low 175")
        pane._simulate_tick(start + 1.01)
        self.root.update()

        self.assertTrue(pane.finished)
        self.assertTrue(pane.test_open)
        self.assertTrue(pane.canvas.winfo_viewable())
        self.assertIsNone(pane.gpu)
        gpu.close.assert_called_once()
        self.assertTrue(any(pane.grid_bounds))
        self.assertEqual(pane.canvas.itemcget(pane.countdown_item, "text"), "測試完成")
        self.assertIn("命中 0", pane.canvas.itemcget(pane.hud.labels[1], "text"))
        self.assertEqual(pane.timer_text.get(), "剩餘 0.0 s")
        self.assertEqual(pane.fps_text.get(), "FPS 177   99% Low 175")
        heading = pane.canvas.bbox(pane.countdown_item)
        score = pane.canvas.bbox(pane.hud.labels[1])
        comparison = pane.canvas.bbox(*pane.hud.change_items)
        hint = pane.canvas.bbox(pane.finish_hint_item)
        self.assertLess(heading[3], score[1])
        self.assertLess(score[3], comparison[1])
        self.assertLess(comparison[3], hint[1])
        self.assertAlmostEqual((heading[1] + hint[3]) / 2, pane.size[1] / 2, delta=1)
        for left, _, right, _ in (heading, score, comparison, hint):
            self.assertAlmostEqual((left + right) / 2, pane.size[0] / 2, delta=1)
        display.close.assert_not_called()

        refresh = 120
        pane._refresh_display_rate(SimpleNamespace(widget=pane.window))
        self.assertEqual(pane.display_hz, 120)
        self.assertEqual(pane.canvas.itemcget(pane.hud.labels[2], "text"), "FPS 177   99% Low 175")

        pane.restart_button.invoke()
        self.root.update()
        self.assertIsNotNone(pane.countdown_until)
        self.assertIsNotNone(pane.gpu)
        self.assertFalse(pane.canvas.winfo_ismapped())
        self.assertEqual(pane.canvas.coords(pane.countdown_item), [pane.size[0] / 2, pane.size[1] / 2])
        pane.close_test()
        display.close.assert_called_once()

    def test_tk_3d_room_uses_filled_tiles_and_hides_them_in_2d(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_model import ROOM_TILES

        pane = AimTestPane(self.root, str, lambda: None)
        pane.dimension.set("3d")
        pane._reset()
        visible = [item for item, bounds in zip(pane.grid_tiles, pane.grid_bounds) if bounds is not None]
        self.assertEqual(
            [pane.canvas.itemcget(item, "fill") for item in pane.grid_tiles],
            [color for color, _ in ROOM_TILES],
        )
        self.assertTrue(all(pane.canvas.type(item) == "polygon" for item in visible))
        self.assertTrue(all(pane.canvas.itemcget(item, "outline") == "" for item in visible))
        pane.dimension.set("2d")
        pane._reset()
        self.assertTrue(all(pane.canvas.itemcget(item, "state") == "hidden" for item in visible))

    def test_3d_depth_shading_matches_in_tk_and_gpu_and_keeps_dark_colors_visible(self):
        from dataclasses import replace

        from gamepad_aim import VIEW_BACKGROUND, AimTestPane
        from gamepad_aim_settings import AimSettings

        draw = Mock()
        pane = AimTestPane(self.root, str, lambda: None, gpu_renderer=lambda *_: SimpleNamespace(
            draw=draw, close=lambda: None,
        ))
        pane.mode.set("timed-shooting")
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450", target_color=VIEW_BACKGROUND, depth_shading=True))
        pane._open_test_window()
        near, far = (-0.4, 0, 4), (0.4, 0, 23)
        with patch.object(pane.scene, "target_positions", return_value=(near, far)):
            pane._draw()
            self.assertTrue(pane._present_gpu())
        pane._stop_gpu()
        fills = tuple(pane.canvas.itemcget(item, "fill") for item in pane._ball_items)
        self.assertEqual(tuple(ball[-1] for ball in draw.call_args.kwargs["balls"]), fills)
        self.assertFalse(draw.call_args.kwargs["ball_outlines"])
        self.assertTrue(all(not pane.canvas.itemcget(item, "outline") for item in pane._ball_items))

        def brightness(color):
            return sum(weight * channel for weight, channel in zip((299, 587, 114), bytes.fromhex(color[1:])))

        self.assertGreater(brightness(fills[1]), brightness(fills[0]))
        self.assertGreater(brightness(fills[0]) - brightness(VIEW_BACKGROUND), 40_000)

        pane._save_settings(replace(pane.settings, depth_shading=False))
        with patch.object(pane.scene, "target_positions", return_value=(near, far)):
            pane._draw()
            self.assertTrue(pane._present_gpu())
        pane._stop_gpu()
        self.assertTrue(draw.call_args.kwargs["ball_outlines"])
        self.assertTrue(all(pane.canvas.itemcget(item, "outline") for item in pane._ball_items))

    def test_seeded_depth_shading_uses_the_full_palette_even_at_default_depth(self):
        from gamepad_aim import AimTestPane, _target_depth_palette
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane._save_settings(AimSettings(fullscreen=False, seed=17))
        near = min(pane.scene._targets, key=lambda target: math.hypot(*target))
        far = max(pane.scene._targets, key=lambda target: math.hypot(*target))
        with patch.object(pane.scene, "target_positions", return_value=(near, far)):
            pane._draw()
        colors = {
            round(pane.canvas.coords(item)[2] - pane.canvas.coords(item)[0], 4):
            pane.canvas.itemcget(item, "fill")
            for item in pane._ball_items
        }
        self.assertEqual(colors[max(colors)], pane.settings.target_color)
        self.assertEqual(colors[min(colors)], _target_depth_palette(pane.settings.target_color)[-1])

    def test_depth_palette_changes_by_at_most_one_color_unit_per_channel(self):
        from gamepad_aim import _target_depth_palette

        colors = [bytes.fromhex(color[1:]) for color in _target_depth_palette("#57dab1")]
        self.assertGreater(len(set(colors)), 64)
        self.assertTrue(all(0 <= near - far <= 1 for before, after in zip(colors, colors[1:])
                            for near, far in zip(before, after)))

    def test_overlapping_3d_target_draws_the_first_surface_last(self):
        from gamepad_aim import AimTestPane, project_ball

        pane = AimTestPane(self.root, str, lambda: None)
        pane.mode.set("timed-shooting")
        pane._reset()
        pane.scene.start()
        front, back = (0.10, 0.0, 6.94), (0.35, 0.0, 6.82)
        pane.scene._live[:] = [front, back]
        pane._draw()
        self.assertEqual(pane._ball_bounds[1], project_ball(front, *pane.size))
        self.assertEqual(pane.scene.shoot(), front)

    def test_gpu_failure_restores_the_software_canvas(self):
        from gamepad_aim import AimTestPane

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        closed = []

        def draw(_width, _height, **_parts):
            raise OSError("device lost")

        def renderer(_hwnd, _width, _height):
            return SimpleNamespace(draw=draw, close=lambda: closed.append(True))

        pane = AimTestPane(frame, str, lambda: None, gpu_renderer=renderer)
        self.root.deiconify()
        self.root.update()
        pane.dimension.set("3d")
        pane._reset()
        pane.output_ready = True
        pane._start()
        started = pane.countdown_until
        pane._advance_countdown(started)
        pane.scene.yaw = math.atan2(2, 8)
        pane._shoot(started + 0.01)
        pane.next_draw = 0.0
        pane._draw_if_due(started + 0.05)
        self.root.update()
        self.assertEqual(closed, [True])
        self.assertEqual(pane.gpu_error, "device lost")
        self.assertTrue(pane.scene.running)
        self.assertTrue(pane.canvas.winfo_ismapped())
        self.assertFalse(pane._gpu_view.winfo_ismapped())
        self.assertTrue(any(pane.canvas.coords(pane.hit_particles[0])))
        self.assertEqual(pane.canvas.cget("cursor"), "none")

    def test_threaded_gpu_failure_starts_fresh_tk_frame_statistics(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        first_draw = threading.Event()

        def draw(_width, _height, **_parts):
            if first_draw.is_set():
                raise OSError("worker device lost")
            first_draw.set()

        renderer = SimpleNamespace(draw=draw, close=Mock())
        pane = AimTestPane(
            self.root, str, lambda: None, gpu_renderer=lambda *_: renderer, render_in_worker=True,
        )
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450"))
        pane._open_test_window()
        worker = pane._render_worker
        pane.countdown_until = time.perf_counter()
        pane._advance_countdown(pane.countdown_until)
        round_basis = pane.round_basis
        pane.fps_text.set("D2D 提交 180 FPS")
        pane._draw_if_due(time.perf_counter())
        self.assertTrue(first_draw.wait(2))
        pane.next_draw = 0.0
        pane._draw_if_due(time.perf_counter())
        self.assertTrue(worker.stopped.wait(2))
        pane._collect_render_results()

        self.assertEqual(pane.gpu_error, "worker device lost")
        self.assertIsNone(pane.gpu)
        self.assertIs(pane._view, pane.canvas)
        self.assertIsNone(pane._gpu_view)
        self.assertTrue(pane.cursor.running)
        self.assertIs(pane.round_basis, round_basis)
        self.assertEqual(pane._frame_stats.frames, 0)
        self.assertEqual(pane.fps_text.get(), "")
        renderer.close.assert_called_once()
        pane._draw_if_due(time.perf_counter())
        self.assertEqual(pane._frame_stats.frames, 1)
        self.assertEqual(pane._frame_stats.intervals, [])
        pane.close_test()

    def test_worker_failure_during_the_last_hit_keeps_animation_and_round_frame_rates(self):
        from gamepad_aim import AimTestPane, HIT_DURATION
        from gamepad_aim_settings import AimSettings

        worker = SimpleNamespace(
            stopped=threading.Event(), drain=Mock(return_value=((), "worker device lost")),
            close=Mock(), discard_pending=Mock(),
        )
        worker.stopped.set()
        with patch("gamepad_aim.AimRenderWorker", return_value=worker):
            pane = AimTestPane(
                self.root, str, lambda: None, gpu_renderer=Mock(), render_in_worker=True,
            )
            pane._save_settings(AimSettings(fullscreen=False, window_size="800x450", target_count=1))
            pane._start()
            start = pane.countdown_until
            pane._advance_countdown(start)
        pane.scene.yaw = math.atan2(2, 8)
        rates = "FPS 177   99% Low 175"
        pane.fps_text.set(rates)
        shot = start + 0.2
        pane._shoot(shot)
        pane._draw_hit_effect(shot + HIT_DURATION / 3)
        self.assertTrue(pane._gpu_fragments)
        self.assertTrue(pane.scene.completed)
        self.assertFalse(pane.finished)

        pane._collect_render_results()
        pane._draw_if_due(shot + HIT_DURATION / 2)
        self.assertIs(pane._view, pane.canvas)
        self.assertTrue(any(pane.grid_bounds))
        self.assertEqual(pane.canvas.itemcget(pane.hit_particles[0], "state"), "normal")
        self.assertEqual(pane.fps_text.get(), rates)
        pane._draw_hit_effect(shot + HIT_DURATION + 0.01)
        self.assertTrue(pane.finished)
        self.assertEqual(pane.fps_text.get(), rates)
        self.assertEqual(len(pane.history.records), 1)

    def test_reopen_does_not_reuse_a_view_with_an_inflight_submission(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def draw(_width, _height, **_parts):
            entered.set()
            if not release.wait(2):
                raise OSError("test did not unblock the renderer")

        renderer = SimpleNamespace(draw=draw, close=Mock())
        factory = Mock(return_value=renderer)
        pane = AimTestPane(self.root, str, lambda: None, gpu_renderer=factory, render_in_worker=True)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450"))
        pane._open_test_window()
        worker, old_view = pane._render_worker, pane._gpu_view
        pane._draw_if_due(time.perf_counter())
        self.assertTrue(entered.wait(2))
        self.assertEqual(pane.draws, 0)

        pane.close_test()
        self.assertFalse(worker.stopped.is_set())
        self.assertTrue(old_view.winfo_exists())
        self.assertTrue(pane._open_test_window())
        self.assertIsNone(pane.gpu)
        factory.assert_called_once()

        release.set()
        self.assertTrue(worker.stopped.wait(2))
        self.root.after(50, self.root.quit)
        self.root.mainloop()
        self.assertFalse(old_view.winfo_exists())
        self.assertIsNotNone(pane.gpu)
        self.assertIsNone(pane._render_cleanup_job)
        self.assertIsNot(pane._gpu_view, old_view)
        pane.close_test()

    def test_fullscreen_start_during_renderer_shutdown_is_replayed_or_canceled(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                parent = tk.Frame(self.root)
                pane = AimTestPane(parent, str, lambda: None)
                pane.dimension.set("2d")
                pane._save_settings(AimSettings(fullscreen=True, fullscreen_size="800x450"))
                worker = SimpleNamespace(stopped=threading.Event())
                old_view = tk.Frame(pane.window)
                pane._retired_render_view = worker, old_view
                pane._fullscreen_mode = Mock()
                restore = pane._fullscreen_mode.close

                with patch("gamepad_aim.activate_fullscreen_mode", return_value=Mock(bounds=(0, 0, 800, 450))) as mode:
                    pane._start()
                    self.assertFalse(pane.test_open)
                    restore.assert_not_called()
                    mode.assert_not_called()
                    if cancel:
                        pane.set_visible(False)
                    worker.stopped.set()
                    pane._reap_render_view()
                    restore.assert_called_once()
                    self.assertFalse(old_view.winfo_exists())
                    self.assertEqual(pane.test_open, not cancel)
                    self.assertEqual(pane.countdown_until is not None, not cancel)
                    self.assertEqual(mode.call_count, 0 if cancel else 1)
                    pane.close_test()
                parent.destroy()

    def test_restart_from_fullscreen_results_waits_for_the_finished_worker(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        worker = SimpleNamespace(stopped=threading.Event(), close=Mock(), discard_pending=Mock())
        with patch("gamepad_aim.AimRenderWorker", return_value=worker) as make_worker:
            pane = AimTestPane(self.root, str, lambda: None, gpu_renderer=Mock(), render_in_worker=True)
            pane.mode.set("timed-shooting")
            pane._save_settings(AimSettings(fullscreen=True, fullscreen_size="800x450", timed_shooting_seconds=1))
            self.root.deiconify()
            self.root.update()
            display = SimpleNamespace(bounds=(0, 0, 800, 450), close=Mock())
            with patch("gamepad_aim.activate_fullscreen_mode", return_value=display):
                pane._start()
            start = pane.countdown_until
            pane._advance_countdown(start)
            pane._simulate_tick(start + 1.01)
            self.assertIsNotNone(pane._retired_render_view)
            pane._start()
            self.assertTrue(pane.finished)
            self.assertIsNone(pane.countdown_until)
            make_worker.assert_called_once()

            worker.stopped.set()
            pane.owner.after_cancel(pane._render_cleanup_job)
            pane._reap_render_view()
            self.assertTrue(pane.test_open)
            self.assertFalse(pane.finished)
            self.assertIsNotNone(pane.countdown_until)
            self.assertEqual(make_worker.call_count, 2)
            display.close.assert_not_called()
            pane.close_test()

    def test_windows_pointer_can_score_with_no_gamepad(self):
        from gamepad_aim import AimTestPane

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        pane = AimTestPane(frame, str, lambda: None)
        self.root.deiconify()
        self.root.update()
        pane.dimension.set("2d")
        pane._reset()
        pane._start()
        pane._advance_countdown(pane.countdown_until)
        x, y, _radius = pane.cursor.target(*pane.size, pane._cursor_top())
        pane.canvas.event_generate("<Button-1>", x=round(x), y=round(y))
        self.assertEqual((pane.cursor.hits, pane.cursor.shots), (1, 1))

    def test_2d_mouse_and_stick_take_turns_moving_one_reticle(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        output = None
        pane = AimTestPane(self.root, str, lambda: output)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, auto_input_switch=True))
        pane._start()
        start = pane.countdown_until
        self.assertIsNotNone(start)
        pane._advance_countdown(start)
        pane.last_frame = start
        pane._schedule = lambda _now: None
        with patch("gamepad_aim.time.perf_counter", return_value=start + 0.01):
            pane._mouse_move(SimpleNamespace(x=101, y=92))
        output = "device", SimpleNamespace(buttons=(), right=(0.5, 0), right_trigger=0)
        for offset in (0.02, 0.1):
            pane.next_frame = 0
            with patch("gamepad_aim.time.perf_counter", return_value=start + offset):
                pane._tick()
            if offset < 0.05:
                self.assertEqual((pane.cursor.x, pane.cursor.y), (101, 92))
        self.assertGreater(pane.cursor.x, 101)
        self.assertEqual(pane.cursor.y, 92)
        self.assertTrue(pane.cursor.running)

    def test_connected_controller_blocks_mouse_aim_by_default(self):
        from gamepad_aim import AimTestPane

        for dimension in ("2d", "3d"):
            with self.subTest(dimension=dimension):
                frame = tk.Frame(self.root)
                self.addCleanup(frame.destroy)
                state = SimpleNamespace(buttons=(), right=(0.5, 0), right_trigger=0)
                output = ("pad", state)
                pane = AimTestPane(frame, str, lambda: output)
                pane.dimension.set(dimension)
                pane._reset()
                pane._sample_input(99.99)
                pane.countdown_until = 100
                pane._advance_countdown(100)
                view = pane._view
                x, y = pane.size[0] // 2, pane.size[1] // 2
                left, top = view.winfo_rootx(), view.winfo_rooty()
                event = SimpleNamespace(x=x + 10, y=y)
                with (
                    patch.object(view, "winfo_pointerxy", return_value=(left + x + 10, top + y)) as pointer,
                    patch.object(view, "winfo_containing", return_value=view),
                    patch.object(view, "focus_displayof", return_value=view),
                    patch("gamepad_aim._warp_mouse", return_value=False) as warp,
                    patch("gamepad_aim.time.perf_counter", return_value=100.01),
                ):
                    self.assertFalse(pane._mouse_move(event))
                    pane._mouse_click(event)
                    pane._toggle_mouse_look(event)
                    self.assertFalse(pane._mouse_released)
                    self.assertEqual(pane._round().shots, 0)
                    warp.assert_not_called()
                    pane._simulate_tick(100.02)
                    self.assertGreater(pane.scene.yaw if dimension == "3d" else pane.cursor.x,
                                       0 if dimension == "3d" else x)

                    output = None
                    pane._sample_input(100.03)
                    self.assertTrue(pane._mouse_move(event, 100.03))
                    if dimension == "3d":
                        yaw = pane.scene.yaw
                        pointer.return_value = left + x + 20, top + y
                        pane._mouse_move(SimpleNamespace(x=x + 20, y=y), 100.04)
                        self.assertGreater(pane.scene.yaw, yaw)
                    else:
                        self.assertEqual((pane.cursor.x, pane.cursor.y), (x + 10, y))

    def test_2d_tracking_runs_without_a_controller_and_records_pixel_error(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane.dimension.set("2d")
        pane.mode.set("tracking")
        pane._save_settings(AimSettings(fullscreen=False, tracking_seconds=1))
        pane._start()
        start = pane.countdown_until
        pane._advance_countdown(start)
        pane.last_frame = start
        pane._schedule = lambda _now: None
        for tick in range(1, 12):
            pane.next_frame = 0
            with patch("gamepad_aim.time.perf_counter", return_value=start + tick / 10):
                pane._tick()
        self.assertTrue(pane.finished)
        record, = pane.history.records
        self.assertEqual(record["basis"]["mode"], "cursor-tracking")
        self.assertEqual(record["metrics"]["seconds"], 1)
        self.assertIn("tracking", record["metrics"])
        self.assertIn(" px", pane.score.get())

    def test_2d_tracking_mouse_motion_does_not_backdate_contact(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane.dimension.set("2d")
        pane.mode.set("tracking")
        pane._save_settings(AimSettings(fullscreen=False, tracking_seconds=1))
        pane.countdown_until = 100
        pane._advance_countdown(100)
        x, y = 100, 100
        starting_error = math.hypot(pane.cursor.x - x, pane.cursor.y - y)
        with patch.object(pane.cursor, "_target_at", return_value=(x, y, 10)):
            pane._mouse_move(SimpleNamespace(x=x, y=y), 100.2)
            pane._simulate_tick(100.4)
            self.assertAlmostEqual(pane.cursor.on_target_time, 0.2)
            self.assertAlmostEqual(pane.cursor.mean_error, starting_error / 2)
            pane._simulate_tick(100.9)
            self.assertFalse(pane._mouse_move(SimpleNamespace(x=x + 80, y=y), 101.01))

        self.assertTrue(pane.finished)
        self.assertEqual((pane.cursor.x, pane.cursor.y), (x, y))
        self.assertAlmostEqual(pane.cursor.on_target_time, 0.8)

    def test_3d_tracking_mouse_motion_does_not_backdate_contact(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane.mode.set("tracking")
        pane._save_settings(AimSettings(fullscreen=False, tracking_seconds=1))
        pane.countdown_until = 100
        view = pane._view
        left, top = view.winfo_rootx(), view.winfo_rooty()
        x, y = pane.size[0] // 2, pane.size[1] // 2
        anchor = left + x, top + y
        angle = math.radians(18)
        target = 8 * math.sin(angle), 0, 8 * math.cos(angle)
        with (
            patch.object(view, "winfo_pointerxy", return_value=anchor) as pointer,
            patch.object(view, "winfo_containing", return_value=view),
            patch.object(view, "focus_displayof", return_value=view),
            patch.object(pane.tracking, "target", return_value=target),
            patch("gamepad_aim._warp_mouse", return_value=False),
        ):
            pane._advance_countdown(100)
            pointer.return_value = anchor[0] + 100, anchor[1]
            pane._mouse_move(SimpleNamespace(x=x + 100, y=y), 100.2)
            pane._simulate_tick(100.4)

        self.assertAlmostEqual(pane.scene.yaw, angle)
        self.assertAlmostEqual(pane.tracking.on_target_time, 0.2)
        self.assertAlmostEqual(pane.tracking.mean_error, 9.0)

    def test_hiding_the_tab_stops_tracking_and_cancels_the_timer(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None)
        pane.mode.set("tracking")
        pane._reset()
        pane.set_visible(True)
        pane.tracking.start()
        pane.set_visible(False)
        self.assertFalse(pane.tracking.running)
        self.assertIsNone(pane.job)

    def test_scene_redraw_follows_display_rate_through_600_fps(self):
        from gamepad_aim import AimTestPane

        for refresh in (60, 120, 144, 180, 240, 360, 500, 540, 600):
            with self.subTest(refresh=refresh):
                frame = tk.Frame(self.root)
                pane = AimTestPane(frame, str, lambda: None, display_hz=lambda _window: refresh)
                pane.dimension.set("3d")
                pane.mode.set("tracking")
                pane._reset()
                for index in range(refresh):
                    pane._draw_if_due(index / refresh)
                self.assertEqual(pane.draws, refresh)
                frame.destroy()

    def test_render_stalls_do_not_change_tracking_simulation(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings
        from gamepad_aim_timing import SIMULATION_PERIOD

        def play(dimension, hz, tick_spacing):
            frame = tk.Frame(self.root)
            state = SimpleNamespace(buttons=(), right=(0.31, -0.17), right_trigger=0.0)
            pane = AimTestPane(frame, str, lambda: ("pad", state), display_hz=lambda _window: hz)
            pane.dimension.set(dimension)
            pane.mode.set("tracking")
            pane._save_settings(AimSettings(fullscreen=False, tracking_seconds=1))
            pane.device_key = "pad"
            pane._sim_state = state
            pane.test_open = True
            pane.countdown_until = 100.0
            pane._advance_countdown(100.0)
            pane.last_frame = 100.0
            pane.next_frame = 100.0 + SIMULATION_PERIOD
            pane._draw_if_due = lambda _stamp: None
            for index in range(1, math.ceil(1.025 / tick_spacing) + 1):
                with patch("gamepad_aim.time.perf_counter", return_value=100 + index * tick_spacing):
                    pane._tick()
            self.assertTrue(pane.finished)
            test = pane._round()
            position = (test.yaw, test.pitch) if dimension == "3d" else (test.x, test.y)
            result = (*position, test.elapsed, test.mean_error, test.on_target_percent)
            frame.destroy()
            return result

        for dimension in ("2d", "3d"):
            with self.subTest(dimension=dimension):
                smooth = play(dimension, 600, 1 / 600)
                stalled = play(dimension, 60, 0.04)
                for actual, expected in zip(smooth, stalled):
                    self.assertAlmostEqual(actual, expected, places=9)

    def test_buffered_rt_presses_are_ordered_and_not_repeated(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        released = SimpleNamespace(buttons=(), right=(0.0, 0.0), right_trigger=0.0)
        pressed = SimpleNamespace(buttons=(), right=(0.0, 0.0), right_trigger=1.0)
        reports = tuple((100 + offset, state) for offset, state in (
            (0.001, released), (0.002, pressed), (0.003, released), (0.004, pressed),
        ))
        batch = reports

        def input_report():
            nonlocal batch
            samples, batch = batch, ()
            return "pad", pressed, samples, None

        pane = AimTestPane(self.root, str, lambda: None, read_input=input_report)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False))
        pane.countdown_until = 100
        pane._advance_countdown(100)
        pane._sample_input(100.01)
        pane._simulate_tick(100.01)
        self.assertEqual(pane.cursor.shots, 2)
        pane._sample_input(100.02)
        pane._simulate_tick(100.02)
        self.assertEqual(pane.cursor.shots, 2)
        self.assertIsNone(pane._source_hz)

    def test_controller_actions_during_a_stall_are_not_replayed_after_it(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        released = SimpleNamespace(buttons=(), right=(0.0, 0.0), right_trigger=0.0)
        for source in ("buffered", "snapshot"):
            for button in ("START", "B"):
                with self.subTest(source=source, button=button):
                    frame = tk.Frame(self.root)
                    self.addCleanup(frame.destroy)
                    held = SimpleNamespace(buttons=(button,), right=(0.0, 0.0), right_trigger=0.0)
                    state, batch = released, ()

                    def read_input():
                        nonlocal batch
                        samples, batch = batch, ()
                        return "pad", state, samples, None

                    pane = AimTestPane(
                        frame, str, lambda: ("pad", state),
                        read_input=read_input if source == "buffered" else None,
                    )
                    pane._save_settings(AimSettings(fullscreen=False, window_size="800x450"))
                    pane._start()
                    start = pane.countdown_until
                    pane._sample_input(start - 0.01)
                    pane._simulate_to_input(start - 0.01)
                    pane._advance_countdown(start)
                    pane.last_frame = pane.next_frame = start
                    pane._visible = True
                    state = held
                    batch = ((start + 0.10, held), (start + 0.41, released), (start + 0.42, held))

                    with (
                        patch.object(pane.owner, "focus_displayof", return_value=pane.canvas),
                        patch.object(pane.window, "focus_displayof", return_value=pane.canvas),
                        patch.object(pane, "_schedule"),
                    ):
                        with patch("gamepad_aim.time.perf_counter", return_value=start + 0.40):
                            pane._tick()
                        self.assertTrue(pane.test_open)
                        self.assertTrue(pane.interrupted)
                        self.assertIsNone(pane.countdown_until)

                        if source == "snapshot":
                            state = released
                            pane.next_frame = 0
                            with patch("gamepad_aim.time.perf_counter", return_value=start + 0.43):
                                pane._tick()
                            state = held
                            pane.next_frame = 0
                        with patch("gamepad_aim.time.perf_counter", return_value=start + 0.45):
                            pane._tick()
                    self.assertEqual(pane.test_open, button == "START")
                    self.assertEqual(pane.countdown_until is not None, button == "START")

    def test_countdown_arms_first_buffered_rt_press_only_if_trigger_is_released(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        released = SimpleNamespace(buttons=(), right=(0.0, 0.0), right_trigger=0.0)
        pressed = SimpleNamespace(buttons=(), right=(0.0, 0.0), right_trigger=1.0)
        for baseline in (released, pressed):
            with self.subTest(baseline=baseline.right_trigger):
                frame = tk.Frame(self.root)
                self.addCleanup(frame.destroy)
                pane = AimTestPane(frame, str, lambda: None, read_input=lambda: None)
                pane.dimension.set("2d")
                pane._save_settings(AimSettings(fullscreen=False))
                pane._sim_state = baseline
                pane.countdown_until = 100
                pane._advance_countdown(100)
                pane._input_events.append((100.01, pressed))
                pane._simulate_tick(100.01)
                self.assertEqual(pane.cursor.shots, 0 if baseline is pressed else 1)
                pane._input_events.extend(((100.02, released), (100.03, pressed)))
                pane._simulate_tick(100.03)
                self.assertEqual(pane.cursor.shots, 1 if baseline is pressed else 2)

    def test_buffered_disconnect_clears_old_input_and_held_rt_requires_release(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        moving = SimpleNamespace(buttons=(), right=(0.5, 0.0), right_trigger=0.0)
        released = SimpleNamespace(buttons=(), right=(0.0, 0.0), right_trigger=0.0)
        pressed = SimpleNamespace(buttons=(), right=(0.0, 0.0), right_trigger=1.0)
        output = ("original", moving, ((100.01, moving),), None)
        pane = AimTestPane(self.root, str, lambda: None, read_input=lambda: output)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False))
        pane.countdown_until = 100
        pane._advance_countdown(100)

        pane._sample_input(100.02)
        pane._simulate_tick(100.02)
        position = pane.cursor.x
        self.assertGreater(position, pane.size[0] / 2)

        output = ("original", pressed, ((100.04, pressed),), None)
        pane._sample_input(100.04)
        output = None
        pane._sample_input(100.05)
        pane._simulate_tick(100.05)
        self.assertTrue(pane.cursor.running)
        self.assertEqual(pane.cursor.shots, 0)
        self.assertEqual(pane.cursor.x, position)

        output = ("reconnected", pressed, ((100.06, pressed),), None)
        pane._sample_input(100.06)
        pane._simulate_tick(100.06)
        self.assertEqual(pane.cursor.shots, 0)

        output = ("reconnected", pressed, ((100.07, released), (100.08, pressed)), None)
        pane._sample_input(100.08)
        pane._simulate_tick(100.08)
        self.assertEqual(pane.cursor.shots, 1)
        self.assertEqual(pane.cursor.x, position)

    def test_3d_keyboard_shot_uses_buffered_input_identity(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        state = SimpleNamespace(buttons=(), right=(0.0, 0.0), right_trigger=0.0)
        pane = AimTestPane(
            self.root, str, lambda: ("legacy", state),
            read_input=lambda: ("generation", state, (), 125.0),
        )
        pane._save_settings(AimSettings(fullscreen=False))
        pane.device_key = "generation"
        pane._sim_state = state
        pane.countdown_until = 100
        pane._advance_countdown(100)
        pane.space_armed = True
        with patch("gamepad_aim.time.perf_counter", return_value=100.01):
            pane._space_shot(None)
        self.assertEqual(pane.shooting.shots, 1)
        self.assertTrue(pane.shooting.running)

    def test_3d_mouse_shot_uses_stick_motion_before_the_click(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        moving = SimpleNamespace(buttons=(), right=(1.0, 0.0), right_trigger=0.0)
        pane = AimTestPane(
            self.root, str, lambda: None,
            read_input=lambda: ("pad", moving, ((100.01, moving),), None),
        )
        pane._save_settings(AimSettings(fullscreen=False, auto_input_switch=True))
        pane.countdown_until = 100
        pane._advance_countdown(100)
        x, _, z = pane.shooting.target_position()
        target_yaw = math.atan2(x, z)
        pane.scene.yaw = target_yaw - math.radians(14)
        self.assertFalse(pane.scene.crosshair_on_target())

        with (patch.object(pane._view, "focus_displayof", return_value=pane._view),
              patch("gamepad_aim.time.perf_counter", return_value=100.11)):
            pane._mouse_click(SimpleNamespace(x=100, y=100))

        self.assertAlmostEqual(pane.scene.yaw, target_yaw)
        self.assertEqual((pane.shooting.hits, pane.shooting.shots), (1, 1))

    def test_2d_mouse_shot_does_not_move_earlier_buffered_trigger_shots(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        released = SimpleNamespace(buttons=(), right=(0.0, 0.0), right_trigger=0.0)
        pressed = SimpleNamespace(buttons=(), right=(0.0, 0.0), right_trigger=1.0)
        samples = ((100.01, released), (100.02, pressed))
        pane = AimTestPane(
            self.root, str, lambda: None,
            read_input=lambda: ("pad", pressed, samples, None),
        )
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, target_count=1, auto_input_switch=True))
        pane.countdown_until = 100
        pane._advance_countdown(100)
        self.assertFalse(pane.cursor.on_target(pane.cursor.x, pane.cursor.y, *pane.size))
        x, y, _ = pane.cursor.target(*pane.size)

        with patch("gamepad_aim.time.perf_counter", return_value=100.03):
            pane._mouse_click(SimpleNamespace(x=x, y=y))

        self.assertEqual((pane.cursor.hits, pane.cursor.shots), (1, 2))

    def test_mouse_motion_does_not_aim_an_earlier_buffered_trigger_shot(self):
        from gamepad_aim import AimTestPane, MOUSE_LOOK_RADIANS_PER_PIXEL
        from gamepad_aim_settings import AimSettings

        released = SimpleNamespace(buttons=(), right=(0.0, 0.0), right_trigger=0.0)
        pressed = SimpleNamespace(buttons=(), right=(0.0, 0.0), right_trigger=1.0)
        for dimension in ("2d", "3d"):
            with self.subTest(dimension=dimension):
                frame = tk.Frame(self.root)
                self.addCleanup(frame.destroy)
                state, batch = released, ()

                def read_input():
                    nonlocal batch
                    samples, batch = batch, ()
                    return "pad", state, samples, None

                pane = AimTestPane(frame, str, lambda: None, read_input=read_input)
                pane.dimension.set(dimension)
                pane._save_settings(AimSettings(fullscreen=False, auto_input_switch=True))
                pane._sample_input(99.99)
                pane._simulate_to_input(99.99)
                pane.countdown_until = 100
                view = pane._view
                left, top = view.winfo_rootx(), view.winfo_rooty()
                x, y = pane.size[0] // 2, pane.size[1] // 2
                anchor = left + x, top + y
                with (
                    patch.object(view, "winfo_pointerxy", return_value=anchor) as pointer,
                    patch.object(view, "winfo_containing", return_value=view),
                    patch.object(view, "focus_displayof", return_value=view),
                    patch("gamepad_aim._warp_mouse", return_value=False),
                ):
                    pane._advance_countdown(100)
                    if dimension == "3d":
                        target_x, _, target_z = pane.scene.target_position()
                        x += round(math.atan2(target_x, target_z) / MOUSE_LOOK_RADIANS_PER_PIXEL)
                    else:
                        x, y, _ = pane.cursor.target(*pane.size)
                    pointer.return_value = left + x, top + y
                    state, batch = pressed, ((100.01, pressed),)
                    pane._mouse_move(SimpleNamespace(x=x, y=y), 100.02)
                    pane._sample_input(100.03)
                    pane._simulate_tick(100.03)
                    test = pane._round()
                    self.assertEqual((test.hits, test.shots), (0, 1))

                    batch = ((100.04, released), (100.05, pressed))
                    pane._sample_input(100.05)
                    pane._simulate_tick(100.05)
                    self.assertEqual((test.hits, test.shots), (1, 2))

    def test_releasing_mouse_control_does_not_backdate_stick_turning(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane._save_settings(AimSettings(fullscreen=False, auto_input_switch=True))
        pane._sim_state = SimpleNamespace(buttons=(), right=(1.0, 0.0), right_trigger=0.0)
        pane.output_ready = True
        pane.countdown_until = 100
        pane._advance_countdown(100)
        pane._mouse_priority_until = 100.05

        with patch("gamepad_aim.time.perf_counter", return_value=100.03):
            pane._toggle_mouse_look(None)
        self.assertTrue(pane._mouse_released)
        self.assertEqual(pane.scene.yaw, 0)
        pane._simulate_tick(100.06)
        self.assertAlmostEqual(math.degrees(pane.scene.yaw), 4.2)

    def test_gpu_initialization_failure_draws_and_close_releases_aim_work(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        capture = Mock()
        factory = Mock(side_effect=OSError("no Direct2D target"))
        pane = AimTestPane(self.root, str, lambda: None, gpu_renderer=factory, set_input_active=capture)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450"))
        pane.set_visible(True)
        pane._start()
        self.assertTrue(pane.test_open)
        self.assertIs(pane._view, pane.canvas)
        self.assertIsNone(pane.gpu)
        pane._draw_if_due(time.perf_counter())
        self.assertEqual(pane.draws, 1)
        pane.set_visible(False)
        self.assertFalse(pane.test_open)
        self.assertIsNone(pane.job)
        self.assertEqual([call.args for call in capture.call_args_list], [(True,), (False,)])
        factory.assert_called_once()

    def test_frame_stall_produces_only_one_draw_and_does_not_leave_a_backlog(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None, display_hz=lambda _window: 600)
        pane.next_draw = 10.0
        pane._draw = Mock()
        pane._draw_if_due(10.3)
        pane._draw.assert_called_once_with()
        self.assertGreater(pane.next_draw, 10.3)
        self.assertLessEqual(pane.next_draw - 10.3, 1 / 600 + 1e-9)

    def test_unavailable_native_timer_falls_back_to_tk(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None, display_hz=lambda _window: 540)
        pane.dimension.set("2d")
        pane._reset()
        pane.test_open = pane._visible = True
        pane.cursor.start()
        with patch("gamepad_aim.NativeAimTimer", side_effect=OSError("unavailable")) as native:
            pane._schedule(time.perf_counter())
        native.assert_called_once()
        self.assertIsInstance(pane.job, str)
        self.assertIsNone(pane._native_timer)
        pane.set_visible(False)
        self.assertIsNone(pane.job)

    def test_monitor_change_rephases_draws_without_moving_simulation(self):
        from gamepad_aim import AimTestPane

        refresh = 60
        pane = AimTestPane(self.root, str, lambda: None, display_hz=lambda _window: refresh)
        pane.test_open = True
        pane.next_frame = 1.025
        refresh = 540
        with patch("gamepad_aim.time.perf_counter", return_value=1.0):
            pane._refresh_display_rate(SimpleNamespace(widget=pane.window))
        self.assertEqual(pane.display_hz, 540)
        self.assertAlmostEqual(pane.next_draw - 1, 1 / 540)
        self.assertEqual(pane.next_frame, 1.025)

    def test_both_dimensions_share_the_three_drill_controls(self):
        from gamepad_aim import AimTestPane

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        pane = AimTestPane(frame, str, lambda: None)
        toolbar = pane.dimension_controls.master
        dimensions = toolbar.grid_slaves(row=0, column=0)[0].winfo_children()
        choices = {button.cget("text"): button for button in dimensions if isinstance(button, ttk.Radiobutton)}
        self.assertEqual(set(choices), {"2D 模式", "3D 模式"})
        drills = {button.cget("text"): button for button in pane.drill_controls.winfo_children()}
        self.assertEqual(set(drills), {"跟槍", "點靶", "限時點靶"})
        choices["2D 模式"].invoke()
        drills["跟槍"].invoke()
        self.assertEqual(pane._mode_key(), "cursor-tracking")
        drills["點靶"].invoke()
        self.assertEqual(pane._mode_key(), "cursor-shooting")
        drills["限時點靶"].invoke()
        self.assertEqual(pane._mode_key(), "timed-cursor")
        choices["3D 模式"].invoke()
        self.assertEqual(pane._mode_key(), "timed-shooting")
        self.root.deiconify()
        self.root.update()
        self.assertEqual(toolbar.grid_size()[1], 2)
        self.assertEqual(int(pane.drill_controls.grid_info()["row"]), 1)
        self.assertTrue(pane.mode_is_timed)
        self.assertEqual(pane.start_button.cget("text"), "開始")

    def test_page_shows_history_and_only_opens_the_selected_size_on_start(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        pane = AimTestPane(frame, str, lambda: None)
        pane._save_settings(AimSettings(fullscreen=False, window_size="960x540"))
        self.root.deiconify()
        self.root.update()
        self.assertTrue(pane.history_view.winfo_ismapped())
        self.assertFalse(pane.canvas.winfo_ismapped())
        clear = pane.history_view.clear_button
        actions = (clear, pane.settings_button, pane.start_button)
        self.assertEqual(len({button.winfo_width() for button in actions}), 1)
        self.assertTrue(all(int(button.cget("width")) < 18 for button in actions))
        self.assertEqual(len({button.winfo_rooty() for button in actions}), 1)
        self.assertLess(clear.winfo_rootx(), pane.settings_button.winfo_rootx())
        self.assertLess(pane.settings_button.winfo_rootx(), pane.start_button.winfo_rootx())
        self.assertEqual(pane.start_button.winfo_rooty() + pane.start_button.winfo_height(),
                         pane.drill_controls.winfo_rooty() + pane.drill_controls.winfo_height())
        self.assertTrue(clear.instate(["disabled"]))
        pane.history.add(pane.settings.basis("shooting", (960, 540)), {"hits": 1})
        self.assertFalse(clear.instate(["disabled"]))
        with patch("gamepad_aim_history.messagebox.askyesno", return_value=True):
            clear.invoke()
        self.assertFalse(pane.history.records)
        pane.output_ready = True
        pane._update_labels()
        self.assertIn("Enter", pane.connection_hint["text"])
        pane.start_button.invoke()
        self.root.update()
        self.assertTrue(pane.test_open)
        self.assertEqual(pane.size, (960, 540))
        self.assertFalse(pane.window.attributes("-fullscreen"))
        first, second = pane.restart_button, pane.close_button
        self.assertIsInstance(first, ttk.Button)
        self.assertIsInstance(second, ttk.Button)
        self.assertTrue(first.winfo_ismapped() and second.winfo_ismapped())
        self.assertGreater(first.winfo_x(), pane.size[0] / 2)
        self.assertEqual(first.winfo_y(), second.winfo_y())
        self.assertEqual(first.winfo_width(), second.winfo_width())
        self.assertEqual(second.winfo_x() - first.winfo_x() - first.winfo_width(), 6)

    def test_result_comparison_is_centered_and_popup_button_restarts(self):
        from gamepad_aim import AimTestPane

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        pane = AimTestPane(frame, str, lambda: None)
        self.root.deiconify()
        self.root.update()
        pane.dimension.set("2d")
        pane._reset()
        pane._open_test_window()
        pane.finished = True
        pane._show_changes((("命中 ↑ +1", True),))
        pane._update_labels()
        left, _, right, _ = pane.canvas.bbox(*pane.hud.change_items)
        self.assertAlmostEqual((left + right) / 2, pane.size[0] / 2, delta=1)
        self.assertEqual(pane.restart_button.cget("text"), "重新開始")
        pane.restart_button.invoke()
        self.root.update()
        self.assertTrue(pane.test_open)
        self.assertIsNotNone(pane.countdown_until)

    def test_timer_and_frame_rates_use_opposite_corners(self):
        from gamepad_aim import AimTestPane

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        pane = AimTestPane(frame, str, lambda: None)
        self.root.deiconify()
        self.root.update()
        pane._open_test_window()
        pane._frame_stats.reset(10)
        for stamp in (10.01, 10.02, 10.07):
            pane._frame_stats.submitted(stamp)
        pane._update_performance(10.1)
        self.assertEqual(pane.fps_text.get(), "FPS 30   99% Low 20")
        status, _, fps = (pane.canvas.bbox(item) for item in pane.hud.labels)
        timer = pane.canvas.bbox(pane.hud.timer_item)
        self.assertLess(timer[0], 25)
        self.assertLess(timer[1], 25)
        self.assertLess(pane.size[0] - fps[2], 25)
        self.assertLess(pane.size[1] - fps[3], 30)
        self.assertEqual(status[3], fps[3])
        self.assertLess(status[0], 25)
        self.assertTrue(all(
            pane.canvas.type(item) == "text" for item in pane.canvas.find_withtag(pane.hud.tag)
        ))

    def test_short_software_round_replaces_old_frame_rates_with_its_own_samples(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, target_count=1))
        pane._start()
        start = pane.countdown_until
        pane.fps_text.set("FPS 144   99% Low 120")
        pane._advance_countdown(start)
        self.assertEqual(pane.fps_text.get(), "")
        for offset in (0.02, 0.04):
            pane._record_submission(start + offset)
        x, y, _ = pane.cursor.target(*pane.size, pane._cursor_top())
        with patch("gamepad_aim.time.perf_counter", return_value=start + 0.1):
            pane._mouse_click(SimpleNamespace(x=x, y=y))
        with patch("gamepad_aim.time.perf_counter", return_value=start + 0.4):
            pane._draw_hit_effect(start + 0.4)
        self.assertTrue(pane.finished)
        self.assertEqual(pane.fps_text.get(), "FPS 5   99% Low 50")

    def test_defaults_to_3d_shooting_and_shows_fps_in_2d(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None)
        self.assertTrue(pane.mode_is_shooting)
        pane.dimension.set("2d")
        pane._reset()
        pane._open_test_window()
        pane.fps_started = time.perf_counter() - 0.6
        pane._tick()
        self.assertIn("FPS", pane.fps_text.get())

    def test_3d_question_mark_shows_help_on_hover(self):
        from gamepad_aim import AimTestPane

        frame = tk.Frame(self.root)
        frame.pack()
        pane = AimTestPane(frame, str, lambda: None)
        self.root.deiconify()
        self.root.update()
        help_mark = next(
            child for child in pane.dimension_controls.winfo_children() if child.cget("text") == "?"
        )
        self.assertIsInstance(help_mark, tk.Label)
        help_mark.event_generate("<Enter>")
        tip, = (child for child in self.root.winfo_children()
                if isinstance(child, tk.Toplevel) and child is not pane.window)
        text = tip.winfo_children()[0].cget("text").replace("\n", "")
        self.assertIn("跟靶率", text)
        self.assertIn("同設定", text)
        self.assertIn("靶色、準心顏色與大小不影響比對", text)
        self.assertIn("綠色代表進步，紅色代表退步", text)
        self.assertIn("−14.4 pp", text)
        self.assertIn("平均偏差是準心與靶心的平均距離", text)
        help_mark.event_generate("<Leave>")
        self.assertFalse(tip.winfo_exists())

    def test_fullscreen_score_and_comparison_use_adjacent_lines(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None)
        pane._open_test_window()
        pane._resize(SimpleNamespace(widget=pane._view, width=1280, height=720))
        pane.finished = True
        pane._show_changes((("命中 ↑ +1", True),))
        pane._update_labels()
        score = pane.hud.labels[1]
        comparison = pane.hud.change_items[0]
        gap = pane.canvas.bbox(comparison)[1] - pane.canvas.bbox(score)[3]
        self.assertGreaterEqual(gap, 0)
        self.assertLessEqual(gap, pane.hud.padding + 1)

    def test_fullscreen_start_uses_selected_display_mode_and_escape_restores_it(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        pane = AimTestPane(frame, str, lambda: None)
        pane._save_settings(AimSettings(fullscreen=True, fullscreen_size="1920x1080"))
        self.root.deiconify()
        self.root.update()
        pane.output_ready = True
        display_mode = SimpleNamespace(bounds=(0, 0, 1920, 1080), close=Mock())
        with patch("gamepad_aim.activate_fullscreen_mode", return_value=display_mode) as activate:
            pane._start()
        activate.assert_called_once_with(self.root.winfo_id(), (1920, 1080))
        self.root.update()
        self.assertTrue(pane.window.overrideredirect())
        self.assertFalse(self.root.attributes("-fullscreen"))
        self.assertEqual(pane.size, (pane.window.winfo_width(), pane.window.winfo_height()))
        self.assertEqual((pane.canvas.winfo_rootx(), pane.canvas.winfo_rooty()),
                         (pane.window.winfo_rootx(), pane.window.winfo_rooty()))
        pane.canvas.focus_force()
        self.root.update()
        pane.canvas.event_generate("<Escape>")
        self.root.update()
        self.assertFalse(pane.test_open)
        self.assertFalse(pane.window.winfo_ismapped())
        self.assertTrue(pane.history_view.winfo_ismapped())
        display_mode.close.assert_called_once_with()

    def test_fullscreen_without_a_mode_switch_uses_the_owner_monitor_origin(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        self.root.deiconify()
        self.root.update()
        pane._save_settings(AimSettings(fullscreen=True, fullscreen_size="1920x1080"))
        pane.output_ready = True
        with (
            patch("gamepad_aim.activate_fullscreen_mode", return_value=None),
            patch("gamepad_aim.fullscreen_monitor_bounds", return_value=(-1536, 80, 0, 944)),
        ):
            pane._start()
        self.root.update()
        self.assertTrue(pane.window.overrideredirect())
        self.assertEqual(pane.size, (1536, 864))
        self.assertEqual((pane.canvas.winfo_rootx(), pane.canvas.winfo_rooty()), (-1536, 80))

    def test_transient_display_restore_failure_can_be_retried(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None)
        mode = SimpleNamespace(close=Mock(side_effect=(OSError("temporary display error"), OSError("retry failed"), None)))
        pane._fullscreen_mode = mode
        with (
            self.assertLogs("gamepad_aim", level="ERROR"),
            patch.object(self.root, "after") as after,
        ):
            pane.close_test()
            self.assertIs(pane._fullscreen_mode, mode)
            after.assert_called_once()
            _, retry, *args = after.call_args.args
            retry(*args)
            self.assertEqual(after.call_count, 2)
            _, retry, *args = after.call_args.args
            retry(*args)
        self.assertEqual(mode.close.call_count, 3)
        self.assertIsNone(pane._fullscreen_mode)

    def test_destroyed_fullscreen_window_retries_restoring_its_display_mode(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None)
        mode = SimpleNamespace(close=Mock(side_effect=(OSError("temporary display error"), None)))
        pane._fullscreen_mode = mode
        with (
            self.assertLogs("gamepad_aim", level="ERROR"),
            patch.object(self.root, "after", return_value="display-retry") as after,
        ):
            pane.window.destroy()
            after.assert_called_once()
            _, retry, *args = after.call_args.args
            retry(*args)

        self.assertEqual(mode.close.call_count, 2)
        self.assertIsNone(pane._fullscreen_mode)

    def test_unsupported_fullscreen_mode_reports_error_without_starting(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane._save_settings(AimSettings(fullscreen=True, fullscreen_size="1920x1080"))
        pane.output_ready = True
        with (
            patch("gamepad_aim.activate_fullscreen_mode", side_effect=OSError("unsupported")),
            patch("gamepad_aim.messagebox.showerror") as error,
            self.assertLogs("gamepad_aim", level="ERROR"),
        ):
            pane._start()
        self.assertIn("1920x1080", error.call_args.args[1])
        self.assertFalse(pane.test_open)
        self.assertIsNone(pane.countdown_until)

    def test_failed_fullscreen_setup_schedules_rollback_retry(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_display import DisplayModeRestoreError
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane._save_settings(AimSettings(fullscreen=True, fullscreen_size="1920x1080"))
        mode = SimpleNamespace(close=Mock())
        with (
            patch("gamepad_aim.activate_fullscreen_mode", side_effect=DisplayModeRestoreError(mode, "rollback failed")),
            patch("gamepad_aim.messagebox.showerror"),
            self.assertLogs("gamepad_aim", level="ERROR"),
        ):
            pane._start()
        self.assertIs(pane._fullscreen_mode, mode)
        self.assertIsNotNone(pane._display_restore_job)
        self.assertFalse(pane.test_open)

    def test_fullscreen_auto_selects_highest_driver_resolution(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane._save_settings(AimSettings())
        pane.output_ready = True
        with (
            patch("gamepad_aim.available_resolutions", return_value=("1920x1080", "3840x2160", "2560x1440")),
            patch("gamepad_aim.activate_fullscreen_mode", return_value=None) as activate,
        ):
            pane._start()
        activate.assert_called_once_with(self.root.winfo_id(), (3840, 2160))

    @unittest.skipUnless(sys.platform == "win32", "Windows work area is required")
    def test_default_window_uses_work_area_and_manual_size_reopens_normally(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        self.root.deiconify()
        self.root.update()
        pane.output_ready = True
        with patch("gamepad_aim.activate_fullscreen_mode") as activate:
            pane._start()
        self.root.update()
        self.assertEqual(pane.window.state(), "zoomed")
        self.assertEqual(pane.size, (pane.window.winfo_width(), pane.window.winfo_height()))
        self.assertFalse(pane.window.overrideredirect())
        activate.assert_not_called()
        pane.close_test()
        pane._save_settings(AimSettings(fullscreen=False, window_size="960x540"))
        pane._start()
        self.root.update()
        self.assertEqual(pane.window.state(), "normal")
        self.assertEqual(pane.size, (960, 540))

    def test_manual_window_fits_the_owner_monitor_when_opened_near_its_edge(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        for resolution, bounds, owner_x in (
            ("960x540", (-1280, 40, 0, 760), -350),
            ("2560x1440", (1920, 40, 3200, 760), 2800),
        ):
            with self.subTest(resolution=resolution, bounds=bounds):
                frame = tk.Frame(self.root)
                self.addCleanup(frame.destroy)
                pane = AimTestPane(frame, str, lambda: None)
                pane._save_settings(AimSettings(fullscreen=False, window_size=resolution))
                with (
                    patch("gamepad_aim.window_work_area", return_value=bounds, create=True),
                    patch.object(pane.owner, "winfo_rootx", return_value=owner_x),
                    patch.object(pane.owner, "winfo_rooty", return_value=365),
                    patch.object(pane.owner, "winfo_x", return_value=owner_x - 8),
                    patch.object(pane.owner, "winfo_y", return_value=333),
                    patch.object(pane.owner, "winfo_width", return_value=320),
                    patch.object(pane.owner, "winfo_height", return_value=380),
                    patch.object(pane.window, "geometry", wraps=pane.window.geometry) as geometry,
                ):
                    self.assertTrue(pane._open_test_window())
                size, x, y = geometry.call_args_list[0].args[0].split("+")
                width, height = map(int, size.split("x"))
                x, y = int(x), int(y)
                left, top, right, bottom = bounds
                self.assertGreaterEqual(x, left)
                self.assertGreaterEqual(y, top)
                self.assertLessEqual(x + width + 16, right)
                self.assertLessEqual(y + height + 40, bottom)
                if resolution == "960x540":
                    self.assertEqual((width, height), (960, 540))
                else:
                    self.assertAlmostEqual(width, height * 16 / 9, delta=1)
                pane.close_test()

    def test_controller_b_closes_the_aim_window_on_a_new_press(self):
        from gamepad_aim import AimTestPane

        buttons = ("B",)
        state = lambda: SimpleNamespace(buttons=buttons, right=(0, 0), right_trigger=0)
        pane = AimTestPane(self.root, str, lambda: ("device", state()))
        pane._schedule = lambda _now: None
        pane.output_ready = True
        self.root.deiconify()
        self.root.update()
        pane._tick()
        pane._start()
        self.root.update()
        pane._tick()
        self.assertTrue(pane.test_open)
        buttons = ()
        pane.next_frame = 0
        pane._tick()
        buttons = ("B",)
        pane.next_frame = 0
        pane._tick()
        self.assertFalse(pane.test_open)
        self.assertIsNone(pane.countdown_until)

    def test_timed_targets_draw_two_live_balls_and_record_the_fixed_duration(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        rendered = []
        renderer = SimpleNamespace(
            draw=lambda _width, _height, **parts: rendered.append(parts["balls"]),
            close=lambda: None,
        )
        pane = AimTestPane(frame, str, lambda: None, gpu_renderer=lambda *_: renderer)
        self.root.deiconify()
        self.root.update()
        pane.mode.set("timed-shooting")
        pane._save_settings(AimSettings(fullscreen=False, timed_shooting_seconds=12, seed=0))
        pane.output_ready = True
        pane._start()
        started = pane.countdown_until
        pane._advance_countdown(started)
        now = started + 0.5
        pane._advance_point_clock(now)
        live = pane.scene.target_positions()
        self.assertEqual(len(live), 2)
        pane.next_draw = 0
        pane._draw_if_due(now)
        self.assertEqual(len(rendered[-1]), 2)
        x, y, z = live[0]
        pane.scene.yaw = math.atan2(x, z)
        pane.scene.pitch = math.atan2(y, math.hypot(x, z))
        pane._shoot(now)
        self.assertEqual(pane.scene.hits, 1)
        self.assertEqual(pane.scene.target_positions(), live[1:])
        pane._draw_hit_effect(started + 12)
        pane._advance_point_clock(started + 12)
        pane._check_finish()
        record, = pane.history.records
        self.assertEqual(record["basis"]["mode"], "timed-shooting")
        self.assertEqual(record["basis"]["targets"], 0)
        self.assertEqual(record["metrics"]["seconds"], 12)
        self.assertTrue(pane.finished)

    def test_fullscreen_native_buttons_do_not_score_canvas_clicks(self):
        from gamepad_aim import AimTestPane

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        pane = AimTestPane(frame, str, lambda: None)
        self.root.deiconify()
        self.root.update()
        pane.dimension.set("2d")
        pane._reset()
        pane._start()
        pane._advance_countdown(pane.countdown_until)
        pane.canvas.event_generate("<Button-1>", x=10, y=10)
        self.assertEqual(pane.cursor.shots, 1)
        pane.canvas.event_generate("<Button-1>", x=18, y=pane.size[1] - 20)
        self.assertEqual(pane.cursor.shots, 2)
        pane.canvas.focus_force()
        self.root.update()
        pane.canvas.event_generate("<Tab>")
        self.root.update()
        self.assertIs(pane.canvas.focus_displayof(), pane.canvas)
        start = pane.restart_button
        x, y = start.winfo_width() // 2, start.winfo_height() // 2
        start.event_generate("<Enter>", x=x, y=y)
        self.assertTrue(start.instate(["active"]))
        start.event_generate("<Button-1>", x=x, y=y)
        self.assertTrue(start.instate(["pressed"]))
        self.assertEqual(pane.cursor.shots, 2)
        self.assertTrue(pane.cursor.running)
        start.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.assertIsNotNone(pane.countdown_until)
        self.assertFalse(pane.cursor.running)

    def test_2d_timed_targets_work_with_stick_and_windows_cursor(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        for source in ("stick", "mouse"):
            with self.subTest(source=source):
                frame = tk.Frame(self.root)
                pane = AimTestPane(frame, str, lambda: None)
                pane.dimension.set("2d")
                pane.mode.set("timed-shooting")
                pane._save_settings(AimSettings(fullscreen=False, timed_shooting_seconds=5))
                pane.output_ready = source == "stick"
                pane._start()
                start = pane.countdown_until
                pane._advance_countdown(start)
                viewport = (*pane.size, pane._cursor_top())
                initial = pane.cursor.target_positions(*viewport)
                self.assertEqual(len(initial), 2)
                x, y, _ = initial[0]
                if source == "stick":
                    pane.cursor.x, pane.cursor.y = x, y
                    pane._shoot(start + 0.01)
                else:
                    with patch("gamepad_aim.time.perf_counter", return_value=start + 0.01):
                        pane._mouse_click(SimpleNamespace(x=x, y=y))
                pane._advance_point_clock(start + 0.5)
                self.assertEqual(len(pane.cursor.target_positions(*viewport)), 2)
                self.assertEqual(pane.cursor.target_positions(*viewport)[0], initial[1])
                self.assertEqual(pane.cursor.hits, 1)
                self.assertEqual(pane.round_basis["mode"], "timed-cursor")
                self.assertEqual(pane.round_basis["targets"], 0)
                frame.destroy()

    def test_fullscreen_mouse_target_stays_below_controls_on_short_viewport(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None)
        pane.dimension.set("2d")
        pane._reset()
        pane._start()
        pane._resize(SimpleNamespace(width=640, height=360))
        pane._advance_countdown(pane.countdown_until)
        for _ in range(10):
            x0, y0, x1, y1 = pane.canvas.coords(pane._ball_items[0])
            pane._mouse_click(SimpleNamespace(x=(x0 + x1) / 2, y=(y0 + y1) / 2))
        self.assertEqual(pane.cursor.hits, 10)

    def test_target_contact_recolors_the_whole_ball_then_restores_the_custom_color(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, target_color="#fa5544", target_on_target_color="#4d8dff"))
        pane._draw_balls(((100, 100, 12),), on_target=True)
        self.assertEqual(pane.canvas.itemcget(pane._ball_items[0], "fill"), "#4d8dff")
        self.assertEqual(float(pane.canvas.itemcget(pane._ball_items[0], "width")), 1.0)
        outline = pane.canvas.itemcget(pane._ball_items[0], "outline")
        self.assertTrue(outline)
        pane._draw_balls(((100, 100, 12),), on_target=False)
        self.assertEqual(pane.canvas.itemcget(pane._ball_items[0], "fill"), "#fa5544")
        self.assertEqual(pane.canvas.itemcget(pane._ball_items[0], "outline"), outline)
        pane._save_settings(AimSettings(fullscreen=False, target_color="#172231"))
        pane._draw_balls(((100, 100, 12),))
        self.assertNotEqual(pane.canvas.itemcget(pane._ball_items[0], "outline"), outline)

    def test_idle_controller_axes_do_not_move_the_cursor_or_camera(self):
        from gamepad_aim import AimTestPane
        from gamepad_devices import GamepadState

        state = GamepadState(1, 0, (), (0, 0), (0.7, 0.8), 0, 0)
        pane = AimTestPane(self.root, str, lambda: ("device", state))
        pane._schedule = lambda _now: None
        pane.device_key = "device"
        with patch("gamepad_aim.time.perf_counter", return_value=1.1):
            pane.last_frame = 1
            pane._tick()
        self.assertIsNone(pane.cursor.x)
        pane.dimension.set("3d")
        pane._reset()
        pane.next_frame = 0
        with patch("gamepad_aim.time.perf_counter", return_value=1.2):
            pane._tick()
        self.assertEqual((pane.scene.yaw, pane.scene.pitch), (0, 0))

    def test_3d_target_contact_only_colors_reticle_and_hit_triggers_a_burst(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_model import TARGET_RADIUS
        from gamepad_aim_settings import AimSettings

        calls = []
        feedback = SimpleNamespace(
            stop=lambda: None, close=lambda: None,
            contact=lambda *args: calls.append("contact"), hit=lambda *args: calls.append("hit"),
            miss=lambda sound: calls.append(("miss", sound)),
        )
        pane = AimTestPane(self.root, str, lambda: None, feedback=feedback)
        pane.dimension.set("3d")
        pane.mode.set("shooting")
        pane._save_settings(AimSettings(fullscreen=False, depth_shading=False, reticle_on_target_color="#ff467a"))
        pane.output_ready = True
        pane._start()
        pane._shoot()
        self.assertEqual(calls, [])
        start_at = pane.countdown_until
        pane._advance_countdown(start_at)
        pane.shooting.yaw = math.atan2(2, 8)
        pane._draw()
        pane._update_feedback(start_at)
        self.assertEqual(calls, [])
        self.assertEqual(pane.canvas.itemcget(pane.reticle[0], "fill"), "#ff467a")
        self.assertEqual(pane.canvas.itemcget(pane._ball_items[0], "fill"), pane.settings.target_color)
        pane.shooting.yaw = 0.0
        pane._shoot(start_at + 0.05)
        self.assertEqual(calls, [("miss", True)])
        self.assertIsNone(pane.hit_effect)
        pane.shooting.yaw = math.atan2(2, 8)
        pane._shoot(start_at + 0.1)
        self.assertEqual(calls, [("miss", True), "hit"])
        impact = pane.hit_effect[1]
        self.assertAlmostEqual(math.dist(impact, (2, 0, 8)), TARGET_RADIUS)
        camera_x, camera_y, _ = pane.scene.camera_transform()(impact)
        self.assertAlmostEqual(camera_x, 0)
        self.assertAlmostEqual(camera_y, 0)
        pane._draw_hit_effect(start_at + 0.15)
        self.assertEqual(pane.canvas.itemcget(pane.hit_particles[0], "state"), "normal")
        fragment_x = pane.canvas.coords(pane.hit_particles[0])[0]
        pane.shooting.yaw += 0.1
        pane._draw_hit_effect(start_at + 0.15)
        self.assertLess(pane.canvas.coords(pane.hit_particles[0])[0], fragment_x)
        pane._draw_hit_effect(start_at + 0.4)
        self.assertEqual(pane.canvas.itemcget(pane.hit_particles[0], "state"), "hidden")

    def test_hit_burst_varies_between_hits_and_keeps_its_pattern_through_redraws(self):
        from gamepad_aim import HIT_DURATION, AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, target_count=3))
        pane._start()
        started = pane.countdown_until
        pane._advance_countdown(started)
        patterns = []
        for shot, angle in enumerate((0, math.pi / 2), 1):
            x, y, _ = pane.cursor.target(*pane.size, pane._cursor_top())
            pane.cursor.x, pane.cursor.y = x, y
            now = started + shot
            magnitudes = (1.1, 0.75, 1.2, 1.15, 0.8, 0.9, 1.3, 0.8, 0.95, 1)
            with patch("gamepad_aim.random.uniform", side_effect=(angle, *magnitudes)):
                pane._shoot(now)
            pane._draw_hit_effect(now + HIT_DURATION / 2)
            positions = [pane.canvas.coords(item) for item in pane.hit_particles]
            pane._draw_hit_effect(now + HIT_DURATION / 2)
            self.assertEqual(positions, [pane.canvas.coords(item) for item in pane.hit_particles])
            self.assertGreater(len({round(coords[2] - coords[0], 3) for coords in positions}), 1)
            patterns.append([(round(coords[2] - x, 3), round(coords[3] - y, 3)) for coords in positions])
        self.assertNotEqual(*patterns)

    def test_edge_hit_burst_starts_at_the_contact_and_travels_farther_inward(self):
        from gamepad_aim import AimTestPane, HIT_DURATION
        from gamepad_aim_model import TARGET_RADIUS
        from gamepad_aim_settings import AimSettings

        for dimension in ("2d", "3d"):
            with self.subTest(dimension=dimension):
                frame = tk.Frame(self.root)
                self.addCleanup(frame.destroy)
                pane = AimTestPane(frame, str, lambda: None)
                pane.dimension.set(dimension)
                pane._save_settings(AimSettings(fullscreen=False))
                pane.countdown_until = 100
                pane._advance_countdown(100)
                if dimension == "2d":
                    x, y, radius = pane.cursor.target(*pane.size)
                    x += 0.85 * radius
                    pane.cursor.x, pane.cursor.y = x, y
                else:
                    target_x, _, target_z = pane.scene.target_position()
                    pane.scene.yaw = math.atan2(target_x, target_z) + math.asin(
                        0.85 * TARGET_RADIUS / math.hypot(target_x, target_z)
                    )
                    x, y = (length / 2 for length in pane.size)
                with patch("gamepad_aim.random.uniform", side_effect=(0, *([1.0] * 10))):
                    pane._shoot(100.01)
                self.assertEqual(pane._round().hits, 1)
                if dimension == "2d":
                    pane.cursor.x, pane.cursor.y = x + 50, y + 50

                with patch.object(pane, "gpu", object()):
                    pane._draw_hit_effect(100.01)
                    for px, py, _, _ in pane._gpu_fragments:
                        self.assertAlmostEqual(px, x)
                        self.assertAlmostEqual(py, y)
                    pane._draw_hit_effect(100.01 + HIT_DURATION / 2)
                    near_x, near_y, _, _ = pane._gpu_fragments[0]
                    far_x, far_y, _, _ = pane._gpu_fragments[3]
                    self.assertGreater(near_x, x)
                    self.assertLess(far_x, x)
                    self.assertGreater(
                        math.hypot(far_x - x, far_y - y),
                        4 * math.hypot(near_x - x, near_y - y),
                    )

    def test_controller_test_hides_system_pointer_except_over_fullscreen_controls(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None)
        pane.output_ready = True
        pane._start()
        self.assertEqual(pane.canvas.cget("cursor"), "none")
        self.assertIs(pane.restart_button.master, pane.canvas.master)
        self.assertEqual(str(pane.restart_button.cget("cursor")), "")
        pane.dimension.set("2d")
        pane._reset()
        pane._start()
        self.assertEqual(pane.canvas.cget("cursor"), "none")

    def test_final_target_effect_plays_before_results_in_2d_and_3d(self):
        from gamepad_aim import AimTestPane, HIT_DURATION
        from gamepad_aim_settings import AimSettings

        for dimension in ("2d", "3d"):
            with self.subTest(dimension=dimension):
                frame = tk.Frame(self.root)
                pane = AimTestPane(frame, str, lambda: None)
                pane.dimension.set(dimension)
                pane._save_settings(AimSettings(fullscreen=False, target_count=1))
                pane.output_ready = True
                pane._start()
                start = pane.countdown_until
                pane._advance_countdown(start)
                if dimension == "2d":
                    x, y, _ = pane.cursor.target(*pane.size, pane._cursor_top())
                    pane.cursor.x, pane.cursor.y = x, y
                else:
                    pane.scene.yaw = math.atan2(2, 8)
                shot = start + 0.2
                pane._shoot(shot)
                self.assertTrue(pane._round().completed)
                self.assertFalse(pane.finished)
                self.assertFalse(pane.history.records)
                pane._draw_hit_effect(shot + HIT_DURATION / 2)
                self.assertEqual(pane.canvas.itemcget(pane.hit_particles[0], "state"), "normal")
                self.assertFalse(pane.finished)
                pane._draw_hit_effect(shot + HIT_DURATION + 0.01)
                self.assertEqual(pane.canvas.itemcget(pane.hit_particles[0], "state"), "hidden")
                self.assertTrue(pane.finished)
                self.assertEqual(len(pane.history.records), 1)
                frame.destroy()

    def test_2d_reticle_and_target_sizes_are_applied_independently(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, reticle_size=26, target_size_2d=14))
        x1, _, x2, _ = pane.canvas.coords(pane.reticle[0])
        self.assertEqual(x2 - x1, 26)
        pane.cursor.start()
        target = pane.cursor.target(640, 360)
        self.assertAlmostEqual(target[2] * 2, 50.4)
        pane.cursor.click(target[0] + 20, target[1], 640, 360)
        self.assertEqual(pane.cursor.hits, 1)

    def test_3d_mode_shoots_once_per_rt_press(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings
        from gamepad_devices import GamepadState

        state = GamepadState(1, 0, (), (0.0, 0.0), (0.0, 0.0), 0.0, 0.0)
        pane = AimTestPane(self.root, str, lambda: ("device", state))
        pane._schedule = lambda _now: None
        pane.dimension.set("3d")
        pane.mode.set("shooting")
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450"))
        pane.device_key = "device"
        pane.output_ready = True
        pane._start()
        started = pane.countdown_until
        pane._advance_countdown(started)
        pane.last_frame = pane.next_frame = started
        pane.shooting.yaw = math.atan2(2, 8)
        with patch("gamepad_aim.time.perf_counter", return_value=started):
            pane._tick()
        state = GamepadState(2, 0, (), (0.0, 0.0), (0.0, 0.0), 0.0, 1.0)
        for offset in (0.01, 0.02):
            with patch("gamepad_aim.time.perf_counter", return_value=started + offset):
                pane._tick()
        self.assertEqual((pane.shooting.hits, pane.shooting.shots), (1, 1))

    def test_space_used_to_start_a_round_does_not_also_fire(self):
        from gamepad_aim import AimTestPane
        from gamepad_devices import GamepadState

        frame = tk.Frame(self.root)
        frame.pack()
        state = GamepadState(1, 0, (), (0.0, 0.0), (0.0, 0.0), None, None)
        pane = AimTestPane(frame, str, lambda: ("device", state))
        self.root.deiconify()
        self.root.update()
        pane.dimension.set("3d")
        pane.mode.set("shooting")
        pane.device_key = "device"
        pane.output_ready = True
        pane._reset()
        pane.start_button.focus_force()
        self.root.update()
        pane.start_button.event_generate("<KeyPress-space>")
        self.assertIsNone(pane.countdown_until)
        pane.start_button.event_generate("<KeyRelease-space>")
        pane._advance_countdown(pane.countdown_until)
        self.assertTrue(pane.shooting.running)
        pane.canvas.event_generate("<KeyRelease-space>")
        self.assertEqual(pane.shooting.shots, 0)

    def test_countdown_shows_three_two_one_before_accepting_shots(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None)
        pane.dimension.set("3d")
        pane.mode.set("shooting")
        pane._reset()
        pane.output_ready = True
        pane._start()
        deadline = pane.countdown_until
        for offset, text in ((3, "3"), (2, "2"), (1, "1")):
            pane._advance_countdown(deadline - offset)
            self.assertEqual(pane.canvas.itemcget(pane.countdown_item, "text"), text)
            self.assertTrue(all(value < 0 for value in pane.canvas.coords(pane.reticle[0])))
            pane._shoot()
            self.assertEqual(pane.shooting.shots, 0)
        pane._advance_countdown(deadline)
        self.assertEqual(pane.canvas.itemcget(pane.countdown_item, "text"), "")
        self.assertTrue(pane.shooting.running)
        pane._shoot()
        self.assertEqual(pane.shooting.shots, 1)

    def test_enter_and_controller_start_open_a_single_countdown(self):
        from gamepad_aim import AimTestPane
        from gamepad_devices import GamepadState

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        buttons = ()

        def read():
            return ("device", GamepadState(
                1, 0, buttons, (0.0, 0.0), (0.0, 0.0), None, None,
            ))

        pane = AimTestPane(frame, str, read)
        self.root.deiconify()
        self.root.update()
        pane.start_button.focus_force()
        self.root.update()
        pane.device_key = "device"
        pane.output_ready = True
        pane._update_labels()
        pane.start_button.event_generate("<KeyPress-Return>")
        self.assertIsNone(pane.countdown_until)
        pane.start_button.event_generate("<KeyRelease-Return>")
        self.assertIsNotNone(pane.countdown_until)
        pane._advance_countdown(pane.countdown_until)
        pane._view.event_generate("<KeyPress-Return>")
        pane._view.event_generate("<KeyPress-Return>")
        self.assertTrue(pane.shooting.running)
        self.assertIsNone(pane.countdown_until)
        pane._view.event_generate("<KeyRelease-Return>")
        self.assertIsNotNone(pane.countdown_until)
        pane._reset()
        pane.owner.after_cancel(pane.job)
        pane._schedule = lambda _now: None
        pane.next_frame = 0
        pane._tick()
        buttons = ("START",)
        pane.next_frame = 0
        pane._tick()
        deadline = pane.countdown_until
        self.assertIsNotNone(deadline)
        pane.next_frame = 0
        pane._tick()
        self.assertEqual(pane.countdown_until, deadline)
        pane.job = None

    def test_completed_round_shows_colored_changes_only_on_the_same_basis(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_history import AimHistory
        from gamepad_aim_settings import AimSettings

        history = AimHistory(None)
        pane = AimTestPane(self.root, str, lambda: None, history=history)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, target_count=1))

        def finish(miss):
            pane._start()
            pane._advance_countdown(pane.countdown_until)
            x, y, _ = pane.cursor.target(*pane.size, pane._cursor_top())
            if miss:
                pane._mouse_click(SimpleNamespace(x=x + 80, y=y))
            pane._mouse_click(SimpleNamespace(x=x, y=y))
            pane._draw_hit_effect(pane.hit_effect[0] + 1)
            self.assertEqual(pane.canvas.itemcget(pane.countdown_item, "text"), "測試完成")
            self.assertEqual(
                pane.canvas.itemcget(pane.finish_hint_item, "text"),
                "按 Enter 或手把 START 再測一次。",
            )
            hint_x, hint_y = pane.canvas.coords(pane.finish_hint_item)
            self.assertEqual(hint_x, pane.size[0] / 2)
            self.assertGreater(hint_y, pane.canvas.bbox(pane.countdown_item)[3])
            self.assertFalse(pane.canvas.find_withtag("finish_outline"))
            self.assertFalse(any(pane._ball_bounds))
            self.assertEqual(pane.status.get(), "")

        finish(True)
        self.assertEqual(len(history.records), 1)
        finish(False)
        self.assertIn(("命中率 ↑ +50.0 pp", True), pane.change_parts)
        self.assertTrue(any(improved and "↓" in text for text, improved in pane.change_parts))
        pane._save_settings(AimSettings(fullscreen=False, target_count=1, seed=17))
        finish(False)
        self.assertEqual(pane.change_parts, (("同基準尚無紀錄", None),))
        self.assertEqual(len(history.records), 3)

    @unittest.skipUnless(sys.platform == "win32", "Windows private fonts")
    def test_result_uses_bundled_font_for_chinese_english_and_symbols(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings
        from localization import translate_text

        for language in ("zh-TW", "en"):
            with self.subTest(language=language):
                pane = AimTestPane(
                    self.root, lambda text: translate_text(text, language), lambda: None,
                )
                pane.dimension.set("2d")
                pane._save_settings(AimSettings(fullscreen=False, target_count=1))
                pane._start()
                pane._advance_countdown(pane.countdown_until)
                x, y, _ = pane.cursor.target(*pane.size, pane._cursor_top())
                pane._mouse_click(SimpleNamespace(x=x, y=y))
                pane._draw_hit_effect(pane.hit_effect[0] + 1)
                pane._show_changes((("命中率 ↑ +50.0 pp", True),))
                pane.fps_text.set("FPS 120   99% Low 118")
                pane._update_hud()

                self.assertTrue(pane.finished)
                title = pane.countdown_item
                for item in (
                    title, *pane.hud.labels[1:], pane.hud.timer_item,
                    pane.finish_hint_item, *pane.hud.change_items,
                ):
                    spec = pane.canvas.itemcget(item, "font")
                    text = pane.canvas.itemcget(item, "text")
                    self.assertTrue(text)
                    weight = pane.canvas.tk.call("font", "actual", spec, "-weight")
                    self.assertEqual(weight, "bold" if item == title else "normal")
                    for char in set(text) - {"\n"}:
                        actual = pane.canvas.tk.call(
                            "font", "actual", spec, "-displayof", pane.canvas, "-family", "--", char,
                        )
                        self.assertEqual(actual, "S2P Aim Sans", (language, text, char))
                pane.restart_button.invoke()
                self.assertEqual(
                    pane.canvas.itemcget(pane.hud.timer_item, "font"), "TkDefaultFont",
                )
                pane.close_test()

    def test_finishing_round_when_history_clear_button_is_destroyed(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_history import AimHistory
        from gamepad_aim_settings import AimSettings

        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            pane = AimTestPane(self.root, str, lambda: None, history=AimHistory(path))
            pane.dimension.set("2d")
            pane._save_settings(AimSettings(fullscreen=False, window_size="800x450", target_count=1))
            pane._start()
            started = pane.countdown_until
            pane._advance_countdown(started)
            pane.cursor.x, pane.cursor.y, _ = pane.cursor.target(*pane.size, pane._cursor_top())
            pane._shoot(started + 0.05)
            pane.history_view.clear_button.destroy()
            pane._draw_hit_effect(started + 1)

            self.assertTrue(pane.finished)
            self.assertIn("命中 1/1", pane.score.get())
            self.assertTrue(pane.history_view.tree.get_children())
            record, = AimHistory(path).records
            self.assertEqual(record["metrics"]["hits"], 1)
            pane.close_test()
            self.assertEqual(len(AimHistory(path).records), 1)

    def test_write_failure_still_shows_score_and_allows_restart(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_history import AimHistory
        from gamepad_aim_settings import AimSettings

        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            pane = AimTestPane(self.root, str, lambda: None, history=AimHistory(path))
            pane.dimension.set("2d")
            pane._save_settings(AimSettings(fullscreen=False, window_size="800x450", target_count=1))

            def finish():
                pane._start()
                started = pane.countdown_until
                pane._advance_countdown(started)
                pane.cursor.x, pane.cursor.y, _ = pane.cursor.target(*pane.size, pane._cursor_top())
                pane._shoot(started + 0.05)
                pane._draw_hit_effect(started + 1)

            with patch("gamepad_aim_history.atomic_write_bytes", side_effect=PermissionError("history is locked")):
                finish()
            self.assertTrue(pane.finished)
            self.assertIn("命中 1/1", pane.score.get())
            self.assertIn("無法儲存這筆紀錄", pane.status.get())
            self.assertFalse(path.exists())
            self.assertFalse(pane.change_parts)
            self.assertFalse(pane.restart_button.instate(("disabled",)))

            finish()
            self.assertTrue(pane.finished)
            self.assertFalse(pane.history_error)
            self.assertEqual(len(AimHistory(path).records), 1)
            pane.close_test()

    def test_result_text_fits_a_small_window_at_high_dpi_after_resize(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings
        from localization import translate_text

        self.root.tk.call("tk", "scaling", 4.0)
        for language in ("en", "zh-TW"):
            pane = AimTestPane(self.root, lambda text: translate_text(text, language), lambda: None)
            pane.dimension.set("2d")
            pane._save_settings(AimSettings(fullscreen=False, window_size="800x450", target_count=1))
            pane._start()
            pane._advance_countdown(pane.countdown_until)
            x, y, _ = pane.cursor.target(*pane.size, pane._cursor_top())
            pane._mouse_click(SimpleNamespace(x=x, y=y))
            pane._draw_hit_effect(pane.hit_effect[0] + 1)
            self.assertTrue(pane.finished)

            for width, height in ((640, 360), (800, 450)):
                with self.subTest(language=language, size=(width, height)):
                    pane._resize(SimpleNamespace(widget=pane.canvas, width=width, height=height))
                    groups = [
                        pane.canvas.bbox(pane.countdown_item),
                        pane.canvas.bbox(pane.hud.labels[1]),
                        pane.canvas.bbox(*pane.hud.change_items),
                        pane.canvas.bbox(pane.finish_hint_item),
                    ]
                    for left, top, right, bottom in groups:
                        self.assertGreaterEqual(left, 0)
                        self.assertGreaterEqual(top, 0)
                        self.assertLessEqual(right, width)
                        self.assertLessEqual(bottom, height)
                        self.assertAlmostEqual((left + right) / 2, width / 2, delta=1)
                    for earlier, later in zip(groups, groups[1:]):
                        self.assertLess(earlier[3], later[1])
            pane.restart_button.invoke()
            self.assertEqual(float(pane.canvas.itemcget(pane.countdown_item, "width")), 0)
            pane.close_test()

    def test_all_modes_clear_targets_at_completion(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        settings = AimSettings(fullscreen=False, window_size="800x450", target_seconds=1)
        for dimension in ("2d", "3d"):
            for mode in ("tracking", "shooting", "timed-shooting"):
                with self.subTest(dimension=dimension, mode=mode):
                    pane.dimension.set(dimension)
                    pane.mode.set(mode)
                    pane._save_settings(settings)
                    pane._start()
                    pane._advance_countdown(pane.countdown_until)
                    self.assertTrue(any(pane._ball_bounds))
                    test = pane._round()
                    if mode != "tracking":
                        test.advance_time(test.duration)
                    elif dimension == "3d":
                        test.advance((0, 0), test.duration)
                    else:
                        test.advance(test.duration, *pane.size, pane._cursor_top())
                    pane._check_finish()
                    self.assertTrue(pane.finished)
                    self.assertFalse(any(pane._ball_bounds))
                    self.assertFalse(pane.canvas.find_withtag("finish_outline"))

    def test_mouse_shot_after_timeout_cannot_be_scored_between_ticks(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, target_count=1, target_seconds=1))
        pane._start()
        begun = pane.countdown_until
        pane._advance_countdown(begun)
        pane._simulate_to(begun + 0.99)
        x, y, _ = pane.cursor.target(*pane.size, pane._cursor_top())
        with patch("gamepad_aim.time.perf_counter", return_value=begun + 1.01):
            pane._mouse_click(SimpleNamespace(x=x, y=y))
        self.assertTrue(pane.finished)
        self.assertEqual((pane.cursor.hits, pane.cursor.shots, pane.cursor.elapsed), (0, 0, 1))

    def test_mouse_and_space_cannot_score_before_the_first_tick_after_a_stall(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        for action in ("mouse", "space"):
            with self.subTest(action=action):
                frame = tk.Frame(self.root)
                self.addCleanup(frame.destroy)
                pane = AimTestPane(frame, str, lambda: None)
                pane.dimension.set("2d")
                pane._save_settings(AimSettings(fullscreen=False, window_size="800x450", target_count=1))
                pane._start()
                start = pane.countdown_until
                pane._advance_countdown(start)
                x, y, _ = pane.cursor.target(*pane.size, pane._cursor_top())
                pane.cursor.x, pane.cursor.y = x, y
                pane._space_down(None)

                with patch("gamepad_aim.time.perf_counter", return_value=start + 0.4):
                    if action == "mouse":
                        pane._mouse_click(SimpleNamespace(x=x, y=y))
                    else:
                        pane._space_shot(None)
                self.assertEqual((pane.cursor.hits, pane.cursor.shots), (0, 0))
                self.assertTrue(pane.interrupted)
                self.assertFalse(pane.history.records)
                self.assertFalse(pane.space_armed)

    def test_expired_round_is_saved_when_leaving_before_the_next_tick(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        for dimension, mode in (("2d", "shooting"), ("2d", "tracking"), ("3d", "tracking")):
            for action in ("close", "restart", "focus", "resize", "switch-mode", "hide", "destroy"):
                with self.subTest(dimension=dimension, mode=mode, action=action):
                    frame = tk.Frame(self.root)
                    self.addCleanup(frame.destroy)
                    pane = AimTestPane(frame, str, lambda: None)
                    pane.dimension.set(dimension)
                    pane.mode.set(mode)
                    pane._save_settings(AimSettings(
                        fullscreen=False, window_size="800x450", target_seconds=1, tracking_seconds=1,
                    ))
                    pane._start()
                    start = pane.countdown_until
                    pane._advance_countdown(start)
                    pane._simulate_to(start + 0.99)
                    self.assertTrue(pane._round().running)
                    expected_mode = pane.round_basis["mode"]

                    with patch("gamepad_aim.time.perf_counter", return_value=start + 1.001):
                        if action == "close":
                            pane.close_test()
                        elif action == "restart":
                            pane._start()
                        elif action == "focus":
                            with patch.object(pane.window, "focus_displayof", return_value=None):
                                pane._view_lost_focus(SimpleNamespace(widget=pane._view))
                        elif action == "resize":
                            pane._resize(SimpleNamespace(widget=pane._view, width=900, height=500))
                        elif action == "switch-mode":
                            pane.mode.set("tracking" if mode == "shooting" else "shooting")
                            pane._change_test_mode()
                        elif action == "hide":
                            pane.set_visible(False)
                        else:
                            pane.window.destroy()

                    self.assertEqual(len(pane.history.records), 1)
                    record, = pane.history.records
                    self.assertEqual(record["basis"]["mode"], expected_mode)
                    self.assertEqual(record["metrics"]["seconds"], 1)
                    self.assertIn("tracking" if mode == "tracking" else "hits", record["metrics"])
                    pane.close_test()
                    self.assertEqual(len(pane.history.records), 1)

    def test_buffered_cancel_or_restart_before_timeout_interrupts_even_when_processed_late(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        for button in ("B", "START"):
            with self.subTest(button=button):
                frame = tk.Frame(self.root)
                self.addCleanup(frame.destroy)
                pane = AimTestPane(frame, str, lambda: None)
                pane.mode.set("tracking")
                pane._save_settings(AimSettings(fullscreen=False, window_size="800x450", tracking_seconds=1))
                pane._start()
                start = pane.countdown_until
                pane._advance_countdown(start)
                pane._simulate_to(start + 0.98)
                pane._visible = True
                pane.start_pressed = pane.cancel_pressed = False
                pressed = SimpleNamespace(buttons=(button,), right=(0.0, 0.0), right_trigger=0.0)
                pane._input_events.append((start + 0.99, pressed))
                with (
                    patch.object(pane.owner, "focus_displayof", return_value=pane.canvas),
                    patch.object(pane.window, "focus_displayof", return_value=pane.canvas),
                    patch.object(pane, "_schedule"),
                    patch("gamepad_aim.time.perf_counter", return_value=start + 1.01),
                ):
                    pane._simulate_to_input(start + 1.01)
                self.assertEqual(pane.test_open, button == "START")
                self.assertEqual(pane.countdown_until is not None, button == "START")
                self.assertFalse(pane.history.records)

    def test_buffered_start_after_timeout_saves_completed_round_and_restarts(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        for mode in ("shooting", "tracking", "timed-shooting"):
            with self.subTest(mode=mode):
                frame = tk.Frame(self.root)
                self.addCleanup(frame.destroy)
                pane = AimTestPane(frame, str, lambda: None, read_input=lambda: None)
                pane.dimension.set("2d")
                pane.mode.set(mode)
                pane._save_settings(AimSettings(
                    fullscreen=False, window_size="800x450", target_seconds=1,
                    tracking_seconds=1, timed_shooting_seconds=1,
                ))
                pane._start()
                started = pane.countdown_until
                pane._advance_countdown(started)
                basis = pane.round_basis
                pane._simulate_to(started + 0.98)
                pane.start_pressed = False
                pressed = SimpleNamespace(buttons=("START",), right=(0.0, 0.0), right_trigger=0.0)
                pane._input_events.extend((
                    (started + 1.01, pressed),
                    (started + 1.02, pressed),
                ))
                with (
                    patch.object(pane.owner, "focus_displayof", return_value=pane.canvas),
                    patch.object(pane, "_schedule"),
                    patch("gamepad_aim.time.perf_counter", return_value=started + 1.03),
                ):
                    pane._simulate_tick(started + 1.03)

                record, = pane.history.records
                self.assertEqual(record["basis"], basis)
                self.assertEqual(record["metrics"]["seconds"], 1)
                self.assertIsNotNone(pane.countdown_until)
                self.assertFalse(pane.finished)
                pane.close_test()
                self.assertEqual(len(pane.history.records), 1)

    def test_held_start_during_final_hit_does_not_skip_results(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None, read_input=lambda: None)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450", target_count=1))
        pane._start()
        started = pane.countdown_until
        pane._advance_countdown(started)
        pane.cursor.x, pane.cursor.y, _ = pane.cursor.target(*pane.size, pane._cursor_top())
        pane._shoot(started + 0.05)
        self.assertTrue(pane._awaiting_finish())
        pane.start_pressed = False
        pressed = SimpleNamespace(buttons=("START",), right=(0.0, 0.0), right_trigger=0.0)
        pane._input_events.append((started + 0.06, pressed))
        with patch.object(pane.owner, "focus_displayof", return_value=pane.canvas):
            pane._simulate_tick(started + 0.06)
            self.assertFalse(pane.finished)
            self.assertIsNone(pane.countdown_until)
            pane._draw_hit_effect(started + 1)
            pane._input_events.append((started + 1.01, pressed))
            pane._simulate_tick(started + 1.01)

        self.assertTrue(pane.finished)
        self.assertIsNone(pane.countdown_until)
        self.assertEqual(len(pane.history.records), 1)
        released = SimpleNamespace(buttons=(), right=(0.0, 0.0), right_trigger=0.0)
        pane._input_events.extend(((started + 1.02, released), (started + 1.03, pressed)))
        with (
            patch.object(pane.owner, "focus_displayof", return_value=pane.canvas),
            patch("gamepad_aim.time.perf_counter", return_value=started + 1.03),
        ):
            pane._simulate_tick(started + 1.03)
        self.assertIsNotNone(pane.countdown_until)
        self.assertEqual(len(pane.history.records), 1)
        pane.close_test()

    def test_destroying_the_test_window_saves_the_last_hit_once(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450", target_count=1))
        pane._start()
        start = pane.countdown_until
        pane._advance_countdown(start)
        pane.cursor.x, pane.cursor.y, _ = pane.cursor.target(*pane.size, pane._cursor_top())
        pane._shoot(start + 0.05)
        self.assertTrue(pane.cursor.completed)
        self.assertFalse(pane.history.records)

        pane.window.destroy()

        record, = pane.history.records
        self.assertEqual(record["metrics"]["hits"], 1)

    def test_destroying_the_aim_tab_during_the_last_hit_does_not_touch_destroyed_controls(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        frame = tk.Frame(self.root)
        pane = AimTestPane(frame, str, lambda: None)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450", target_count=1))
        pane._start()
        start = pane.countdown_until
        pane._advance_countdown(start)
        pane.cursor.x, pane.cursor.y, _ = pane.cursor.target(*pane.size, pane._cursor_top())
        pane._shoot(start + 0.05)
        self.assertTrue(pane.cursor.completed)
        self.assertFalse(pane.history.records)

        with patch.object(self.root, "report_callback_exception") as error:
            frame.destroy()
        error.assert_not_called()
        self.assertFalse(pane.window.winfo_exists())
        record, = pane.history.records
        self.assertEqual(record["metrics"]["hits"], 1)

    def test_2d_point_drills_play_miss_feedback_only_for_a_counted_shot(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        feedback = Mock()
        pane = AimTestPane(self.root, str, lambda: None, feedback=feedback)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450"))
        for mode in ("shooting", "timed-shooting"):
            with self.subTest(mode=mode):
                feedback.reset_mock()
                pane.mode.set(mode)
                pane._start()
                pane._mouse_click(SimpleNamespace(x=0, y=0))
                feedback.miss.assert_not_called()
                pane._advance_countdown(pane.countdown_until)
                pane._mouse_click(SimpleNamespace(x=0, y=0))
                self.assertEqual((pane.cursor.hits, pane.cursor.shots), (0, 1))
                feedback.miss.assert_called_once_with(True)
                feedback.hit.assert_not_called()

    def test_hiding_the_aim_page_cancels_a_pending_round(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None)
        pane.dimension.set("3d")
        pane.mode.set("tracking")
        pane._reset()
        pane.output_ready = True
        pane._start()
        deadline = pane.countdown_until
        pane.set_visible(False)
        pane._advance_countdown(deadline)
        self.assertFalse(pane.tracking.running)
        self.assertIsNone(pane.countdown_until)

    def test_hiding_the_aim_page_during_open_does_not_restart_the_countdown(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        frame = tk.Frame(self.root)
        frame.pack()
        pane = AimTestPane(frame, str, lambda: None)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450"))
        self.root.deiconify()
        self.root.update()
        self.root.after_idle(frame.pack_forget)

        pane._start()

        self.assertFalse(pane.test_open)
        self.assertIsNone(pane.countdown_until)
        self.assertIsNone(pane.job)

    def test_controller_start_does_not_reschedule_an_unmapped_aim_page(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        state = SimpleNamespace(buttons=(), right=(0, 0), right_trigger=0)
        frame = tk.Frame(self.root)
        frame.pack()
        pane = AimTestPane(frame, str, lambda: ("controller", state))
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450"))
        self.root.deiconify()
        self.root.update()
        pane.start_button.focus_force()
        self.root.update()
        pane.device_key = "controller"
        pane.start_pressed = False
        pane.next_frame = 0.0
        state.buttons = ("START",)
        self.root.after_idle(frame.pack_forget)

        pane._tick()

        self.assertFalse(pane.test_open)
        self.assertIsNone(pane.countdown_until)
        self.assertIsNone(pane.job)

    def test_changing_modes_during_final_hit_effect_keeps_completed_result(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450", target_count=1))
        pane._start()
        pane._advance_countdown(pane.countdown_until)
        pane.cursor.x, pane.cursor.y, _ = pane.cursor.target(*pane.size, pane._cursor_top())
        pane._shoot(pane.round_started_at + 0.05)
        self.assertTrue(pane.cursor.completed)
        self.assertFalse(pane.history.records)

        pane.drill_controls.winfo_children()[0].invoke()

        record, = pane.history.records
        self.assertEqual(record["basis"]["mode"], "cursor-shooting")
        self.assertEqual(record["metrics"]["hits"], 1)
        self.assertEqual(pane._mode_key(), "cursor-tracking")

    def test_aim_surface_losing_focus_interrupts_a_round_immediately(self):
        from gamepad_aim import AimTestPane

        frame = tk.Frame(self.root)
        frame.pack()
        pane = AimTestPane(frame, str, lambda: None)
        pane.mode.set("tracking")
        pane._reset()
        self.root.deiconify()
        self.root.update()
        pane._open_test_window()
        pane.tracking.start()
        self.root.focus_force()
        self.root.update()
        self.assertFalse(pane.tracking.running)
        self.assertTrue(pane.interrupted)
        pane._update_labels()
        self.assertIn("Enter", pane.status.get())

    def test_stale_focus_out_keeps_the_new_countdown(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        self.root.deiconify()
        self.root.update()
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450"))
        pane._start()
        self.root.update()
        self.assertIs(pane.canvas.focus_displayof(), pane.canvas)
        countdown = pane.countdown_until

        pane.canvas.event_generate("<FocusOut>")

        self.assertEqual(pane.countdown_until, countdown)
        self.assertFalse(pane.interrupted)

    def test_space_can_shoot_when_the_controller_has_no_rt_axis(self):
        from gamepad_aim import AimTestPane
        from gamepad_devices import GamepadState

        frame = tk.Frame(self.root)
        frame.pack()
        state = GamepadState(1, 0, (), (0.0, 0.0), (0.0, 0.0), None, None)
        pane = AimTestPane(frame, str, lambda: (("winmm", 0), state))
        pane.dimension.set("2d")
        pane._reset()
        self.root.deiconify()
        self.root.update()
        pane.device_key = ("winmm", 0)
        pane.output_ready = True
        pane._start()
        pane._advance_countdown(pane.countdown_until)
        pane.canvas.focus_force()
        self.root.update()
        x, y, _radius = pane.cursor.target(*pane.size, pane._cursor_top())
        pane.cursor.x, pane.cursor.y = x, y
        pane.canvas.event_generate("<KeyPress-space>")
        pane.canvas.event_generate("<KeyRelease-space>")
        self.assertEqual((pane.cursor.hits, pane.cursor.shots), (1, 1))

    def test_3d_mouse_can_turn_and_hit_without_a_controller(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450", mouse_sensitivity=50))
        self.assertFalse(pane.start_button.instate(["disabled"]))
        pane._start()
        started = pane.countdown_until
        view = pane._view
        left, top = view.winfo_rootx(), view.winfo_rooty()
        x, y = pane.size[0] // 2, pane.size[1] // 2
        center = left + x, top + y
        event = SimpleNamespace(x=x, y=y)
        with (patch.object(view, "winfo_pointerxy", return_value=center) as pointer,
              patch.object(view, "focus_displayof", return_value=view),
              patch.object(view, "winfo_containing", return_value=view),
              patch("gamepad_aim._warp_mouse", return_value=True) as warp):
            def center_pointer(x, y):
                pointer.return_value = x, y
                return True

            warp.side_effect = center_pointer
            pane._mouse_move(event, started - 1)
            warp.assert_not_called()
            pane._advance_countdown(started)
            pointer.return_value = center[0] + 70, center[1] - 20
            pane._mouse_move(event, started + 0.01)
            yaw = pane.scene.yaw
            self.assertAlmostEqual(math.degrees(yaw), 6.3)
            self.assertAlmostEqual(math.degrees(pane.scene.pitch), 1.8)
            warp.assert_called_once_with(*center)
            pointer.return_value = center
            pane._mouse_move(event, started + 0.02)
            self.assertEqual(pane.scene.yaw, yaw)
            pane._toggle_mouse_look(event)
            self.assertEqual(view.cget("cursor"), "")
            pointer.return_value = center[0] + 50, center[1]
            self.assertFalse(pane._mouse_move(event, started + 0.021))
            pane._mouse_click(event)
            self.assertEqual(pane.shooting.shots, 0)
            pane._toggle_mouse_look(event)
            self.assertEqual(view.cget("cursor"), "none")
            pane.scene.yaw = math.atan2(2, 8)
            pane.scene.pitch = 0.0
            pointer.return_value = center[0] + 70, center[1]
            with patch("gamepad_aim.time.perf_counter", return_value=started + 0.03):
                pane._mouse_click(event)
            self.assertAlmostEqual(pane.scene.yaw, math.atan2(2, 8))
        self.assertEqual((pane.shooting.hits, pane.shooting.shots), (1, 1))

    def test_3d_mouse_warp_readback_prevents_drift_when_windows_offsets_center(self):
        from gamepad_aim import AimTestPane, MOUSE_LOOK_RADIANS_PER_PIXEL
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450"))
        pane._start()
        view = pane._view
        started = pane.countdown_until
        event = SimpleNamespace(x=pane.size[0] // 2, y=pane.size[1] // 2)
        center = view.winfo_rootx() + event.x, view.winfo_rooty() + event.y
        with (
            patch.object(view, "winfo_pointerxy", return_value=center) as pointer,
            patch.object(view, "focus_displayof", return_value=view),
            patch.object(view, "winfo_containing", return_value=view),
            patch("gamepad_aim._warp_mouse") as warp,
        ):
            def warp_one_pixel_short(x, y):
                pointer.return_value = x - 1, y + 1
                return True

            warp.side_effect = warp_one_pixel_short
            pane._advance_countdown(started)
            pointer.return_value = center[0] + 20, center[1] - 10
            pane._mouse_move(event, started + 0.01)
            pose = pane.scene.yaw, pane.scene.pitch
            self.assertAlmostEqual(pose[0], 20 * MOUSE_LOOK_RADIANS_PER_PIXEL)
            self.assertAlmostEqual(pose[1], 10 * MOUSE_LOOK_RADIANS_PER_PIXEL)
            for tick in range(2, 12):
                now = started + 0.01 * tick
                pane._mouse_move(event, now)
                pane._simulate_tick(now)
            self.assertEqual((pane.scene.yaw, pane.scene.pitch), pose)
            warp.assert_called_once_with(*center)

            pointer.return_value = center[0], center[1] + 1
            pane._mouse_move(event, started + 0.12)
            self.assertAlmostEqual(pane.scene.yaw, pose[0] + MOUSE_LOOK_RADIANS_PER_PIXEL)
            self.assertEqual(pane.scene.pitch, pose[1])

    def test_3d_mouse_reentry_reanchors_without_turning(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450"))
        self.root.deiconify()
        self.root.update()
        pane._start()
        self.root.update()
        view = pane._view
        started = pane.countdown_until
        left, top = view.winfo_rootx(), view.winfo_rooty()
        center = left + pane.size[0] // 2, top + pane.size[1] // 2
        with (
            patch.object(view, "winfo_pointerxy", return_value=center) as pointer,
            patch.object(view, "focus_displayof", return_value=view),
            patch.object(view, "winfo_containing", return_value=view),
            patch("gamepad_aim._warp_mouse", return_value=True) as warp,
        ):
            pane._advance_countdown(started)
            view.event_generate("<Leave>")
            self.assertIsNone(pane._mouse_position)
            pointer.return_value = left + 10, center[1]
            pane._mouse_move(SimpleNamespace(x=10, y=pane.size[1] // 2), started + 0.01)

            self.assertEqual(pane.scene.yaw, 0.0)
            warp.assert_called_once_with(*center)
            pointer.return_value = center[0] + 10, center[1]
            pane._mouse_move(
                SimpleNamespace(x=pane.size[0] // 2 + 10, y=pane.size[1] // 2),
                started + 0.02,
            )
            self.assertGreater(pane.scene.yaw, 0.0)

    def test_3d_mouse_priority_and_stick_replacement_keep_round_running(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        state = SimpleNamespace(right=(0, 0), buttons=(), right_trigger=0)
        output = ("original", state)
        pane = AimTestPane(self.root, str, lambda: output)
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450", auto_input_switch=True))
        pane.device_key = "original"
        pane.output_ready = True
        pane._start()
        started = pane.countdown_until
        pane._advance_countdown(started)
        pane._schedule = lambda _now: None
        pane.last_frame = pane.next_frame = started
        pane._mouse_priority_until = started + 0.05
        pane.scene.yaw = math.atan2(2, 8)
        yaw = pane.scene.yaw
        state.right, state.right_trigger = (0.5, 0), 1
        output = ("replacement", state)
        with patch("gamepad_aim.time.perf_counter", return_value=started + 0.02):
            pane._tick()
        self.assertTrue(pane.shooting.running)
        self.assertEqual(pane.scene.yaw, yaw)
        self.assertEqual(pane.shooting.shots, 0)
        with patch("gamepad_aim.time.perf_counter", return_value=started + 0.08):
            pane._tick()
        self.assertGreater(pane.scene.yaw, yaw)
        self.assertEqual(pane.shooting.shots, 0)
        output = None
        pane.scene.yaw = yaw
        with patch("gamepad_aim.time.perf_counter", return_value=started + 0.1):
            pane._tick()
            pane._space_down(None)
            pane._space_shot(None)
        self.assertEqual((pane.shooting.hits, pane.shooting.shots), (1, 1))
