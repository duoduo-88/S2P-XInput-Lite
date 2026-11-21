import json
import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from runtime_status import ControllerStatusPublisher, SETTINGS_RELOAD_RESULT_LIMIT, settings_reload_result


class RuntimeStatusTests(unittest.TestCase):
    def test_completed_reload_survives_later_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controller_status.json"
            publisher = ControllerStatusPublisher(path)

            def publish(request_id, state, message=""):
                self.assertTrue(publisher.publish(
                    settings_reload_request_id=request_id,
                    settings_reload_state=state,
                    settings_reload_message=message,
                ))

            publish("preview", "failed", "calibration is running")
            publish("main", "running")
            publish("main", "success")
            publisher.publish(battery_percent=75)
            status = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(status["settings_reload_request_id"], "main")
            self.assertEqual(status["settings_reload_results"], {
                "preview": {"state": "failed", "message": "calibration is running"},
                "main": {"state": "success", "message": ""},
            })

            for index in range(SETTINGS_RELOAD_RESULT_LIMIT):
                publish(str(index), "success")
            status = publisher.snapshot()
            self.assertEqual(len(status["settings_reload_results"]), SETTINGS_RELOAD_RESULT_LIMIT)
            self.assertIsNone(settings_reload_result(status, "preview"))
            self.assertEqual(settings_reload_result(status, "0"), {"state": "success", "message": ""})

    def test_reload_result_supports_a_connector_with_only_the_latest_reply(self):
        status = {"settings_reload_request_id": "saved", "settings_reload_state": "running"}
        self.assertIsNone(settings_reload_result(status, "saved"))
        status.update(settings_reload_state="failed", settings_reload_message="calibration is running")
        self.assertIsNone(settings_reload_result(status, "different"))
        self.assertEqual(settings_reload_result(status, "saved"), {
            "state": "failed", "message": "calibration is running",
        })

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
