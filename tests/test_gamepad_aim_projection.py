import ctypes
import unittest
from unittest.mock import patch

import gamepad_aim_projection as projection
from gamepad_aim_model import FLOOR_TILE_COLORS, WALL_TILE_COLORS


class AimRoomProjectionTests(unittest.TestCase):
    def test_room_projects_filled_color_batches(self):
        with patch.object(projection, "load_render_library", return_value=None):
            batches = projection.project_room(1920, 1080, 0.7, -0.3)

        self.assertEqual({color for color, _, _ in batches}, {*FLOOR_TILE_COLORS, *WALL_TILE_COLORS})
        for _, vertices, count in batches:
            self.assertIsInstance(vertices, ctypes.Array)
            self.assertGreater(count, 0)
            for x, y in zip(vertices[:count * 6:2], vertices[1:count * 6:2]):
                self.assertTrue(0 <= x <= 1920)
                self.assertTrue(0 <= y <= 1080)

    def test_tile_coverage_has_no_gaps_at_wall_and_floor_seams(self):
        def area(batches):
            total = 0.0
            for _, vertices, count in batches:
                for i in range(count):
                    x0, y0, x1, y1, x2, y2 = vertices[i * 6:i * 6 + 6]
                    total += abs((x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)) / 2
            return total

        for yaw in (0, 0.7, -2.7):
            with self.subTest(yaw=yaw):
                self.assertAlmostEqual(area(projection.project_room(1920, 1080, yaw, 0)), 1920 * 1080, delta=0.5)
        self.assertLess(area(projection.project_room(1920, 1080, 0, 1.45)), 1920 * 1080 * 0.8)

    def test_native_projection_matches_the_python_fallback(self):
        library = projection.load_render_library()
        if library is None or projection._native_projector(library) is None:
            self.skipTest("build the Windows native rendering library first")
        for width, height in ((800, 450), (3840, 2160)):
            for yaw, pitch in ((0, 0), (0.7, -0.3), (3.1, 1.4), (-2.7, -1.45)):
                with self.subTest(size=(width, height), pose=(yaw, pitch)):
                    args = width, height, yaw, pitch
                    native = projection.project_room(*args)
                    with patch.object(projection, "load_render_library", return_value=None):
                        reference = projection.project_room(*args)
                    self.assertEqual([(c, n) for c, _, n in native], [(c, n) for c, _, n in reference])
                    for (_, actual, count), (_, expected, _) in zip(native, reference):
                        for a, b in zip(actual[:count * 6], expected[:count * 6]):
                            self.assertAlmostEqual(a, b, delta=0.001)
