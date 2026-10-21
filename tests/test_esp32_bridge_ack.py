import threading
import unittest

from esp32_bridge import ESP32Bridge


class ESP32BridgeAckTests(unittest.TestCase):
    def make_bridge(self, response):
        bridge = ESP32Bridge.__new__(ESP32Bridge)
        bridge.running = True
        bridge._closing = False
        bridge._state_lock = threading.RLock()
        bridge.connected_channel = 2
        bridge._ready_channel = None
        bridge._connection_generation = 7
        bridge._command_response_lock = threading.Lock()
        bridge._command_wait_lock = threading.Lock()
        bridge._command_response_event = threading.Event()
        bridge._command_response = None

        def send(_command):
            with bridge._command_response_lock:
                bridge._command_response = (7, response)
            bridge._command_response_event.set()
            return True

        bridge.send = send
        return bridge

    def test_command_ack_requires_matching_command_and_subcommand(self):
        bridge = self.make_bridge(bytes([0x15, 0x91, 0x01, 0x04]))
        self.assertTrue(
            bridge._send_controller_command_wait_ack(
                2, 7, 0x15, 0x04, b"", timeout=0.01
            )
        )

    def test_wrong_subcommand_ack_is_rejected(self):
        bridge = self.make_bridge(bytes([0x15, 0x91, 0x01, 0x02]))
        self.assertFalse(
            bridge._send_controller_command_wait_ack(
                2, 7, 0x15, 0x04, b"", timeout=0.01
            )
        )

    def test_same_channel_reuse_does_not_let_old_waiter_steal_ack(self):
        bridge = self.make_bridge(b"")
        first_send_entered = threading.Event()
        release_first_send = threading.Event()
        send_count = 0
        send_lock = threading.Lock()

        def send(_command):
            nonlocal send_count
            with send_lock:
                send_count += 1
                call_number = send_count
            if call_number == 1:
                first_send_entered.set()
                release_first_send.wait(1.0)
            else:
                with bridge._command_response_lock:
                    bridge._command_response = (
                        8,
                        bytes([0x15, 0x91, 0x01, 0x04]),
                    )
                bridge._command_response_event.set()
            return True

        bridge.send = send
        results = {}
        old_thread = threading.Thread(
            target=lambda: results.setdefault(
                "old",
                bridge._send_controller_command_wait_ack(
                    2, 7, 0x15, 0x04, b"", timeout=0.2
                ),
            )
        )
        old_thread.start()
        self.assertTrue(first_send_entered.wait(1.0))
        with bridge._state_lock:
            bridge._connection_generation = 8

        new_thread = threading.Thread(
            target=lambda: results.setdefault(
                "new",
                bridge._send_controller_command_wait_ack(
                    2, 8, 0x15, 0x04, b"", timeout=0.2
                ),
            )
        )
        new_thread.start()
        release_first_send.set()
        old_thread.join(1.0)
        new_thread.join(1.0)

        self.assertFalse(results["old"])
        self.assertTrue(results["new"])


if __name__ == "__main__":
    unittest.main()
