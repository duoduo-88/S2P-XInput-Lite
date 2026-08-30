import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import imufusion
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import xinput_controller as xinput_module
from xinput_controller import XInputController


def _new_controller():
    controller = XInputController.__new__(XInputController)
    controller._ahrs = imufusion.Ahrs()
    controller._ahrs.settings = imufusion.Settings(
        imufusion.CONVENTION_NWU, 0.1, 2000.0, 10.0, 20.0, 625
    )
    controller._mag_field_reference = 1.0
    controller._mag_field_valid = True
    controller._fusion_gyro = np.empty(3, dtype=np.float64)
    controller._fusion_accel = np.empty(3, dtype=np.float64)
    controller._fusion_mag = np.empty(3, dtype=np.float64)
    controller._gyro_bias = [0.0, 0.0, 0.0]
    controller._impact_accel_reject_until = 0.0
    controller._impact_accel_recover_until = 0.0
    controller.gyro_accel_suppression = 0.0
    controller._nine_axis_orientation = None
    controller._nine_axis_quaternion = None
    controller._nine_axis_has_magnetometer = False
    controller._mag_last_valid_time = None
    controller._mag_recovery_started = None
    controller._mag_recovery_accumulator = 0.0
    controller._mag_bias = [0.0, 0.0, 0.0]
    controller._mag_scale = [1.0, 1.0, 1.0]
    controller._mag_matrix = None
    controller._reset_adaptive_magnetic_reference()
    return controller


def _corrected_mag_for_heading(heading):
    radians = math.radians(heading)
    # _magnetic_heading_from_corrected maps report (m0, m1, m2) to
    # controller (m0, m2, m1), matching the runtime fusion path.
    return (math.cos(radians), 0.0, -math.sin(radians))


class AdaptiveMagneticReferenceTests(unittest.TestCase):
    @staticmethod
    def _update(controller, now, magnetometer):
        with patch.object(xinput_module.time, "perf_counter", return_value=now):
            return controller._update_nine_axis_orientation(
                (0.0, 0.0, 0.0), (0.0, 0.0, 16384.0), magnetometer, 0.01
            )

    def test_normal_reference_does_not_create_a_transform(self):
        controller = _new_controller()
        self.assertEqual(controller._mag_reference_state, "NORMAL")
        self.assertFalse(controller._mag_heading_transform_valid)

    def test_stable_candidate_reanchors_to_current_gyro_heading(self):
        controller = _new_controller()
        accelerometer = (0.0, 0.0, 1.0)
        gyroscope = np.array((0.0, 0.0, 90.0))
        for _ in range(100):
            controller._ahrs.update_no_magnetometer(
                gyroscope, np.array(accelerometer), 0.008
            )
        gyro_heading = controller._current_fusion_heading()
        self.assertIsNotNone(gyro_heading)
        candidate = _corrected_mag_for_heading(40.0)
        promoted = False
        for sample in range(100):
            promoted = controller._update_magnetic_reference_candidate(
                candidate, accelerometer, 0.0, sample * 0.01
            )
            if promoted:
                break
        self.assertTrue(promoted)
        self.assertEqual(controller._mag_reference_state, "RECOVERING")
        self.assertTrue(controller._mag_heading_transform_valid)
        self.assertAlmostEqual(
            controller._mag_heading_offset_degrees,
            controller._wrap_degrees(gyro_heading - 40.0),
            places=5,
        )

        fusion_mag = np.array((candidate[0], candidate[2], candidate[1]))
        self.assertTrue(
            controller._rotate_vector_around_axis(
                fusion_mag,
                (0.0, 0.0, 1.0),
                -controller._mag_heading_offset_degrees,
                fusion_mag,
            )
        )
        transformed = (fusion_mag[0], fusion_mag[2], fusion_mag[1])
        aligned_heading = controller._magnetic_heading_from_corrected(
            accelerometer, transformed
        )
        self.assertAlmostEqual(
            controller._wrap_degrees(aligned_heading - gyro_heading), 0.0,
            places=5,
        )

    def test_candidate_interruption_restarts_without_promoting(self):
        controller = _new_controller()
        accelerometer = (0.0, 0.0, 1.0)
        for sample in range(20):
            controller._update_magnetic_reference_candidate(
                _corrected_mag_for_heading(20.0), accelerometer, 0.0,
                sample * 0.01,
            )
        self.assertEqual(controller._mag_reference_state, "CANDIDATE")
        controller._update_magnetic_reference_candidate(
            _corrected_mag_for_heading(45.0), accelerometer, 0.0, 0.21
        )
        self.assertEqual(controller._mag_candidate_samples, 1)
        self.assertFalse(controller._mag_heading_transform_valid)

    def test_fast_rotation_cannot_promote_a_candidate(self):
        controller = _new_controller()
        for sample in range(100):
            promoted = controller._update_magnetic_reference_candidate(
                _corrected_mag_for_heading(20.0), (0.0, 0.0, 1.0),
                controller.MAG_REFERENCE_MAX_ANGULAR_SPEED_DPS + 1.0,
                sample * 0.01,
            )
            self.assertFalse(promoted)
        self.assertEqual(controller._mag_candidate_samples, 0)
        self.assertFalse(controller._mag_heading_transform_valid)

    def test_short_interference_keeps_the_original_reference(self):
        controller = _new_controller()
        normal = _corrected_mag_for_heading(0.0)
        for sample in range(100):
            self._update(controller, sample * 0.01, normal)
        for sample in range(30):
            self._update(controller, 1.0 + sample * 0.01, (0.0, 0.0, 0.0))
        for sample in range(100):
            self._update(controller, 1.3 + sample * 0.01, normal)
        self.assertEqual(controller._mag_reference_state, "NORMAL")
        self.assertFalse(controller._mag_heading_transform_valid)
        self.assertEqual(controller._mag_candidate_samples, 0)

    def test_timeout_candidate_recovery_reanchors_without_heading_jump(self):
        controller = _new_controller()
        normal = _corrected_mag_for_heading(0.0)
        for sample in range(100):
            self._update(controller, sample * 0.01, normal)
        heading_before_reject = controller._current_fusion_heading()

        disturbed = (0.0, 0.0, 0.0)
        for sample in range(60):
            self._update(controller, 1.0 + sample * 0.01, disturbed)
        self.assertEqual(controller._mag_reference_state, "REJECTED")
        self.assertFalse(controller._nine_axis_has_magnetometer)

        replacement = tuple(
            value * 10.0 for value in _corrected_mag_for_heading(40.0)
        )
        for sample in range(100):
            self._update(controller, 1.6 + sample * 0.01, replacement)
            if controller._mag_reference_state == "RECOVERING":
                break
        self.assertEqual(controller._mag_reference_state, "RECOVERING")
        self.assertTrue(controller._mag_heading_transform_valid)
        for sample in range(80):
            self._update(controller, 2.6 + sample * 0.01, replacement)
        heading_after_recovery = controller._current_fusion_heading()
        self.assertAlmostEqual(
            controller._wrap_degrees(heading_after_recovery - heading_before_reject),
            0.0,
            places=2,
        )


if __name__ == "__main__":
    unittest.main()
