import threading
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import runtime_cleanup


@unittest.skipUnless(os.name == "nt", "Windows process wait API")
class BridgeGuiLifetimeTests(unittest.TestCase):
    def test_gui_process_exit_requests_child_shutdown_and_closes_handle(self):
        callback = threading.Event()
        kernel = Mock()
        kernel.OpenProcess.return_value = 456
        kernel.WaitForSingleObject.return_value = 0
        with patch("runtime_cleanup.ctypes.WinDLL", return_value=kernel):
            watcher = runtime_cleanup.watch_parent_process_exit(123, callback.set)
            watcher.join(1.0)
        self.assertTrue(callback.is_set())
        kernel.OpenProcess.assert_called_once_with(0x00100000, False, 123)
        kernel.WaitForSingleObject.assert_called_once_with(456, 0xFFFFFFFF)
        kernel.CloseHandle.assert_called_once_with(456)

    def test_no_gui_pid_does_not_create_watcher(self):
        with patch("runtime_cleanup.ctypes.WinDLL") as api:
            self.assertIsNone(runtime_cleanup.watch_parent_process_exit(0, Mock()))
        api.assert_not_called()

    def test_failed_wait_does_not_claim_parent_has_exited(self):
        callback = Mock()
        kernel = Mock()
        kernel.OpenProcess.return_value = 456
        kernel.WaitForSingleObject.return_value = 0xFFFFFFFF
        with patch("runtime_cleanup.ctypes.WinDLL", return_value=kernel):
            watcher = runtime_cleanup.watch_parent_process_exit(123, callback)
            watcher.join(1.0)
        callback.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(456)

    def test_parent_already_gone_before_child_opens_handle(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 0
        callback = Mock()
        with patch("runtime_cleanup.ctypes.WinDLL", return_value=kernel), \
             patch("runtime_cleanup.ctypes.get_last_error", return_value=87):
            self.assertIsNone(runtime_cleanup.watch_parent_process_exit(123, callback))
        callback.assert_called_once()

    def test_real_windows_process_termination_wakes_waiter(self):
        exited = threading.Event()
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            watcher = runtime_cleanup.watch_parent_process_exit(process.pid, exited.set)
            self.assertIsNotNone(watcher)
            self.assertFalse(exited.is_set())
            process.terminate()
            process.wait(timeout=3)
            self.assertTrue(exited.wait(3))
            watcher.join(1)
            self.assertFalse(watcher.is_alive())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
