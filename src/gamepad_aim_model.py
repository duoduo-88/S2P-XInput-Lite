"""Aim drill state, scoring, and 3D geometry without UI dependencies."""

from __future__ import annotations

import heapq
import math
import random
from functools import cached_property
from itertools import islice, pairwise


ROUND_SECONDS = 30.0
MAX_TIMED_TARGETS = 2
MIN_REFILL_SECONDS = 0.06
MAX_REFILL_SECONDS = 0.14
TIMED_MODES = frozenset(("timed-shooting", "timed-cursor", "timed-cursor-stick", "timed-cursor-mouse"))
TRACKING_MODES = frozenset(("tracking", "cursor-tracking"))
TARGET_RADIUS = 0.36
NEAR_PLANE = 0.2
FOCAL_SCALE = 0.5 / math.tan(math.radians(30))
CURSOR_TARGETS = (
    (0.32, 0.40), (0.72, 0.30), (0.63, 0.72), (0.26, 0.66),
    (0.46, 0.22), (0.78, 0.55), (0.40, 0.78), (0.22, 0.25),
    (0.66, 0.43), (0.49, 0.57),
)
SHOOT_TARGETS = (
    (2, 0, 8), (-2.4, 0.8, 8), (0.5, -0.6, 6),
    (3, 0.9, 8.5), (-2, -0.5, 7), (0, 1.3, 7.5),
    (1.5, -0.7, 6.5), (-3.1, 0.35, 8), (2.7, 0.3, 9),
    (0.25, -0.8, 7),
)
SHOOT_WIDE_DEPTHS = (3.5, 19.5, 2.8, 17, 6.5, 21, 4.5, 14, 23, 10)
FLOOR_Y = -1.55
GRID_STEP = 6
GRID_X = tuple(GRID_STEP * i for i in range(-3, 4))
GRID_Z = tuple(GRID_STEP * i for i in range(-6, 7))
WALL_LEVELS = tuple(FLOOR_Y + GRID_STEP * layer for layer in range(1, 7))
ROOM_BACKGROUND = "#101825"
FLOOR_TILE_COLORS = ("#111b27", "#162332")
WALL_TILE_COLORS = ("#1b2632", "#232f3d")
SIDE_WALL_TILE_COLORS = ("#1d2834", "#25313f")


def _checkerboard(columns, rows, colors, point, *, phase=0):
    for row, (v0, v1) in enumerate(pairwise(rows)):
        for column, (u0, u1) in enumerate(pairwise(columns)):
            color = colors[(row + column + phase) % 2]
            yield color, (point(u0, v0), point(u1, v0), point(u1, v1), point(u0, v1))


_WALL_ROWS = FLOOR_Y, *WALL_LEVELS
# Start with a light tile to the right at Z=0, and carry the phase around the room.
# The floor and wall palettes distinguish their tiles where they meet.
ROOM_TILES = (
    *(_checkerboard(GRID_X, GRID_Z, FLOOR_TILE_COLORS, lambda x, z: (x, FLOOR_Y, z), phase=1)),
    *(_checkerboard(GRID_Z, _WALL_ROWS, SIDE_WALL_TILE_COLORS, lambda z, y: (-18, y, z))),
    *(_checkerboard(GRID_Z, _WALL_ROWS, SIDE_WALL_TILE_COLORS, lambda z, y: (18, y, z), phase=1)),
    *(_checkerboard(GRID_X, _WALL_ROWS, WALL_TILE_COLORS, lambda x, y: (x, y, -36), phase=1)),
    *(_checkerboard(GRID_X, _WALL_ROWS, WALL_TILE_COLORS, lambda x, y: (x, y, 36))),
)


class _TargetRound:
    def __init__(self, count, seconds):
        self.count = max(1, count)
        self.duration = seconds
        self._reset_score()

    def _reset_score(self):
        self.running = self.completed = False
        self.hits = self.shots = 0
        self.elapsed = self.error_sum = 0.0

    def start(self):
        self._reset_score()
        self.running = True

    def advance_time(self, seconds):
        if self.running:
            self.elapsed += seconds
            if self.duration and self.elapsed >= self.duration:
                self.elapsed = self.duration
                self._finish()

    def _record_shot(self, error, hit):
        self.shots += 1
        self.error_sum += error
        if hit:
            self.hits += 1
            if self.hits >= self.count:
                self._finish()

    def _finish(self):
        self.running = False
        self.completed = True

    @property
    def accuracy(self):
        return 100 * self.hits / self.shots if self.shots else 0.0

    @property
    def mean_error(self):
        return self.error_sum / self.shots if self.shots else 0.0

    @property
    def has_error_samples(self):
        return self.shots > 0


class _CursorGeometry:
    def _cursor_position(self, width, height):
        return (width / 2, height / 2) if self.x is None else (self.x, self.y)

    def move(self, axes, seconds, width, height):
        x, y = self._cursor_position(width, height)
        # Full stick travels one viewport height per second.
        self.x = max(0, min(width - 1, x + axes[0] * height * seconds))
        self.y = max(0, min(height - 1, y - axes[1] * height * seconds))

    def _project_target(self, position, width, height, top):
        x, y = position
        return x * width, top + y * (height - top), min(width, height) * self.target_size / 200

    @staticmethod
    def _cursor_error(x, y, bounds):
        target_x, target_y, radius = bounds
        error = math.hypot(x - target_x, y - target_y)
        return error, error <= radius


class CursorTest(_TargetRound, _CursorGeometry):
    def __init__(self, count=10, seconds=0, seed=0, *, target_size=7):
        super().__init__(count, seconds)
        self.x = self.y = None
        self.target_size = target_size
        self._targets = CURSOR_TARGETS
        if seed:
            rng = random.Random(seed)
            self._targets = tuple(
                (rng.uniform(0.15, 0.85), rng.uniform(0.17, 0.83)) for _ in range(self.count)
            )

    def start(self):
        super().start()
        self.x = self.y = None

    def target(self, width, height, top=0):
        if self.running:
            return self._project_target(self._targets[self.hits % len(self._targets)], width, height, top)
        return None

    def target_positions(self, width, height, top=0):
        target = self.target(width, height, top)
        return () if target is None else (target,)

    def _target_contact(self, x, y, width, height, top):
        target = self.target(width, height, top)
        if target is None or not (0 <= x < width and 0 <= y < height):
            return None
        return self._cursor_error(x, y, target)

    def on_target(self, x, y, width, height, top=0):
        contact = self._target_contact(x, y, width, height, top)
        return contact is not None and contact[1]

    def click(self, x, y, width, height, top=0):
        contact = self._target_contact(x, y, width, height, top)
        if contact is None:
            return None
        impact = self.target(width, height, top) if contact[1] else None
        self._record_shot(*contact)
        return impact


class AimCamera:
    def __init__(self):
        self.yaw = self.pitch = 0.0

    def turn(self, axes, seconds):
        self.look(axes[0] * math.radians(140) * seconds, axes[1] * math.radians(115) * seconds)

    def look(self, yaw_delta, pitch_delta):
        self.yaw = math.remainder(self.yaw + yaw_delta, math.tau)
        limit = math.radians(85)
        self.pitch = max(-limit, min(limit, self.pitch + pitch_delta))

    def camera_transform(self):
        cos_yaw, sin_yaw = math.cos(self.yaw), math.sin(self.yaw)
        cos_pitch, sin_pitch = math.cos(self.pitch), math.sin(self.pitch)

        def transform(point):
            x, y, z = point
            forward = x * sin_yaw + z * cos_yaw
            return (
                x * cos_yaw - z * sin_yaw,
                y * cos_pitch - forward * sin_pitch,
                y * sin_pitch + forward * cos_pitch,
            )

        return transform

    def target_positions(self):
        target = self.target_position()
        return () if target is None else (target,)

    @staticmethod
    def target_error(target):
        x, y, z = target
        return math.degrees(math.atan2(math.hypot(x, y), z))

    @staticmethod
    def on_target(target):
        x, y, z = target
        return z > 0 and x * x + y * y <= TARGET_RADIUS * TARGET_RADIUS

    @staticmethod
    def crosshair_depth(target):
        """Depth of the first sphere surface hit by the +Z crosshair ray."""
        x, y, z = target
        radius_squared = TARGET_RADIUS * TARGET_RADIUS - (x * x + y * y)
        return z - math.sqrt(radius_squared) if z > 0 and radius_squared >= 0 else None


def _distance_range(positions):
    distances = [math.hypot(*position) for position in positions]
    return min(distances), max(distances)


def _seed_tracking_path(path, seed):
    if not seed:
        return path
    rng = random.Random(seed)
    return tuple(
        (amplitude * rng.choice((-1, 1)), rate * rng.uniform(0.7, 1.3))
        for amplitude, rate in path
    )


class _TrackingRound:
    def __init__(self, seconds):
        self.duration = seconds
        self.running = self.completed = False
        self.elapsed = self.on_target_time = self.error_integral = 0.0

    def start(self):
        self.elapsed = self.on_target_time = self.error_integral = 0.0
        self.completed = False
        self.running = True

    def record(self, seconds, error, on_target):
        if not self.running:
            return
        seconds = min(seconds, self.duration - self.elapsed)
        self.elapsed += seconds
        self.error_integral += error * seconds
        if on_target:
            self.on_target_time += seconds
        if self.elapsed >= self.duration:
            self.running = False
            self.completed = True

    @property
    def on_target_percent(self):
        return 100 * self.on_target_time / self.elapsed if self.elapsed else 0.0

    @property
    def mean_error(self):
        return self.error_integral / self.elapsed if self.elapsed else 0.0


class TrackingTest(_TrackingRound, AimCamera):
    def __init__(self, seconds=ROUND_SECONDS, seed=0):
        super().__init__(seconds)
        AimCamera.__init__(self)
        self._path = _seed_tracking_path(((3.6, 0.92), (0.65, 2.1), (0.95, 1.35), (1.6, 0.52)), seed)

    def start(self):
        self.yaw = self.pitch = 0.0
        super().start()

    def advance(self, axes, seconds):
        if self.running:
            seconds = min(seconds, self.duration - self.elapsed)
        self.turn(axes, seconds)
        if self.running:
            target = self.camera_transform()(self.target(self.elapsed + seconds))
            error = self.target_error(target)
            self.record(seconds, error, self.on_target(target))

    def target(self, seconds):
        wide, small, vertical, depth = (
            amplitude * math.sin(seconds * rate) for amplitude, rate in self._path
        )
        return wide + small, vertical, 7.4 + depth

    @cached_property
    def target_distance_range(self):
        steps = max(1, math.ceil(self.duration * 30))
        return _distance_range(self.target(self.duration * step / steps) for step in range(steps + 1))

    def target_position(self):
        return self.target(self.elapsed)

    def crosshair_on_target(self):
        return self.running and self.on_target(self.camera_transform()(self.target_position()))


class CursorTrackingTest(_TrackingRound, _CursorGeometry):
    def __init__(self, seconds=ROUND_SECONDS, seed=0, *, target_size=7):
        super().__init__(seconds)
        self.x = self.y = None
        self.target_size = target_size
        self._path = _seed_tracking_path(((0.21, 0.78), (0.07, 1.45), (0.16, 0.94), (0.07, 1.8)), seed)

    def start(self):
        self.x = self.y = None
        super().start()

    def _target_at(self, seconds, width, height, top):
        x_slow, x_fast, y_slow, y_fast = (
            amplitude * math.sin(seconds * rate) for amplitude, rate in self._path
        )
        position = (0.5 + x_slow + x_fast, 0.5 + y_slow + y_fast)
        x, y, radius = self._project_target(position, width, height, top)
        radius = min(radius, (height - top) / 2)
        return x, max(top + radius, min(height - radius, y)), radius

    def target_positions(self, width, height, top=0):
        return (self._target_at(self.elapsed, width, height, top),) if self.running else ()

    def on_target(self, x, y, width, height, top=0):
        if not self.running:
            return False
        return self._cursor_error(x, y, self._target_at(self.elapsed, width, height, top))[1]

    def crosshair_on_target(self, width, height, top=0):
        return self.on_target(*self._cursor_position(width, height), width, height, top)

    def advance(self, seconds, width, height, top=0):
        if not self.running:
            return
        seconds = min(seconds, self.duration - self.elapsed)
        bounds = self._target_at(self.elapsed + seconds, width, height, top)
        error, on_target = self._cursor_error(*self._cursor_position(width, height), bounds)
        self.record(seconds, error, on_target)


def _shoot_positions(count, seed, depth, horizontal_spread, vertical_spread):
    positions = SHOOT_TARGETS
    if seed:
        rng = random.Random(seed)
        positions = []
        for index in range(count):
            # Alternate depth bands so even a short seeded drill varies in depth.
            near = 6 if index % 2 == 0 else 8
            z = rng.uniform(near, near + 1)
            positions.append((rng.uniform(-0.37, 0.37) * z, rng.uniform(-0.95, 1.3), z))
    if not depth and horizontal_spread == vertical_spread == 100:
        return positions

    expanded_depths = SHOOT_WIDE_DEPTHS
    if seed:
        # Expand the original 6–9 depth band to 2.8–23 at full variation.
        expanded_depths = (2.8 + (z - 6) / 3 * (23 - 2.8) for _, _, z in positions)
    varied = []
    amount = depth / 100
    for (x, y, z), expanded in zip(positions, expanded_depths):
        new_z = z + (expanded - z) * amount
        scale = new_z / z
        # Keep the projected direction and clear the floor at longer distances.
        varied.append((
            x * scale * horizontal_spread / 100,
            max(FLOOR_Y + TARGET_RADIUS, y * scale * vertical_spread / 100),
            new_z,
        ))
    return varied


class ShootingTest(_TargetRound, AimCamera):
    def __init__(self, count=10, seconds=0, seed=0, depth=0, *,
                 horizontal_spread=100, vertical_spread=100):
        super().__init__(count, seconds)
        AimCamera.__init__(self)
        self._targets = _shoot_positions(self.count, seed, depth, horizontal_spread, vertical_spread)

    def start(self):
        super().start()
        self.yaw = self.pitch = 0.0

    def advance(self, axes, seconds):
        if self.running and self.duration:
            seconds = min(seconds, self.duration - self.elapsed)
        self.turn(axes, seconds)
        self.advance_time(seconds)

    def target_position(self):
        if self.running:
            return self._targets[self.hits % len(self._targets)]
        return None

    @cached_property
    def target_distance_range(self):
        return _distance_range(self._targets[:self.count])

    def crosshair_on_target(self):
        return self.running and self.on_target(self.camera_transform()(self.target_position()))

    def shoot(self):
        position = self.target_position()
        if position is None:
            return None
        target = self.camera_transform()(position)
        hit = self.on_target(target)
        self._record_shot(self.target_error(target), hit)
        return position if hit else None


class _TimedTargetRound(_TargetRound):
    def __init__(self, seconds, seed):
        super().__init__(MAX_TIMED_TARGETS, max(1, seconds))
        self.seed = seed

    def _reset_score(self):
        super()._reset_score()
        self._error_samples = 0

    def start(self):
        super().start()
        self._timing = random.Random(self.seed)
        self._pending = [0.0] * MAX_TIMED_TARGETS
        self._live = []
        self._positions = self._position_stream()
        self.advance_time(0)

    def advance_time(self, seconds):
        super().advance_time(seconds)
        if not self.running:
            return
        while self._pending and self._pending[0] <= self.elapsed:
            heapq.heappop(self._pending)
            self._live.append(next(self._positions))

    def _shoot_at(self, contact):
        if not self.running:
            return None
        self.shots += 1
        if not self._live:
            return None
        missed, _, error, index = min(
            (hit_order is None, error if hit_order is None else hit_order, error, index)
            for index, (error, hit_order) in enumerate(map(contact, self._live))
        )
        self.error_sum += error
        self._error_samples += 1
        if missed:
            return None
        self.hits += 1
        refill = self._timing.uniform(MIN_REFILL_SECONDS, MAX_REFILL_SECONDS)
        heapq.heappush(self._pending, self.elapsed + refill)
        return self._live.pop(index)

    @property
    def has_error_samples(self):
        return self._error_samples > 0

    @property
    def mean_error(self):
        return self.error_sum / self._error_samples if self.has_error_samples else 0.0


class TimedShootingTest(_TimedTargetRound, AimCamera):
    def __init__(self, seconds=30, seed=0, depth=0, *, horizontal_spread=100, vertical_spread=100):
        super().__init__(seconds, seed)
        AimCamera.__init__(self)
        self._placement = depth, horizontal_spread, vertical_spread

    def _position_stream(self):
        rng = random.Random(self.seed)
        while True:
            yield from _shoot_positions(32, rng.randrange(1, 2**32), *self._placement)

    def start(self):
        self.yaw = self.pitch = 0.0
        super().start()

    def target_positions(self):
        return tuple(self._live) if self.running else ()

    @cached_property
    def target_distance_range(self):
        # Hitting each ball as soon as it spawns gives the earliest refill schedule.
        timing = random.Random(self.seed)
        ready = [0.0] * MAX_TIMED_TARGETS
        count = 0
        while ready[0] < self.duration:
            count += 1
            refill = timing.uniform(MIN_REFILL_SECONDS, MAX_REFILL_SECONDS)
            heapq.heapreplace(ready, ready[0] + refill)
        return _distance_range(islice(self._position_stream(), count))

    def shoot(self):
        transform = self.camera_transform()

        def contact(position):
            target = transform(position)
            return self.target_error(target), self.crosshair_depth(target)

        return self._shoot_at(contact)


class TimedCursorTest(_TimedTargetRound, _CursorGeometry):
    def __init__(self, seconds=30, seed=0, *, target_size=7):
        super().__init__(seconds, seed)
        self.x = self.y = None
        self.target_size = target_size

    def _position_stream(self):
        rng = random.Random(self.seed)
        while True:
            yield rng.uniform(0.15, 0.85), rng.uniform(0.17, 0.83)

    def start(self):
        self.x = self.y = None
        super().start()

    def target_positions(self, width, height, top=0):
        return tuple(self._project_target(point, width, height, top) for point in self._live) if self.running else ()

    def on_target(self, x, y, width, height, top=0):
        return any(self._cursor_error(x, y, bounds)[1] for bounds in self.target_positions(width, height, top))

    def click(self, x, y, width, height, top=0):
        if not (0 <= x < width and 0 <= y < height):
            return None

        def contact(position):
            error, hit = self._cursor_error(x, y, self._project_target(position, width, height, top))
            return error, error if hit else None

        point = self._shoot_at(contact)
        return self._project_target(point, width, height, top) if point is not None else None


def project_ball(target, width, height):
    """Project a camera-space ball and discard targets outside the viewport."""
    x, y, z = target
    if z <= TARGET_RADIUS:
        return None
    scale = height * FOCAL_SCALE / z
    x, y, radius = width / 2 + scale * x, height / 2 - scale * y, scale * TARGET_RADIUS
    if x + radius < 0 or x - radius > width or y + radius < 0 or y - radius > height:
        return None
    return x, y, radius


def room_redraw_interval(width, height):
    """Limit full-area grid repaints independently of target and input updates."""
    pixels = width * height
    scale = max(1.0, pixels / (1920 * 1080))
    maximum = min(1 / 18, max(1.0, pixels / (2560 * 1440)) / 35)
    return min(maximum, scale * scale / 90)
