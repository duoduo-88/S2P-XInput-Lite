"""Thread-safe publication of the connector's runtime status."""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path


STATUS_PATH = Path(__file__).with_name("controller_status.json")


def initial_controller_status(updated_at):
    return {
        "state": "starting",
        "mode": None,
        "battery_percent": None,
        "battery_voltage": None,
        "charging": False,
        "wired_full_report": None,
        "wired_polling_rate": None,
        "wired_processing_rate": None,
        "input_report_rate": None,
        "xinput_output_rate": None,
        "xinput_slot": None,
        "sensor_mode": None,
        "gyro_raw": None,
        "accel_raw": None,
        "report_time": None,
        "report_delta": None,
        "mag_field_valid": None,
        "gyro_bias_samples": 0,
        "gyro_calibration_state": "idle",
        "gyro_calibration_message": "",
        "gyro_calibration_quality": None,
        "mag_calibration_state": "idle",
        "mag_calibration_message": "",
        "mag_calibration_progress": 0,
        "mag_calibration_spans": [0.0, 0.0, 0.0],
        "mag_calibration_quality": None,
        "mag_orientation_bins": [],
        "mag_orientation_coverage": 0.0,
        "accel_calibration_state": "idle",
        "accel_calibration_message": "",
        "accel_calibration_progress": 0,
        "accel_calibration_quality": None,
        "accel_orientation_bins": [],
        "accel_orientation_coverage": 0.0,
        "tilt_recenter_state": "idle",
        "tilt_recenter_updated_at": 0.0,
        "settings_reload_state": "idle",
        "settings_reload_message": "",
        "settings_reload_updated_at": 0.0,
        "rumble": {},
        "firmware_diagnostics": {},
        "updated_at": updated_at,
    }


class ControllerStatusPublisher:
    """Keep status updates cheap for input threads and publish atomically."""

    def __init__(self, path=STATUS_PATH, clock=time.time):
        self.path = Path(path)
        self._clock = clock
        self._state_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._status = initial_controller_status(self._clock())

    def stage(self, **changes):
        """Update in memory without performing disk I/O on the calling thread."""
        with self._state_lock:
            self._status.update(changes)
            self._status["updated_at"] = self._clock()

    def publish(self, **changes):
        """Publish a complete snapshot via an atomic same-directory replace."""
        with self._write_lock:
            with self._state_lock:
                self._status.update(changes)
                self._status["updated_at"] = self._clock()
                serialized = json.dumps(self._status, ensure_ascii=False)

            temp_path = self.path.with_suffix(".json.tmp")
            try:
                temp_path.write_text(serialized, encoding="utf-8")
                temp_path.replace(self.path)
            except OSError:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return False
        return True

    def snapshot(self):
        """Return an isolated copy for diagnostics and tests."""
        with self._state_lock:
            return copy.deepcopy(self._status)
