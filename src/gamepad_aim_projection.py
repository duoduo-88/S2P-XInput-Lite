"""Project the immutable 3D room on the rendering worker."""

import ctypes
from functools import lru_cache
from threading import local

from gamepad_aim_model import (
    FOCAL_SCALE, NEAR_PLANE, ROOM_LINES, AimCamera, clip_near, clip_viewport,
)
from gamepad_render_native import load_render_library


_LINE_COUNT = len(ROOM_LINES)
_WORLD_LINES = (ctypes.c_double * (_LINE_COUNT * 6))(
    *(coordinate for _, start, end in ROOM_LINES for point in (start, end) for coordinate in point)
)
_LOCAL = local()


@lru_cache(maxsize=1)
def _native_projector(library):
    try:
        project = library.aim_project_room
    except AttributeError:
        return None
    double_pointer = ctypes.POINTER(ctypes.c_double)
    project.argtypes = (
        double_pointer, ctypes.c_uint,
        ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double,
        ctypes.c_double, ctypes.c_double, double_pointer, ctypes.POINTER(ctypes.c_uint),
    )
    project.restype = ctypes.c_uint
    return project


def project_room(width, height, yaw, pitch):
    """Return visible segments in the original drawing order."""
    library = load_render_library()
    native = _native_projector(library) if library is not None else None
    if native is None:
        return _project_python(width, height, yaw, pitch)
    if not hasattr(_LOCAL, "coordinates"):
        _LOCAL.coordinates = (ctypes.c_double * (_LINE_COUNT * 4))()
        _LOCAL.indices = (ctypes.c_uint * _LINE_COUNT)()
    coordinates, indices = _LOCAL.coordinates, _LOCAL.indices
    count = native(
        _WORLD_LINES, _LINE_COUNT, width, height, yaw, pitch,
        FOCAL_SCALE, NEAR_PLANE, coordinates, indices,
    )
    return tuple(
        (*coordinates[offset * 4:offset * 4 + 4], ROOM_LINES[indices[offset]][0])
        for offset in range(count)
    )


def _project_python(width, height, yaw, pitch):
    camera = AimCamera()
    camera.yaw, camera.pitch = yaw, pitch
    transform = camera.camera_transform()
    cx, cy, focal = width / 2, height / 2, height * FOCAL_SCALE

    def project(point):
        x, y, z = point
        return cx + focal * x / z, cy - focal * y / z

    lines = []
    for color, start, end in ROOM_LINES:
        clipped = clip_near(transform(start), transform(end))
        if clipped is not None:
            bounds = clip_viewport(project(clipped[0]), project(clipped[1]), width, height)
            if bounds is not None:
                lines.append((*bounds, color))
    return tuple(lines)
