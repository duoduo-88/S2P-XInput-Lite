import threading
import unittest

from esp32_bridge import ESP32Bridge


class ESP32BridgeAckTests(unittest.TestCase):
    def make_bridge(self, response):
        bridge = ESP32Bridge.__new__(ESP32Bridge)
        bridge.running = True
        bridge.connected_channel = 2
        bridge._command_response_lock = threading.Lock()
        bridge._command_response_event = threading.Event()
        bridge._command_response = None

        def send(_command):
            with bridge._command_response_lock:
                bridge._command_response = response
            bridge._command_response_event.set()
            return True

        bridge.send = send
        return bridge

    def test_command_ack_requires_matching_command_and_subcommand(self):
        bridge = self.make_bridge(bytes([0x15, 0x91, 0x01, 0x04]))
        self.assertTrue(
            bridge._send_controller_command_wait_ack(
                2, 0x15, 0x04, b"", timeout=0.01
            )
        )

    def test_wrong_subcommand_ack_is_rejected(self):
        bridge = self.make_bridge(bytes([0x15, 0x91, 0x01, 0x02]))
        self.assertFalse(
            bridge._send_controller_command_wait_ack(
                2, 0x15, 0x04, b"", timeout=0.01
            )
        )


if __name__ == "__main__":
    unittest.main()
