import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from command_queue import (
    cleanup_controller_commands,
    enqueue_controller_command,
    finish_controller_command,
    next_controller_command,
)
from config_utils import config_file_lock


class CommandQueueTests(unittest.TestCase):
    def test_requests_are_not_overwritten_and_are_processed_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            first_id = enqueue_controller_command("first", directory)
            second_id = enqueue_controller_command("second", directory)

            first = next_controller_command(directory)
            self.assertEqual(first["id"], first_id)
            self.assertEqual(first["command"], "first")
            finish_controller_command(first)

            second = next_controller_command(directory)
            self.assertEqual(second["id"], second_id)
            self.assertEqual(second["command"], "second")
            finish_controller_command(second)
            self.assertIsNone(next_controller_command(directory))

    def test_invalid_request_is_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text("{bad", encoding="utf-8")
            self.assertIsNone(next_controller_command(directory))
            self.assertTrue((Path(directory) / "bad.invalid").is_file())

    def test_requests_from_previous_process_are_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            enqueue_controller_command("calibrate_gyro", directory)
            removed = cleanup_controller_commands(
                not_before=10**20, queue_dir=directory
            )
            self.assertEqual(removed, 1)
            self.assertIsNone(next_controller_command(directory))

    def test_configuration_lock_is_reentrant_in_one_thread(self):
        with config_file_lock():
            with config_file_lock():
                pass


if __name__ == "__main__":
    unittest.main()
