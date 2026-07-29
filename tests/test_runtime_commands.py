import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from command_queue import enqueue_controller_command
from runtime_commands import ControllerCommandInbox


class RuntimeCommandInboxTests(unittest.TestCase):
    def test_queued_command_has_priority_and_is_finished(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_path = root / "controller_command.txt"
            queue_dir = root / "controller_commands"
            legacy_path.write_text("pin", encoding="utf-8")
            request_id = enqueue_controller_command("reload_settings", queue_dir)
            inbox = ControllerCommandInbox(legacy_path, queue_dir)

            pending = inbox.next()
            self.assertEqual(pending.command, "reload_settings")
            self.assertEqual(pending.request_id, request_id)
            pending.clear_legacy()
            self.assertTrue(legacy_path.exists())
            pending.finish()

            legacy = inbox.next()
            self.assertEqual(legacy.command, "pin")
            legacy.clear_legacy()
            legacy.finish()
            self.assertFalse(legacy_path.exists())

    def test_blank_legacy_command_is_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_path = root / "controller_command.txt"
            legacy_path.write_text("  \n", encoding="utf-8")
            inbox = ControllerCommandInbox(
                legacy_path,
                root / "controller_commands",
            )

            self.assertIsNone(inbox.next())
            self.assertFalse(legacy_path.exists())

    def test_reset_discards_stale_queue_and_legacy_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_path = root / "controller_command.txt"
            queue_dir = root / "controller_commands"
            legacy_path.write_text("pin", encoding="utf-8")
            enqueue_controller_command("reload_settings", queue_dir)
            inbox = ControllerCommandInbox(legacy_path, queue_dir)

            inbox.reset(process_started_at=10**20)

            self.assertFalse(legacy_path.exists())
            self.assertIsNone(inbox.next())


if __name__ == "__main__":
    unittest.main()
