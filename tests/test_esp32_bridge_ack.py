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

    def test_buffered_ack_is_rejected_if_connection_changes_while_waiting(self):
        bridge = self.make_bridge(bytes([0x15, 0x91, 0x01, 0x04]))
        wait = bridge._command_response_event.wait

        def disconnect_before_consuming_ack(timeout):
            self.assertTrue(wait(timeout))
            with bridge._state_lock:
                bridge._connection_generation += 1
            return True

        bridge._command_response_event.wait = disconnect_before_consuming_ack
        self.assertFalse(bridge._send_controller_command_wait_ack(
            2, 7, 0x15, 0x04, b"", timeout=0.1,
        ))

    def test_same_channel_reuse_does_not_let_old_waiter_steal_ack(self):
        bridge = self.make_bridge(b"")
        first_wait_entered = threading.Event()
        release_first_wait = threading.Event()
        send_count = 0
        wait = bridge._command_response_event.wait

        def wait_for_ack(timeout):
            if threading.current_thread() is old_thread:
                first_wait_entered.set()
                if not release_first_wait.wait(1.0):
                    raise AssertionError("Timed out waiting to replace the connection")
                return False
            return wait(timeout)

        def send(_command):
            nonlocal send_count
            send_count += 1
            if send_count == 2:
                with bridge._command_response_lock:
                    bridge._command_response = (
                        8,
                        bytes([0x15, 0x91, 0x01, 0x04]),
                    )
                bridge._command_response_event.set()
            return True

        bridge.send = send
        bridge._command_response_event.wait = wait_for_ack
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
        self.assertTrue(first_wait_entered.wait(1.0))
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
        release_first_wait.set()
        old_thread.join(1.0)
        new_thread.join(1.0)

        self.assertFalse(results["old"])
        self.assertTrue(results["new"])

    def test_initialization_write_keeps_connection_state_locked(self):
        bridge = self.make_bridge(b"")
        lock_available = []

        def send(_command):
            def try_change_connection():
                acquired = bridge._state_lock.acquire(blocking=False)
                lock_available.append(acquired)
                if acquired:
                    bridge._connection_generation += 1
                    bridge._state_lock.release()

            replacement = threading.Thread(target=try_change_connection)
            replacement.start()
            replacement.join(1.0)
            return False

        bridge.send = send
        self.assertFalse(bridge._send_controller_command_wait_ack(2, 7, 0x15, 0x04, b""))
        self.assertEqual(lock_available, [False])
        self.assertEqual(bridge._connection_generation, 7)


if __name__ == "__main__":
    unittest.main()
