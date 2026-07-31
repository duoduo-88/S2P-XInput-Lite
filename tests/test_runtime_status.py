import json
import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from runtime_status import ControllerStatusPublisher


class RuntimeStatusTests(unittest.TestCase):
    def test_stage_is_in_memory_until_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controller_status.json"
            timestamps = iter((1.0, 2.0, 3.0))
            publisher = ControllerStatusPublisher(
                path,
                clock=lambda: next(timestamps),
            )

            publisher.stage(state="connected", battery_percent=75)
            self.assertFalse(path.exists())

            self.assertTrue(publisher.publish(mode="wired"))
            status = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(status["state"], "connected")
            self.assertEqual(status["battery_percent"], 75)
            self.assertEqual(status["mode"], "wired")
            self.assertEqual(status["updated_at"], 3.0)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_snapshot_cannot_mutate_internal_state(self):
        publisher = ControllerStatusPublisher(clock=lambda: 1.0)
        snapshot = publisher.snapshot()
        snapshot["state"] = "changed"
        snapshot["rumble"]["injected"] = True
        snapshot["mag_orientation_bins"].append("injected")
        self.assertEqual(publisher.snapshot()["state"], "starting")
        self.assertEqual(publisher.snapshot()["rumble"], {})
        self.assertEqual(publisher.snapshot()["mag_orientation_bins"], [])


if __name__ == "__main__":
    unittest.main()
