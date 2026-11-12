"""Project the immutable 3D room on the rendering worker."""

import ctypes
from functools import lru_cache

from gamepad_aim_model import FOCAL_SCALE, NEAR_PLANE, ROOM_TILES, AimCamera
from gamepad_render_native import load_render_library


def _world_batches():
    groups = {}
    for color, quad in ROOM_TILES:
        groups.setdefault(color, []).extend(coordinate for point in quad for coordinate in point)
    return tuple(
        (color, len(values) // 12, (ctypes.c_double * len(values))(*values))
        for color, values in groups.items()
    )


_WORLD_BATCHES = _world_batches()
_FLOATS_PER_TRIANGLE = 6
_MAX_TRIANGLES_PER_TILE = 7  # Four corners plus up to five clipping intersections.


@lru_cache(maxsize=1)
def _native_projector(library):
    try:
        project = library.aim_project_tiles
    except AttributeError:
        return None
    double_pointer = ctypes.POINTER(ctypes.c_double)
    project.argtypes = (
        double_pointer, ctypes.c_uint,
        ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double,
        ctypes.c_double, ctypes.c_double, ctypes.POINTER(ctypes.c_float),
    )
    project.restype = ctypes.c_uint
    return project


def project_room(width, height, yaw, pitch):
    """Return one packed triangle array per visible shade."""
    library = load_render_library()
    native = _native_projector(library) if library is not None else None
    if native is None:
        return _project_python(width, height, yaw, pitch)
    batches = []
    for color, tile_count, quads in _WORLD_BATCHES:
        coordinates = (ctypes.c_float * (tile_count * _MAX_TRIANGLES_PER_TILE * _FLOATS_PER_TRIANGLE))()
        count = native(quads, tile_count, width, height, yaw, pitch, FOCAL_SCALE, NEAR_PLANE, coordinates)
        if count:
            batches.append((color, coordinates, count))
    return tuple(batches)


def _project_python(width, height, yaw, pitch):
    batches = {color: [] for color, _, _ in _WORLD_BATCHES}
    for index, points in project_room_polygons(width, height, yaw, pitch):
        triangles = batches[ROOM_TILES[index][0]]
        first = points[0]
        for i in range(1, len(points) - 1):
            triangles.extend((*first, *points[i], *points[i + 1]))
    return tuple(
        (color, (ctypes.c_float * len(values))(*values), len(values) // _FLOATS_PER_TRIANGLE)
        for color, values in batches.items() if values
    )


def _clip_polygon(points, distance):
    if not points:
        return points
    clipped = []
    previous = points[-1]
    previous_distance = distance(previous)
    for point in points:
        current_distance = distance(point)
        if (previous_distance < 0) != (current_distance < 0):
            if previous_distance < 0:
                outside, inside = previous, point
                outside_distance, inside_distance = previous_distance, current_distance
            else:
                outside, inside = point, previous
                outside_distance, inside_distance = current_distance, previous_distance
            amount = outside_distance / (outside_distance - inside_distance)
            clipped.append(tuple(a + (b - a) * amount for a, b in zip(outside, inside)))
        if current_distance >= 0:
            clipped.append(point)
        previous, previous_distance = point, current_distance
    return clipped


def project_room_polygons(width, height, yaw, pitch):
    """Yield visible tile indices and clipped polygons for the Tk fallback."""
    camera = AimCamera()
    camera.yaw, camera.pitch = yaw, pitch
    transform = camera.camera_transform()
    cx, cy, focal = width / 2, height / 2, height * FOCAL_SCALE

    # Clip in camera space before dividing by depth.
    planes = (
        lambda p: p[2] - NEAR_PLANE,
        lambda p: cx * p[2] + focal * p[0],
        lambda p: cx * p[2] - focal * p[0],
        lambda p: cy * p[2] + focal * p[1],
        lambda p: cy * p[2] - focal * p[1],
    )
    for index, (_, quad) in enumerate(ROOM_TILES):
        points = tuple(map(transform, quad))
        for distance in planes:
            points = _clip_polygon(points, distance)
            if len(points) < 3:
                break
        else:
            yield index, tuple(
                (min(width, max(0.0, cx + focal * x / z)), min(height, max(0.0, cy - focal * y / z)))
                for x, y, z in points
            )
