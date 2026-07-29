import asyncio
import threading
import time
import unittest
from unittest.mock import patch

from bluetooth_controller import (
    BluetoothController,
    COMMAND_RESPONSE_UUID,
    COMMAND_WRITE_UUID,
)
from esp32_bridge import ESP32Bridge
from runtime_cleanup import (
    close_xinput_after_dispatcher,
    controller_application_ready,
)
from wired_controller import WiredController, _rumble_active


class _BleClient:
    def __init__(self):
        self.is_connected = True


class _AliveThread:
    def __init__(self):
        self.join_calls = []

    def is_alive(self):
        return True

    def join(self, timeout=None):
        self.join_calls.append(timeout)


class _RecordingHid:
    def __init__(self):
        self.reports = []
        self.write_event = threading.Event()

    def write(self, report):
        self.reports.append(bytes(report))
        self.write_event.set()
        return len(report)


class _RecordingSerial:
    def __init__(self):
        self.is_open = True
        self.writes = []
        self.write_event = threading.Event()

    def write(self, data):
        self.writes.append(bytes(data))
        self.write_event.set()
        return len(data)


class _ObservedLock:
    def __init__(self):
        self._lock = threading.Lock()
        self.waiting = threading.Event()

    def acquire(self, blocking=True, timeout=-1):
        if self._lock.locked():
            self.waiting.set()
        if timeout == -1:
            return self._lock.acquire(blocking)
        return self._lock.acquire(blocking, timeout)

    def release(self):
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        self.release()


class _ManualRumbleClock:
    def __init__(self):
        self.value = 100.0
        self.sleep_started = threading.Event()
        self.release_sleep = threading.Event()
        self.sleep_calls = 0

    def perf_counter(self):
        return self.value

    def sleep(self, duration):
        self.sleep_calls += 1
        if self.sleep_calls == 1:
            self.sleep_started.set()
            self.release_sleep.wait(1.0)
        self.value += max(0.0, float(duration))


class CallbackCommitTests(unittest.TestCase):
    def make_bluetooth(self):
        controller = BluetoothController()
        controller.running = True
        controller.connected = True
        controller._connection_generation = 9
        controller.client = _BleClient()
        return controller

    def test_bluetooth_callback_error_never_commits_ready(self):
        controller = self.make_bluetooth()
        events = []

        def connected():
            events.append("connected")
            raise RuntimeError("injected")

        controller.connected_callback = connected
        controller.disconnected_callback = lambda: events.append(
            "disconnected"
        )

        self.assertFalse(controller._commit_application_ready(9))
        self.assertFalse(controller.is_ready)
        self.assertFalse(controller._rumble_accepting)
        self.assertEqual(events, ["connected", "disconnected"])

    def test_bluetooth_link_loss_during_callback_rolls_back(self):
        controller = self.make_bluetooth()
        events = []

        def connected():
            events.append("connected")
            controller.client.is_connected = False

        controller.connected_callback = connected
        controller.disconnected_callback = lambda: events.append(
            "disconnected"
        )

        self.assertFalse(controller._commit_application_ready(9))
        self.assertFalse(controller.is_ready)
        self.assertEqual(events, ["connected", "disconnected"])

    @patch("esp32_bridge.time.sleep")
    def test_esp32_callback_error_disconnects_without_ready(self, _sleep):
        bridge = ESP32Bridge("COM1")
        bridge.running = True
        bridge.connected_channel = 2
        bridge._connection_generation = 5
        bridge.initialize_controller_features = lambda *_args: True
        bridge.connection_rumble = lambda **_kwargs: None
        events = []

        def connected():
            events.append("connected")
            raise RuntimeError("injected")

        bridge.connected_callback = connected
        bridge.disconnected_callback = lambda: events.append(
            "disconnected"
        )
        bridge.send = lambda command: events.append(command) or True
        bridge._restart_scan = lambda: events.append("restart")

        bridge._prepare_connected_controller(2, 5, False)

        self.assertFalse(bridge.is_ready)
        self.assertIsNone(bridge.connected_channel)
        self.assertEqual(
            events,
            [
                "connected",
                "disconnected",
                "ble disconnect",
                "restart",
            ],
        )


class CharacteristicDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_uuid_wins_over_shuffled_handle_fallback(self):
        class Characteristic:
            def __init__(self, uuid, handle, properties):
                self.uuid = uuid
                self.handle = handle
                self.properties = properties

        class Service:
            uuid = "ab7de9be-0000-0000-0000-000000000000"
            characteristics = [
                Characteristic("fallback-notify", 99, ["notify"]),
                Characteristic(COMMAND_RESPONSE_UUID, 3, ["notify"]),
                Characteristic("fallback-write", 2, ["write"]),
                Characteristic(COMMAND_WRITE_UUID, 50, ["write"]),
            ]

        controller = BluetoothController()
        controller.client = type(
            "Client",
            (),
            {"services": [Service()]},
        )()

        await controller._discover_sw2_characteristics()

        self.assertEqual(controller.command_write_uuid, COMMAND_WRITE_UUID)
        self.assertEqual(
            controller.command_response_uuid,
            COMMAND_RESPONSE_UUID,
        )


class CleanupGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_old_client_cleanup_stops_before_discovery(self):
        controller = BluetoothController()
        controller.running = True
        controller.client = _BleClient()
        discovery_calls = []
        disconnect_calls = []
        controller._get_local_mac_value = lambda: 0

        async def fail_disconnect():
            disconnect_calls.append(True)
            controller.running = False
            return False

        async def find_controller():
            discovery_calls.append(True)
            return None

        controller._disconnect_with_retry = fail_disconnect
        controller._find_controller = find_controller

        await controller._run()

        self.assertEqual(discovery_calls, [])
        self.assertGreaterEqual(len(disconnect_calls), 1)

    async def test_bluetooth_close_retains_live_worker_reference(self):
        controller = BluetoothController()
        worker = _AliveThread()
        loop = type(
            "Loop",
            (),
            {
                "is_running": lambda self: False,
                "is_closed": lambda self: False,
            },
        )()
        controller._thread = worker
        controller._loop = loop

        self.assertFalse(controller.close())
        self.assertIs(controller._thread, worker)
        self.assertIs(controller._loop, loop)


class ESP32ShutdownTests(unittest.TestCase):
    def test_open_rejects_stale_worker(self):
        bridge = ESP32Bridge("COM1")
        bridge._read_thread = _AliveThread()

        with self.assertRaises(RuntimeError):
            bridge.open()

    def test_close_waits_for_disconnect_confirmation(self):
        bridge = ESP32Bridge("COM1")
        bridge.running = True
        bridge.connected_channel = 0
        bridge._ready_channel = 0
        bridge.serial = type(
            "Serial",
            (),
            {
                "is_open": True,
                "close": lambda self: setattr(self, "is_open", False),
            },
        )()
        bridge._send_final_zero_rumble = lambda **_kwargs: True
        events = []

        def send(command):
            events.append(command)
            if command == "ble disconnect":
                bridge._disconnect_event.set()
            return True

        bridge.send = send

        self.assertTrue(bridge.close(timeout=0.5))
        self.assertIn("ble disconnect", events)

    def test_close_retains_live_worker_references(self):
        bridge = ESP32Bridge("COM1")
        bridge.running = True
        bridge.serial = type(
            "Serial",
            (),
            {
                "is_open": True,
                "close": lambda self: setattr(self, "is_open", False),
            },
        )()
        rumble = _AliveThread()
        heartbeat = _AliveThread()
        reader = _AliveThread()
        bridge._rumble_thread = rumble
        bridge._heartbeat_thread = heartbeat
        bridge._read_thread = reader
        bridge.send = lambda _command: True

        self.assertFalse(bridge.close(timeout=0.01))
        self.assertIs(bridge._rumble_thread, rumble)
        self.assertIs(bridge._heartbeat_thread, heartbeat)
        self.assertIs(bridge._read_thread, reader)

    def test_missing_channel_mask_is_ignored_while_not_ready(self):
        bridge = ESP32Bridge("COM1")
        bridge.running = True
        bridge.connected_channel = 2
        bridge._ready_channel = None
        bridge._connection_generation = 3
        bridge._channel_missing_count = 2
        bridge._status_grace_until = 0.0

        bridge._handle_text(b'{"cmd":"status lite","ble_channels":0}')

        self.assertEqual(bridge.connected_channel, 2)
        self.assertEqual(bridge._channel_missing_count, 0)

    def test_clean_close_disconnect_event_does_not_call_callback(self):
        bridge = ESP32Bridge("COM1")
        bridge.running = True
        bridge._closing = True
        bridge.connected_channel = 2
        bridge._ready_channel = 2
        bridge._connection_generation = 3
        events = []
        bridge.disconnected_callback = lambda: events.append("callback")
        bridge._restart_scan = lambda: events.append("restart")

        bridge._handle_text(b'{"cmd":"disconnected"}')

        self.assertEqual(events, ["restart"])
        self.assertIsNone(bridge.connected_channel)
        self.assertTrue(bridge._disconnect_event.is_set())

    @patch("esp32_bridge.time.sleep")
    def test_failed_idle_disconnect_restores_rumble_gate(self, _sleep):
        bridge = ESP32Bridge("COM1")
        bridge.running = True
        bridge.connected_channel = 2
        bridge._ready_channel = 2
        bridge._connection_generation = 3
        bridge._rumble_worker_running = True
        bridge._rumble_accepting = True
        bridge._send_final_zero_rumble = lambda **_kwargs: True
        bridge.send = lambda _command: False

        self.assertFalse(bridge.disconnect_for_idle())
        self.assertTrue(bridge._rumble_accepting)

    def test_final_zero_send_lock_obeys_timeout(self):
        bridge = ESP32Bridge("COM1")
        bridge.running = True
        bridge.connected_channel = 2
        bridge._ready_channel = 2
        bridge._connection_generation = 3
        bridge._rumble_send_lock.acquire()
        try:
            started = time.perf_counter()
            self.assertFalse(
                bridge._send_final_zero_rumble(timeout=0.05)
            )
            elapsed = time.perf_counter() - started
        finally:
            bridge._rumble_send_lock.release()

        self.assertLess(elapsed, 0.2)


class WiredShutdownTests(unittest.TestCase):
    @patch("wired_controller.initialize_usb_reports", return_value=True)
    def test_connected_callback_error_never_commits_ready(
        self,
        _initialize,
    ):
        class Device:
            def read(self, _size, _timeout):
                return []

            def write(self, data):
                return len(data)

            def close(self):
                return None

        controller = WiredController()
        controller.running = True
        controller._open_hid = lambda _path: Device()
        controller._start_delayed_reinit = lambda _generation: None
        controller._start_rumble_thread = lambda: None
        events = []

        def connected():
            events.append(("connected", controller.is_ready))
            raise RuntimeError("injected")

        controller.connected_callback = connected
        controller.disconnected_callback = lambda: events.append(
            ("disconnected", controller.is_ready)
        )

        with self.assertRaises(RuntimeError):
            controller._run_device(
                {"path": b"usb-path", "serial_number": "AABBCCDDEEFF"}
            )

        self.assertFalse(controller.is_ready)
        self.assertFalse(controller._rumble_accepting)
        self.assertEqual(
            events,
            [("connected", False), ("disconnected", False)],
        )

    def test_shutdown_send_lock_obeys_overall_deadline(self):
        controller = WiredController()
        controller.running = True
        controller.connected = True
        controller._application_ready = True
        controller._device = object()
        controller._rumble_send_lock.acquire()
        try:
            started = time.perf_counter()
            self.assertFalse(controller.close(timeout=0.05))
            elapsed = time.perf_counter() - started
        finally:
            controller._rumble_send_lock.release()

        self.assertLess(elapsed, 0.2)

    def test_close_retains_live_manager_reference_and_hid_handle(self):
        controller = WiredController()
        worker = _AliveThread()
        device = object()
        controller.running = True
        controller._thread = worker
        controller._device = device

        self.assertFalse(controller.close(timeout=0.01))
        self.assertIs(controller._thread, worker)
        self.assertIs(controller._device, device)

    def test_stale_ordinary_rumble_slot_is_discarded(self):
        class Device:
            def __init__(self):
                self.reports = []

            def write(self, report):
                self.reports.append(bytes(report))
                return len(report)

        controller = WiredController()
        controller.running = True
        controller.connected = True
        controller._application_ready = True
        controller._rumble_accepting = True
        device = Device()
        controller._device = device

        self.assertTrue(controller.send_pro_rumble(80, 800, 160, 800))
        controller._connection_generation += 1
        controller._start_rumble_thread()
        time.sleep(0.05)
        controller._rumble_stop.set()
        controller._rumble_wake.set()
        controller._rumble_thread.join(1.0)

        self.assertEqual(device.reports, [])


class WiredRumbleLatestTests(unittest.TestCase):
    @staticmethod
    def _controller(device):
        controller = WiredController()
        controller.running = True
        controller.connected = True
        controller._application_ready = True
        controller._rumble_accepting = True
        controller._device = device
        return controller

    @staticmethod
    def _stop(controller, clock):
        clock.release_sleep.set()
        controller._rumble_stop.set()
        controller._rumble_wake.set()
        controller._rumble_thread.join(1.0)

    def test_waiting_state_is_replaced_before_hid_write(self):
        device = _RecordingHid()
        clock = _ManualRumbleClock()
        controller = self._controller(device)

        with (
            patch(
                "wired_controller.time.perf_counter",
                side_effect=clock.perf_counter,
            ),
            patch("wired_controller.time.sleep", side_effect=clock.sleep),
        ):
            controller._start_rumble_thread()
            try:
                self.assertTrue(
                    controller.send_pro_rumble(
                        80, 50, 160, 50, priority=True
                    )
                )
                self.assertTrue(device.write_event.wait(1.0))
                device.write_event.clear()

                self.assertTrue(
                    controller.send_pro_rumble(
                        80, 200, 160, 200, priority=True
                    )
                )
                self.assertTrue(clock.sleep_started.wait(1.0))
                self.assertTrue(
                    controller.send_pro_rumble(
                        80, 700, 160, 700, priority=True
                    )
                )
                clock.release_sleep.set()
                self.assertTrue(device.write_event.wait(1.0))
            finally:
                self._stop(controller, clock)

        self.assertEqual(len(device.reports), 2)
        old_frame = controller._encode_vibration(80, 200, 160, 200)
        latest_frame = controller._encode_vibration(80, 700, 160, 700)
        self.assertNotIn(old_frame, device.reports[-1])
        self.assertIn(latest_frame, device.reports[-1])

    def test_pending_priority_is_sticky_but_zero_state_is_latest(self):
        controller = self._controller(_RecordingHid())
        with patch(
            "wired_controller.time.perf_counter",
            side_effect=(100.0, 101.0),
        ):
            self.assertTrue(
                controller.send_pro_rumble(
                    80, 0, 160, 0,
                    priority=True,
                    force_zero=True,
                )
            )
            self.assertTrue(
                controller.send_pro_rumble(80, 700, 160, 700)
            )

        state = controller._rumble_slot
        self.assertTrue(state[3])
        self.assertFalse(state[4])
        self.assertEqual(state[2], 100.0)
        self.assertEqual(state[1], 101.0)
        self.assertIn(
            controller._encode_vibration(80, 700, 160, 700),
            state[0],
        )

    def test_send_lock_wait_uses_latest_zero_and_records_wait_latency(self):
        device = _RecordingHid()
        controller = self._controller(device)
        clock = _ManualRumbleClock()
        send_lock = _ObservedLock()
        controller._rumble_send_lock = send_lock
        send_lock.acquire()
        lock_held = True

        with patch(
            "wired_controller.time.perf_counter",
            side_effect=clock.perf_counter,
        ):
            self.assertTrue(
                controller.send_pro_rumble(80, 700, 160, 700)
            )
            controller._start_rumble_thread()
            try:
                self.assertTrue(send_lock.waiting.wait(1.0))
                self.assertTrue(
                    controller.send_pro_rumble(
                        80, 0, 160, 0,
                        priority=True,
                        force_zero=True,
                    )
                )
                clock.value += 0.025
                send_lock.release()
                lock_held = False
                self.assertTrue(device.write_event.wait(1.0))
            finally:
                if lock_held:
                    send_lock.release()
                controller._rumble_stop.set()
                controller._rumble_wake.set()
                controller._rumble_thread.join(1.0)

        self.assertEqual(len(device.reports), 1)
        self.assertFalse(_rumble_active(device.reports[0]))
        diagnostics = controller.get_rumble_diagnostics()
        self.assertEqual(diagnostics["send_attempts"], 1)
        self.assertEqual(diagnostics["zero_latency_samples"], 1)
        self.assertAlmostEqual(
            diagnostics["zero_latency_max_ms"], 25.0, places=3
        )

    def test_force_zero_shortens_in_progress_audio_deadline(self):
        device = _RecordingHid()
        clock = _ManualRumbleClock()
        controller = self._controller(device)
        controller.set_audio_haptics_active(True)

        with (
            patch(
                "wired_controller.time.perf_counter",
                side_effect=clock.perf_counter,
            ),
            patch("wired_controller.time.sleep", side_effect=clock.sleep),
        ):
            controller._start_rumble_thread()
            try:
                self.assertTrue(
                    controller.send_pro_rumble(
                        80, 50, 160, 50, priority=True
                    )
                )
                self.assertTrue(device.write_event.wait(1.0))
                device.write_event.clear()

                self.assertTrue(
                    controller.send_pro_rumble(80, 700, 160, 700)
                )
                self.assertTrue(clock.sleep_started.wait(1.0))
                self.assertTrue(
                    controller.send_pro_rumble(
                        80, 0, 160, 0,
                        priority=True,
                        force_zero=True,
                    )
                )
                clock.release_sleep.set()
                self.assertTrue(device.write_event.wait(1.0))
            finally:
                self._stop(controller, clock)

        self.assertEqual(len(device.reports), 2)
        self.assertFalse(_rumble_active(device.reports[-1]))
        diagnostics = controller.get_rumble_diagnostics()
        self.assertLess(diagnostics["zero_latency_max_ms"], 10.0)


class ESP32RumbleLatestTests(unittest.TestCase):
    @staticmethod
    def _bridge(serial_port=None):
        bridge = ESP32Bridge("COM1")
        bridge.serial = serial_port or _RecordingSerial()
        bridge.running = True
        bridge.connected_channel = 2
        bridge._ready_channel = 2
        bridge._connection_generation = 3
        bridge._rumble_worker_running = True
        bridge._rumble_accepting = True
        return bridge

    def test_pending_priority_is_sticky_but_force_zero_is_latest(self):
        bridge = self._bridge()
        with patch(
            "esp32_bridge.time.perf_counter",
            side_effect=(100.0, 101.0),
        ):
            self.assertTrue(
                bridge.send_pro_rumble_latest(
                    80, 0, 160, 0,
                    priority=True,
                    force_zero=True,
                )
            )
            self.assertTrue(
                bridge.send_pro_rumble_latest(80, 700, 160, 700)
            )

        state = bridge._rumble_pending
        self.assertEqual(state[3], 700)
        self.assertEqual(state[5], 700)
        self.assertTrue(state[6])
        self.assertFalse(state[7])
        self.assertEqual(state[9], 100.0)
        self.assertEqual(state[8], 101.0)

    def test_send_lock_wait_uses_latest_zero_and_records_wait_latency(self):
        serial_port = _RecordingSerial()
        bridge = self._bridge(serial_port)
        clock = _ManualRumbleClock()
        send_lock = _ObservedLock()
        bridge._rumble_send_lock = send_lock
        send_lock.acquire()
        lock_held = True
        worker = threading.Thread(target=bridge._rumble_output_loop)

        with patch(
            "esp32_bridge.time.perf_counter",
            side_effect=clock.perf_counter,
        ):
            self.assertTrue(
                bridge.send_pro_rumble_latest(80, 700, 160, 700)
            )
            worker.start()
            try:
                self.assertTrue(send_lock.waiting.wait(1.0))
                self.assertTrue(
                    bridge.send_pro_rumble_latest(
                        80, 0, 160, 0,
                        priority=True,
                        force_zero=True,
                    )
                )
                clock.value += 0.025
                send_lock.release()
                lock_held = False
                self.assertTrue(serial_port.write_event.wait(1.0))
            finally:
                if lock_held:
                    send_lock.release()
                with bridge._rumble_condition:
                    bridge._rumble_worker_running = False
                    bridge._rumble_condition.notify_all()
                worker.join(1.0)

        self.assertEqual(len(serial_port.writes), 1)
        payload_hex = serial_port.writes[0].decode().split()[3]
        self.assertFalse(_rumble_active(bytes.fromhex(payload_hex)))
        diagnostics = bridge.get_rumble_diagnostics()
        self.assertEqual(diagnostics["send_attempts"], 1)
        self.assertEqual(diagnostics["zero_latency_samples"], 1)
        self.assertAlmostEqual(
            diagnostics["zero_latency_max_ms"], 25.0, places=3
        )


class RuntimeCleanupTests(unittest.TestCase):
    def test_readiness_requires_application_ready(self):
        controller = type(
            "Controller",
            (),
            {"connected": True, "is_ready": False},
        )()
        self.assertFalse(controller_application_ready(controller))

    def test_stuck_dispatcher_does_not_close_xinput_state(self):
        class Dispatcher:
            def __init__(self):
                self.calls = []

            def stop(self, timeout):
                self.calls.append(timeout)
                return False

        class XInput:
            def __init__(self):
                self.closed = False
                self.rumble_stops = []

            def close(self):
                self.closed = True

            def stop_rumble_dispatcher(self, timeout):
                self.rumble_stops.append(timeout)
                return True

        dispatcher = Dispatcher()
        xinput = XInput()

        self.assertFalse(
            close_xinput_after_dispatcher(
                dispatcher,
                xinput,
                stop_timeout=0.01,
                grace_timeout=0.02,
            )
        )
        self.assertEqual(dispatcher.calls, [0.01, 0.02])
        self.assertFalse(xinput.closed)
        self.assertEqual(xinput.rumble_stops, [0.02])

    def test_xinput_close_failure_is_propagated(self):
        class Dispatcher:
            def stop(self, timeout):
                return True

        class XInput:
            def close(self):
                return False

        self.assertFalse(close_xinput_after_dispatcher(
            Dispatcher(),
            XInput(),
        ))


if __name__ == "__main__":
    unittest.main()
