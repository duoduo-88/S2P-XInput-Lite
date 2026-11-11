import math
import unittest
from unittest.mock import patch


class AimViewportTests(unittest.TestCase):
    def test_4k_background_uses_a_separate_slower_repaint_budget(self):
        from gamepad_aim_model import room_redraw_interval

        self.assertAlmostEqual(room_redraw_interval(1920, 1080), 1 / 90)
        self.assertAlmostEqual(room_redraw_interval(2560, 1440), 1 / 35)
        self.assertAlmostEqual(room_redraw_interval(3840, 2160), 1 / 18)

from gamepad_aim_model import (
    FLOOR_TILE_COLORS, FLOOR_Y, GRID_STEP, ROOM_TILES, WALL_TILE_COLORS, CursorTest, ShootingTest, TrackingTest,
)


class AimSpreadTests(unittest.TestCase):
    def test_3d_spread_widens_placement_without_changing_depth(self):
        normal = ShootingTest(seed=321)
        wider = ShootingTest(seed=321, horizontal_spread=180, vertical_spread=180)
        for drill in (normal, wider):
            drill.start()
        x, y, z = normal.target_position()
        wide_x, wide_y, wide_z = wider.target_position()
        self.assertGreater(abs(wide_x), abs(x))
        self.assertGreater(abs(wide_y), abs(y))
        self.assertEqual(wide_z, z)


class TimedShootingTests(unittest.TestCase):
    @staticmethod
    def aim_at(drill, position):
        x, y, z = position
        drill.yaw = math.atan2(x, z)
        drill.pitch = math.atan2(y, math.hypot(x, z))

    def test_seed_replays_spawn_times_and_random_positions_at_different_frame_rates(self):
        from gamepad_aim_model import TimedShootingTest

        drill = TimedShootingTest(seconds=12, seed=0)

        def replay(drill, parts):
            drill.start()
            seen = {}
            for sample in range(12 * 32):
                for position in drill.target_positions():
                    seen.setdefault(position, sample)
                    self.aim_at(drill, position)
                    self.assertEqual(drill.shoot(), position)
                for _ in range(parts):
                    drill.advance_time(1 / (32 * parts))
            self.assertGreater(len(seen), 3)
            return seen

        schedule = replay(drill, 1)
        self.assertEqual(schedule, replay(drill, 4))
        other = replay(TimedShootingTest(seconds=12, seed=7), 1)
        self.assertNotEqual(list(schedule.values()), list(other.values()))
        self.assertNotEqual(list(schedule), list(other))

    def test_two_targets_stay_until_hit_and_a_hit_refills_its_slot(self):
        from gamepad_aim_model import TimedShootingTest

        drill = TimedShootingTest(seconds=30, seed=0)
        drill.start()
        self.assertEqual(len(drill.target_positions()), 2)
        drill.advance_time(0.5)
        live = drill.target_positions()
        self.assertEqual(len(live), 2)
        drill.advance_time(10)
        self.assertEqual(drill.target_positions(), live)
        self.aim_at(drill, live[1])
        self.assertEqual(drill.shoot(), live[1])
        remaining = live[:1] + live[2:]
        self.assertEqual(drill.target_positions(), remaining)
        drill.advance_time(0.15)
        self.assertEqual(len(drill.target_positions()), 2)
        self.assertEqual(drill.target_positions()[:-1], remaining)
        self.assertNotIn(drill.target_positions()[-1], live)

    def test_one_second_drill_runs_to_deadline_after_every_visible_target_is_hit(self):
        from gamepad_aim_model import TimedShootingTest

        drill = TimedShootingTest(seconds=1, seed=8)
        drill.start()
        self.assertEqual(drill.duration, 1)
        drill.advance_time(0.5)
        for position in drill.target_positions():
            self.aim_at(drill, position)
            self.assertEqual(drill.shoot(), position)
        self.assertEqual(drill.target_positions(), ())
        self.assertFalse(drill.completed)
        self.assertTrue(drill.running)
        drill.advance_time(2)
        self.assertIsNone(drill.shoot())
        self.assertEqual((drill.elapsed, drill.hits, drill.shots, drill.accuracy), (1, 2, 2, 100))
        self.assertFalse(drill.running)
        self.assertTrue(drill.completed)

    def test_empty_shots_count_as_misses_without_diluting_angular_error(self):
        from gamepad_aim_model import TimedShootingTest

        drill = TimedShootingTest(seconds=1)
        drill.start()
        for position in drill.target_positions():
            self.aim_at(drill, position)
            drill.shoot()
        self.assertIsNone(drill.shoot())
        self.assertEqual(drill.shots, 3)
        self.assertAlmostEqual(drill.mean_error, 0)
        drill.advance_time(0.5)
        position = drill.target_positions()[0]
        self.aim_at(drill, position)
        drill.yaw += math.pi / 2
        transform = drill.camera_transform()
        error = min(drill.target_error(transform(point)) for point in drill.target_positions())
        self.assertIsNone(drill.shoot())
        self.assertAlmostEqual(drill.mean_error, error / 3)
        self.assertTrue(drill.has_error_samples)
        self.aim_at(drill, position)
        self.assertEqual(drill.shoot(), position)
        self.assertAlmostEqual(drill.mean_error, error / 4)
        self.assertAlmostEqual(drill.accuracy, 60)

    def test_overlapping_hit_prefers_the_nearer_intersection_in_either_spawn_order(self):
        from gamepad_aim_model import TimedShootingTest

        far = (0, 0, 8)
        near = (0.24, 0, 4)
        for positions in ((far, near), (near, far)):
            with self.subTest(positions=positions):
                drill = TimedShootingTest(seconds=10)
                with patch.object(drill, "_position_stream", return_value=iter(positions)):
                    drill.start()
                self.assertEqual(drill.shoot(), near)
                self.assertAlmostEqual(drill.mean_error, drill.target_error(near))
                self.assertEqual(drill.target_positions(), (far,))
                self.assertEqual(drill.shoot(), far)


class CursorTrackingTests(unittest.TestCase):
    def test_tracks_pixel_error_and_contact_time_until_deadline(self):
        from gamepad_aim_model import CursorTrackingTest

        drill = CursorTrackingTest(seconds=2)
        path = CursorTrackingTest(seconds=3)
        drill.start()
        path.start()
        size = (800, 400, 100)
        path.advance(0.25, *size)
        ((x, y, _radius),) = path.target_positions(*size)
        drill.x, drill.y = x, y
        drill.advance(0.25, *size)
        self.assertTrue(drill.crosshair_on_target(*size))
        path.advance(1.75, *size)
        ((x, y, _radius),) = path.target_positions(*size)
        drill.x, drill.y = x + 40, y
        drill.advance(2, *size)
        self.assertEqual(drill.elapsed, 2)
        self.assertEqual(drill.on_target_percent, 12.5)
        self.assertAlmostEqual(drill.mean_error, 35)
        self.assertFalse(drill.running)
        self.assertTrue(drill.completed)
        self.assertEqual(drill.target_positions(*size), ())

    def test_seeded_paths_repeat_and_keep_the_entire_ball_in_the_playable_area(self):
        from gamepad_aim_model import CursorTrackingTest

        def sample(drill):
            drill.start()
            positions = []
            for _ in range(20):
                ((x, y, radius),) = drill.target_positions(640, 360, top=180)
                self.assertEqual(radius, 36)
                self.assertGreaterEqual(x - radius, 0)
                self.assertLessEqual(x + radius, 640)
                self.assertGreaterEqual(y - radius, 180)
                self.assertLessEqual(y + radius, 360)
                positions.append((x, y))
                drill.advance(0.2, 640, 360, top=180)
            return positions

        drill = CursorTrackingTest(seconds=5, seed=328, target_size=20)
        path = sample(drill)
        self.assertEqual(path, sample(drill))
        self.assertNotEqual(path, sample(CursorTrackingTest(seconds=5, seed=329, target_size=20)))
        self.assertEqual(path[0], (320, 270))
        self.assertGreater(len(set(path)), 15)

    def test_cursor_uses_shared_stick_motion_and_restarts_at_the_center(self):
        from gamepad_aim_model import CursorTrackingTest

        drill = CursorTrackingTest()
        self.assertEqual(drill.target_positions(800, 400), ())
        drill.start()
        self.assertTrue(drill.on_target(400, 200, 800, 400))
        drill.move((0.1, 0.2), 0.5, 800, 400)
        self.assertEqual((drill.x, drill.y), (420, 160))
        self.assertFalse(drill.crosshair_on_target(800, 400))
        drill.running = False
        drill.advance(1, 800, 400)
        self.assertEqual(drill.elapsed, 0)
        self.assertFalse(drill.completed)
        drill.start()
        self.assertTrue(drill.crosshair_on_target(800, 400))


class AimModelTests(unittest.TestCase):
    def test_cursor_completes_after_the_configured_hits_and_restarts(self):
        drill = CursorTest(count=2)
        drill.start()
        first = drill.target(800, 400)
        self.assertEqual(drill.count, 2)
        x, y, _ = first
        drill.click(x + 30, y, 800, 400)
        self.assertEqual(drill.accuracy, 0)
        for _ in range(2):
            x, y, _ = drill.target(800, 400)
            drill.click(x, y, 800, 400)
        self.assertTrue(drill.completed)
        self.assertFalse(drill.running)
        self.assertIsNone(drill.target(800, 400))
        self.assertEqual(drill.accuracy, 100 * 2 / 3)
        drill.start()
        self.assertFalse(drill.completed)
        self.assertEqual(drill.target(800, 400), first)

    def test_cursor_timeout_blocks_later_clicks(self):
        drill = CursorTest(count=20, seconds=2)
        drill.start()
        self.assertEqual(drill.duration, 2)
        x, y, _ = drill.target(800, 400)
        drill.advance_time(1.25)
        self.assertTrue(drill.running)
        drill.advance_time(1.25)
        drill.click(x, y, 800, 400)
        self.assertEqual((drill.elapsed, drill.shots, drill.hits), (2, 0, 0))
        self.assertTrue(drill.completed)

    def test_cursor_contact_uses_fullscreen_top_offset(self):
        drill = CursorTest()
        drill.start()
        self.assertTrue(drill.on_target(256, 220, 800, 400, top=100))
        self.assertFalse(drill.on_target(256, 160, 800, 400, top=100))
        drill.running = False
        self.assertFalse(drill.on_target(256, 220, 800, 400, top=100))
        self.assertFalse(drill.completed)

    def test_seeded_cursor_sequence_repeats_beyond_the_default_count(self):
        def targets(seed):
            drill = CursorTest(count=12, seed=seed)
            drill.start()
            drill.advance_time(90)
            result = []
            while drill.running:
                x, y, _ = drill.target(800, 400)
                result.append((x, y))
                drill.click(x, y, 800, 400)
            return result

        layout = targets(321)
        self.assertEqual(len(set(layout)), 12)
        self.assertEqual(layout, targets(321))
        self.assertNotEqual(layout, targets(322))

    def test_shooting_contact_and_configured_count_share_hit_geometry(self):
        drill = ShootingTest(count=1)
        drill.start()
        self.assertEqual(drill.count, 1)
        self.assertEqual(drill.target_position(), (2, 0, 8))
        self.assertFalse(drill.crosshair_on_target())
        drill.shoot()
        drill.yaw = math.atan2(2, 8)
        self.assertTrue(drill.crosshair_on_target())
        drill.shoot()
        self.assertTrue(drill.completed)
        self.assertEqual((drill.hits, drill.shots, drill.accuracy), (1, 2, 50))
        self.assertFalse(drill.crosshair_on_target())

    def test_shooting_timeout_caps_elapsed_and_blocks_later_shots(self):
        drill = ShootingTest(seconds=2)
        drill.start()
        drill.yaw = math.atan2(2, 8)
        drill.advance((0, 0), 1.75)
        drill.advance((0, 0), 0.5)
        drill.shoot()
        self.assertEqual((drill.duration, drill.elapsed, drill.shots), (2, 2, 0))
        self.assertFalse(drill.running)
        self.assertTrue(drill.completed)

    def test_shooting_depth_broadens_visible_seeded_target_positions(self):
        def targets(depth, seed):
            drill = ShootingTest(count=12, depth=depth, seed=seed)
            drill.start()
            result = []
            while drill.running:
                x, y, z = drill.target_position()
                self.assertGreater(z, 2)
                self.assertLess(abs(x / z), 0.45)
                self.assertLess(abs(y / z), 0.25)
                result.append((x, y, z))
                drill.yaw = math.atan2(x, z)
                drill.pitch = math.atan2(y, math.hypot(x, z))
                drill.shoot()
            return result

        for seed in (0, 321):
            with self.subTest(seed=seed):
                normal = targets(0, seed)
                varied = targets(100, seed)
                self.assertEqual(len(varied), 12)
                self.assertEqual(varied, targets(100, seed))
                self.assertLess(min(point[2] for point in varied), 5)
                self.assertGreater(max(point[2] for point in varied), 16)
                self.assertGreater(max(point[2] for point in varied), max(point[2] for point in normal))
        self.assertNotEqual(targets(100, 321), targets(100, 322))

    def test_maximum_depth_varies_even_in_a_two_hit_fixed_round(self):
        drill = ShootingTest(count=2, depth=100)
        drill.start()
        x, y, z = drill.target_position()
        drill.yaw = math.atan2(x, z)
        drill.pitch = math.atan2(y, math.hypot(x, z))
        drill.shoot()
        self.assertGreater(abs(drill.target_position()[2] - z), 9)

    def test_tracking_duration_caps_weighted_score_at_natural_completion(self):
        drill = TrackingTest(seconds=2)
        drill.start()
        self.assertEqual(drill.duration, 2)
        self.assertTrue(drill.crosshair_on_target())
        drill.record(0.5, 0, True)
        drill.record(2, 40, False)
        drill.record(1, 0, True)
        self.assertEqual(drill.elapsed, 2)
        self.assertEqual(drill.on_target_percent, 25)
        self.assertEqual(drill.mean_error, 30)
        self.assertFalse(drill.running)
        self.assertTrue(drill.completed)

    def test_tracking_seed_repeats_the_path_after_reset(self):
        drill = TrackingTest(seconds=5, seed=321)
        drill.start()
        drill.advance((0, 0), 1)
        position = drill.target_position()
        drill.start()
        drill.advance((0, 0), 1)
        self.assertEqual(drill.target_position(), position)
        other = TrackingTest(seconds=5, seed=322)
        other.start()
        other.advance((0, 0), 1)
        self.assertNotEqual(other.target_position(), position)

    def test_room_keeps_square_tiles_and_an_open_top(self):
        self.assertEqual(len(ROOM_TILES), 288)
        for color, quad in ROOM_TILES:
            self.assertTrue(all(
                math.isclose(math.dist(quad[i], quad[(i + 1) % 4]), GRID_STEP) for i in range(4)
            ))
            xs, ys, zs = map(set, zip(*quad))
            if len(ys) == 1:
                self.assertEqual(ys, {FLOOR_Y})
                self.assertIn(color, FLOOR_TILE_COLORS)
            else:
                self.assertTrue(xs in ({-18}, {18}) or zs in ({-36}, {36}))
                self.assertIn(color, WALL_TILE_COLORS)
        self.assertEqual([color for color, _ in ROOM_TILES[:3]],
                         [FLOOR_TILE_COLORS[0], FLOOR_TILE_COLORS[1], FLOOR_TILE_COLORS[0]])
