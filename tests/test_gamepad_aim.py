import math
import sys
import threading
import time
import tkinter as tk
import unittest
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import Mock, patch


class AimModelTests(unittest.TestCase):
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
        self.assertEqual(window._read_aim_output(), ((first.key, first.index), state))
        window._selected_device = lambda: second
        self.assertIsNone(window._read_aim_output())
        self.assertEqual(len(sampler.read_snapshot()[1]), 1)

    def test_s2p_aim_reads_the_published_xinput_slot(self):
        from gamepad_devices import GamepadDevice, GamepadState
        from gamepad_test_window import GamepadTestWindow

        state = GamepadState(4, 0, (), (0.0, 0.0), (0.125, -0.25), 0.0, 0.5)
        window = object.__new__(GamepadTestWindow)
        window._selected_device = lambda: GamepadDevice("s2p", "s2p", 2, "S2P", True)
        window.latest_telemetry = {"xinput_slot": 2, "right": {"final": (1.0, 1.0)}}
        window._telemetry_is_fresh = lambda _telemetry: True
        window.backend = SimpleNamespace(read_state=lambda device: (
            state if (device.kind, device.index) == ("xinput", 2) else None
        ))
        self.assertEqual(window._read_aim_output(), (("s2p", 2), state))
        window.latest_telemetry["xinput_slot"] = 1
        self.assertIsNone(window._read_aim_output())


class AimPaneTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(self.root.destroy)
        self.addCleanup(self.root.update_idletasks)
        self.root.withdraw()

    def test_gpu_3d_draws_current_grid_and_target_in_the_same_frame(self):
        from gamepad_aim import AimTestPane

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        draws = []
        closed = []

        def renderer(_hwnd, _width, _height):
            return SimpleNamespace(
                draw=lambda _w, _h, **parts: draws.append(dict(parts, lines=tuple(parts["lines"]))),
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
        self.assertTrue(draws[-1]["lines"])
        first = draws[-1]
        self.assertEqual(first["reticle"][2], pane.settings.reticle_size / 2)
        pane.scene.yaw = 0.08
        pane.next_draw = 0.0
        # Even if a software grid repaint would still be on cooldown, the GPU
        # frame uses the same current camera for the lines and the target.
        pane.next_room_draw = float("inf")
        pane._draw_if_due(1.001)
        self.assertNotEqual(draws[-1]["lines"], first["lines"])
        self.assertNotEqual(draws[-1]["balls"], first["balls"])

        pane.dimension.set("2d")
        pane._reset()
        self.root.update()
        self.assertEqual(closed, [])
        pane.next_draw = 0.0
        pane._draw_if_due(1.002)
        self.assertEqual(draws[-1]["lines"], ())
        pane.close_test()
        self.assertEqual(closed, [True])
        self.assertIs(pane._view, pane.canvas)

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
        pane._advance_countdown(pane.countdown_until)
        pane.next_draw = 0.0
        pane._draw_if_due(1.0)
        self.root.update()
        self.assertEqual(closed, [True])
        self.assertEqual(pane.gpu_error, "device lost")
        self.assertTrue(pane.scene.running)
        self.assertTrue(pane.canvas.winfo_ismapped())
        self.assertFalse(pane._gpu_view.winfo_ismapped())

    def test_threaded_gpu_failure_restores_canvas_without_counting_failed_draw(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        def draw(_width, _height, **_parts):
            raise OSError("worker device lost")

        renderer = SimpleNamespace(draw=draw, close=Mock())
        pane = AimTestPane(
            self.root, str, lambda: None, gpu_renderer=lambda *_: renderer, render_in_worker=True,
        )
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(fullscreen=False, window_size="800x450"))
        pane._open_test_window()
        worker = pane._render_worker
        pane._draw_if_due(time.perf_counter())
        self.assertTrue(worker.stopped.wait(2))
        pane._collect_render_results()

        self.assertEqual(pane.gpu_error, "worker device lost")
        self.assertIsNone(pane.gpu)
        self.assertIs(pane._view, pane.canvas)
        self.assertIsNone(pane._gpu_view)
        self.assertEqual(pane.draws, 0)
        renderer.close.assert_called_once()
        pane._draw_if_due(time.perf_counter())
        self.assertEqual(pane.draws, 1)
        pane.close_test()

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

        output = None
        pane = AimTestPane(self.root, str, lambda: output)
        pane.dimension.set("2d")
        pane._reset()
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

    def test_2d_tracking_runs_without_a_controller_and_records_pixel_error(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane.dimension.set("2d")
        pane.mode.set("tracking")
        pane._save_settings(AimSettings(tracking_seconds=1))
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
        pane._save_settings(AimSettings(window_size="960x540"))
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

    def test_result_footer_reserves_comparison_space_and_popup_button_restarts(self):
        from gamepad_aim import AimTestPane

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        pane = AimTestPane(frame, str, lambda: None)
        self.root.deiconify()
        self.root.update()
        pane.dimension.set("2d")
        pane._reset()
        pane._open_test_window()
        change_y = pane.hud.change_origin[1]
        pane.finished = True
        pane._show_changes((("命中 ↑ +1", True),))
        pane._update_labels()
        self.assertEqual(pane.hud.change_origin[1], change_y)
        self.assertEqual(pane.restart_button.cget("text"), "重新開始")
        pane.restart_button.invoke()
        self.root.update()
        self.assertTrue(pane.test_open)
        self.assertIsNotNone(pane.countdown_until)

    def test_frame_rates_stay_at_the_bottom_right_below_the_score(self):
        from gamepad_aim import AimTestPane

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        pane = AimTestPane(frame, str, lambda: None)
        self.root.deiconify()
        self.root.update()
        pane._open_test_window()
        pane.fps_text.set("繪圖 180 FPS　更新 500 Hz")
        pane._show_changes((("命中 ↑ +1", True),))
        status, score, fps = (pane.canvas.bbox(item) for item in pane.hud.labels)
        self.assertEqual(fps[2], score[2])
        self.assertGreater(fps[1], score[3])
        self.assertLess(pane.size[1] - fps[3], 30)
        self.assertEqual(status[3], fps[3])
        self.assertLess(status[0], 25)

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
        self.assertIn("14.4 百分點", text)
        self.assertIn("平均偏差是準心與靶心的平均距離", text)
        help_mark.event_generate("<Leave>")
        self.assertFalse(tip.winfo_exists())

    def test_fullscreen_score_and_comparison_use_adjacent_lines(self):
        from gamepad_aim import AimTestPane

        pane = AimTestPane(self.root, str, lambda: None)
        pane._open_test_window()
        pane._resize(SimpleNamespace(width=1280, height=720))
        pane._show_changes((("命中 ↑ +1", True),))
        score = pane.hud.labels[1]
        comparison = pane.hud.change_items[0]
        gap = pane.canvas.bbox(comparison)[1] - pane.canvas.bbox(score)[3]
        self.assertGreaterEqual(gap, 0)
        self.assertLess(gap, pane.hud.line // 2)

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
        mode = SimpleNamespace(close=Mock(side_effect=(OSError("temporary display error"), None)))
        pane._fullscreen_mode = mode
        with (
            self.assertLogs("gamepad_aim", level="ERROR"),
            patch.object(self.root, "after") as after,
        ):
            pane.close_test()
        self.assertIs(pane._fullscreen_mode, mode)
        after.assert_called_once()
        _delay, retry, failed_mode = after.call_args.args
        retry(failed_mode)
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

    def test_fullscreen_auto_selects_highest_driver_resolution(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane._save_settings(AimSettings(fullscreen=True))
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
        pane._save_settings(AimSettings(window_size="960x540"))
        pane._start()
        self.root.update()
        self.assertEqual(pane.window.state(), "normal")
        self.assertEqual(pane.size, (960, 540))

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
        pane._save_settings(AimSettings(timed_shooting_seconds=12, seed=0))
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
                pane._save_settings(AimSettings(timed_shooting_seconds=5))
                pane.output_ready = True
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
        pane._save_settings(AimSettings(target_color="#fa5544", target_on_target_color="#4d8dff"))
        pane._draw_balls(((100, 100, 12),), on_target=True)
        self.assertEqual(pane.canvas.itemcget(pane._ball_items[0], "fill"), "#4d8dff")
        self.assertEqual(float(pane.canvas.itemcget(pane._ball_items[0], "width")), 1.0)
        outline = pane.canvas.itemcget(pane._ball_items[0], "outline")
        self.assertTrue(outline)
        pane._draw_balls(((100, 100, 12),), on_target=False)
        self.assertEqual(pane.canvas.itemcget(pane._ball_items[0], "fill"), "#fa5544")
        self.assertEqual(pane.canvas.itemcget(pane._ball_items[0], "outline"), outline)
        pane._save_settings(AimSettings(target_color="#172231"))
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
        pane._save_settings(AimSettings(reticle_on_target_color="#ff467a"))
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
        self.assertEqual(pane.hit_effect[1], (2, 0, 8))
        pane._draw_hit_effect(start_at + 0.15)
        self.assertEqual(pane.canvas.itemcget(pane.hit_particles[0], "state"), "normal")
        fragment_x = pane.canvas.coords(pane.hit_particles[0])[0]
        pane.shooting.yaw += 0.1
        pane._draw_hit_effect(start_at + 0.15)
        self.assertLess(pane.canvas.coords(pane.hit_particles[0])[0], fragment_x)
        pane._draw_hit_effect(start_at + 0.4)
        self.assertEqual(pane.canvas.itemcget(pane.hit_particles[0], "state"), "hidden")

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
                pane._save_settings(AimSettings(target_count=1))
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
        pane._save_settings(AimSettings(reticle_size=26, target_size_2d=14))
        x1, _, x2, _ = pane.canvas.coords(pane.reticle[0])
        self.assertEqual(x2 - x1, 26)
        pane.cursor.start()
        target = pane.cursor.target(640, 360)
        self.assertAlmostEqual(target[2] * 2, 50.4)
        pane.cursor.click(target[0] + 20, target[1], 640, 360)
        self.assertEqual(pane.cursor.hits, 1)

    def test_3d_mode_shoots_once_per_rt_press(self):
        from gamepad_aim import AimTestPane
        from gamepad_devices import GamepadState

        state = GamepadState(1, 0, (), (0.0, 0.0), (0.0, 0.0), 0.0, 0.0)
        pane = AimTestPane(self.root, str, lambda: ("device", state))
        pane._schedule = lambda _now: None
        pane.dimension.set("3d")
        pane.mode.set("shooting")
        pane._reset()
        pane.device_key = "device"
        pane.output_ready = True
        pane._start()
        pane._advance_countdown(pane.countdown_until)
        pane.shooting.yaw = math.atan2(2, 8)
        pane._tick()
        state = GamepadState(2, 0, (), (0.0, 0.0), (0.0, 0.0), 0.0, 1.0)
        for _ in range(2):
            pane.next_frame = 0.0
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
        pane._save_settings(AimSettings(target_count=1))

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
            outlines = pane.canvas.find_withtag("finish_outline")
            self.assertEqual(len(outlines), 16)
            self.assertTrue(all(pane.canvas.itemcget(item, "fill") == pane.canvas["background"]
                                for item in outlines))
            self.assertEqual(pane.status.get(), "")

        finish(True)
        self.assertEqual(len(history.records), 1)
        finish(False)
        self.assertTrue(any(improved and "↑" in text for text, improved in pane.change_parts))
        self.assertTrue(any(improved and "↓" in text for text, improved in pane.change_parts))
        pane._save_settings(AimSettings(target_count=1, seed=17))
        finish(False)
        self.assertEqual(pane.change_parts, (("同基準尚無紀錄", None),))
        self.assertEqual(len(history.records), 3)

    def test_mouse_shot_after_timeout_cannot_be_scored_between_ticks(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        pane = AimTestPane(self.root, str, lambda: None)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(target_count=1, target_seconds=1))
        pane._start()
        begun = pane.countdown_until
        pane._advance_countdown(begun)
        x, y, _ = pane.cursor.target(*pane.size, pane._cursor_top())
        with patch("gamepad_aim.time.perf_counter", return_value=begun + 1.01):
            pane._mouse_click(SimpleNamespace(x=x, y=y))
        self.assertTrue(pane.finished)
        self.assertEqual((pane.cursor.hits, pane.cursor.shots, pane.cursor.elapsed), (0, 0, 1))

    def test_2d_point_drills_play_miss_feedback_only_for_a_counted_shot(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        feedback = Mock()
        pane = AimTestPane(self.root, str, lambda: None, feedback=feedback)
        pane.dimension.set("2d")
        pane._save_settings(AimSettings(window_size="800x450"))
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
        pane._save_settings(AimSettings(window_size="800x450"))
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
        pane._save_settings(AimSettings(window_size="800x450"))
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
        pane._save_settings(AimSettings(window_size="800x450", target_count=1))
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
        pane._save_settings(AimSettings(window_size="800x450"))
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

    def test_3d_space_does_not_score_after_output_changes_between_polls(self):
        from gamepad_aim import AimTestPane
        from gamepad_aim_settings import AimSettings

        state = SimpleNamespace(right=(0, 0), buttons=(), right_trigger=0)
        output = ("original", state)
        pane = AimTestPane(self.root, str, lambda: output)
        pane._save_settings(AimSettings(window_size="800x450", target_count=1))
        pane.device_key = "original"
        pane.output_ready = True
        pane._start()
        pane._advance_countdown(pane.countdown_until)
        pane.scene.yaw = math.atan2(2, 8)
        pane._space_down(None)
        output = ("replacement", state)

        pane._space_shot(None)

        self.assertTrue(pane.interrupted)
        self.assertEqual(pane.shooting.shots, 0)
        self.assertFalse(pane.history.records)
