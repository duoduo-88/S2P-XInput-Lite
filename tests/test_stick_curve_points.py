import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config_gui import _curve_point_x_bounds


class StickCurvePointTests(unittest.TestCase):
    def test_coordinate_editor_uses_neighbor_aware_x_bounds(self):
        points = (0.10, 0.25, 0.50, 0.75, 0.90)
        self.assertEqual(_curve_point_x_bounds(points, 0), (0.0, 0.24))
        self.assertEqual(_curve_point_x_bounds(points, 2), (0.26, 0.74))
        self.assertEqual(_curve_point_x_bounds(points, 4), (0.76, 1.0))

    def test_coordinate_bounds_reject_an_invalid_point_index(self):
        with self.assertRaises(IndexError):
            _curve_point_x_bounds((0.25, 0.50, 0.75), 3)


if __name__ == "__main__":
    unittest.main()
