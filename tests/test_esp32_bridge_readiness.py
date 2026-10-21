import threading
import unittest
from unittest.mock import patch

from esp32_bridge import ESP32Bridge, SERIAL_RECEIVE_BUFFER_LIMIT


class ESP32BridgeReadinessTests(unittest.TestCase):
    def make_bridge(self):
        bridge = ESP32Bridge.__new__(ESP32Bridge)
        bridge.running = True
        bridge._closing = False
        bridge._state_lock = threading.RLock()
        bridge.connected_channel = 2
        bridge._ready_channel = None
        bridge._connection_generation = 7
        bridge._connecting = False
        bridge.controller_id = "AA:BB:CC:DD:EE:FF"
        bridge._rumble_condition = threading.Condition()
        bridge._rumble_accepting = False
        bridge._rumble_pending = None
        bridge._command_response_lock = threading.Lock()
        bridge._command_wait_lock = threading.Lock()
        bridge._command_response_event = threading.Event()
        bridge._command_response = None
        bridge._disconnect_event = threading.Event()
        bridge._last_input_time = 0.0
        bridge.input_callback = None
        bridge.connected_callback = None
        bridge.send = lambda _command: True
        bridge._restart_scan = lambda: None
        bridge.connection_rumble = lambda **_kwargs: None
        return bridge

    @patch("esp32_bridge.threading.Thread")
    def test_ble_connected_event_does_not_report_ready_immediately(
        self,
        thread_factory,
    ):
        bridge = self.make_bridge()
        events = []
        bridge._channel_missing_count = 0
        bridge._status_grace_until = 0.0
        bridge._pending_pair = False
        bridge.connected_callback = lambda: events.append("connected")

        bridge._handle_text(b'{"cmd":"connected","channel":2}\n')

        self.assertEqual(events, [])
        self.assertIsNone(bridge._ready_channel)
        self.assertFalse(bridge._rumble_accepting)
        thread_factory.assert_called_once()
        self.assertEqual(
            thread_factory.call_args.kwargs["target"],
            bridge._prepare_connected_controller,
        )

    @patch("esp32_bridge.time.sleep")
    def test_ready_callback_runs_only_after_initialization(self, _sleep):
        bridge = self.make_bridge()
        events = []
        bridge.initialize_controller_features = lambda _channel, _generation: (
            events.append("initialize") or True
        )
        bridge.connected_callback = lambda: events.append(
            ("connected", bridge.is_ready)
        )
        bridge.connection_rumble = (
            lambda **_kwargs: events.append("rumble")
        )

        bridge._prepare_connected_controller(2, 7, False)

        self.assertEqual(
            events,
            ["initialize", ("connected", False), "rumble"],
        )
        self.assertEqual(bridge._ready_channel, 2)
        self.assertTrue(bridge._rumble_accepting)

    def test_initialization_failure_disconnects_without_ready_callback(self):
        bridge = self.make_bridge()
        events = []
        bridge.initialize_controller_features = (
            lambda _channel, _generation: False
        )
        bridge.connected_callback = lambda: events.append("connected")
        bridge.send = lambda command: events.append(command) or True
        bridge._restart_scan = lambda: events.append("restart_scan")

        bridge._prepare_connected_controller(2, 7, False)

        self.assertNotIn("connected", events)
        self.assertEqual(events, ["ble disconnect", "restart_scan"])
        self.assertIsNone(bridge.connected_channel)
        self.assertIsNone(bridge._ready_channel)
        self.assertFalse(bridge._rumble_accepting)

    @patch("esp32_bridge.time.sleep")
    def test_stale_generation_never_reports_ready(self, _sleep):
        bridge = self.make_bridge()
        events = []
        bridge.initialize_controller_features = (
            lambda _channel, _generation: True
        )
        bridge.connected_callback = lambda: events.append("connected")

        bridge._prepare_connected_controller(2, 6, False)

        self.assertEqual(events, [])
        self.assertIsNone(bridge._ready_channel)

    def test_stale_pairing_preparation_sends_no_pairing_command(self):
        bridge = self.make_bridge()
        events = []
        bridge.pair_controller_to_esp32 = (
            lambda _channel, _generation: events.append("pair") or True
        )

        bridge._prepare_connected_controller(2, 6, True)

        self.assertEqual(events, [])

    def test_disconnect_immediately_after_final_check_blocks_callback(self):
        bridge = self.make_bridge()
        events = []
        bridge.initialize_controller_features = (
            lambda _channel, _generation: True
        )
        bridge.connected_callback = lambda: events.append("connected")
        original_check = bridge._connection_is_current
        check_count = 0

        def disconnect_after_final_check(channel, generation):
            nonlocal check_count
            check_count += 1
            current = original_check(channel, generation)
            if check_count == 3 and current:
                with bridge._state_lock:
                    bridge._connection_generation += 1
            return current

        bridge._connection_is_current = disconnect_after_final_check

        bridge._prepare_connected_controller(2, 7, False)

        self.assertEqual(events, [])
        self.assertFalse(bridge.is_ready)

    def test_command_ack_is_accepted_before_ready(self):
        bridge = self.make_bridge()
        response = b"\x15\x91\x01\x04"

        result = bridge._handle_binary(b"\x83" + response)

        self.assertIsNone(result)
        self.assertEqual(bridge._command_response, (7, response))
        self.assertTrue(bridge._command_response_event.is_set())

    def test_ordinary_input_is_blocked_before_ready(self):
        bridge = self.make_bridge()
        received = []
        bridge.input_callback = received.append

        result = bridge._handle_binary(b"\x03input")

        self.assertIsNone(result)
        self.assertEqual(received, [])
        self.assertEqual(bridge._last_input_time, 0.0)

    def test_ordinary_input_is_dispatched_after_ready(self):
        bridge = self.make_bridge()
        received = []
        bridge._ready_channel = 2
        bridge.input_callback = received.append

        result = bridge._handle_binary(b"\x03input")

        self.assertEqual(result, b"input")
        self.assertEqual(received, [b"input"])
        self.assertGreater(bridge._last_input_time, 0.0)

    def test_input_before_disconnect_in_same_cdc_read_is_discarded(self):
        bridge = self.make_bridge()
        bridge._ready_channel = 2
        bridge._channel_missing_count = 0
        bridge._disconnect_event = threading.Event()
        bridge.disconnected_callback = None
        bridge._restart_scan = lambda: None
        received = []
        bridge.input_callback = received.append
        data = b"\x03input"
        stream = (
            b"\xaa\x55"
            + bytes([len(data)])
            + data
            + b'{"cmd":"disconnected"}\n'
        )

        class SerialChunk:
            def __init__(self):
                self.remaining = bytearray(stream)
                self.reads = 0

            @property
            def in_waiting(self):
                return len(self.remaining)

            def read(self, size):
                self.reads += 1
                if not self.remaining:
                    bridge.running = False
                    return b""
                result = bytes(self.remaining[:size])
                del self.remaining[:size]
                return result

        bridge.serial = SerialChunk()

        bridge._read_loop()

        self.assertEqual(received, [])


class ESP32BridgeReceiveBufferTests(unittest.TestCase):
    def test_corrupt_receive_buffer_is_bounded_and_resynchronized(self):
        tail = b"\xaa\x55\x03abc"
        buf = bytearray(
            b"x" * (SERIAL_RECEIVE_BUFFER_LIMIT + 32) + tail
        )

        ESP32Bridge._limit_receive_buffer(buf)

        self.assertEqual(buf, tail)
        self.assertLessEqual(len(buf), SERIAL_RECEIVE_BUFFER_LIMIT)

    def test_partial_packet_header_is_preserved(self):
        buf = bytearray(
            b"x" * (SERIAL_RECEIVE_BUFFER_LIMIT + 1) + b"\xaa"
        )

        ESP32Bridge._limit_receive_buffer(buf)

        self.assertEqual(buf, b"\xaa")

    def test_unframed_corrupt_data_is_discarded(self):
        buf = bytearray(b"x" * (SERIAL_RECEIVE_BUFFER_LIMIT + 1))

        ESP32Bridge._limit_receive_buffer(buf)

        self.assertEqual(buf, b"")


if __name__ == "__main__":
    unittest.main()
