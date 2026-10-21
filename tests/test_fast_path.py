import random
import configparser
import math
import struct
import sys
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from input_dispatcher import InputDispatcher
from audio_haptics import AudioHaptics
from config_utils import load_stick_calibration
from switch2_input import parse_input_report
from xinput_controller import XInputController
from vgamepad.win import vigem_commons as vcom
from vgamepad.win.virtual_gamepad import VX360Gamepad


class _FakePad:
    def __init__(self):
        self.notification_callbacks = []
        self.reset_count = 0
        self.update_count = 0

    def register_notification(self, callback_function):
        self.notification_callbacks.append(callback_function)

    def reset(self):
        self.reset_count += 1

    def update(self):
        self.update_count += 1


def reference_parse(payload):
    if len(payload) < 16:
        return None

    def stick(offset):
        data = payload[offset:offset + 3]
        return (
            data[0] | ((data[1] & 0x0F) << 8),
            ((data[1] >> 4) & 0x0F) | (data[2] << 4),
        )

    def signed(offset):
        return int.from_bytes(payload[offset:offset + 2], "little", signed=True)

    magnetometer = (0, 0, 0)
    accelerometer = (0, 0, 0)
    gyroscope = (0, 0, 0)
    if len(payload) >= 31:
        magnetometer = tuple(signed(offset) for offset in (25, 27, 29))
    if len(payload) >= 60:
        accelerometer = tuple(signed(offset) for offset in (48, 50, 52))
        gyroscope = tuple(signed(offset) for offset in (54, 56, 58))
    voltage = None
    percent = None
    if len(payload) >= 33:
        millivolts = int.from_bytes(payload[31:33], "little")
        if 2500 <= millivolts <= 5000:
            voltage = millivolts / 1000.0
            curve = (
                (2589, 0), (3000, 3), (3100, 5), (3150, 10),
                (3200, 22), (3250, 34), (3300, 45), (3350, 51),
                (3400, 55), (3450, 66), (3500, 72), (3550, 80),
                (3600, 87), (3650, 95), (3687, 100),
            )
            if millivolts <= curve[0][0]:
                percent = 0
            elif millivolts >= curve[-1][0]:
                percent = 100
            else:
                for (low_mv, low_pct), (high_mv, high_pct) in zip(
                    curve, curve[1:]
                ):
                    if millivolts <= high_mv:
                        fraction = (millivolts - low_mv) / (high_mv - low_mv)
                        estimate = low_pct + fraction * (high_pct - low_pct)
                        percent = min(100, max(0, int(5 * round(estimate / 5))))
                        break
    return (
        int.from_bytes(payload[4:8], "little"),
        stick(10),
        stick(13),
        accelerometer,
        gyroscope,
        magnetometer,
        percent,
        voltage,
        bool(payload[33]) if len(payload) >= 34 else False,
        int.from_bytes(payload[0:4], "little"),
    )


class InputParserTests(unittest.TestCase):
    def test_random_reports_match_reference(self):
        rng = random.Random(0x523250)
        for length in (16, 30, 31, 32, 33, 59, 60, 64):
            for _ in range(500):
                payload = bytes(rng.getrandbits(8) for _ in range(length))
                actual = parse_input_report(payload)
                self.assertEqual(
                    (
                        actual.buttons,
                        actual.left_stick,
                        actual.right_stick,
                        actual.accelerometer,
                        actual.gyroscope,
                        actual.magnetometer,
                        actual.battery_percent,
                        actual.battery_voltage,
                        actual.charging,
                        actual.report_time,
                    ),
                    reference_parse(payload),
                )


class DispatcherTests(unittest.TestCase):
    @staticmethod
    def report(buttons, marker=0):
        payload = bytearray(64)
        struct.pack_into("<I", payload, 4, buttons)
        payload[20] = marker
        return bytes(payload)

    def test_inline_path_preserves_identity_and_edges(self):
        seen = []
        dispatcher = InputDispatcher(seen.append, inline_fast_path=True)
        try:
            reports = [
                self.report(0, 1),
                self.report(1, 2),
                self.report(1, 3),
                self.report(0, 4),
            ]
            for report in reports:
                dispatcher.submit(report)
            self.assertEqual(seen, reports)
            self.assertTrue(all(a is b for a, b in zip(seen, reports)))
            self.assertEqual(dispatcher.inline_reports, len(reports))
        finally:
            dispatcher.stop()

    def test_busy_path_keeps_press_and_release(self):
        entered = threading.Event()
        release = threading.Event()
        seen = []

        def callback(payload):
            seen.append(int.from_bytes(payload[4:8], "little"))
            if len(seen) == 1:
                entered.set()
                release.wait(1.0)

        dispatcher = InputDispatcher(callback, inline_fast_path=True)
        try:
            first = threading.Thread(target=dispatcher.submit, args=(self.report(0),))
            first.start()
            self.assertTrue(entered.wait(1.0))
            dispatcher.submit_batch([
                self.report(1, 1),
                self.report(1, 2),
                self.report(0, 3),
            ])
            release.set()
            first.join(1.0)
            deadline = time.monotonic() + 1.0
            while len(seen) < 3 and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(seen, [0, 1, 0])
        finally:
            release.set()
            dispatcher.stop()

    def test_exclusive_reconfigure_waits_and_discards_old_queue(self):
        entered = threading.Event()
        release = threading.Event()
        reconfigured = threading.Event()
        seen = []

        def callback(payload):
            seen.append(payload[20])
            if payload[20] == 1:
                entered.set()
                release.wait(1.0)

        dispatcher = InputDispatcher(callback, inline_fast_path=True)
        try:
            first = threading.Thread(
                target=dispatcher.submit, args=(self.report(0, 1),)
            )
            first.start()
            self.assertTrue(entered.wait(1.0))
            dispatcher.submit(self.report(0, 2))
            reload_thread = threading.Thread(
                target=lambda: dispatcher.run_exclusive(reconfigured.set)
            )
            reload_thread.start()
            self.assertFalse(reconfigured.wait(0.02))
            release.set()
            first.join(1.0)
            reload_thread.join(1.0)
            self.assertTrue(reconfigured.is_set())
            dispatcher.submit(self.report(0, 3))
            deadline = time.monotonic() + 1.0
            while seen != [1, 3] and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(seen, [1, 3])
        finally:
            release.set()
            dispatcher.stop()

    def test_stop_does_not_report_success_while_inline_callback_runs(self):
        entered = threading.Event()
        release = threading.Event()

        def callback(_payload):
            entered.set()
            release.wait(1.0)

        dispatcher = InputDispatcher(callback, inline_fast_path=True)
        submitter = threading.Thread(
            target=dispatcher.submit,
            args=(self.report(0, 1),),
        )
        submitter.start()
        self.assertTrue(entered.wait(1.0))

        started = time.perf_counter()
        self.assertFalse(dispatcher.stop(timeout=0.05))
        self.assertLess(time.perf_counter() - started, 0.2)

        release.set()
        submitter.join(1.0)
        self.assertTrue(dispatcher.stop(timeout=0.5))

    def test_reset_obeys_timeout_while_callback_runs(self):
        entered = threading.Event()
        release = threading.Event()

        def callback(_payload):
            entered.set()
            release.wait(1.0)

        dispatcher = InputDispatcher(callback, inline_fast_path=True)
        submitter = threading.Thread(
            target=dispatcher.submit,
            args=(self.report(0, 1),),
        )
        submitter.start()
        try:
            self.assertTrue(entered.wait(1.0))
            started = time.perf_counter()
            self.assertFalse(dispatcher.reset(timeout=0.05))
            self.assertLess(time.perf_counter() - started, 0.2)
        finally:
            release.set()
            submitter.join(1.0)
            dispatcher.stop()

    def test_run_exclusive_obeys_timeout_while_callback_runs(self):
        entered = threading.Event()
        release = threading.Event()
        reconfigured = threading.Event()

        def callback(_payload):
            entered.set()
            release.wait(1.0)

        dispatcher = InputDispatcher(callback, inline_fast_path=True)
        submitter = threading.Thread(
            target=dispatcher.submit,
            args=(self.report(0, 1),),
        )
        submitter.start()
        try:
            self.assertTrue(entered.wait(1.0))
            started = time.perf_counter()
            self.assertFalse(dispatcher.run_exclusive(
                reconfigured.set,
                timeout=0.05,
            ))
            self.assertLess(time.perf_counter() - started, 0.2)
            self.assertFalse(reconfigured.is_set())
        finally:
            release.set()
            submitter.join(1.0)
            dispatcher.stop()


class XusbReportTests(unittest.TestCase):
    def test_in_place_report_matches_vgamepad_helpers(self):
        class HelperPad:
            left_trigger = VX360Gamepad.left_trigger
            right_trigger = VX360Gamepad.right_trigger
            left_joystick = VX360Gamepad.left_joystick
            right_joystick = VX360Gamepad.right_joystick
            left_joystick_float = VX360Gamepad.left_joystick_float
            right_joystick_float = VX360Gamepad.right_joystick_float

        rng = random.Random(0x58425553)
        for _ in range(1000):
            buttons = rng.randrange(0x10000)
            left_trigger = rng.randrange(256)
            right_trigger = rng.randrange(256)
            axes = [rng.uniform(-1.0, 1.0) for _ in range(4)]

            helper = HelperPad()
            helper.report = vcom.XUSB_REPORT()
            helper.report.wButtons = buttons
            helper.left_trigger(left_trigger)
            helper.right_trigger(right_trigger)
            helper.left_joystick_float(axes[0], axes[1])
            helper.right_joystick_float(axes[2], axes[3])

            direct = vcom.XUSB_REPORT()
            direct.wButtons = buttons
            direct.bLeftTrigger = left_trigger
            direct.bRightTrigger = right_trigger
            direct.sThumbLX = round(axes[0] * 32767)
            direct.sThumbLY = round(axes[1] * 32767)
            direct.sThumbRX = round(axes[2] * 32767)
            direct.sThumbRY = round(axes[3] * 32767)

            self.assertEqual(bytes(helper.report), bytes(direct))


class LiveReconfigureTests(unittest.TestCase):
    def test_audio_updates_are_suppressed_in_game_and_deduplicated(self):
        game = configparser.ConfigParser()
        self.assertTrue(game.read(
            ROOT / "src" / "profiles" / "System Default.ini",
            encoding="utf-8",
        ))
        calibration = load_stick_calibration(game)
        controller = XInputController(game, calibration, pad=_FakePad())
        try:
            original_sequence = controller._rumble_sequence
            controller.set_audio_rumble(0.5, 0.5)
            self.assertEqual(controller._rumble_sequence, original_sequence)

            audio = configparser.ConfigParser()
            audio.read_dict({
                section: dict(game.items(section))
                for section in game.sections()
            })
            audio.set("audio_haptics", "mode", "AUDIO")
            controller.reconfigure(audio, calibration)
            controller.set_audio_rumble(0.5, 0.5)
            first_sequence = controller._rumble_sequence
            controller.set_audio_rumble(0.5, 0.5)
            self.assertEqual(controller._rumble_sequence, first_sequence)
        finally:
            controller.close()

    def test_reconfigure_keeps_one_pad_callback_and_worker(self):
        config = configparser.ConfigParser()
        self.assertTrue(config.read(
            ROOT / "src" / "profiles" / "System Default.ini",
            encoding="utf-8",
        ))
        calibration = load_stick_calibration(config)
        pad = _FakePad()
        controller = XInputController(config, calibration, pad=pad)
        original_thread = controller._rumble_thread
        try:
            changed = configparser.ConfigParser()
            changed.read_dict({
                section: dict(config.items(section))
                for section in config.sections()
            })
            changed.set("rumble", "lf_frequency", "205")
            changed.set("audio_haptics", "mode", "MIX")

            for _ in range(25):
                controller.reconfigure(changed, calibration)
                controller.reconfigure(config, calibration)

            self.assertIs(controller.pad, pad)
            self.assertIs(controller._rumble_thread, original_thread)
            self.assertEqual(len(pad.notification_callbacks), 1)
            self.assertEqual(controller.audio_haptics_mode, "GAME")
            self.assertTrue(original_thread.is_alive())
        finally:
            controller.close()

    def test_audio_reconfigure_preserves_running_native_stream(self):
        game = configparser.ConfigParser()
        mix = configparser.ConfigParser()
        self.assertTrue(game.read(
            ROOT / "src" / "profiles" / "System Default.ini",
            encoding="utf-8",
        ))
        self.assertTrue(mix.read(
            ROOT / "src" / "profiles" / "General.ini",
            encoding="utf-8",
        ))
        # The test exercises GAME -> MIX hot reconfiguration independently of
        # whichever audio mode the General release preset currently chooses.
        mix.set("audio_haptics", "mode", "MIX")
        levels = []
        audio = AudioHaptics(game, lambda lf, hf: levels.append((lf, hf)))
        native_stream = object()
        audio._stream = native_stream
        audio._running = True

        audio.reconfigure(mix)
        self.assertIs(audio._stream, native_stream)
        self.assertEqual(audio.mode, "MIX")
        audio.reconfigure(game)
        self.assertIs(audio._stream, native_stream)
        self.assertEqual(audio.mode, "GAME")
        self.assertEqual(levels[-1], (0.0, 0.0))


class GyroScalarPathTests(unittest.TestCase):
    @staticmethod
    def controller(gravity, player_space=True):
        class Ahrs:
            pass

        controller = XInputController.__new__(XInputController)
        controller._gyro_bias = [3.5, -1.25, 7.0]
        controller.gyro_player_space = player_space
        controller._gyro_was_active = True
        controller._nine_axis_quaternion = (1.0, 0.0, 0.0, 0.0)
        controller._aim_gravity_sign = 1.0
        controller._aim_pose_ready_since = None
        controller._aim_player_space_blend = 1.0
        controller._ahrs = Ahrs()
        controller._ahrs.gravity = gravity
        return controller

    def test_controller_space_matches_fixed_axes(self):
        controller = self.controller((0.0, 0.0, 1.0), player_space=False)
        gyro = (146.35714, 284.46428, -421.57142)
        actual = controller._gravity_aware_aim_axes(gyro, 0.004)
        scale = 1.0 / 14.285714
        expected = (
            -(gyro[2] - controller._gyro_bias[2]) * scale,
            (gyro[0] - controller._gyro_bias[0]) * scale,
        )
        self.assertAlmostEqual(actual[0], expected[0], places=12)
        self.assertAlmostEqual(actual[1], expected[1], places=12)

    def test_player_space_matches_vector_projection(self):
        rng = random.Random(0x4759524F)
        for _ in range(500):
            gravity = [rng.uniform(-1.0, 1.0) for _ in range(3)]
            magnitude = math.sqrt(sum(value * value for value in gravity))
            if magnitude < 0.2:
                continue
            gravity = tuple(value / magnitude for value in gravity)
            # Skip the same controller-X singularity guarded by production.
            if math.sqrt(max(0.0, 1.0 - gravity[0] * gravity[0])) <= 0.15:
                continue
            gyro = tuple(rng.uniform(-2000.0, 2000.0) for _ in range(3))
            controller = self.controller(gravity)
            actual = controller._gravity_aware_aim_axes(gyro, 0.004)

            rates = tuple(
                (gyro[index] - controller._gyro_bias[index]) / 14.285714
                for index in range(3)
            )
            gx, gy, gz = gravity
            vertical = (1.0 - gx * gx, -gx * gy, -gx * gz)
            vertical_norm = math.sqrt(sum(value * value for value in vertical))
            vertical = tuple(value / vertical_norm for value in vertical)
            transformed = (
                -sum(rates[index] * gravity[index] for index in range(3)),
                sum(rates[index] * vertical[index] for index in range(3)),
            )
            confidence = max(0.0, min(1.0, (vertical_norm - 0.15) / 0.25))
            legacy = (-rates[2], rates[0])
            expected = (
                legacy[0] + (transformed[0] - legacy[0]) * confidence,
                legacy[1] + (transformed[1] - legacy[1]) * confidence,
            )
            self.assertAlmostEqual(actual[0], expected[0], places=10)
            self.assertAlmostEqual(actual[1], expected[1], places=10)

    def test_scalar_accelerometer_matrix_matches_reference(self):
        controller = XInputController.__new__(XInputController)
        controller._accel_bias = (12.5, -7.25, 31.0)
        controller._accel_matrix = (
            (0.000245, 0.000003, -0.000002),
            (0.000003, 0.000251, 0.000004),
            (-0.000002, 0.000004, 0.000239),
        )
        rng = random.Random(0x41434345)
        for _ in range(500):
            raw = tuple(rng.uniform(-5000.0, 5000.0) for _ in range(3))
            actual = controller._correct_accelerometer(raw)
            delta = tuple(
                raw[index] - controller._accel_bias[index]
                for index in range(3)
            )
            expected = tuple(
                sum(
                    controller._accel_matrix[row][column] * delta[column]
                    for column in range(3)
                )
                for row in range(3)
            )
            expected_magnitude = math.sqrt(sum(value * value for value in expected))
            if 0.25 <= expected_magnitude <= 2.5:
                self.assertIsNotNone(actual)
                for value, reference in zip(actual, expected):
                    self.assertAlmostEqual(float(value), reference, places=12)
            else:
                self.assertIsNone(actual)

    def test_scalar_magnetometer_matrix_matches_reference(self):
        controller = XInputController.__new__(XInputController)
        controller._mag_bias = [11.0, -22.0, 5.5]
        controller._mag_scale = [1.0, 1.0, 1.0]
        controller._mag_matrix = (
            (0.0053, 0.0001, 0.0003),
            (0.0001, 0.0056, 0.0004),
            (0.0003, 0.0004, 0.0045),
        )
        raw = (283.0, -417.0, 191.0)
        actual = controller._correct_magnetometer(raw)
        delta = tuple(
            raw[index] - controller._mag_bias[index] for index in range(3)
        )
        expected = tuple(
            sum(
                controller._mag_matrix[row][column] * delta[column]
                for column in range(3)
            )
            for row in range(3)
        )
        for value, reference in zip(actual, expected):
            self.assertAlmostEqual(value, reference, places=12)

if __name__ == "__main__":
    unittest.main()
