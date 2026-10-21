import unittest

from gyro_processing import _apply_gyro_stick_anti_deadzone


class GyroProcessingTests(unittest.TestCase):
    def test_anti_deadzone_does_not_amplify_smoothing_tail(self):
        self.assertEqual(
            _apply_gyro_stick_anti_deadzone(9e-6, -2e-6, 0.12),
            (0.0, 0.0),
        )

    def test_anti_deadzone_still_maps_intentional_motion(self):
        x, y = _apply_gyro_stick_anti_deadzone(0.01, 0.0, 0.12)
        self.assertAlmostEqual(x, 0.1288)
        self.assertEqual(y, 0.0)


if __name__ == "__main__":
    unittest.main()
