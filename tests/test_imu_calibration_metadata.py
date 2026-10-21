import math
import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from imu_calibration import fit_magnetometer_ellipsoid


def _samples(octants=range(8), per_octant=90):
    rng = np.random.default_rng(0x710)
    values = []
    for octant in octants:
        signs = np.array((
            1.0 if octant & 1 else -1.0,
            1.0 if octant & 2 else -1.0,
            1.0 if octant & 4 else -1.0,
        ))
        direction = rng.normal(size=(per_octant, 3))
        direction = np.abs(direction) * signs
        direction /= np.linalg.norm(direction, axis=1)[:, None]
        values.extend(direction)
    values = np.asarray(values)
    # A non-trivial but well-conditioned raw LSB ellipsoid.
    transform = np.diag((420.0, 360.0, 300.0))
    return values @ transform.T + np.array((140.0, -75.0, 32.0))


class ImuCalibrationMetadataTests(unittest.TestCase):
    def test_full_ellipsoid_reports_all_octants_and_stable_reference(self):
        samples = _samples()
        _bias, _matrix, quality = fit_magnetometer_ellipsoid(samples)
        self.assertEqual(quality["octant_count"], 8)
        self.assertEqual(quality["octant_mask"], 0xFF)
        self.assertTrue(math.isfinite(quality["reference_magnitude_lsb"]))
        self.assertGreater(quality["reference_magnitude_lsb"], 250.0)
        repeated = fit_magnetometer_ellipsoid(samples)[2]
        self.assertEqual(
            quality["reference_magnitude_lsb"], repeated["reference_magnitude_lsb"]
        )

    def test_missing_direction_reduces_octant_coverage(self):
        samples = _samples(octants=range(1, 8))
        _bias, _matrix, quality = fit_magnetometer_ellipsoid(samples)
        self.assertLess(quality["octant_count"], 8)

    def test_rejected_outlier_does_not_change_full_octant_coverage(self):
        samples = _samples()
        outlier = np.array([[-1000.0, -1000.0, -1000.0]])
        _bias, _matrix, quality = fit_magnetometer_ellipsoid(
            np.vstack((samples, outlier))
        )
        self.assertEqual(quality["octant_count"], 8)
        self.assertGreater(quality["rejected_count"], 0)


if __name__ == "__main__":
    unittest.main()
