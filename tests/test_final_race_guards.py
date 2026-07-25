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
from wired_controller import WiredController


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
        bridge.connection_rumble = lambda: None
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
        bridge._send_final_zero_rumble = lambda: True
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


if __name__ == "__main__":
    unittest.main()
