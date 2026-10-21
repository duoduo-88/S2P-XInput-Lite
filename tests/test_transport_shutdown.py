import asyncio
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bluetooth_controller import BluetoothController
from esp32_bridge import ESP32Bridge
from wired_controller import WiredController, _rumble_active


class _Serial:
    def __init__(self, events):
        self.events = events
        self.is_open = True

    def close(self):
        self.events.append("transport_close")
        self.is_open = False


class _BleClient:
    def __init__(self, events):
        self.events = events
        self.is_connected = True

    async def disconnect(self):
        self.events.append("transport_close")
        self.is_connected = False


class _HidDevice:
    def __init__(self, events):
        self.events = events

    def write(self, _report):
        self.events.append("zero_rumble")
        return 1


class TransportShutdownTests(unittest.TestCase):
    def test_shutdown_handler_is_registered_before_transport_open(self):
        source = (
            Path(__file__).resolve().parents[1] / "src" / "main.py"
        ).read_text(encoding="utf-8")

        self.assertLess(
            source.index("signal.signal("),
            source.index("controller.open()"),
        )

    @patch("esp32_bridge.time.sleep")
    def test_esp32_zero_rumble_precedes_serial_close(self, _sleep):
        events = []
        bridge = ESP32Bridge.__new__(ESP32Bridge)
        bridge._closing = False
        bridge.running = True
        bridge._state_lock = threading.RLock()
        bridge.connected_channel = 0
        bridge._ready_channel = 0
        bridge._connection_generation = 1
        bridge._rumble_condition = threading.Condition()
        bridge._rumble_send_lock = threading.Lock()
        bridge._rumble_worker_running = True
        bridge._rumble_accepting = True
        bridge._rumble_pending = None
        bridge._rumble_thread = None
        bridge._heartbeat_thread = None
        bridge._read_thread = None
        bridge._status_event = threading.Event()
        bridge._disconnect_event = threading.Event()
        bridge._command_response_event = threading.Event()
        bridge.serial = _Serial(events)
        bridge._send_final_zero_rumble = (
            lambda **_kwargs: events.append("zero_rumble") or True
        )
        bridge.send = lambda command: events.append(command)
        bridge._release_preferred_connection_request = lambda: None

        bridge.close()

        self.assertLess(
            events.index("zero_rumble"), events.index("transport_close")
        )
        self.assertLess(
            events.index("zero_rumble"), events.index("ble disconnect")
        )

    @patch("esp32_bridge.time.sleep")
    def test_esp32_idle_disconnect_sends_zero_synchronously(self, _sleep):
        events = []
        bridge = ESP32Bridge.__new__(ESP32Bridge)
        bridge.running = True
        bridge._state_lock = threading.RLock()
        bridge.connected_channel = 0
        bridge._ready_channel = 0
        bridge._connection_generation = 1
        bridge._closing = False
        bridge._disconnect_event = threading.Event()
        bridge._rumble_condition = threading.Condition()
        bridge._rumble_worker_running = True
        bridge._rumble_accepting = True
        bridge._feedback_active = False
        bridge._rumble_pending = object()
        bridge._send_final_zero_rumble = (
            lambda **_kwargs: events.append("zero_rumble") or True
        )
        def send(command):
            events.append(command)
            if command == "ble disconnect":
                bridge._disconnect_event.set()
            return True
        bridge.send = send

        self.assertTrue(bridge.disconnect_for_idle())

        self.assertFalse(bridge._rumble_accepting)
        self.assertIsNone(bridge._rumble_pending)
        self.assertLess(
            events.index("zero_rumble"), events.index("ble disconnect")
        )

    def test_bluetooth_zero_rumble_precedes_disconnect(self):
        events = []
        controller = BluetoothController.__new__(BluetoothController)
        controller._state_lock = threading.RLock()
        controller.client = _BleClient(events)
        controller._closing = True
        controller.connected = True
        controller._application_ready = True
        controller._rumble_accepting = True
        controller._disconnect_notification_pending = False
        controller._connection_generation = 1
        controller._rumble_diag_lock = threading.Lock()
        controller._rumble_pending = object()
        controller._rumble_send_task = None
        controller._feedback_task = None
        controller._feedback_active = False
        controller._release_preferred_connection_request = lambda: None

        async def send_zero(*_args):
            events.append("zero_rumble")

        controller._send_pro_rumble_async = send_zero

        asyncio.run(controller._disconnect())

        self.assertLess(
            events.index("zero_rumble"), events.index("transport_close")
        )

    def test_wired_zero_rumble_precedes_hid_close(self):
        events = []
        controller = WiredController.__new__(WiredController)
        controller.input_callback = object()
        controller._state_lock = threading.RLock()
        controller.connected = True
        controller._application_ready = True
        controller.running = True
        controller._rumble_packet_id = 0
        controller._rumble_lock = threading.Lock()
        controller._rumble_send_lock = threading.Lock()
        controller._rumble_accepting = True
        controller._feedback_active = False
        controller._hid_write_lock = threading.Lock()
        controller._device_lock = threading.Lock()
        controller._rumble_slot = None
        controller._device = _HidDevice(events)
        controller._rumble_stop = threading.Event()
        controller._rumble_wake = threading.Event()
        controller._rumble_thread = None
        controller._thread = None

        def close_device():
            events.append("transport_close")
            controller._device = None

        controller._close_device = close_device

        controller.close()

        self.assertLess(
            events.index("zero_rumble"), events.index("transport_close")
        )

    def test_wired_claimed_nonzero_cannot_follow_shutdown_zero(self):
        class BlockingHid:
            def __init__(self):
                self.entered = threading.Event()
                self.release = threading.Event()
                self.reports = []

            def write(self, report):
                if not self.reports:
                    self.entered.set()
                    self.release.wait(1.0)
                self.reports.append(bytes(report))
                return len(report)

        device = BlockingHid()
        controller = WiredController()
        controller.running = True
        controller.connected = True
        controller._device = device
        controller._rumble_accepting = True
        controller._start_rumble_thread()

        self.assertTrue(controller.send_pro_rumble(80, 800, 160, 800))
        self.assertTrue(device.entered.wait(1.0))

        close_thread = threading.Thread(target=controller.close)
        close_thread.start()
        deadline = time.monotonic() + 1.0
        while controller._rumble_accepting and time.monotonic() < deadline:
            time.sleep(0.001)
        device.release.set()
        close_thread.join(2.0)

        self.assertFalse(close_thread.is_alive())
        self.assertGreaterEqual(len(device.reports), 2)
        self.assertFalse(_rumble_active(device.reports[-1]))


if __name__ == "__main__":
    unittest.main()
