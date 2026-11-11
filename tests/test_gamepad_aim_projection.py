import unittest
from unittest.mock import patch

import gamepad_aim_projection as projection


class AimRoomProjectionTests(unittest.TestCase):
    def test_fallback_keeps_lines_inside_the_viewport(self):
        with patch.object(projection, "load_render_library", return_value=None):
            lines = projection.project_room(1920, 1080, 0.7, -0.3)

        self.assertTrue(lines)
        for x0, y0, x1, y1, color in lines:
            self.assertTrue(-1e-9 <= min(x0, x1) <= max(x0, x1) <= 1920 + 1e-9)
            self.assertTrue(-1e-9 <= min(y0, y1) <= max(y0, y1) <= 1080 + 1e-9)
            self.assertIn(color, ("#354c66", "#8096b0"))

    def test_native_projection_matches_the_existing_room_model(self):
        if projection.load_render_library() is None:
            self.skipTest("build the Windows native rendering library first")
        for width, height in ((800, 450), (3840, 2160)):
            for yaw, pitch in ((0, 0), (0.7, -0.3), (3.1, 1.4), (-2.7, -1.45)):
                with self.subTest(size=(width, height), pose=(yaw, pitch)):
                    args = width, height, yaw, pitch
                    native = projection.project_room(*args)
                    with patch.object(projection, "load_render_library", return_value=None):
                        reference = projection.project_room(*args)
                    self.assertEqual(len(native), len(reference))
                    for actual, expected in zip(native, reference):
                        self.assertEqual(actual[4], expected[4])
                        for value, reference_value in zip(actual[:4], expected[:4]):
                            self.assertAlmostEqual(value, reference_value, places=8)
