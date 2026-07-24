import asyncio
import threading
import unittest
from unittest.mock import patch

from bluetooth_controller import BluetoothController
from esp32_bridge import ESP32Bridge
from wired_controller import WiredController


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


class _HidDevice:
    def __init__(self, events):
        self.events = events

    def write(self, _report):
        self.events.append("zero_rumble")
        return 1


class TransportShutdownTests(unittest.TestCase):
    @patch("esp32_bridge.time.sleep")
    def test_esp32_zero_rumble_precedes_serial_close(self, _sleep):
        events = []
        bridge = ESP32Bridge.__new__(ESP32Bridge)
        bridge._closing = False
        bridge.running = True
        bridge.connected_channel = 0
        bridge._rumble_condition = threading.Condition()
        bridge._rumble_worker_running = True
        bridge._rumble_pending = None
        bridge._rumble_thread = None
        bridge._heartbeat_thread = None
        bridge._read_thread = None
        bridge._status_event = threading.Event()
        bridge._command_response_event = threading.Event()
        bridge.serial = _Serial(events)
        bridge.send_pro_rumble = lambda *_args: events.append("zero_rumble")
        bridge.send = lambda command: events.append(command)
        bridge._release_preferred_connection_request = lambda: None

        bridge.close()

        self.assertLess(
            events.index("zero_rumble"), events.index("transport_close")
        )

    def test_bluetooth_zero_rumble_precedes_disconnect(self):
        events = []
        controller = BluetoothController.__new__(BluetoothController)
        controller.client = _BleClient(events)
        controller._rumble_pending = object()
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
        controller.connected = True
        controller.running = True
        controller._rumble_packet_id = 0
        controller._rumble_lock = threading.Lock()
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


if __name__ == "__main__":
    unittest.main()
